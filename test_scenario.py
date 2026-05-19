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
OpenCode response
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

        print("test_scenario: success")
    finally:
        main.subprocess.Popen = original_popen


if __name__ == "__main__":
    run_tests()
