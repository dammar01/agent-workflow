#!/usr/bin/env bash
# intent-gate-check.sh - PreToolUse hook (Pre-flight gate: CHECK side) POSIX parity.
# Matcher (settings.json): mcp__.*|Read|Grep|Glob|Bash. If a DELEGATED marker is pending
# (set by intent-gate-set.sh, not yet cleared by .workflow/run) -> HARD-block: exit 2 with
# the reason on stderr. Bash allowlist: only a clean .workflow/{run,check,inspect} call.
# A pending verify marker also lets through what skills/verify.md asks before the run: a
# clean `git diff --name-only|--name-status|--stat ...`, a Read of .workflow/config.json or
# of this session's verify/tests.json, and a Write of that tests.json.
# Escapes: WORKFLOW_LOCAL_MODE=1 / local_mode.flag. Marker/session absent -> allow (fail-open).
# exit 2 = block ; every other path exits 0. Exit code flows from python3 (no trailing exit).
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import os, sys, json, re, datetime


def workflow_data_dir(root):
    # Same rule as core/workspace/workspace_paths.data_dir: .workflow/data once it exists,
    # the .workflow root while a v3.5.x-layout workspace still keeps its data there, data/ otherwise.
    wf = os.path.join(root, ".workflow")
    data = os.path.join(wf, "data")
    if os.path.isdir(data):
        return data
    for name in ("sessions", "provider-sessions", "reports", "audit.jsonl", "usage.jsonl", "quality.jsonl", "facts.jsonl", "evidence.jsonl", "redactions.jsonl"):
        if os.path.exists(os.path.join(wf, name)):
            return wf
    return data


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


def same_path(candidate, expected, base):
    # True when candidate names expected. A relative path resolves against the project root.
    if not candidate.strip():
        return False
    try:
        if not os.path.isabs(candidate):
            candidate = os.path.join(base, candidate)
        a = os.path.normcase(os.path.abspath(candidate))
        b = os.path.normcase(os.path.abspath(expected))
        return a == b
    except Exception:
        return False


# A runner call: the command's first token is the runner script (bare or quoted), optionally
# behind an interpreter and its flags (`powershell -NoProfile -ExecutionPolicy Bypass -File`).
_RUNNER_PATH = r"\.workflow[\\/](?:run|check|inspect)\.(?:ps1|sh)"
RUNNER = re.compile(
    r"^\s*(?:(?:powershell|pwsh|bash|sh)(?:\.exe)?(?:\s+-[A-Za-z]+(?:\s+Bypass)?)*\s+)?"
    r"(?:\"(?:[^\"]*[\\/])?" + _RUNNER_PATH + r"\""
    r"|'(?:[^']*[\\/])?" + _RUNNER_PATH + r"'"
    r"|(?:[^\s\"']*[\\/])?" + _RUNNER_PATH + r")(?=\s|$)",
    re.IGNORECASE,
)

DIFF_SUMMARY = {"--name-only", "--name-status"}
DIFF_ALLOWED = {"--cached", "--staged", "--relative", "--no-renames", "--no-color", "--"}


def diff_summary(command):
    # `git diff` that names files only: --name-only, --name-status or --stat, every other
    # option from a short list that can neither print a patch nor write a file; refs and
    # paths are free. The caller has already rejected shell metacharacters.
    tokens = command.split()
    if len(tokens) < 3 or tokens[0] != "git" or tokens[1] != "diff":
        return False
    summary = False
    for tok in tokens[2:]:
        if tok in DIFF_SUMMARY or re.fullmatch(r"--stat(=\d+(,\d+)*)?", tok):
            summary = True
        elif tok in DIFF_ALLOWED:
            continue
        elif tok.startswith("-"):
            return False
    return summary


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

    runtime_dir = os.path.join(workflow_data_dir(root), "sessions", main_id, "runtime")
    marker = os.path.join(runtime_dir, "delegated.marker")
    local_flag = os.path.join(runtime_dir, "local_mode.flag")

    if os.path.isfile(local_flag):
        sys.exit(0)
    if not os.path.isfile(marker):
        sys.exit(0)

    cmd = "?"
    try:
        with open(marker, "r", encoding="utf-8") as f:
            cmd = str(json.load(f).get("command") or "?")
    except Exception as exc:
        cmd = "?"
        hook_warning("marker_unreadable", exc)

    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}

    # Bash allowlist: permit ONLY a clean .workflow/{run,check,inspect} call. Any shell
    # metacharacter that could chain a gather step forces the block path below.
    if tool_name == "Bash":
        bash_cmd = str(ti.get("command") or "")
        chained = (
            bool(re.search(r"[&;|`]", bash_cmd))
            or ("$(" in bash_cmd)
            or bool(re.search(r"[<>]", bash_cmd))
            or ("\n" in bash_cmd)
        )
        # The runner must be the command itself (optionally behind powershell/pwsh/bash/sh
        # and their flags), not a word anywhere in it: `python -c "..." x/.workflow/run.sh`
        # is not a runner call.
        if (not chained) and RUNNER.search(bash_cmd):
            sys.exit(0)
        # verify: the diff that tells main_agent which tests to pick (skills/verify.md).
        if cmd == "verify" and not chained and diff_summary(bash_cmd):
            sys.exit(0)

    # verify: the test allowlist and this session's test request, nothing else.
    if cmd == "verify" and tool_name in ("Read", "Write"):
        target = str(ti.get("file_path") or "")
        tests_json = os.path.join(os.path.dirname(runtime_dir), "verify", "tests.json")
        if same_path(target, tests_json, root):
            sys.exit(0)
        if tool_name == "Read" and same_path(target, os.path.join(root, ".workflow", "config.json"), root):
            sys.exit(0)

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
