"""Codex CLI as a second_agent provider.

Shaped by four facts about `codex exec` that make it a different animal from opencode,
not a rename of it:

1. The prompt goes in on STDIN (`codex exec -`), never as an argument. OpenCode's adapter
   has to measure its own argv against the Windows 8191-char command-line cap and can
   fail a call before the provider ever runs; here that limit is simply unreachable, so
   there is no `prompt_too_long` path to write.
2. The session id arrives in the FIRST event of the stream —
   `{"type":"thread.started","thread_id":"<uuid>"}` — so there is no bootstrap call to
   pay for. OpenCode needs `init_session` because its id only appears once a session has
   been opened; codex hands it over as the run begins. The id is reported through
   `on_session_created` the moment it is seen rather than after the run, so a call that
   dies mid-way still leaves a resumable session behind.
3. Continuing work is a different subcommand, not a flag: `codex exec resume <id> -`.
   That subcommand accepts NEITHER `-C/--cd` NOR `--sandbox` — cwd and sandbox are
   inherited from the session being resumed. The sandbox is re-asserted through
   `-c sandbox_mode=...` anyway, so a resumed call cannot end up looser than the call
   that opened it.
4. Policy is per-invocation, not a config file. Codex reads `~/.codex/config.toml`, but
   every value in it can be overridden with `-c key=value` on the command line, and the
   sandbox has its own `-s` flag. So this provider ships no global config to merge: the
   read-only boundary rides on every single call and there is no file for a user to drift
   out from under it. `config/providers.py` records that choice.

`--output-schema` is deliberately unused. It would force the final message into JSON, and
the workflow's contract is the text blocks `[EVIDENCE]`/`[DIGEST]` — schema-forcing here
would break the very output the runtime parses.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from config.settings import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from core.contract import make_error as _contract_make_error
from core.contract import make_ok as _contract_make_ok
from utils import osutil
from utils.parser import ensure_text
from utils.redact import redact, redact_value

CODEX_COMMAND = os.getenv("CODEX_COMMAND", "codex")
# read-only is the whole point of a second agent: it gathers evidence and writes nothing.
# Overridable for the rare caller that knows better, but never widened by this adapter.
CODEX_SANDBOX = os.getenv("CODEX_SANDBOX", "read-only")

# Upper bound on captured stdout/stderr per stream (~4MB). A healthy evidence run is a few
# hundred KB; this only clamps a runaway process. The FRONT is dropped — the evidence
# contract is emitted at the end, and the tail is what the runtime consumes.
MAX_CAPTURE_CHARS = 4_000_000

# `thread.started` is the documented first event, but the id also appears in the persisted
# rollout header as `session_id`. Both are accepted: a stream that lost its opening line
# should not cost the session.
_THREAD_ID_PATTERNS = (
    r'"thread_id"\s*:\s*"([0-9a-fA-F-]{16,})"',
    r'"session_id"\s*:\s*"([0-9a-fA-F-]{16,})"',
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
)

_STREAM_FAIL_SIGNS = (
    "stream closed",
    "stream error",
    "connection reset",
    "connection closed",
    "socket hang up",
    "premature close",
    "econnreset",
    "epipe",
)

_PERMISSION_SIGNS = (
    "permission denied",
    "eacces",
    "not permitted",
    "access is denied",
    "operation not permitted",
    "outside",
    "forbidden",
)

_ERROR_TAIL_CHARS = 1600


def _error_tail(*texts: str) -> str:
    blob = "\n".join(text for text in texts if text)
    return blob[-_ERROR_TAIL_CHARS:]


def _matches(blob: str, signs: tuple[str, ...]) -> bool:
    lowered = (blob or "").lower()
    return any(sign in lowered for sign in signs)


def _argv_meta(args: list[str]) -> dict:
    encoded = "\0".join(str(arg) for arg in args).encode("utf-8", errors="replace")
    return {
        "argv_count": len(args),
        "argv_chars": sum(len(str(arg)) for arg in args),
        "argv_sha256": hashlib.sha256(encoded).hexdigest()[:16],
    }


def _sanitize_meta(meta: dict | None) -> dict:
    clean, _ = redact_value(meta or {})
    return clean if isinstance(clean, dict) else {}


def make_error(
    error_type: str,
    message: str,
    next_action: str,
    meta: dict | None = None,
    **fields,
) -> dict:
    clean_message, _ = redact(str(message or ""))
    clean_next, _ = redact(str(next_action or ""))
    clean_fields, _ = redact_value(fields)
    return _contract_make_error(
        error_type, clean_message, clean_next, meta=_sanitize_meta(meta), **clean_fields
    )


def make_ok(content: str, meta: dict | None = None, digest: dict | None = None) -> dict:
    clean_content, _ = redact(content or "")
    clean_digest, _ = redact_value(digest)
    return _contract_make_ok(clean_content, _sanitize_meta(meta), clean_digest)


class CodexAdapter:
    adapter = "codex"

    def __init__(
        self,
        command: str = CODEX_COMMAND,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        on_progress=None,
        sandbox: str = CODEX_SANDBOX,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.no_timeout = timeout_seconds is None or timeout_seconds <= 0
        self.on_progress = on_progress
        self.poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self.bootstrap_timeout_seconds = None
        self.agent = None
        self.sandbox = sandbox
        self.last_call_meta: dict = {}
        self.on_session_created = None

    # ---------------------------------------------------------------- public

    @staticmethod
    def extract_session_id(text: str) -> str | None:
        """The codex thread id, read out of its own JSONL stream."""
        body = ensure_text(text)
        for pattern in _THREAD_ID_PATTERNS:
            match = re.search(pattern, body)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def clean_output(text: str) -> str:
        """The agent's final message, pulled out of the JSONL event stream.

        `codex exec --json` emits one JSON object per line; the answer lives in the last
        `{"type":"item.completed","item":{"type":"agent_message","text":...}}`. Anything
        that is not parseable JSON is passed through untouched — a codex build that prints
        plain text should degrade to "the text", not to "".
        """
        body = ensure_text(text)
        found = ""
        saw_json = False
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except ValueError:
                continue
            saw_json = True
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                message = item.get("text")
                if isinstance(message, str) and message.strip():
                    found = message.strip()
        if found:
            return found
        return "" if saw_json else body.strip()

    def run(
        self,
        prompt: str,
        session: dict,
        model: str | None = None,
        work_dir: str | None = None,
    ) -> dict:
        """One delegated call. Resumes the session in `session` when there is one."""
        guard = self._command_guard(work_dir)
        if guard is not None:
            return guard

        session = session if isinstance(session, dict) else {}
        resume_id = session.get("provider_session_id")
        cwd = self._resolve_work_dir(work_dir)
        handle, last_message_path = tempfile.mkstemp(
            prefix="codex-last-", suffix=".txt"
        )
        os.close(handle)
        last_message = Path(last_message_path)

        args = self._build_args(
            resume_id=resume_id, model=model, cwd=cwd, last_message=last_message
        )

        captured: dict = {"session_id": resume_id}

        def _saw_session(thread_id: str) -> None:
            if captured["session_id"] == thread_id:
                return
            captured["session_id"] = thread_id
            session["provider_session_id"] = thread_id
            if not self.on_session_created:
                return
            try:
                self.on_session_created(thread_id)
            except Exception:
                pass 

        try:
            outcome = self._popen_capture(
                args, prompt, cwd, self._effective_timeout(), "agent", _saw_session
            )
        except FileNotFoundError as exc:
            last_message.unlink(missing_ok=True)
            return make_error(
                "command_not_found",
                f"command not found: {args[0]}",
                next_action=(
                    "Install the codex CLI, or set provider_command in "
                    ".workflow/second_agent.json to its absolute path."
                ),
                meta={"error": str(exc), **_argv_meta(args), "cwd": cwd},
            )
        except OSError as exc:
            last_message.unlink(missing_ok=True)
            return make_error(
                "unknown",
                str(exc),
                next_action="Inspect .workflow/sessions/<session>/logs and rerun.",
                meta={"error": type(exc).__name__, **_argv_meta(args), "cwd": cwd},
            )

        meta = {
            "returncode": outcome["returncode"],
            **_argv_meta(args),
            "cwd": cwd,
            "duration_seconds": outcome["duration_seconds"],
            "idle_seconds": outcome["idle_seconds"],
            "provider_session_id": captured["session_id"],
            "resumed": bool(resume_id),
            "sandbox": self.sandbox,
            "stderr": ensure_text(outcome["stderr"]).strip(),
        }
        tail = _error_tail(outcome["stderr"], outcome["stdout"])
        content = self._read_last_message(last_message, outcome["stdout"])
        last_message.unlink(missing_ok=True)

        if outcome["timed_out"]:
            meta["kill"] = outcome["kill"]
            meta["timeout_seconds"] = self.timeout_seconds
            if _matches(tail, _RATE_LIMIT_SIGNS):
                return make_error(
                    "rate_limited",
                    content or "codex ran out of provider quota mid-call",
                    next_action=(
                        "Second agent is out of quota. Wait for the limit to reset or "
                        "switch default_model in .workflow/second_agent.json — do NOT "
                        "resubmit immediately."
                    ),
                    meta=meta,
                )
            return make_error(
                "timeout",
                content or f"codex exceeded {self.timeout_seconds}s",
                next_action=(
                    "Increase timeout_seconds (0 = no limit) or narrow the task, then "
                    "retry. The session was captured, so the retry resumes rather than "
                    "starting over."
                ),
                meta=meta,
            )

        if outcome["returncode"] != 0:
            if _matches(tail, _RATE_LIMIT_SIGNS):
                return make_error(
                    "rate_limited",
                    content or "codex refused: provider quota exhausted",
                    next_action=(
                        "Second agent is out of quota. Wait for the limit to reset or "
                        "switch default_model in .workflow/second_agent.json."
                    ),
                    meta=meta,
                )
            if _matches(tail, _STREAM_FAIL_SIGNS):
                return make_error(
                    "streaming_failed",
                    content or "codex lost the provider stream mid-answer",
                    next_action=(
                        "Transient — the request itself was fine. Retry once; if it dies "
                        "again, split the task into two narrower delegated calls. Do NOT "
                        "wait for a quota reset, this is not a limit."
                    ),
                    meta=meta,
                )
            if _matches(tail, _PERMISSION_SIGNS):
                return make_error(
                    "permission_denied",
                    content or "codex was refused access while reading the target",
                    next_action=(
                        "Grant read access to the path or move the context inside the "
                        "project, then retry."
                    ),
                    meta=meta,
                )
            return make_error(
                "unknown",
                content or f"codex exited {outcome['returncode']}",
                next_action=(
                    "Inspect .workflow/sessions/<session>/logs for the raw JSONL events "
                    "and rerun."
                ),
                meta=meta,
            )

        if not content.strip():
            return make_error(
                "empty_output",
                "codex returned no final message",
                next_action=(
                    "Rephrase the task, or check the raw JSONL events in "
                    ".workflow/sessions/<session>/logs — the run succeeded but produced "
                    "nothing."
                ),
                meta=meta,
                raw_tail=ensure_text(outcome["stdout"])[-500:],
            )

        return make_ok(content, meta)

    def probe(
        self,
        session_id: str | None = None,
        model: str | None = None,
        work_dir: str | None = None,
        timeout_seconds: int | None = 45,
    ) -> dict:
        """Liveness probe: a trivial prompt in a BRAND NEW codex thread.

        `session_id` is accepted for contract symmetry and deliberately ignored — the
        suspect session is the thing under test, so resuming it would prove nothing about
        whether codex itself still answers.

        Returns {'alive', 'reason', ...}. Its own short budget matters: a watchdog that
        can hang is not a watchdog.
        """
        guard = self._command_guard(work_dir)
        if guard is not None:
            return _sanitize_meta(
                {
                    "alive": False,
                    "reason": "command_not_found",
                    "error": guard["content"],
                    "returncode": None,
                    "duration_seconds": None,
                    "timed_out": False,
                }
            )

        cwd = self._resolve_work_dir(work_dir)
        args = self._build_args(
            resume_id=None, model=model, cwd=cwd, last_message=None
        )
        budget = max(5, int(timeout_seconds or 45))

        try:
            outcome = self._popen_capture(
                args, "PING. Reply PONG.", cwd, budget, "probe", None
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

        tail = _error_tail(outcome["stderr"], outcome["stdout"])
        rate_limited = _matches(tail, _RATE_LIMIT_SIGNS)
        stream_failed = _matches(tail, _STREAM_FAIL_SIGNS)
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

        if not tail and outcome["timed_out"]:
            rate_limited = None

        return _sanitize_meta(
            {
                "alive": reason == "probe_ok",
                "reason": reason,
                "rate_limited": rate_limited,
                "no_probe_output": not tail,
                "stream_failed": stream_failed,
                "returncode": outcome["returncode"],
                "duration_seconds": outcome["duration_seconds"],
                "timed_out": outcome["timed_out"],
                "stderr_tail": ensure_text(outcome["stderr"]).strip()[-500:],
            }
        )

    # --------------------------------------------------------------- private

    def _command_guard(self, work_dir: str | None) -> dict | None:
        """Refuse loudly when the configured command is not a codex binary.

        `config/settings.py` defaults `provider_command` to opencode's binary for EVERY
        provider, so a project that selects codex without also setting `provider_command`
        arrives here pointed at the wrong CLI. Running it would half-work and produce
        evidence from a provider nobody asked for; that silent substitution is exactly
        what `UnknownProviderError` refuses to do one layer up.
        """
        name = Path(str(self.command or "")).name.lower()
        stem = name.split(".")[0]
        if stem and stem != "codex" and "codex" not in stem:
            return make_error(
                "command_not_found",
                f"provider is codex but provider_command is '{self.command}'",
                next_action=(
                    'Set "provider_command": "codex" in .workflow/second_agent.json. '
                    "The default value is opencode's binary and does not follow the "
                    "selected provider."
                ),
                meta={
                    "provider": self.adapter,
                    "provider_command": str(self.command),
                    "cwd": self._resolve_work_dir(work_dir),
                },
            )
        return None

    def _build_args(
        self,
        *,
        resume_id: str | None,
        model: str | None,
        cwd: str | None,
        last_message: Path | None,
    ) -> list[str]:
        """argv for one call. `-` puts the prompt on stdin, so argv stays short."""
        command = osutil.resolve_exe(self.command)
        if resume_id:
            # `resume` takes neither -C nor --sandbox: both are inherited from the session
            # being resumed. The sandbox is re-asserted through -c so a resumed call
            # cannot end up looser than the one that opened the thread.
            args = [
                command,
                "exec",
                "resume",
                resume_id,
                "-",
                "--json",
                "--color",
                "never",
                "--skip-git-repo-check",
                "-c",
                f'sandbox_mode="{self.sandbox}"',
            ]
        else:
            args = [
                command,
                "exec",
                "-",
                "--json",
                "--color",
                "never",
                "--skip-git-repo-check",
                "--sandbox",
                self.sandbox,
            ]
            if cwd:
                args.extend(["-C", cwd])
        if last_message is not None:
            args.extend(["-o", str(last_message)])
        if model:
            args.extend(["-m", model])
        return args

    def _popen_capture(
        self,
        args: list[str],
        prompt: str,
        cwd: str | None,
        timeout: int | None,
        phase: str,
        on_session,
    ) -> dict:
        """Run `args`, feeding `prompt` on stdin and draining both streams on threads.

        Threaded draining is what makes the heartbeat and the timeout kill possible:
        `communicate()` blocks until the process ends, which is precisely the case that
        needs handling.
        """
        started = time.monotonic()
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        self.last_call_meta = _sanitize_meta(
            {"phase": phase, **_argv_meta(args), "cwd": cwd, "timeout_seconds": timeout}
        )

        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
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
        seen_session = {"done": False}

        def _drain(stream, key: str) -> None:
            buf = chunks[key]
            try:
                for line in iter(stream.readline, ""):
                    buf.append(line)
                    sizes[key] += len(line)
                    last_output["at"] = time.monotonic()
                    if on_session and not seen_session["done"] and key == "stdout":
                        thread_id = CodexAdapter.extract_session_id(line)
                        if thread_id:
                            seen_session["done"] = True
                            on_session(thread_id)
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

        try:
            proc.stdin.write(prompt or "")
        except Exception:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

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

        outcome = {
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
                "returncode": outcome["returncode"],
                "timed_out": timed_out,
                "duration_seconds": outcome["duration_seconds"],
                "timeout_seconds": timeout,
                "kill": kill_info or None,
                "output_complete": drained,
                "idle_seconds": outcome["idle_seconds"],
                "stderr_tail": outcome["stderr"][-2000:],
            }
        )
        return outcome

    def _tick(self, phase: str, elapsed: float, idle: float = 0.0) -> None:
        """One liveness beat. A broken callback must not kill a running call."""
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

    @staticmethod
    def _read_last_message(last_message: Path | None, stdout: str) -> str:
        """The final answer: the `-o` file first, the event stream as the fallback.

        Two sources because they fail differently. The file is codex's own statement of
        what the final message was, but it is not written when the run dies early; the
        stream survives that, and carries the same text one event deeper.
        """
        raw = ""
        if last_message is not None:
            try:
                raw = last_message.read_text(encoding="utf-8").strip()
            except OSError:
                raw = ""
        return raw or CodexAdapter.clean_output(stdout)

    def _effective_timeout(self) -> int | None:
        return None if self.no_timeout else self.timeout_seconds

    @staticmethod
    def _resolve_work_dir(work_dir: str | None) -> str | None:
        return str(Path(work_dir).resolve()) if work_dir else None
