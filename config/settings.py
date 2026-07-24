import os
import json
from pathlib import Path

from config.routing import COMMAND_ROUTES

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "storage" / "sessions"
CACHE_FILE = BASE_DIR / "storage" / "cache.json"
JOB_DIR = BASE_DIR / "storage" / "jobs"
OPENCODE_CONFIG_FILE = BASE_DIR / "config" / "opencode.json"

TOOL_VERSION = "3.4.0"
MAIN_PY = BASE_DIR / "main.py"
CHECK_PY = BASE_DIR / "check.py"

# Per-component versions for lazy upgrades (P0.7). Both derive from TOOL_VERSION; a
# maintainer bumps ONE entry when only that component changed, so an upgrade re-applies
# just that part instead of rewriting the whole workspace / install.
#   prompt_bundle : LLM-facing contract shipped to ~/.claude (CLAUDE.md, skills, AGENTS.md)
#   runtime       : machine wiring in .workflow (run/inspect/check scripts, config schema,
#                   opencode adapter) + shipped hooks/settings
COMPONENT_VERSIONS = {
    "prompt_bundle": TOOL_VERSION,
    "runtime": TOOL_VERSION,
}

# Overall delegated-call timeout; 0 disables it.
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_TIMEOUT_SECONDS", "1800"))
# Bootstrap only says "READY"; it must never inherit the long task budget.
DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS = int(
    os.getenv("AI_PROXY_BOOTSTRAP_TIMEOUT_SECONDS", "180")
)
# Gap between liveness ticks while opencode runs.
DEFAULT_POLL_INTERVAL_SECONDS = float(
    os.getenv("AI_PROXY_POLL_INTERVAL_SECONDS", "2")
)
# No heartbeat for this long while the PID is still alive => stalled, probe it.
DEFAULT_STALL_THRESHOLD_SECONDS = int(
    os.getenv("AI_PROXY_STALL_THRESHOLD_SECONDS", "360")
)
# Unlike heartbeat age, this measures how long the output stream has been idle.
DEFAULT_IDLE_STALL_SECONDS = int(os.getenv("AI_PROXY_IDLE_STALL_SECONDS", "240"))
DEFAULT_PROBE_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_PROBE_TIMEOUT_SECONDS", "45"))
# Cadence for re-probing a stalled job.
DEFAULT_PROBE_RECHECK_SECONDS = int(os.getenv("AI_PROXY_PROBE_RECHECK_SECONDS", "120"))
# Max fresh-session probes for one stalled job before probing stops entirely. Each probe
# spends quota, so a job that stays alive-but-silent must not re-probe forever — after the
# budget the job_max_runtime backstop is what ends it. The recheck interval also backs off
# (doubles) between probes so the budget is not spent in one tight burst.
DEFAULT_MAX_PROBES = int(os.getenv("AI_PROXY_MAX_PROBES", "3"))
# Hard ceiling: a job running past this is failed even if its PID looks alive.
# Backstop for the OOM case, where the worker dies in a way PID checks miss.
DEFAULT_JOB_MAX_RUNTIME_SECONDS = int(
    os.getenv("AI_PROXY_JOB_MAX_RUNTIME_SECONDS", "5400")
)
# The delegated prompt is passed as ONE CLI arg (Windows caps argv at 8191 chars; the
# adapter's _too_long_for_cmd is the hard backstop). Scaffolding is fixed cost, so the
# task string is the only variable-size part worth capping here — cap it before assembly
# so a long task degrades to a visible truncation instead of a deterministic call failure.
DEFAULT_MAX_TASK_CHARS = int(os.getenv("AI_PROXY_MAX_TASK_CHARS", "3000"))
OPENCODE_COMMAND = os.getenv("OPENCODE_COMMAND", "opencode")
DEFAULT_JOB_POLL_INTERVAL_SECONDS = float(os.getenv("AI_PROXY_JOB_POLL_INTERVAL_SECONDS", "2"))
DEFAULT_JOB_POLL_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_JOB_POLL_TIMEOUT_SECONDS", "0"))


def default_opencode_config() -> dict:
    return {
        "opencode_command": OPENCODE_COMMAND,
        "default_model": None,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "bootstrap_timeout_seconds": DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
        "stall_threshold_seconds": DEFAULT_STALL_THRESHOLD_SECONDS,
        "idle_stall_seconds": DEFAULT_IDLE_STALL_SECONDS,
        "probe_timeout_seconds": DEFAULT_PROBE_TIMEOUT_SECONDS,
        "probe_recheck_seconds": DEFAULT_PROBE_RECHECK_SECONDS,
        "max_probes": DEFAULT_MAX_PROBES,
        "job_max_runtime_seconds": DEFAULT_JOB_MAX_RUNTIME_SECONDS,
        "job_poll_interval_seconds": DEFAULT_JOB_POLL_INTERVAL_SECONDS,
        "job_poll_timeout_seconds": DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
        "agent_workflow_path": None,
        "routes": COMMAND_ROUTES,
    }


def get_cached_main_session_id() -> str | None:
    """Read the cached main agent session ID from cache file."""
    try:
        with Path(CACHE_FILE).open("r", encoding="utf-8") as file:
            cache = json.load(file)
        if isinstance(cache, dict):
            return cache.get("main_session_id")
    except (OSError, json.JSONDecodeError):
        pass
    return None


def set_cached_main_session_id(session_id: str) -> None:
    """Write the main agent session ID to cache file."""
    cache: dict = {}
    try:
        with Path(CACHE_FILE).open("r", encoding="utf-8") as file:
            cache = json.load(file)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, json.JSONDecodeError):
        cache = {}

    cache["main_session_id"] = session_id
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with Path(CACHE_FILE).open("w", encoding="utf-8") as file:
        json.dump(cache, file, indent=2)


def load_opencode_config(path: Path = OPENCODE_CONFIG_FILE) -> dict:
    config = default_opencode_config()
    try:
        with Path(path).open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, json.JSONDecodeError):
        return config

    if isinstance(loaded, dict):
        config.update({key: value for key, value in loaded.items() if value is not None})
        if not isinstance(config.get("routes"), dict):
            config["routes"] = COMMAND_ROUTES
    return config


def load_opencode_config_for(project_root) -> dict:
    """Prefer the project-local .workflow/opencode.json, falling back to the tool default."""
    local = Path(project_root) / ".workflow" / "opencode.json"
    if local.exists():
        return load_opencode_config(local)
    return load_opencode_config()
