#!/usr/bin/env bash
# intent-gate-set.sh - UserPromptSubmit hook (Pre-flight gate: SET side) POSIX parity.
# Classifies the raw prompt against the DELEGATED NL-map (intent-map.json). Delegated ->
# write .workflow/sessions/<MAIN_SESSION_ID>/runtime/delegated.marker ; else delete stale.
# MAIN_SESSION_ID resolved from $HOME/.claude/session_registry.json. Never blocks (exit 0).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
HOOK_DIR="$SCRIPT_DIR" CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import os, sys, json, re, datetime

try:
    raw = os.environ.get("CLAUDE_HOOK_RAW", "")
    if not raw.strip():
        sys.exit(0)
    payload = json.loads(raw)
    prompt = str(payload.get("user_prompt") or "")
    claude_sid = payload.get("session_id")
    cwd = payload.get("cwd")
    if not claude_sid:
        sys.exit(0)

    home = os.path.expanduser("~")
    registry_path = os.path.join(home, ".claude", "session_registry.json")
    if not os.path.isfile(registry_path):
        sys.exit(0)
    with open(registry_path, "r", encoding="utf-8") as f:
        reg = json.load(f) or {}
    entry = reg.get(claude_sid)
    if not entry:
        sys.exit(0)
    main_id = str(entry.get("main_session_id") or "")
    root = str(entry.get("cwd") or "") or str(cwd or "")
    if not main_id or not root:
        sys.exit(0)

    runtime_dir = os.path.join(root, ".workflow", "sessions", main_id, "runtime")
    marker = os.path.join(runtime_dir, "delegated.marker")

    map_path = os.path.join(os.environ.get("HOOK_DIR", ""), "intent-map.json")
    if not os.path.isfile(map_path):
        sys.exit(0)
    with open(map_path, "r", encoding="utf-8") as f:
        mp = json.load(f)

    resolved = None
    prefix_regex = mp.get("prefix_regex")
    m = re.search(prefix_regex, prompt) if prefix_regex else None
    if m:
        resolved = m.group(1)
    else:
        for cmd in mp.get("delegated", []):
            for pat in mp.get("patterns", {}).get(cmd, []):
                if re.search(pat, prompt, re.IGNORECASE):
                    resolved = cmd
                    break
            if resolved:
                break

    if resolved:
        os.makedirs(runtime_dir, exist_ok=True)
        obj = {
            "command": resolved,
            "set_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "claude_sid": claude_sid,
            "prompt_excerpt": prompt[:160],
        }
        with open(marker, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj))
    else:
        if os.path.isfile(marker):
            try:
                os.remove(marker)
            except Exception:
                pass
    sys.exit(0)
except SystemExit:
    raise
except Exception:
    sys.exit(0)
PY
exit 0
