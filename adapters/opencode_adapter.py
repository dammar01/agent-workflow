import os
import threading
import time
from pathlib import Path
import subprocess

from config.settings import (
    DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    OPENCODE_COMMAND,
)
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
        on_progress=None,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.no_timeout = timeout_seconds is None or timeout_seconds <= 0
        # Called once per poll tick while opencode runs. The worker is otherwise
        # fully blocked on the subprocess, so this is the ONLY place a liveness
        # heartbeat can be emitted from. Set by the caller (worker/executor).
        self.on_progress = on_progress
        self.poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self.bootstrap_timeout_seconds = DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS
        self.last_call_meta: dict = {}

    def _popen_capture(
        self,
        args: list[str],
        env: dict,
        cwd: str | None,
        timeout: int | None,
        phase: str,
    ) -> dict:
        """Run `args`, draining stdout/stderr on threads while the main loop polls.

        Replaces subprocess.run so the worker gets a tick between polls (heartbeat)
        and so a timeout can kill the whole process tree rather than just the shim.

        Returns {'returncode', 'stdout', 'stderr', 'timed_out', 'duration_seconds',
                 'pid', 'kill'} — never raises for timeout.
        """
        started = time.monotonic()
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

        chunks: dict[str, list[str]] = {"stdout": [], "stderr": []}

        def _drain(stream, key: str) -> None:
            try:
                for line in iter(stream.readline, ""):
                    chunks[key].append(line)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        kill_info: dict = {}
        interval = max(0.2, float(self.poll_interval or 2))
        while True:
            if proc.poll() is not None:
                break
            if timeout and (time.monotonic() - started) >= timeout:
                timed_out = True
                kill_info = osutil.terminate_tree(proc)
                break
            self._tick(phase, time.monotonic() - started)
            time.sleep(interval)

        # If the kill failed the child is still holding the pipes open, the drain threads
        # are still blocked in readline, and the captured output below is whatever landed
        # so far. Surface that instead of returning a quietly truncated result.
        drained = True
        for reader in readers:
            reader.join(timeout=10)
            if reader.is_alive():
                drained = False
        try:
            proc.wait(timeout=10)
        except Exception:
            pass

        meta = {
            "output_complete": drained,
            "returncode": proc.returncode,
            "stdout": "".join(chunks["stdout"]),
            "stderr": "".join(chunks["stderr"]),
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "pid": proc.pid,
            "kill": kill_info or None,
        }
        self.last_call_meta = {
            "phase": phase,
            "args": args,
            "cwd": cwd,
            "returncode": meta["returncode"],
            "timed_out": timed_out,
            "duration_seconds": meta["duration_seconds"],
            "timeout_seconds": timeout,
            "kill": kill_info or None,
            "output_complete": drained,
            "stderr_tail": meta["stderr"][-2000:],
        }
        return meta

    def _tick(self, phase: str, elapsed: float) -> None:
        """Emit one liveness beat. Never let a bad callback kill the run."""
        if not self.on_progress:
            return
        try:
            self.on_progress({"phase": phase, "elapsed_seconds": round(elapsed, 1)})
        except Exception:
            pass

    def _effective_timeout(self) -> int | None:
        return None if self.no_timeout else self.timeout_seconds

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

        # Bootstrap gets its OWN (shorter) budget: a hung `opencode run` here used to
        # wait forever because communicate(timeout=None) was hardcoded.
        budget = self.bootstrap_timeout_seconds
        if budget is not None and budget <= 0:
            budget = None

        try:
            outcome = self._popen_capture(args, env, cwd, budget, "bootstrap")
        except (OSError, FileNotFoundError) as exc:
            meta.update(
                {"error": str(exc), "returncode": 1, "opencode_session_id": None}
            )
            return None, meta

        meta["duration_seconds"] = outcome["duration_seconds"]
        if outcome["kill"]:
            meta["kill"] = outcome["kill"]

        if outcome["timed_out"]:
            meta.update(
                {
                    "error": f"bootstrap timeout after {budget}s",
                    "returncode": 1,
                    "opencode_session_id": None,
                    "timed_out": True,
                    "stderr": outcome["stderr"].strip()[-2000:],
                }
            )
            return None, meta

        combined = outcome["stdout"] + outcome["stderr"]
        session_id = extract_opencode_session_id(combined)
        meta.update(
            {
                "returncode": outcome["returncode"],
                "opencode_session_id": session_id,
                "stderr": outcome["stderr"].strip()[-2000:],
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

    def probe(
        self,
        model: str | None = None,
        work_dir: str | None = None,
        timeout_seconds: int = 45,
    ) -> dict:
        """Liveness probe: trivial prompt in a BRAND NEW opencode session.

        Answers the one question PID liveness cannot: is the second agent alive but
        waiting (rate limited / thinking), or actually gone? Deliberately does NOT
        reuse `-s <session>` — the stuck session is the thing under suspicion.

        Returns {'alive', 'reason', 'returncode', 'duration_seconds', 'timed_out'}.
        Its own short timeout matters: without it the watchdog hangs like its patient.
        """
        command = self._resolve_command()
        args = [command, "run", "PING. Reply PONG.", "--agent", "plan"]
        if model:
            args.extend(["-m", model])

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cwd = self._resolve_work_dir(work_dir)

        try:
            outcome = self._popen_capture(
                args, env, cwd, max(5, int(timeout_seconds)), "probe"
            )
        except (OSError, FileNotFoundError) as exc:
            return {
                "alive": False,
                "reason": "command_not_found",
                "error": str(exc),
                "returncode": None,
                "duration_seconds": None,
                "timed_out": False,
            }

        if outcome["timed_out"]:
            reason = "probe_timeout"
        elif outcome["returncode"] != 0:
            reason = "probe_error"
        else:
            reason = "probe_ok"

        return {
            "alive": reason == "probe_ok",
            "reason": reason,
            "returncode": outcome["returncode"],
            "duration_seconds": outcome["duration_seconds"],
            "timed_out": outcome["timed_out"],
            "stderr_tail": outcome["stderr"].strip()[-500:],
        }

    def _run_args(self, args: list[str], work_dir: str | None = None) -> dict:

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cwd = self._resolve_work_dir(work_dir)

        try:
            outcome = self._popen_capture(
                args, env, cwd, self._effective_timeout(), "agent"
            )
        except FileNotFoundError as exc:
            return make_error(
                "command_not_found",
                f"command not found: {args[0]}",
                next_action="Install opencode or fix opencode_command in .workflow/opencode.json.",
                meta={"error": str(exc), "args": args, "cwd": cwd},
            )
        except OSError as exc:
            return make_error(
                "unknown",
                str(exc),
                next_action="Inspect .workflow/sessions/<session>/logs and rerun; report if it persists.",
                meta={"error": type(exc).__name__, "args": args, "cwd": cwd},
            )

        if outcome["timed_out"]:
            raw = first_non_empty(
                outcome["stderr"],
                outcome["stdout"],
                f"timeout after {self.timeout_seconds}s",
            )
            return make_error(
                "timeout",
                clean_opencode_output(raw),
                next_action="Increase timeout_seconds (0 = no limit) or narrow the task, then retry.",
                meta={
                    "timeout_seconds": self.timeout_seconds,
                    "args": args,
                    "cwd": cwd,
                    "duration_seconds": outcome["duration_seconds"],
                    "kill": outcome["kill"],
                },
            )

        raw = first_non_empty(outcome["stdout"], outcome["stderr"])
        combined = "\n".join(
            part
            for part in (ensure_text(outcome["stdout"]), ensure_text(outcome["stderr"]))
            if part
        )
        meta = {
            "returncode": outcome["returncode"],
            "stderr": ensure_text(outcome["stderr"]).strip(),
            "args": args,
            "cwd": cwd,
            "duration_seconds": outcome["duration_seconds"],
            "opencode_session_id": extract_opencode_session_id(combined),
        }
        cleaned = clean_opencode_output(raw)

        if outcome["returncode"] != 0:
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
                cleaned or f"opencode exited {outcome['returncode']}",
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
