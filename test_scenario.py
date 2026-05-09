from core.executor import Executor
from utils.parser import clean_opencode_output, extract_opencode_session_id
import main


class FakeOpenCodeAdapter:
    def __init__(self) -> None:
        self.calls = []
        self.fail_next = False

    def run(self, prompt: str, session: dict, model: str | None = None) -> dict:
        self.calls.append({"prompt": prompt, "session": dict(session), "model": model})
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


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_tests() -> None:
    fake_opencode = FakeOpenCodeAdapter()
    main.EXECUTOR = Executor(opencode=fake_opencode, session_manager=main.SESSION_MANAGER)
    main.CACHE.clear()

    try:
        explore = main.run("explore", "cari entry point module auth", "test-session-v2")
        assert_true(explore["status"] == "success", "explore must succeed")
        assert_true(explore["adapter"] == "opencode", "explore must route to OpenCode")
        assert_true(explore["role"] == "exploration", "explore must use exploration role")
        assert_true(explore["opencode_session_id"] == "ses_test123", "first run must capture OpenCode session")
        assert_true("INFO  2026" not in explore["content"], "OpenCode logs must be stripped")
        assert_true("> build" not in explore["content"], "OpenCode model banner must be stripped")

        analyze = main.run("analyze", "cek logic auth", "test-session-v2", model="9router-sdi/gpt-5.3-codex")
        assert_true(analyze["status"] == "success", "analyze must succeed")
        assert_true(analyze["model"] == "9router-sdi/gpt-5.3-codex", "model override must be returned")
        assert_true(fake_opencode.calls[-1]["model"] == "9router-sdi/gpt-5.3-codex", "model override must reach adapter")
        assert_true(
            fake_opencode.calls[-1]["session"].get("opencode_session_id") == "ses_test123",
            "second run must reuse OpenCode session",
        )

        calls_before_cache = len(fake_opencode.calls)
        cached = main.run("explore", "cari entry point module auth", "test-session-v2")
        assert_true(cached["status"] == "success", "cached explore must succeed")
        assert_true("cache_hit" in cached["meta"]["notes"], "repeated request must hit cache")
        assert_true(len(fake_opencode.calls) == calls_before_cache, "cache hit must not call OpenCode again")

        fake_opencode.fail_next = True
        failure = main.run("verify", "simulate opencode failure", "test-session-v2")
        assert_true(failure["status"] == "error", "OpenCode failure must return error")
        assert_true(failure["adapter"] == "opencode", "failure must stay on OpenCode contract")

        print("test_scenario: success")
    finally:
        main.CACHE.clear()


if __name__ == "__main__":
    run_tests()
