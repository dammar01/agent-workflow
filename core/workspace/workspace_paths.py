"""Workspace paths, constants, and the atomic JSON/text primitives.

Bottom layer of the runtime: imports nothing from core, so every other
split module can depend on it without closing a cycle."""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import TOOL_VERSION
from utils import osutil
from utils.path_guard import safe_path_component


def _safe_component(value: str) -> str:
    return safe_path_component(value)


WORKFLOW_DIRNAME = ".workflow"
# The workflow's own tuning for whichever second_agent is selected. Named after the
# role, not the vendor: `.workflow/opencode.json` was the v3.4.2 name and is migrated
# on upgrade. Distinct from OpenCode's own config files (~/.config/opencode/opencode.json
# and <project_root>/opencode.json), which keep their vendor names.
PROVIDER_CONFIG_NAME = "second_agent.json"
# The v3.4.2 name. Kept as a constant rather than a literal because the last rename left
# one reader still spelling it by hand, and a project whose file no longer matched fell
# through to the tool defaults without a word — see resolve_provider_config below.
LEGACY_PROVIDER_CONFIG_NAME = "opencode.json"
LOCK_TTL_SECONDS = 300
JSON_INDENT = 2
ARCHIVE_KEEP = 20
# Derived, not restated. `tools/stamp_version.py` makes TOOL_VERSION the single source for
# every version string that ships, but this one was a hand-maintained literal outside its
# TARGETS — guarded only by an e2e assertion, which reports the drift after it exists
# rather than preventing it. The config schema is versioned in lockstep with the tool, so
# the two numbers were never independent; only their maintenance was.
CONFIG_VERSION = TOOL_VERSION


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}."
        f"{secrets.token_hex(8)}.tmp"
    )
    try:
        temp.write_text(content, encoding=encoding)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_text(path, json.dumps(payload, indent=JSON_INDENT))


def detect_project_root(work_dir: str | None = None) -> Path:
    start = Path(work_dir).resolve() if work_dir else Path.cwd().resolve()
    current = start

    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def slugify_project_name(name: str) -> str:
    safe = []
    for char in name.lower():
        safe.append(char if char.isalnum() else "-")
    slug = "".join(safe).strip("-")
    return slug or "project"


def workflow_paths(
    project_root: Path, session_id: str | None = None
) -> dict[str, Path]:
    """Resolve workflow paths. Mutable per-flow state (state/scope/cache/runtime)
    lives under sessions/<sid>/ so concurrent main agents on the SAME project never
    clobber each other; static config/logs/reports stay shared at the .workflow root.
    session_id=None → legacy root fallback (init scaffolding / no-session tooling)."""
    workflow_dir = project_root / WORKFLOW_DIRNAME
    reports_dir = workflow_dir / "reports"
    session_dir = (
        workflow_dir / "sessions" / _safe_component(session_id)
        if session_id
        else workflow_dir
    )
    runtime_dir = session_dir / "runtime"
    logs_dir = (session_dir / "logs") if session_id else (workflow_dir / "logs")
    sweep_report = (
        session_dir / "reports" / "sweep.last.md"
        if session_id
        else reports_dir / "sweep.last.md"
    )
    return {
        "project_root": project_root,
        "workflow_dir": workflow_dir,
        "config": workflow_dir / "config.json",
        "session_dir": session_dir,
        "state": session_dir / "state.json",
        "scope": session_dir / "scope.json",
        "command_cache": session_dir / "command-cache.json",
        "gitignore": workflow_dir / ".gitignore",
        "runtime_dir": runtime_dir,
        "prompt": runtime_dir / "prompt.txt",
        "prompt_meta": runtime_dir / "prompt.meta.json",
        "response_last": runtime_dir / "response.last.md",
        # Evidence sidecars: dynamic leads/facts the second agent reads for itself,
        # instead of them riding in the (8191-capped) command-line prompt.
        "leads": runtime_dir / "leads.json",
        "facts": runtime_dir / "facts.json",
        "lock": runtime_dir / "lock",
        "reports_dir": reports_dir,
        "doctor_report": reports_dir / "doctor.json",
        "sweep_report": sweep_report,
        "logs_dir": logs_dir,
    }


def resolve_provider_config(project_root) -> tuple[Path | None, str]:
    """Locate a project's provider config. Returns (path, source).

    `source` is part of the answer, not a debugging extra. When v3.4.3 renamed the file
    from `opencode.json` to `second_agent.json`, one resolver kept the old literal — so
    every project silently ran on the tool defaults: another model, another timeout, no
    error anywhere. The only visible symptom was quota burning on a provider nobody had
    selected. Resolution lives here, next to the names it resolves, so the next rename
    has one place to miss instead of several.

    A `None` path means no project-local file exists at all, which is a legitimate state
    for a workspace that never tuned anything — the caller falls back to the tool default
    knowingly rather than by accident.
    """
    workflow_dir = Path(project_root) / WORKFLOW_DIRNAME
    for name, source in (
        (PROVIDER_CONFIG_NAME, "project"),
        (LEGACY_PROVIDER_CONFIG_NAME, "project_legacy"),
    ):
        candidate = workflow_dir / name
        if candidate.exists():
            return candidate, source
    return None, "tool_default"


def _tool_paths(agent_workflow_path: str | None) -> dict:
    """Resolve absolute tool paths (main.py/check.py) so .workflow is self-contained."""
    from config.settings import CHECK_PY, COMPONENT_VERSIONS, MAIN_PY, TOOL_VERSION

    main_py = Path(agent_workflow_path).resolve() if agent_workflow_path else MAIN_PY
    tool_dir = main_py.parent
    check_py = tool_dir / "check.py"
    if not check_py.exists():
        check_py = CHECK_PY
    return {
        "main_py_path": str(main_py),
        "check_py_path": str(check_py),
        "tool_dir": str(tool_dir),
        "tool_version": TOOL_VERSION,
        "runtime_version": COMPONENT_VERSIONS["runtime"],
    }


def read_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object in {path}")
    return data


def ensure_valid_json_or_create(path: Path, factory) -> tuple[str, dict]:
    if path.exists():
        return "existing", read_json_file(path)
    payload = factory()
    atomic_write_json(path, payload)
    return "created", payload

