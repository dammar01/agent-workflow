"""The five workflow contracts, and the usage stream they feed.

Two things are worth proving here and they are not the same thing. The first is that the
dataclasses round-trip — cheap, and the reason `from_dict` can be trusted at a version
boundary. The second is that `usage_from_result` DERIVES the right things, which is where
a telemetry bug would actually live: a metric that is quietly wrong reads exactly like a
metric that is right, and nothing downstream can tell the difference.

So the derivation assertions are deliberately about the distinctions that would be
invisible if broken: `accepted=None` versus `accepted=False`, an absent duration versus a
zero one, and a reuse hit being recorded at all.
"""

import json
import shutil
import tempfile
from pathlib import Path

from core.evidence.contracts import (
    CONTRACT_VERSION,
    EvidenceBundle,
    RouteDecision,
    TaskSpec,
    UsageRecord,
    VerificationReport,
    correlation_id_for,
    usage_from_result,
)
from core.provider.executor import Executor
from core.prompt.router import Router
from core.evidence.runtime_io import write_usage_record
from core.runtime.state import ensure_workflow_workspace
from tests.checks.support import assert_true


def _round_trips() -> None:
    spec = TaskSpec.build("EXPLORE", "  find the router  ", "sid-1", "/repo", model="m")
    assert_true(
        spec.command == "explore",
        "TaskSpec.build must normalise the command; routing and the usage stream key off it",
    )
    assert_true(
        TaskSpec.from_dict(spec.to_dict()) == spec,
        "TaskSpec must survive a dict round trip",
    )

    route = RouteDecision.from_dict(Router().route("explore"))
    assert_true(
        route.role == "exploration" and route.command == "explore",
        "RouteDecision must absorb a real Router.route() payload, not a hand-made one",
    )
    assert_true(
        RouteDecision.from_dict(route.to_dict()) == route,
        "RouteDecision must survive a dict round trip",
    )

    bundle = EvidenceBundle(artifact_path="a.md", anchors=3, reused=True)
    assert_true(
        EvidenceBundle.from_dict(bundle.to_dict()) == bundle,
        "EvidenceBundle must survive a dict round trip",
    )

    report = VerificationReport.from_dict(
        {"verdict": "pass", "declared_verdict": "DONE", "checks_run": 4}
    )
    assert_true(
        report.passed and report.checks_run == 4,
        "VerificationReport.passed must read the DERIVED verdict, not the declared one",
    )
    assert_true(
        not VerificationReport.from_dict({"verdict": "incomplete"}).passed,
        "an incomplete verification is not a pass — accepted-task accounting depends on it",
    )

    # Unknown keys are dropped, not raised on. An archived record written by a later
    # version must stay readable by the aggregator, or history breaks on every release.
    grown = UsageRecord().to_dict()
    grown["a_field_from_the_future"] = 1
    assert_true(
        UsageRecord.from_dict(grown).contract_version == CONTRACT_VERSION,
        "from_dict must ignore unknown keys instead of failing on a newer record",
    )


def _correlation_scoping() -> None:
    same = correlation_id_for("/repo", "sid-1", "Add the thing")
    assert_true(
        same == correlation_id_for("/repo", "sid-1", "  add   the thing  "),
        "correlation must survive whitespace and case, or rework counts as new work",
    )
    assert_true(
        same != correlation_id_for("/repo", "sid-2", "Add the thing"),
        "the same task in another session is a fresh attempt, not rework of the old one",
    )
    assert_true(
        same != correlation_id_for("/other", "sid-1", "Add the thing"),
        "correlation must not cross project roots",
    )


def _correlation_chain() -> None:
    """A plan and the verify that follows it must land in the usage stream as ONE task.

    This is the aggregation the P1 metrics group by: before the chain hop, plan and
    verify derived ids from their own task texts, so the same piece of work produced
    three subjects and "right on the first try" was true of none of them.
    """
    assert_true(
        TaskSpec.build("verify", "t", "sid", "/repo", correlation_id="chain-1").correlation_id
        == "chain-1",
        "an explicit correlation id must win over derivation — the chain depends on it",
    )
    assert_true(
        TaskSpec.build("verify", "t", "sid", "/repo", correlation_id=None).correlation_id
        == correlation_id_for("/repo", "sid", "t"),
        "no explicit id must fall back to deriving exactly as before",
    )

    root = Path(tempfile.mkdtemp(prefix="aw-chain-"))
    try:
        ensure_workflow_workspace(root, str(Path("main.py").resolve()))
        executor = Executor()
        executor._last_call_meta = None
        executor._finalize_runtime_result(
            {"ok": True, "content": "plan body", "meta": {}},
            root, "plan", "build feature X", "sid-1", {"session_reset": False}, False,
        )
        executor._finalize_runtime_result(
            {"ok": True, "content": _VERIFICATION, "meta": {}},
            root, "verify", "check the feature X work", "sid-1",
            {"session_reset": False}, False,
        )
        rows = [
            json.loads(line)
            for line in (root / ".workflow" / "usage.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        plan_row = next(row for row in rows if row["command"] == "plan")
        verify_row = next(row for row in rows if row["command"] == "verify")
        assert_true(
            plan_row["correlation_id"] == verify_row["correlation_id"],
            "a verify following a plan must adopt the plan's correlation id — "
            "different task texts were exactly what broke the aggregation",
        )
        assert_true(
            plan_row["correlation_id"]
            == correlation_id_for(root, "sid-1", "build feature X"),
            "the chain's identity must be the PLAN's derived id, so history and "
            "derivation still agree on what the task is",
        )

        # Another session has no chain: verify there still derives its own id.
        executor._finalize_runtime_result(
            {"ok": True, "content": _VERIFICATION, "meta": {}},
            root, "verify", "unrelated check", "sid-2",
            {"session_reset": False}, False,
        )
        rows = [
            json.loads(line)
            for line in (root / ".workflow" / "usage.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        lone = next(row for row in rows if row["session_id"] == "sid-2")
        assert_true(
            lone["correlation_id"] == correlation_id_for(root, "sid-2", "unrelated check"),
            "a chainless verify must fall back to derivation, never inherit another "
            "session's chain",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _usage_derivation() -> None:
    verify_pass = usage_from_result(
        {"ok": True, "content": "x" * 400, "meta": {"verdict": "pass"}},
        spec=TaskSpec.build("verify", "t", "sid", "/repo"),
        call_meta={"role": "verification", "model": "m", "response_chars": 400,
                   "duration_seconds": 12.5, "token_source": "estimated"},
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    assert_true(
        verify_pass.accepted is True,
        "a verify whose derived verdict is pass is the definition of an accepted task",
    )

    verify_fail = usage_from_result(
        {"ok": True, "content": "x", "meta": {"verdict": "fail"}},
        spec=TaskSpec.build("verify", "t", "sid", "/repo"),
        call_meta=None,
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    assert_true(
        verify_fail.accepted is False,
        "a judged-and-rejected task must be False, never None",
    )

    explore = usage_from_result(
        {"ok": True, "content": "x" * 1000, "digest": {"summary": "s" * 100}},
        spec=TaskSpec.build("explore", "t", "sid", "/repo"),
        call_meta={"response_chars": 1000},
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    assert_true(
        explore.accepted is None,
        "a command that never goes through verify is unjudged, not rejected — "
        "collapsing the two is what makes cost-per-accepted-task dishonest",
    )
    assert_true(
        explore.premium_context_avoided_tokens == (1000 - 100) // 4,
        "premium context avoided is the output the digest stood in for, in tokens",
    )
    assert_true(
        explore.duration_seconds is None,
        "an unrecorded duration must stay None; a zero would average into the report as fact",
    )

    reused = usage_from_result(
        {"ok": True, "content": "x" * 40, "meta": {"reused_evidence": True}},
        spec=TaskSpec.build("explore", "t", "sid", "/repo"),
        call_meta=None,
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    assert_true(
        reused.provider_call_avoided and reused.reused_evidence,
        "a reuse hit is a delegated call that cost nothing — omitting it overstates cost per task",
    )


def _stream_append() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-contracts-"))
    try:
        write_usage_record(root, UsageRecord(command="explore").to_dict())
        write_usage_record(root, UsageRecord(command="verify").to_dict())
        path = root / ".workflow" / "usage.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        assert_true(
            [row["command"] for row in rows] == ["explore", "verify"],
            "the usage stream is append-only; a rewritten history is not a measurement",
        )

        # Instrumentation must never fail the call it measures. A payload json cannot
        # serialise is the realistic version of that, so it is the one asserted.
        write_usage_record(root, {"command": "explore", "bad": object()})
        still = path.read_text(encoding="utf-8").splitlines()
        assert_true(
            len([line for line in still if line]) == 2,
            "an unserialisable record must be dropped silently, never partially written",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


_VERIFICATION = """[VERIFICATION]
verdict: DONE
blocking_findings: none
escalations: none
notes: none
checks_run:
- read core/router.py:48
not_verified: none
confidence: high
"""


def _verify_verdict_is_recorded() -> None:
    """The one ordering bug this wiring can have, asserted directly.

    `meta.verdict` is derived a layer above the executor, in job_lifecycle — so the usage
    record is assembled BEFORE it exists. Left unhandled, every verify lands with
    accepted=None and cost-per-accepted-task has no denominator at all: an empty metric
    that looks like a conservative one.

    Also asserts the result handed back is unchanged. `meta.verdict` belongs to
    job_lifecycle; measuring here must read the same validator without pre-empting the
    layer whose job it is to decide.
    """
    root = Path(tempfile.mkdtemp(prefix="aw-usage-"))
    try:
        ensure_workflow_workspace(root, str(Path("main.py").resolve()))
        executor = Executor()
        executor._last_call_meta = None
        result = {"ok": True, "content": _VERIFICATION, "meta": {}}
        returned = executor._finalize_runtime_result(
            result, root, "verify", "check it", "sid-1", {"session_reset": False}, False
        )
        row = json.loads(
            (root / ".workflow" / "usage.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert_true(
            row["verdict"] == "pass" and row["accepted"] is True,
            "a clean verification must record accepted=True; the verdict is derived later, "
            "so reading meta alone would leave every verify unjudged",
        )
        assert_true(
            "verdict" not in (returned.get("meta") or {}),
            "measuring must not mutate the result — job_lifecycle owns the verdict field",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


_GAPPY_VERIFICATION = """[VERIFICATION]
verdict: DONE
blocking_findings: none
escalations: none
notes: none
checks_run:
- read core/router.py:48
not_verified:
- the delegated path, no provider credentials here
confidence: high
"""


def _finalize_is_idempotent() -> None:
    """Finalising the same verify payload twice must not double its warnings.

    It happens twice by design: the worker finalises what it produced, and `await`
    finalises the stored output again on the way back — it cannot assume the record it
    read was ever finalised. The old `extend` turned one real gap into two rows, which
    reads as two problems and makes a clean-ish verify look worse than it is.

    Warnings from other producers must survive the merge. `contract_warnings` also
    carries executor entries (evidence contract misses, task truncation), so a replace
    would fix the duplication by dropping them.
    """
    from core.evidence.result_shaping import _finalize_verify_result

    foreign = {"kind": "task_truncated", "detail": "120 chars cut from the instruction"}
    result = {
        "ok": True,
        "content": _GAPPY_VERIFICATION,
        "meta": {"contract_warnings": [dict(foreign)]},
    }
    once = _finalize_verify_result("verify", result)
    first = list(once["meta"]["contract_warnings"])
    assert_true(
        any(item["kind"] == "verification_gap" for item in first),
        "the declared gap must be reported at least once",
    )

    twice = _finalize_verify_result("verify", once)
    assert_true(
        twice["meta"]["contract_warnings"] == first,
        "a second finalisation must add nothing; got "
        f"{len(twice['meta']['contract_warnings'])} vs {len(first)}",
    )
    # Three passes, not two. Two is the count the bug produced, so a fix asserted only at
    # two passes proves the count changed rather than that it stopped growing — and the
    # `await`-of-a-`result`-of-a-retry path can finalise more than twice.
    thrice = _finalize_verify_result("verify", twice)
    assert_true(
        thrice["meta"]["contract_warnings"] == first,
        "warnings must stop growing, not merely grow more slowly",
    )

    # No pre-existing key at all: the first producer to touch a fresh payload.
    fresh = _finalize_verify_result(
        "verify", {"ok": True, "content": _GAPPY_VERIFICATION, "meta": {}}
    )
    fresh_warnings = list(fresh["meta"]["contract_warnings"])
    assert_true(
        fresh_warnings
        and _finalize_verify_result("verify", fresh)["meta"]["contract_warnings"]
        == fresh_warnings,
        "a payload with no contract_warnings key must gain them once and only once",
    )
    assert_true(
        foreign in twice["meta"]["contract_warnings"],
        "merging must keep warnings this function did not produce — a wholesale replace "
        "would silently drop the executor's own entries",
    )

    kinds = [item["kind"] for item in twice["meta"]["contract_warnings"]]
    assert_true(
        len(kinds) == len(set(kinds)) or kinds.count("verification_gap") == 1,
        f"no warning may appear twice after two passes; got {kinds}",
    )


def _test_workflow_contracts() -> None:
    _round_trips()
    _correlation_scoping()
    _correlation_chain()
    _usage_derivation()
    _stream_append()
    _verify_verdict_is_recorded()
    _finalize_is_idempotent()
