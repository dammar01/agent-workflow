import os

from adapters.opencode_adapter import OpenCodeAdapter
from core.job_manager import JobManager
from core.executor import Executor
from core.prompt_builder import build_prompt
from core.workflow_runtime import ensure_workflow_workspace
from utils.parser import clean_opencode_output, extract_opencode_session_id
import check
import main
import tempfile
import threading
import time
from pathlib import Path


class FakeOpenCodeAdapter:
    def __init__(self) -> None:
        self.calls = []
        self.fail_next = False

    def run(self, prompt: str, session: dict, model: str | None = None, work_dir: str | None = None) -> dict:
        self.calls.append({"prompt": prompt, "session": dict(session), "model": model, "work_dir": work_dir})
        if self.fail_next:
            self.fail_next = False
            return {"ok": False, "content": "simulated opencode failure", "meta": {"simulated": True}}
        content = """
INFO  2026-05-09T12:10:24 +1ms service=session.prompt session.id=ses_test123 step=0 loop
> build · gpt-5.3-codex
[EVIDENCE]
findings:
- entry point at app/main.py
[DIGEST]
summary: OpenCode response.
INFO  2026-05-09T12:10:28 +0ms service=session.idle publishing
""".strip()
        return {
            "ok": True,
            "content": clean_opencode_output(content),
            "meta": {
                "simulated": True,
                "opencode_session_id": extract_opencode_session_id(content),
                "args": ["opencode", "run", prompt],
            },
        }


class RecordingOpenCodeAdapter(OpenCodeAdapter):
    def __init__(self) -> None:
        super().__init__(command="opencode")
        self.init_calls = []
        self.run_calls = []

    def init_session(
        self,
        model: str | None = None,
        work_dir: str | None = None,
        workflow_session_id: str | None = None,
    ) -> tuple[str | None, dict]:
        args = ["opencode", "run", "Initialize session. Reply READY."]
        if model:
            args.extend(["-m", model])
        args.extend(["--print-logs", "--log-level", "INFO"])
        self.init_calls.append(
            {"args": args, "model": model, "work_dir": work_dir, "workflow_session_id": workflow_session_id}
        )
        return "ses_boot123", {
            "args": args,
            "opencode_session_id": "ses_boot123",
            "returncode": 0,
        }

    def _run_args(self, args: list[str], work_dir: str | None = None) -> dict:
        self.run_calls.append({"args": args, "work_dir": work_dir})
        return {
            "ok": True,
            "content": "OpenCode response",
            "meta": {"args": args, "opencode_session_id": None, "cwd": work_dir},
        }


class FakeJobProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_tests() -> None:
    fake_opencode = FakeOpenCodeAdapter()
    temp_root = Path(tempfile.mkdtemp(prefix="agent-workflow-test-"))
    original_popen = main.subprocess.Popen
    main.SESSION_MANAGER = main.SessionManager(temp_root / "sessions")
    main.JOB_MANAGER = JobManager(temp_root / "jobs")
    main.EXECUTOR = Executor(opencode=fake_opencode, session_manager=main.SESSION_MANAGER)
    check.JOB_MANAGER = main.JOB_MANAGER
    work_dir = str(temp_root)
    ensure_workflow_workspace(temp_root, os.getenv("AGENT_PATH"))

    try:
        # 1. Prompt constraints
        prompt = build_prompt(
            role="exploration",
            task="cek graph",
            session_id="prompt-test",
            command="explore",
            project_root=str(temp_root),
        )
        assert_true("[WORKFLOW_AGENT]" in prompt, "prompt must tag workflow-agent context")
        assert_true("read graphify output first" in prompt, "prompt must require graphify-first exploration")

        # 2. Bootstrap flow
        recording_opencode = RecordingOpenCodeAdapter()
        bootstrapped = recording_opencode.run(
            "real prompt",
            {"session_id": "bootstrap-test", "opencode_session_id": None},
            "provider/model",
            work_dir,
        )
        assert_true(bootstrapped["ok"], "bootstrap flow must succeed")
        assert_true(recording_opencode.init_calls[0]["work_dir"] == work_dir, "init must receive work_dir")
        assert_true("--print-logs" in recording_opencode.init_calls[0]["args"], "init must use --print-logs")
        assert_true("stderr" not in bootstrapped["meta"]["bootstrap"], "init logs must not be exposed in bootstrap meta")
        assert_true(recording_opencode.run_calls[0]["work_dir"] == work_dir, "agent run must receive work_dir")
        run_args = recording_opencode.run_calls[0]["args"]
        assert_true("--print-logs" not in run_args, "agent run must not use --print-logs")
        assert_true("--log-level" not in run_args, "agent run must not use --log-level after init")
        assert_true(run_args[-2:] == ["-s", "ses_boot123"], "agent run must resume captured OpenCode session")

        # 3. Explore command
        explore = main.run("explore", "cari entry point module auth", "test-session-v2", work_dir=work_dir)
        assert_true(explore["ok"], "explore must succeed")
        assert_true(explore["meta"]["opencode_session_id"] == "ses_test123", "first run must capture OpenCode session")
        assert_true(fake_opencode.calls[-1]["work_dir"] == work_dir, "work_dir must reach adapter")
        assert_true("INFO  2026" not in explore["content"], "OpenCode logs must be stripped")
        assert_true("> build" not in explore["content"], "OpenCode model banner must be stripped")

        # 4. Analyze with model override and session reuse
        analyze = main.run(
            "analyze",
            "cek logic auth",
            "test-session-v2",
            work_dir=work_dir,
            model="9router-sdi/gpt-5.3-codex",
        )
        assert_true(analyze["ok"], "analyze must succeed")
        assert_true(fake_opencode.calls[-1]["model"] == "9router-sdi/gpt-5.3-codex", "model override must reach adapter")
        assert_true(
            fake_opencode.calls[-1]["session"].get("opencode_session_id") == "ses_test123",
            "second run must reuse OpenCode session",
        )

        # 5. Failure simulation
        fake_opencode.fail_next = True
        failure = main.run("verify", "simulate opencode failure", "test-session-v2", work_dir=work_dir)
        assert_true(not failure["ok"], "OpenCode failure must return error")

        # 6. Job manager lock + result states
        job = main.JOB_MANAGER.create_job("execute", "do async thing", "async-session", work_dir, None)
        assert_true(job["status"] == "pending", "job must start pending")
        duplicate_error = None
        try:
            main.JOB_MANAGER.create_job("execute", "second async thing", "async-session", work_dir, None)
        except ValueError as exc:
            duplicate_error = str(exc)
        assert_true(duplicate_error is not None, "second active job on same session must fail")
        running = main.JOB_MANAGER.mark_running(job["job_id"])
        assert_true(running["status"] == "running", "job must move to running")
        completed = main.JOB_MANAGER.complete_job(job["job_id"], {"ok": True, "content": "done", "meta": {}})
        assert_true(completed["status"] == "completed", "job must complete")
        result = main.get_result(job["job_id"])
        assert_true(result["ok"] and result["status"] == "completed", "result must expose completed output")
        missing = main.get_status("missing-job")
        assert_true(not missing["ok"] and missing["status"] == "not_found", "missing status must be structured")

        # 7. Submit spawns worker process and returns pending payload
        def fake_popen(*args, **kwargs):
            return FakeJobProcess()

        main.subprocess.Popen = fake_popen
        submitted = main.submit("execute", "long task", "submit-session", work_dir, None)
        assert_true(submitted["ok"], "submit must succeed")
        assert_true(submitted["status"] == "pending", "submit must return pending")
        assert_true(submitted["meta"]["pid"] == 4242, "submit must expose worker pid")

        queued = main.submit("explore", "inspect queued flow", "queued-session", work_dir, None)
        assert_true(queued["ok"], "queued background submit must succeed")
        assert_true(main.should_run_in_background("explore"), "explore must be marked as background command")
        main.subprocess.Popen = original_popen

        # 8. Worker path updates job state on success
        worker_job = main.JOB_MANAGER.create_job("explore", "inspect async flow", "worker-session", work_dir, None)
        worker_output = main.run_worker(worker_job["job_id"])
        assert_true(worker_output["ok"], "worker must execute queued command")
        worker_status = main.get_status(worker_job["job_id"])
        assert_true(worker_status["status"] == "completed", "worker must persist completed state")

        # 9. check.py status/result payloads
        pending_status = check._status_payload(queued["job_id"])
        assert_true(pending_status["status"] == "pending", "check status must expose pending job")
        assert_true(pending_status["done"] is False, "pending job must not be done")

        complete_job = main.JOB_MANAGER.create_job("execute", "done task", "result-session", work_dir, None)
        main.JOB_MANAGER.complete_job(complete_job["job_id"], {"ok": True, "content": "clean output", "meta": {}})
        result_ok, result_payload = check._result_payload(complete_job["job_id"])
        assert_true(result_ok, "completed job must return output-only payload")
        assert_true(result_payload == "clean output", "completed result must expose cleaned content only")

        failed_job = main.JOB_MANAGER.create_job("execute", "bad task", "failed-session", work_dir, None)
        main.JOB_MANAGER.fail_job(failed_job["job_id"], "boom")
        failed_ok, failed_payload = check._result_payload(failed_job["job_id"])
        assert_true(not failed_ok, "failed job must not return plain output")
        assert_true(failed_payload["status"] == "failed", "failed result must keep failed status")

        missing_status = check._status_payload("missing-job")
        assert_true(missing_status["status"] == "not_found", "missing job must report not_found in check.py")

        # 10. check.py internal wait loop and timeout contract
        wait_job = main.JOB_MANAGER.create_job("execute", "wait task", "wait-session", work_dir, None)

        def complete_later() -> None:
            time.sleep(0.1)
            main.JOB_MANAGER.complete_job(wait_job["job_id"], {"ok": True, "content": "waited output", "meta": {}})

        waiter = threading.Thread(target=complete_later)
        waiter.start()
        wait_ok, wait_payload = check._wait_for_result(wait_job["job_id"], 0.05, 2)
        waiter.join()
        assert_true(wait_ok, "wait loop must eventually return completed result")
        assert_true(wait_payload == "waited output", "wait loop must return cleaned output only on success")

        timeout_job = main.JOB_MANAGER.create_job("execute", "timeout task", "timeout-session", work_dir, None)
        timeout_ok, timeout_payload = check._result_payload(timeout_job["job_id"])
        assert_true(not timeout_ok, "non-wait result lookup must stay incomplete")

        timeout_status = check._wait_for_status(timeout_job["job_id"], 0.05, 1)
        assert_true(timeout_status["status"] == "pending", "timed out status must preserve current job state")
        assert_true(timeout_status.get("timed_out") is True, "timed out wait must mark timed_out")

        # 11. v3.3.0 — structured errors, idempotency, reaper, digest, guard, router
        from core.contract import extract_digest, make_error
        from core.router import Router
        from utils import osutil, path_guard

        # next_action mandatory
        enforced = False
        try:
            make_error("permission_denied", "x", next_action="")
        except ValueError:
            enforced = True
        assert_true(enforced, "make_error must require next_action")

        # role code-authoritative; local command rejected from delegation
        assert_true(Router().route("explore")["role"] == "exploration", "router derives role from code")
        execute_rejected = False
        try:
            Router().route("execute")
        except ValueError:
            execute_rejected = True
        assert_true(execute_rejected, "execute must not be delegable")

        # idempotency: identical request reuses the same job
        idem_a = main.JOB_MANAGER.create_job("explore", "same task", "idem-session", work_dir, None)
        idem_b = main.JOB_MANAGER.create_job("explore", "same task", "idem-session", work_dir, None)
        assert_true(idem_a["job_id"] == idem_b["job_id"], "identical request must reuse job")
        main.JOB_MANAGER.complete_job(idem_a["job_id"], {"ok": True, "content": "x", "meta": {}})

        # reaper: running job with a dead worker pid is failed on lookup
        dead = main.JOB_MANAGER.create_job("explore", "dead worker", "reaper-session", work_dir, None)
        main.JOB_MANAGER.set_worker_pid(dead["job_id"], 999999999)
        main.JOB_MANAGER.mark_running(dead["job_id"])
        reaped = main.get_result(dead["job_id"])
        assert_true(reaped["status"] == "failed", "dead-worker job must be reaped to failed")

        # digest extraction + fallback
        digest = extract_digest("findings:\n- a\n[DIGEST]\nsummary: s\nkey_findings:\n- k\nrisk_level: high\nrecommended_next_action: go\nconfidence: low")
        assert_true(digest and digest["summary"] == "s" and digest["risk_level"] == "high", "digest must parse")
        assert_true(extract_digest("no digest here") is None, "missing digest must fall back to None")

        # preflight path guard
        guard_ok, blocked = path_guard.validate_scope("read the .env and secret files", temp_root)
        assert_true(not guard_ok and blocked, "path guard must block sensitive references")
        clean_ok, _ = path_guard.validate_scope("find the auth entry point", temp_root)
        assert_true(clean_ok, "clean task must pass the guard")

        # cross-OS primitive
        assert_true(osutil.process_alive(999999999) is False, "process_alive must report dead pid")

        # invalid_evidence: menu/refusal (ok:true but no evidence) must be rejected as proxy failure
        class MenuAdapter:
            command = "opencode"
            timeout_seconds = 0
            no_timeout = True

            def run(self, prompt, session, model=None, work_dir=None):
                return {"ok": True, "content": "Specify command: explore, plan, analyze, verify, sweep, doctor.", "meta": {"opencode_session_id": "ses_menu"}}

        menu_exec = Executor(opencode=MenuAdapter(), session_manager=main.SESSION_MANAGER)
        menu_session = main.SESSION_MANAGER.load_or_create("menu-session")
        menu_res = menu_exec.execute("analyze", "do analysis", menu_session, work_dir)
        assert_true(not menu_res["ok"], "menu/refusal response must be rejected, not treated as success")
        assert_true(menu_res["meta"]["error_type"] == "invalid_evidence", "non-evidence must flag invalid_evidence")

        # rich error (error_type + next_action) must survive the job path, not collapse to a string
        rich_job = main.JOB_MANAGER.create_job("analyze", "rich err", "rich-session", work_dir, None)
        main.JOB_MANAGER.fail_job(
            rich_job["job_id"],
            "non-evidence",
            output={"ok": False, "content": "non-evidence", "meta": {"error_type": "invalid_evidence", "next_action": "STOP, ask user"}},
        )
        rich_res = main.get_result(rich_job["job_id"])
        assert_true(rich_res["meta"].get("error_type") == "invalid_evidence", "job path must preserve error_type")
        assert_true(rich_res["meta"].get("next_action") == "STOP, ask user", "job path must preserve next_action")

        # 12. v3.4.0 reliability: liveness tri-state, heartbeat, runtime ceiling, probe
        from core import fact_store, graph_index, job_manager as jm_mod

        # Stall detection and the runtime ceiling are separate managers on purpose: the
        # ceiling outranks stall in get_result (a hard backstop must win over a probe
        # hint), so sharing one tiny threshold would mask the stall path entirely.
        watchdog = JobManager(
            temp_root / "jobs", stall_threshold_seconds=1, max_runtime_seconds=3600
        )
        live_job = watchdog.create_job("explore", "watchdog", "watchdog-session", work_dir, None)
        job_id = live_job["job_id"]

        assert_true(
            watchdog.liveness(watchdog.get_job(job_id)) is None,
            "a job with no worker pid yet must not be classified (nothing to reap)",
        )
        watchdog.set_worker_pid(job_id, 999999999)
        assert_true(
            watchdog.liveness(watchdog.get_job(job_id)) == jm_mod.DEAD,
            "a gone pid must classify as dead",
        )

        # Live pid + fresh heartbeat = progressing; the SAME pid with a stale heartbeat
        # must classify as stalled. That difference is the whole point of the heartbeat:
        # pid liveness alone reports both cases identically.
        watchdog.mark_running(job_id)
        watchdog.set_worker_pid(job_id, os.getpid())
        watchdog.touch_heartbeat(job_id, {"phase": "agent", "elapsed_seconds": 3})
        beat = watchdog.read_heartbeat(job_id)
        assert_true(
            beat and beat.get("at") and beat["progress"]["phase"] == "agent",
            "heartbeat must record its timestamp and progress payload",
        )
        beating = watchdog.get_job(job_id)
        assert_true(
            watchdog.liveness(beating) == jm_mod.ALIVE_PROGRESSING,
            "live pid with a fresh heartbeat must be progressing",
        )
        time.sleep(1.2)  # exceed the 1s stall threshold configured above
        assert_true(
            watchdog.liveness(watchdog.get_job(job_id)) == jm_mod.ALIVE_STALLED,
            "live pid with a stale heartbeat must be stalled, not dead",
        )

        stalled = watchdog.get_result(job_id)
        assert_true(
            stalled["meta"].get("error_type") == "worker_stalled",
            "a stalled worker must be reported as stalled",
        )
        assert_true(
            watchdog.get_job(job_id)["status"] == "running",
            "a stalled worker must NOT be reaped on suspicion — its work may still land",
        )

        probed = watchdog.record_probe(job_id, {"alive": False, "reason": "probe_timeout"})
        assert_true(
            probed["liveness"] == "stalled_on_limit",
            "a probe that cannot reach opencode means rate/usage limit, not a hang",
        )
        assert_true(
            watchdog.read_probe(job_id)["liveness"] == "stalled_on_limit",
            "the probe verdict must survive a round-trip through its side file",
        )
        assert_true(
            watchdog.record_probe(job_id, {"alive": True, "reason": "probe_ok"})["liveness"]
            == "stalled_no_progress",
            "a probe that answers means opencode is healthy and this session is hung",
        )

        # A late beat must NOT resurrect a job another process already ended. Heartbeats
        # arrive every couple of seconds and get_result runs in a different process, so
        # folding the beat into the job record made this a routine collision, not a rare one.
        raced = watchdog.create_job("explore", "race", "race-session", work_dir, None)
        watchdog.set_worker_pid(raced["job_id"], os.getpid())
        watchdog.mark_running(raced["job_id"])
        watchdog.fail_job(raced["job_id"], "worker process died before completing (reaped)")
        watchdog.touch_heartbeat(raced["job_id"], {"phase": "agent"})  # beat arrives late
        settled = watchdog.get_job(raced["job_id"])
        assert_true(
            settled["status"] == "failed" and settled["error"],
            "a late heartbeat must never revert a terminal job or erase its error",
        )

        # Runtime ceiling: the OOM backstop, where the pid can look alive but the job is lost.
        ceiling = JobManager(
            temp_root / "jobs", stall_threshold_seconds=3600, max_runtime_seconds=1
        )
        expired = ceiling.create_job("plan", "expired", "expired-session", work_dir, None)
        ceiling.set_worker_pid(expired["job_id"], os.getpid())
        ceiling.mark_running(expired["job_id"])
        ceiling.touch_heartbeat(expired["job_id"], {"phase": "agent"})
        time.sleep(1.2)
        expired_res = ceiling.get_result(expired["job_id"])
        assert_true(
            expired_res["meta"].get("error_type") == "job_expired",
            "a job past the runtime ceiling must fail as expired, distinct from worker_died",
        )

        # Tolerant fact parsing: the old parser stopped at the first non-bullet line, so a
        # blank line or a nested bullet silently emptied the section (and ingest hid it).
        messy = (
            "grounded:\n\n- claim A [main.py:1]\n  * nested detail\n"
            "- claim B [core/x.py:2]\n  wrapped tail\n\nassumptions:\n- guess\n"
        )
        grounded = fact_store._parse_block(messy, "grounded")
        assert_true(len(grounded) == 3, f"tolerant parser must keep blank/nested bullets, got {grounded}")
        assert_true(
            "wrapped tail" in grounded[-1],
            "a wrapped continuation line must join its bullet, not be dropped",
        )
        assert_true(
            fact_store._parse_block(messy, "assumptions") == ["guess"],
            "the next section header must end the block",
        )
        assert_true(
            fact_store._parse_block("grounded:\n- a [x.py:1]\n- b [y.py:2]\nassumptions:\n- c\n", "grounded")
            == ["a [x.py:1]", "b [y.py:2]"],
            "the pre-3.4.0 flat format must keep parsing identically",
        )
        # A section header carrying trailing text ends the block. Real second_agent output
        # writes `external: none (...)`, and gluing that onto the last bullet corrupts the
        # claim text — which is what the recurrence key is computed from.
        assert_true(
            fact_store._parse_block(
                "dependents:\n- calls X [a.py:1]\nexternal: none (no external libs)\n", "dependents"
            )
            == ["calls X [a.py:1]"],
            "a `key: value` line at column 0 must end the section, not extend the last bullet",
        )
        assert_true(
            fact_store._parse_block("grounded:\n- claim [a.py:1]\nconfidence: high\n", "grounded")
            == ["claim [a.py:1]"],
            "a trailing confidence line must not be absorbed into a claim",
        )

        # Graph leads: absent graph degrades to None, never an exception.
        assert_true(
            graph_index.leads(temp_root, "anything") is None,
            "a project without graphify-out must yield no leads instead of failing",
        )
        assert_true(
            graph_index.load_graph(temp_root) is None,
            "a missing graph.json must return None, not raise",
        )
        repo_root = Path(__file__).resolve().parent
        repo_leads = graph_index.leads(repo_root, "session manager")
        # Guarded on the graph EXISTING, not on leads being truthy: keying the guard on
        # the result itself would let a leads() that silently returns None skip its own
        # assertions and still report success.
        assert_true(
            (graph_index.graph_path(repo_root).exists()) == (repo_leads is not None),
            "leads must be produced whenever graph.json exists, and only then",
        )
        if repo_leads:
            assert_true(
                all("\\" not in row["file"] for row in repo_leads["files"]),
                "lead paths must be repo-relative POSIX so they mean the same on any machine",
            )
            leads_prompt = build_prompt(
                role="reasoning",
                task="session manager",
                session_id="leads",
                command="plan",
                project_root=str(repo_root),
                graph_leads=repo_leads,
            )
            assert_true("[GRAPH_LEADS" in leads_prompt, "leads must reach the prompt")
            assert_true(
                "not evidence" in leads_prompt,
                "leads must be framed as starting points, never as findings",
            )

        # Timeout is on by default now: an unbounded wait is how a rate-limited agent
        # used to hang a job forever.
        default_adapter = OpenCodeAdapter()
        assert_true(
            not default_adapter.no_timeout and default_adapter.timeout_seconds > 0,
            "the default adapter must carry a real timeout",
        )
        assert_true(
            default_adapter.bootstrap_timeout_seconds > 0
            and default_adapter.bootstrap_timeout_seconds < default_adapter.timeout_seconds,
            "bootstrap must have its own, shorter budget than a full task",
        )
        # Side files must not outlive their job — nothing rewrites them once it ends.
        pruned_id = raced["job_id"]
        assert_true(
            watchdog._beat_path(pruned_id).exists(),
            "precondition: the raced job still has its beat file",
        )
        watchdog.prune_jobs(ttl_days=0, keep_last=0)
        assert_true(
            not watchdog._beat_path(pruned_id).exists()
            and not watchdog._probe_path(pruned_id).exists(),
            "pruning a job must take its heartbeat/probe side files with it",
        )

        print("test_scenario: success")
    finally:
        main.subprocess.Popen = original_popen


if __name__ == "__main__":
    run_tests()
