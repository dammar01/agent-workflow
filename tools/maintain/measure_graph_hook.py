#!/usr/bin/env python3
"""Measure what the graph-refresh Stop hook costs on one project (RQ-13, CASE-008).

The graph-refresh hook runs at the end of every implementing turn: it looks for the newest
source file outside the dependency and build directories, and runs `graphify update` when
the graph is older. Its cost was a perception until this measured it. CASE-008 was counted
with it, and it is kept so the figures can be re-counted on any project:

  1. project size: files inside the hook's skip directories vs source files outside them
  2. the v3.7.3 PowerShell scan (Get-ChildItem -Recurse, filtered afterwards)
  3. a pruned scan that never enters a skip directory, the change DEC-012 proposes
  4. optionally the installed hook end to end, and `graphify update` alone

Both scans are timed inside PowerShell, because that is what the hook runs; Python only
starts them. Without PowerShell (the POSIX hook already prunes) they are reported as null.

Read-only unless --run-hook or --run-graphify is given: both may regenerate graphify-out/.
Prints one JSON object of counts and timings, nothing from the project's files. Not part of
the test suite.

  python tools/maintain/measure_graph_hook.py --project <root>
  python tools/maintain/measure_graph_hook.py --project <root> --run-hook --run-graphify
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

# The same lists as dist/config/claude/hooks/graph-refresh.ps1 (v3.7.3).
SOURCE_EXTENSIONS = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".php", ".go", ".rs", ".java", ".rb")
SKIP_DIRS = ("node_modules", ".git", ".venv", "venv", "__pycache__", "vendor", "dist", "build", ".next", "coverage", "graphify-out", ".workflow")

_PS_INCLUDE = ", ".join(f"'*{ext}'" for ext in SOURCE_EXTENSIONS)
_PS_SKIP_PATTERN = r"[\\/](" + "|".join(name.replace(".", r"\.") for name in SKIP_DIRS) + r")[\\/]"
_PS_SKIP_NAMES = ", ".join(f"'{name}'" for name in SKIP_DIRS)

# The hook's scan as v3.7.3 runs it: walk everything, drop skip directories afterwards.
CURRENT_SCAN = f"""
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$null = Get-ChildItem -LiteralPath $env:MEASURE_ROOT -Recurse -File -Include @({_PS_INCLUDE}) -ErrorAction SilentlyContinue |
    Where-Object {{ $_.FullName -notmatch '{_PS_SKIP_PATTERN}' }} |
    Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
$sw.Stop()
[math]::Round($sw.Elapsed.TotalMilliseconds)
"""

# The scan DEC-012 proposes: an explicit stack that never enters a skip directory.
PRUNED_SCAN = f"""
$skip = @({_PS_SKIP_NAMES})
$ext = @{{}}; foreach ($e in @({_PS_INCLUDE})) {{ $ext[$e.TrimStart('*')] = $true }}
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$visited = 0; $newest = $null
$stack = [System.Collections.Generic.Stack[string]]::new(); $stack.Push($env:MEASURE_ROOT)
while ($stack.Count -gt 0) {{
    $dir = $stack.Pop()
    try {{ $entries = [System.IO.Directory]::EnumerateFileSystemEntries($dir) }} catch {{ continue }}
    foreach ($entry in $entries) {{
        $name = [System.IO.Path]::GetFileName($entry)
        if ([System.IO.Directory]::Exists($entry)) {{ if ($skip -notcontains $name) {{ $stack.Push($entry) }}; continue }}
        if (-not $ext.ContainsKey([System.IO.Path]::GetExtension($name).ToLower())) {{ continue }}
        $visited++
        $t = [System.IO.File]::GetLastWriteTimeUtc($entry)
        if ($null -eq $newest -or $t -gt $newest) {{ $newest = $t }}
    }}
}}
$sw.Stop()
"$([math]::Round($sw.Elapsed.TotalMilliseconds)) $visited"
"""


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _run_ps(shell: str, script: str, root: Path) -> str:
    done = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, env={**os.environ, "MEASURE_ROOT": str(root)}, check=True,
    )
    return done.stdout.strip().splitlines()[-1]


def project_size(root: Path) -> dict:
    """Files inside skip directories (by directory) and source files outside them."""
    in_skip: dict[str, int] = {}
    total = source = 0
    for current, dirs, files in os.walk(root):
        parts = Path(current).relative_to(root).parts
        hit = next((part for part in parts if part in SKIP_DIRS), None)
        total += len(files)
        if hit:
            in_skip[hit] = in_skip.get(hit, 0) + len(files)
        else:
            source += sum(1 for name in files if os.path.splitext(name)[1].lower() in SOURCE_EXTENSIONS)
    return {
        "files_total": total,
        "files_in_skip_dirs": sum(in_skip.values()),
        "files_in_skip_dirs_by_dir": dict(sorted(in_skip.items(), key=lambda item: -item[1])),
        "source_files_outside_skip": source,
    }


def _mtime(path: Path) -> float | None:
    return path.stat().st_mtime if path.is_file() else None


def default_hook() -> Path:
    name = "graph-refresh.ps1" if os.name == "nt" else "graph-refresh.sh"
    return Path.home() / ".claude" / "hooks" / name


def measure(root: Path, *, hook: Path, repeat: int, run_hook: bool, run_graphify: bool) -> dict:
    graph = root / "graphify-out" / "graph.json"
    result: dict = {
        "measured_on": _dt.date.today().isoformat(),
        "hook_measured": "v3.7.3 graph-refresh.ps1",
        "graph_exists": graph.is_file(),
        **project_size(root),
    }
    shell = _powershell()
    if shell:
        current = [int(float(_run_ps(shell, CURRENT_SCAN, root))) for _ in range(repeat)]
        pruned_runs = [_run_ps(shell, PRUNED_SCAN, root).split() for _ in range(repeat)]
        pruned = [int(float(ms)) for ms, _ in pruned_runs]
        result.update(
            scan_current_ms=current,
            scan_current_median_ms=statistics.median_low(current),
            scan_pruned_ms=pruned,
            scan_pruned_median_ms=statistics.median_low(pruned),
            scan_pruned_files_visited=int(pruned_runs[-1][1]),
        )
    else:
        result.update(scan_current_ms=None, scan_pruned_ms=None, scan_note="no PowerShell: the scans measure the Windows hook")

    if run_hook:
        if not hook.is_file():
            raise SystemExit(f"hook not found: {hook}")
        command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(hook)] if hook.suffix == ".ps1" else ["bash", str(hook)]
        payload = json.dumps({"last_assistant_message": "[EXECUTION RESULT] measurement", "cwd": str(root)})
        before = _mtime(graph)
        started = time.perf_counter()
        subprocess.run(command, input=payload, capture_output=True, text=True)
        result["hook_total_ms"] = round((time.perf_counter() - started) * 1000)
        result["hook_refreshed_graph"] = _mtime(graph) not in (None, before)

    if run_graphify:
        graphify = shutil.which("graphify")
        if graphify is None:
            result["graphify_update_ms"] = None
        else:
            before = _mtime(graph)
            started = time.perf_counter()
            done = subprocess.run([graphify, "update"], cwd=root, capture_output=True, text=True, errors="replace")
            result["graphify_update_ms"] = round((time.perf_counter() - started) * 1000)
            # graphify reports on stderr and exits 1 when only its HTML view fails; whether
            # graph.json was rewritten is recorded beside the exit code (CASE-008).
            result["graphify_exit_code"] = done.returncode
            lines = [line for line in (done.stdout + "\n" + done.stderr).splitlines() if line.strip()]
            result["graphify_last_line"] = lines[-1] if lines else None
            result["graphify_rewrote_graph"] = _mtime(graph) not in (None, before)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="project root to measure")
    parser.add_argument("--hook", type=Path, default=default_hook(), help="installed graph-refresh hook")
    parser.add_argument("--repeat", type=int, default=3, help="runs per scan (median reported)")
    parser.add_argument("--run-hook", action="store_true", help="also run the installed hook end to end")
    parser.add_argument("--run-graphify", action="store_true", help="also time `graphify update` alone")
    args = parser.parse_args(argv)
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    result = measure(root, hook=args.hook, repeat=max(1, args.repeat), run_hook=args.run_hook, run_graphify=args.run_graphify)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
