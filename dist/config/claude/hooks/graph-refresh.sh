#!/usr/bin/env bash
# graph-refresh.sh - Stop hook (POSIX parity of graph-refresh.ps1).
# Regenerates graphify-out/ after main_agent actually changed code. Two gates:
#   1. Did this turn implement? ([EXECUTION RESULT] / [REFACTOR RESULT] in last message)
#   2. Is the graph older than the sources? (mtime compare, skip dirs pruned before descent)
# Both must pass. The refresh is detached (DEC-012): the hook starts a worker that runs
# `graphify update` only (never init/build/watch), records one row in the quality stream,
# and removes graphify-out/.refresh.lock. Readers treat the graph as stale while the lock
# is held, because graphify rewrites graph.json in place.
# Never blocks the response (always exit 0).
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import os, sys, json, shutil, subprocess, time

SOURCE_EXT = (
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".php", ".go", ".rs", ".java", ".rb",
)
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", "vendor",
    "dist", "build", ".next", "coverage", "target", "graphify-out", ".workflow",
}
GRAPHIFY_LIMIT_S = 600
# How old a lock may be before a dead or reused pid no longer holds it. core.graph.graph_index
# reads the lock with the same age, and graph-refresh.ps1 uses the same numbers.
LOCK_MAX_AGE_S = 900
# The bound always ends this far before the lock expires: a graphify still running when its
# lock reads as stale would have a second one started beside it.
LOCK_MARGIN_S = 60


def limit_seconds(default=GRAPHIFY_LIMIT_S):
    # GRAPH_REFRESH_LIMIT_S overrides the bound (tests); a value that is not a positive
    # whole number keeps the default instead of failing the refresh, and a larger one is
    # clamped below the lock's age.
    try:
        value = int(str(os.environ.get("GRAPH_REFRESH_LIMIT_S", "")).strip())
    except ValueError:
        return default
    return min(value, LOCK_MAX_AGE_S - LOCK_MARGIN_S) if value > 0 else default

# The worker runs as its own python process, started in a new session so it outlives the
# hook, with no handle shared with it. argv: root hook_ms scan_ms visited skipped token
WORKER = r'''
import os, sys, json, shutil, subprocess, time
root, hook_ms, scan_ms, visited, skipped, token = sys.argv[1], *map(int, sys.argv[2:6]), sys.argv[6]
graph = os.path.join(root, "graphify-out", "graph.json")
lock = os.path.join(root, "graphify-out", ".refresh.lock")
hide = {}
if os.name == "nt":
    # This worker has no console; without SW_HIDE a console child (graphify.cmd) would
    # open a visible window of its own.
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    hide = {"startupinfo": info, "creationflags": 0x08000000}  # CREATE_NO_WINDOW
try:
    limit = int(str(os.environ.get("GRAPH_REFRESH_LIMIT_S", "")).strip())
except ValueError:
    limit = 0
limit = limit if limit > 0 else 600
outcome, exit_code, graphify_ms, rewritten, finished = "error", None, 0, False, False
try:
    before = os.path.getmtime(graph)
    started = time.monotonic()
    # Its own process group, so a timeout kills graphify and everything it started: a
    # wrapper (graphify.cmd, a venv shim) whose child survived would keep writing graph.json
    # after the lock that warns readers is gone.
    group = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen(
        [shutil.which("graphify") or "graphify", "update"], cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **group, **hide,
    )
    try:
        exit_code = proc.wait(timeout=limit)
        finished = True
    except subprocess.TimeoutExpired:
        outcome = "timeout"
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, **hide)
            else:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
    graphify_ms = int((time.monotonic() - started) * 1000)
    rewritten = os.path.getmtime(graph) != before
    # graphify exits 1 on a large graph when only its HTML view fails, having written
    # graph.json (CASE-008): a rewritten graph is the success signal, not the exit code.
    # Only from a graphify that finished: one killed at the bound may have left a graph
    # half written, and stays a timeout. A finished rewrite that does not parse is
    # "corrupt", never a refresh.
    if finished and rewritten:
        try:
            with open(graph, encoding="utf-8") as fh:
                json.load(fh)
            outcome = "refreshed"
        except Exception:
            outcome = "corrupt"
except Exception:
    pass
finally:
    data = os.path.join(root, ".workflow", "data")
    if os.path.isdir(data):
        row = {"kind": "graph_refresh", "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "hook": "sh", "outcome": outcome, "hook_ms": hook_ms, "scan_ms": scan_ms,
               "files_visited": visited, "dirs_skipped": skipped, "graphify_ms": graphify_ms,
               "graphify_exit": exit_code, "graph_rewritten": rewritten}
        try:
            with open(os.path.join(data, "quality.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            pass
    try:
        with open(lock, encoding="utf-8") as fh:
            owned = json.load(fh).get("token") == token
        if owned:
            os.remove(lock)
    except Exception:
        pass
'''


def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) sends CTRL_C_EVENT on Windows; ask the kernel instead.
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_lock(path):
    # The lock as written, or None when there is none (or it cannot be read).
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def lock_held(path):
    try:
        with open(path, encoding="utf-8") as fh:
            lock = json.load(fh)
        return time.time() - int(lock["started"]) < LOCK_MAX_AGE_S and pid_alive(int(lock["pid"]))
    except FileNotFoundError:
        return False
    except Exception:
        # Present but unreadable: held while young, like core.graph.graph_index reads it.
        try:
            return time.time() - os.path.getmtime(path) < LOCK_MAX_AGE_S
        except OSError:
            return False


def lock_token(path):
    # The token of the lock as written, or None when there is none (or it cannot be read).
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("token")
    except Exception:
        return None


def hand_off(lock_path, token, worker):
    # Replace the hook's lock with one naming the worker, but only while the lock is still
    # this hook's: a worker that already finished has removed it, and a lock written back
    # then would name a dead pid with no one left to remove it. Written beside the lock and
    # renamed over it, so a reader never sees an empty or half lock.
    staged = lock_path + "." + token
    try:
        with open(staged, "w", encoding="utf-8") as fh:
            json.dump({"pid": worker.pid, "token": token, "started": int(time.time())}, fh)
        if lock_token(lock_path) != token:
            return
        os.replace(staged, lock_path)
        # POSIX has no rename that requires its target to exist (graph-refresh.ps1 gets one
        # from File.Replace): a worker that finished between the check and the rename has
        # just had its lock written back, so take it out again.
        if worker.poll() is not None and lock_token(lock_path) == token:
            os.remove(lock_path)
    finally:
        if os.path.exists(staged):
            os.remove(staged)


def write_row(root, fields):
    # Fail-open: a workspace without .workflow/data gets no row.
    data = os.path.join(root, ".workflow", "data")
    if not os.path.isdir(data):
        return
    row = {"kind": "graph_refresh", "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "hook": "sh"}
    row.update(fields)
    try:
        with open(os.path.join(data, "quality.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


try:
    started = time.monotonic()
    hook_ms = lambda: int((time.monotonic() - started) * 1000)
    raw = os.environ.get("CLAUDE_HOOK_RAW", "")
    if not raw.strip():
        sys.exit(0)
    payload = json.loads(raw)
    message = str(payload.get("last_assistant_message") or "")
    cwd = payload.get("cwd") or os.getcwd()

    # Gate 1: did this turn implement anything?
    if "[EXECUTION RESULT]" not in message and "[REFACTOR RESULT]" not in message:
        sys.exit(0)

    try:
        root = os.path.realpath(cwd)
    except Exception:
        root = cwd

    graph_path = os.path.join(root, "graphify-out", "graph.json")
    if not os.path.isfile(graph_path):
        # No graph in this project. `graphify init` is never run automatically.
        sys.exit(0)

    lock_path = os.path.join(root, "graphify-out", ".refresh.lock")
    seen_lock = read_lock(lock_path)
    # A lock that changed since it was judged belongs to a hook that just took it: deleting
    # it as stale would start a second graphify beside the first. Re-read right before the
    # delete, and only the lock judged stale is removed.
    if lock_held(lock_path) or (seen_lock is not None and read_lock(lock_path) != seen_lock):
        write_row(root, {"outcome": "skipped_running", "hook_ms": hook_ms()})
        sys.exit(0)
    if seen_lock is not None:
        try:
            os.remove(lock_path)
        except OSError:
            pass

    # Gate 2: is the graph behind the sources?
    graph_time = os.path.getmtime(graph_path)
    scan_started = time.monotonic()
    newest, visited, skipped = 0.0, 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Case-insensitive, as the .ps1 flavour matches: Vendor/ is vendor/ on Windows and macOS.
        kept = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        skipped += len(dirnames) - len(kept)
        dirnames[:] = kept
        for fn in filenames:
            if fn.lower().endswith(SOURCE_EXT):
                visited += 1
                try:
                    mt = os.path.getmtime(os.path.join(dirpath, fn))
                    if mt > newest:
                        newest = mt
                except Exception:
                    pass
    scan_ms = int((time.monotonic() - scan_started) * 1000)
    scan = {"scan_ms": scan_ms, "files_visited": visited, "dirs_skipped": skipped}
    if newest <= graph_time:
        write_row(root, {"outcome": "skipped_fresh", "hook_ms": hook_ms(), **scan})
        sys.exit(0)  # graph is current

    if not shutil.which("graphify"):
        write_row(root, {"outcome": "no_graphify", "hook_ms": hook_ms(), **scan})
        sys.exit(0)

    # Exclusive create: of two hooks that found the graph stale, one starts the worker.
    # The token, not the pid, says who owns the lock: the pid Popen reports can belong to
    # a launcher in front of the interpreter that actually runs.
    token = os.urandom(8).hex()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps({"pid": os.getpid(), "token": token, "started": int(time.time())}).encode())
        os.close(fd)
    except OSError:
        sys.exit(0)
    flags = {}
    if os.name == "nt":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        flags["startupinfo"] = info
        flags["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        flags["start_new_session"] = True
    env = {**os.environ}
    env["GRAPH_REFRESH_LIMIT_S"] = str(limit_seconds())
    worker = subprocess.Popen(
        [sys.executable, "-c", WORKER, root, str(hook_ms()), str(scan_ms), str(visited), str(skipped), token],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, env=env, **flags,
    )
    # Hand the lock to the worker before this hook exits: a lock naming the hook's own pid
    # would read as abandoned the moment it returns, while graphify is still writing.
    try:
        hand_off(lock_path, token, worker)
    except Exception:
        pass
    sys.exit(0)
except SystemExit:
    raise
except Exception:
    sys.exit(0)
PY
exit 0
