"""End-to-end checks for agent-workflow.

Two tiers, because they cost very different things:

    python tools/e2e.py           # local only: no opencode, no quota, seconds
    python tools/e2e.py --full    # also runs delegated commands: minutes + real quota

`--full` is opt-in on purpose. A delegated command spends the same rate-limited budget
the workflow itself needs; a test suite that quietly burns it is worse than no suite.

Checks assert the CONTRACT, not prose: `ok`, the evidence markers, and the fields
`core/contract.py` declares required. A check that could not run is reported as SKIPPED
and never counted as a pass.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIPPED"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.rows.append((name, status, detail))
        print(f"  {status:8} {name}{'  — ' + detail if detail else ''}")

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.record(name, PASS if condition else FAIL, detail)
        return condition

    @property
    def failed(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == SKIP)


def _run_cli(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "main.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd or REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUTF8": "1"},
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _json_from(output: str) -> dict | None:
    start = output.find("{")
    if start < 0:
        return None
    try:
        return json.loads(output[start:])
    except json.JSONDecodeError:
        return None


def local_checks(report: Report) -> None:
    print("\n[LOCAL] no opencode, no quota")

    project = Path(tempfile.mkdtemp(prefix="e2e-project-"))
    (project / ".git").mkdir()
    try:
        code, out = _run_cli("--command", "init", "--work-dir", str(project), "--pretty")
        payload = _json_from(out) or {}
        report.check("init returns ok", payload.get("ok") is True, f"rc={code}")
        report.check("init creates .workflow", (project / ".workflow").is_dir())
        report.check(
            "init generates run scripts",
            any((project / ".workflow").glob("run.*")),
        )
        config_path = project / ".workflow" / "config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            from config.settings import TOOL_VERSION

            report.check(
                "config carries current version",
                config.get("version") == TOOL_VERSION,
                f"{config.get('version')} vs {TOOL_VERSION}",
            )
        else:
            report.record("config carries current version", SKIP, "config.json absent")

        code, out = _run_cli("--command", "doctor", "--work-dir", str(project), "--pretty")
        payload = _json_from(out) or {}
        report.check(
            "doctor reports a status",
            payload.get("meta", {}).get("status") in {"READY", "NEEDS SETUP"},
            payload.get("meta", {}).get("status", "?"),
        )

        code, out = _run_cli("--command", "inspect", "--work-dir", str(project), "--pretty")
        report.check("inspect returns ok", (_json_from(out) or {}).get("ok") is True)

        code, out = _run_cli("--command", "clean", "--work-dir", str(project), "--pretty")
        report.check("clean returns ok", (_json_from(out) or {}).get("ok") is True)
    finally:
        shutil.rmtree(project, ignore_errors=True)

    # The shipped example is what a fresh clone actually installs.
    from config.settings import load_opencode_config
    from core.router import Router

    example = REPO_ROOT / "config" / "opencode.example.json"
    if example.exists():
        routes = Router(load_opencode_config(example))
        models = [routes.route(c)["model"] for c in ("explore", "plan", "analyze")]
        report.check(
            "shipped example has no placeholder models",
            all(m is None or "provider_id" not in str(m) for m in models),
            str(models),
        )
    else:
        report.record("shipped example has no placeholder models", SKIP, "example missing")


def installer_checks(report: Report) -> None:
    """Install into a throwaway HOME. Installing onto the machine that produced dist/
    only ever proves idempotency — it never exercises the create path a new user hits."""
    print("\n[INSTALLER] against a throwaway HOME")

    if not (REPO_ROOT / "dist" / "manifest.json").exists():
        report.record("installer dry run", SKIP, "dist/ not extracted")
        return

    fake_home = Path(tempfile.mkdtemp(prefix="e2e-home-"))
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUTF8": "1",
           "USERPROFILE": str(fake_home), "HOME": str(fake_home)}
    try:
        dry = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        report.check("dry run exits clean", dry.returncode == 0, f"rc={dry.returncode}")
        report.check("dry run writes nothing", not (fake_home / ".claude").exists())
        report.check("dry run says so", "DRY RUN" in dry.stdout)

        applied = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py"), "--apply"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        report.check("apply exits clean", applied.returncode == 0, f"rc={applied.returncode}")

        claude_md = fake_home / ".claude" / "CLAUDE.md"
        report.check("CLAUDE.md installed", claude_md.exists())
        skills = list((fake_home / ".claude" / "skills").glob("*.md"))
        report.check("skills installed", len(skills) >= 10, f"{len(skills)} file(s)")
        settings = fake_home / ".claude" / "settings.json"
        report.check("settings.json installed", settings.exists())

        if settings.exists():
            try:
                loaded = json.loads(settings.read_text(encoding="utf-8"))
                report.check("settings.json is valid JSON", True, f"{len(loaded)} key(s)")
                hook_text = json.dumps(loaded.get("hooks", {}))
                report.check(
                    "hook path resolved to this HOME",
                    "{{HOME}}" not in hook_text,
                    "placeholder left unresolved" if "{{HOME}}" in hook_text else "",
                )
            except json.JSONDecodeError as exc:
                report.check("settings.json is valid JSON", False, str(exc))

        leftover = [
            str(p.relative_to(fake_home))
            for p in fake_home.rglob("*")
            if p.is_file() and "{{HOME}}" in p.read_text(encoding="utf-8", errors="ignore")
        ]
        report.check("no unresolved placeholders", not leftover, str(leftover[:3]))

        second = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py"), "--apply"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        report.check(
            "re-install is idempotent",
            "replace: " not in second.stdout and "create: " not in second.stdout,
            "second apply reported writes" if "create: " in second.stdout else "",
        )
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)


def delegated_checks(report: Report, session_id: str) -> None:
    """Real opencode calls. Minutes and real quota — only via --full."""
    print("\n[DELEGATED] real opencode — costs quota")
    from core.contract import REQUIRED_FIELDS

    script = REPO_ROOT / ".workflow" / ("run.ps1" if os.name == "nt" else "run.sh")
    if not script.exists():
        report.record("delegated commands", SKIP, "no .workflow/run script")
        return

    # sweep is local and returns a git-diff report, not delegated evidence.
    cases = [
        ("explore", "Sebutkan entry point utama. Maksimal 3 baris.", "evidence"),
        ("sweep", "scan git diff, identify impact", "local-report"),
    ]
    for command, task, contract in cases:
        runner = (["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
                  if os.name == "nt" else [str(script)])
        result = subprocess.run(
            [*runner, command, task, session_id],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(REPO_ROOT),
        )
        payload = _json_from(result.stdout or "")
        if payload is None:
            report.record(f"{command} returns JSON", FAIL, "no JSON in output")
            continue
        if not report.check(f"{command} returns ok", payload.get("ok") is True,
                            str(payload.get("meta", {}).get("error_type", ""))):
            continue
        content = (payload.get("content") or "").lower()

        if contract == "evidence":
            report.check(f"{command} carries evidence markers",
                         any(m in content for m in ("[evidence]", "[digest]", "grounded:")))
            for field in REQUIRED_FIELDS.get(command, ()):
                report.check(f"{command} has required field '{field}'", field in content)
        else:
            report.check(
                f"{command} reports changed files",
                any(k in content for k in ("changed", "diff", "modified", "no changes")),
                "local git report, not an evidence block",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-workflow end-to-end checks")
    parser.add_argument("--full", action="store_true",
                        help="also run delegated commands (minutes + real quota)")
    parser.add_argument("--session", default="e2e-session", help="session id for delegated runs")
    args = parser.parse_args()

    report = Report()
    local_checks(report)
    installer_checks(report)
    if args.full:
        delegated_checks(report, args.session)
    else:
        report.record("delegated commands", SKIP, "opt-in via --full")

    total = len(report.rows)
    passed = total - report.failed - report.skipped
    print("\n[E2E REPORT]")
    print(f"  {passed} passed | {report.failed} failed | {report.skipped} skipped "
          f"({total} checks)")
    if report.skipped:
        print("  skipped checks are NOT passes — see the reasons above")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
