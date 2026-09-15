"""The per-invocation request behind /.verify-browser.

The skill interviews the user, then writes one JSON file for the session:

  .workflow/sessions/<session>/e2e/request.json
  {"version": 1, "phase": "draft" | "run", "settings": {...},
   "scenario": {...}, "existing_tests": [...], "spec_notes": [...]}

`draft` asks second_agent for a spec and stops before any browser starts; `run` executes
the scenario the user confirmed. Nothing is read from config.json: every knob has a
default below and is overridden per run by `settings`, so two sessions verifying two apps
on one project never share a setting. Hybrid review is not a knob — it always runs.

Credentials never travel in the request. `${NAME}` placeholders resolve from one profile of
`.workflow/e2e/secrets.json`, then from the process environment:

  {"default": "qa", "profiles": {"qa": {"E2E_USER": "...", "E2E_PASS": "..."},
                                 "admin": {"E2E_USER": "...", "E2E_PASS": "..."}}}

The request picks a profile by name (`settings.secrets_profile`), never by value, so trying
another account is another run with another name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from core.workspace.workspace_paths import workflow_paths

REQUEST_VERSION = 1
PHASES = ("draft", "run")
SECRETS_FILE = "secrets.json"
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SECRET_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
        # POSTs that only READ (a search, a filter, a GraphQL query), confirmed by the user
        # from the draft's read_only_requests: "POST /api/search" or "POST http(s)://host/path".
        # Exact endpoint, like allowed_mutation_paths, but reported as reads rather than
        # writes, and a GraphQL body that asks for a mutation is still refused.
        "allowed_read_only_requests": [],
        # which secrets.json profile fills ${NAME}; empty = the file's `default`, or its only
        # profile. A name, never a value.
        "secrets_profile": "",
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
    errors += read_only_request_errors(settings["allowed_read_only_requests"])
    if settings["secrets_profile"] and not _PROFILE_NAME.match(settings["secrets_profile"]):
        errors.append("settings.secrets_profile: a profile name, [A-Za-z0-9_-], at most 64 characters")
    return settings, errors


MAX_MUTATION_PATHS = 20
MAX_READ_ONLY_REQUESTS = 20
# A read that has to travel as a request body is a POST. PUT/PATCH/DELETE are writes by
# their own definition, so no entry may name them.
READ_ONLY_METHODS = ("POST",)


def endpoint_error(entry: str) -> str | None:
    """Why `entry` is not an exact endpoint (`/path` or `http(s)://host/path`), or None."""
    if any(mark in entry for mark in ("*", "?", "#")) or any(ch.isspace() for ch in entry):
        return "exact path only, no wildcard, query, fragment or whitespace"
    if entry.startswith("/"):
        return "'//' is a scheme-relative URL, write the full http(s) URL" if entry.startswith("//") else None
    parts = urlsplit(entry)
    if parts.scheme not in ("http", "https") or not parts.netloc or not parts.path.startswith("/"):
        return "a path starting with '/' or a full http(s)://host/path URL"
    return None


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
        problem = endpoint_error(entry)
        if problem:
            errors.append(f"{where}: {problem}")
    return errors


def read_only_request_target(entry: str) -> tuple[str, str] | None:
    """`"POST /api/search"` -> ("POST", "/api/search"); None when it is not that shape."""
    method, _, target = entry.strip().partition(" ")
    target = target.strip()
    if method not in READ_ONLY_METHODS or not target:
        return None
    return method, target


def read_only_request_errors(entries: list, where_root: str = "settings.allowed_read_only_requests") -> list[str]:
    """Shape of read-only request entries: `<METHOD> <exact endpoint>`, METHOD POST."""
    errors: list[str] = []
    if len(entries) > MAX_READ_ONLY_REQUESTS:
        errors.append(f"{where_root}: at most {MAX_READ_ONLY_REQUESTS} entries")
    for index, entry in enumerate(entries):
        where = f"{where_root}[{index}]"
        if not isinstance(entry, str) or not entry.strip():
            errors.append(f"{where}: must be a non-empty string")
            continue
        parsed = read_only_request_target(entry)
        if parsed is None:
            errors.append(f"{where}: '<METHOD> <path or URL>' with METHOD one of {', '.join(READ_ONLY_METHODS)}")
            continue
        problem = endpoint_error(parsed[1])
        if problem:
            errors.append(f"{where}: {problem}")
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


def load_secrets(project_root: Path, profile: str = "") -> tuple[dict[str, str], list[str], dict]:
    """(values of the selected profile, errors, info). A missing file is no secrets, not an error.

    Errors name a location — a profile, a key, a line — and never a value: a malformed
    entry may still hold the password. An empty string is an unfilled template slot, so it
    is dropped rather than resolved to an empty credential. `info` carries the path, the
    profile names and the one selected; values never enter it.
    """
    path = secrets_path(project_root)
    info: dict = {"file": str(path), "exists": False, "profile": None, "profiles": []}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, [], info
    except OSError as exc:
        return {}, [f"{SECRETS_FILE}: unreadable ({type(exc).__name__})"], info
    info["exists"] = True
    try:
        data = json.loads(text)
    except ValueError as exc:
        return {}, [f"{SECRETS_FILE}: not valid JSON (line {getattr(exc, 'lineno', '?')})"], info
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        return {}, [f'{SECRETS_FILE}: expected {{"default": "<profile>", "profiles": {{"<profile>": {{"KEY": "value"}}}}}}'], info

    profiles = data["profiles"]
    errors: list[str] = []
    for name, entries in profiles.items():
        if not _PROFILE_NAME.match(str(name)):
            errors.append(f"{SECRETS_FILE}: profile #{list(profiles).index(name) + 1}: name must match [A-Za-z0-9_-]")
            continue
        if not isinstance(entries, dict):
            errors.append(f"{SECRETS_FILE}: profiles.{name}: must be an object of KEY: value")
            continue
        for position, (key, value) in enumerate(entries.items(), 1):
            if not _SECRET_KEY.match(str(key)):
                # The key itself is not echoed: a value pasted into the key slot is the
                # mistake this message exists to report.
                errors.append(f"{SECRETS_FILE}: profiles.{name}: entry #{position}: key must match [A-Za-z_][A-Za-z0-9_]*")
            elif not isinstance(value, str):
                errors.append(f"{SECRETS_FILE}: profiles.{name}.{key}: must be a string")
    info["profiles"] = [str(name) for name in profiles]
    default = data.get("default")
    if default is not None and (not isinstance(default, str) or default not in profiles):
        errors.append(f"{SECRETS_FILE}: default names no profile in profiles")
    if errors:
        return {}, errors, info

    chosen = profile or default or (next(iter(profiles)) if len(profiles) == 1 else None)
    if profile and profile not in profiles:
        return {}, [f"settings.secrets_profile: {SECRETS_FILE} has no profile '{profile}' (has: {', '.join(info['profiles']) or 'none'})"], info
    if chosen is None:
        if profiles:
            return {}, [f"{SECRETS_FILE}: {len(profiles)} profiles and no default; set settings.secrets_profile"], info
        return {}, [], info
    info["profile"] = chosen
    return {key: value for key, value in profiles[chosen].items() if value != ""}, [], info


def ensure_secrets_template(project_root: Path, names: list[str], profile: str = "") -> bool:
    """Create secrets.json with empty slots for `names` when it does not exist. True if created.

    The user fills the values; nothing here ever writes one. An existing file is never
    touched, not even to add a missing key: it is the user's, and a rewrite is how a
    hand-kept file loses what it held.
    """
    path = secrets_path(project_root)
    if not names or path.exists():
        return False
    slot = profile or "default"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"default": slot, "profiles": {slot: {name: "" for name in names}}}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return True
