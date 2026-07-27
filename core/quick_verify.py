"""Local, cheap verification for `/.verify` when commands.verify_mode is "syntax".

Answers one narrow question: do the files this session touched still parse, and do
they reference names that exist? It deliberately does NOT run the project's test
suite — that is the whole point of the "syntax" mode.

Anything it cannot check is reported as `not_checked` / `skipped`, never as a pass:
a silent gap here would be exactly the false-safety signal this command exists to avoid.
"""
import json
import shutil
import subprocess
from pathlib import Path

from core.workflow_runtime import now_iso
from utils import osutil

FILE_TIMEOUT_SECONDS = 20
# Generated bundles and dumps are read whole to be parsed; a multi-GB one would take
# the process down. Reported as not_checked rather than silently passed.
MAX_FILE_BYTES = 2 * 1024 * 1024

# Build artifacts and vendored trees: never the subject of a verification.
_IGNORED_PARTS = {"__pycache__", "node_modules", ".git", "vendor", ".venv", "venv"}

# extension -> (language, argv builder). argv is run with cwd=project_root, no shell.
# Python is checked in-process instead: `py_compile` would drop .pyc files into the
# user's tree, and a verification command must not leave artifacts behind.
_SYNTAX_CHECKS: dict[str, tuple[str, object]] = {
    ".js": ("node", lambda rel: ["node", "--check", rel]),
    ".mjs": ("node", lambda rel: ["node", "--check", rel]),
    ".cjs": ("node", lambda rel: ["node", "--check", rel]),
    ".php": ("php", lambda rel: ["php", "-l", rel]),
}

# Syntax checkers prove the file parses; they do not catch a misspelled name.
# These optional linters do — when the toolchain happens to be present.
_NAME_CHECKS: dict[str, tuple[str, object]] = {
    ".py": ("pyflakes", lambda rel: ["pyflakes", rel]),
}


def _run(argv: list[str], project_root: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=FILE_TIMEOUT_SECONDS,
            check=False,
            **osutil.hidden_run_kwargs(),  # Windows: no console flash per checker/git call
        )
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {FILE_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return 127, str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def _git_lines(argv: list[str], project_root: Path) -> list[str]:
    code, output = _run(argv, project_root)
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def changed_files(project_root: Path) -> list[str]:
    """Tracked modifications plus untracked new files, relative to project_root.

    Untracked files are included so newly created files are verified too.
    """
    tracked = _git_lines(["git", "diff", "--name-only", "HEAD"], project_root)
    if not tracked:
        tracked = _git_lines(["git", "diff", "--name-only"], project_root)
    untracked = _git_lines(
        ["git", "ls-files", "--others", "--exclude-standard"], project_root
    )
    seen: list[str] = []
    for rel in [*tracked, *untracked]:
        if rel in seen or not (project_root / rel).is_file():
            continue
        if _IGNORED_PARTS & set(Path(rel).parts):
            continue
        seen.append(rel)
    return seen


def _check_json(project_root: Path, rel: str) -> tuple[bool, str]:
    try:
        json.loads((project_root / rel).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, str(exc)
    return True, ""


def _check_python(project_root: Path, rel: str) -> tuple[bool, str]:
    """Parse-only, in-process: no bytecode written next to the user's source."""
    try:
        source = (project_root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        return False, str(exc)
    try:
        compile(source, rel, "exec")
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"
    except ValueError as exc:  # e.g. source containing null bytes
        return False, str(exc)
    return True, ""


def verify_files(project_root: Path, files: list[str]) -> dict:
    passed: list[str] = []
    failed: list[dict] = []
    not_checked: list[dict] = []
    skipped: list[dict] = []
    name_findings: list[dict] = []

    for rel in files:
        suffix = Path(rel).suffix.lower()

        try:
            size = (project_root / rel).stat().st_size
        except OSError as exc:
            not_checked.append({"file": rel, "reason": f"unreadable: {exc}"})
            continue
        if size > MAX_FILE_BYTES:
            not_checked.append(
                {"file": rel, "reason": f"{size} bytes exceeds {MAX_FILE_BYTES}-byte check limit"}
            )
            continue

        if suffix in (".json", ".py"):
            check = _check_json if suffix == ".json" else _check_python
            ok, detail = check(project_root, rel)
            if not ok:
                failed.append({"file": rel, "check": f"{suffix.lstrip('.')} syntax", "detail": detail})
                continue
            passed.append(rel)
        else:
            entry = _SYNTAX_CHECKS.get(suffix)
            if entry is None:
                not_checked.append(
                    {"file": rel, "reason": f"no syntax checker for '{suffix or 'no extension'}'"}
                )
                continue

            language, build_argv = entry
            argv = build_argv(rel)
            if shutil.which(argv[0]) is None:
                skipped.append({"file": rel, "reason": f"{language} toolchain not on PATH"})
                continue

            code, output = _run(argv, project_root)
            if code != 0:
                failed.append({"file": rel, "check": f"{language} syntax", "detail": output[:600]})
                continue
            passed.append(rel)

        name_entry = _NAME_CHECKS.get(suffix)
        if name_entry is None:
            continue
        tool, build_name_argv = name_entry
        if shutil.which(tool) is None:
            continue  # optional; absence is reported once in meta, not per file
        code, output = _run(build_name_argv(rel), project_root)
        if code != 0 and output:
            name_findings.append({"file": rel, "tool": tool, "detail": output[:600]})

    return {
        "passed": passed,
        "failed": failed,
        "not_checked": not_checked,
        "skipped": skipped,
        "name_findings": name_findings,
        "name_check_available": shutil.which("pyflakes") is not None,
    }


def run(project_root: Path, session_id: str | None = None) -> dict:
    """Contract-shaped result, mirroring a delegated call so callers stay uniform."""
    files = changed_files(project_root)
    if not files:
        return {
            "ok": True,
            "content": (
                "[QUICK VERIFY]\n"
                "verdict: skipped\n"
                "reason: no changed or untracked files detected\n"
                "note: verify_mode=syntax — no test suite was run"
            ),
            "meta": {
                "mode": "quick",
                "verdict": "skipped",
                "checked_at": now_iso(),
                "project_root": str(project_root),
                "session_id": session_id,
            },
        }

    report = verify_files(project_root, files)
    if report["failed"]:
        verdict = "fail"
    elif report["not_checked"]:
        # Changed files we could not check (unsupported type, vanished, unreadable) are a
        # verification GAP, not a clean bill of health. Calling them "pass" would be a false
        # all-clear — the whole point of honest verify is that a gap is not a pass.
        verdict = "incomplete"
    else:
        verdict = "pass"

    lines = [
        "[QUICK VERIFY]",
        f"verdict: {verdict}",
        "mode: local syntax/name check only — verify_mode=syntax, no test suite run",
        f"files_considered: {len(files)}",
        f"passed: {len(report['passed'])}",
    ]
    if report["failed"]:
        lines.append("failed:")
        lines.extend(f"- {item['file']} [{item['check']}] {item['detail']}" for item in report["failed"])
    if report["name_findings"]:
        lines.append("name_findings:")
        lines.extend(f"- {item['file']} [{item['tool']}] {item['detail']}" for item in report["name_findings"])
    if report["not_checked"]:
        lines.append("not_checked:")
        lines.extend(f"- {item['file']} — {item['reason']}" for item in report["not_checked"])
    if report["skipped"]:
        lines.append("skipped:")
        lines.extend(f"- {item['file']} — {item['reason']}" for item in report["skipped"])
    if not report["name_check_available"]:
        lines.append("name_check: unavailable (pyflakes not installed) — syntax only")
    lines.append(
        "coverage: syntax/parse level. Runtime behaviour, tests and the exact user case are NOT covered."
    )

    return {
        "ok": True,
        "content": "\n".join(lines),
        "meta": {
            "mode": "quick",
            "verdict": verdict,
            "checked_at": now_iso(),
            "project_root": str(project_root),
            "session_id": session_id,
            "quick_verify": report,
        },
    }
