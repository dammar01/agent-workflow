import json
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import SESSION_DIR


class SessionManager:
    def __init__(self, session_dir: Path = SESSION_DIR) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def load_or_create(self, session_id: str) -> dict:
        if not session_id or not session_id.strip():
            raise ValueError("session_id is required")

        path = self._path_for(session_id)
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)

        now = self._now()
        session = {
            "session_id": session_id,
            "opencode_session_id": None,
            "history": {
                "created_at": now,
                "updated_at": now,
                "runs": [],
            },
        }
        self._save(session)
        return session

    def update_opencode_session_id(self, session: dict, opencode_session_id: str) -> None:
        session["opencode_session_id"] = opencode_session_id
        self._save(session)

    def record_run(self, session: dict, command: str) -> None:
        history = session.setdefault("history", {})
        runs = history.setdefault("runs", [])
        runs.append(
            {
                "command": command,
                "timestamp": self._now(),
            }
        )
        history["updated_at"] = self._now()
        self._save(session)

    def _save(self, session: dict) -> None:
        path = self._path_for(session["session_id"])
        with path.open("w", encoding="utf-8") as file:
            json.dump(session, file, indent=2)

    def _path_for(self, session_id: str) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
        return self.session_dir / f"{safe_name}.json"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
