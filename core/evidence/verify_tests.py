"""Tests for /.verify: chosen by main_agent, run by the runtime, judged beside the review.

The second agent verifies by reading and tracing only. When it also ran tests, providers
diverged: codex ran whole suites (CASE-010: verify medians up to 366 s), opencode could run
none and every such verify came back `incomplete` (CASE-009). Now main_agent picks the tests
that cover the change and its dependents and writes them, with its reason, to the session's
request file before calling verify:

    .workflow/data/sessions/<MAIN_SESSION_ID>/verify/tests.json
    {"commands": ["python tests/run.py --only installer"], "reason": "installer changed"}

`commands: []` with a reason says no test covers the change. The runtime runs a command only
when it starts with a prefix the project lists in `commands.verify_test_commands`, with no
shell (no `&&`, pipes, redirects or substitution), one at a time, each under
`commands.verify_test_timeout_seconds`. Results are written into the [VERIFICATION] block
the second agent returned, which never sees them: `checks_run` gets one runtime line per
command; a failing, timed-out or unrunnable command is a blocking finding (origin `unknown`,
so it fails closed); a refused command or a missing request is a `not_verified` gap. The
verdict is then derived from the block as usual, so `pass` needs green tests and a review
with no blocking finding.

The request is consumed: it is renamed to `tests.used.json` once read, so a later verify
never reruns an old choice silently.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from core.workspace.workspace_paths import read_json_file, workflow_paths
from utils import osutil
from utils.redact import redact

MAX_COMMANDS = 5
DEFAULT_TIMEOUT_SECONDS = 900
OUTPUT_TAIL_CHARS = 1200
_SHELL_SYNTAX = re.compile(r"[&|;<>`\n\r]|\$\(")


def request_path(project_root, session_id: str) -> Path:
    return workflow_paths(Path(project_root), session_id)["session_dir"] / "verify" / "tests.json"


def _policy(project_root) -> tuple[list[list[str]], int]:
    try:
        config = read_json_file(workflow_paths(Path(project_root))["config"])
    except (OSError, ValueError):
        config = {}
    commands = config.get("commands") if isinstance(config.get("commands"), dict) else {}
    prefixes = [
        _split(str(item)) for item in commands.get("verify_test_commands") or [] if str(item).strip()
    ]
    timeout = commands.get("verify_test_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or timeout <= 0:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return [p for p in prefixes if p], timeout


def _split(command: str) -> list[str]:
    """Tokens of a command line. Windows keeps backslashes (non-POSIX rules), whose tokens
    keep their surrounding quotes; those are stripped, so `"C:\\Program Files\\x.exe" -k`
    names the executable it quotes."""
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return []
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]


def read_request(project_root, session_id: str) -> dict | None:
    """The request as written, or None when there is none. Consumed on read."""
    path = request_path(project_root, session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"invalid": "tests.json is not valid JSON"}
    try:
        path.replace(path.with_name("tests.used.json"))
    except OSError:
        pass
    if not isinstance(data, dict):
        return {"invalid": "tests.json must be an object"}
    return data


def _allowed(tokens: list[str], prefixes: list[list[str]]) -> bool:
    return any(tokens[: len(prefix)] == prefix for prefix in prefixes)


def run_tests(project_root, request: dict) -> list[dict]:
    """One outcome per requested command: passed | failed | timeout | error | refused."""
    prefixes, timeout = _policy(project_root)
    outcomes: list[dict] = []
    commands = request.get("commands")
    if not isinstance(commands, list):
        return [{"command": "", "status": "refused", "detail": "`commands` must be a list"}]
    for raw in commands[:MAX_COMMANDS]:
        command = str(raw).strip()
        tokens = _split(command)
        if not command or not tokens:
            outcomes.append({"command": command, "status": "refused", "detail": "empty or unparseable"})
            continue
        if _SHELL_SYNTAX.search(command):
            outcomes.append({"command": command, "status": "refused", "detail": "shell syntax is not run"})
            continue
        if not _allowed(tokens, prefixes):
            outcomes.append({
                "command": command,
                "status": "refused",
                "detail": "not under commands.verify_test_commands in .workflow/config.json",
            })
            continue
        started = time.monotonic()
        try:
            done = subprocess.run(
                [osutil.resolve_exe(tokens[0]), *tokens[1:]],
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                **osutil.hidden_run_kwargs(),
            )
            status = "passed" if done.returncode == 0 else "failed"
            tail = ((done.stdout or "") + "\n" + (done.stderr or "")).strip()[-OUTPUT_TAIL_CHARS:]
            outcome = {"command": command, "status": status, "exit_code": done.returncode, "tail": tail}
        except subprocess.TimeoutExpired:
            outcome = {"command": command, "status": "timeout", "detail": f"over {timeout} s"}
        except OSError as exc:
            outcome = {"command": command, "status": "error", "detail": str(exc)[:200]}
        outcome["seconds"] = round(time.monotonic() - started, 1)
        if outcome.get("tail"):
            outcome["tail"] = redact(outcome["tail"])[0]
        outcomes.append(outcome)
    for raw in commands[MAX_COMMANDS:]:
        outcomes.append({"command": str(raw), "status": "refused", "detail": f"over {MAX_COMMANDS} commands"})
    return outcomes


def _findings(request: dict | None, outcomes: list[dict]) -> dict[str, list[str]]:
    """The lines to add, per [VERIFICATION] section."""
    add: dict[str, list[str]] = {"blocking_findings": [], "checks_run": [], "not_verified": []}
    if request is None:
        add["not_verified"].append(
            "tests: main_agent wrote no test request for this verify (verify/tests.json); no test ran"
        )
        return add
    if request.get("invalid"):
        add["not_verified"].append(f"tests: the test request was not usable ({request['invalid']}); no test ran")
        return add
    reason = str(request.get("reason") or "").strip() or "no reason given"
    if not outcomes:
        add["checks_run"].append(f"runtime: no test requested — {reason}")
        return add
    for outcome in outcomes:
        command = outcome["command"]
        status = outcome["status"]
        if status == "passed":
            add["checks_run"].append(f"runtime: `{command}` passed in {outcome['seconds']} s")
        elif status == "refused":
            add["not_verified"].append(f"tests: `{command}` was not run — {outcome['detail']}")
        else:
            detail = (
                f"exited {outcome['exit_code']}" if status == "failed" else outcome.get("detail") or status
            )
            tail = " ".join((outcome.get("tail") or "").split())[-300:]
            add["checks_run"].append(f"runtime: `{command}` {status} ({detail})")
            add["blocking_findings"].append(
                f"severity: high | origin: unknown | scope_relation: in_scope — runtime test "
                f"`{command}` {detail}" + (f" — tail: {tail}" if tail else "")
                + " — fix the failure or show it is unrelated, then re-verify"
            )
    return add


def _is_none(item: str) -> bool:
    from core.evidence.contract import _NONE_ITEM

    return _NONE_ITEM.fullmatch(item.lstrip("-").strip())


def _insert(lines: list[str], section: str, items: list[str]) -> list[str]:
    head = re.compile(rf"^\s*{re.escape(section)}\s*:(.*)$", re.IGNORECASE)
    start = next((i for i, line in enumerate(lines) if head.match(line)), None)
    new = [f"- {item}" for item in items]
    if start is None:
        return lines + ["", f"{section}:", *new]
    inline = head.match(lines[start]).group(1).strip()
    if inline and _is_none(inline):
        lines[start] = f"{section}:"
    out = lines[: start + 1] + new
    rest = lines[start + 1 :]
    # Drop "- none" entries of this section: it is no longer empty.
    i = 0
    while i < len(rest):
        stripped = rest[i].strip()
        if re.match(r"^[a-z_]+\s*:", stripped, re.IGNORECASE) or (stripped.startswith("[") and stripped.endswith("]")):
            break
        if stripped.startswith("-") and _is_none(stripped):
            rest.pop(i)
            continue
        i += 1
    return out + rest


def merge(content: str, request: dict | None, outcomes: list[dict]) -> str:
    """`content` with the runtime's test results written into its [VERIFICATION] block."""
    add = _findings(request, outcomes)
    marker = "[VERIFICATION]"
    before, sep, body = (content or "").partition(marker)
    if not sep:
        before, body = "", content or ""
    # Keep [DIGEST] and anything after it out of the section edits.
    body, digest_sep, tail = body.partition("[DIGEST]")
    lines = body.split("\n")
    for section in ("blocking_findings", "checks_run", "not_verified"):
        if add[section]:
            lines = _insert(lines, section, add[section])
    if add["blocking_findings"]:
        lines = [
            re.sub(r"^(\s*verdict\s*:).*$", r"\1 NEEDS FIX", line, flags=re.IGNORECASE)
            for line in lines
        ]
    merged = "\n".join(lines)
    return before + sep + merged + (digest_sep + tail if digest_sep else "")


def apply(project_root, session_id: str, result: dict) -> dict:
    """Run the requested tests and write their results into a delegated verify result."""
    request = read_request(project_root, session_id)
    outcomes = run_tests(project_root, request) if request and not request.get("invalid") else []
    result["content"] = merge(result.get("content") or "", request, outcomes)
    meta = result.setdefault("meta", {})
    meta["runtime_tests"] = {
        "requested": request is not None and not request.get("invalid"),
        "reason": (request or {}).get("reason"),
        "outcomes": [{k: v for k, v in o.items() if k != "tail"} for o in outcomes],
        "seconds": round(sum(o.get("seconds") or 0 for o in outcomes), 1),
    }
    return result
