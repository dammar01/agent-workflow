import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_DIR = BASE_DIR / "storage" / "sessions"
CACHE_FILE = BASE_DIR / "storage" / "cache.json"

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("AI_PROXY_TIMEOUT_SECONDS", "300"))

KIMI_COMMAND = os.getenv("KIMI_COMMAND", "kimi")
KIMI_SESSION_FLAG = os.getenv("KIMI_SESSION_FLAG", "--session")
KIMI_WORK_DIR = os.getenv("KIMI_WORK_DIR", str(Path.cwd()))
KIMI_JSON_PATH = Path.home() / ".kimi" / "kimi.json"

CLAUDE_COMMAND = os.getenv("CLAUDE_COMMAND", "")
