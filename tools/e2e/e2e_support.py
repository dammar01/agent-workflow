"""Shared staging report, dependency identity and bounded process launch."""

from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, SKIP, NOT_RUN = "PASS", "FAIL", "SKIPPED", "NOT_RUN"
SETUP_TIMEOUT = 120.0
DELEGATED_TIMEOUT = 900.0
PROVIDER_STAGES = ("git-init", "init", "provider", "explore", "sweep")


@contextmanager
def staging_environment():
    """Isolate runtime state and host interpreter pins; imports change nothing."""
    saved = {name: os.environ.get(name) for name in ("EXOMA_HOME", "AGENT_HOME", "PYTHONUTF8")}
    with tempfile.TemporaryDirectory(prefix="exoma-staging-home-") as home:
        os.environ.update(EXOMA_HOME=home, AGENT_HOME=home, PYTHONUTF8="1")
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.required: list[str] = []
        self.stages: dict[str, dict] = {}
        self.identity: dict = {}

    def require(self, *names: str) -> None:
        for name in names:
            if name not in self.required:
                self.required.append(name)
                self.stages[name] = {"status": NOT_RUN, "executed": False}

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.rows.append((name, status, detail))
        print(f"  {status:8} {name}{'  — ' + detail if detail else ''}")

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.record(name, PASS if condition else FAIL, detail)
        return condition

    def stage(
        self, name: str, status: str, *, exit_code: int | None = None,
        timed_out: bool = False, error_type: str = "", executed: bool = True,
    ) -> None:
        self.stages[name] = {
            "status": status, "executed": executed, "exit_code": exit_code,
            "timed_out": timed_out, "error_type": error_type,
        }
        self.record(name, status, error_type)

    def complete_required(self) -> None:
        for name in self.required:
            if self.stages[name]["status"] in (NOT_RUN, SKIP):
                self.record(f"required stage {name}", FAIL, "required_stage_not_passed")

    @property
    def failed(self) -> int:
        return sum(status == FAIL for _, status, _ in self.rows)

    @property
    def skipped(self) -> int:
        return sum(status == SKIP for _, status, _ in self.rows)

    def receipt(self, *, full: bool, requests: list, limits: dict) -> dict:
        accepted = bool(self.required) and all(
            self.stages[name]["status"] == PASS for name in self.required
        )
        verdict = ("pass" if accepted and not self.failed else "fail") if full else "not_run"
        return {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "mode": "full" if full else "informational",
            "requests": [{"provider": provider, "model": model} for provider, model in requests],
            "identity": self.identity,
            "limits_seconds": limits,
            "required_stages": self.required,
            "stages": self.stages,
            "checks": [{"name": name, "status": status, "detail": detail} for name, status, detail in self.rows],
            "coverage": {
                "delegated_commands": ["explore"] if any(
                    name.endswith(": explore") and row["status"] == PASS
                    for name, row in self.stages.items()
                ) else [],
                "local_commands": ["sweep"] if any(
                    name.endswith(": sweep") and row["status"] == PASS
                    for name, row in self.stages.items()
                ) else [],
                "plan_approval_execute_verify": "not_run",
                "deployed_hooks": "not_verified",
                "scientific_validation": "not_assessed",
                "command_lists": "Accepted stages only; failed or timed-out attempts remain in stages.",
                "identity_limit": "Git pin and installed metadata, not byte-for-byte package or hook verification.",
                "cleanup_limit": "Owned process tree only; detached runtime jobs are not independently verified.",
            },
        }


def _installed_identity() -> dict:
    import exoma

    distribution = metadata.distribution("exoma")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    return {
        "installed_pin": direct.get("vcs_info", {}).get("commit_id"),
        "installed_version": distribution.version,
        "package_path": str(Path(exoma.__file__).resolve()),
        "distribution_path": str(Path(distribution.locate_file("exoma")).resolve()),
    }


def dependency_identity(root: Path = REPO_ROOT) -> dict:
    """Require manifest, lock and installed Git metadata to name the same source."""
    identity = {"interpreter": sys.executable, "verified": False}
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        pins = [
            match.group(1).lower()
            for dependency in project["project"]["dependencies"]
            if (match := re.fullmatch(r"exoma\s*@\s*git\+.+@([0-9a-fA-F]{40})", dependency))
        ]
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        locked = [
            package["source"]["git"].rsplit("#", 1)[-1].lower()
            for package in lock["package"] if package["name"] == "exoma"
        ]
        if len(pins) != 1 or len(locked) != 1:
            raise ValueError("ambiguous_dependency_pin")
        identity.update(expected_pin=pins[0], locked_pin=locked[0])
        identity.update(_installed_identity())
        identity["verified"] = (
            pins[0] == locked[0] == identity.get("installed_pin")
            and Path(identity["package_path"]) == (Path(identity["distribution_path"]) / "__init__.py").resolve()
        )
        if not identity["verified"]:
            identity["error_type"] = "dependency_identity_mismatch"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, metadata.PackageNotFoundError):
        identity["error_type"] = "dependency_identity_unavailable"
    return identity


def _run_process(
    args: list[str], *, timeout: float, cwd: Path | None = None,
) -> tuple[int | None, str, str]:
    from exoma.utils.osutil import run_bounded

    try:
        return run_bounded(args, timeout=timeout, cwd=str(cwd or REPO_ROOT), drain_seconds=1)
    except OSError:
        # Do not publish raw child output or inherited credentials in failure details.
        return 127, "", "launch_error"


def _run_cli(
    *args: str, cwd: Path | None = None, timeout: float = SETUP_TIMEOUT,
) -> tuple[int | None, str, str]:
    # Parent -I does not remove PYTHONPATH from the inherited child environment.
    # Isolate every CLI launch so init generates a runner from the verified install.
    return _run_process(
        [sys.executable, "-I", "-X", "utf8", "-m", "exoma", *args], cwd=cwd, timeout=timeout,
    )


def _json_from(output: str) -> dict | None:
    """Accept a top-level object, optionally after non-JSON log lines."""
    stripped = output.lstrip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        if stripped.startswith("{"):
            return None
        lines = stripped.splitlines()
        for index, line in enumerate(lines):
            if line.lstrip().startswith("{"):
                try:
                    value = json.loads("\n".join(lines[index:]))
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
            # Permit bracketed log labels, never a nested object borrowed from an array.
            if line.lstrip().startswith(("[", '"')) and not re.match(r"^\s*\[[A-Za-z][\w -]*\](?:\s|$)", line):
                return None
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    pass
                else:
                    return None
        return None
    return value if isinstance(value, dict) else None
