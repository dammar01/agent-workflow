from core.executor import Executor
import main


class FakeKimiAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.fail_next = False

    def run(self, prompt: str, session: dict, work_dir: str | None = None) -> dict:
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            return {"ok": False, "content": "simulated kimi failure", "meta": {"simulated": True}}
        if "Collect bounded evidence" in prompt:
            content = '{"entry_points": ["main.py"], "relevant_files": ["core/executor.py"], "flow_summary": "run() -> Executor -> adapter", "unknown_area": null}'
            return {"ok": True, "content": content, "meta": {"simulated": True}}
        return {"ok": True, "content": "exploration result from kimi", "meta": {"simulated": True}}

    def read_last_session_id(self, work_dir: str | None = None) -> str | None:
        return "test-kimi-session-id"


class FakeClaudeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, prompt: str, session: dict) -> dict:
        self.calls += 1
        return {"ok": True, "content": "reasoning result from claude", "meta": {"simulated": True}}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_tests() -> None:
    fake_kimi = FakeKimiAdapter()
    fake_claude = FakeClaudeAdapter()
    main.EXECUTOR = Executor(kimi=fake_kimi, claude=fake_claude)
    main.CACHE.clear()

    try:
        explore = main.run("explore", "cari entry point module auth", "test-session")
        assert_true(explore["status"] == "success", "explore must succeed")
        assert_true(explore["model"] == "kimi", "explore must route to Kimi")
        assert_true(fake_claude.calls == 0, "explore must not call Claude")

        plan = main.run("plan", "buat plan module auth", "test-session")
        assert_true(plan["status"] == "success", "plan must succeed")
        assert_true(plan["model"] == "claude", "plan must return Claude output")
        assert_true(fake_kimi.calls == 2, "plan must call Kimi for evidence")
        assert_true(fake_claude.calls == 1, "plan must call Claude after Kimi")

        calls_before_cache = fake_kimi.calls
        cached = main.run("explore", "cari entry point module auth", "test-session")
        assert_true(cached["status"] == "success", "cached explore must succeed")
        assert_true("cache_hit" in cached["meta"]["notes"], "repeated request must hit cache")
        assert_true(fake_kimi.calls == calls_before_cache, "cache hit must not call Kimi again")

        claude_calls_before_failure = fake_claude.calls
        fake_kimi.fail_next = True
        failure = main.run("explore", "simulate kimi failure", "test-session")
        assert_true(failure["status"] == "error", "Kimi failure must return error")
        assert_true(failure["model"] == "kimi", "Kimi failure must stay on Kimi contract")
        assert_true(fake_claude.calls == claude_calls_before_failure, "Kimi failure must not fallback to Claude")

        print("test_scenario: success")
    finally:
        main.CACHE.clear()


if __name__ == "__main__":
    run_tests()
