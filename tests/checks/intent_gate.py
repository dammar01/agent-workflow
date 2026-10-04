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

from tests.checks.support import assert_true

HOOKS = Path(__file__).resolve().parents[2] / "dist" / "config" / "claude" / "hooks"


def _embedded_python() -> str:
    text = (HOOKS / "intent-gate-set.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def _runners() -> list[tuple[str, list[str]]]:
    runners = [("sh:python", [sys.executable, "-c", _embedded_python()])]
    shell = shutil.which("pwsh") or (shutil.which("powershell") if sys.platform == "win32" else None)
    if shell:
        runners.append(("ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HOOKS / "intent-gate-set.ps1")]))
    bash = shutil.which("bash") if sys.platform != "win32" else None
    if bash:
        runners.append(("sh", [bash, str(HOOKS / "intent-gate-set.sh")]))
    return runners


def _test_intent_gate_reads_the_prompt_field() -> None:
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
