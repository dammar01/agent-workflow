#!/usr/bin/env bash
# task-events.sh - PostToolUse hook, and PreToolUse for Bash (DEC-015). POSIX parity with
# task-events.ps1. Matcher (settings.json): PostToolUse Read|Skill|Edit|Write|MultiEdit|
# NotebookEdit|Bash; PreToolUse Bash.
# Records what the runtime never sees into .workflow/data/tasks.jsonl:
#   - a local workflow skill being loaded (Read of ~/.claude/skills/<name>.md, or the Skill tool)
#   - a file edited inside the project (not .workflow/ or .git/)
#   - a commit just made: PreToolUse saves HEAD before a Bash `git commit`, PostToolUse
#     records the new HEAD only if it moved. A failed commit, or one with no saved HEAD,
#     records nothing.
# Delegated skills are left out: the runtime records them with their verdict. No prompt
# change is needed; every event is a tool call that already happened. Only appends: task
# state is derived at report time (core/audit/task_telemetry.py). Never blocks, exit 0.
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import datetime, json, os, re, subprocess

LOCAL_SKILLS = {
    "execute", "init", "upgrade", "doctor", "sweep", "refactor", "commit", "review",
    "compress", "memory", "caveman", "local", "provider", "promote", "help",
}
SKILL_FILE = re.compile(r"[\\/]\.claude[\\/]skills[\\/]\.?([\w-]+)\.md$")
GIT_COMMIT = re.compile(r"\bgit\b[^\n|;&]*\bcommit\b")


def project_root(start):
    current = os.path.abspath(start or ".")
    while True:
        if os.path.isdir(os.path.join(current, ".workflow", "data")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def main_session(claude_sid):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", "session_registry.json")
        with open(path, encoding="utf-8") as fh:
            entry = (json.load(fh) or {}).get(claude_sid) or {}
        return entry.get("main_session_id") or None
    except Exception:
        return None


def inside(root, path):
    if not path:
        return None
    full = os.path.abspath(path if os.path.isabs(path) else os.path.join(root, path))
    try:
        rel = os.path.relpath(full, root)
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("../") or rel == ".." or rel.split("/")[0] in (".workflow", ".git"):
        return None
    return rel


def git_out(root, *args):
    out = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=5)
    return out.stdout if out.returncode == 0 else None


def snapshot_path(root, payload):
    key = re.sub(r"[^\w-]", "_", str(payload.get("tool_use_id") or payload.get("session_id") or ""))
    return os.path.join(root, ".workflow", "data", "task-hook", key + ".head") if key else None


def save_head(payload, root):
    path = snapshot_path(root, payload)
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write((git_out(root, "rev-parse", "--verify", "-q", "HEAD") or "").strip())


def event_for(payload, root):
    tool = str(payload.get("tool_name") or "")
    ti = payload.get("tool_input") or {}
    if tool == "Read":
        match = SKILL_FILE.search(str(ti.get("file_path") or ""))
        if match and match.group(1) in LOCAL_SKILLS:
            return {"kind": "skill", "skill": match.group(1)}
        return None
    if tool == "Skill":
        name = str(ti.get("skill") or "").lstrip(".")
        return {"kind": "skill", "skill": name} if name in LOCAL_SKILLS else None
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        rel = inside(root, ti.get("file_path") or ti.get("notebook_path"))
        return {"kind": "edit", "path": rel} if rel else None
    if tool == "Bash" and GIT_COMMIT.search(str(ti.get("command") or "")):
        path = snapshot_path(root, payload)
        if not path or not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            before = fh.read().strip()
        os.remove(path)
        out = git_out(root, "log", "-1", "--format=%H", "--name-only", "--relative")
        lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
        if not lines or lines[0] == before:
            return None
        return {"kind": "commit", "commit": lines[0], "paths": lines[1:]}
    return None


try:
    payload = json.loads(os.environ.get("CLAUDE_HOOK_RAW", ""))
    root = project_root(payload.get("cwd"))
    pre = payload.get("hook_event_name") == "PreToolUse"
    if root and pre:
        if payload.get("tool_name") == "Bash" and GIT_COMMIT.search(str((payload.get("tool_input") or {}).get("command") or "")):
            save_head(payload, root)
    event = event_for(payload, root) if root and not pre else None
    if event:
        row = {
            "v": 1,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "hook",
            "session_id": main_session(payload.get("session_id")),
            **event,
        }
        with open(os.path.join(root, ".workflow", "data", "tasks.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
except Exception:
    pass
PY
exit 0
