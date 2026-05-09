import hashlib
import json
import time
from pathlib import Path

from config.settings import CACHE_FILE


class SimpleCache:
    def __init__(self, path: Path = CACHE_FILE, ttl_seconds: int | None = None) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def make_key(self, command: str, task: str, work_dir: str | None = None) -> str:
        task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()
        if work_dir:
            dir_hash = hashlib.sha256(work_dir.encode("utf-8")).hexdigest()[:12]
            return f"{command.strip().lower()}:{dir_hash}:{task_hash}"
        return f"{command.strip().lower()}:{task_hash}"

    def get(self, key: str) -> dict | None:
        data = self._read()
        item = data.get(key)
        if not item:
            return None

        if self.ttl_seconds is not None:
            age = time.time() - item.get("created_at", 0)
            if age > self.ttl_seconds:
                return None

        return item.get("response")

    def set(self, key: str, response: dict) -> None:
        data = self._read()
        data[key] = {
            "created_at": time.time(),
            "response": response,
        }
        self._write(data)

    def clear(self) -> None:
        self._write({})

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
