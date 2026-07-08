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
    """Kwargs to run a foreground subprocess without flashing a console (Windows)."""
    if IS_WINDOWS:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


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
