"""One bounded continuation when a reply stops short of its contract.

The failure this covers is not a dead call and not a refusal: the second agent reads
everything it was asked to read, runs out of room, and hands back a work-state summary
that ends in "continue if you have next steps". The work happened; the contract was
never emitted. Failing there discards a completed read and sends the user off to gather
evidence that already exists in a live session.
"""

import os
import shutil
import tempfile
from pathlib import Path

from core.evidence.contract import validate_verification_contract
from core.provider.continuation import _VERIFY_SHAPE_KINDS
from core.provider.executor import Executor
from core.runtime.state import ensure_workflow_workspace

from tests.checks.support import assert_true

_STALLED = (
    "## Work State\n"
    "Read all 11 changed files. Evidence gathered for all 6 fixes.\n"
    "Continue if you have next steps, or stop and ask for clarification.\n"
)

_EVIDENCE = (
    "[EVIDENCE]\n"
    "confidence: high\n"
    "grounded:\n"
    "- entry point at app/main.py [app/main.py:1]\n"
    "assumptions: none\n"
    "dependencies: none\n"
    "dependents: none\n"
    "external: none\n"
    "scope_covered:\n"
    "- app/main.py\n"
    "scope_not_covered: none\n"
    "implications: none\n"
    "uncertainties: none\n"
    "[DIGEST]\n"
    "summary: resumed and finished.\n"
    "key_findings:\n"
    "- entry point located\n"
    "evidence_basis: grounded\n"
    "risk_level: low\n"
    "recommended_next_action: none\n"
    "confidence: high\n"
)

# The costlier stall: the read finished, the anchors are on the page, and the reply dies
# partway through its own digest. Observed in the field on three of four delegated calls —
# and the recovery discarded the whole body, keeping a digest that described evidence no
# longer present anywhere. The digest header below is deliberately cut mid-block.
_TRUNCATED_BODY = (
    "[EVIDENCE]\n"
    "confidence: high\n"
    "grounded:\n"
    "- token estimate is chars//4 [core/executor.py:808]\n"
    "- job timestamps persisted [core/job_manager.py:191]\n"
    "assumptions: none\n"
    "scope_covered:\n"
    "- core/executor.py\n"
    "uncertainties: none\n"
    "[DIGEST]\n"
    "summary: telemetry mapped, cost fie"
)

_DIGEST_ONLY = (
    "[DIGEST]\n"
    "summary: telemetry mapped, cost fields absent.\n"
    "key_findings:\n"
    "- token counts are estimates\n"
    "evidence_basis: grounded\n"
    "risk_level: low\n"
    "recommended_next_action: none\n"
    "confidence: high\n"
)

_VERIFICATION = (
    "[VERIFICATION]\n"
    "verdict: DONE\n"
    "blocking_findings: none\n"
    "escalations: none\n"
    "notes: none\n"
    "checks_run:\n"
    "- ran python tests/run.py -> success\n"
    "not_verified: none\n"
    "confidence: high\n"
    "[DIGEST]\n"
    "summary: every claim checked.\n"
)


class _ScriptedAdapter:
    """Returns queued replies in order, repeating the last one once exhausted."""

    def __init__(self, replies: list[str], provider_session_id="ses_scripted") -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.provider_session_id = provider_session_id

    def run(self, prompt, session, model=None, work_dir=None) -> dict:
        self.calls.append({"prompt": prompt, "session": dict(session)})
        content = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        return {
            "ok": True,
            "content": content,
            "meta": {"provider_session_id": self.provider_session_id},
        }


def _session(provider_session_id="ses_scripted") -> dict:
    return {
        "session_id": "continuation-session",
        "provider_session_id": provider_session_id,
    }


def _test_contract_continuation() -> None:
    root = Path(tempfile.mkdtemp(prefix="continuation-"))
    try:
        ensure_workflow_workspace(root, os.getenv("AGENT_PATH"))

        # A reply that reached the contract on its own must cost exactly one call. The
        # whole mechanism is worthless if it also taxes the runs that were already fine.
        clean = _ScriptedAdapter([_EVIDENCE])
        first_try = Executor(adapter=clean).execute(
            "analyze", "map the thing", _session(), str(root)
        )
        assert_true(
            first_try.get("ok") and len(clean.calls) == 1,
            f"a complete reply must not be re-prompted: calls={len(clean.calls)} {first_try.get('meta')}",
        )
        assert_true(
            "continuation_attempts" not in (first_try.get("meta") or {}),
            "a run that needed no continuation must not carry continuation meta",
        )

        # The real case: stalled first, contract second.
        stalled = _ScriptedAdapter([_STALLED, _EVIDENCE])
        resumed = Executor(adapter=stalled).execute(
            "analyze", "map the thing", _session(), str(root)
        )
        meta = resumed.get("meta") or {}
        assert_true(
            resumed.get("ok"),
            f"a reply recovered by continuation must succeed, not fail: {meta}",
        )
        assert_true(
            meta.get("continuation_recovered") is True
            and meta.get("continuation_attempts") == 1,
            f"the recovery must be recorded, not silent: {meta}",
        )
        assert_true(
            "grounded:" in (resumed.get("content") or ""),
            "the continuation's evidence must replace the stalled summary",
        )
        assert_true(
            len(stalled.calls) == 2
            and "NOT redo it" in stalled.calls[1]["prompt"]
            and stalled.calls[1]["session"].get("provider_session_id")
            == "ses_scripted",
            f"the follow-up must resume the SAME session and forbid a restart: {stalled.calls[-1]['prompt'][:120]!r}",
        )

        # A first reply that already carries the evidence must KEEP it. The follow-up only
        # ever supplies the missing block, so replacing the reply with it destroys the read
        # the run paid for and leaves an artifact with a digest and no anchors behind it.
        truncated = _ScriptedAdapter([_TRUNCATED_BODY, _DIGEST_ONLY])
        salvaged = Executor(adapter=truncated).execute(
            "explore", "map the telemetry", _session(), str(root)
        )
        salvaged_content = salvaged.get("content") or ""
        salvaged_meta = salvaged.get("meta") or {}
        assert_true(
            salvaged.get("ok") and salvaged_meta.get("continuation_recovered") is True,
            f"a truncated-digest reply must recover, not fail: {salvaged_meta}",
        )
        assert_true(
            "core/executor.py:808" in salvaged_content
            and "core/job_manager.py:191" in salvaged_content,
            "the continuation must not discard anchors the first reply already delivered",
        )
        assert_true(
            "cost fields absent" in salvaged_content
            and salvaged_meta.get("continuation_merged") is True,
            f"the recovered digest must be joined onto the body, not replace it: {salvaged_meta}",
        )
        assert_true(
            salvaged_content.count("[DIGEST]") == 1,
            "the truncated digest header must be dropped so the complete block is the one read",
        )

        # A reply that quoted the output template before stalling carries TWO [DIGEST]
        # markers. The contract reads the first one, so trimming only the trailing marker
        # left the quoted header in front and the join still read as damaged — the merge
        # was silently rejected and the body lost anyway. Observed live before the cut was
        # moved to the first marker.
        quoted = _TRUNCATED_BODY.replace(
            "assumptions: none",
            "assumptions: none\nformat note: the [DIGEST] block wants summary/key_findings",
        )
        twice = _ScriptedAdapter([quoted, _DIGEST_ONLY])
        rescued = Executor(adapter=twice).execute(
            "explore", "map the telemetry", _session(), str(root)
        )
        rescued_content = rescued.get("content") or ""
        assert_true(
            (rescued.get("meta") or {}).get("continuation_merged") is True
            and "core/executor.py:808" in rescued_content,
            f"a quoted [DIGEST] in the stalled reply must not block the merge: {rescued.get('meta')}",
        )
        assert_true(
            rescued_content.count("[DIGEST]") == 1,
            "every [DIGEST] marker from the stalled reply must be cut, not just the last",
        )

        # Bounded: an agent that cannot produce the contract twice is a real failure, and
        # must surface as one rather than being retried until the quota is gone.
        hopeless = _ScriptedAdapter([_STALLED, _STALLED])
        failed = Executor(adapter=hopeless).execute(
            "analyze", "map the thing", _session(), str(root)
        )
        assert_true(
            not failed.get("ok")
            and (failed.get("meta") or {}).get("error_type") == "invalid_evidence",
            f"two contract-less replies must still fail: {failed.get('meta')}",
        )
        assert_true(
            len(hopeless.calls) == 2,
            f"the continuation must be attempted once, never looped: calls={len(hopeless.calls)}",
        )
        assert_true(
            "continuation" in (failed.get("content") or "")
            or "continuation" in str(failed.get("meta")),
            f"the failure must say a continuation was already tried: {failed.get('meta')}",
        )

        # Without a captured provider session there is no thread to continue; asking again
        # would open an empty one. Fail, but say which of the two happened.
        orphan = _ScriptedAdapter([_STALLED], provider_session_id=None)
        no_session = Executor(adapter=orphan).execute(
            "analyze", "map the thing", _session(None), str(root)
        )
        assert_true(
            not no_session.get("ok") and len(orphan.calls) == 1,
            f"a sessionless stall must not be re-prompted into a fresh thread: calls={len(orphan.calls)}",
        )

        # verify has its own contract, and the same stall. A block that is merely absent
        # must be asked for; a verdict the agent reached deliberately must not be.
        verify_stalled = _ScriptedAdapter([_STALLED, _VERIFICATION])
        verified = Executor(adapter=verify_stalled).execute(
            "verify", "check the six fixes", _session(), str(root)
        )
        verify_meta = verified.get("meta") or {}
        assert_true(
            verified.get("ok") and verify_meta.get("continuation_recovered") is True,
            f"a verify reply missing its contract must be continued too: {verify_meta}",
        )
        # Imported rather than restated: two copies of this set drifted apart once already,
        # and the copy here is the one that decides whether the assertion means anything.
        shape_kinds = _VERIFY_SHAPE_KINDS
        assessment = validate_verification_contract(verified.get("content") or "")
        assert_true(
            not [
                warning
                for warning in assessment.get("warnings") or []
                if warning.get("kind") in shape_kinds
            ],
            f"the continued verify output must satisfy the contract: {assessment}",
        )

        # An agent that DID emit the block and honestly declared the work incomplete has
        # finished. Re-prompting it would be asking it to talk itself out of a true answer.
        honest = _VERIFICATION.replace("verdict: DONE", "verdict: INCOMPLETE").replace(
            "not_verified: none", "not_verified:\n- hooks/*.sh never executed"
        )
        settled = _ScriptedAdapter([honest])
        settled_result = Executor(adapter=settled).execute(
            "verify", "check the six fixes", _session(), str(root)
        )
        assert_true(
            settled_result.get("ok") and len(settled.calls) == 1,
            f"an honest incomplete verdict must be left alone: calls={len(settled.calls)}",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
