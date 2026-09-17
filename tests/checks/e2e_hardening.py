"""The stability fixes to verify-browser that no other check owns.

Each block pins one defect that shipped: a retry that turned a flaky run into a clean pass,
a resolved credential echoed in what a step expected, reset tokens kept in the request
ledger, a headed launch on a machine with no screen, a fake player that could never pass a
cleanup, one artifact budget per retry, and a dead browser worker resumed by replaying its
writes.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.evidence.contract import validate_verification_contract
from core.evidence.e2e.browser import Session
from core.evidence.e2e.classify import build_report
from core.evidence.e2e.normalize import to_verification
from core.evidence.e2e.redact import sanitize_endpoint
from tests.checks.support import assert_true

_SCENARIO = {"version": 1, "claims": [{"id": "login", "severity": "blocking"}], "steps": []}
_CLEAN_REVIEW = (
    "[VERIFICATION]\nverdict: DONE\n\nblocking_findings:\n- none\n\nescalations:\n- none\n\n"
    "notes:\n- none\n\nchecks_run:\n- read the code\n\nnot_verified:\n- none\n\nconfidence: high — fine\n"
)


def _check_retry_pass_is_not_clean() -> None:
    passing = build_report(
        [
            {"type": "progress", "step": 1, "action": "expect_url", "status": "passed", "claim_id": "login"},
            {"type": "result", "status": "finished"},
        ],
        _SCENARIO,
    )
    clean = to_verification(passing, reviewer_content=_CLEAN_REVIEW, attempts=[{"browser_verdict": "pass", "reason": None}])
    assert_true(clean["verdict"] == "pass", f"fixture assumption: one attempt that passed is a pass: {clean['verdict']}")
    retried = to_verification(
        passing,
        reviewer_content=_CLEAN_REVIEW,
        attempts=[{"browser_verdict": "incomplete", "reason": "timeout"}, {"browser_verdict": "pass", "reason": None}],
    )
    assert_true(
        retried["verdict"] == "incomplete" and "passed only on attempt 2 of 2" in retried["content"] and "timeout" in retried["content"],
        f"a pass that needed a retry is incomplete and names what the first attempt hit: {retried['verdict']}\n{retried['content']}",
    )
    assert_true(
        validate_verification_contract(retried["content"])["verdict"] == retried["verdict"],
        "the shared validator reaches the same verdict from the content",
    )


def _check_expected_values_stay_placeholders() -> None:
    class _Empty:
        def count(self):
            return 0

    page = SimpleNamespace(url="http://localhost:8000/login", main_frame=object(),
                           get_by_label=lambda *a, **k: _Empty(), locator=lambda *a, **k: _Empty(),
                           get_by_text=lambda *a, **k: _Empty(), get_by_role=lambda *a, **k: _Empty(),
                           get_by_placeholder=lambda *a, **k: _Empty(), get_by_test_id=lambda *a, **k: _Empty())
    ticks = [0.0]

    def clock():
        return ticks[0]

    def sleep(seconds):
        ticks[0] += seconds

    events: list[dict] = []
    resolved = {"id": "who", "action": "expect_dom", "selector": {"text": "ab1"}, "text": "ab1",
                "ready": [{"text": "ab1"}]}
    shown = {"id": "who", "action": "expect_dom", "selector": {"text": "${E2E_USER}"}, "text": "${E2E_USER}",
             "ready": [{"text": "${E2E_USER}"}]}
    session = Session(page, {"base_url": "http://localhost:8000", "step_timeout_ms": 200}, events.append,
                      clock=clock, sleep=sleep, display_scenario={"steps": [shown]})
    outcome = session._execute(resolved, "who")
    text = repr(outcome)
    assert_true(outcome["status"] == "failed", f"fixture assumption: nothing matches: {outcome}")
    assert_true(
        "ab1" not in text and "${E2E_USER}" in text,
        f"what a step expected is reported in placeholder form — a 3-character value cannot be scrubbed back: {outcome}",
    )


def _check_tokens_leave_the_ledger() -> None:
    for url, expected in (
        ("http://app.test/reset/eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4f", "http://app.test/reset/:id"),
        ("http://app.test/invite/Ab3dE5fG7hJ9kL1mN2", "http://app.test/invite/:id"),
        ("http://app.test/user-settings-page", "http://app.test/user-settings-page"),
        ("http://app.test/api-v2-endpoints-list", "http://app.test/api-v2-endpoints-list"),
    ):
        assert_true(sanitize_endpoint(url) == expected, f"{url} -> {sanitize_endpoint(url)}, expected {expected}")


def _check_headed_without_a_display_runs_headless(root: Path) -> None:
    import core.evidence.e2e.runner as runner

    captured: dict = {}
    saved_display, saved_player, saved_preflight = runner.display_available, runner.run_player, runner.preflight

    def fake_player(argv, *, stdin_payload, **kwargs):
        import json

        captured["config"] = json.loads(stdin_payload)["config"]
        return {"events": [{"type": "result", "status": "finished"}], "stderr_tail": [], "malformed": [],
                "launch_error": None, "timed_out": False, "stalled": False, "truncated": False,
                "returncode": 0, "duration_seconds": 0.0}

    runner.display_available = lambda: False
    runner.run_player = fake_player
    # No app is listening in a test: the reachability probe is not what this checks.
    runner.preflight = lambda config, fake=False: {"ok": True, "checks": [], "network": {"write_hosts": [], "pins": {}, "hosts": []}}
    try:
        from tests.checks.e2e_routing import _adapter, _run, _scenario, _workspace

        workspace = _workspace("e2e-headless-")
        try:
            result = _run(workspace, _adapter(), _scenario(), fake=None, settings={"headless": False}, env={"E2E_USER": "user"})
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    finally:
        runner.display_available, runner.run_player, runner.preflight = saved_display, saved_player, saved_preflight
    e2e = result["meta"]["e2e"]
    assert_true(
        captured.get("config", {}).get("headless") is True and e2e["config"]["headless"] is True
        and any("no display" in w for w in e2e.get("config_warnings") or []),
        f"a headed run with no screen launches headless and says so: {captured.get('config', {}).get('headless')} {e2e.get('config_warnings')}",
    )


def _check_fake_player_reports_cleanup() -> None:
    import io
    import json
    import sys

    from core.evidence.e2e import player

    scenario = {"steps": [{"id": "make", "action": "click", "selector": {"text": "Add"}}],
                "cleanup": [{"id": "undo", "cleans": "make", "action": "click", "selector": {"text": "Delete"}}]}
    buffer, saved = io.StringIO(), sys.stdout
    sys.stdout = buffer
    try:
        player._fake_run("pass", scenario)
    finally:
        sys.stdout = saved
    events = [json.loads(line) for line in buffer.getvalue().splitlines()]
    cleanup = [e for e in events if e.get("type") == "cleanup"]
    assert_true(
        len(cleanup) == 1 and cleanup[0]["status"] == "passed" and cleanup[0]["cleans"] == "make",
        f"the fake reports a cleanup event, so a cleanup can pass without a browser: {cleanup}",
    )


def _check_budget_covers_retries(root: Path) -> None:
    from core.evidence.e2e.runner import _enforce_artifact_budget

    run_dir = root / "budget"
    (run_dir / "retry1").mkdir(parents=True)
    (run_dir / "trace.zip").write_bytes(b"\0" * (700 * 1024))
    (run_dir / "retry1" / "trace.zip").write_bytes(b"\0" * (700 * 1024))
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    pruned = _enforce_artifact_budget(run_dir, 1)
    assert_true(
        pruned == ["trace.zip"] or pruned == ["retry1/trace.zip"],
        f"one budget spans the run and its retries, and names say which attempt lost a file: {pruned}",
    )
    assert_true((run_dir / "report.json").exists(), "the runner's own evidence is never pruned")


def _check_browser_job_is_not_resumed() -> None:
    import core.jobs.job_lifecycle as lifecycle

    failed: list[dict] = []

    class _Jobs:
        def get_job(self, job_id):
            return {"job_id": job_id, "command": "verify-browser", "task": "t", "session_id": "s",
                    "work_dir": None, "model": None, "recovery_attempt": 1}

        def set_worker_pid(self, *args):
            pass

        def mark_running(self, job_id):
            return {"status": "running"}

        def touch_heartbeat(self, *args):
            pass

        def fail_job(self, job_id, message, output=None, **kwargs):
            failed.append({"job_id": job_id, "output": output})
            return {}

    def must_not_run(*args, **kwargs):
        raise AssertionError("a recovered browser job must not run its scenario again")

    saved = lifecycle._main
    lifecycle._main = lambda: SimpleNamespace(JOB_MANAGER=_Jobs(), run=must_not_run)
    try:
        output = lifecycle.run_worker("job-1")
    finally:
        lifecycle._main = saved
    assert_true(
        output["meta"].get("reason") == "not_recoverable" and failed and failed[0]["output"] is output,
        f"a dead browser job is failed with a reason, not replayed: {output}",
    )


def _test_e2e_hardening() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-e2e-hardening-"))
    try:
        _check_retry_pass_is_not_clean()
        _check_expected_values_stay_placeholders()
        _check_tokens_leave_the_ledger()
        _check_headed_without_a_display_runs_headless(root)
        _check_fake_player_reports_cleanup()
        _check_budget_covers_retries(root)
        _check_browser_job_is_not_resumed()
    finally:
        shutil.rmtree(root, ignore_errors=True)
