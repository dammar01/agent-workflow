import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import (
    DEFAULT_IDLE_STALL_SECONDS,
    DEFAULT_JOB_MAX_RUNTIME_SECONDS,
    DEFAULT_MAX_GLOBAL_WORKERS,
    DEFAULT_STALL_THRESHOLD_SECONDS,
    JOB_DIR,
)
from utils import osutil

ALIVE_PROGRESSING = "alive-progressing"  # PID up, heartbeat fresh, stream producing
ALIVE_STALLED = "alive-stalled"  # PID up but silent -> probe before judging
DEAD = "dead"  # PID gone


class JobManager:
    def __init__(
        self,
        job_dir: Path = JOB_DIR,
        stall_threshold_seconds: int = DEFAULT_STALL_THRESHOLD_SECONDS,
        max_runtime_seconds: int = DEFAULT_JOB_MAX_RUNTIME_SECONDS,
        idle_stall_seconds: int = DEFAULT_IDLE_STALL_SECONDS,
        max_global_workers: int = DEFAULT_MAX_GLOBAL_WORKERS,
    ) -> None:
        self.job_dir = Path(job_dir)
        self.lock_dir = self.job_dir / "locks"
        self.beat_dir = self.job_dir / "beats"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.beat_dir.mkdir(parents=True, exist_ok=True)
        self.stall_threshold_seconds = stall_threshold_seconds
        self.max_runtime_seconds = max_runtime_seconds
        self.idle_stall_seconds = idle_stall_seconds
        self.max_global_workers = max_global_workers

    def create_job(
        self,
        workflow_command: str,
        task: str | None,
        session_id: str,
        work_dir: str | None,
        model: str | None,
    ) -> dict:
        task = task or ""
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
        # Snapshot the creation time NOW, while the PID provably belongs to the worker we
        # just spawned. A later reap compares against this to tell a live worker from a
        # recycled PID (Windows). None on POSIX / when unreadable — the reuse guard then
        # degrades to the prior always-kill behaviour, which is safe.
        job["worker_create_time"] = osutil.pid_create_time(pid)
        self._save(job)

    def _kill_worker(self, job: dict) -> dict:
        """Terminate a worker by PID — unless the PID was recycled to a different process.

        Centralises the reuse guard so every by-PID kill path is protected. A proven
        mismatch is reported as a skipped kill rather than taking out an innocent process.
        """
        pid = job.get("worker_pid")
        if pid and osutil.pid_reused(pid, job.get("worker_create_time")):
            return {"method": "skipped", "ok": False, "reason": "pid_reuse_detected", "pid": pid}
        return osutil.terminate_tree(None, pid=pid)

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
        if job.get("reaped"):
            # The job was reaped while this worker was still running (rate limit, dead
            # probe). Letting the late finish flip it back to completed would undo a
            # decision the caller has already acted on. The payload is kept rather than
            # dropped: it is real work, just no longer the answer to anyone's question.
            #
            # `reaped` alone, not `status == "failed" and reaped`: the flag IS the claim,
            # and pairing it with a status made the guard depend on two fields landing
            # together. `reap_stalled` writes both in one atomic save, so a record that
            # carries the flag has been claimed no matter what its status currently reads.
            job["late_output"] = output
            self._save(job)
            return job
        job["status"] = "completed"
        job["completed_at"] = self._now()
        job["output"] = output
        job["error"] = None
        self._save(job)
        self._release_session_lock(job)
        return job

    def fail_job(
        self,
        job_id: str,
        error: str,
        output: dict | None = None,
        reaped: bool = False,
    ) -> dict:
        job = self._load(job_id)
        job["status"] = "failed"
        job["completed_at"] = self._now()
        job["error"] = error
        if output is not None:
            job["output"] = output
        if reaped:
            job["reaped"] = True
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
                    reaped=True,
                )
            elif self._exceeded_max_runtime(job):
                # The one path that can race a live worker: the backstop fires on age while
                # the PID is still up (or was reused), so the worker may finish and call
                # complete_job right after this. reaped=True makes that finish land as
                # late_output instead of resurrecting the job; terminate_tree then stops the
                # worker from running to completion and burning quota after we stopped waiting.
                job = self.fail_job(
                    job_id,
                    f"job exceeded max runtime {self.max_runtime_seconds}s (reaped)",
                    reaped=True,
                )
                self._kill_worker(job)
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
                        "idle_seconds": self._idle_seconds(job),
                        "progress": beat.get("progress"),
                        "probe": probe.get("probe"),
                    },
                }

        if job["status"] == "completed":
            return {
                "ok": True,
                "job_id": job_id,
                "status": "completed",
                "output": job["output"],
            }
        if job["status"] == "failed":
            stored = job.get("output")
            if isinstance(stored, dict) and stored.get("meta"):
                return {
                    "ok": False,
                    "job_id": job_id,
                    "status": "failed",
                    "content": stored.get("content")
                    or job.get("error")
                    or "job failed",
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

    def _worker_dead(self, job: dict) -> bool:
        return self.liveness(job) == DEAD

    def liveness(self, job: dict) -> str | None:
        """Tri-state worker health, or None when there is nothing to judge yet.

        Three signals, in order of how much they can be trusted:

        1. PID gone            -> DEAD. Unambiguous.
        2. Stream idle         -> ALIVE_STALLED. The adapter reports how long it has
           been since opencode emitted a byte. This is the only one that catches a
           rate-limited agent: its PID is up and its heartbeat is fresh, because the
           poll loop that emits the beat keeps turning while nothing comes back.
        3. Heartbeat stale     -> ALIVE_STALLED. Catches a worker that stopped beating
           entirely (crashed loop, blocked thread) rather than one that beats emptily.

        Stream idleness is checked before heartbeat age because a polling loop can
        keep emitting fresh heartbeats while provider output is stalled.
        """
        pid = job.get("worker_pid")
        if pid is None:
            return None  # not yet spawned; nothing to reap
        if not osutil.process_alive(pid):
            return DEAD

        idle = self._idle_seconds(job)
        if (
            self.idle_stall_seconds
            and idle is not None
            and idle > self.idle_stall_seconds
        ):
            return ALIVE_STALLED

        age = self._heartbeat_age_seconds(job)
        if age is None:
            # No beat yet: fall back to how long the job has been running, so a worker
            # that died before its first tick still gets classified instead of hanging.
            age = self._age_seconds(job.get("started_at") or job.get("created_at"))
        if age is not None and age > self.stall_threshold_seconds:
            return ALIVE_STALLED
        return ALIVE_PROGRESSING

    def _idle_seconds(self, job: dict) -> float | None:
        """Seconds since opencode last produced output, per the latest beat.

        None when the beat predates this field (a worker still running from an older
        build) — absence must not be read as zero, or an old worker would look busy.
        """
        beat = self.read_heartbeat(job.get("job_id") or "")
        progress = (beat or {}).get("progress")
        if not isinstance(progress, dict):
            return None
        idle = progress.get("idle_seconds")
        if idle is None:
            return None
        try:
            reported = float(idle)
        except (TypeError, ValueError):
            return None
        # The beat itself may be stale; the idle window has kept growing since it was
        # written. Add that gap, otherwise a worker that stopped beating mid-wait looks
        # like it is only as idle as it was at its last beat.
        drift = self._age_seconds((beat or {}).get("at")) or 0.0
        return reported + max(0.0, drift)

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
        """Attach a liveness-probe verdict without mutating the job record.

        Side file for the same reason as the heartbeat: the worker may complete the job
        at any moment, and a probe written into the job record could overwrite that.
        """
        payload = {
            "probe": probe,
            "liveness": (
                "stalled_no_progress" if probe.get("alive") else "stalled_on_limit"
            ),
            "at": self._now(),
        }
        try:
            self._write_side(self._probe_path(job_id), payload)
        except OSError:
            pass
        return payload

    def read_probe(self, job_id: str) -> dict | None:
        return self._read_side(self._probe_path(job_id))

    def reap_stalled(
        self,
        job_id: str,
        error_type: str,
        message: str,
        next_action: str,
        probe: dict | None = None,
    ) -> dict:
        """Terminate a stalled worker and fail its job with a typed reason.

        The kill is not optional. `fail_job` releases the session lock, so without it
        the detached worker keeps burning quota against a job nobody is waiting for,
        and a later `complete_job` would resurrect a record the caller already treated
        as terminal. Best-effort: an unkillable worker still gets its job failed —
        reporting a stall the caller can act on beats blocking on a process kill.
        """
        from core.contract import make_error

        job = self.get_job(job_id) or {}

        # Claim atomically before killing so a concurrent completion becomes late output.
        try:
            record = self._load(job_id)
            record["status"] = "failed"
            record["reaped"] = True
            self._save(record)
        except (OSError, ValueError, KeyError):
            # Best effort. An unwritable record must not stop the kill below: a worker
            # left running burns quota against a job nobody is waiting for.
            pass

        kill = self._kill_worker(job)
        meta = {
            "worker_pid": job.get("worker_pid"),
            "kill": kill,
            "idle_seconds": self._idle_seconds(job),
            "stall_threshold_seconds": self.stall_threshold_seconds,
            "idle_stall_seconds": self.idle_stall_seconds,
        }
        if probe is not None:
            meta["probe"] = probe
        output = make_error(error_type, message, next_action=next_action, meta=meta)

        # Attaches the typed error and releases the session lock. `fail_job` reloads the
        # record, so the `reaped` flag claimed above survives this write. Pass reaped=True
        # too as a backstop: if the atomic claim's _save above threw (disk full, permission),
        # the flag was never persisted, and only setting it here keeps a late complete_job
        # from resurrecting the job.
        failed = self.fail_job(job_id, message, output=output, reaped=True)
        return {**output, "job_id": job_id, "status": failed.get("status", "failed")}

    def active_worker_count(self) -> int:
        """Workers in flight across ALL sessions (global concurrency).

        Counts pending jobs (a slot claimed, worker about to spawn) plus running jobs whose
        PID is still alive. A running-but-dead record (crashed, not yet reaped) does NOT
        count — it holds no worker. Cheap: a handful of small top-level JSON files.
        """
        n = 0
        for path in self.job_dir.glob("*.json"):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("reaped"):
                continue
            status = rec.get("status")
            if status == "pending":
                n += 1
            elif status == "running" and osutil.process_alive(rec.get("worker_pid")):
                n += 1
        return n

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
        payload = {
            "session_id": session_id,
            "job_id": job_id,
            "created_at": self._now(),
        }

        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = self._read_lock(lock_path)
            existing_job = (
                self.get_job(existing.get("job_id", "")) if existing else None
            )
            if existing_job and existing_job.get("status") in {"pending", "running"}:
                # Reap a dead worker instead of blocking forever.
                if self._worker_dead(existing_job):
                    self.fail_job(
                        existing_job["job_id"],
                        "worker process died before completing (reaped)",
                    )
                elif self._exceeded_max_runtime(existing_job):
                    # Same live-worker race as get_result's backstop: the existing worker
                    # may be hung past max runtime yet still alive. Claim with reaped=True so
                    # a late complete_job cannot resurrect it, and kill the tree so it stops
                    # burning quota while the new job takes the lock.
                    self.fail_job(
                        existing_job["job_id"],
                        f"job exceeded max runtime {self.max_runtime_seconds}s (reaped)",
                        reaped=True,
                    )
                    self._kill_worker(existing_job)
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
    def _request_hash(
        command: str, task: str | None, session_id: str, work_dir: str | None
    ) -> str:
        # task is optional for commands such as sweep; normalize None before hashing.
        raw = "|".join(
            [
                (command or "").strip().lower(),
                (task or "").strip(),
                session_id,
                work_dir or "",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _safe(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _new_job_id(self) -> str:
        return f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
