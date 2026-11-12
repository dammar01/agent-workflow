"""Real delegated calls. Opt-in: spends provider quota."""

from pathlib import Path
import tempfile

from tools.e2e.e2e_support import (
    DELEGATED_TIMEOUT, FAIL, PASS, SETUP_TIMEOUT, Report,
    _json_from, _run_cli, _run_process,
)


def find_bash():
    from exoma.utils.osutil import find_bash as locate
    return locate()


def _process_ok(report: Report, name: str, code: int | None) -> bool:
    if code == 0:
        return True
    report.stage(
        name, FAIL, exit_code=code, timed_out=code is None,
        error_type="timeout" if code is None else "process_failed",
    )
    return False


def delegated_checks(
    report: Report, session_id: str, provider: str, model: str | None, *,
    setup_timeout: float = SETUP_TIMEOUT, delegated_timeout: float = DELEGATED_TIMEOUT,
) -> None:
    """Run required stages; stdout alone never overrides a failed process."""
    from exoma.core.evidence.contract import REQUIRED_FIELDS

    tag = f"{provider}: "
    if not model:
        report.stage(tag + "provider", FAIL, executed=False, error_type="model_missing")
        return

    with tempfile.TemporaryDirectory(prefix="e2e-delegated-project-") as directory:
        project = Path(directory)
        name = tag + "git-init"
        code, _, _ = _run_process(["git", "init", "-q", str(project)], timeout=setup_timeout)
        if not _process_ok(report, name, code):
            return
        report.stage(name, PASS, exit_code=code)
        (project / "app.py").write_text("def main():\n    return 'ready'\n", encoding="utf-8")

        name = tag + "init"
        code, output, _ = _run_cli(
            "--command", "init", "--work-dir", str(project), "--pretty",
            timeout=setup_timeout,
        )
        if not _process_ok(report, name, code):
            return
        script = project / ".workflow" / "run.sh"
        initialized = _json_from(output)
        if not script.is_file() or not initialized or initialized.get("ok") is not True:
            report.stage(name, FAIL, exit_code=code, error_type="runner_or_init_payload_missing")
            return
        report.stage(name, PASS, exit_code=code)

        name = tag + "provider"
        code, output, _ = _run_cli(
            "--command", "provider", "--prompt", f"{provider}|{model}",
            "--work-dir", str(project), "--pretty", timeout=setup_timeout,
        )
        if not _process_ok(report, name, code):
            return
        selected = _json_from(output)
        if not selected or selected.get("ok") is not True:
            report.stage(name, FAIL, exit_code=code, error_type="provider_selection_failed")
            return
        report.stage(name, PASS, exit_code=code)

        bash = find_bash()
        if not bash:
            report.stage(tag + "explore", FAIL, executed=False, error_type="bash_missing")
            return

        cases = (
            ("explore", "Sebutkan entry point utama. Maksimal 3 baris.", "evidence"),
            ("sweep", "scan git diff, identify impact", "local-report"),
        )
        for command, task, contract in cases:
            name = tag + command
            code, stdout, _ = _run_process(
                [bash, str(script), command, task, session_id],
                timeout=delegated_timeout, cwd=project,
            )
            if not _process_ok(report, name, code):
                continue
            payload = _json_from(stdout)
            if payload is None or payload.get("ok") is not True:
                report.stage(name, FAIL, exit_code=code, error_type="invalid_or_failed_payload")
                continue
            content = payload.get("content")
            if not isinstance(content, str):
                report.stage(name, FAIL, exit_code=code, error_type="content_not_text")
                continue
            content = content.lower()
            checks = []
            if contract == "evidence":
                reused = payload.get("evidence_ref")
                checks.append(report.check(
                    name + " is not reused evidence",
                    not (isinstance(reused, dict) and reused.get("reused") is True),
                ))
                checks.append(report.check(
                    name + " carries evidence markers",
                    any(marker in content for marker in ("[evidence]", "[digest]", "grounded:")),
                ))
                for field in REQUIRED_FIELDS.get(command, ()):
                    checks.append(report.check(name + f" has required field '{field}'", field in content))
            else:
                checks.append(report.check(
                    name + " reports changed files",
                    any(word in content for word in ("changed", "diff", "modified", "no changes")),
                    "local report; does not establish provider execution",
                ))
            report.stage(
                name, PASS if all(checks) else FAIL, exit_code=code,
                error_type="" if all(checks) else "contract_not_met",
            )
