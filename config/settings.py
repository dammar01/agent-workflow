import os
import json
from pathlib import Path

from config.routing import COMMAND_ROUTES

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "storage" / "sessions"
CACHE_FILE = BASE_DIR / "storage" / "cache.json"
OPENCODE_CONFIG_FILE = BASE_DIR / "config" / "opencode.json"

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_TIMEOUT_SECONDS", "0"))
OPENCODE_COMMAND = os.getenv("OPENCODE_COMMAND", "opencode")


def default_opencode_config() -> dict:
    return {
        "opencode_command": OPENCODE_COMMAND,
        "default_model": None,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "routes": COMMAND_ROUTES,
    }


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
