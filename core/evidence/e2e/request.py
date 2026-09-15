"""The per-invocation request behind /.verify-browser.

The skill interviews the user, then writes one JSON file for the session:

  .workflow/sessions/<session>/e2e/request.json
  {"version": 1, "phase": "draft" | "run", "settings": {...},
   "scenario": {...}, "existing_tests": [...], "spec_notes": [...]}

`draft` asks second_agent for a spec and stops before any browser starts; `run` executes
the scenario the user confirmed. Nothing is read from config.json: every knob has a
default below and is overridden per run by `settings`, so two sessions verifying two apps
on one project never share a setting. Hybrid review is not a knob — it always runs.

Credentials never travel in the request. `${NAME}` placeholders resolve from
`.workflow/e2e/secrets.env` (KEY=VALUE lines), then from the process environment.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from core.workspace.workspace_paths import workflow_paths

REQUEST_VERSION = 1
PHASES = ("draft", "run")
SECRETS_FILE = "secrets.env"
_SECRET_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def default_settings() -> dict:
    """Smoke-test starting points, not calibrated limits (plan §19)."""
    return {
        "base_url": "http://localhost:8000",
        "browser": "chromium",
        "headless": True,
        # pause after every Playwright action so a headed run can be watched; 0 = full
        # speed. Clamped to 0..5000 ms, and the pauses count against total_timeout_s.
        "slow_mo_ms": 0,
        "nav_timeout_ms": 15000,
        "step_timeout_ms": 8000,
        "idle_timeout_s": 30,
        "total_timeout_s": 300,
        "probe_max_elements": 200,
        # a non-loopback base_url (a virtual host such as http://app.test) needs both.
        "allow_remote": False,
        "allowed_origins": [],
        # steps that declare side_effect (creates/modifies/deletes test data) are refused
        # unless this is true — plan §18: data-changing actions need explicit opt-in.
        "allow_side_effects": False,
        # while allow_side_effects is false the player aborts every request whose method is
        # not GET/HEAD/OPTIONS, on any origin. These are the exceptions, typically a login
        # form: "/login" (path on base_url's origin) or "http://host:port/path" (any other
        # origin). Matched on scheme, host, port and exact path; the query is ignored.
        "allowed_mutation_paths": [],
        "fail_on_console_error": False,
        # size budget for one run's player artifacts (trace, screenshots, HTML); the
        # heaviest class is pruned first.
        "artifact_max_mb": 25,
        # the project's own E2E tests (plan §8). Run only through this argv template,
        # without a shell; {files} expands to the allow-listed test files, {base_url} to
        # base_url. Empty: listed tests are recorded, never executed.
        "existing_test_command": [],
        "existing_test_allowlist": [],
        "existing_test_timeout_s": 300,
    }


def request_path(project_root: Path, session_id: str) -> Path:
    return workflow_paths(project_root, session_id)["session_dir"] / "e2e" / "request.json"


def draft_path(project_root: Path, session_id: str) -> Path:
    return workflow_paths(project_root, session_id)["session_dir"] / "e2e" / "draft.json"


def secrets_path(project_root: Path) -> Path:
    return workflow_paths(project_root)["workflow_dir"] / "e2e" / SECRETS_FILE


def settings_from(overrides: object) -> tuple[dict, list[str]]:
    """Defaults with the request's overrides applied. A wrong key or type is an error,
    not a silent fallback: the user confirmed these values, so a typo must stop the run."""
    settings = default_settings()
    if overrides is None:
        return settings, []
    if not isinstance(overrides, dict):
        return settings, ["settings must be an object"]
    errors: list[str] = []
    for key, value in overrides.items():
        if key not in settings:
            errors.append(f"settings.{key}: unknown key")
            continue
        fallback = settings[key]
        if isinstance(fallback, bool):
            acceptable = isinstance(value, bool)
        elif isinstance(fallback, int):
            acceptable = isinstance(value, int) and not isinstance(value, bool)
        else:
            acceptable = isinstance(value, type(fallback))
        if not acceptable:
            errors.append(f"settings.{key}: {type(value).__name__}, expected {type(fallback).__name__}")
            continue
        settings[key] = value
    errors += mutation_path_errors(settings["allowed_mutation_paths"])
    return settings, errors


MAX_MUTATION_PATHS = 20


def mutation_path_errors(entries: list) -> list[str]:
    """Shape of settings.allowed_mutation_paths. Exact paths only: a wildcard or a query
    would let one confirmed login entry quietly cover every write under it."""
    errors: list[str] = []
    if len(entries) > MAX_MUTATION_PATHS:
        errors.append(f"settings.allowed_mutation_paths: at most {MAX_MUTATION_PATHS} entries")
    for index, entry in enumerate(entries):
        where = f"settings.allowed_mutation_paths[{index}]"
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{where}: must be a non-empty string")
            continue
        if any(mark in entry for mark in ("*", "?", "#")) or any(ch.isspace() for ch in entry):
            errors.append(f"{where}: exact path only, no wildcard, query, fragment or whitespace")
            continue
        if entry.startswith("/"):
            if entry.startswith("//"):
                errors.append(f"{where}: '//' is a scheme-relative URL, write the full http(s) URL")
            continue
        parts = urlsplit(entry)
        if parts.scheme not in ("http", "https") or not parts.netloc or not parts.path.startswith("/"):
            errors.append(f"{where}: a path starting with '/' or a full http(s)://host/path URL")
    return errors


def load_request(project_root: Path, session_id: str) -> tuple[dict | None, str | None, list[str]]:
    """(request, reason, errors). reason is `request_missing` or `request_invalid`."""
    path = request_path(project_root, session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "request_missing", [f"{path} does not exist; /.verify-browser writes it before calling the runtime"]
    except (OSError, ValueError) as exc:
        return None, "request_invalid", [f"{path}: {type(exc).__name__}: {exc}"]
    if not isinstance(data, dict):
        return None, "request_invalid", ["request top level is not an object"]
    errors: list[str] = []
    if data.get("version") != REQUEST_VERSION:
        errors.append(f"version must be {REQUEST_VERSION}")
    phase = data.get("phase")
    if phase not in PHASES:
        errors.append(f"phase must be one of: {', '.join(PHASES)}")
    settings, setting_errors = settings_from(data.get("settings"))
    errors += setting_errors
    if phase == "run" and not isinstance(data.get("scenario"), dict):
        errors.append("phase run needs the confirmed scenario object")
    for key in ("existing_tests", "spec_notes"):
        if key in data and not isinstance(data[key], list):
            errors.append(f"{key} must be a list")
    if errors:
        return None, "request_invalid", errors
    return (
        {
            "phase": phase,
            "settings": settings,
            "scenario": data.get("scenario"),
            "existing_tests": data.get("existing_tests") or [],
            "spec_notes": data.get("spec_notes") or [],
            "path": str(path),
        },
        None,
        [],
    )


def load_secrets(project_root: Path) -> tuple[dict[str, str], list[str]]:
    """KEY=VALUE pairs from secrets.env; a missing file is no secrets, not an error.

    Errors name the line number only — a malformed line may still hold the value.
    """
    path = secrets_path(project_root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, []
    except OSError as exc:
        return {}, [f"{SECRETS_FILE}: unreadable ({type(exc).__name__})"]
    values: dict[str, str] = {}
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SECRET_LINE.match(line)
        if not match:
            errors.append(f"{SECRETS_FILE}:{number}: not a KEY=VALUE line")
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values, errors
