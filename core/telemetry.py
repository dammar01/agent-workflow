"""Aggregation over the usage stream. Reads history, never writes it.

Every metric here is DERIVED at read time from `UsageRecord` rows rather than counted as
calls happen. That is the whole design: a counter incremented at runtime freezes one
definition of a metric forever, and the definitions in this file are exactly the ones
worth being able to change later — "accepted", "rework", and "first-pass correct" are
judgement calls, not facts. Re-deriving means a better definition re-reads history
instead of invalidating it.

Every metric also reports its own denominator. A rate computed over four calls and a rate
computed over four hundred are different claims, and a dashboard that shows only the
percentage lets the reader mistake the first for the second.
"""

import json
from pathlib import Path

from core.contracts import QUALITY_STREAM_NAME, USAGE_STREAM_NAME, UsageRecord


def _stream_path(project_root, name: str) -> Path:
    return Path(project_root) / ".workflow" / name


def load_usage(project_root) -> list[UsageRecord]:
    """Every usage row on disk, oldest first. Unreadable rows are skipped, not fatal.

    A stream is append-only and written fail-open, so a torn final line is a realistic
    state — a process killed mid-write. Dropping that one row is right; refusing to
    report anything because of it is not.
    """
    path = _stream_path(project_root, USAGE_STREAM_NAME)
    rows: list[UsageRecord] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            rows.append(UsageRecord.from_dict(payload))
    return rows


def load_quality(project_root) -> list[dict]:
    """Recorded test/security check outcomes, oldest first."""
    path = _stream_path(project_root, QUALITY_STREAM_NAME)
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def _by_correlation(rows: list[UsageRecord]) -> dict[str, list[UsageRecord]]:
    grouped: dict[str, list[UsageRecord]] = {}
    for row in rows:
        if row.correlation_id:
            grouped.setdefault(row.correlation_id, []).append(row)
    return grouped


def _cost(rows: list[UsageRecord]) -> dict:
    """Token spend, kept separate from any currency conversion.

    No price table here on purpose. Prices change per provider, per model, and per month;
    baking one in would make every historical figure silently wrong the day it moved. The
    denominator a cost-per-task question actually needs is tokens, and multiplying that by
    today's rate is a decision for whoever reads the report.
    """
    inputs = [row.estimated_input_tokens or 0 for row in rows]
    outputs = [row.estimated_output_tokens or 0 for row in rows]
    sources = {row.token_source for row in rows if row.token_source}
    return {
        "input_tokens": sum(inputs),
        "output_tokens": sum(outputs),
        "total_tokens": sum(inputs) + sum(outputs),
        # `estimated` means chars//4, not a provider count. A cost figure that cannot
        # tell an estimate from a measurement is not a cost figure.
        "token_source": sorted(sources) or ["unknown"],
    }


def accepted_tasks(rows: list[UsageRecord]) -> dict:
    """Distinct pieces of work whose verification passed.

    Counted per correlation_id, not per call: a task verified twice is one accepted task,
    and counting calls would reward re-running verify until it went green.
    """
    judged: set[str] = set()
    accepted: set[str] = set()
    for correlation, group in _by_correlation(rows).items():
        verdicts = [row.accepted for row in group if row.accepted is not None]
        if not verdicts:
            continue
        judged.add(correlation)
        if any(verdicts):
            accepted.add(correlation)
    return {
        "accepted": len(accepted),
        "judged": len(judged),
        "accepted_ids": sorted(accepted),
    }


def first_pass_correctness(rows: list[UsageRecord]) -> dict:
    """Share of judged tasks that passed on their FIRST verification.

    The distinction from plain acceptance is the entire point: a task that failed twice
    and passed on the third attempt is accepted, and it is not first-pass correct. Only
    tasks that were judged at all appear in the denominator — never-verified work is
    unknown, not incorrect.
    """
    first_pass = 0
    judged = 0
    for group in _by_correlation(rows).values():
        verdicts = [row for row in group if row.accepted is not None]
        if not verdicts:
            continue
        judged += 1
        if verdicts[0].accepted:
            first_pass += 1
    return {
        "first_pass": first_pass,
        "judged": judged,
        "rate": round(first_pass / judged, 3) if judged else None,
    }


def rework(rows: list[UsageRecord]) -> dict:
    """Tasks that needed more than one verification round.

    Read together with first_pass_correctness rather than instead of it: this counts how
    often work came back, that one counts how often it came back green the first time.
    """
    reworked = 0
    judged = 0
    extra_rounds = 0
    for group in _by_correlation(rows).values():
        verdicts = [row for row in group if row.accepted is not None]
        if not verdicts:
            continue
        judged += 1
        if len(verdicts) > 1:
            reworked += 1
            extra_rounds += len(verdicts) - 1
    return {
        "reworked_tasks": reworked,
        "judged": judged,
        "extra_verification_rounds": extra_rounds,
        "rate": round(reworked / judged, 3) if judged else None,
    }


def security_pass_rate(rows: list[UsageRecord]) -> dict:
    """Share of delegated calls whose output carried nothing credential-shaped.

    Measures the redaction boundary firing, which is a weaker claim than "no secret
    leaked" and is stated as such: a call with zero redactions is a call where the
    scanner found nothing, not a proof that nothing was there.
    """
    total = len(rows)
    clean = sum(1 for row in rows if not row.redactions)
    return {
        "clean_calls": clean,
        "total_calls": total,
        "rate": round(clean / total, 3) if total else None,
    }


def test_pass_rate(project_root) -> dict:
    """Recorded outcomes of the test suite, from the quality stream.

    Separate from the usage stream because it measures a different actor: usage rows are
    delegated calls the runtime made, quality rows are check runs someone (CI, a person)
    performed on the repo. Fusing them would let a green test run inflate the count of
    delegated work.
    """
    rows = [row for row in load_quality(project_root) if row.get("kind") == "tests"]
    total = len(rows)
    passed = sum(1 for row in rows if row.get("ok"))
    return {
        "passed_runs": passed,
        "total_runs": total,
        "rate": round(passed / total, 3) if total else None,
        # An empty stream is not a failing one. Saying so stops a fresh workspace from
        # reading as a repo whose tests never pass.
        "recorded": bool(total),
    }


def report(project_root) -> dict:
    """Every P1 metric, over the whole recorded history of this project."""
    rows = load_usage(project_root)
    durations = [row.duration_seconds for row in rows if row.duration_seconds is not None]
    avoided = [
        row.premium_context_avoided_tokens
        for row in rows
        if row.premium_context_avoided_tokens is not None
    ]
    acceptance = accepted_tasks(rows)
    cost = _cost(rows)

    by_command: dict[str, int] = {}
    for row in rows:
        by_command[row.command] = by_command.get(row.command, 0) + 1

    accepted_count = acceptance["accepted"]
    return {
        "calls": len(rows),
        "calls_by_command": dict(sorted(by_command.items())),
        "cost": cost,
        "accepted_tasks": {
            "accepted": accepted_count,
            "judged": acceptance["judged"],
            # The headline P1 number. `None` rather than a large-looking figure when
            # nothing has been accepted yet: dividing by zero accepted tasks does not
            # produce an expensive project, it produces no answer.
            "tokens_per_accepted_task": (
                round(cost["total_tokens"] / accepted_count) if accepted_count else None
            ),
        },
        "premium_context_avoided": {
            "total_tokens": sum(avoided),
            "measured_calls": len(avoided),
            "provider_calls_avoided": sum(1 for row in rows if row.provider_call_avoided),
        },
        "time_to_completion_seconds": {
            "mean": _mean(durations),
            "median": _median(durations),
            "measured_calls": len(durations),
            # Unmeasured calls are named, not averaged away. A mean over a third of the
            # calls is a different claim from a mean over all of them.
            "unmeasured_calls": len(rows) - len(durations),
        },
        "first_pass_correctness": first_pass_correctness(rows),
        "rework": rework(rows),
        "security": security_pass_rate(rows),
        "tests": test_pass_rate(project_root),
    }
