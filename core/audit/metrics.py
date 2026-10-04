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
recounted on the maintainer's machine only. `RECOUNT` says so in every stamp, so no output
can be read as reproducible by anyone with a clone.

The registry is documented in docs/evaluation/metrics.md; a test keeps the two in step.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from utils import osutil

METRICS_VERSION = 1

RECOUNT = (
    "maintainer machine only: inputs are private transcripts, usage streams and git history; "
    "the method is public, the data is not"
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
        "version": 1, "producer": _REAL_USE, "unit": "prompt answered by a provider", "statistic": "count",
        "definition": "Distinct prompt_id (else correlation_id) among a session's usage rows that name a provider. Its `delegated_runtime` block is per row, not per command.",
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
        "version": 1, "producer": _OUTCOMES, "unit": "tokens per task", "statistic": "median_stdlib",
        "definition": "Second agent's fresh input (input - cached) plus reasoning, minus what returned to the main agent (response characters // 4). p90 uses p90_floor.",
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
