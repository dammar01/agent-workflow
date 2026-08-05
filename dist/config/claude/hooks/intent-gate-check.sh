#!/usr/bin/env bash
# intent-gate-check.sh - PreToolUse hook (Pre-flight gate: CHECK side) POSIX parity.
# Matcher (settings.json): mcp__.*|Read|Grep|Glob|Bash. If a DELEGATED marker is pending
# (set by intent-gate-set.sh, not yet cleared by .workflow/run) -> HARD-block: exit 2 with
# the reason on stderr. Bash allowlist: only a clean .workflow/{run,check,inspect} call.
# Escapes: WORKFLOW_LOCAL_MODE=1 / local_mode.flag. Marker/session absent -> allow (fail-open).
# exit 2 = block ; every other path exits 0. Exit code flows from python3 (no trailing exit).
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import os, sys, json, re, datetime

# Fail-open leaves no trace, and that is the problem: a hook that dies on a malformed
# registry exits 0 exactly like a hook that found nothing to block, so the enforcement
# layer can be dead for an entire session with nothing to show for it. Record the fault
# and still exit 0 — non-wedging, just no longer silent. Written ONLY on real faults,
# never on the normal allow/block paths, and overwritten rather than appended.
runtime_dir = None


def hook_warning(kind, message):
    try:
        # Session dir when it is known. The fault most worth recording — an unparseable
        # registry — happens BEFORE that dir can be resolved, so a session-only location
        # would miss exactly the case this exists for; ~/.claude is the fallback.
        target = runtime_dir or os.path.join(os.path.expanduser("~"), ".claude")
        if not target:
            return
        os.makedirs(target, exist_ok=True)
        payload = {
            "hook": "intent-gate-check",
            "kind": kind,
            "message": str(message),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        with open(
            os.path.join(target, "hook-warning.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(payload, fh, indent=2)
    except Exception:
        pass


try:
    raw = os.environ.get("CLAUDE_HOOK_RAW", "")
    if not raw.strip():
        sys.exit(0)
    payload = json.loads(raw)
    claude_sid = payload.get("session_id")
    tool_name = str(payload.get("tool_name") or "")
    cwd = payload.get("cwd")
    if not claude_sid:
        sys.exit(0)

    # global env escape
    if os.environ.get("WORKFLOW_LOCAL_MODE") == "1":
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
    local_flag = os.path.join(runtime_dir, "local_mode.flag")

    if os.path.isfile(local_flag):
        sys.exit(0)
    if not os.path.isfile(marker):
        sys.exit(0)

    # Bash allowlist: permit ONLY a clean .workflow/{run,check,inspect} call. Any shell
    # metacharacter that could chain a gather step forces the block path below.
    if tool_name == "Bash":
        ti = payload.get("tool_input") or {}
        bash_cmd = str(ti.get("command") or "") if isinstance(ti, dict) else ""
        chained = (
            bool(re.search(r"[&;|`]", bash_cmd))
            or ("$(" in bash_cmd)
            or bool(re.search(r"[<>]", bash_cmd))
            or ("\n" in bash_cmd)
        )
        if (not chained) and re.search(
            r"(^|[\\/])\.workflow[\\/](run|check|inspect)\.(ps1|sh)\b", bash_cmd
        ):
            sys.exit(0)

    cmd = "?"
    try:
        with open(marker, "r", encoding="utf-8") as f:
            cmd = str(json.load(f).get("command") or "?")
    except Exception as exc:
        cmd = "?"
        hook_warning("marker_unreadable", exc)

    what = (
        "a shell read (cat/rg/grep/git show) -- reading the codebase is second_agent's job"
        if tool_name == "Bash"
        else "a bulk-gather tool"
    )
    reason = (
        "[PRE-FLIGHT GATE] intent=DELEGATED (%s) but .workflow/run has NOT run this turn.\n"
        "Tool '%s' is %s -- FORBIDDEN before delegation (Division of Labor: gather = second_agent).\n"
        "Do this instead: .workflow/run.sh %s \"<task>\" \"%s\"\n"
        "That routes evidence to second_agent AND clears this gate.\n"
        "False positive? Escapes: export WORKFLOW_LOCAL_MODE=1, create %s, or delete %s."
    ) % (cmd, tool_name, what, cmd, main_id, local_flag, marker)
    sys.stderr.write(reason + "\n")
    sys.exit(2)
except SystemExit:
    raise
except Exception as exc:
    # on any hook error, fail-open (never wedge the agent) — but leave the reason behind
    hook_warning("hook_error", exc)
    sys.exit(0)
PY
