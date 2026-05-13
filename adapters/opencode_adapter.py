import os
from pathlib import Path
import shutil
import subprocess
import sys

from config.settings import DEFAULT_TIMEOUT_SECONDS, OPENCODE_COMMAND
from utils.parser import (
    clean_opencode_output,
    ensure_text,
    extract_opencode_session_id,
    first_non_empty,
)


class OpenCodeAdapter:
    adapter = "opencode"

    def __init__(
        self,
        command: str = OPENCODE_COMMAND,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.no_timeout = timeout_seconds is None or timeout_seconds <= 0

    @staticmethod
    def _get_windows_startupinfo() -> tuple:
        """Return startupinfo and creationflags to hide console window on Windows."""
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            return startupinfo, subprocess.CREATE_NO_WINDOW
        return None, 0

    def init_session(
        self,
        model: str | None = None,
        work_dir: str | None = None,
        workflow_session_id: str | None = None,
    ) -> tuple[str | None, dict]:
        """Capture a new OpenCode session id by running bootstrap and waiting for completion."""
        command = self._resolve_command()
        args = [command, "run", "Initialize session. Reply READY."]
        args.extend(["--agent", "plan"])
        if model:
            args.extend(["-m", model])
        args.extend(["--print-logs", "--log-level", "INFO"])

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        cwd = self._resolve_work_dir(work_dir)
        meta: dict = {"args": args, "cwd": cwd}
        if workflow_session_id:
            meta["workflow_session_id"] = workflow_session_id

        startupinfo, creationflags = self._get_windows_startupinfo()
        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=cwd,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            stdout, stderr = proc.communicate(timeout=None)
        except (OSError, FileNotFoundError) as exc:
            meta.update(
                {"error": str(exc), "returncode": 1, "opencode_session_id": None}
            )
            return None, meta
        except subprocess.TimeoutExpired:
            if proc is not None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            meta.update(
                {
                    "error": "timeout after 30s",
                    "returncode": 1,
                    "opencode_session_id": None,
                }
            )
            return None, meta

        combined = stdout + stderr
        session_id = extract_opencode_session_id(combined)
        meta.update(
            {
                "returncode": proc.returncode,
                "opencode_session_id": session_id,
            }
        )
        return session_id, meta

    def run_agent(
        self,
        prompt: str,
        session_id: str,
        model: str | None = None,
        work_dir: str | None = None,
    ) -> dict:
        """Spawn workflow agent in existing session."""
        command = self._resolve_command()
        safe_prompt = prompt.replace("\n", " \\n ")
        args = [command, "run", safe_prompt]
        args.extend(["--agent", "plan"])
        if model:
            args.extend(["-m", model])
        args.extend(["-s", session_id])
        return self._run_args(args, work_dir)

    def run(
        self,
        prompt: str,
        session: dict,
        model: str | None = None,
        work_dir: str | None = None,
    ) -> dict:
        opencode_session_id = session.get("opencode_session_id")
        bootstrap_meta = None

        if not opencode_session_id:
            opencode_session_id, bootstrap_meta = self.init_session(
                model, work_dir, workflow_session_id=session.get("session_id")
            )

        if not opencode_session_id:
            return self._error(
                "init_session failed: session_id not captured", bootstrap_meta or {}
            )

        result = self.run_agent(prompt, opencode_session_id, model, work_dir)

        if bootstrap_meta is not None:
            result["meta"]["bootstrap"] = bootstrap_meta
            result["meta"]["opencode_session_id"] = (
                result["meta"].get("opencode_session_id") or opencode_session_id
            )

        return result

    def _run_args(self, args: list[str], work_dir: str | None = None) -> dict:

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cwd = self._resolve_work_dir(work_dir)

        startupinfo, creationflags = self._get_windows_startupinfo()
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds if not self.no_timeout else None,
                check=False,
                env=env,
                cwd=cwd,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            return self._error(
                f"command not found: {args[0]}",
                {"error": str(exc), "args": args, "cwd": cwd},
            )
        except subprocess.TimeoutExpired as exc:
            raw = first_non_empty(
                exc.stderr, exc.stdout, f"timeout after {self.timeout_seconds}s"
            )
            return self._error(
                raw, {"timeout_seconds": self.timeout_seconds, "args": args, "cwd": cwd}
            )
        except OSError as exc:
            return self._error(
                str(exc), {"error": type(exc).__name__, "args": args, "cwd": cwd}
            )

        raw = first_non_empty(completed.stdout, completed.stderr)
        combined = "\n".join(
            part
            for part in (ensure_text(completed.stdout), ensure_text(completed.stderr))
            if part
        )
        meta = {
            "returncode": completed.returncode,
            "stderr": ensure_text(completed.stderr).strip(),
            "args": args,
            "cwd": cwd,
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
        return (
            shutil.which(f"{self.command}.cmd")
            or shutil.which(self.command)
            or self.command
        )

    @staticmethod
    def _resolve_work_dir(work_dir: str | None) -> str | None:
        return str(Path(work_dir).resolve()) if work_dir else None
