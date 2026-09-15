"""Classification and normalisation: origins, the verdict matrix, the canonical contract."""

from __future__ import annotations

from core.evidence.contract import validate_verification_contract, verify_exit_status
from core.evidence.e2e.classify import ORIGIN_APP, ORIGIN_HARNESS, ORIGIN_UNKNOWN, build_report, classify_step
from core.evidence.e2e.normalize import evidence_block, to_verification
from tests.checks.support import assert_true

_SCENARIO = {
    "version": 1,
    "claims": [
        {"id": "login", "severity": "blocking"},
        {"id": "banner", "severity": "non_blocking"},
    ],
    "steps": [],
}


def _progress(step, action, status, claim_id=None, **extra) -> dict:
    return {"type": "progress", "step": step, "action": action, "status": status, "claim_id": claim_id, **extra}


def _events(*progress, finished=True, observations=()):
    events = [*progress, *observations]
    if finished:
        events.append({"type": "result", "status": "finished"})
    return events


def _agrees(norm: dict) -> bool:
    return validate_verification_contract(norm["content"])["verdict"] == norm["verdict"]


def _test_e2e_classification_and_verdicts() -> None:
    # --- origin per failure ---------------------------------------------------------
    assert_true(classify_step({"action": "expect_url", "error": {"kind": "assertion"}}) == ORIGIN_APP, "a failed URL assertion is the app's")
    assert_true(classify_step({"action": "click", "error": {"kind": "selector_missing"}, "selector_provenance": "heuristic"}) == ORIGIN_HARNESS, "a heuristic selector that misses is the harness's")
    assert_true(classify_step({"action": "click", "error": {"kind": "selector_missing"}, "selector_provenance": "source", "page_stable": False}) == ORIGIN_UNKNOWN, "a source selector missing on an unsettled page is unknown")
    assert_true(classify_step({"action": "click", "error": {"kind": "selector_missing"}, "selector_provenance": "source", "page_stable": True}) == ORIGIN_APP, "a source selector missing on a settled page is the app's")
    assert_true(classify_step({"action": "click", "error": {"kind": "timeout"}}) == ORIGIN_UNKNOWN, "a timeout is not attributed")
    assert_true(classify_step({"action": "expect_dom", "error": {"kind": "assertion"}, "selector_provenance": "heuristic"}) == ORIGIN_UNKNOWN, "a DOM assertion on a guessed selector proves nothing either way")

    # --- report: pass ------------------------------------------------------------------
    report = build_report(_events(_progress(1, "goto", "passed"), _progress(2, "expect_url", "passed", "login"), _progress(3, "expect_dom", "passed", "banner")), _SCENARIO)
    assert_true(report["browser_verdict"] == "pass" and report["claims"]["login"]["status"] == "proven", f"all claims proven is pass: {report['browser_verdict']}")

    # --- report: blocking claim fails → fail; non-blocking only → pass with a note --------
    report = build_report(_events(_progress(1, "expect_url", "failed", "login", error={"kind": "assertion"}, expected={"contains": "/d"}, actual={"url": "/l"})), _SCENARIO)
    assert_true(report["browser_verdict"] == "fail" and report["claims"]["login"]["origin"] == ORIGIN_APP, "a blocking app failure is fail")
    report = build_report(_events(_progress(1, "expect_url", "passed", "login"), _progress(2, "expect_dom", "failed", "banner", error={"kind": "assertion"}, selector_provenance="source")), _SCENARIO)
    assert_true(report["browser_verdict"] == "pass", f"a non-blocking failure does not fail the run: {report['browser_verdict']} {report['reason']}")

    # --- report: harness / unknown / early end → incomplete with reason -----------------
    report = build_report(_events(_progress(1, "click", "failed", None, error={"kind": "selector_missing"}, selector_provenance="heuristic"), _progress(2, "expect_url", "skipped", "login")), _SCENARIO)
    assert_true(report["browser_verdict"] == "incomplete" and report["reason"] == "harness_error", f"harness failure is incomplete: {report['reason']}")
    report = build_report(_events(_progress(1, "click", "failed", "login", error={"kind": "selector_missing"}, selector_provenance="source", page_stable=False)), _SCENARIO)
    assert_true(report["reason"] == "unknown_origin", "unknown stays unknown, never promoted to harness")
    report = build_report(_events(_progress(1, "goto", "passed"), finished=False), _SCENARIO)
    assert_true(report["browser_verdict"] == "incomplete", "a run that never reported result is incomplete")
    report = build_report(_events(_progress(1, "goto", "passed")), _SCENARIO, run_meta={"timed_out": True})
    assert_true(report["reason"] == "timeout", "a supervisor timeout names itself")
    report = build_report([{"type": "harness", "reason": "browser_missing", "detail": "x"}, {"type": "result", "status": "aborted"}], _SCENARIO)
    assert_true(report["reason"] == "browser_missing", "a player harness event carries its reason")

    # --- observations: same-origin 5xx on the main request fails; third-party noise warns --
    five = {"type": "observation", "kind": "http_5xx", "url": "http://localhost/api", "status": 500, "detail": "", "same_origin": True, "main_request": True}
    noise = {"type": "observation", "kind": "http_5xx", "url": "https://cdn.example/x", "status": 503, "detail": "", "same_origin": False, "main_request": False}
    report = build_report(_events(_progress(1, "expect_url", "passed", "login"), _progress(2, "expect_dom", "passed", "banner"), observations=[five]), _SCENARIO)
    assert_true(report["browser_verdict"] == "fail" and report["app_errors"], "a same-origin 5xx on the main request fails even with green assertions")
    report = build_report(_events(_progress(1, "expect_url", "passed", "login"), _progress(2, "expect_dom", "passed", "banner"), observations=[noise]), _SCENARIO)
    assert_true(report["browser_verdict"] == "pass" and not report["app_errors"], "third-party noise is a warning, not a fail")

    # --- normalise: the matrix, and the validator agreeing every time --------------------
    clean_review = "[VERIFICATION]\nverdict: DONE\n\nblocking_findings:\n- none\n\nescalations:\n- none\n\nnotes:\n- none\n\nchecks_run:\n- read the code\n\nnot_verified:\n- none\n\nconfidence: high — fine\n"
    blocking_review = clean_review.replace("verdict: DONE", "verdict: NEEDS FIX").replace(
        "blocking_findings:\n- none", "blocking_findings:\n- severity: high | origin: introduced | scope_relation: in_scope\n  problem: wrong role redirect [src/a.ts:1]"
    )
    incomplete_review = clean_review.replace("verdict: DONE", "verdict: INCOMPLETE")

    passing = build_report(_events(_progress(1, "expect_url", "passed", "login"), _progress(2, "expect_dom", "passed", "banner")), _SCENARIO)
    failing = build_report(_events(_progress(1, "expect_url", "failed", "login", error={"kind": "assertion"}, expected={"contains": "/d"}, actual={"url": "/l"})), _SCENARIO)
    stuck = build_report(_events(_progress(1, "goto", "passed")), _SCENARIO, run_meta={"stalled": True})

    cases = [
        ("fail + reviewer DONE", failing, clean_review, "fail", "NEEDS FIX"),
        ("fail + reviewer NEEDS FIX", failing, blocking_review, "fail", "NEEDS FIX"),
        ("pass + reviewer clean", passing, clean_review, "pass", "DONE"),
        ("pass + reviewer blocking", passing, blocking_review, "fail", "NEEDS FIX"),
        ("pass + reviewer incomplete", passing, incomplete_review, "incomplete", "INCOMPLETE"),
        ("incomplete + reviewer clean", stuck, clean_review, "incomplete", "INCOMPLETE"),
    ]
    for name, report, review, verdict, declared in cases:
        norm = to_verification(report, reviewer_content=review)
        assert_true(norm["verdict"] == verdict and norm["declared"] == declared, f"{name}: expected {verdict}/{declared}, got {norm['verdict']}/{norm['declared']}")
        assert_true(_agrees(norm), f"{name}: the shared validator must reach the same verdict:\n{norm['content']}")
    overridden = to_verification(failing, reviewer_content=clean_review)
    assert_true(any(w["kind"] == "reviewer_verdict_overridden" for w in overridden["warnings"]), "a reviewer pass over a browser fail is flagged")
    assert_true("origin: unknown" in overridden["content"] and "evidence_source: e2e_runtime" in overridden["content"], "runtime findings are tagged fail-closed with a temporal unknown")
    assert_true("origin: app" not in overridden["content"], "app/harness never becomes an origin tag")

    # A reviewer that files a blocking-class finding under the wrong heading: the shared
    # validator routes by tags, so the normaliser must too — never INCOMPLETE vs fail.
    misfiled = clean_review.replace(
        "escalations:\n- none",
        "escalations:\n- severity: high | origin: introduced | scope_relation: in_scope\n  problem: session cookie not httpOnly [src/auth.ts:9]",
    )
    norm = to_verification(passing, reviewer_content=misfiled)
    assert_true(norm["verdict"] == "fail" and norm["declared"] == "NEEDS FIX", f"a misfiled blocking finding still fails: {norm['verdict']}/{norm['declared']}")
    assert_true(_agrees(norm), f"misfiled finding: validator and normaliser agree:\n{norm['content']}")
    body = norm["content"].split("blocking_findings:", 1)[1].split("escalations:", 1)[0]
    assert_true("session cookie not httpOnly" in body, "the finding is re-filed under blocking_findings with its detail")
    demoted = clean_review.replace(
        "blocking_findings:\n- none",
        "blocking_findings:\n- severity: low | origin: introduced | scope_relation: in_scope — naming nit [src/a.ts:2]",
    )
    norm = to_verification(passing, reviewer_content=demoted)
    # Not a fail: the table says note. Not a pass either — the reviewer's own contract
    # was misrouted, which the shared validator reads as incomplete, and the normaliser
    # does not launder a malformed review into a clean one.
    assert_true(norm["verdict"] == "incomplete" and _agrees(norm), f"a note-class finding filed as blocking does not fail the run: {norm['verdict']}")
    notes_body = norm["content"].split("notes:", 1)[1].split("checks_run:", 1)[0]
    assert_true("naming nit" in notes_body, "the finding is re-filed under notes")

    # A reviewer line the validator cannot read: whatever the normaliser thought, the
    # stricter reading wins and the declared verdict follows it.
    untagged = clean_review.replace("notes:\n- none", "notes:\n- looks fine to me")
    norm = to_verification(passing, reviewer_content=untagged)
    assert_true(norm["verdict"] == "incomplete" and _agrees(norm), f"an unparseable reviewer finding cannot yield pass: {norm['verdict']}")

    # Exhaustive agreement over every browser outcome x reviewer shape used above.
    for report in (passing, failing, stuck):
        for review in (None, clean_review, blocking_review, incomplete_review, misfiled, demoted, untagged):
            norm = to_verification(report, reviewer_content=review)
            assert_true(_agrees(norm), f"validator disagreement: browser={report['browser_verdict']} review={bool(review)}\n{norm['content']}")

    # Without a reviewer: a pass is a declared gap (DONE, incomplete, exit 0); a fail is a fail.
    norm = to_verification(passing)
    assert_true(norm["declared"] == "DONE" and norm["verdict"] == "incomplete" and _agrees(norm), "no reviewer = honest gap")
    assert_true("hybrid review: not run" in norm["content"], "the missing review is named, not silently absent")
    assert_true(verify_exit_status(norm["verdict"], validate_verification_contract(norm["content"])) == 0, "a declared gap alone still exits 0")
    norm = to_verification(failing, reviewer_error="invalid_evidence")
    assert_true(norm["verdict"] == "fail" and "hybrid review: not obtained" in norm["content"] and _agrees(norm), "a reviewer failure does not soften a browser fail")

    # Preflight failure: every claim lands in not_verified with the reason.
    norm = to_verification({"claims": {"login": {"severity": "blocking", "status": "unproven"}}, "browser_verdict": None}, preflight={"ok": False, "reason": "playwright_missing", "detail": "pip install playwright"})
    assert_true(norm["declared"] == "INCOMPLETE" and "playwright_missing" in norm["content"] and "e2e:login: not run" in norm["content"] and _agrees(norm), "preflight reasons reach the contract")

    # The compact evidence block: bounded, structured, no secrets by construction.
    block = evidence_block(failing, artifacts="/tmp/run/e2e", spec_notes=["label inferred"])
    for needle in ("[E2E EVIDENCE]", "browser_verdict: fail", "login | blocking | status: failed | origin: app", "assertion_failed:", "artifacts: /tmp/run/e2e", "spec_uncertainties:"):
        assert_true(needle in block, f"evidence block carries {needle!r}:\n{block}")
