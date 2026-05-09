import os
import shutil
import subprocess
import sys

from config.settings import DEFAULT_TIMEOUT_SECONDS, OPENCODE_COMMAND
from utils.parser import clean_opencode_output, ensure_text, extract_opencode_session_id, first_non_empty


class OpenCodeAdapter:
    adapter = "opencode"

    def __init__(self, command: str = OPENCODE_COMMAND, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    def run(self, prompt: str, session: dict, model: str | None = None) -> dict:
        command = self._resolve_command()
        opencode_session_id = session.get("opencode_session_id")
        bootstrap_meta = None

        if not opencode_session_id:
            bootstrap = self._run_args(
                [command, "run", "Initialize session. Reply READY.", "--print-logs", "--log-level", "INFO"]
            )
            bootstrap_meta = bootstrap["meta"]
            opencode_session_id = bootstrap_meta.get("opencode_session_id")

        args = [command, "run", prompt]
        if model:
            args.extend(["-m", model])

        if opencode_session_id:
            args.extend(["-s", opencode_session_id])
        else:
            args.extend(["--print-logs", "--log-level", "INFO"])

        result = self._run_args(args)
        if bootstrap_meta is not None:
            result["meta"]["bootstrap"] = bootstrap_meta
            result["meta"]["opencode_session_id"] = result["meta"].get("opencode_session_id") or opencode_session_id
        return result

    def _run_args(self, args: list[str]) -> dict:

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
            return self._error(f"command not found: {args[0]}", {"error": str(exc), "args": args})
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

    def _resolve_command(self) -> str:
        if sys.platform != "win32" or os.path.splitext(self.command)[1]:
            return self.command
        return shutil.which(f"{self.command}.cmd") or shutil.which(self.command) or self.command
