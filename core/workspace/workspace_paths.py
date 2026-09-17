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
# The tool-level TEMPLATE `init` copies into a new workspace, rebuilt by every
# `install.py --apply`. Never read by the runtime: a project without its own
# second_agent.json is refused, not run on whatever this machine was once set to.
PROVIDER_SEED_NAME = "second_agent.seed.json"
LOCK_TTL_SECONDS = 300
JSON_INDENT = 2
ARCHIVE_KEEP = 20
# Derived, not restated. `tools/maintain/stamp_version.py` makes TOOL_VERSION the single source for
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


DATA_DIRNAME = "data"
# What a layout-3.6 workspace kept at the .workflow root that a 3.7 one keeps under data/.
# Its presence without data/ marks a workspace the upgrade migration has not moved yet.
_LEGACY_MARKERS = (
    "sessions",
    "provider-sessions",
    "reports",
    "audit.jsonl",
    "usage.jsonl",
    "quality.jsonl",
    "facts.jsonl",
    "evidence.jsonl",
    "redactions.jsonl",
)


def data_dir(project_root: Path) -> Path:
    """Where this workspace keeps everything that is not a file the user edits.

    `.workflow/` itself holds only what a person opens: config.json, second_agent.json,
    e2e/secrets.json, the run/check/inspect scripts, and current/. The rest — streams,
    stores, caches, sessions — lives in `.workflow/data/`.

    A workspace written by 3.6 or earlier keeps those at the root until `upgrade` moves
    them. Until then this returns the root, so an un-migrated workspace keeps working
    exactly as before instead of silently starting an empty history beside its real one.
    The rule is the directory's presence, and the shipped hooks and run scripts apply the
    same rule, so every reader agrees on one layout at any moment.
    """
    workflow_dir = Path(project_root) / WORKFLOW_DIRNAME
    data = workflow_dir / DATA_DIRNAME
    if data.is_dir():
        return data
    if any((workflow_dir / name).exists() for name in _LEGACY_MARKERS):
        return workflow_dir
    return data


def is_legacy_layout(project_root: Path) -> bool:
    return data_dir(project_root) == Path(project_root) / WORKFLOW_DIRNAME


def workflow_paths(
    project_root: Path, session_id: str | None = None
) -> dict[str, Path]:
    """Resolve workflow paths — the one place a `.workflow` path is spelled.

    Mutable per-flow state (state/scope/cache/runtime/logs) lives under
    `sessions/<sid>/` so concurrent main agents on the SAME project never clobber each
    other. Shared streams and stores sit beside `sessions/` in the data directory
    (`data_dir`). Only user-editable files stay at the `.workflow` root.

    session_id=None → session-scoped keys point at the data directory itself: init
    scaffolding and no-session tooling, never a session's state.
    """
    project_root = Path(project_root)
    workflow_dir = project_root / WORKFLOW_DIRNAME
    data = data_dir(project_root)
    sessions_dir = data / "sessions"
    reports_dir = data / "reports"
    session_dir = sessions_dir / _safe_component(session_id) if session_id else data
    runtime_dir = session_dir / "runtime"
    logs_dir = session_dir / "logs"
    sweep_report = (
        session_dir / "reports" / "sweep.last.md"
        if session_id
        else reports_dir / "sweep.last.md"
    )
    return {
        "project_root": project_root,
        "workflow_dir": workflow_dir,
        "data_dir": data,
        # --- user-editable, at the .workflow root -------------------------------------
        "config": workflow_dir / "config.json",
        "provider_config": workflow_dir / PROVIDER_CONFIG_NAME,
        "secrets": workflow_dir / "e2e" / "secrets.json",
        "gitignore": workflow_dir / ".gitignore",
        "current_dir": workflow_dir / "current",
        # --- internal, in the data directory -------------------------------------------
        "sessions_dir": sessions_dir,
        "provider_sessions_dir": data / "provider-sessions",
        "reports_dir": reports_dir,
        "doctor_report": reports_dir / "doctor.json",
        "audit_stream": data / "audit.jsonl",
        "usage_stream": data / "usage.jsonl",
        "quality_stream": data / "quality.jsonl",
        "redactions_stream": data / "redactions.jsonl",
        "evidence_store": data / "evidence.jsonl",
        "facts_store": data / "facts.jsonl",
        "recurrence_cache": data / "recurrence-cache.json",
        "e2e_knowledge": data / "e2e-knowledge.jsonl",
        "capabilities": data / "capabilities.json",
        "graph_meta": data / "graph-meta.json",
        "graph_stale": data / "graph-stale.json",
        "promote_lock": data / "promote.lock",
        "backups_dir": data / "backups",
        # --- per session -------------------------------------------------------------
        "session_dir": session_dir,
        "state": session_dir / "state.json",
        "scope": session_dir / "scope.json",
        "command_cache": session_dir / "command-cache.json",
        "runtime_dir": runtime_dir,
        "prompt": runtime_dir / "prompt.txt",
        "prompt_meta": runtime_dir / "prompt.meta.json",
        "response_last": runtime_dir / "response.last.md",
        # Evidence sidecars: dynamic leads/facts the second agent reads for itself,
        # instead of them riding in the prompt.
        "leads": runtime_dir / "leads.json",
        "facts": runtime_dir / "facts.json",
        "lock": runtime_dir / "lock",
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

    A `None` path means no project-local file exists. That used to fall back to the
    tool-level config/second_agent.json, and that fallback is how one stale machine-wide
    choice (`opencode/mimo-v2.5-free`) reached every project initialised from it. There is
    no fallback any more: the executor refuses a `missing` config with a next_action.
    """
    workflow_dir = Path(project_root) / WORKFLOW_DIRNAME
    for name, source in (
        (PROVIDER_CONFIG_NAME, "project"),
        (LEGACY_PROVIDER_CONFIG_NAME, "project_legacy"),
    ):
        candidate = workflow_dir / name
        if candidate.exists():
            return candidate, source
    return None, "missing"


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

