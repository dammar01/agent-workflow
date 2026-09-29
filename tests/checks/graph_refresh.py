"""The graph-refresh Stop hook returns before graphify finishes, and a reader waits it out.

DEC-012 after CASE-008: on a large graph `graphify update` outlived the hook's 45 s wait,
was killed before writing, and the turn paid 47 s for a graph that stayed stale. The hook
now prunes skip directories before walking, starts a detached worker under
`graphify-out/.refresh.lock`, and returns. These checks hold it to that with a fake
graphify that sleeps, rewrites graph.json, and exits 1 as the real one does on a large
graph.

Runners follow tests/checks/statusline.py: the `.ps1` through PowerShell when present, the
`.sh` flavour's embedded python everywhere, and the real bash wrapper on POSIX only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from core.graph import graph_index
from utils.osutil import hidden_run_kwargs
from tests.checks.support import assert_true

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "dist" / "config" / "claude" / "hooks"
_FAKE_SLEEP_S = 3

_FAKE_GRAPHIFY = """
import os, sys, time
time.sleep(float(os.environ.get("FAKE_GRAPHIFY_SLEEP", "0")))
with open(os.path.join("graphify-out", "graph.json"), "w", encoding="utf-8") as fh:
    fh.write('{"nodes": [], "links": [], "refreshed": true}')
sys.exit(1)
"""


def _embedded_python() -> str:
    text = (HOOKS / "graph-refresh.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def _runners() -> list[tuple[str, list[str]]]:
    runners: list[tuple[str, list[str]]] = [("sh:python", [sys.executable, "-c", _embedded_python()])]
    shell = shutil.which("pwsh") or (shutil.which("powershell") if sys.platform == "win32" else None)
    if shell:
        runners.append(("ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOKS / "graph-refresh.ps1")]))
    bash = shutil.which("bash") if sys.platform != "win32" else None
    if bash:
        runners.append(("sh", [bash, str(HOOKS / "graph-refresh.sh")]))
    return runners


def _fake_bin(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "fake_graphify.py").write_text(_FAKE_GRAPHIFY, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "graphify.cmd").write_text(
            f'@"{sys.executable}" "%~dp0fake_graphify.py" %*\r\n', encoding="utf-8"
        )
    else:
        script = bin_dir / "graphify"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_graphify.py" "$@"\n', encoding="utf-8")
        script.chmod(0o755)
    return bin_dir


def _project(root: Path, *, stale: bool) -> Path:
    project = root / "project"
    (project / "src").mkdir(parents=True)
    (project / "graphify-out").mkdir()
    (project / ".workflow" / "data").mkdir(parents=True)
    (project / "node_modules" / "dep").mkdir(parents=True)
    graph = project / "graphify-out" / "graph.json"
    graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    source = project / "src" / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    dependency = project / "node_modules" / "dep" / "index.js"
    dependency.write_text("module.exports = 1\n", encoding="utf-8")
    now = time.time()
    os.utime(graph, (now - 100, now - 100))
    os.utime(source, (now - (50 if stale else 200),) * 2)
    # Newer than the graph on purpose: a walk that entered node_modules would call the
    # graph stale here. Only pruning before descent keeps this project fresh.
    os.utime(dependency, (now,) * 2)
    return project


def _run_hook(command: list[str], project: Path, bin_dir: Path, message: str = "[EXECUTION RESULT] done") -> float:
    payload = json.dumps({"last_assistant_message": message, "cwd": str(project)})
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
           "CLAUDE_HOOK_RAW": payload, "FAKE_GRAPHIFY_SLEEP": str(_FAKE_SLEEP_S)}
    started = time.monotonic()
    # Hidden: a PowerShell started from a console-less test runner opens a window of its own.
    subprocess.run(command, input=payload, capture_output=True, text=True, env=env, timeout=60, **hidden_run_kwargs())
    return time.monotonic() - started


def _rows(project: Path) -> list[dict]:
    path = project / ".workflow" / "data" / "quality.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_for_release(project: Path, limit: float = 60.0) -> bool:
    lock = project / "graphify-out" / ".refresh.lock"
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if not lock.exists():
            return True
        time.sleep(0.2)
    return False


def _write_lock(project: Path, pid: int, started: float) -> None:
    (project / "graphify-out" / ".refresh.lock").write_text(
        json.dumps({"pid": pid, "started": int(started)}), encoding="utf-8"
    )


def _check_runner(flavour: str, command: list[str]) -> None:
    root = Path(tempfile.mkdtemp(prefix=f"graph-refresh-{flavour.replace(':', '-')}-"))
    try:
        bin_dir = _fake_bin(root)

        # Gate 1: a turn that implemented nothing touches nothing.
        project = _project(root / "gate", stale=True)
        _run_hook(command, project, bin_dir, message="just an answer")
        assert_true(
            not _rows(project) and not (project / "graphify-out" / ".refresh.lock").exists(),
            f"[{flavour}] a turn without [EXECUTION RESULT] must not scan, lock, or record",
        )

        # Fresh: a newer file under node_modules/ does not make the graph stale.
        project = _project(root / "fresh", stale=False)
        _run_hook(command, project, bin_dir)
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["skipped_fresh"] and rows[0].get("files_visited") == 1,
            f"[{flavour}] skip directories are pruned before the walk, so only src/app.py is "
            f"visited and the graph is fresh; got {rows}",
        )

        # Stale: the hook returns before graphify finishes; the worker refreshes and records.
        project = _project(root / "stale", stale=True)
        elapsed = _run_hook(command, project, bin_dir)
        locked = (project / "graphify-out" / ".refresh.lock").exists()
        assert_true(
            elapsed < _FAKE_SLEEP_S and locked,
            f"[{flavour}] the hook must return while graphify still runs ({elapsed:.1f}s against "
            f"a {_FAKE_SLEEP_S}s graphify) and leave the refresh lock held; locked={locked}",
        )
        assert_true(
            graph_index.load_graph(project) is None,
            f"[{flavour}] a reader must not parse graph.json while the refresh lock is held: "
            "graphify rewrites it in place",
        )
        assert_true(_wait_for_release(project), f"[{flavour}] the worker must remove its lock when done")
        rows = _rows(project)
        assert_true(
            len(rows) == 1 and rows[0].get("outcome") == "refreshed"
            and rows[0].get("graphify_exit") == 1 and rows[0].get("graph_rewritten") is True,
            f"[{flavour}] a rewritten graph is a refresh even when graphify exits 1 (CASE-008); got {rows}",
        )
        assert_true(
            (graph_index.load_graph(project) or {}).get("refreshed") is True,
            f"[{flavour}] once the lock is gone the refreshed graph is readable",
        )

        # A live lock from an earlier turn: no second graphify.
        project = _project(root / "running", stale=True)
        _write_lock(project, os.getpid(), time.time())
        _run_hook(command, project, bin_dir)
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["skipped_running"],
            f"[{flavour}] a refresh already running is not started twice; got {rows}",
        )

        # A lock caught half written is held while young: failing open would read the graph
        # exactly while its owner is replacing the lock.
        project = _project(root / "torn", stale=True)
        (project / "graphify-out" / ".refresh.lock").write_text('{"pid": ', encoding="utf-8")
        assert_true(
            graph_index.load_graph(project) is None,
            f"[{flavour}] an unreadable young lock keeps the graph unavailable",
        )
        _run_hook(command, project, bin_dir)
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["skipped_running"],
            f"[{flavour}] an unreadable young lock is not refreshed over; got {rows}",
        )

        # No .workflow/data: the refresh still runs, it just records nothing.
        project = _project(root / "nodata", stale=True)
        shutil.rmtree(project / ".workflow")
        _run_hook(command, project, bin_dir)
        assert_true(_wait_for_release(project), f"[{flavour}] the worker finishes without a data dir")
        assert_true(
            (graph_index.load_graph(project) or {}).get("refreshed") is True
            and not (project / ".workflow").exists(),
            f"[{flavour}] a project without .workflow/data is refreshed and gets no stream",
        )

        # A lock whose holder is long gone is reclaimed.
        project = _project(root / "dead", stale=True)
        _write_lock(project, os.getpid(), time.time() - 3600)
        _run_hook(command, project, bin_dir)
        assert_true(_wait_for_release(project), f"[{flavour}] the reclaiming worker removes its lock")
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["refreshed"],
            f"[{flavour}] a lock older than the limit holds nothing; got {rows}",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_graph_refresh_hook() -> None:
    for flavour, command in _runners():
        _check_runner(flavour, command)
