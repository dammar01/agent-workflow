from adapters.opencode_adapter import OpenCodeAdapter
from core.executor import Executor
from core.prompt_builder import build_prompt
from utils.parser import clean_opencode_output, extract_opencode_session_id
import main


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

    def init_session(self, model: str | None = None, work_dir: str | None = None) -> tuple[str | None, dict]:
        args = ["opencode", "run", "Initialize session. Reply READY."]
        if model:
            args.extend(["-m", model])
        args.extend(["--print-logs", "--log-level", "INFO"])
        self.init_calls.append({"args": args, "model": model, "work_dir": work_dir})
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


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_tests() -> None:
    fake_opencode = FakeOpenCodeAdapter()
    main.EXECUTOR = Executor(opencode=fake_opencode, session_manager=main.SESSION_MANAGER)
    work_dir = "/tmp/agent-workflow-target"

    try:
        # 1. Prompt constraints
        prompt = build_prompt(role="exploration", task="cek graph", session_id="prompt-test")
        assert_true("do not run `graphify`" in prompt, "prompt must prevent graphify CLI dependency")
        assert_true("graphify-out/GRAPH_REPORT.md" in prompt, "prompt must point to graphify artifacts")

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

        print("test_scenario: success")
    finally:
        pass


if __name__ == "__main__":
    run_tests()
