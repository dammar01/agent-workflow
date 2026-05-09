import os
import subprocess

from config.settings import DEFAULT_TIMEOUT_SECONDS, OPENCODE_COMMAND
from utils.parser import clean_opencode_output, ensure_text, extract_opencode_session_id, first_non_empty


class OpenCodeAdapter:
    adapter = "opencode"

    def __init__(self, command: str = OPENCODE_COMMAND, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def run(self, prompt: str, session: dict, model: str | None = None) -> dict:
        args = [self.command, "run", prompt]
        if model:
            args.extend(["-m", model])

        opencode_session_id = session.get("opencode_session_id")
        if opencode_session_id:
            args.extend(["-s", opencode_session_id])
        else:
            args.append("--print-logs")

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            return self._error(f"command not found: {self.command}", {"error": str(exc), "args": args})
        except subprocess.TimeoutExpired as exc:
            raw = first_non_empty(exc.stderr, exc.stdout, f"timeout after {self.timeout_seconds}s")
            return self._error(raw, {"timeout_seconds": self.timeout_seconds, "args": args})
        except OSError as exc:
            return self._error(str(exc), {"error": type(exc).__name__, "args": args})

        raw = first_non_empty(completed.stdout, completed.stderr)
        combined = "\n".join(part for part in (ensure_text(completed.stdout), ensure_text(completed.stderr)) if part)
        meta = {
            "returncode": completed.returncode,
            "stderr": ensure_text(completed.stderr).strip(),
            "args": args,
            "opencode_session_id": extract_opencode_session_id(combined),
        }

        if completed.returncode != 0:
            return self._error(clean_opencode_output(raw), meta)

        return {"ok": True, "content": clean_opencode_output(raw), "meta": meta}

    @staticmethod
    def _error(content: str, meta: dict) -> dict:
        return {"ok": False, "content": ensure_text(content), "meta": meta}
