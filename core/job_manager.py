import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import (
    DEFAULT_JOB_MAX_RUNTIME_SECONDS,
    DEFAULT_STALL_THRESHOLD_SECONDS,
    JOB_DIR,
)
from utils import osutil

# Worker liveness, in increasing order of trouble.
ALIVE_PROGRESSING = "alive-progressing"  # PID up, heartbeat fresh
ALIVE_STALLED = "alive-stalled"  # PID up, heartbeat stale -> probe before judging
DEAD = "dead"  # PID gone


class JobManager:
    def __init__(
        self,
        job_dir: Path = JOB_DIR,
        stall_threshold_seconds: int = DEFAULT_STALL_THRESHOLD_SECONDS,
        max_runtime_seconds: int = DEFAULT_JOB_MAX_RUNTIME_SECONDS,
    ) -> None:
        self.job_dir = Path(job_dir)
        self.lock_dir = self.job_dir / "locks"
        self.beat_dir = self.job_dir / "beats"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.beat_dir.mkdir(parents=True, exist_ok=True)
        self.stall_threshold_seconds = stall_threshold_seconds
        self.max_runtime_seconds = max_runtime_seconds

    def create_job(
        self,
        workflow_command: str,
        task: str,
        session_id: str,
        work_dir: str | None,
        model: str | None,
    ) -> dict:
        request_hash = self._request_hash(workflow_command, task, session_id, work_dir)

        # Idempotency: identical request already active on this session → reuse it.
        existing = self.active_job_for_session(session_id)
        if existing and existing.get("request_hash") == request_hash:
            return existing

        job_id = self._new_job_id()
        self._acquire_session_lock(session_id, job_id)
        job = {
            "job_id": job_id,
            "request_hash": request_hash,
            "command": workflow_command,
            "task": task,
            "session_id": session_id,
            "work_dir": work_dir,
            "model": model,
            "status": "pending",
            "worker_pid": None,
            "created_at": self._now(),
            "started_at": None,
            "completed_at": None,
            # Liveness (heartbeat) and probe verdicts live in side files, not here —
            # see touch_heartbeat. `last_heartbeat` stays only as a read fallback for
            # job records written by an earlier build.
            "output": None,
            "error": None,
        }
        self._save(job)
        return job

    def set_worker_pid(self, job_id: str, pid: int | None) -> None:
        job = self._load(job_id)
        job["worker_pid"] = pid
        self._save(job)

    def touch_heartbeat(self, job_id: str, progress: dict | None = None) -> None:
        """Record one beat from the worker's poll loop.

        Written to a SIDE FILE, never into the job record. The job JSON is
        read-modify-written by whoever calls get_result (a different process), and a
        beat lands every couple of seconds: folding the beat into the job record let a
        late write resurrect a job that had just been reaped, wiping its terminal state.
        One writer per file removes the race instead of narrowing it.

        Best-effort: a failed heartbeat write must never abort the call it is watching.
        """
        payload = {"at": self._now()}
        if progress is not None:
            payload["progress"] = progress
        try:
            self._write_side(self._beat_path(job_id), payload)
        except OSError:
            pass

    def read_heartbeat(self, job_id: str) -> dict | None:
        return self._read_side(self._beat_path(job_id))

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

    def fail_job(self, job_id: str, error: str, output: dict | None = None) -> dict:
        job = self._load(job_id)
        job["status"] = "failed"
        job["completed_at"] = self._now()
        job["error"] = error
        if output is not None:
            job["output"] = output  # preserve rich error (error_type, next_action, meta)
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

        if job["status"] in {"pending", "running"}:
            state = self.liveness(job)
            if state == DEAD:
                # Reap a job whose worker died without writing a terminal state.
                job = self.fail_job(
                    job_id,
                    "worker process died before completing (reaped)",
                )
            elif self._exceeded_max_runtime(job):
                job = self.fail_job(
                    job_id,
                    f"job exceeded max runtime {self.max_runtime_seconds}s (reaped)",
                )
            elif state == ALIVE_STALLED:
                # Alive but silent. Report it — never reap on suspicion alone.
                beat = self.read_heartbeat(job_id) or {}
                probe = self.read_probe(job_id) or {}
                return {
                    "ok": False,
                    "job_id": job_id,
                    "status": job["status"],
                    "content": (
                        f"job {job_id} is running but has produced no progress for "
                        f">{self.stall_threshold_seconds}s"
                    ),
                    "meta": {
                        "error_type": "worker_stalled",
                        "liveness": probe.get("liveness") or ALIVE_STALLED,
                        "next_action": (
                            "Probe the second agent to tell a rate limit from a hang; "
                            "do not resubmit blindly."
                        ),
                        "worker_pid": job.get("worker_pid"),
                        "last_heartbeat": beat.get("at"),
                        "progress": beat.get("progress"),
                        "probe": probe.get("probe"),
                    },
                }

        if job["status"] == "completed":
            return {"ok": True, "job_id": job_id, "status": "completed", "output": job["output"]}
        if job["status"] == "failed":
            stored = job.get("output")
            if isinstance(stored, dict) and stored.get("meta"):
                return {
                    "ok": False,
                    "job_id": job_id,
                    "status": "failed",
                    "content": stored.get("content") or job.get("error") or "job failed",
                    "meta": dict(stored.get("meta") or {}),
                }
            return {
                "ok": False,
                "job_id": job_id,
                "status": "failed",
                "content": job.get("error") or "job failed",
                "meta": {"error_type": self._failure_type(job.get("error"))},
            }
        return {
            "ok": False,
            "job_id": job_id,
            "status": job["status"],
            "content": f"job {job_id} not completed yet",
            "meta": {},
        }

    def prune_jobs(self, ttl_days: int = 7, keep_last: int = 50) -> dict:
        """Delete terminal jobs older than ttl_days, always keeping the newest keep_last."""
        files = sorted(
            (p for p in self.job_dir.glob("job_*.json")),
            key=lambda p: p.name,
            reverse=True,
        )
        cutoff = time.time() - ttl_days * 86400
        removed = 0
        for index, path in enumerate(files):
            if index < keep_last:
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") not in {"completed", "failed"}:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    # Side files outlive their job otherwise: they are never rewritten
                    # after it ends, so nothing else would ever clean them up.
                    for side in (
                        self._beat_path(job.get("job_id", "")),
                        self._probe_path(job.get("job_id", "")),
                    ):
                        try:
                            side.unlink()
                        except OSError:
                            pass
                    removed += 1
            except OSError:
                continue
        return {"removed": removed, "kept": min(len(files), keep_last)}

    # --- internals -------------------------------------------------------

    def _worker_dead(self, job: dict) -> bool:
        return self.liveness(job) == DEAD

    def liveness(self, job: dict) -> str | None:
        """Tri-state worker health, or None when there is nothing to judge yet.

        PID liveness alone cannot tell "working" from "hung on a rate limit" — the
        process is up in both cases. The heartbeat, emitted from the adapter's poll
        loop, is what separates them.
        """
        pid = job.get("worker_pid")
        if pid is None:
            return None  # not yet spawned; nothing to reap
        if not osutil.process_alive(pid):
            return DEAD
        age = self._heartbeat_age_seconds(job)
        if age is None:
            # No beat yet: fall back to how long the job has been running, so a worker
            # that died before its first tick still gets classified instead of hanging.
            age = self._age_seconds(job.get("started_at") or job.get("created_at"))
        if age is not None and age > self.stall_threshold_seconds:
            return ALIVE_STALLED
        return ALIVE_PROGRESSING

    def _heartbeat_age_seconds(self, job: dict) -> float | None:
        beat = self.read_heartbeat(job.get("job_id") or "")
        stamp = beat.get("at") if beat else job.get("last_heartbeat")
        return self._age_seconds(stamp)

    @staticmethod
    def _age_seconds(stamp: str | None) -> float | None:
        if not stamp:
            return None
        try:
            when = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - when).total_seconds()

    def _exceeded_max_runtime(self, job: dict) -> bool:
        """Backstop for the OOM case: the box ran out of RAM, the worker went down in a
        way the PID check can miss (or the PID got reused), and the job would otherwise
        sit in `running` forever."""
        if not self.max_runtime_seconds or self.max_runtime_seconds <= 0:
            return False
        age = self._age_seconds(job.get("started_at") or job.get("created_at"))
        return age is not None and age > self.max_runtime_seconds

    def record_probe(self, job_id: str, probe: dict) -> dict:
        """Attach a liveness-probe verdict. A stalled-but-answering agent is NOT reaped:
        its work may still land once the limit resets.

        Side file for the same reason as the heartbeat: the worker may complete the job
        at any moment, and a probe written into the job record could overwrite that.
        """
        payload = {
            "probe": probe,
            "liveness": "stalled_no_progress" if probe.get("alive") else "stalled_on_limit",
            "at": self._now(),
        }
        try:
            self._write_side(self._probe_path(job_id), payload)
        except OSError:
            pass
        return payload

    def read_probe(self, job_id: str) -> dict | None:
        return self._read_side(self._probe_path(job_id))

    def active_job_for_session(self, session_id: str) -> dict | None:
        lock_path = self._lock_path(session_id)
        if not lock_path.exists():
            return None
        data = self._read_lock(lock_path)
        if not data:
            return None
        job = self.get_job(data.get("job_id", ""))
        if job and job.get("status") in {"pending", "running"}:
            return job
        return None

    def _path(self, job_id: str) -> Path:
        return self.job_dir / f"{self._safe(job_id)}.json"

    def _beat_path(self, job_id: str) -> Path:
        return self.beat_dir / f"{self._safe(job_id)}.beat.json"

    def _probe_path(self, job_id: str) -> Path:
        return self.beat_dir / f"{self._safe(job_id)}.probe.json"

    def _write_side(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
        temp.replace(path)

    @staticmethod
    def _read_side(path: Path) -> dict | None:
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

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
                # Reap a dead worker instead of blocking forever.
                if self._worker_dead(existing_job):
                    self.fail_job(existing_job["job_id"], "worker process died before completing (reaped)")
                elif self._exceeded_max_runtime(existing_job):
                    self.fail_job(
                        existing_job["job_id"],
                        f"job exceeded max runtime {self.max_runtime_seconds}s (reaped)",
                    )
                else:
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
    def _failure_type(error: str | None) -> str:
        text = error or ""
        if "max runtime" in text:
            return "job_expired"
        if "reaped" in text:
            return "worker_died"
        return "unknown"

    @staticmethod
    def _request_hash(command: str, task: str, session_id: str, work_dir: str | None) -> str:
        raw = "|".join([command.strip().lower(), task.strip(), session_id, work_dir or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _new_job_id(self) -> str:
        return f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
