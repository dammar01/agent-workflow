#!/usr/bin/env python3
"""Grade real coding tasks by what reached branch main (RQ-02, RQ-04; CASE-012).

A Senior-SWE-bench-style reading of real sessions, without replaying them. A task is one
Claude Code session whose purpose was a code change; the maintainer labels each session's
category once (feature, bug, performance, migration, refactor, or a non-coding kind) in a
local labels file, because a first prompt does not say reliably what the session became.

Per task, from the transcript, the project's Git history, and the runtime's usage rows:

  outcome      the substantive lines (8+ characters) the session's successful edits left
               behind -- lines it added and removed again itself are not its result -- are
               looked up on main's first-parent states from the session's start. The first
               state holding 80% or more is where the work entered main: `solved`. A lower
               best share is `partial` (30-80%) or `not_in_main`; no edit is `no_change`.
  fix within 24 h
               a solved task whose lines change on main within 24 hours of entering it, by a
               commit from an author of the commits that brought them in, counts as fixed
               after the fact: `solved` splits into clean and fixed. (The maintainer's rule.)
  prompts      follow-up prompts classified as plan, execute, fix, question, verify,
               explore_analyze, change_request, or other, from the slash command or the
               intent map shipped in dist/config/claude/hooks/intent-map.json
  delegation   delegated calls: distinct prompt ids of the session's usage rows that a provider
               answered (a skill runs `.workflow/run` in a subagent, so the transcript's main
               thread does not show it); verify-browser's spec and review stages are each a call
  context      main-agent context of the last and the largest assistant message (input plus
               cache read and creation), per message id
  saved        tokens kept from entering the main agent: the second agent's fresh input less
               the answers that came back (see saved_net); the status line's figure, which
               adds reasoning, is reported beside it. The digest
               figure (`premium_context_avoided_tokens`, answer minus digest) is kept beside
               it, with the rows the runtime left empty on verify-browser's path recovered
               from archived replies (see saved_tokens)

Lines are matched by content, so a file moved to another directory is followed by its name.
Read-only. Prints one JSON object of figures; project names become letters unless
--show-names. Not part of the test suite.

  python tools/maintain/measure_task_outcomes.py --projects-root <dir> --labels <labels.json> --version 3.6.0 --version 3.7.3
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.audit.transcript import project_slug  # noqa: E402
from tools.maintain.measure_real_use import CORRECTION, load_usage, read_transcript  # noqa: E402

CODING = ("feature", "bug", "performance", "migration", "refactor")
SOLVED_SHARE, PARTIAL_SHARE, MIN_LINE = 0.8, 0.3, 8
FIX_WINDOW = timedelta(hours=24)
INTENT_MAP = Path(__file__).resolve().parents[2] / "dist" / "config" / "claude" / "hooks" / "intent-map.json"
TESTFILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|\.(test|spec)\.[jt]sx?$|Test\.php$", re.I)


def git(project: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", project, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


# --- prompt kinds ------------------------------------------------------------------------

def _any(words):
    return re.compile("|".join(words), re.I)


_IM = json.loads(INTENT_MAP.read_text(encoding="utf-8"))["patterns"]
VERIFY = _any(_IM["verify"] + _IM["verify-browser"] + [r"\btes(t|ting)?\b", r"coba (cek|jalankan|run)",
                                                       r"sudah (jalan|benar|bener|aman)", r"lolos", r"\bverif"])
PLAN = _any(_IM["plan"] + [r"buat(kan)? rencana", r"\bplan\b", r"langkah", r"strategi"])
EXPLORE = _any(_IM["explore"] + _IM["analyze"] + [r"telusuri", r"pahami", r"jelaskan"])
EXECUTE = re.compile(r"^\s*(ya|y|ok|oke|okay|baik|gas|lanjut|lanjutkan|kerjakan|implement\w*|eksekusi|terapkan"
                     r"|jalankan|silahkan|silakan|continue|go|yes|setuju|boleh|sip|mantap)\b|\b(dilanjutkan|lanjutkan)\b", re.I)
QUESTION = re.compile(r"\?|^\s*(apa|apakah|bagaimana|bagiamana|gimana|kenapa|mengapa|berapa|di ?mana|siapa|kapan"
                      r"|bisa(kah)?|seharusnya|cara)\b|\b(apakah|bagaimana|bagiamana|gimana|mengapa|apa (yang|saja|itu)"
                      r"|jadi (masalah|maslaah|kesimpulan)\w*|atau rata-rata)\b", re.I)
FIX = re.compile(r"\b(perbaiki|benerin|fix|masih|error|gagal|bug|tidak (muncul|tampil|berubah|jalan|ada)"
                 r"|gak (muncul|jalan)|maksud saya|saya menanyakan|yang saya maksud|bukan itu|exited|failure"
                 r"|failed|exception|sqlstate)\b", re.I)
CHANGE = re.compile(r"\b(tambah\w*|ubah|ganti|hapus|buat\w*|hide|sembunyikan|pindah\w*|sesuaikan|rapikan"
                    r"|optimasi\w*|update|perbarui)\b", re.I)
KINDS = ("plan", "execute", "fix", "question", "verify", "explore_analyze", "change_request", "other")


def prompt_kind(prompt: str) -> str:
    """One follow-up prompt's kind. A heuristic: 85% agreed with a hand label on 80 samples."""
    text = prompt.strip()
    if text.startswith("/."):
        cmd = text[2:].split()[0] if len(text) > 2 else ""
        for prefix, kind in (("plan", "plan"), ("execute", "execute"), ("verify", "verify")):
            if cmd.startswith(prefix):
                return kind
        return "explore_analyze" if cmd in ("explore", "analyze") else "other"
    head = text[:600]
    if CORRECTION.search(head) or FIX.search(head):
        return "fix"
    if VERIFY.search(head) and not re.match(r"\s*(validasi \w+ (\w+ )?(juga )?agar|\W*revisi)", head, re.I):
        return "verify"
    for rx, kind in ((PLAN, "plan"), (EXECUTE, "execute"), (EXPLORE, "explore_analyze"),
                     (QUESTION, "question"), (CHANGE, "change_request")):
        if rx.search(head):
            return kind
    return "other"


# --- lines a session left behind ---------------------------------------------------------

def session_lines(path: str, root: str) -> dict[str, set[str]]:
    """Project-relative file (real case) -> substantive lines the session added and kept."""
    added, churned, pending = defaultdict(set), defaultdict(set), set()
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        content = (rec.get("message") or {}).get("content")
        if rec.get("isSidechain") or not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") in ("Edit", "Write", "MultiEdit"):
                pending.add(b.get("id"))
            if b.get("type") != "tool_result" or b.get("tool_use_id") not in pending or b.get("is_error"):
                continue
            result = rec.get("toolUseResult")
            if not isinstance(result, dict) or not result.get("filePath"):
                continue
            full = os.path.abspath(result["filePath"]).replace("\\", "/")
            if not full.lower().startswith(root.lower()):
                continue
            rel = full[len(root):]
            hunks = result.get("structuredPatch")
            if isinstance(hunks, list) and hunks:
                lines = [l for h in hunks if isinstance(h, dict) for l in h.get("lines", [])]
                new = [l[1:] for l in lines if l.startswith("+")]
                churned[rel] |= {l[1:].strip() for l in lines if l.startswith("-")} & added[rel]
            elif result.get("type") == "create" and isinstance(result.get("content"), str):
                new = result["content"].splitlines()
            else:
                continue
            added[rel].update(x.strip() for x in new if len(x.strip()) >= MIN_LINE)
    return {k: v - churned[k] for k, v in added.items() if v - churned[k]}


class Repo:
    """Cached file contents of one project at Git revisions."""

    def __init__(self, path: str):
        self.path, self._lines, self._names = path, {}, {}

    def lines(self, rel: str, rev: str) -> set[str]:
        key = (rel, rev)
        if key not in self._lines:
            text = git(self.path, "show", f"{rev}:{rel}")
            if not text:  # moved: follow the file by its name
                for moved in self.names(rev).get(rel.rsplit("/", 1)[-1].lower(), [])[:5]:
                    text += git(self.path, "show", f"{rev}:{moved}")
            self._lines[key] = {x.strip() for x in text.splitlines()}
        return self._lines[key]

    def names(self, rev: str) -> dict[str, list[str]]:
        if rev not in self._names:
            idx = defaultdict(list)
            for p in git(self.path, "ls-tree", "-r", "--name-only", rev).splitlines():
                idx[p.rsplit("/", 1)[-1].lower()].append(p)
            self._names[rev] = idx
        return self._names[rev]

    def share(self, per_file, rev) -> float:
        total = sum(len(v) for v in per_file.values())
        return sum(1 for k, v in per_file.items() for x in v if x in self.lines(k, rev)) / total if total else 0.0

    def touching(self, ref, since, until, files, authors=None) -> list[dict]:
        out = git(self.path, "log", ref, "--no-merges", "--format=@@%H\t%ae", "--name-only",
                  f"--since={since.isoformat()}", f"--until={until.isoformat()}")
        commits, cur = [], None
        for line in out.splitlines():
            if line.startswith("@@"):
                h, author = line[2:].split("\t", 1)
                cur = {"hash": h, "author": author.lower(), "files": set()}
                commits.append(cur)
            elif line.strip() and cur is not None:
                cur["files"].add(line.strip().replace("\\", "/").lower())
        return [c for c in commits if (authors is None or c["author"] in authors) and c["files"] & files]


def outcome(repo: Repo, per_file, start, end) -> dict:
    out = {"main_share": 0.0, "fixed_24h": False}
    if not per_file or not git(repo.path, "rev-parse", "--verify", "-q", "main").strip():
        return out
    best = None
    for row in git(repo.path, "log", "main", "--first-parent", "--reverse", "--format=%H\t%cI",
                   f"--since={start.isoformat()}", f"--until={(end + timedelta(days=30)).isoformat()}").splitlines():
        rev, ts = row.split("\t")
        share = repo.share(per_file, rev)
        if best is None or share > best[0] + 1e-9 or share >= SOLVED_SHARE:
            best = (share, rev, datetime.fromisoformat(ts))
        if share >= SOLVED_SHARE:
            break
    if not best or not best[0]:
        return out
    share, rev0, t0 = best
    out["main_share"] = round(share, 2)
    files = {k.lower() for k in per_file}
    authors = {c["author"] for c in repo.touching(rev0, start - timedelta(minutes=5), t0 + timedelta(minutes=1), files)}
    rev1 = git(repo.path, "rev-list", "-1", "--first-parent", f"--before={(t0 + FIX_WINDOW).isoformat()}", "main").strip()
    if rev1 and rev1 != rev0:
        lost = {k.lower() for k, v in per_file.items()
                if any(x in repo.lines(k, rev0) and x not in repo.lines(k, rev1) for x in v)}
        out["fixed_24h"] = bool(lost and repo.touching("main", t0 + timedelta(seconds=1), t0 + FIX_WINDOW, lost, authors))
    return out


def context(path: str) -> dict | None:
    per, order = {}, []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        msg = rec.get("message") or {}
        usage, mid = msg.get("usage") or {}, msg.get("id")
        if not mid or not usage:
            continue
        if mid not in per:
            order.append(mid)
        per[mid] = sum(int(usage.get(k) or 0) for k in
                       ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    if not order:
        return None
    return {"final": per[order[-1]], "peak": max(per.values())}


def saved_net(rows: list[dict]) -> dict:
    """Tokens kept from entering the main agent: what the second agent read, less what came back.

    `fresh` is the second agent's fresh input (input minus cache read) on every row the
    provider counted -- the material it read so the main agent did not have to. `returned`
    is what the second agent answered (`response_chars` // 4 summed over every row): a
    continuation's answer is merged from its rows, and the closing row alone holds only the
    last fragment, so summing errs toward more returned and a lower net. The net is fresh
    minus returned. Reasoning is output, not input to
    the main agent, so it is reported apart and not counted. `badge` is the status line's
    figure (fresh plus reasoning, falling back to `premium_context_avoided_tokens` on rows
    the provider did not count), for comparison. A call that failed before any provider
    counted it is skipped. Rows without provider counts add nothing to `fresh`, so `partial`
    marks a task where part of the reading went unmeasured. The second agent's own fixed
    overhead per call (its system prompt and instructions) is inside `fresh` and is not
    separated by the runtime, so the net is a slight overstatement.
    """
    out = {"fresh": 0, "reasoning": 0, "returned": 0, "badge": 0, "partial": False}
    for r in rows:
        if r.get("ok") is False and r.get("actual_input_tokens") is None and r.get("actual_output_tokens") is None:
            continue
        if r.get("actual_input_tokens") is not None:
            fresh = max(0, int(r["actual_input_tokens"]) - int(r.get("actual_cached_input_tokens") or 0))
            reasoning = int(r.get("actual_reasoning_tokens") or 0)
            out["fresh"] += fresh
            out["reasoning"] += reasoning
            out["badge"] += fresh + reasoning
        else:
            out["badge"] += int(r.get("premium_context_avoided_tokens") or 0)
            out["partial"] = True
        out["returned"] += int(r.get("response_chars") or 0) // 4
    out["net"] = max(0, out["fresh"] - out["returned"])
    return out


def saved_tokens(project: str, rows: list[dict]) -> tuple[int, int]:
    """Context saved over a session's usage rows: (as recorded, with the runner's rows recovered).

    The runtime records `premium_context_avoided_tokens` only where the digest was parsed,
    which is the main delegated path. verify-browser's stages (the spec draft and the
    review, `core/evidence/e2e/runner.py`) call `_run_delegated` directly and never parse
    the digest their replies carry, so their rows hold None since commit cb98768
    (2026-09-15). For those rows the saving is recomputed with the runtime's own rule,
    `max(0, response_chars - digest_chars) // 4`, from the reply archived under the session's
    logs. A continuation's first row stays empty, as the runtime intends.
    """
    from core.evidence.contract import extract_digest
    from core.evidence.contracts import _digest_chars

    recorded = recovered = 0
    for r in rows:
        value = r.get("premium_context_avoided_tokens")
        if isinstance(value, int):
            recorded += value
            continue
        if not r.get("provider") or not r.get("ok") or r.get("provider_call_index") == 0 or not r.get("prompt_id"):
            continue
        for raw in glob.glob(os.path.join(project, ".workflow", "data", "sessions", "*", "logs",
                                          r["prompt_id"], "output.raw.md"))[:1]:
            text = Path(raw).read_text(encoding="utf-8", errors="replace")
            digest = extract_digest(text)
            if digest is not None:
                recovered += max(0, (r.get("response_chars") or len(text)) - (_digest_chars(digest) or 0)) // 4
    return recorded, recorded + recovered


def grade(share: float, changed: bool, fixed: bool) -> str:
    if not changed:
        return "no_change"
    if share >= SOLVED_SHARE:
        return "solved_fixed" if fixed else "solved_clean"
    return "partial" if share >= PARTIAL_SHARE else "not_in_main"


# --- report ------------------------------------------------------------------------------

def med(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def p90(values):
    values = sorted(v for v in values if v is not None)
    return values[int(0.9 * (len(values) - 1))] if values else None


def summary(tasks: list[dict]) -> dict:
    n = len(tasks)
    g = Counter(t["grade"] for t in tasks)
    solved = g["solved_clean"] + g["solved_fixed"]
    kinds = Counter()
    for t in tasks:
        kinds.update(t["prompt_kinds"])
    ctx = [t["context"] for t in tasks if t["context"]]
    return {
        "tasks": n, "solved": solved, "solved_rate": round(solved / n, 3) if n else None,
        "solved_clean": g["solved_clean"], "solved_clean_rate": round(g["solved_clean"] / n, 3) if n else None,
        "solved_fixed_24h": g["solved_fixed"], "partial": g["partial"], "not_in_main": g["not_in_main"],
        "no_change": g["no_change"],
        "prompts_median": med([t["prompts"] for t in tasks]),
        "active_minutes_median": med([t["active_minutes"] for t in tasks]),
        "delegated_calls_median": med([t["delegated_calls"] for t in tasks]),
        "tasks_delegating": sum(1 for t in tasks if t["delegated_calls"]),
        "follow_up_kinds_per_task": {k: round(kinds[k] / n, 1) for k in KINDS} if n else {},
        "context_final_median": med([c["final"] for c in ctx]), "context_final_p90": p90([c["final"] for c in ctx]),
        "context_peak_max": max((c["peak"] for c in ctx), default=None),
        "context_saved_median": med([t["context_saved"] for t in tasks]),
        "context_saved_p90": p90([t["context_saved"] for t in tasks]),
        "context_saved_total": sum(t["context_saved"] for t in tasks),
        "context_saved_partly_estimated_tasks": sum(1 for t in tasks if t["context_saved_estimated"]),
        "context_saved_to_final_context_median": med([t["context_saved"] / t["context"]["final"]
                                                      for t in tasks if t["context"] and t["context"]["final"]]),
        "second_agent_fresh_median": med([t["second_agent_fresh"] for t in tasks]),
        "second_agent_reasoning_median": med([t["second_agent_reasoning"] for t in tasks]),
        "returned_to_main_median": med([t["returned_to_main"] for t in tasks]),
        "badge_saved_median": med([t["badge_saved"] for t in tasks]),
        "badge_saved_total": sum(t["badge_saved"] for t in tasks),
        "digest_saved_median": med([t["digest_saved"] for t in tasks]),
        "digest_saved_total": sum(t["digest_saved"] for t in tasks),
        "digest_saved_recorded_total": sum(t["digest_saved_recorded"] for t in tasks),
        "solved_touching_tests": sum(1 for t in tasks if t["grade"].startswith("solved") and t["tests_edited"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects-root", required=True)
    ap.add_argument("--labels", required=True, help="JSON: {transcript file name: category}")
    ap.add_argument("--version", action="append", required=True)
    ap.add_argument("--transcripts", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--exclude", action="append", default=["agent-workflow"])
    ap.add_argument("--show-names", action="store_true")
    args = ap.parse_args()
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))

    tasks, names, unlabelled = [], {}, 0
    for path in sorted(p for p in glob.glob(os.path.join(args.projects_root, "*")) if os.path.isdir(p)):
        name = os.path.basename(path)
        if name in args.exclude:
            continue
        tdir = os.path.join(args.transcripts, project_slug(os.path.abspath(path)))
        usage = load_usage(os.path.join(path, ".workflow", "data", "usage.jsonl"))
        root = os.path.abspath(path).replace("\\", "/") + "/"
        repo = Repo(path)
        for f in sorted(glob.glob(os.path.join(tdir, "*.jsonl"))):
            s = read_transcript(f, 900)
            if s["version"] not in args.version or not s["prompts"]:
                continue
            category = labels.get(os.path.basename(f))
            if category is None:
                unlabelled += 1
                continue
            if category not in CODING:
                continue
            start = datetime.fromisoformat(s["start"])
            end = start + timedelta(minutes=s["wall_minutes"])
            per_file = session_lines(f, root)
            out = outcome(repo, per_file, start, end)
            rows = [r for r in usage if r.get("session_id") in s["sessions"]]
            saved_recorded, saved_digest = saved_tokens(path, rows)
            net = saved_net(rows)
            tasks.append({
                "project": name, "version": s["version"], "category": category,
                "grade": grade(out["main_share"], bool(per_file), out["fixed_24h"]),
                "prompts": len(s["prompts"]), "active_minutes": s["active_minutes"],
                "prompt_kinds": Counter(prompt_kind(p) for p in s["prompts"][1:]),
                # A call is one prompt a provider answered: rows without a provider (a
                # verify-browser run's closing row, a quick verify) are not second-agent work.
                # verify-browser's spec and review stages are each a call.
                "delegated_calls": len({r.get("prompt_id") or r.get("correlation_id")
                                        for r in rows if r.get("provider")}),
                "context": context(f),
                "context_saved": net["net"],
                "context_saved_estimated": net["partial"],
                "second_agent_fresh": net["fresh"], "second_agent_reasoning": net["reasoning"],
                "returned_to_main": net["returned"], "badge_saved": net["badge"],
                "digest_saved": saved_digest,
                "digest_saved_recorded": saved_recorded,
                "tests_edited": any(TESTFILE.search(k) for k in per_file),
            })
    for t in tasks:
        names.setdefault(t["project"], None)
    order = sorted(names, key=lambda n: -sum(1 for t in tasks if t["project"] == n))
    letters = {n: (n if args.show_names else f"Project {chr(ord('A') + i)}") for i, n in enumerate(order)}

    def bucket(n):
        return "0" if n == 0 else "1-2" if n <= 2 else "3-5" if n <= 5 else "6+"

    report = {
        "versions": args.version, "unlabelled_sessions": unlabelled,
        "all": summary(tasks),
        "by_version": {v: summary([t for t in tasks if t["version"] == v]) for v in args.version},
        "by_category": {c: summary([t for t in tasks if t["category"] == c]) for c in CODING
                        if any(t["category"] == c for t in tasks)},
        "by_delegated_calls": {b: summary([t for t in tasks if bucket(t["delegated_calls"]) == b])
                               for b in ("0", "1-2", "3-5", "6+")},
        "by_project": {letters[n]: summary([t for t in tasks if t["project"] == n]) for n in order},
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
