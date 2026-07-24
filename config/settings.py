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

# A delegated call that has produced nothing for this long is not working, it is hung.
# 0 still means "no limit", but it is no longer the DEFAULT — an unbounded wait is how
# a rate-limited second agent used to hang a job forever.
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
# Separate from the heartbeat threshold on purpose. The heartbeat only proves the poll
# loop is turning, which it does whether or not opencode is producing anything — a
# rate-limited agent keeps beating forever. This one measures the STREAM: no byte on
# stdout/stderr for this long means waiting, not working. It must never be folded into
# stall_threshold; ten other call sites read that value for a different question.
DEFAULT_IDLE_STALL_SECONDS = int(os.getenv("AI_PROXY_IDLE_STALL_SECONDS", "240"))
DEFAULT_PROBE_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_PROBE_TIMEOUT_SECONDS", "45"))
# A stalled job is re-probed on this cadence. Probing once per job was enough to
# classify a stall but not to catch one that starts after the single probe landed.
DEFAULT_PROBE_RECHECK_SECONDS = int(os.getenv("AI_PROXY_PROBE_RECHECK_SECONDS", "120"))
# Hard ceiling: a job running past this is failed even if its PID looks alive.
# Backstop for the OOM case, where the worker dies in a way PID checks miss.
DEFAULT_JOB_MAX_RUNTIME_SECONDS = int(
    os.getenv("AI_PROXY_JOB_MAX_RUNTIME_SECONDS", "5400")
)
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
