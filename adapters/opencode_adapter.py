import os
from pathlib import Path
import subprocess

from config.settings import DEFAULT_TIMEOUT_SECONDS, OPENCODE_COMMAND
from core.contract import make_error, make_ok
from utils import osutil
from utils.parser import (
    clean_opencode_output,
    ensure_text,
    extract_opencode_session_id,
    first_non_empty,
)

# Substrings that signal opencode refused a path rather than a real crash.
_PERMISSION_SIGNS = ("permission denied", "eacces", "not permitted", "access is denied", "outside", "forbidden")


def _guess_blocked_paths(stderr: str) -> list[str]:
    """Best-effort pull of path-like tokens from an opencode permission error."""
    import re

    tokens = re.findall(r"(?:[A-Za-z]:[\\/]|/|~)[^\s\"']+", stderr or "")
    seen: set[str] = set()
    return [t for t in tokens if not (t in seen or seen.add(t))][:8]


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
                **osutil.hidden_run_kwargs(),
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
        # opencode `run` truncates a multiline arg at the first newline (only line 1
        # reaches the agent). Flatten to a single line with visible \n markers so the
        # whole prompt survives. The multiline original stays archived in
        # .workflow/sessions/<session>/logs + runtime/prompt.txt for audit — only the wire form is flattened.
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
            if not opencode_session_id:  # one retry — capture is the flaky step
                opencode_session_id, bootstrap_meta = self.init_session(
                    model, work_dir, workflow_session_id=session.get("session_id")
                )

        if not opencode_session_id:
            meta = dict(bootstrap_meta or {})
            return make_error(
                "session_capture_failed",
                "init_session failed: opencode session id not captured after retry",
                next_action="Check opencode is logged in and `opencode run` prints a ses_ id; rerun the command.",
                meta=meta,
                raw_tail=ensure_text(meta.get("error"))[:500],
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
                **osutil.hidden_run_kwargs(),
            )
        except FileNotFoundError as exc:
            return make_error(
                "command_not_found",
                f"command not found: {args[0]}",
                next_action="Install opencode or fix opencode_command in .workflow/opencode.json.",
                meta={"error": str(exc), "args": args, "cwd": cwd},
            )
        except subprocess.TimeoutExpired as exc:
            raw = first_non_empty(exc.stderr, exc.stdout, f"timeout after {self.timeout_seconds}s")
            return make_error(
                "timeout",
                clean_opencode_output(raw),
                next_action="Increase timeout_seconds (0 = no limit) or narrow the task, then retry.",
                meta={"timeout_seconds": self.timeout_seconds, "args": args, "cwd": cwd},
            )
        except OSError as exc:
            return make_error(
                "unknown",
                str(exc),
                next_action="Inspect .workflow/sessions/<session>/logs and rerun; report if it persists.",
                meta={"error": type(exc).__name__, "args": args, "cwd": cwd},
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
        cleaned = clean_opencode_output(raw)

        if completed.returncode != 0:
            stderr_low = meta["stderr"].lower()
            if any(sign in stderr_low or sign in cleaned.lower() for sign in _PERMISSION_SIGNS):
                return make_error(
                    "permission_denied",
                    cleaned or "opencode refused access",
                    next_action="Grant explicit access to the path or move the context inside the project, then retry.",
                    meta=meta,
                    blocked_paths=_guess_blocked_paths(meta["stderr"]),
                )
            return make_error(
                "unknown",
                cleaned or f"opencode exited {completed.returncode}",
                next_action="Inspect .workflow/sessions/<session>/logs for the raw output and rerun.",
                meta=meta,
            )

        if not cleaned.strip():
            return make_error(
                "empty_output",
                "opencode returned no content",
                next_action="Rephrase the task or check .workflow/sessions/<session>/logs raw_tail; the run succeeded but produced nothing.",
                meta=meta,
                raw_tail=raw[:500],
            )

        return make_ok(cleaned, meta)

    @staticmethod
    def _error(content: str, meta: dict) -> dict:
        return make_error(
            "unknown",
            ensure_text(content),
            next_action="Inspect .workflow/sessions/<session>/logs and rerun.",
            meta=meta,
        )

    def _resolve_command(self) -> str:
        return osutil.resolve_exe(self.command)

    @staticmethod
    def _resolve_work_dir(work_dir: str | None) -> str | None:
        return str(Path(work_dir).resolve()) if work_dir else None
