import os
import json
from pathlib import Path

from config.routing import COMMAND_ROUTES

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "storage" / "sessions"
CACHE_FILE = BASE_DIR / "storage" / "cache.json"
JOB_DIR = BASE_DIR / "storage" / "jobs"
OPENCODE_CONFIG_FILE = BASE_DIR / "config" / "opencode.json"

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_TIMEOUT_SECONDS", "0"))
OPENCODE_COMMAND = os.getenv("OPENCODE_COMMAND", "opencode")
DEFAULT_JOB_POLL_INTERVAL_SECONDS = float(os.getenv("AI_PROXY_JOB_POLL_INTERVAL_SECONDS", "2"))
DEFAULT_JOB_POLL_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_JOB_POLL_TIMEOUT_SECONDS", "0"))


def default_opencode_config() -> dict:
    return {
        "opencode_command": OPENCODE_COMMAND,
        "default_model": None,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "job_poll_interval_seconds": DEFAULT_JOB_POLL_INTERVAL_SECONDS,
        "job_poll_timeout_seconds": DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
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
