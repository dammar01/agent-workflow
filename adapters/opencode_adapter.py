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

    def init_session(self, model: str | None = None) -> tuple[str | None, dict]:
        """Bootstrap new opencode session. Returns (session_id, bootstrap_meta)."""
        command = self._resolve_command()
        args = [command, "run", "Initialize session. Reply READY.", "--print-logs", "--log-level", "INFO"]
        if model:
            args.extend(["-m", model])
        result = self._run_args(args)
        return result["meta"].get("opencode_session_id"), result["meta"]

    def run_agent(self, prompt: str, session_id: str, model: str | None = None) -> dict:
        """Spawn workflow agent in existing session."""
        command = self._resolve_command()
        args = [command, "run", prompt]
        if model:
            args.extend(["-m", model])
        args.extend(["-s", session_id])
        return self._run_args(args)

    def run(self, prompt: str, session: dict, model: str | None = None) -> dict:
        opencode_session_id = session.get("opencode_session_id")
        bootstrap_meta = None

        if not opencode_session_id:
            opencode_session_id, bootstrap_meta = self.init_session(model)

        if opencode_session_id:
            result = self.run_agent(prompt, opencode_session_id, model)
        else:
            command = self._resolve_command()
            args = [command, "run", prompt]
            if model:
                args.extend(["-m", model])
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
