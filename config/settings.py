import hashlib
import os
import json
import secrets
from pathlib import Path

from config.routing import COMMAND_ROUTES

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "storage" / "sessions"
CACHE_FILE = BASE_DIR / "storage" / "cache.json"
JOB_DIR = BASE_DIR / "storage" / "jobs"
OPENCODE_CONFIG_FILE = BASE_DIR / "config" / "opencode.json"

TOOL_VERSION = "3.4.2"
MAIN_PY = BASE_DIR / "main.py"
CHECK_PY = BASE_DIR / "check.py"

# Component stamps allow lazy upgrades and may diverge when only one surface changes.
# Both surfaces changed in v3.4.2, so both currently match TOOL_VERSION.
# The stamps no longer gate script regeneration — upgrade compares generated content
# directly, so a generator change reaches existing workspaces whether or not this bumps.
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
# Fraction of the task that may be cut before the call is refused instead of degraded.
# A tail trimmed off a long instruction usually costs nothing; losing a third of it means
# the second agent answered a different question than the one asked — and it answers with
# full confidence either way, which is the part that misleads.
DEFAULT_TASK_TRUNCATION_HARD_RATIO = float(
    os.getenv("AI_PROXY_TASK_TRUNCATION_HARD_RATIO", "0.2")
)
# A delegated result ships the evidence text in `content` AND the path it was archived to
# in `evidence_ref.artifact_path` — the same bytes, twice. Delegation exists to keep raw
# code out of main_agent's context window, and carrying both puts it straight back in.
# Opt-in rather than default: a consumer that reads `content` without checking
# `meta.content_mode` would get a preview and have no way to notice.
SLIM_CONTENT_ENV = "AI_PROXY_SLIM_CONTENT"
DEFAULT_CONTENT_PREVIEW_CHARS = int(os.getenv("AI_PROXY_CONTENT_PREVIEW_CHARS", "500"))
# Below roughly twice the preview there is nothing to reclaim and a whole answer to lose,
# so short results are left whole no matter what the flag says.
DEFAULT_SLIM_CONTENT_MIN_CHARS = int(
    os.getenv("AI_PROXY_SLIM_CONTENT_MIN_CHARS", str(DEFAULT_CONTENT_PREVIEW_CHARS * 2))
)
OPENCODE_COMMAND = os.getenv("OPENCODE_COMMAND", "opencode")
# The opencode agent that runs delegated calls. `plan` is opencode's own read-only
# primary, and the workflow deliberately adds no second primary: every wf-* agent ships
# as a subagent that `plan` spawns through the `task` tool.
DEFAULT_OPENCODE_AGENT = os.getenv("AI_PROXY_OPENCODE_AGENT", "plan")
# How long a learned "this project cannot fan out" verdict stands before it is retried.
# It is inferred from one sentence the second agent wrote about itself, and that sentence
# has been wrong: an agent listed `task` among its tools and still called itself incapable.
# Without an expiry the wrong verdict is permanent, because a project with fan-out off
# stops sending the fan-out plan and can never produce the evidence that would undo it.
DEFAULT_FANOUT_RECHECK_HOURS = float(os.getenv("AI_PROXY_FANOUT_RECHECK_HOURS", "24"))
DEFAULT_JOB_POLL_INTERVAL_SECONDS = float(os.getenv("AI_PROXY_JOB_POLL_INTERVAL_SECONDS", "2"))
DEFAULT_JOB_POLL_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_JOB_POLL_TIMEOUT_SECONDS", "0"))
# Global ceiling on concurrent in-flight delegated workers across ALL sessions. Per-session
# concurrency is already 1 (session lock); this bounds the machine-wide fan-out so a burst of
# parallel main-agents cannot spawn unbounded opencode processes. Admission is serialized
# across processes so the configured ceiling is a hard bound for this runtime version.
DEFAULT_MAX_GLOBAL_WORKERS = int(os.getenv("AI_PROXY_MAX_GLOBAL_WORKERS", "6"))


def default_opencode_config() -> dict:
    return {
        "opencode_command": OPENCODE_COMMAND,
        "opencode_agent": DEFAULT_OPENCODE_AGENT,
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


def _main_session_cache_path(project_root=None) -> Path:
    if project_root is None:
        return Path(CACHE_FILE)
    normalized = os.path.normcase(str(Path(project_root).resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return Path(CACHE_FILE).parent / "main-sessions" / f"{digest}.json"


def get_cached_main_session_id(project_root=None) -> str | None:
    """Read the cached main agent session ID from cache file."""
    cache_path = _main_session_cache_path(project_root)
    try:
        with cache_path.open("r", encoding="utf-8") as file:
            cache = json.load(file)
        if isinstance(cache, dict):
            return cache.get("main_session_id")
    except (OSError, json.JSONDecodeError):
        pass
    return None


def set_cached_main_session_id(session_id: str, project_root=None) -> None:
    """Write the main agent session ID to cache file."""
    cache_path = _main_session_cache_path(project_root)
    cache: dict = {}
    try:
        with cache_path.open("r", encoding="utf-8") as file:
            cache = json.load(file)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, json.JSONDecodeError):
        cache = {}

    cache["main_session_id"] = session_id
    if project_root is not None:
        cache["project_root"] = str(Path(project_root).resolve())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(cache_path.suffix + f".{secrets.token_hex(6)}.tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(cache, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(cache_path)


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
