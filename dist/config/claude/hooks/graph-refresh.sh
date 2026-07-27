#!/usr/bin/env bash
# graph-refresh.sh - Stop hook (POSIX parity of graph-refresh.ps1).
# Regenerates graphify-out/ after main_agent actually changed code. Two gates:
#   1. Did this turn implement? ([EXECUTION RESULT] / [REFACTOR RESULT] in last message)
#   2. Is the graph older than the sources? (mtime compare)
# Both must pass. Runs `graphify update` only (never init/build/watch), bounded 45s.
# Never blocks the response (always exit 0).
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import os, sys, json, shutil, subprocess

SOURCE_EXT = (
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".php", ".go", ".rs", ".java", ".rb",
)
SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", "vendor",
    "dist", "build", ".next", "coverage", "graphify-out", ".workflow",
}

try:
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

    # Gate 2: is the graph behind the sources?
    graph_time = os.path.getmtime(graph_path)
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(SOURCE_EXT):
                try:
                    mt = os.path.getmtime(os.path.join(dirpath, fn))
                    if mt > newest:
                        newest = mt
                except Exception:
                    pass
    if newest <= graph_time:
        sys.exit(0)  # graph is current

    graphify = shutil.which("graphify")
    if not graphify:
        sys.exit(0)

    # `update` only. Bounded 45s so a hung graphify does not outlive the hook. A graph too
    # large to index is a known outcome -- not retried, not reported.
    try:
        subprocess.run(
            [graphify, "update"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
        )
    except Exception:
        pass
    sys.exit(0)
except SystemExit:
    raise
except Exception:
    sys.exit(0)
PY
exit 0
