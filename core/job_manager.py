import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import JOB_DIR


class JobManager:
    def __init__(self, job_dir: Path = JOB_DIR) -> None:
        self.job_dir = Path(job_dir)
        self.lock_dir = self.job_dir / "locks"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        workflow_command: str,
        task: str,
        session_id: str,
        work_dir: str | None,
        model: str | None,
    ) -> dict:
        job_id = self._new_job_id()
        self._acquire_session_lock(session_id, job_id)
        job = {
            "job_id": job_id,
            "command": workflow_command,
            "task": task,
            "session_id": session_id,
            "work_dir": work_dir,
            "model": model,
            "status": "pending",
            "created_at": self._now(),
            "started_at": None,
            "completed_at": None,
            "output": None,
            "error": None,
        }
        self._save(job)
        return job

    def mark_running(self, job_id: str) -> dict:
        job = self._load(job_id)
        job["status"] = "running"
        job["started_at"] = job["started_at"] or self._now()
        self._save(job)
        return job

    def complete_job(self, job_id: str, output: dict) -> dict:
        job = self._load(job_id)
        job["status"] = "completed"
        job["completed_at"] = self._now()
        job["output"] = output
        job["error"] = None
        self._save(job)
        self._release_session_lock(job)
        return job

    def fail_job(self, job_id: str, error: str) -> dict:
        job = self._load(job_id)
        job["status"] = "failed"
        job["completed_at"] = self._now()
        job["error"] = error
        self._save(job)
        self._release_session_lock(job)
        return job

    def get_job(self, job_id: str) -> dict | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        return self._load(job_id)

    def get_result(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if not job:
            return {"ok": False, "job_id": job_id, "status": "not_found", "meta": {}}
        if job["status"] == "completed":
            return {"ok": True, "job_id": job_id, "status": "completed", "output": job["output"]}
        if job["status"] == "failed":
            return {
                "ok": False,
                "job_id": job_id,
                "status": "failed",
                "content": job.get("error") or "job failed",
                "meta": {},
            }
        return {
            "ok": False,
            "job_id": job_id,
            "status": job["status"],
            "content": f"job {job_id} not completed yet",
            "meta": {},
        }

    def _path(self, job_id: str) -> Path:
        return self.job_dir / f"{self._safe(job_id)}.json"

    def _lock_path(self, session_id: str) -> Path:
        return self.lock_dir / f"{self._safe(session_id)}.lock"

    def _load(self, job_id: str) -> dict:
        with self._path(job_id).open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save(self, job: dict) -> None:
        path = self._path(job["job_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(job, file, indent=2)
        temp.replace(path)

    def _acquire_session_lock(self, session_id: str, job_id: str) -> None:
        lock_path = self._lock_path(session_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"session_id": session_id, "job_id": job_id, "created_at": self._now()}

        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = self._read_lock(lock_path)
            existing_job = self.get_job(existing.get("job_id", "")) if existing else None
            if existing_job and existing_job.get("status") in {"pending", "running"}:
                raise ValueError(
                    f"session {session_id} already has active job {existing_job['job_id']}"
                )
            self._release_lock_path(lock_path)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)

    def _release_session_lock(self, job: dict) -> None:
        self._release_lock_path(self._lock_path(job["session_id"]))

    @staticmethod
    def _release_lock_path(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _read_lock(path: Path) -> dict | None:
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _new_job_id(self) -> str:
        return f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
