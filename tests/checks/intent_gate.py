"""The intent gate's SET side, run as shipped.

intent-gate-set read the prompt from `user_prompt`, a field Claude Code never sends (it
sends `prompt`), so every prompt classified as "no command" and the runtime gate never
armed. Pinned on the real scripts: PowerShell where it exists, the bash wrapper on POSIX, and
the `.sh` flavour's embedded python everywhere.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.checks.support import assert_true, powershell_for_hooks

HOOKS = Path(__file__).resolve().parents[2] / "dist" / "config" / "claude" / "hooks"


def _embedded_python() -> str:
    text = (HOOKS / "intent-gate-set.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def _runners() -> list[tuple[str, list[str]]]:
    runners = [("sh:python", [sys.executable, "-c", _embedded_python()])]
    shell = powershell_for_hooks()
    if shell:
        runners.append(("ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOKS / "intent-gate-set.ps1")]))
    bash = shutil.which("bash") if sys.platform != "win32" else None
    if bash:
        runners.append(("sh", [bash, str(HOOKS / "intent-gate-set.sh")]))
    return runners


def _test_intent_gate_reads_the_prompt_field() -> None:
    _check_prompt_field()
    _check_verify_lane()


def _check_prompt_field() -> None:
    for label, command in _runners():
        base = Path(tempfile.mkdtemp(prefix=f"gate-set-{label.replace(':', '-')}-"))
        try:
            home, project = base / "home", base / "project"
            (home / ".claude").mkdir(parents=True)
            (project / ".workflow" / "data").mkdir(parents=True)
            registry = {"c1": {"main_session_id": "m1", "cwd": str(project)}}
            (home / ".claude" / "session_registry.json").write_text(json.dumps(registry), encoding="utf-8")
            payload = json.dumps({
                "session_id": "c1", "cwd": str(project),
                "hook_event_name": "UserPromptSubmit", "prompt": "/.plan add a feature",
            })
            env = {
                **os.environ, "HOME": str(home), "USERPROFILE": str(home),
                # What the bash wrapper hands its embedded python.
                "CLAUDE_HOOK_RAW": payload, "HOOK_DIR": str(HOOKS),
            }
            subprocess.run(command, input=payload, text=True, env=env, capture_output=True, timeout=60)
            marker = project / ".workflow" / "data" / "sessions" / "m1" / "runtime" / "delegated.marker"
            assert_true(marker.is_file(), f"[{label}] a delegated prompt in Claude Code's `prompt` field arms the gate")
        finally:
            shutil.rmtree(base, ignore_errors=True)


def _check_script(name: str) -> str:
    text = (HOOKS / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def _check_runners() -> list[tuple[str, list[str]]]:
    runners = [("sh:python", [sys.executable, "-c", _check_script("intent-gate-check.sh")])]
    shell = powershell_for_hooks()
    if shell:
        runners.append(("ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOKS / "intent-gate-check.ps1")]))
    bash = shutil.which("bash") if sys.platform != "win32" else None
    if bash:
        runners.append(("sh", [bash, str(HOOKS / "intent-gate-check.sh")]))
    return runners


def _check_verify_lane() -> None:
    """A pending verify opens exactly what skills/verify.md needs before the run.

    The skill has main_agent pick tests from the diff and read the test allowlist before
    `.workflow/run verify`, while the gate blocks Read and Bash: without this lane the step
    the skill demands was the one the gate refused. The lane is the diff's file names, the
    config, and this session's tests.json; a pipe, a patch, or a source file stays blocked,
    and no other delegated command gets the lane.
    """
    for label, command in _check_runners():
        base = Path(tempfile.mkdtemp(prefix=f"gate-check-{label.replace(':', '-')}-"))
        try:
            home, project = base / "home", base / "project"
            (home / ".claude").mkdir(parents=True)
            session = project / ".workflow" / "data" / "sessions" / "m1"
            (session / "runtime").mkdir(parents=True)
            registry = {"c1": {"main_session_id": "m1", "cwd": str(project)}}
            (home / ".claude" / "session_registry.json").write_text(json.dumps(registry), encoding="utf-8")
            env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
            env.pop("WORKFLOW_LOCAL_MODE", None)
            tests_json = session / "verify" / "tests.json"
            config = project / ".workflow" / "config.json"
            other_tests = project / ".workflow" / "data" / "sessions" / "m2" / "verify" / "tests.json"

            def exit_code(tool: str, tool_input: dict) -> int:
                payload = json.dumps({
                    "session_id": "c1", "cwd": str(project), "hook_event_name": "PreToolUse",
                    "tool_name": tool, "tool_input": tool_input,
                })
                done = subprocess.run(
                    command, input=payload, text=True, env={**env, "CLAUDE_HOOK_RAW": payload},
                    capture_output=True, timeout=60,
                )
                return done.returncode

            def arm(intent: str) -> None:
                (session / "runtime" / "delegated.marker").write_text(json.dumps({"command": intent}), encoding="utf-8")

            arm("verify")
            allowed = [
                ("Bash", {"command": "git diff --name-only"}),
                ("Bash", {"command": "git diff --name-only HEAD"}),
                ("Bash", {"command": "git diff --stat HEAD~1 -- src"}),
                ("Bash", {"command": "git diff --cached --name-status"}),
                ("Bash", {"command": ".workflow/run.ps1 verify \"t\" \"m1\""}),
                ("Read", {"file_path": str(config)}),
                ("Read", {"file_path": ".workflow/config.json"}),
                ("Read", {"file_path": str(tests_json)}),
                ("Write", {"file_path": str(tests_json), "content": "{}"}),
            ]
            for tool, tool_input in allowed:
                code = exit_code(tool, tool_input)
                assert_true(code == 0, f"[{label}] verify lane must allow {tool} {tool_input}; exit {code}")
            blocked = [
                ("Bash", {"command": "git diff --name-only | cat"}),
                ("Bash", {"command": "git diff --name-only && cat src/app.py"}),
                ("Bash", {"command": "git diff --stat > out.txt"}),
                ("Bash", {"command": "git diff --stat -p"}),
                ("Bash", {"command": "git diff --name-only --output=out.txt"}),
                ("Bash", {"command": "git diff HEAD"}),
                ("Bash", {"command": "git show HEAD"}),
                ("Bash", {"command": "cat .workflow/config.json"}),
                ("Read", {"file_path": str(project / "src" / "app.py")}),
                ("Read", {"file_path": str(other_tests)}),
                ("Write", {"file_path": str(config), "content": "{}"}),
                ("Grep", {"pattern": "x"}),
            ]
            for tool, tool_input in blocked:
                code = exit_code(tool, tool_input)
                assert_true(code == 2, f"[{label}] verify lane must still block {tool} {tool_input}; exit {code}")

            # Another delegated intent gets no lane.
            arm("plan")
            for tool, tool_input in [("Bash", {"command": "git diff --name-only"}), ("Read", {"file_path": str(config)})]:
                code = exit_code(tool, tool_input)
                assert_true(code == 2, f"[{label}] a pending plan must block {tool} {tool_input}; exit {code}")
        finally:
            shutil.rmtree(base, ignore_errors=True)
