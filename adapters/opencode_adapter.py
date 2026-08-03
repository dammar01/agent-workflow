import hashlib
import os
import threading
import time
from pathlib import Path
import subprocess

from config.settings import (
    DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
    DEFAULT_OPENCODE_AGENT,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    OPENCODE_COMMAND,
)
from core.contract import make_error as _contract_make_error
from core.contract import make_ok as _contract_make_ok
from utils import osutil
from utils.redact import redact, redact_value
from utils.parser import (
    clean_opencode_output,
    ensure_text,
    extract_opencode_session_id,
    first_non_empty,
)

# Upper bound on captured stdout/stderr per stream (~4MB of text). A well-behaved evidence
# run is a few hundred KB; this only clamps a runaway/pathological process so it cannot
# grow the in-memory buffer without limit. The tail is kept (evidence blocks live at the end).
MAX_CAPTURE_CHARS = 4_000_000

# Substrings that signal opencode refused a path rather than a real crash.
_PERMISSION_SIGNS = (
    "permission denied",
    "eacces",
    "not permitted",
    "access is denied",
    "outside",
    "forbidden",
)

_RATE_LIMIT_SIGNS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "too many requests",
    "quota",
    "usage limit",
    "insufficient credits",
    "out of credits",
    "overloaded",
    "capacity",
)


def _is_rate_limited(*texts: str) -> bool:
    """True when any text carries a provider quota/rate-limit signature."""
    blob = " ".join(t.lower() for t in texts if t)
    return any(sign in blob for sign in _RATE_LIMIT_SIGNS)


_STREAM_FAIL_SIGNS = (
    "streaming response failed",
    "stream closed",
    "stream error",
    "connection reset",
    "connection closed",
    "socket hang up",
    "premature close",
    "econnreset",
    "epipe",
)


def _is_stream_failure(*texts: str) -> bool:
    """True when any text carries a dropped-provider-stream signature."""
    blob = " ".join(t.lower() for t in texts if t)
    return any(sign in blob for sign in _STREAM_FAIL_SIGNS)


_ERROR_TAIL_CHARS = 1600


def _error_tail(*texts: str) -> str:
    """The trailing slice of the combined error text — opencode's own terminal error,
    isolated from the agent transcript that precedes it."""
    blob = "\n".join(t for t in texts if t)
    return blob[-_ERROR_TAIL_CHARS:]


_STREAM_FAIL_NEXT_ACTION = (
    "Transient — the provider stream dropped mid-answer, the request itself was fine. "
    "Retry once; if it dies again, split the task into two narrower delegated calls. "
    "Do NOT wait for a quota reset, this is not a limit."
)


_CMD_LINE_LIMIT = 8191
_CMD_LINE_HEADROOM = 400
_CMD_LINE_SIGNS = ("command line is too long", "the input line is too long")


def _argv_meta(args: list[str]) -> dict:
    encoded = "\0".join(str(arg) for arg in args).encode("utf-8", errors="replace")
    return {
        "argv_count": len(args),
        "argv_chars": sum(len(str(arg)) for arg in args),
        "argv_sha256": hashlib.sha256(encoded).hexdigest()[:16],
    }


def _attach_redactions(meta: dict, hits: list[dict]) -> None:
    if not hits:
        return
    counts: dict[str, int] = {}
    for hit in [*(meta.get("redactions") or []), *hits]:
        if not isinstance(hit, dict) or not hit.get("kind"):
            continue
        kind = str(hit["kind"])
        counts[kind] = counts.get(kind, 0) + int(hit.get("count") or 0)
    meta["redactions"] = [
        {"kind": kind, "count": count} for kind, count in counts.items()
    ]
    meta["redaction_count"] = sum(counts.values())


def _sanitize_meta(meta: dict | None, extra_hits: list[dict] | None = None) -> dict:
    clean, hits = redact_value(meta or {})
    if not isinstance(clean, dict):
        clean = {}
    _attach_redactions(clean, [*(extra_hits or []), *hits])
    return clean


def make_error(
    error_type: str,
    message: str,
    next_action: str,
    meta: dict | None = None,
    **fields,
) -> dict:
    clean_message, message_hits = redact(str(message or ""))
    clean_next, next_hits = redact(str(next_action or ""))
    clean_fields, field_hits = redact_value(fields)
    safe_meta = _sanitize_meta(meta, [*message_hits, *next_hits, *field_hits])
    return _contract_make_error(
        error_type, clean_message, clean_next, meta=safe_meta, **clean_fields
    )


def make_ok(content: str, meta: dict | None = None, digest: dict | None = None) -> dict:
    clean_content, content_hits = redact(content or "")
    clean_digest, digest_hits = redact_value(digest)
    safe_meta = _sanitize_meta(meta, [*content_hits, *digest_hits])
    return _contract_make_ok(clean_content, safe_meta, clean_digest)


def _too_long_for_cmd(args: list[str]) -> int | None:
    """Total command-line length when it will not fit, else None. Windows only."""
    if not osutil.IS_WINDOWS:
        return None
    total = sum(len(str(a)) + 3 for a in args)  # +3: two quotes and a separator
    return total if total > (_CMD_LINE_LIMIT - _CMD_LINE_HEADROOM) else None


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
        self.on_progress = on_progress
        self.poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self.bootstrap_timeout_seconds = DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS
        # `plan` is the read-only fallback when project config omits a custom agent.
        self.agent = DEFAULT_OPENCODE_AGENT
        self.last_call_meta: dict = {}
        self.on_session_created = None

    def _agent_args(self) -> list[str]:
        return ["--agent", self.agent or DEFAULT_OPENCODE_AGENT]

    def _popen_capture(
        self,
        args: list[str],
        env: dict,
        cwd: str | None,
        timeout: int | None,
        phase: str,
    ) -> dict:
        """Run `args`, draining stdout/stderr on threads while the main loop polls.

        Threaded draining allows heartbeat polling and whole-process-tree termination
        on timeout.

        Returns {'returncode', 'stdout', 'stderr', 'timed_out', 'duration_seconds',
                 'pid', 'kill'} — never raises for timeout.
        """
        started = time.monotonic()
        self.last_call_meta = _sanitize_meta(
            {"phase": phase, **_argv_meta(args), "cwd": cwd, "timeout_seconds": timeout}
        )
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
        sizes: dict[str, int] = {"stdout": 0, "stderr": 0}
        truncated: dict[str, bool] = {"stdout": False, "stderr": False}
        last_output = {"at": started}

        def _drain(stream, key: str) -> None:
            # Bounded capture: a runaway or pathological opencode run must not grow the
            # buffer without limit and exhaust RAM. The evidence contract ([EVIDENCE] /
            # [DIGEST]) is emitted at the END, so when we exceed the cap we drop from the
            # FRONT and keep the tail — the part the main_agent actually consumes.
            buf = chunks[key]
            try:
                for line in iter(stream.readline, ""):
                    buf.append(line)
                    sizes[key] += len(line)
                    last_output["at"] = time.monotonic()
                    while sizes[key] > MAX_CAPTURE_CHARS and len(buf) > 1:
                        sizes[key] -= len(buf.pop(0))
                        truncated[key] = True
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        def _joined(key: str) -> str:
            text = "".join(chunks[key])
            if truncated[key]:
                return (
                    f"[...TRUNCATED: earlier {key} dropped, kept last "
                    f"~{MAX_CAPTURE_CHARS // 1000}KB...]\n" + text
                )
            return text

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
            now = time.monotonic()
            self._tick(phase, now - started, now - last_output["at"])
            time.sleep(interval)

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
            "stdout": _joined("stdout"),
            "stderr": _joined("stderr"),
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "idle_seconds": round(time.monotonic() - last_output["at"], 1),
            "pid": proc.pid,
            "kill": kill_info or None,
        }
        self.last_call_meta = _sanitize_meta(
            {
                "phase": phase,
                **_argv_meta(args),
                "cwd": cwd,
                "returncode": meta["returncode"],
                "timed_out": timed_out,
                "duration_seconds": meta["duration_seconds"],
                "timeout_seconds": timeout,
                "kill": kill_info or None,
                "output_complete": drained,
                "idle_seconds": meta["idle_seconds"],
                "stderr_tail": meta["stderr"][-2000:],
            }
        )
        return meta

    def _tick(self, phase: str, elapsed: float, idle: float = 0.0) -> None:
        """Emit one liveness beat. Never let a bad callback kill the run.

        A heartbeat only proves this loop is turning; `idle_seconds` distinguishes
        active output from waiting.
        """
        if not self.on_progress:
            return
        try:
            self.on_progress(
                {
                    "phase": phase,
                    "elapsed_seconds": round(elapsed, 1),
                    "idle_seconds": round(idle, 1),
                }
            )
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
        args.extend(self._agent_args())
        if model:
            args.extend(["-m", model])
        args.extend(["--print-logs", "--log-level", "INFO"])

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        cwd = self._resolve_work_dir(work_dir)
        meta: dict = {**_argv_meta(args), "cwd": cwd}
        if workflow_session_id:
            meta["workflow_session_id"] = workflow_session_id

        # Bootstrap uses a separate budget so a hung init cannot wait indefinitely.
        budget = self.bootstrap_timeout_seconds
        if budget is not None and budget <= 0:
            budget = None

        try:
            outcome = self._popen_capture(args, env, cwd, budget, "bootstrap")
        except (OSError, FileNotFoundError) as exc:
            meta.update(
                {"error": str(exc), "returncode": 1, "opencode_session_id": None}
            )
            return None, _sanitize_meta(meta)

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
                }
            )
            return None, _sanitize_meta(meta)

        combined = outcome["stdout"] + outcome["stderr"]
        session_id = extract_opencode_session_id(combined)
        meta.update(
            {
                "returncode": outcome["returncode"],
                "opencode_session_id": session_id,
                "stderr_tail": outcome["stderr"].strip()[-2000:],
                "stdout_tail": outcome["stdout"].strip()[-500:],
            }
        )
        return session_id, _sanitize_meta(meta)

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
        args.extend(self._agent_args())
        if model:
            args.extend(["-m", model])
        args.extend(["-s", session_id])
        return self._run_args(args, work_dir)

    def _prospective_agent_args(self, prompt: str, model: str | None) -> list[str]:
        """The argv run_agent WILL build, with the same `\\n` expansion, so a length
        check here measures the real command line — not the pre-expansion prompt."""
        command = self._resolve_command()
        safe_prompt = prompt.replace("\n", " \\n ")
        args = [command, "run", safe_prompt, *self._agent_args()]
        if model:
            args.extend(["-m", model])
        # `-s <ses_id>` is appended once the session exists; a real id is ~30 chars.
        # Include a stand-in so the pre-bootstrap measurement matches the final argv.
        args.extend(["-s", "ses_" + "0" * 26])
        return args

    def run(
        self,
        prompt: str,
        session: dict,
        model: str | None = None,
        work_dir: str | None = None,
    ) -> dict:
        # Fail fast: the command-line length is knowable BEFORE bootstrap, so an
        # oversize prompt must not first pay ~10-60s spawning an opencode session only
        # to be rejected. Measure the real (\n-expanded) argv up front.
        oversize = _too_long_for_cmd(self._prospective_agent_args(prompt, model))
        if oversize is not None:
            return make_error(
                "prompt_too_long",
                f"command line is {oversize} chars; the Windows shell caps it at {_CMD_LINE_LIMIT}",
                next_action=(
                    "Shorten the task text — split it into two narrower delegated calls. "
                    "The prompt scaffolding is anchored in AGENTS.md and the evidence "
                    "sidecars, so the task is the only caller-controlled size left."
                ),
                meta={
                    "command_line_chars": oversize,
                    "limit": _CMD_LINE_LIMIT,
                    "cwd": self._resolve_work_dir(work_dir),
                    "checked": "pre_bootstrap",
                },
            )

        opencode_session_id = session.get("opencode_session_id")
        bootstrap_meta = None

        if not opencode_session_id:
            opencode_session_id, bootstrap_meta = self.init_session(
                model, work_dir, workflow_session_id=session.get("session_id")
            )
            if not opencode_session_id:
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
                raw_tail=first_non_empty(
                    meta.get("error"), meta.get("stderr_tail"), meta.get("stdout_tail")
                )[:500],
            )

        session["opencode_session_id"] = opencode_session_id
        if bootstrap_meta is not None and self.on_session_created:
            try:
                self.on_session_created(opencode_session_id)
            except Exception as exc:
                return make_error(
                    "session_capture_failed",
                    "captured OpenCode session id but failed to persist it",
                    next_action=(
                        "Check session storage permissions, then retry; the delegated task "
                        "was not started."
                    ),
                    meta={
                        "error": f"{type(exc).__name__}: {exc}",
                        "opencode_session_id": opencode_session_id,
                    },
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
        args = [command, "run", "PING. Reply PONG.", *self._agent_args()]
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
            return _sanitize_meta(
                {
                    "alive": False,
                    "reason": "command_not_found",
                    "error": str(exc),
                    "returncode": None,
                    "duration_seconds": None,
                    "timed_out": False,
                }
            )

        probe_tail = _error_tail(outcome["stderr"], outcome["stdout"])
        rate_limited = _is_rate_limited(probe_tail)
        stream_failed = _is_stream_failure(probe_tail)
        if rate_limited:
            reason = "probe_rate_limited"
        elif stream_failed:
            reason = "probe_stream_failed"
        elif outcome["timed_out"]:
            reason = "probe_timeout"
        elif outcome["returncode"] != 0:
            reason = "probe_error"
        else:
            reason = "probe_ok"

        if not probe_tail and outcome["timed_out"]:
            rate_limited = None

        return _sanitize_meta(
            {
                "alive": reason == "probe_ok",
                "reason": reason,
                "rate_limited": rate_limited,
                "no_probe_output": not probe_tail,
                "stream_failed": stream_failed,
                "returncode": outcome["returncode"],
                "duration_seconds": outcome["duration_seconds"],
                "timed_out": outcome["timed_out"],
                "stderr_tail": outcome["stderr"].strip()[-500:],
            }
        )

    def _run_args(self, args: list[str], work_dir: str | None = None) -> dict:
        oversize = _too_long_for_cmd(args)
        if oversize is not None:
            return make_error(
                "prompt_too_long",
                f"command line is {oversize} chars; the Windows shell caps it at {_CMD_LINE_LIMIT}",
                next_action=(
                    "Shorten the task text — split it into two narrower delegated calls. "
                    "The prompt scaffolding (constraints, graph leads, output format) is "
                    "fixed cost; only the task is yours to trim."
                ),
                meta={
                    "command_line_chars": oversize,
                    "limit": _CMD_LINE_LIMIT,
                    "cwd": work_dir,
                },
            )

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
                meta={"error": str(exc), **_argv_meta(args), "cwd": cwd},
            )
        except OSError as exc:
            return make_error(
                "unknown",
                str(exc),
                next_action="Inspect .workflow/sessions/<session>/logs and rerun; report if it persists.",
                meta={"error": type(exc).__name__, **_argv_meta(args), "cwd": cwd},
            )

        if outcome["timed_out"]:
            raw = first_non_empty(
                outcome["stderr"],
                outcome["stdout"],
                f"timeout after {self.timeout_seconds}s",
            )
            timeout_meta = {
                "timeout_seconds": self.timeout_seconds,
                **_argv_meta(args),
                "cwd": cwd,
                "duration_seconds": outcome["duration_seconds"],
                "idle_seconds": outcome.get("idle_seconds"),
                "kill": outcome["kill"],
            }
            if _is_rate_limited(_error_tail(outcome["stderr"], outcome["stdout"])):
                return make_error(
                    "rate_limited",
                    clean_opencode_output(raw) or "opencode hit a provider rate limit",
                    next_action=(
                        "Second agent is out of quota. Wait for the limit to reset, switch "
                        "model in .workflow/opencode.json, or check the provider account — "
                        "do NOT resubmit immediately."
                    ),
                    meta=timeout_meta,
                )
            return make_error(
                "timeout",
                clean_opencode_output(raw),
                next_action="Increase timeout_seconds (0 = no limit) or narrow the task, then retry.",
                meta=timeout_meta,
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
            **_argv_meta(args),
            "cwd": cwd,
            "duration_seconds": outcome["duration_seconds"],
            "idle_seconds": outcome.get("idle_seconds"),
            "opencode_session_id": extract_opencode_session_id(combined),
        }
        cleaned = clean_opencode_output(raw)

        if outcome["returncode"] != 0:
            err_tail = _error_tail(meta["stderr"])
            tail_low = err_tail.lower()
            if _is_rate_limited(err_tail):
                return make_error(
                    "rate_limited",
                    cleaned
                    or "opencode refused: provider rate limit / quota exhausted",
                    next_action=(
                        "Second agent is out of quota. Wait for the limit to reset, switch "
                        "model in .workflow/opencode.json, or check the provider account — "
                        "do NOT resubmit immediately."
                    ),
                    meta=meta,
                )
            if _is_stream_failure(err_tail):
                return make_error(
                    "streaming_failed",
                    cleaned or "opencode lost the provider stream mid-response",
                    next_action=_STREAM_FAIL_NEXT_ACTION,
                    meta=meta,
                )
            if any(sign in tail_low for sign in _CMD_LINE_SIGNS):
                return make_error(
                    "prompt_too_long",
                    cleaned or "the shell refused the command line as too long",
                    next_action=(
                        "Shorten the task text — split it into two narrower delegated calls."
                    ),
                    meta=meta,
                )
            if any(sign in tail_low for sign in _PERMISSION_SIGNS):
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

        # make_ok sanitizes content and metadata before either can reach persistence.
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
