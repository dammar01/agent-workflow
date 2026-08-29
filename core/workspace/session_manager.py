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
                session = json.load(file)
            if self._migrate_legacy_key(session):
                self._save(session)
            return session

        now = self._now()
        session = {
            "session_id": session_id,
            "provider_session_id": None,
            "history": {
                "created_at": now,
                "updated_at": now,
                "runs": [],
            },
        }
        self._save(session)
        return session

    @staticmethod
    def _migrate_legacy_key(session: dict) -> bool:
        """Move a v3.4.2 `opencode_session_id` onto the current key. True if changed.

        Runs on every load rather than only on upgrade. The bulk migration in
        core/provider_migration.py fires from `upgrade_workflow_workspace`, but nothing
        forces a user to upgrade before their next delegated call — and a session record
        the reader cannot understand does not fail loudly. It reads as "no session yet",
        so the adapter bootstraps a fresh one: a full model round trip, on every call,
        to rebuild an id that was sitting on disk the whole time.
        """
        if "opencode_session_id" not in session:
            return False
        legacy = session.pop("opencode_session_id")
        if session.get("provider_session_id") is None and legacy:
            session["provider_session_id"] = legacy
        return True

    def update_provider_session_id(self, session: dict, provider_session_id: str) -> None:
        session["provider_session_id"] = provider_session_id
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
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(session, file, indent=2)
        temp.replace(path)

    def _path_for(self, session_id: str) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
        return self.session_dir / f"{safe_name}.json"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
