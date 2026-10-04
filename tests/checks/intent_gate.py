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
    _check_skill_read_lane()
    _check_precision_read_lane()


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
                ("PowerShell", {"command": "git diff --name-only HEAD"}),
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
                ("Grep", {"pattern": "x"}),
                ("PowerShell", {"command": "git diff --name-only | Out-String"}),
                ("PowerShell", {"command": "git diff HEAD"}),
            ]
            for tool, tool_input in blocked:
                code = exit_code(tool, tool_input)
                assert_true(code == 2, f"[{label}] verify lane must still block {tool} {tool_input}; exit {code}")

            # Another delegated intent gets no lane.
            arm("plan")
            for tool, tool_input in [("Bash", {"command": "git diff --name-only"}), ("Read", {"file_path": str(config)})]:
                code = exit_code(tool, tool_input)
                assert_true(code == 2, f"[{label}] a pending plan must block {tool} {tool_input}; exit {code}")

            # The runner passes only as the command itself, never as a word inside another one.
            runner_calls = [
                ".workflow/run.ps1 plan \"t\" \"m1\"",
                "./.workflow/run.sh plan \"t\" \"m1\"",
                f"\"{project.as_posix()}/.workflow/run.ps1\" plan \"t\" \"m1\"",
                "powershell -NoProfile -ExecutionPolicy Bypass -File .workflow/run.ps1 plan \"t\" \"m1\"",
                "bash .workflow/inspect.sh",
            ]
            for runner_call in runner_calls:
                code = exit_code("Bash", {"command": runner_call})
                assert_true(code == 0, f"[{label}] a runner call must pass the gate: {runner_call}; exit {code}")
            smuggled = [
                "python -c \"open('secret').read()\" /tmp/.workflow/run.sh",
                "cat src/app.py .workflow/run.ps1",
                "rg secret .workflow/check.sh",
                "powershell -Command Get-Content secret .workflow/run.ps1",
                ".workflow/run.ps1.bak plan",
            ]
            for command_line in smuggled:
                code = exit_code("Bash", {"command": command_line})
                assert_true(code == 2, f"[{label}] a runner path inside another command must block: {command_line}; exit {code}")
        finally:
            shutil.rmtree(base, ignore_errors=True)


def _check_skill_read_lane() -> None:
    """Under any pending marker a Read of a skill definition passes, and nothing near it.

    The Skill tool tells the agent to read ~/.claude/skills/<name>.md, while the prompt
    that invoked it already armed the gate: the instruction saying how to dispatch was
    blocked until after the dispatch. The lane is an existing .md whose real path lies in
    ~/.claude/skills; `..`, a relative path, another extension, a missing file, and a link
    that leaves the folder stay blocked.
    """
    for label, command in _check_runners():
        gate = _ArmedGate(label, command)
        try:
            skills = gate.home / ".claude" / "skills"
            (skills / "pkg").mkdir(parents=True)
            (skills / "analyze.md").write_text("# Skill: analyze\n", encoding="utf-8")
            (skills / "pkg" / "SKILL.md").write_text("# nested\n", encoding="utf-8")
            (skills / "notes.txt").write_text("x\n", encoding="utf-8")
            (gate.home / ".claude" / "settings.md").write_text("x\n", encoding="utf-8")
            (gate.project / "secret.md").write_text("x\n", encoding="utf-8")
            for intent in ("plan", "analyze", "verify"):
                gate.arm(intent)
                gate.expect("Read", {"file_path": str(skills / "analyze.md")}, 0, f"a pending {intent} lets a skill be read")
                gate.expect("Read", {"file_path": str(skills / "pkg" / "SKILL.md")}, 0, "a skill in a subfolder")
            gate.arm("plan")
            blocked = [
                (str(skills / "notes.txt"), "a file that is not .md"),
                (str(skills / "missing.md"), "a skill that does not exist"),
                (str(gate.home / ".claude" / "settings.md"), "a .md outside the skills folder"),
                (str(skills / ".." / "settings.md"), "a path stepping out with .."),
                (os.path.join(".claude", "skills", "analyze.md"), "a relative path"),
                (str(gate.project / "secret.md"), "a project file"),
            ]
            for path, why in blocked:
                gate.expect("Read", {"file_path": path}, 2, f"{why} stays blocked")
            gate.expect("Grep", {"pattern": "x", "path": str(skills)}, 2, "a Grep over the skills folder stays blocked")
            try:
                os.symlink(gate.project / "secret.md", skills / "out.md")
                os.symlink(skills / "analyze.md", skills / "in.md")
            except (OSError, NotImplementedError):
                print(f"  note: [{label}] no symlink privilege; the link cases of the skill-read lane are not exercised")
            else:
                gate.expect("Read", {"file_path": str(skills / "out.md")}, 2, "a link leaving the skills folder stays blocked")
                gate.expect("Read", {"file_path": str(skills / "in.md")}, 0, "a link inside the skills folder passes")
            gate.disarm()
            gate.expect("Read", {"file_path": str(gate.project / "secret.md")}, 0, "with no marker every Read passes")
        finally:
            gate.close()


def _check_precision_read_lane() -> None:
    """DEC-043: under any marker the main agent may read a small slice itself, as CLAUDE.md
    allows for file:line attribution: a project file outside .workflow, at most 200 lines
    (`limit`, or a file that short), two per marker. A third, a longer span, a whole large
    file, a file outside the project, and Grep stay blocked; a new marker starts a new count.
    """
    for label, command in _check_runners():
        gate = _ArmedGate(label, command)
        try:
            src = gate.project / "src"
            src.mkdir()
            big = src / "big.tsx"
            big.write_text("\n".join(f"line {n}" for n in range(500)) + "\n", encoding="utf-8")
            small = src / "small.py"
            small.write_text("\n".join(f"x{n} = {n}" for n in range(200)) + "\n", encoding="utf-8")
            (gate.project / ".workflow" / "config.json").write_text("{}", encoding="utf-8")
            outside = gate.base / "outside.py"
            outside.write_text("x = 1\n", encoding="utf-8")

            gate.arm("plan", set_at="2026-10-04T00:00:00+00:00")
            for tool_input, why in (
                ({"file_path": str(big), "offset": 1, "limit": 201}, "a span over 200 lines"),
                ({"file_path": str(big)}, "a whole file over 200 lines"),
                ({"file_path": str(outside), "limit": 10}, "a file outside the project"),
                ({"file_path": str(gate.project / ".workflow" / "config.json"), "limit": 5}, "a .workflow file"),
                ({"file_path": str(src / "missing.py"), "limit": 5}, "a file that does not exist"),
                ({"file_path": str(big), "limit": "50"}, "a limit that is not a number"),
            ):
                gate.expect("Read", tool_input, 2, f"{why} is not a precision read")
            gate.expect("Read", {"file_path": str(big), "offset": 100, "limit": 200}, 0, "a 200-line slice of a large file passes")
            gate.expect("Read", {"file_path": str(small)}, 0, "a 200-line file read whole passes")
            gate.expect("Read", {"file_path": str(big), "offset": 1, "limit": 10}, 2, "a third precision read is over the quota")
            gate.expect("Grep", {"pattern": "line", "path": str(src)}, 2, "Grep stays blocked")

            gate.arm("plan", set_at="2026-10-04T00:05:00+00:00")
            gate.expect("Read", {"file_path": os.path.join("src", "small.py"), "limit": 20}, 0, "a new marker starts a new count; a relative path resolves to the project")
            gate.arm("plan")
            gate.expect("Read", {"file_path": str(small), "limit": 20}, 2, "a marker without set_at keys no count, so no precision read")
        finally:
            gate.close()


class _ArmedGate:
    """A throwaway home + project with the gate armed, and one hook flavour to ask."""

    def __init__(self, label: str, command: list[str], root_name: str = "project") -> None:
        self.label, self.command = label, command
        self.base = Path(tempfile.mkdtemp(prefix=f"gate-arm-{label.replace(':', '-')}-"))
        self.home, self.project = self.base / "home", self.base / root_name
        self.foreign = self.base / "other"
        (self.home / ".claude").mkdir(parents=True)
        self.runtime = self.project / ".workflow" / "data" / "sessions" / "m1" / "runtime"
        self.runtime.mkdir(parents=True)
        (self.foreign / ".workflow").mkdir(parents=True)
        registry = {"c1": {"main_session_id": "m1", "cwd": str(self.project)}}
        # session-bind writes UTF-8 without a BOM; so does this.
        (self.home / ".claude" / "session_registry.json").write_bytes(json.dumps(registry, ensure_ascii=False).encode("utf-8"))
        self.env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        self.env.pop("WORKFLOW_LOCAL_MODE", None)

    def arm(self, intent: str = "plan", set_at: str | None = None) -> None:
        """Without `set_at` the marker keys no precision-read count, so that lane stays shut
        and the other lanes are tested alone; intent-gate-set always writes one."""
        marker = {"command": intent}
        if set_at is not None:
            marker["set_at"] = set_at
        (self.runtime / "delegated.marker").write_text(json.dumps(marker), encoding="utf-8")

    def disarm(self) -> None:
        (self.runtime / "delegated.marker").unlink(missing_ok=True)

    def exit_code(self, tool: str, tool_input: dict) -> int:
        payload = json.dumps({
            "session_id": "c1", "cwd": str(self.project), "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input,
        })
        done = subprocess.run(
            self.command, input=payload, text=True, encoding="utf-8", errors="replace",
            env={**self.env, "CLAUDE_HOOK_RAW": payload}, capture_output=True, timeout=60,
        )
        return done.returncode

    def expect(self, tool: str, tool_input: dict, code: int, why: str) -> None:
        got = self.exit_code(tool, tool_input)
        assert_true(got == code, f"[{self.label}] {why}: {tool} {tool_input}; exit {got}, expected {code}")

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def _flavours() -> list[tuple[str, list[str]]]:
    runners = _check_runners()
    labels = {label for label, _ in runners}
    if "ps1" not in labels:
        print("  note: no PowerShell on PATH; the .ps1 flavour of intent-gate-check is not exercised")
    if "sh" not in labels:
        print("  note: bash wrapper not exercised on this platform; the .sh flavour runs through its embedded python")
    return runners


def _test_intent_gate_runner_parsed() -> None:
    """The runner allowlist is a parse, not a pattern, and is anchored to the project root.

    The regex let any directory sit in front of `.workflow/run.sh` and let an interpreter
    flag such as `-c`/`-Command` hand the rest of the line to PowerShell, and the PowerShell
    tool was never gated at all. Every case runs against both flavours with one verdict.
    """
    for label, command in _flavours():
        gate = _ArmedGate(label, command)
        try:
            gate.arm("plan")
            root, posix_root = str(gate.project), gate.project.as_posix()
            foreign = gate.foreign.as_posix()
            ps_allowed = [
                f"& \"{root}{os.sep}.workflow{os.sep}run.ps1\" analyze \"task (x) kenapa\" \"m1\"",
                ".workflow/run.ps1 plan \"t\" 'm1'",
                "& .workflow/check.ps1",
                f"& '{posix_root}/.workflow/inspect.ps1' -Verbose",
                "&.workflow/run.ps1 plan \"say \"\"hi\"\"\" 'it''s'",
            ]
            for source in ps_allowed:
                gate.expect("PowerShell", {"command": source}, 0, "a plain runner call must pass")
            ps_refused = [
                "Get-Content README.md",
                "Get-ChildItem -Recurse",
                "& .workflow/run.ps1 (Get-Content README.md -Raw)",
                "& .workflow/run.ps1 \"$(Get-Content README.md)\"",
                "& .workflow/run.ps1 \"$env:USERPROFILE\"",
                "& .workflow/run.ps1 $x",
                "& .workflow/run.ps1 plan; Get-Content README.md",
                "& .workflow/run.ps1 @{x=(gc y)}",
                "& .workflow/run.ps1 plan | Out-Null",
                "& .workflow/run.ps1 plan > out.txt",
                "& .workflow/run.ps1 -Task:x",
                "& .workflow/run.ps1 --% x",
                "& .workflow/run.ps1 plan\nGet-Content README.md",
                ". .workflow/run.ps1 plan",
                f"\"{posix_root}/.workflow/run.ps1\" plan",
                "& .workflow/runx.ps1 plan",
                "& ../.workflow/run.ps1 plan",
                f"& \"{foreign}/.workflow/run.ps1\" plan",
                "& .workflow/run.ps1 “plan”",
            ]
            for source in ps_refused:
                gate.expect("PowerShell", {"command": source}, 2, "PowerShell that is not one plain runner call must block")

            bash_allowed = [
                ".workflow/run.ps1 plan \"t\" \"m1\"",
                "./.workflow/run.sh plan 't (x)' \"m1\"",
                f"\"{posix_root}/.workflow/run.ps1\" plan \"t\" \"m1\"",
                "powershell -NoProfile -ExecutionPolicy Bypass -File .workflow/run.ps1 plan \"t\" \"m1\"",
                "pwsh.exe -NonInteractive -File .workflow/check.ps1",
                "bash .workflow/inspect.sh",
                ".workflow/sub/../run.sh plan",
            ]
            for line in bash_allowed:
                gate.expect("Bash", {"command": line}, 0, "a clean runner call must pass")
            bash_refused = [
                f"bash {foreign}/.workflow/run.sh plan",
                f"\"{foreign}/.workflow/run.ps1\" plan",
                ".workflow/runx.sh plan",
                "../.workflow/run.sh plan",
                ".workflow/../../other/.workflow/run.sh plan",
                "powershell -c .workflow/run.ps1 (Get-Content README.md -Raw)",
                "powershell -Command .workflow/run.ps1 plan",
                "powershell -EncodedCommand ZQBjAGgAbwA=",
                "powershell -e .workflow/run.ps1",
                "powershell .workflow/run.ps1 plan",
                "powershell -NoProfile -ExecutionPolicy Unrestricted -File .workflow/run.ps1 plan",
                "bash -c .workflow/run.sh",
                "bash -x .workflow/run.sh plan",
                "bash -ExecutionPolicy Bypass -File .workflow/run.sh",
                "FOO=1 .workflow/run.sh plan",
                ".workflow/run.sh \"plan",
            ]
            for line in bash_refused:
                gate.expect("Bash", {"command": line}, 2, "a Bash line that is not a clean anchored runner call must block")
        finally:
            gate.close()


def _test_intent_gate_runner_tamper() -> None:
    """While the gate is armed, the runner scripts cannot be rewritten; other writes pass.

    The runner is the one command the gate lets through, so a Write of run.sh followed by
    running it would have been an arbitrary command. Writes elsewhere are not gather and are
    not the gate's business (verify writes tests.json, verify-browser its request draft).
    """
    for label, command in _flavours():
        gate = _ArmedGate(label, command)
        try:
            wf = gate.project / ".workflow"
            gate.arm("plan")
            refused = [
                ("Write", {"file_path": str(wf / "run.sh"), "content": "cat secret"}),
                ("Write", {"file_path": str(wf / "sub" / ".." / "run.ps1"), "content": "x"}),
                ("Edit", {"file_path": ".workflow/run.ps1", "old_string": "a", "new_string": "b"}),
                ("MultiEdit", {"file_path": str(wf / "check.ps1"), "edits": []}),
                ("NotebookEdit", {"notebook_path": str(wf / "inspect.sh"), "new_source": "x"}),
                ("Write", {"file_path": str(wf / "RUN.PS1"), "content": "x"}),
            ]
            for tool, tool_input in refused:
                gate.expect(tool, tool_input, 2, "a write of a runner script must block while armed")
            allowed = [
                ("Write", {"file_path": str(gate.project / "src" / "app.py"), "content": "x"}),
                ("Write", {"file_path": str(wf / "config.json"), "content": "{}"}),
                ("Edit", {"file_path": str(wf / "runner-notes.md"), "old_string": "a", "new_string": "b"}),
                ("Write", {"file_path": str(gate.foreign / ".workflow" / "run.sh"), "content": "x"}),
            ]
            for tool, tool_input in allowed:
                gate.expect(tool, tool_input, 0, "a write that is not a runner script must pass")
            gate.disarm()
            gate.expect("Write", {"file_path": str(wf / "run.sh"), "content": "x"}, 0, "with no marker the runner is writable")
        finally:
            gate.close()


def _test_intent_gate_non_ascii_root() -> None:
    """A project root with non-ASCII characters still resolves through the registry.

    session-bind writes the registry as UTF-8 without a BOM; Windows PowerShell 5.1 read it
    as ANSI (`proyék` -> `proyÃ©k`), resolved a root that does not exist, found no marker and
    let everything through. Both hooks, every flavour available.
    """
    for label, command in _flavours():
        gate = _ArmedGate(label, command, root_name="proyék")
        try:
            gate.arm("plan")
            gate.expect("Grep", {"pattern": "x"}, 2, "a non-ASCII root must still arm the check")
            gate.expect("Bash", {"command": ".workflow/run.sh plan \"t\" \"m1\""}, 0, "the runner under a non-ASCII root must pass")
        finally:
            gate.close()
    for label, command in _runners():
        base = Path(tempfile.mkdtemp(prefix=f"gate-set-{label.replace(':', '-')}-"))
        try:
            home, project = base / "home", base / "proyék"
            (home / ".claude").mkdir(parents=True)
            (project / ".workflow" / "data").mkdir(parents=True)
            registry = {"c1": {"main_session_id": "m1", "cwd": str(project)}}
            (home / ".claude" / "session_registry.json").write_bytes(json.dumps(registry, ensure_ascii=False).encode("utf-8"))
            payload = json.dumps({
                "session_id": "c1", "cwd": str(project),
                "hook_event_name": "UserPromptSubmit", "prompt": "/.plan add a feature",
            })
            env = {
                **os.environ, "HOME": str(home), "USERPROFILE": str(home),
                "CLAUDE_HOOK_RAW": payload, "HOOK_DIR": str(HOOKS),
            }
            subprocess.run(command, input=payload, text=True, encoding="utf-8", errors="replace", env=env, capture_output=True, timeout=60)
            marker = project / ".workflow" / "data" / "sessions" / "m1" / "runtime" / "delegated.marker"
            assert_true(marker.is_file(), f"[{label}] intent-gate-set must arm the gate under a non-ASCII root")
        finally:
            shutil.rmtree(base, ignore_errors=True)
