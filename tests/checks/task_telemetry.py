"""Tasks are sequences of skill calls, closed by a verify the runtime derives as pass (DEC-015,
DEC-020); a commit joins the task it belongs to and closes nothing."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from core.audit import task_telemetry
from core.audit.task_telemetry import derive_tasks

from tests.checks.support import assert_true

HOOKS = Path(__file__).resolve().parents[2] / "dist" / "config" / "claude" / "hooks"


def _skill(sid, skill, verdict=None, derived=None):
    return {"kind": "skill", "session_id": sid, "skill": skill, "verdict": verdict, "derived": derived, "at": "t"}


def _edit(sid, path):
    return {"kind": "edit", "session_id": sid, "path": path, "at": "t"}


def _commit(sid, commit, *paths):
    return {"kind": "commit", "session_id": sid, "commit": commit, "paths": list(paths), "at": "t"}


def _check_states() -> None:
    passed = _skill("s", "verify", "DONE", "pass")
    cases = {
        "completed": [_skill("s", "plan"), _edit("s", "a.py"), passed],
        "completed without a commit": [_edit("s", "a.py"), passed],
        "DONE derived incomplete": [_edit("s", "a.py"), _skill("s", "verify", "DONE", "incomplete")],
        "NEEDS FIX": [_edit("s", "a.py"), _skill("s", "verify", "NEEDS FIX", "fail")],
        "committed, never verified": [_edit("s", "a.py"), _commit("s", "c1", "a.py")],
        "DONE before the derived verdict": [_edit("s", "a.py"), _skill("s", "verify", "DONE")],
        "read_only": [_skill("s", "analyze")],
        "read-only pass": [_skill("s", "verify", "DONE", "pass")],
        "unknown": [_edit(None, "a.py")],
    }
    expected = {
        "completed without a commit": "completed",
        "DONE derived incomplete": "open",
        "NEEDS FIX": "open",
        "committed, never verified": "open",
        "DONE before the derived verdict": "open",
        "read-only pass": "read_only",
    }
    for name, events in cases.items():
        tasks, _ = derive_tasks(events)
        want = expected.get(name, name)
        assert_true(len(tasks) == 1 and tasks[0]["state"] == want, f"[{name}] expected {want}: {tasks}")

    # The commit recommended after a pass joins the task it closed; it closes nothing itself.
    tasks, unclaimed = derive_tasks([_skill("s", "plan"), _edit("s", "a.py"), passed, _commit("s", "c1", "a.py")])
    assert_true(
        len(tasks) == 1 and tasks[0]["state"] == "completed" and unclaimed == 0
        and tasks[0]["sequence"] == ["plan", "edit", "verify", "commit"] and tasks[0]["commit"] == "c1",
        f"a task keeps its ordered sequence, edits collapsed, the commit after its pass included: {tasks}",
    )

    # An edit after a pass is the next task; an unrelated commit is unclaimed; the same
    # commit seen twice counts once; sessions never share a task.
    tasks, unclaimed = derive_tasks([
        _edit("s", "a.py"), _commit("s", "c0", "other.py"), passed,
        _commit("s", "c1", "a.py"), _commit("s", "c1", "a.py"),
        _edit("s", "b.py"), _edit("t", "b.py"),
    ])
    assert_true(
        [t["state"] for t in tasks] == ["completed", "open", "open"] and unclaimed == 1,
        f"tasks split at a pass, commits attributed by edited path, per session, once per hash: {[t['state'] for t in tasks]} unclaimed={unclaimed}",
    )

    # A commit matching both the open task and the one just completed joins the open one;
    # a commit without a session is never claimed.
    tasks, unclaimed = derive_tasks([
        _edit("s", "a.py"), passed, _edit("s", "a.py"), _commit("s", "c1", "a.py"), _commit(None, "c2", "a.py"),
    ])
    assert_true(
        [t["state"] for t in tasks] == ["completed", "open"] and tasks[0]["commit"] is None
        and tasks[1]["commit"] == "c1" and unclaimed == 1,
        f"the open task claims a shared commit first; a sessionless commit is unclaimed: {tasks} unclaimed={unclaimed}",
    )


def _check_runtime_event() -> None:
    root = Path(tempfile.mkdtemp(prefix="task-events-"))
    try:
        (root / ".workflow" / "data").mkdir(parents=True)
        task_telemetry.record_skill(root, "s1", "verify", verdict="DONE", derived="incomplete", next_action="/.commit", prompt_id="p1")
        (root / ".workflow" / "data" / "tasks.jsonl").open("a", encoding="utf-8").write("{torn\n")
        events = task_telemetry.load_events(root)
        assert_true(
            len(events) == 1 and events[0]["source"] == "runtime" and events[0]["verdict"] == "DONE"
            and events[0]["derived"] == "incomplete",
            f"the runtime writes one event per command with both verdicts; a torn line is skipped: {events}",
        )
        summary = task_telemetry.report(root)
        assert_true(summary["by_state"] == {"read_only": 1}, f"report reads the stream: {summary}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _embedded_python() -> str:
    text = (HOOKS / "task-events.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def _runners() -> list[tuple[str, list[str]]]:
    runners = [("sh:python", [sys.executable, "-c", _embedded_python()])]
    shell = shutil.which("pwsh") or (shutil.which("powershell") if sys.platform == "win32" else None)
    if shell:
        runners.append(("ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOKS / "task-events.ps1")]))
    bash = shutil.which("bash") if sys.platform != "win32" else None
    if bash:
        runners.append(("sh", [bash, str(HOOKS / "task-events.sh")]))
    return runners


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _check_hook_flavours() -> None:
    """Both flavours turn the same tool calls into the same events."""
    base = Path(tempfile.mkdtemp(prefix="task-hook-"))
    try:
        home = base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "session_registry.json").write_text(
            json.dumps({"claude-1": {"main_session_id": "main_x"}}), encoding="utf-8"
        )
        skills = home / ".claude" / "skills"
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        results = {}
        for name, argv in _runners():
            project = base / name.replace(":", "-")
            (project / ".workflow" / "data").mkdir(parents=True)
            (project / "src").mkdir()
            _git(project, "init", "-q")
            _git(project, "config", "user.email", "t@example.test")
            _git(project, "config", "user.name", "t")
            (project / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
            _git(project, "add", ".")
            _git(project, "commit", "-q", "-m", "a")
            def commit_b():
                (project / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
                _git(project, "commit", "-q", "-am", "b")

            commit = {"command": "git commit -m b"}
            # (event, tool, input, tool_use_id, what the command itself does in between)
            calls = [
                ("PostToolUse", "Read", {"file_path": str(skills / "execute.md")}, "u0", None),
                ("PostToolUse", "Read", {"file_path": str(skills / "verify.md")}, "u0", None),
                ("PostToolUse", "Read", {"file_path": str(project / "src" / "a.py")}, "u0", None),
                ("PostToolUse", "Skill", {"skill": ".commit"}, "u0", None),
                ("PostToolUse", "Skill", {"skill": "caveman:caveman"}, "u0", None),
                ("PostToolUse", "Edit", {"file_path": str(project / "src" / "a.py")}, "u0", None),
                ("PostToolUse", "Write", {"file_path": str(project / ".workflow" / "config.json")}, "u0", None),
                ("PostToolUse", "Write", {"file_path": str(base / "outside.py")}, "u0", None),
                ("PostToolUse", "Bash", {"command": "git status"}, "u0", None),
                # A commit that moves HEAD is recorded.
                ("PreToolUse", "Bash", commit, "u1", commit_b),
                ("PostToolUse", "Bash", commit, "u1", None),
                # A commit that fails leaves HEAD where it was: nothing, though the last
                # commit is seconds old.
                ("PreToolUse", "Bash", commit, "u2", None),
                ("PostToolUse", "Bash", commit, "u2", None),
                # No saved HEAD: nothing to compare against, so nothing is claimed.
                ("PostToolUse", "Bash", commit, "u3", None),
            ]
            for event, tool, tool_input, use_id, between in calls:
                payload = {
                    "session_id": "claude-1", "cwd": str(project), "hook_event_name": event,
                    "tool_name": tool, "tool_input": tool_input, "tool_use_id": use_id,
                }
                raw = json.dumps(payload)
                # The .sh wrapper hands its stdin to the embedded python as CLAUDE_HOOK_RAW.
                subprocess.run(argv, input=raw, text=True, env={**env, "CLAUDE_HOOK_RAW": raw}, capture_output=True, timeout=60)
                if between:
                    between()
            stream = project / ".workflow" / "data" / "tasks.jsonl"
            rows = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()] if stream.exists() else []
            for row in rows:
                row.pop("at", None)
                if row.get("kind") == "commit":
                    row["commit"] = len(row["commit"])
            results[name] = rows
        expected = [
            {"v": 1, "source": "hook", "session_id": "main_x", "kind": "skill", "skill": "execute"},
            {"v": 1, "source": "hook", "session_id": "main_x", "kind": "skill", "skill": "commit"},
            {"v": 1, "source": "hook", "session_id": "main_x", "kind": "edit", "path": "src/a.py"},
            {"v": 1, "source": "hook", "session_id": "main_x", "kind": "commit", "commit": 40, "paths": ["src/a.py"]},
        ]
        for name, rows in results.items():
            assert_true(rows == expected, f"[{name}] hook events:\n{rows}\nexpected:\n{expected}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _test_task_telemetry() -> None:
    _check_states()
    _check_runtime_event()
    _check_hook_flavours()
