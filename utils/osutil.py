"""Cross-OS primitives. All platform-specific branches live here — nowhere else.

Windows + macOS + Linux. Stdlib only (no psutil).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def python_exe() -> str:
    """Resolve the python interpreter name for generated scripts."""
    return sys.executable or ("python" if IS_WINDOWS else "python3")


def resolve_exe(name: str) -> str:
    """Resolve an executable, honoring the Windows `.cmd` shim."""
    if not IS_WINDOWS or os.path.splitext(name)[1]:
        return shutil.which(name) or name
    return shutil.which(f"{name}.cmd") or shutil.which(name) or name


def script_ext() -> str:
    return "ps1" if IS_WINDOWS else "sh"


def make_executable(path: Path) -> None:
    """chmod +x on POSIX; no-op on Windows."""
    if IS_WINDOWS:
        return
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | 0o111)
    except OSError:
        pass


def detached_popen_kwargs() -> dict:
    """Kwargs to spawn a fully detached background worker, per OS."""
    if IS_WINDOWS:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        return {
            "startupinfo": startupinfo,
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            ),
        }
    # POSIX: new session detaches from the controlling terminal/parent group.
    return {"start_new_session": True}


def hidden_run_kwargs() -> dict:
    """Kwargs to run a foreground subprocess without flashing a console (Windows).

    On Windows the child is also placed in its own process group so the whole tree
    can be terminated later — `opencode` is a `.cmd` shim that spawns node, and
    killing only the shim leaves the node process orphaned (RAM leak per timeout).
    """
    if IS_WINDOWS:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {
            "startupinfo": startupinfo,
            "creationflags": subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP,
        }
    # POSIX: own process group so killpg reaches grandchildren too.
    return {"start_new_session": True}


def process_tree(pid: int | None) -> list[dict]:
    """Descendants of `pid` as [{'pid': int, 'name': str}]. Diagnostics only.

    Windows: CIM query. POSIX: `ps`. Returns [] when the platform tool is missing
    or errors — callers treat an empty list as "unknown", never as "no children".
    """
    if not pid or pid <= 0:
        return []
    try:
        if IS_WINDOWS:
            script = (
                "$ErrorActionPreference='SilentlyContinue';"
                "Get-CimInstance Win32_Process |"
                " Select-Object ProcessId,ParentProcessId,Name |"
                " ConvertTo-Json -Compress"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                **hidden_run_kwargs(),
            ).stdout
            import json as _json

            rows = _json.loads(out or "[]")
            if isinstance(rows, dict):
                rows = [rows]
            children: dict[int, list[int]] = {}
            names: dict[int, str] = {}
            for row in rows:
                child = int(row.get("ProcessId") or 0)
                parent = int(row.get("ParentProcessId") or 0)
                if not child:
                    continue
                names[child] = str(row.get("Name") or "")
                children.setdefault(parent, []).append(child)
        else:
            out = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,comm="],
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout
            children = {}
            names = {}
            for line in (out or "").splitlines():
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                child, parent = int(parts[0]), int(parts[1])
                names[child] = parts[2] if len(parts) > 2 else ""
                children.setdefault(parent, []).append(child)
    except Exception:
        return []

    found: list[dict] = []
    stack = [pid]
    seen = {pid}
    while stack:
        for child in children.get(stack.pop(), []):
            if child in seen:
                continue
            seen.add(child)
            found.append({"pid": child, "name": names.get(child, "")})
            stack.append(child)
    return found


def terminate_tree(proc: "subprocess.Popen | None", pid: int | None = None) -> dict:
    """Kill a process AND every descendant. Returns {'method', 'ok'}.

    Plain `proc.kill()` is not enough: on Windows it terminates the `.cmd` shim
    while node keeps running; on POSIX it misses grandchildren. Best-effort —
    a failure here must never mask the original error the caller is handling.
    """
    target = pid if pid is not None else (proc.pid if proc else None)
    if not target:
        return {"method": "none", "ok": False}

    method, ok = "none", False
    try:
        if IS_WINDOWS:
            method = "taskkill"
            ok = (
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(target)],
                    capture_output=True,
                    timeout=20,
                    **hidden_run_kwargs(),
                ).returncode
                == 0
            )
        else:
            import signal

            method = "killpg"
            try:
                os.killpg(os.getpgid(target), signal.SIGKILL)
                ok = True
            except (ProcessLookupError, PermissionError, OSError):
                os.kill(target, signal.SIGKILL)
                ok = True
    except Exception:
        ok = False

    if proc is not None:
        try:  # reap the direct child so it does not linger as a zombie
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    return {"method": method, "ok": ok}


def process_alive(pid: int | None) -> bool:
    """True if `pid` is a live process. Cross-OS, stdlib only.

    POSIX: signal 0 probe. Windows: OpenProcess via ctypes.
    Conservative: unknown/None -> False (treat as dead so the reaper can act).
    """
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        return _win_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _win_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
