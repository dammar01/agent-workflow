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
from tests.checks.support import assert_true, powershell_for_hooks

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "dist" / "config" / "claude" / "hooks"
_FAKE_SLEEP_S = 3

# FAKE_GRAPHIFY_MODE: "ok" sleeps then writes a graph; "corrupt" sleeps then writes half a
# graph; "early" writes a graph at once and then outlives the bound, as a graphify killed
# after its write would.
_FAKE_GRAPHIFY = """
import os, sys, time
mode = os.environ.get("FAKE_GRAPHIFY_MODE", "ok")
pause = float(os.environ.get("FAKE_GRAPHIFY_SLEEP", "0"))
if mode != "early":
    time.sleep(pause)
body = '{"nodes": [' if mode == "corrupt" else '{"nodes": [], "links": [], "refreshed": true}'
with open(os.path.join("graphify-out", "graph.json"), "w", encoding="utf-8") as fh:
    fh.write(body)
if mode == "early":
    time.sleep(pause)
    # Reached only by a graphify that outlived its kill: the wrapper died, this child did not.
    with open(os.path.join("graphify-out", "survived.txt"), "w", encoding="utf-8") as fh:
        fh.write("alive")
sys.exit(1)
"""


def _embedded_python() -> str:
    text = (HOOKS / "graph-refresh.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def _runners() -> list[tuple[str, list[str]]]:
    runners: list[tuple[str, list[str]]] = [("sh:python", [sys.executable, "-c", _embedded_python()])]
    shell = powershell_for_hooks()
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
    (project / "Target" / "debug").mkdir(parents=True)
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
    # A build directory in another case, with an extension in another case: skipped in
    # both flavours, as Windows and macOS treat the names as one.
    build_output = project / "Target" / "debug" / "Build.RS"
    build_output.write_text("fn main() {}", encoding="utf-8")
    os.utime(build_output, (now,) * 2)
    return project


def _run_hook(
    command: list[str], project: Path, bin_dir: Path, message: str = "[EXECUTION RESULT] done",
    extra_env: dict | None = None,
) -> float:
    payload = json.dumps({"last_assistant_message": message, "cwd": str(project)})
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
           "CLAUDE_HOOK_RAW": payload, "FAKE_GRAPHIFY_SLEEP": str(_FAKE_SLEEP_S)}
    env.pop("GRAPH_REFRESH_LIMIT_S", None)
    env.pop("FAKE_GRAPHIFY_MODE", None)
    env.update(extra_env or {})
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

        # Killed at the bound after it rewrote graph.json: a timeout, never a refresh.
        project = _project(root / "timeout", stale=True)
        _run_hook(command, project, bin_dir, extra_env={"FAKE_GRAPHIFY_MODE": "early", "GRAPH_REFRESH_LIMIT_S": "1"})
        assert_true(_wait_for_release(project), f"[{flavour}] the timed-out worker removes its lock")
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["timeout"] and rows[0].get("graph_rewritten") is True,
            f"[{flavour}] a graphify killed at the bound stays a timeout though graph.json moved; got {rows}",
        )
        # The kill takes the whole tree: graphify runs behind a wrapper (graphify.cmd, a shell
        # shim), and a surviving child would keep writing after the lock is gone.
        time.sleep(_FAKE_SLEEP_S + 2)
        assert_true(
            not (project / "graphify-out" / "survived.txt").exists(),
            f"[{flavour}] a timed-out graphify's child is killed with its wrapper",
        )

        # Finished, rewrote graph.json, and left it unparseable: corrupt, not refreshed.
        project = _project(root / "corrupt", stale=True)
        _run_hook(command, project, bin_dir, extra_env={"FAKE_GRAPHIFY_MODE": "corrupt", "FAKE_GRAPHIFY_SLEEP": "0"})
        assert_true(_wait_for_release(project), f"[{flavour}] the worker removes its lock after a corrupt write")
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["corrupt"] and rows[0].get("graph_rewritten") is True,
            f"[{flavour}] a rewritten graph.json that does not parse is recorded as corrupt; got {rows}",
        )

        # A bad GRAPH_REFRESH_LIMIT_S keeps the default bound instead of stopping the hook.
        project = _project(root / "badlimit", stale=True)
        _run_hook(command, project, bin_dir, extra_env={"GRAPH_REFRESH_LIMIT_S": "soon", "FAKE_GRAPHIFY_SLEEP": "0"})
        assert_true(_wait_for_release(project), f"[{flavour}] the worker runs under the default bound")
        rows = _rows(project)
        assert_true(
            [row.get("outcome") for row in rows] == ["refreshed"],
            f"[{flavour}] an unparseable GRAPH_REFRESH_LIMIT_S falls back to the default; got {rows}",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _check_call_during_refresh() -> None:
    """A delegated call during a refresh goes without leads, and says so (RQ-13).

    Never waits: a verify that ran while the worker held the lock must record
    `refreshing`, not block on it and not pass as a project with no graph.
    """
    from core.provider.executor import graph_leads_for_call, graph_state_for_call

    root = Path(tempfile.mkdtemp(prefix="graph-leads-call-"))
    try:
        cases = [
            ("live", lambda p: _write_lock(p, os.getpid(), time.time()), "refreshing"),
            ("torn", lambda p: (p / "graphify-out" / ".refresh.lock").write_text('{"pid": ', encoding="utf-8"), "refreshing"),
            ("expired", lambda p: _write_lock(p, os.getpid(), time.time() - 3600), "empty"),
            ("released", lambda p: None, "empty"),
            ("absent", lambda p: shutil.rmtree(p / "graphify-out"), "absent"),
        ]
        for name, arrange, expected in cases:
            project = _project(root / name, stale=False)
            arrange(project)
            started = time.monotonic()
            leads, status, ms = graph_leads_for_call(project, "app")
            waited = time.monotonic() - started
            assert_true(
                status == expected and leads is None,
                f"[{name}] graph status for a delegated call: expected {expected}, got {status}",
            )
            assert_true(
                waited < 1 and isinstance(ms, int) and ms >= 0,
                f"[{name}] the lookup must not wait on the refresh lock; took {waited:.2f}s",
            )
            # verify-browser takes no leads: same refresh judgement, no lookup.
            state, state_ms = graph_state_for_call(project)
            assert_true(
                state == {"empty": "available"}.get(expected, expected) and isinstance(state_ms, int),
                f"[{name}] graph state for a browser call: expected {expected}, got {state}",
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _sh_namespace() -> dict:
    """The `.sh` flavour's embedded python, loaded without running a hook (no payload)."""
    namespace: dict = {"__name__": "graph_refresh_sh"}
    saved = os.environ.pop("CLAUDE_HOOK_RAW", None)
    try:
        exec(_embedded_python(), namespace)
    except SystemExit:
        pass
    finally:
        if saved is not None:
            os.environ["CLAUDE_HOOK_RAW"] = saved
    return namespace


def _ps1_block(text: str, first: str) -> str:
    """The lines of graph-refresh.ps1 from the one starting with `first` to its closing `}`."""
    start = text.index(first)
    return text[start : text.index("\n}\n", start) + 3]


def _run_ps1_snippet(shell: str, root: Path, body: str, env: dict | None = None) -> str:
    script = root / f"snippet-{time.monotonic_ns()}.ps1"
    script.write_text(body, encoding="utf-8")
    done = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=60, env={**os.environ, **(env or {})}, **hidden_run_kwargs(),
    )
    return done.stdout.strip()


def _check_limit_is_clamped_below_the_lock() -> None:
    """GRAPH_REFRESH_LIMIT_S cannot outlive the lock: a graphify still running when its lock
    reads as stale would get a second one started beside it. Both flavours clamp to the
    lock's age minus the same margin, and the reader ages the lock the same way."""
    import re

    sh = (HOOKS / "graph-refresh.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    ps1 = (HOOKS / "graph-refresh.ps1").read_text(encoding="utf-8").replace("\r\n", "\n")
    sh_age = int(re.search(r"^LOCK_MAX_AGE_S = (\d+)$", sh, re.M).group(1))
    sh_margin = int(re.search(r"^LOCK_MARGIN_S = (\d+)$", sh, re.M).group(1))
    ps1_age = int(re.search(r"^\$LockMaxAgeSeconds = (\d+)$", ps1, re.M).group(1))
    ps1_margin = int(re.search(r"^\$LockMarginSeconds = (\d+)$", ps1, re.M).group(1))
    reader_age = graph_index._REFRESH_LOCK_MAX_AGE_SECONDS
    assert_true(
        sh_age == ps1_age == reader_age and sh_margin == ps1_margin and 0 < sh_margin < sh_age,
        f"lock age and margin agree across graph-refresh.sh ({sh_age}/{sh_margin}), graph-refresh.ps1 "
        f"({ps1_age}/{ps1_margin}) and core.graph.graph_index ({reader_age})",
    )
    ceiling = sh_age - sh_margin

    namespace = _sh_namespace()
    saved = os.environ.get("GRAPH_REFRESH_LIMIT_S")
    try:
        for given, expected in (("99999", ceiling), (str(ceiling + 1), ceiling), ("5", 5), ("0", 600), ("soon", 600)):
            os.environ["GRAPH_REFRESH_LIMIT_S"] = given
            got = namespace["limit_seconds"]()
            assert_true(got == expected, f"[sh] GRAPH_REFRESH_LIMIT_S={given} bounds graphify at {expected}s; got {got}")
    finally:
        if saved is None:
            os.environ.pop("GRAPH_REFRESH_LIMIT_S", None)
        else:
            os.environ["GRAPH_REFRESH_LIMIT_S"] = saved

    shell = powershell_for_hooks()
    if not shell:
        return
    block = _ps1_block(ps1, "$LockMaxAgeSeconds = ")
    root = Path(tempfile.mkdtemp(prefix="graph-refresh-limit-"))
    try:
        for given, expected in (("99999", ceiling), ("5", 5), ("soon", 600)):
            got = _run_ps1_snippet(shell, root, block + "$GraphifyLimitMs\n", {"GRAPH_REFRESH_LIMIT_S": given})
            assert_true(
                got == str(expected * 1000),
                f"[ps1] GRAPH_REFRESH_LIMIT_S={given} bounds graphify at {expected}s; got {got!r} ms",
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)


class _Worker:
    def __init__(self, pid: int, exit_code):
        self.pid, self._exit_code = pid, exit_code

    def poll(self):
        return self._exit_code


def _check_hand_off_only_while_the_lock_is_ours() -> None:
    """The hook hands its lock to the worker only while it still holds it. A worker that
    already finished has removed the lock; writing it back would leave an orphan naming a
    dead pid. Both flavours: missing stays missing, another hook's lock is not clobbered."""
    hand_off = _sh_namespace()["hand_off"]
    root = Path(tempfile.mkdtemp(prefix="graph-refresh-handoff-"))
    try:
        lock = root / ".refresh.lock"

        def leftovers() -> list[str]:
            return sorted(p.name for p in root.iterdir() if p.name.startswith(".refresh.lock."))

        hand_off(str(lock), "tok", _Worker(4242, None))
        assert_true(not lock.exists() and not leftovers(), "[sh] a lock the worker already removed is not recreated")

        foreign = json.dumps({"pid": 1, "token": "other", "started": 1})
        lock.write_text(foreign, encoding="utf-8")
        hand_off(str(lock), "tok", _Worker(4242, None))
        assert_true(lock.read_text(encoding="utf-8") == foreign and not leftovers(), "[sh] another hook's lock is left alone")

        lock.write_text(json.dumps({"pid": 7, "token": "tok", "started": 1}), encoding="utf-8")
        hand_off(str(lock), "tok", _Worker(4242, None))
        handed = json.loads(lock.read_text(encoding="utf-8"))
        assert_true(
            handed.get("pid") == 4242 and handed.get("token") == "tok" and not leftovers(),
            f"[sh] a lock still ours is handed to the running worker: {handed}",
        )

        lock.write_text(json.dumps({"pid": 7, "token": "tok", "started": 1}), encoding="utf-8")
        hand_off(str(lock), "tok", _Worker(4242, 0))
        assert_true(not lock.exists(), "[sh] a worker that finished during the hand-off leaves no lock behind")

        shell = powershell_for_hooks()
        if not shell:
            return
        ps1 = (HOOKS / "graph-refresh.ps1").read_text(encoding="utf-8").replace("\r\n", "\n")
        function = _ps1_block(ps1, "function Set-RefreshLock")
        call = f"try {{ Set-RefreshLock '{lock}' 4242 'tok' $false }} catch {{ }}\n"
        lock.unlink(missing_ok=True)
        _run_ps1_snippet(shell, root, function + call)
        assert_true(not lock.exists() and not leftovers(), "[ps1] a lock the worker already removed is not recreated")
        lock.write_text(foreign, encoding="utf-8")
        _run_ps1_snippet(shell, root, function + call)
        assert_true(lock.read_text(encoding="utf-8") == foreign and not leftovers(), "[ps1] another hook's lock is left alone")
        lock.write_text(json.dumps({"pid": 7, "token": "tok", "started": 1}), encoding="utf-8")
        _run_ps1_snippet(shell, root, function + call)
        handed = json.loads(lock.read_text(encoding="utf-8"))
        assert_true(
            handed.get("pid") == 4242 and handed.get("token") == "tok" and not leftovers(),
            f"[ps1] a lock still ours is handed to the worker: {handed}",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_graph_refresh_hook() -> None:
    _check_limit_is_clamped_below_the_lock()
    _check_hand_off_only_while_the_lock_is_ours()
    _check_call_during_refresh()
    for flavour, command in _runners():
        _check_runner(flavour, command)
