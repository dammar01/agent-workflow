import subprocess

from config.settings import CLAUDE_COMMAND, DEFAULT_TIMEOUT_SECONDS
from utils.parser import first_non_empty, ensure_text


class ClaudeAdapter:
    model = "claude"

    def __init__(
        self,
        command: str = CLAUDE_COMMAND,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def run(self, prompt: str, session: dict) -> dict:
        if not self.command:
            return {
                "ok": True,
                "content": "Claude adapter placeholder. Reasoning prompt received.",
                "meta": {"placeholder": True},
            }

        args = [self.command, "-p", prompt]
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            return self._error(f"command not found: {self.command}", {"error": str(exc)})
        except subprocess.TimeoutExpired as exc:
            raw = first_non_empty(exc.stderr, exc.stdout, f"timeout after {self.timeout_seconds}s")
            return self._error(raw, {"timeout_seconds": self.timeout_seconds})
        except OSError as exc:
            return self._error(str(exc), {"error": type(exc).__name__})

        raw = first_non_empty(completed.stdout, completed.stderr)
        meta = {
            "returncode": completed.returncode,
            "stderr": ensure_text(completed.stderr).strip(),
        }

        if completed.returncode != 0:
            return self._error(raw, meta)

        return {"ok": True, "content": ensure_text(completed.stdout).strip(), "meta": meta}

    @staticmethod
    def _error(content: str, meta: dict) -> dict:
        return {"ok": False, "content": ensure_text(content), "meta": meta}
