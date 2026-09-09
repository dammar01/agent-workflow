"""Real Claude Code session history -> observed metrics per session.

The controlled study in `BENCHMARK-PLAN.md` assigns arms and pairs by task. This does
neither. It reads sessions that already happened, classifies each one by what it actually
did, and reports what they cost. The two are not interchangeable and the difference is not
a footnote:

**Nothing here is randomised.** A session used the second agent because the operator chose
to, on that task, that day. If big tasks are the ones that get delegated, the delegated
group carries the harder work — and the comparison measures task difficulty wearing a
topology's name. This is the confound the 3-arm design exists to remove, and it is present
in full here.

**Nothing here is paired.** Arms in the controlled study see the same corpus. These
sessions saw whatever the operator was doing. A difference between groups is a difference
between two piles of different work.

**Group membership is derived, not assigned.** A session is called `second_agent` because
its transcript contains `.workflow/run` calls, `native_subagent` because it spawned Task
subagents, and `direct` when it did neither. Sessions that did both are flagged rather than
forced into one group.

So: this measures WHAT HAPPENED. It does not establish that one topology causes lower
premium token use. Report it as observation, and say the word "observed" out loud.

    python bench/observe.py --out bench/observed.jsonl
    python bench/observe.py --report
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audit import transcript as transcript_reader  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = BENCH_DIR / "observed.jsonl"

MODE_DIRECT = "direct"
MODE_NATIVE = "native_subagent"
MODE_SECOND_AGENT = "second_agent"
MODES = (MODE_DIRECT, MODE_NATIVE, MODE_SECOND_AGENT)

# A delegated call, spelled either way. The POSIX runner is `.workflow/run.sh` and the
# Windows one `.workflow\\run.ps1`, and a pattern anchored on one separator silently scores
# every session on the other platform as `direct`.
DELEGATED_CALL = re.compile(r"\.workflow[\\/]run(?:\.ps1|\.sh)?\b")

# The workflow session id the runner is handed as its third argument. Extracted from the
# transcript rather than looked up in `session_registry.json`, which keeps only the newest
# 50 bindings — a cap that would quietly drop most of the history this reads.
MAIN_SESSION_ID = re.compile(r"\bmain_[A-Za-z0-9._-]+")

TOOL_INPUT_COMMAND_KEYS = ("command",)


def _tool_uses(record: dict):
    """Every tool_use block in one assistant record."""
    message = record.get("message")
    if not isinstance(message, dict):
        return
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def _command_text(block: dict) -> str:
    payload = block.get("input")
    if not isinstance(payload, dict):
        return ""
    return " ".join(str(payload.get(key, "")) for key in TOOL_INPUT_COMMAND_KEYS)


def classify(records: list[dict]) -> dict:
    """What this session actually did, counted rather than assumed."""
    delegated = 0
    subagent_calls = 0
    edits = 0
    main_sessions: set[str] = set()
    for record in records:
        if record.get("type") != transcript_reader.ASSISTANT_TYPE:
            continue
        for block in _tool_uses(record):
            name = block.get("name")
            if name in transcript_reader.EDIT_TOOLS:
                edits += 1
            if name == "Task":
                subagent_calls += 1
            text = _command_text(block)
            if not text:
                continue
            if DELEGATED_CALL.search(text):
                delegated += 1
                main_sessions.update(MAIN_SESSION_ID.findall(text))
    sidechain = sum(
        1
        for record in records
        if record.get("type") == transcript_reader.ASSISTANT_TYPE and record.get("isSidechain")
    )
    native = subagent_calls + sidechain
    # Precedence, not exclusivity: a session that delegated at all is a session whose
    # premium context was shaped by delegation, whatever else it also did. The `mixed`
    # flag is what stops that precedence from erasing the overlap.
    if delegated:
        mode = MODE_SECOND_AGENT
    elif native:
        mode = MODE_NATIVE
    else:
        mode = MODE_DIRECT
    return {
        "mode": mode,
        "mixed": bool(delegated and native),
        "delegated_calls": delegated,
        "subagent_calls": subagent_calls,
        "sidechain_assistant_turns": sidechain,
        "edit_tool_calls": edits,
        "main_session_ids": sorted(main_sessions),
    }


def _span_seconds(records: list[dict]) -> float | None:
    stamps = sorted(
        str(record.get("timestamp"))
        for record in records
        if record.get("timestamp")
    )
    if len(stamps) < 2:
        return None
    from datetime import datetime

    try:
        first = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
        last = datetime.fromisoformat(stamps[-1].replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((last - first).total_seconds(), 1)


def load_worker_usage(project_roots: list[Path]) -> dict:
    """Worker token totals per workflow session id, from every `.workflow/usage.jsonl`."""
    totals: dict[str, dict] = {}
    for root in project_roots:
        path = root / ".workflow" / "usage.jsonl"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            session = str(row.get("session_id") or "")
            if not session:
                continue
            entry = totals.setdefault(
                session,
                {
                    "worker_calls": 0,
                    "worker_input_tokens": 0,
                    "worker_output_tokens": 0,
                    "premium_context_avoided_tokens": 0,
                    "measured_calls": 0,
                    "providers": set(),
                },
            )
            entry["worker_calls"] += 1
            # `actual_*` first, exactly as the runtime's own readers do: reading the
            # estimate while reporting the source as `provider` would claim a precision
            # the chars//4 figures do not have.
            measured = row.get("actual_input_tokens") is not None
            entry["measured_calls"] += 1 if measured else 0
            entry["worker_input_tokens"] += int(
                row.get("actual_input_tokens") or row.get("estimated_input_tokens") or 0
            )
            entry["worker_output_tokens"] += int(
                row.get("actual_output_tokens") or row.get("estimated_output_tokens") or 0
            )
            entry["premium_context_avoided_tokens"] += int(
                row.get("premium_context_avoided_tokens") or 0
            )
            if row.get("provider"):
                entry["providers"].add(str(row["provider"]))
    for entry in totals.values():
        entry["providers"] = sorted(entry["providers"])
    return totals


def observe(
    projects_dir: Path | None = None, project_roots: list[Path] | None = None
) -> tuple[list[dict], list[str]]:
    """One row per real session, oldest first."""
    root = projects_dir or transcript_reader.transcripts_root()
    notes: list[str] = []
    if not root.is_dir():
        return [], [f"no transcript directory at {root}"]

    worker = load_worker_usage(project_roots or [])
    rows: list[dict] = []
    for slug_dir in sorted(root.iterdir()):
        if not slug_dir.is_dir():
            continue
        for path in sorted(slug_dir.glob("*.jsonl")):
            records = transcript_reader.load_transcript(path)
            if not records:
                notes.append(f"{slug_dir.name}/{path.name}: unreadable or empty, skipped")
                continue
            row = {
                "project_slug": slug_dir.name,
                "claude_session_id": path.stem,
                "records": len(records),
                "span_seconds": _span_seconds(records),
            }
            row.update(transcript_reader.count_turns(records))
            row.update(transcript_reader.context_tokens(records))
            row["turns_to_first_edit"] = transcript_reader.turns_to_first_edit(records)
            row.update(classify(records))
            joined = [worker[name] for name in row["main_session_ids"] if name in worker]
            row["worker_sessions_joined"] = len(joined)
            row["worker_calls"] = sum(entry["worker_calls"] for entry in joined) or None
            row["worker_input_tokens"] = (
                sum(entry["worker_input_tokens"] for entry in joined) if joined else None
            )
            row["worker_output_tokens"] = (
                sum(entry["worker_output_tokens"] for entry in joined) if joined else None
            )
            row["premium_context_avoided_tokens"] = (
                sum(entry["premium_context_avoided_tokens"] for entry in joined)
                if joined
                else None
            )
            row["worker_measured_calls"] = (
                sum(entry["measured_calls"] for entry in joined) if joined else None
            )
            row["worker_providers"] = sorted(
                {name for entry in joined for name in entry["providers"]}
            )
            rows.append(row)
    return rows, notes


def _median(values: list) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(float(ordered[mid]), 1)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 1)


def _stats(values: list) -> dict:
    """Median and range, never a bare mean.

    Session sizes are heavily skewed — one long session outweighs twenty short ones — so a
    mean describes the longest session more than it describes the group.
    """
    return {
        "n": len(values),
        "median": _median(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "total": sum(values) if values else 0,
    }


def did_work(row: dict, min_turns: int, min_edits: int) -> bool:
    """Did anything happen in this session worth counting?

    A transcript exists for every session the operator opened, including the ones opened
    and closed. Those carry zero turns and zero context, and leaving them in drags every
    median toward zero while describing nothing — which is how a group of abandoned
    sessions can be mistaken for a group of cheap ones.
    """
    return (row.get("message_turns") or 0) >= min_turns and (
        row.get("edit_tool_calls") or 0
    ) >= min_edits


def report(rows: list[dict], min_turns: int = 0, min_edits: int = 0) -> dict:
    kept = [row for row in rows if did_work(row, min_turns, min_edits)]
    groups: dict[str, list[dict]] = {mode: [] for mode in MODES}
    for row in kept:
        groups[row["mode"]].append(row)

    out: dict = {
        "sessions_scanned": len(rows),
        "sessions_counted": len(kept),
        "work_filter": {"min_turns": min_turns, "min_edit_tool_calls": min_edits},
        "design": "OBSERVATIONAL — not randomised, not paired, groups derived from behaviour",
        "by_mode": {},
    }
    for mode in MODES:
        units = groups[mode]
        if not units:
            out["by_mode"][mode] = {"sessions": 0}
            continue
        turns = [row["message_turns"] for row in units if row["message_turns"] is not None]
        context = [
            row["context_input_tokens"] for row in units if row["context_input_tokens"] is not None
        ]
        peaks = [
            row["context_peak_tokens"] for row in units if row["context_peak_tokens"] is not None
        ]
        spans = [row["span_seconds"] for row in units if row["span_seconds"] is not None]
        edits = [row["edit_tool_calls"] for row in units]
        # Premium context per human turn. The closest thing here to a fair comparison:
        # it divides out session length, which is the largest single difference between
        # these groups and has nothing to do with topology.
        per_turn = [
            row["context_input_tokens"] / row["message_turns"]
            for row in units
            if row["message_turns"] and row["context_input_tokens"] is not None
        ]
        worker_in = [
            row["worker_input_tokens"] for row in units if row["worker_input_tokens"] is not None
        ]
        worker_out = [
            row["worker_output_tokens"] for row in units if row["worker_output_tokens"] is not None
        ]
        avoided = [
            row["premium_context_avoided_tokens"]
            for row in units
            if row["premium_context_avoided_tokens"] is not None
        ]
        calls = [row["worker_calls"] for row in units if row["worker_calls"]]
        measured = [
            row["worker_measured_calls"] for row in units if row["worker_measured_calls"] is not None
        ]
        out["by_mode"][mode] = {
            "sessions": len(units),
            "mixed_sessions": sum(1 for row in units if row["mixed"]),
            "projects": sorted({row["project_slug"] for row in units}),
            "message_turns": _stats(turns),
            "context_input_tokens": _stats(context),
            "context_peak_tokens": _stats(peaks),
            "premium_context_per_turn": {
                "n": len(per_turn),
                "median": _median(per_turn),
            },
            "edit_tool_calls": _stats(edits),
            "span_seconds": _stats(spans),
            "delegated_calls": _stats([row["delegated_calls"] for row in units]),
            "worker_calls": _stats(calls),
            "worker_input_tokens": _stats(worker_in),
            "worker_output_tokens": _stats(worker_out),
            # The runtime's OWN estimate of context it kept out of the premium window. Not
            # a measurement against a counterfactual: nobody ran the same task without the
            # second agent, so this is what the runtime believed it avoided, not what a
            # comparison showed.
            "premium_context_avoided_tokens_self_reported": _stats(avoided),
            # How much of the worker figure is measured rather than `chars//4`. Printed
            # beside the totals because a total dominated by estimates is an estimate.
            "worker_calls_provider_measured": sum(measured),
            "worker_calls_total": sum(calls),
            "worker_providers": sorted(
                {name for row in units for name in (row["worker_providers"] or [])}
            ),
            "sessions_joined_to_worker_usage": sum(
                1 for row in units if row["worker_sessions_joined"]
            ),
        }

    # The comparison this file cannot make, said in the output rather than left for the
    # reader to notice. A between-group claim needs both groups; when one of them holds a
    # handful of sessions, any difference between them is the difference between a group
    # and an accident.
    counts = {mode: len(groups[mode]) for mode in MODES}
    weakest = min(counts, key=lambda mode: counts[mode])
    out["comparison_supported"] = counts[weakest] >= 20 and sum(counts.values()) > 0
    out["comparison_note"] = (
        f"group sizes {counts}. A between-mode claim needs real work in every group; "
        "with fewer than ~20 working sessions in a group there is no baseline to compare "
        "against, only a description of the group that does have data."
    )
    return out


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="observed session history -> metrics")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--projects-root",
        default=None,
        help="parent directory of the project checkouts, for `.workflow/usage.jsonl`",
    )
    parser.add_argument("--report", action="store_true", help="print the grouped report")
    parser.add_argument(
        "--min-turns",
        type=int,
        default=0,
        help="ignore sessions with fewer human turns than this when reporting",
    )
    parser.add_argument(
        "--min-edits",
        type=int,
        default=0,
        help="ignore sessions that touched fewer files than this when reporting",
    )
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    roots: list[Path] = []
    if args.projects_root:
        parent = Path(args.projects_root)
        roots = [path for path in parent.iterdir() if path.is_dir()] if parent.is_dir() else []

    rows, notes = observe(project_roots=roots)
    for note in notes:
        print(f"  note: {note}", file=sys.stderr)

    if not args.dry_run:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, default=str) + "\n")
        print(f"wrote {len(rows)} session row(s) to {out}", file=sys.stderr)

    if args.report or args.dry_run:
        print(json.dumps(report(rows, args.min_turns, args.min_edits), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
