"""The P1 metrics, asserted on the distinctions that would be invisible if broken.

A wrong aggregate reads exactly like a right one, so these checks are aimed at the places
where the definition is a judgement call rather than arithmetic: acceptance counted per
task instead of per call, first-pass correctness kept apart from eventual acceptance, and
every rate carrying the denominator that makes it readable.
"""

import shutil
import tempfile
from pathlib import Path

from core import telemetry
from core.contracts import UsageRecord
from core.runtime_io import write_quality_record, write_usage_record
from tests.checks.support import assert_true


def _usage(**kwargs) -> UsageRecord:
    return UsageRecord(**kwargs)


def _acceptance_is_per_task() -> None:
    rows = [
        _usage(correlation_id="t1", command="verify", accepted=False),
        _usage(correlation_id="t1", command="verify", accepted=True),
        _usage(correlation_id="t2", command="verify", accepted=True),
        _usage(correlation_id="t3", command="explore"),
    ]
    acceptance = telemetry.accepted_tasks(rows)
    assert_true(
        acceptance["accepted"] == 2 and acceptance["judged"] == 2,
        "acceptance counts distinct tasks, not calls — counting calls rewards re-running "
        "verify until it goes green",
    )

    first = telemetry.first_pass_correctness(rows)
    assert_true(
        first["first_pass"] == 1 and first["judged"] == 2,
        "t1 passed eventually and not first time; collapsing that into acceptance hides "
        "every retry the workflow needed",
    )

    again = telemetry.rework(rows)
    assert_true(
        again["reworked_tasks"] == 1 and again["extra_verification_rounds"] == 1,
        "rework must count the tasks that came back, and how many extra rounds they cost",
    )


def _unjudged_work_is_not_incorrect() -> None:
    rows = [_usage(correlation_id="t9", command="explore")]
    first = telemetry.first_pass_correctness(rows)
    assert_true(
        first["judged"] == 0 and first["rate"] is None,
        "work that was never verified is unknown, not wrong — scoring it as a failure "
        "would punish every command that does not end in a verify",
    )


def _report_reports_its_denominators() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-telemetry-"))
    try:
        write_usage_record(
            root,
            _usage(
                correlation_id="t1",
                command="explore",
                estimated_input_tokens=400,
                estimated_output_tokens=600,
                premium_context_avoided_tokens=225,
                duration_seconds=10.0,
                token_source="estimated",
            ).to_dict(),
        )
        write_usage_record(
            root,
            _usage(
                correlation_id="t1",
                command="verify",
                accepted=True,
                estimated_input_tokens=100,
                estimated_output_tokens=100,
                token_source="estimated",
            ).to_dict(),
        )
        summary = telemetry.report(root)

        assert_true(
            summary["cost"]["total_tokens"] == 1200
            and summary["accepted_tasks"]["tokens_per_accepted_task"] == 1200,
            "the headline number is total tokens over accepted tasks; got "
            f"{summary['accepted_tasks']}",
        )
        assert_true(
            summary["cost"]["token_source"] == ["estimated"],
            "a cost figure that cannot tell an estimate from a measurement is not a cost figure",
        )
        assert_true(
            summary["time_to_completion_seconds"]["unmeasured_calls"] == 1,
            "calls with no recorded duration must be named, not averaged away — a mean over "
            "half the calls is a different claim from a mean over all of them",
        )
        assert_true(
            summary["premium_context_avoided"]["total_tokens"] == 225,
            "premium context avoided is the digest-first contract's justification as a number",
        )
        assert_true(
            summary["tests"]["recorded"] is False and summary["tests"]["rate"] is None,
            "an empty quality stream is not a failing one; a fresh workspace must not read "
            "as a repo whose tests never pass",
        )

        write_quality_record(root, {"kind": "tests", "ok": True, "suites": ["scenario"]})
        write_quality_record(root, {"kind": "tests", "ok": False, "suites": ["scenario"]})
        rate = telemetry.test_pass_rate(root)
        assert_true(
            rate["passed_runs"] == 1 and rate["total_runs"] == 2 and rate["rate"] == 0.5,
            "test pass rate reads the quality stream, which records check runs rather than "
            "delegated calls",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _torn_row_is_skipped_not_fatal() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-torn-"))
    try:
        write_usage_record(root, _usage(command="explore").to_dict())
        path = root / ".workflow" / "usage.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"command": "verify", "ok"\n')  # killed mid-write
        rows = telemetry.load_usage(root)
        assert_true(
            len(rows) == 1,
            "a torn final line is a realistic state for an append-only stream; dropping "
            "that row is right, refusing to report anything is not",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_telemetry_metrics() -> None:
    _acceptance_is_per_task()
    _unjudged_work_is_not_incorrect()
    _report_reports_its_denominators()
    _torn_row_is_skipped_not_fatal()
