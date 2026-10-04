"""The calculation contract: every benchmark figure has an ID, a version and a stated method.

Three producers compute figures this project reports: the runtime's `--command report`
(`core/audit/telemetry.py`, `task_telemetry.py`) and the two offline tools
`tools/maintain/measure_real_use.py` and `measure_task_outcomes.py`. They used the same words
for different things — "task" meant a correlation id, a session run closed by a verify pass,
and a labelled transcript; "saved" had three formulas — and their statistics differ. This
registry does not unify them, because doing so would silently change figures already recorded
(CASE-011, CASE-012). It names each one apart, states exactly how it is computed, and versions
it: a figure is cited as `<id>@<version>`, and a change to how a metric is computed bumps its
version instead of quietly meaning something else.

Every producer also stamps its output with `provenance()`: the registry version, the tool,
the tool's commit, the parameters it ran with, and a hash over its inputs. The inputs are
private (Claude Code transcripts, usage streams, project git history), so a figure can be
recounted only on the machine that holds them: any user can measure their own use with the
same tools, and nobody can recount another user's figure. `RECOUNT` says so in every stamp.
`write_export` writes a figure set that may leave that machine: aggregates and the stamp,
refused when it would carry a path or a project name.

The registry is documented in docs/evaluation/metrics.md; a test keeps the two in step.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from utils import osutil

METRICS_VERSION = 1

RECOUNT = (
    "on the machine holding the inputs only: inputs are private transcripts, usage streams and "
    "git history; the method is public, the data is not"
)

# Statistic conventions, named once. Each metric says which one it uses.
STATISTICS = {
    "median_interpolated": "statistics-style median (mean of the two middle values), rounded to 2 decimals",
    "nearest_rank": "sorted values, index round(p * (n - 1)) with Python's half-to-even round; rounded to 1 decimal",
    "median_stdlib": "statistics.median, rounded to 1 decimal",
    "p90_floor": "sorted values, index floor(0.9 * (n - 1))",
    "count": "a count; no statistic",
    "rate": "numerator / denominator, rounded to 3 decimals; None when the denominator is 0",
    "sum": "a sum over the unit",
}

_RUNTIME = "core/audit/telemetry.py report()"
_TASKS = "core/audit/task_telemetry.py report()"
_REAL_USE = "tools/maintain/measure_real_use.py"
_OUTCOMES = "tools/maintain/measure_task_outcomes.py"

REGISTRY: dict[str, dict] = {
    # --- runtime: usage.jsonl ------------------------------------------------------------
    "runtime.command": {
        "version": 1, "producer": _RUNTIME, "unit": "command", "statistic": "count",
        "definition": "A delegated command: usage rows grouped by prompt_id (a continuation is one); a row without prompt_id is its own command. Report key `calls`/`commands`.",
        "denominator": "-",
        "missing": "Rows of a provider-less browser run (no prompt_id, provider or tokens; CASE-014) are dropped before counting.",
    },
    "runtime.provider_call": {
        "version": 1, "producer": _RUNTIME, "unit": "provider invocation", "statistic": "count",
        "definition": "One usage row. Report key `provider_calls`.",
        "denominator": "-", "missing": "As runtime.command.",
    },
    "runtime.cost_tokens": {
        "version": 1, "producer": _RUNTIME, "unit": "tokens", "statistic": "sum",
        "definition": "Input plus output per row: the provider's actual count when present, else characters // 4. Cached input and reasoning are inside these counts, never added. Report key `cost`.",
        "denominator": "-", "missing": "`token_source` says which rows were estimated.",
    },
    "runtime.accepted_task": {
        "version": 1, "producer": _RUNTIME, "unit": "correlation_id", "statistic": "count",
        "definition": "A correlation id (plan chain or derived task id) with at least one verify row whose derived verdict is pass. Report key `accepted_tasks`.",
        "denominator": "correlation ids with at least one judged verify",
        "missing": "Never-verified ids are outside the denominator, not counted as failures.",
    },
    "runtime.first_pass_correctness": {
        "version": 1, "producer": _RUNTIME, "unit": "correlation_id", "statistic": "rate",
        "definition": "Share of judged correlation ids whose first judged verify passed.",
        "denominator": "correlation ids with at least one judged verify", "missing": "As runtime.accepted_task.",
    },
    "runtime.time_to_completion": {
        "version": 1, "producer": _RUNTIME, "unit": "seconds per command", "statistic": "median_interpolated",
        "definition": "Summed provider duration_seconds of one command's rows. Browser time (browser_seconds) and runtime test time (tests_seconds) are not included.",
        "denominator": "commands with at least one measured duration", "missing": "Unmeasured commands are counted apart, never averaged in.",
    },
    "runtime.evidence_reuse_rate": {
        "version": 1, "producer": _RUNTIME, "unit": "command", "statistic": "rate",
        "definition": "Exploration and reasoning commands served from a stored artifact. verify-browser drafts are not eligible.",
        "denominator": "exploration and reasoning commands", "missing": "Rows without a role are named, not placed.",
    },
    "runtime.context_avoided": {
        "version": 1, "producer": _RUNTIME, "unit": "tokens", "statistic": "sum",
        "definition": "(answer characters - digest characters) // 4 on the row carrying the digest. Report key `premium_context_avoided`.",
        "denominator": "-", "missing": "Rows without a digest contribute nothing.",
    },
    # --- runtime: tasks.jsonl -------------------------------------------------------------
    "runtime.workflow_task": {
        "version": 1, "producer": _TASKS, "unit": "event run in one MAIN_SESSION_ID", "statistic": "count",
        "definition": "Events from a session's first event to a verify whose derived verdict is pass after an edit (DEC-020). States completed, open, read_only, unknown.",
        "denominator": "-", "missing": "Events without a session are `unknown`; events before DEC-020 carry no derived verdict and stay open.",
    },
    # --- offline: measure_real_use ---------------------------------------------------------
    "real_use.session": {
        "version": 1, "producer": _REAL_USE, "unit": "Claude Code transcript", "statistic": "count",
        "definition": "One transcript file whose last bundle banner names the requested version, with at least one human prompt.",
        "denominator": "-", "missing": "Transcripts without a banner are excluded.",
    },
    "real_use.prompts_per_session": {
        "version": 1, "producer": _REAL_USE, "unit": "prompt", "statistic": "nearest_rank",
        "definition": "Human turns, excluding interrupts, compaction summaries, shell echoes and argument-less built-in slash commands.",
        "denominator": "sessions", "missing": "-",
    },
    "real_use.rebuttal": {
        "version": 1, "producer": _REAL_USE, "unit": "follow-up prompt", "statistic": "count",
        "definition": "A follow-up whose first 800 characters match the CORRECTION pattern (Indonesian and English). Recall measured 0.08 in CASE-011: a lower bound.",
        "denominator": "follow-up prompts", "missing": "-",
    },
    "real_use.active_minutes": {
        "version": 1, "producer": _REAL_USE, "unit": "minutes per session", "statistic": "nearest_rank",
        "definition": "Sum of gaps between consecutive transcript timestamps no longer than --idle-minutes (default 15).",
        "denominator": "sessions", "missing": "-",
    },
    "real_use.delegated_call": {
        "version": 2, "producer": _REAL_USE, "unit": "prompt answered by a provider", "statistic": "count",
        "definition": "Distinct prompt_id (else correlation_id) among a session's usage rows that name a provider. A usage row belongs to one session: the earliest-starting transcript that binds its MAIN_SESSION_ID (@1 joined it to every such transcript; identical on all data recorded up to 2026-10-04, where no id was bound twice). Its `delegated_runtime` block is per row, not per command.",
        "denominator": "-", "missing": "-",
    },
    # --- offline: measure_task_outcomes ----------------------------------------------------
    "outcome.coding_task": {
        "version": 1, "producer": _OUTCOMES, "unit": "labelled transcript", "statistic": "count",
        "definition": "A real_use.session labelled with a coding category (feature, bug, performance, migration, refactor) in the labels file.",
        "denominator": "-", "missing": "Unlabelled sessions are counted apart (`unlabelled_sessions`).",
    },
    "outcome.solved": {
        "version": 1, "producer": _OUTCOMES, "unit": "coding task", "statistic": "rate",
        "definition": "The first first-parent state of `main`, from session start to end + --search-days (30), holding at least --solved-share (0.8) of the lines the session left (Edit/Write/MultiEdit lines of 8+ characters it did not itself remove). 0.3 to 0.8 is partial; below is not in main.",
        "denominator": "coding tasks", "missing": "A repository without `main` grades every task not_in_main.",
    },
    "outcome.solved_fixed": {
        "version": 1, "producer": _OUTCOMES, "unit": "solved task", "statistic": "count",
        "definition": "A solved task whose lines changed on main within --fix-hours (24) of landing, in a commit by an author of the commits that brought them in (author window: -5 min before start, +1 min after landing).",
        "denominator": "-", "missing": "-",
    },
    "outcome.context_final": {
        "version": 1, "producer": _OUTCOMES, "unit": "tokens", "statistic": "median_stdlib",
        "definition": "input + cache_read + cache_creation of the task's last main-agent message. p90 uses p90_floor.",
        "denominator": "tasks with usage", "missing": "Tasks without usage are left out of the statistic.",
    },
    "outcome.context_saved": {
        "version": 2, "producer": _OUTCOMES, "unit": "tokens per task", "statistic": "median_stdlib",
        "definition": "Second agent's fresh input (input - cached) minus what returned to the main agent (response characters // 4), floored at 0; reasoning is reported apart, not counted. Usage rows belong to the earliest-starting transcript that binds their MAIN_SESSION_ID. p90 uses p90_floor. (@1 computed the same net, joined rows to every binding transcript, and its definition text wrongly added reasoning.)",
        "denominator": "coding tasks", "missing": "Rows without provider counts fall back to estimates; such tasks are counted in context_saved_partly_estimated_tasks.",
    },
}


def tool_commit(repo_root: Path | None = None) -> str | None:
    """The producing tool's commit, with `+dirty` when its tree has uncommitted changes."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                              **osutil.hidden_run_kwargs())
        if head.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10, **osutil.hidden_run_kwargs())
        return head.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return None


def inputs_digest(paths) -> dict:
    """A hash over the inputs' contents, with no path in it: names would identify projects."""
    digest = hashlib.sha256()
    count = 0
    total = 0
    for path in sorted(str(p) for p in paths):
        file = Path(path)
        if not file.is_file():
            continue
        data = file.read_bytes()
        digest.update(hashlib.sha256(data).digest())
        count += 1
        total += len(data)
    return {"files": count, "bytes": total, "sha256": digest.hexdigest()}


def provenance(producer: str, params: dict, inputs, metric_ids=None) -> dict:
    """The stamp every benchmark output carries."""
    ids = list(metric_ids) if metric_ids is not None else [k for k, v in REGISTRY.items() if v["producer"] == producer]
    return {
        "metrics_version": METRICS_VERSION,
        "metrics": {mid: REGISTRY[mid]["version"] for mid in ids if mid in REGISTRY},
        "producer": producer,
        "tool_commit": tool_commit(),
        "params": params,
        "inputs": inputs_digest(inputs),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recount": RECOUNT,
    }


def session_owners(transcripts: list[dict]) -> dict[str, int]:
    """Which transcript owns each MAIN_SESSION_ID: the earliest-starting one that binds it.

    A usage row is joined to a transcript by the session id it carries; a resumed or copied
    transcript can bind the same id again, and joining the row to both would count it twice.
    Index into `transcripts`; ties keep the earlier index.
    """
    order = sorted(range(len(transcripts)), key=lambda i: (transcripts[i].get("start") or "~", i))
    owners: dict[str, int] = {}
    for i in order:
        for sid in transcripts[i].get("sessions") or ():
            owners.setdefault(sid, i)
    return owners


# An absolute path in any OS's form: a figure set that leaves the machine carries none. Read
# against each key and string value, not the JSON text, so escaping cannot hide one: a drive
# (`C:\`, `C:/`), UNC (`\\server\share`, `//server/share`), a home (`~/`), and any POSIX path
# from the root with at least two segments (`/private/var/x`, `/etc/x`).
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"|(?:^|[^\\])\\\\[^\\\s]+\\"
    r"|(?:^|[\s\"'(=,;])//[^/\s]+/"
    r"|(?:^|[\s\"'(=,;])~[\\/]"
    r"|(?:^|[\s\"'(=,;])/[^/\s\"']+/"
)


def _strings(value):
    """Every key and string value inside `value`, depth first."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def write_export(report: dict, dest: Path, forbidden=()) -> Path:
    """Write a figure set meant to leave this machine, or refuse with ValueError.

    `forbidden` holds the strings that would identify the user's projects (their directory
    names); the export is refused when any of them, or any absolute path, appears in it. The
    caller decides what goes in: aggregates and the provenance stamp, never prompt text.
    """
    payload = {"export": {"schema": 1, "recount": RECOUNT}, **report}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    strings = list(_strings(payload))
    leaks = sorted({w for w in forbidden if w and len(w) > 2 and any(w in s for s in strings)})
    if leaks or any(_ABSOLUTE_PATH.search(s) for s in strings):
        raise ValueError(f"export refused: it would carry {len(leaks)} project name(s) or a path")
    out = Path(dest)
    out.write_text(text + "\n", encoding="utf-8")
    return out
