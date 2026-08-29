"""Cross-process runtime lock: acquisition, liveness, and release."""

import json
import os
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.workspace.workspace_paths import (
    JSON_INDENT,
    LOCK_TTL_SECONDS,
    now_iso,
    read_json_file,
)
from utils import osutil

_RUNTIME_TRANSITION_THREAD_LOCK = threading.Lock()


class _RuntimeTransitionGuard:
    """Serialize runtime-lock creation and removal across threads and processes."""

    def __init__(self, lock_path: Path):
        self.path = lock_path.with_name(f"{lock_path.name}.guard")
        self.handle = None

    def __enter__(self):
        _RUNTIME_TRANSITION_THREAD_LOCK.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            if osutil.IS_WINDOWS:
                import msvcrt

                self.handle.seek(0, os.SEEK_END)
                if self.handle.tell() == 0:
                    self.handle.write(b"\0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
            return self
        except Exception:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            _RUNTIME_TRANSITION_THREAD_LOCK.release()
            raise

    def __exit__(self, *exc) -> None:
        try:
            if self.handle is not None:
                if osutil.IS_WINDOWS:
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            _RUNTIME_TRANSITION_THREAD_LOCK.release()


def _runtime_lock_payload(lock_path: Path) -> dict | None:
    try:
        payload = read_json_file(lock_path)
    except (ValueError, FileNotFoundError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _runtime_lock_age_seconds(lock_path: Path, payload: dict | None) -> float | None:
    created = None
    if payload:
        try:
            created = datetime.fromisoformat(str(payload.get("created_at") or ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            created = None
    if created is not None:
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
    try:
        modified = datetime.fromtimestamp(lock_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - modified).total_seconds())


def _process_identity(pid: int) -> str | None:
    """Best-effort process start identity, stable across PID reuse."""
    if pid <= 0:
        return None
    native = osutil.pid_create_time(pid)
    if native is not None:
        return f"native:{native!r}"
    if os.name == "nt":
        return None
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        stat_text = proc_stat.read_text(encoding="ascii")
        fields_after_comm = stat_text.rsplit(")", 1)[1].split()
        start_ticks = fields_after_comm[19]
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="ascii"
            ).strip()
        except OSError:
            boot_id = "unknown-boot"
        return f"proc:{boot_id}:{start_ticks}"
    except (OSError, IndexError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            **osutil.hidden_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    started = (result.stdout or "").strip()
    return f"ps:{started}" if result.returncode == 0 and started else None


def _runtime_lock_is_active(lock_path: Path, payload: dict | None) -> bool:
    if payload:
        try:
            pid = int(payload.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            if not osutil.process_alive(pid):
                return False
            expected_identity = payload.get("process_identity")
            if expected_identity:
                return _process_identity(pid) == expected_identity
            expected_create_time = payload.get("pid_create_time")
            if expected_create_time is not None:
                return not osutil.pid_reused(pid, expected_create_time)
            age = _runtime_lock_age_seconds(lock_path, payload)
            return age is None or age <= LOCK_TTL_SECONDS
    age = _runtime_lock_age_seconds(lock_path, payload)
    return age is None or age <= LOCK_TTL_SECONDS


def runtime_lock_owned(lock_path: Path, session_id: str, lock_token: str) -> bool:
    """True only while the exact runtime-lock generation is still installed."""
    with _RuntimeTransitionGuard(lock_path):
        payload = _runtime_lock_payload(lock_path)
        return bool(
            payload
            and payload.get("session_id") == session_id
            and payload.get("token") == lock_token
        )


def acquire_runtime_lock(lock_path: Path, command: str, session_id: str) -> dict:
    stale_payload = None
    stale_replaced = False
    with _RuntimeTransitionGuard(lock_path):
        if lock_path.exists():
            payload = _runtime_lock_payload(lock_path)
            if _runtime_lock_is_active(lock_path, payload):
                return {
                    "ok": False,
                    "stale": False,
                    "payload": payload or {"invalid": True},
                }
            stale_payload = payload or {"invalid": True}
            try:
                lock_path.unlink()
                stale_replaced = True
            except FileNotFoundError:
                pass

        token = secrets.token_hex(16)
        payload = {
            "command": command,
            "session_id": session_id,
            "token": token,
            "pid": os.getpid(),
            "pid_create_time": osutil.pid_create_time(os.getpid()),
            "process_identity": _process_identity(os.getpid()),
            "created_at": now_iso(),
        }
        encoded = json.dumps(payload, indent=JSON_INDENT).encode("utf-8")
        fd = None
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            current = _runtime_lock_payload(lock_path)
            return {
                "ok": False,
                "stale": False,
                "payload": current or {"invalid": True},
            }
        except Exception:
            if fd is not None:
                os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            raise

    return {
        "ok": True,
        "stale": stale_replaced,
        "payload": stale_payload,
        "token": token,
    }


def release_runtime_lock(
    lock_path: Path,
    session_id: str | None = None,
    lock_token: str | None = None,
) -> None:
    """Release only the exact runtime-lock generation owned by this call."""
    with _RuntimeTransitionGuard(lock_path):
        if not lock_path.exists():
            return
        if session_id is not None or lock_token is not None:
            payload = _runtime_lock_payload(lock_path)
            if not isinstance(payload, dict):
                return
            if session_id is not None and payload.get("session_id") != session_id:
                return
            if lock_token is not None and payload.get("token") != lock_token:
                return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


