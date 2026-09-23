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


def _check_repeat_brake_counts_reasons_not_scenarios(root: Path) -> None:
    """The in-run retry loop compares step outcomes; this counts outcomes across runs.

    The loop that shipped twelve browser runs against one environment failure edited the
    scenario between attempts, so a step-level key reset every time. Keying on the outcome
    is what makes the streak survive those edits.
    """
    from core.evidence.e2e.classify import repeat_bucket
    from core.runtime.state import E2E_REPEAT_LIMIT, e2e_repeat_state, note_e2e_outcome

    (root / ".workflow").mkdir(parents=True, exist_ok=True)
    (root / ".workflow" / "config.json").write_text("{}", encoding="utf-8")
    origin = "http://localhost:8000"
    streaks = [note_e2e_outcome(root, "sid-repeat", origin, "harness", "login_failed") for _ in range(3)]
    assert_true(
        streaks == [1, 2, 3] and streaks[-1] >= E2E_REPEAT_LIMIT,
        f"the same outcome across separate runs must accumulate: {streaks}",
    )
    assert_true(
        note_e2e_outcome(root, "sid-repeat", origin, "timeout", "timeout") == 1,
        "a different kind of problem is a different problem and starts its own count",
    )
    note_e2e_outcome(root, "sid-repeat", origin, None)
    assert_true(
        e2e_repeat_state(root, "sid-repeat") == {},
        "a pass clears the streak: the thing being counted stopped happening",
    )

    # One problem, three names. An unreachable base URL is reported as `stuck` on one run
    # and `harness_error` on the next, and keyed on the exact reason each rename started a
    # fresh streak of one — the brake never fired against a loop that was plainly one loop.
    drift = [
        note_e2e_outcome(root, "sid-drift", origin, repeat_bucket("incomplete", reason), reason)
        for reason in ("base_url_unreachable", "harness_error", "unknown_origin")
    ]
    assert_true(
        drift == [1, 2, 3],
        f"reasons that rename the same obstacle keep one streak: {drift}",
    )
    assert_true(
        e2e_repeat_state(root, "sid-drift").get("reason") == "unknown_origin",
        "and the streak quotes back what the last run actually said",
    )
    # The failure most worth braking on was the only one that could not be counted: an app
    # failure carries no reason, because the failing app IS the verdict, and a reason-keyed
    # record read that as nothing to count and cleared itself on every run.
    app = [
        note_e2e_outcome(root, "sid-app", origin, repeat_bucket("fail", None), None)
        for _ in range(3)
    ]
    assert_true(
        app == [1, 2, 3] and app[-1] >= E2E_REPEAT_LIMIT,
        f"three runs against a failing application reach the brake: {app}",
    )
    assert_true(
        repeat_bucket("pass", None) is None and repeat_bucket("incomplete", None) is None,
        "a pass, and an incomplete with nothing to name, are not counted",
    )
    assert_true(
        note_e2e_outcome(root, "sid-app", "http://localhost:9000", "app", None) == 1,
        "a different origin is a different environment and starts its own count",
    )
    assert_true(
        e2e_repeat_state(root, "sid-fresh") == {},
        "and a new session inherits nothing: the record is the session's own",
    )

    # A record written by the build that keyed on the exact reason. It carries no bucket,
    # and without deriving one the next run matched nothing and restarted a streak that
    # had already counted to two — the brake losing its count at exactly the moment it was
    # about to fire.
    from core.runtime.state import load_workspace_state
    from core.workspace.workspace_paths import atomic_write_json

    loaded = load_workspace_state(root, "sid-legacy")
    loaded["state"]["e2e_repeat"] = {"origin": origin, "reason": "stuck", "streak": 2}
    atomic_write_json(loaded["paths"]["state"], loaded["state"])
    assert_true(
        e2e_repeat_state(root, "sid-legacy").get("bucket") == "timeout",
        f"a record from before buckets is read with the bucket its reason maps to: "
        f"{e2e_repeat_state(root, 'sid-legacy')}",
    )
    assert_true(
        note_e2e_outcome(root, "sid-legacy", origin, repeat_bucket("incomplete", "timeout"), "timeout") == 3,
        "so the streak it had already built continues instead of starting over",
    )
    assert_true(
        e2e_repeat_state(root, "sid-legacy").get("bucket") == "timeout",
        "and the migrated record carries the bucket forward itself",
    )


def _check_a_blocked_delete_asks_instead_of_only_refusing() -> None:
    from core.evidence.e2e.runner import _destructive_requests

    pending = _destructive_requests(
        {
            "observations": [
                {"kind": "mutation_blocked", "url": "http://localhost:8000"},
                {"kind": "destructive_unapproved", "url": "http://localhost:8000/api/items/:id"},
                {"kind": "destructive_unapproved", "url": "http://localhost:8000/api/items/:id"},
            ]
        }
    )
    assert_true(
        pending == [
            {
                "method": "DELETE",
                "endpoint": "http://localhost:8000/api/items/:id",
                "entry": "DELETE http://localhost:8000/api/items/:id",
            }
        ],
        f"a refused delete must come back as one answerable request, with the line that "
        f"approves it: {pending}",
    )


def _check_permissions_file_fails_closed(root: Path) -> None:
    """A deny list that could not be read in full does not become a shorter deny list.

    `permissions.json` warned and dropped, the same posture as config.json's knobs. For
    approvals that is right: dropping one leaves the run stricter than asked. For
    `blocked_requests` it inverted the file's purpose — `{"blocked_requests": ["* /api/
    payments", "bad"]}` loaded as NO deny list, one warning among the run's other warnings,
    and the run proceeded free to touch the endpoint the project had named.
    """
    import json

    from core.evidence.e2e.request import load_permissions, permissions_path

    path = permissions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    permissions, warnings, errors = load_permissions(root)
    assert_true(
        permissions["blocked_requests"] == [] and not warnings and not errors,
        f"no file is no permissions, not a problem: {permissions} {warnings} {errors}",
    )

    path.write_text(json.dumps({"blocked_requests": ["* /api/payments", "bad"]}), encoding="utf-8")
    permissions, _warnings, errors = load_permissions(root)
    assert_true(
        errors and "blocked_requests[1]" in " ".join(errors) and permissions["blocked_requests"] == [],
        f"one unparseable deny entry is an error, not a dropped list: {errors} {permissions}",
    )

    path.write_text("{not json", encoding="utf-8")
    _permissions, _warnings, errors = load_permissions(root)
    assert_true(errors, f"a file present but unreadable is an error: {errors}")

    path.write_text(json.dumps({"blocked_requests": "* /api/payments"}), encoding="utf-8")
    _permissions, _warnings, errors = load_permissions(root)
    assert_true(errors, f"a deny list that is not a list is an error: {errors}")

    # The other key keeps the old posture, because dropping an approval fails closed: the
    # run is left stricter than the user asked, which is the safe direction to err.
    path.write_text(json.dumps({"allowed_destructive_requests": ["nonsense"]}), encoding="utf-8")
    permissions, warnings, errors = load_permissions(root)
    assert_true(
        warnings and not errors and permissions["allowed_destructive_requests"] == [],
        f"a malformed approval still warns and drops: {warnings} {errors}",
    )

    # Present but not an object at all: `null`, `[]`, `0`, `""` are a file that says
    # nothing about the deny list, which is not the same as a file that says the deny list
    # is empty. Only `{}` is the latter.
    for raw in ("null", "false", "0", '""', "[]"):
        path.write_text(raw, encoding="utf-8")
        _permissions, _warnings, errors = load_permissions(root)
        assert_true(errors, f"a present top-level {raw} is an error, not an empty deny list: {errors}")
    path.write_text("{}", encoding="utf-8")
    _permissions, warnings, errors = load_permissions(root)
    assert_true(not errors and not warnings, f"an empty object is an empty permissions file: {errors} {warnings}")

    path.write_text(json.dumps({"blocked_requests": ["* /api/payments", "DELETE /api/users/:id"]}), encoding="utf-8")
    permissions, warnings, errors = load_permissions(root)
    assert_true(
        not errors and not warnings and len(permissions["blocked_requests"]) == 2,
        f"a well-formed file loads whole: {permissions} {warnings} {errors}",
    )

    # And the run does not start on a file that could not be read in full: the loader
    # returning an error is only half of failing closed.
    from core.evidence.e2e.request import REQUEST_VERSION, load_request, request_path

    path.write_text(json.dumps({"blocked_requests": ["* /api/payments", "bad"]}), encoding="utf-8")
    session = "sid-permissions"
    req = request_path(root, session)
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text(json.dumps({"version": REQUEST_VERSION, "phase": "draft", "settings": {"base_url": "http://localhost:3000"}}), encoding="utf-8")
    loaded, reason, errors = load_request(root, session)
    assert_true(
        loaded is None and reason == "request_invalid" and any("blocked_requests" in e for e in errors),
        f"a malformed deny list refuses the run, naming the entry: {reason} {errors}",
    )
    path.unlink()


def _check_preflight_failures_reach_the_brake(root: Path) -> None:
    """The brake counts the runs that never start a browser, which is most of the loop.

    An outcome was written only after a browser report. `base_url_unreachable` and
    `playwright_missing` return long before one, so the counter stayed at zero across every
    repetition and the brake never fired against the shape it was built for — while the
    failures it must NOT count, the caller's own `spec_invalid` or `secrets_invalid`, are
    corrected by coming back with better input.
    """
    from core.evidence.e2e.classify import repeat_bucket
    from core.runtime.state import E2E_REPEAT_LIMIT, e2e_repeat_state, note_e2e_outcome

    (root / ".workflow").mkdir(parents=True, exist_ok=True)
    (root / ".workflow" / "config.json").write_text("{}", encoding="utf-8")
    origin = "http://localhost:3000"
    session = "sid-preflight"
    # The runner's own call at the preflight gate, repeated the way a caller repeats it.
    for reason in ("base_url_unreachable", "playwright_missing", "harness_error"):
        note_e2e_outcome(root, session, origin, repeat_bucket("incomplete", reason), reason)
    state = e2e_repeat_state(root, session)
    assert_true(
        state.get("streak") == 3 and state["streak"] >= E2E_REPEAT_LIMIT and state.get("reason") == "harness_error",
        f"one environment problem under three names still reaches the limit: {state}",
    )
    # And the gate counts by reason, not by "preflight failed". `preflight` returns
    # `spec_invalid` for a base_url or write policy the request itself named, which the
    # caller fixes by asking again — counting it would refuse the corrected request.
    from core.evidence.e2e.runner import _REPEATABLE_PREFLIGHT

    assert_true(
        "spec_invalid" not in _REPEATABLE_PREFLIGHT
        and {"playwright_missing", "browser_missing", "base_url_unreachable"} <= _REPEATABLE_PREFLIGHT,
        f"the brake counts the environment's refusals and not the caller's: {sorted(_REPEATABLE_PREFLIGHT)}",
    )


def _test_e2e_hardening() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-e2e-hardening-"))
    try:
        _check_permissions_file_fails_closed(root)
        _check_preflight_failures_reach_the_brake(root)
        _check_repeat_brake_counts_reasons_not_scenarios(root)
        _check_a_blocked_delete_asks_instead_of_only_refusing()
        _check_retry_pass_is_not_clean()
        _check_expected_values_stay_placeholders()
        _check_tokens_leave_the_ledger()
        _check_headed_without_a_display_runs_headless(root)
        _check_fake_player_reports_cleanup()
        _check_budget_covers_retries(root)
        _check_browser_job_is_not_resumed()
    finally:
        shutil.rmtree(root, ignore_errors=True)
