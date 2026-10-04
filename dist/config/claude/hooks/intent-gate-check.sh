#!/usr/bin/env bash
# intent-gate-check.sh - PreToolUse hook (Pre-flight gate: CHECK side) POSIX parity.
# Matcher (settings.json): mcp__.*|Read|Grep|Glob|Bash|PowerShell|Write|Edit|MultiEdit|
# NotebookEdit. If a DELEGATED marker is pending (set by intent-gate-set.sh, not yet cleared
# by .workflow/run) -> HARD-block: exit 2 with the reason on stderr. Bash/PowerShell
# allowlist: only one plain .workflow/{run,check,inspect} call, parsed (shlex for Bash; a
# conservative PowerShell tokeniser standing in for the .ps1 flavour's AST check), behind at
# most powershell/pwsh -NoProfile/-NonInteractive/-ExecutionPolicy Bypass -File or a bare
# bash/sh, and resolving to exactly <root>/.workflow/<runner>.{ps1,sh}. Writes pass except a
# Write/Edit/MultiEdit/NotebookEdit of the runner scripts themselves.
# A pending verify marker also lets through what skills/verify.md asks before the run: a
# clean `git diff --name-only|--name-status|--stat ...`, a Read of .workflow/config.json or
# of this session's verify/tests.json, and a Write of that tests.json.
# Escapes: WORKFLOW_LOCAL_MODE=1 / local_mode.flag. Marker/session absent -> allow (fail-open).
# exit 2 = block ; every other path exits 0. Exit code flows from python3 (no trailing exit).
RAW="$(cat)"
[ -z "$RAW" ] && exit 0
CLAUDE_HOOK_RAW="$RAW" python3 <<'PY'
import os, sys, json, re, shlex, datetime


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


RUNNERS = ("run.ps1", "run.sh", "check.ps1", "check.sh", "inspect.ps1", "inspect.sh")


def runner_target(candidate, root):
    # True when candidate names exactly <root>/.workflow/{run,check,inspect}.{ps1,sh}.
    return any(same_path(candidate, os.path.join(root, ".workflow", name), root) for name in RUNNERS)


def runner_file(candidate, root):
    # True when a file write lands on a runner script: <root>/.workflow/{run,check,inspect}
    # with any extension. On Windows an NTFS stream suffix (`run.ps1::$DATA` writes run.ps1)
    # is cut off first, and a path carrying an 8.3 short name (`WORKFL~1`) counts when the
    # filesystem cannot resolve it. Unsure -> True. Same rule as Test-RunnerFile in the .ps1.
    if not candidate.strip():
        return False
    try:
        if os.name == "nt" and len(candidate) > 2 and ":" in candidate[2:]:
            candidate = candidate[: candidate.index(":", 2)]
        if not os.path.isabs(candidate):
            candidate = os.path.join(root, candidate)
        full = os.path.realpath(candidate).rstrip("\\/")
        if not re.fullmatch(r"(?i)(run|check|inspect)(\..*)?", os.path.basename(full)):
            return False
        parent = os.path.dirname(full)
        wf = os.path.realpath(os.path.join(root, ".workflow"))
        if os.path.normcase(parent) == os.path.normcase(wf) or same_path(parent, wf, root):
            return True
        leaf = os.path.basename(parent)
        return "~" in parent and (leaf == ".workflow" or "~" in leaf)
    except Exception:
        return True


def msys_path(path):
    # Git Bash spells E:\x as /e/x (or /cygdrive/e/x); read it the way that shell does.
    m = re.fullmatch(r"/(?:cygdrive/)?([A-Za-z])(/.*)?", path) if os.name == "nt" else None
    return (m.group(1) + ":" + (m.group(2) or "/")) if m else path


_PS_INTERPRETER = re.compile(r"(?i)(powershell|pwsh)(\.exe)?")
_SH_INTERPRETER = re.compile(r"(?i)(bash|sh)(\.exe)?")


def bash_runner_call(command, root):
    # The runner is the command itself: the first word, or behind powershell/pwsh (only
    # -NoProfile, -NonInteractive, -ExecutionPolicy Bypass, then -File) or behind bash/sh
    # with no flag at all. Anything else in front can run code of its own: powershell
    # without -File evaluates the rest as PowerShell, and `bash -ExecutionPolicy` reads as
    # a bundle holding -c. Same rule as Test-BashRunnerCall in the .ps1 flavour.
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    i = 0
    if _PS_INTERPRETER.fullmatch(words[0]):
        i, file_flag = 1, False
        while i < len(words) and not file_flag:
            w = words[i].lower()
            if w in ("-noprofile", "-noninteractive"):
                i += 1
            elif w == "-executionpolicy" and i + 1 < len(words) and words[i + 1].lower() == "bypass":
                i += 2
            elif w == "-file":
                i, file_flag = i + 1, True
            else:
                return False
        if not file_flag:
            return False
    elif _SH_INTERPRETER.fullmatch(words[0]):
        i = 1
    if i >= len(words):
        return False
    return runner_target(msys_path(words[i]), root)


_PS_FORBIDDEN = set("$()@{};|&<>`,")
_PS_DASHES = ("-", "\u2013", "\u2014", "\u2015")


def ps_plain_command(source):
    # The words of PowerShell source that is one plain command and nothing else, or None.
    # The .ps1 flavour asks PowerShell's own parser; there is none here, so this is the
    # conservative stand-in: optional leading `&`, then words that are bare (none of
    # $ ( ) @ { } ; | & < > ` ,), '..' (with '' for a quote) or ".." (with "" for a quote,
    # no $ or backtick), each quote spanning a whole word. A dash word carrying `:` (a
    # switch with an argument) refuses; `#` at a word start begins a comment. Raw newlines,
    # --% and typographic quotes refuse outright, as they do in the .ps1 flavour. It refuses
    # some odd quoting the parser accepts (a"b c"), never the other way round.
    if re.search("[\r\n]|--%|[\u2018-\u201e]", source):
        return None
    text = source.strip(" \t")
    call = text.startswith("&")
    if call:
        text = text[1:].lstrip(" \t")
    words, quoted = [], []
    i, n = 0, len(text)
    while i < n:
        if text[i] in " \t":
            i += 1
            continue
        if text[i] == "#":
            break
        if text[i] in "'\"":
            q = text[i]
            buf = []
            i += 1
            while True:
                if i >= n:
                    return None
                c = text[i]
                if c == q:
                    if i + 1 < n and text[i + 1] == q:
                        buf.append(q)
                        i += 2
                        continue
                    i += 1
                    break
                if q == '"' and c in "$`":
                    return None
                buf.append(c)
                i += 1
            if i < n and text[i] not in " \t":
                return None
            words.append("".join(buf))
            quoted.append(True)
            continue
        start = i
        while i < n and text[i] not in " \t":
            if text[i] in _PS_FORBIDDEN or text[i] in "'\"":
                return None
            i += 1
        word = text[start:i]
        if word.startswith(_PS_DASHES) and ":" in word:
            return None
        words.append(word)
        quoted.append(False)
    if not words or (quoted[0] and not call):
        return None
    return words


DIFF_SUMMARY = {"--name-only", "--name-status"}
DIFF_ALLOWED = {"--cached", "--staged", "--relative", "--no-renames", "--no-color", "--"}


def diff_summary(command):
    return diff_words(command.split())


def diff_words(tokens):
    # `git diff` that names files only: --name-only, --name-status or --stat, every other
    # option from a short list that can neither print a patch nor write a file; refs and
    # paths are free. The caller has already rejected shell metacharacters.
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

    # File writes: only the runner scripts are refused. Rewriting run.sh and then running it
    # would make the runner allowlist below an arbitrary command.
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if runner_file(target, root):
            sys.stderr.write((
                "[PRE-FLIGHT GATE] intent=DELEGATED (%s) is pending and '%s' targets a runner script: %s\n"
                "The runner is the one command this gate lets through, so it cannot be rewritten while the gate is armed.\n"
                "Do this instead: .workflow/run.sh %s \"<task>\" \"%s\" as shipped.\n"
                "False positive? Escapes: export WORKFLOW_LOCAL_MODE=1, create %s, or delete %s.\n"
            ) % (cmd, tool_name, target, cmd, main_id, local_flag, marker))
            sys.exit(2)
        sys.exit(0)

    # Bash allowlist: permit ONLY a clean .workflow/{run,check,inspect} call. Any shell
    # metacharacter that could chain a gather step forces the block path below.
    if tool_name == "Bash":
        bash_cmd = str(ti.get("command") or "")
        chained = (
            bool(re.search(r"[&;|`]", bash_cmd))
            or ("$(" in bash_cmd)
            or bool(re.search(r"[<>]", bash_cmd))
            or bool(re.search(r"[\r\n]", bash_cmd))
        )
        # The runner must be the command itself, anchored to this project's .workflow, not
        # a word anywhere in it: `python -c "..." x/.workflow/run.sh` is not a runner call.
        if (not chained) and bash_runner_call(bash_cmd, root):
            sys.exit(0)
        # verify: the diff that tells main_agent which tests to pick (skills/verify.md).
        if cmd == "verify" and not chained and diff_summary(bash_cmd):
            sys.exit(0)

    # PowerShell tool: its command is PowerShell source, so it is held to one plain command;
    # the same runner anchor and verify diff lane then apply.
    if tool_name == "PowerShell":
        words = ps_plain_command(str(ti.get("command") or ""))
        if words is not None:
            if runner_target(words[0], root):
                sys.exit(0)
            if cmd == "verify" and diff_words(words):
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
        if tool_name in ("Bash", "PowerShell")
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
