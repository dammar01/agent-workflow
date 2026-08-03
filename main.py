import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from core.contract import make_error, validate_verification_contract
from core.executor import Executor
from core.job_manager import DEAD, JobManager
from core.session_manager import SessionManager
from utils import osutil
from utils.path_guard import safe_path_component
from utils.redact import redact_value
from core.workflow_runtime import (
    acquire_runtime_lock,
    detect_project_root,
    ensure_workflow_workspace,
    needs_upgrade,
    prune_sessions,
    release_runtime_lock,
    resolve_agent_workflow_path,
    run_doctor,
    run_sweep,
    upgrade_workflow_workspace,
    workflow_paths,
    workspace_versions,
)
from config.settings import (
    DEFAULT_IDLE_STALL_SECONDS,
    DEFAULT_JOB_POLL_INTERVAL_SECONDS,
    DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
    DEFAULT_MAX_PROBES,
    DEFAULT_PROBE_RECHECK_SECONDS,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    DEFAULT_STALL_THRESHOLD_SECONDS,
    load_opencode_config,
    load_opencode_config_for,
    get_cached_main_session_id,
    set_cached_main_session_id,
)
from utils.parser import generate_main_session_id

SESSION_MANAGER = SessionManager()
_DEFAULT_SESSION_MANAGER = SESSION_MANAGER
EXECUTOR = Executor(session_manager=SESSION_MANAGER)
JOB_MANAGER = JobManager()
BACKGROUND_COMMANDS = {"explore", "plan", "analyze", "verify"}


def _finalize_verify_result(command: str, result: dict) -> dict:
    if command.strip().lower() != "verify" or not isinstance(result, dict):
        return result
    if not result.get("ok"):
        return result

    meta = result.setdefault("meta", {})
    if meta.get("mode") == "quick" or "quick_verify" in meta:
        return result

    assessment = validate_verification_contract(result.get("content") or "")
    meta["verdict"] = assessment["verdict"]
    meta["verify_contract"] = assessment
    warnings = assessment.get("warnings") or []
    if warnings:
        meta.setdefault("contract_warnings", []).extend(warnings)
    return result


def _session_storage_id(session_id: str) -> str:
    return safe_path_component(session_id)


def _session_manager_for(project_root: Path) -> SessionManager:
    if SESSION_MANAGER is not _DEFAULT_SESSION_MANAGER:
        return SESSION_MANAGER
    return SessionManager(
        workflow_paths(project_root)["workflow_dir"] / "provider-sessions"
    )


def resolve_session_id(
    session_id: str, fresh: bool = False, project_root: str | Path | None = None
) -> str:
    """Resolve the effective session ID, using cache or generating a new one."""
    cache_root = (
        detect_project_root(str(project_root)) if project_root is not None else None
    )
    if session_id != "default" and not fresh:
        return session_id
    if not fresh:
        cached = get_cached_main_session_id(cache_root)
        if cached:
            return cached
    new_id = f"{generate_main_session_id()}_{secrets.token_hex(3)}"
    if session_id == "default":
        set_cached_main_session_id(new_id, cache_root)
    return new_id


def run(
    command: str,
    task: str,
    session_id: str,
    work_dir: str | None = None,
    model: str | None = None,
    on_progress=None,
    allow_reuse: bool = True,
    require_provider_session: bool = False,
) -> dict:
    normalized_command = command.strip().lower()
    project_root = detect_project_root(work_dir)

    if normalized_command == "init":
        resolver = resolve_agent_workflow_path(project_root)
        agent_workflow_path = resolver.get("path")
        try:
            meta = ensure_workflow_workspace(project_root, agent_workflow_path)
        except (OSError, ValueError) as exc:
            return make_error(
                "workflow_init_error",
                str(exc),
                next_action="Fix the reported workspace path/config issue, then retry init.",
                meta={"project_root": str(project_root)},
            )
        return {"ok": True, "content": "workflow workspace initialized", "meta": meta}

    if normalized_command == "doctor":
        config = load_opencode_config()
        return run_doctor(
            project_root, config.get("opencode_command", "opencode"), session_id
        )

    if normalized_command == "upgrade":
        resolver = resolve_agent_workflow_path(project_root)
        try:
            meta = upgrade_workflow_workspace(project_root, resolver.get("path"))
        except (OSError, ValueError) as exc:
            return make_error(
                "workflow_upgrade_error",
                str(exc),
                next_action=(
                    "Initialize the workspace first or finish active jobs, then retry upgrade."
                ),
                meta={"project_root": str(project_root)},
            )
        return {
            "ok": True,
            "content": "workflow workspace upgraded",
            "meta": meta,
        }

    if normalized_command == "clean":
        from core import fact_store

        locks = JOB_MANAGER.release_stale_session_locks()
        summary = JOB_MANAGER.prune_jobs()
        facts = fact_store.prune(project_root)
        sessions = prune_sessions(project_root)
        return {
            "ok": True,
            "content": (
                f"released {len(locks['released'])} stale session lock(s), "
                f"kept {locks['kept']}; "
                f"pruned {summary['removed']} job(s), kept {summary['kept']}; "
                f"logs removed {summary['logs_removed']}; "
                f"facts kept {facts['kept']}, dropped {facts['removed']} stale; "
                f"sessions removed {sessions['removed']}, kept {sessions['kept']}"
            ),
            "meta": {**summary, "locks": locks, "facts": facts, "sessions": sessions},
        }

    if normalized_command == "inspect":
        return _inspect(project_root, session_id)

    if normalized_command == "sweep":
        output = run_sweep(project_root, session_id)
        output["session_id"] = session_id
        return output

    lock_path = workflow_paths(project_root, session_id)["lock"]
    lock_claim = acquire_runtime_lock(lock_path, normalized_command, session_id)
    if not lock_claim.get("ok"):
        payload = lock_claim.get("payload") or {}
        return make_error(
            "runtime_lock",
            f"runtime lock active for session {payload.get('session_id') or 'unknown'}",
            next_action="Wait for the in-flight call on this session to finish, then retry.",
            meta={"lock": payload, "lock_path": str(lock_path)},
        )
    try:
        provider_sessions = _session_manager_for(project_root)
        try:
            session = provider_sessions.load_or_create(_session_storage_id(session_id))
        except (OSError, ValueError) as exc:
            return make_error(
                "session_state_error",
                str(exc),
                next_action="Repair or remove the project-local provider session file, then retry.",
                meta={"project_root": str(project_root)},
            )
        if require_provider_session and not session.get("opencode_session_id"):
            return make_error(
                "session_capture_failed",
                "cannot recover interrupted job because its OpenCode session ID was not captured",
                next_action=(
                    "The session lock was released. Run the original task again as a clean invocation."
                ),
                meta={"reason": "recovery_session_unavailable"},
            )
        output = EXECUTOR.execute(
            command,
            task,
            session,
            work_dir,
            model,
            on_progress=on_progress,
            allow_reuse=allow_reuse,
            session_manager=provider_sessions,
            workflow_session_id=session_id,
            _runtime_lock=lock_claim,
        )
        output = _finalize_verify_result(normalized_command, output)
        try:
            provider_sessions.record_run(session, command)
        except (OSError, ValueError) as exc:
            output.setdefault("meta", {})[
                "session_history_error"
            ] = f"{type(exc).__name__}: {exc}"
        output["session_id"] = session_id
        return output
    finally:
        release_runtime_lock(lock_path, session_id, lock_claim.get("token"))


def submit(
    command: str,
    task: str,
    session_id: str,
    work_dir: str | None = None,
    model: str | None = None,
    allow_reuse: bool = True,
) -> dict:
    normalized_command = command.strip().lower()
    if normalized_command not in BACKGROUND_COMMANDS:
        return make_error(
            "job_submit_error",
            f"unsupported submit target: {command}",
            next_action="Use a delegated command (explore/plan/analyze/verify).",
            meta={"command": normalized_command},
        )

    expected_hash = JOB_MANAGER.request_hash(
        normalized_command, task, session_id, work_dir, allow_reuse, model
    )
    recovering = False

    def capacity_error() -> dict:
        return make_error(
            "worker_capacity",
            f"global worker limit reached ({JOB_MANAGER.max_global_workers} active)",
            next_action="Wait for an in-flight delegated job to finish, then retry.",
            meta={
                "command": normalized_command,
                "max_global_workers": JOB_MANAGER.max_global_workers,
            },
        )

    try:
        with JOB_MANAGER.capacity_guard():
            active = JOB_MANAGER.active_job_for_session(session_id)
            if active:
                if active.get("request_hash") != expected_hash:
                    active_id = active["job_id"]
                    return make_error(
                        "job_already_running",
                        f"session {session_id} already has active job {active_id}",
                        next_action=(
                            f"Wait for {active_id} or run: "
                            f".workflow/check.{osutil.script_ext()} {active_id} --wait"
                        ),
                        meta={"command": normalized_command},
                        active_job_id=active_id,
                    )

                if active.get("worker_pid") and JOB_MANAGER.liveness(active) != DEAD:
                    return {
                        "ok": True,
                        "job_id": active["job_id"],
                        "status": active["status"],
                        "submitted_at": active["created_at"],
                        "meta": {
                            "pid": active.get("worker_pid"),
                            "reused": True,
                            "recovery": False,
                        },
                    }

                pending_reservation = (
                    active.get("status") == "pending"
                    and active.get("worker_pid") is None
                )
                if (
                    not pending_reservation
                    and JOB_MANAGER.active_worker_count()
                    >= JOB_MANAGER.max_global_workers
                ):
                    return capacity_error()

                recovery = JOB_MANAGER.claim_recovery(active["job_id"])
                action = recovery.get("action")
                job = recovery.get("job") or active
                if action in {"wait", "attach"}:
                    waiting_for_recovery = bool(job.get("recovery_in_progress"))
                    return {
                        "ok": True,
                        "job_id": job["job_id"],
                        "status": job.get("status", "pending"),
                        "submitted_at": job["created_at"],
                        "meta": {
                            "pid": job.get("worker_pid"),
                            "reused": True,
                            "recovery": waiting_for_recovery,
                            "recovery_pending": waiting_for_recovery,
                        },
                    }
                if action == "exhausted":
                    return make_error(
                        "worker_died",
                        f"job {job['job_id']} exhausted its single recovery attempt",
                        next_action=(
                            "The session lock was released. Report the interruption or "
                            "invoke the original task again as a clean run; do not send "
                            "continue automatically."
                        ),
                        meta={
                            "reason": "recovery_exhausted",
                            "job_id": job["job_id"],
                            "recovery_attempt": job.get("recovery_attempt", 1),
                        },
                    )
                if action != "recover":
                    return make_error(
                        "job_submit_error",
                        f"cannot recover active job {active['job_id']}",
                        next_action="Inspect the job record, then retry the original task.",
                        meta={"reason": action or "recovery_unknown"},
                    )
                recovering = True
            else:
                if JOB_MANAGER.active_worker_count() >= JOB_MANAGER.max_global_workers:
                    return capacity_error()
                try:
                    job = JOB_MANAGER.create_job(
                        normalized_command,
                        task,
                        session_id,
                        work_dir,
                        model,
                        allow_reuse,
                    )
                except ValueError as exc:
                    active = JOB_MANAGER.active_job_for_session(session_id)
                    active_id = active["job_id"] if active else None
                    next_action = (
                        f"Wait for {active_id} or run: "
                        f".workflow/check.{osutil.script_ext()} {active_id} --wait"
                        if active_id
                        else "Wait for the active job to finish, then retry."
                    )
                    return make_error(
                        "job_already_running",
                        str(exc),
                        next_action=next_action,
                        meta={"command": normalized_command},
                        active_job_id=active_id,
                    )
    except OSError as exc:
        return make_error(
            "job_submit_error",
            str(exc),
            next_action="Check that the job store is writable, then retry.",
            meta={"command": normalized_command, "error": type(exc).__name__},
        )

    try:
        worker = _spawn_worker(job["job_id"], work_dir)
        if not worker["ok"]:
            JOB_MANAGER.fail_job(job["job_id"], worker["content"])
            return worker
        JOB_MANAGER.set_worker_pid(job["job_id"], worker["meta"]["pid"])
        return {
            "ok": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "submitted_at": job["created_at"],
            "meta": {
                "pid": worker["meta"]["pid"],
                "reused": recovering,
                "recovery": recovering,
                "recovery_attempt": job.get("recovery_attempt", 0),
            },
        }
    finally:
        if recovering:
            JOB_MANAGER.release_recovery_claim(job["job_id"])


def get_status(job_id: str) -> dict:
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        return {"ok": False, "job_id": job_id, "status": "not_found", "meta": {}}
    return {"ok": True, **job}


def get_result(job_id: str) -> dict:
    return JOB_MANAGER.get_result(job_id)


def should_run_in_background(command: str) -> bool:
    return command.strip().lower() in BACKGROUND_COMMANDS


def _job_config(work_dir: str | None) -> dict:
    return load_opencode_config_for(detect_project_root(work_dir))


def _apply_job_thresholds(work_dir: str | None) -> None:
    """Push the project's stall/idle thresholds onto the shared JobManager.

    These live in .workflow/opencode.json next to the other adapter timings, but the
    JobManager is constructed at import time with the tool defaults — so without this
    a project that tuned them got the defaults anyway.
    """
    config = _job_config(work_dir)
    try:
        JOB_MANAGER.stall_threshold_seconds = int(
            config.get("stall_threshold_seconds", DEFAULT_STALL_THRESHOLD_SECONDS)
        )
        JOB_MANAGER.idle_stall_seconds = int(
            config.get("idle_stall_seconds", DEFAULT_IDLE_STALL_SECONDS)
        )
    except (TypeError, ValueError):
        pass


def _probe_recheck_seconds(work_dir: str | None) -> float:
    try:
        return max(
            10.0,
            float(
                _job_config(work_dir).get(
                    "probe_recheck_seconds", DEFAULT_PROBE_RECHECK_SECONDS
                )
            ),
        )
    except (TypeError, ValueError):
        return float(DEFAULT_PROBE_RECHECK_SECONDS)


def _max_probes(work_dir: str | None) -> int:
    try:
        return max(
            1,
            int(_job_config(work_dir).get("max_probes", DEFAULT_MAX_PROBES)),
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_PROBES


def _warn_if_workspace_stale(work_dir: str | None) -> None:
    """Warn when the workspace version differs; upgrade remains explicit."""
    try:
        project_root = detect_project_root(work_dir)
        if not needs_upgrade(project_root):
            return
        versions = workspace_versions(project_root)
    except (OSError, ValueError):
        return
    print(
        f"[workflow] WARN: .workflow was built by tool "
        f"{versions['installed_tool_version']} / config {versions['installed_config_version']}, "
        f"running {versions['current_tool_version']} / {versions['current_config_version']} — "
        f'run: python main.py --command upgrade --work-dir "{project_root}"',
        file=sys.stderr,
    )


def probe_second_agent(work_dir: str | None = None, model: str | None = None) -> dict:
    """Ask the second agent a trivial question in a fresh session.

    This is the only signal that separates "rate limited, still coming back" from
    "hung, never coming back" — the worker PID looks identical in both cases.
    """
    project_root = detect_project_root(work_dir)
    config = load_opencode_config_for(project_root)
    from adapters.opencode_adapter import OpenCodeAdapter

    adapter = OpenCodeAdapter(command=config.get("opencode_command", "opencode"))
    return adapter.probe(
        model=model or config.get("default_model"),
        work_dir=str(project_root),
        timeout_seconds=int(
            config.get("probe_timeout_seconds", DEFAULT_PROBE_TIMEOUT_SECONDS)
        ),
    )


def check_stalled_job(
    job_id: str, work_dir: str | None = None, model: str | None = None
) -> dict | None:
    """Two independent checks on a stalled job; either failure reaps it.

    1. Is the worker PID still a live process?
    2. Does the second agent answer a trivial prompt in a SEPARATE session?

    Check 2 is what check 1 cannot do. A rate-limited opencode holds the PID and keeps
    the poll loop turning, so PID liveness reports health for a job that will never
    return. Asking a fresh session is the only way to distinguish "quota exhausted" from
    "genuinely working" — and it must be a fresh session, because the stuck one is the
    thing under suspicion.

    Returns the terminal result when the job was reaped, or None to keep waiting.
    """
    job = JOB_MANAGER.get_job(job_id) or {}
    pid = job.get("worker_pid")

    if pid is None or not osutil.process_alive(pid):
        return JOB_MANAGER.reap_stalled(
            job_id,
            "worker_died",
            f"worker process {pid} is gone while job {job_id} was still running (reaped)",
            next_action=(
                "Check .workflow job logs for the worker traceback, then resubmit the command."
            ),
        )

    probe = probe_second_agent(work_dir, model)
    try:
        JOB_MANAGER.record_probe(job_id, probe)
    except (OSError, KeyError, ValueError):
        pass

    if probe.get("alive"):
        return None

    if probe.get("rate_limited") or probe.get("reason") == "probe_rate_limited":
        return JOB_MANAGER.reap_stalled(
            job_id,
            "rate_limited",
            (
                f"job {job_id} stalled and the second agent is refusing on quota "
                f"({probe.get('reason')}) — reaped instead of waiting out the limit"
            ),
            next_action=(
                "Second agent is out of quota. Wait for the limit to reset, switch model in "
                ".workflow/opencode.json, or check the provider account, then rerun the command."
            ),
            probe=probe,
        )

    if probe.get("stream_failed") or probe.get("reason") == "probe_stream_failed":
        # A partial provider stream is transient; reap the stalled job for resubmission.
        return JOB_MANAGER.reap_stalled(
            job_id,
            "streaming_failed",
            (
                f"job {job_id} stalled and the probe's provider stream dropped "
                f"({probe.get('reason')}) — reaped as transient, not as an outage"
            ),
            next_action=(
                "The provider stream is dropping, not refusing. Resubmit the command; if it "
                "dies again, split the task into two narrower calls. Do NOT wait for a quota reset."
            ),
            probe=probe,
        )

    return JOB_MANAGER.reap_stalled(
        job_id,
        "second_agent_unavailable",
        (
            f"job {job_id} stalled and a fresh-session probe also failed "
            f"({probe.get('reason')}) — reaped"
        ),
        next_action=(
            "Second agent is not answering at all. Check `opencode run` works manually "
            "and that it is logged in, then rerun the command."
        ),
        probe=probe,
    )


def await_job(
    command: str,
    task: str,
    session_id: str,
    work_dir: str | None = None,
    model: str | None = None,
    poll_interval: float = DEFAULT_JOB_POLL_INTERVAL_SECONDS,
    poll_timeout: int = DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
    allow_reuse: bool = True,
) -> dict:
    if command.strip().lower() == "sweep":
        return run("sweep", task, session_id, work_dir, model)

    _apply_job_thresholds(work_dir)
    _warn_if_workspace_stale(work_dir)
    submitted = submit(command, task, session_id, work_dir, model, allow_reuse)
    if not submitted.get("ok"):
        return submitted

    job_id = submitted["job_id"]
    started_at = time.monotonic()
    interval = poll_interval if poll_interval > 0 else DEFAULT_JOB_POLL_INTERVAL_SECONDS
    recheck_base = _probe_recheck_seconds(work_dir)
    max_probes = _max_probes(work_dir)
    last_probe_at: float | None = None
    probe_count = 0
    recheck = recheck_base

    while True:
        result = get_result(job_id)
        if (result.get("meta") or {}).get("error_type") == "worker_died" and (
            result.get("meta") or {}
        ).get("recoverable"):
            return {
                "ok": False,
                "content": result.get("content"),
                "meta": {
                    **dict(result.get("meta") or {}),
                    "job_id": job_id,
                    "submitted_at": submitted.get("submitted_at"),
                },
            }
        if (result.get("meta") or {}).get("error_type") == "worker_stalled":
            now = time.monotonic()
            if probe_count < max_probes and (
                last_probe_at is None or (now - last_probe_at) >= recheck
            ):
                last_probe_at = now
                probe_count += 1
                reaped = check_stalled_job(job_id, work_dir, model)
                if reaped is not None:
                    if reaped.get("status") == "completed":
                        continue
                    meta = dict(reaped.get("meta") or {})
                    meta.setdefault("job_id", job_id)
                    meta.setdefault("submitted_at", submitted.get("submitted_at"))
                    meta.setdefault("probe_count", probe_count)
                    return {
                        "ok": False,
                        "content": reaped.get("content") or f"job {job_id} reaped",
                        "meta": meta,
                    }
                # Probe came back alive: back off before spending the next one.
                recheck = min(recheck * 2, recheck_base * 8)
        if result.get("status") == "completed":
            output = result.get("output") or {}
            if isinstance(output, dict):
                output = _finalize_verify_result(command, output)
                meta = output.setdefault("meta", {})
                meta.setdefault("job_id", job_id)
                meta.setdefault("submitted_at", submitted.get("submitted_at"))
                meta.setdefault("worker_pid", submitted.get("meta", {}).get("pid"))
                return output
            return {
                "ok": False,
                "content": f"job {job_id} returned invalid output payload",
                "meta": {
                    "job_id": job_id,
                    "submitted_at": submitted.get("submitted_at"),
                },
            }

        if result.get("status") == "failed":
            meta = dict(result.get("meta") or {})
            meta.setdefault("job_id", job_id)
            meta.setdefault("submitted_at", submitted.get("submitted_at"))
            return {
                "ok": False,
                "content": result.get("content") or f"job {job_id} failed",
                "meta": meta,
            }

        if result.get("status") == "pending":
            queued = JOB_MANAGER.get_job(job_id) or {}
            if queued.get("worker_pid") is None:
                resumed = submit(
                    command, task, session_id, work_dir, model, allow_reuse
                )
                if not resumed.get("ok"):
                    return resumed
                submitted = resumed

        if poll_timeout > 0 and (time.monotonic() - started_at) >= poll_timeout:
            return {
                "ok": False,
                "content": f"await timeout after {poll_timeout}s",
                "meta": {
                    "job_id": job_id,
                    "status": result.get("status"),
                    "submitted_at": submitted.get("submitted_at"),
                    "worker_pid": submitted.get("meta", {}).get("pid"),
                },
            }

        time.sleep(interval)


def _inspect(project_root, session_id: str | None = None) -> dict:
    """Human-readable snapshot: session, workflow stage, recent jobs, last response."""
    import json as _json

    paths = workflow_paths(project_root, session_id)
    lines: list[str] = [f"# .workflow inspect — {project_root}"]

    try:
        state = _json.loads(paths["state"].read_text(encoding="utf-8"))
        session = (state.get("session") or {}).get("id")
        stage = state.get("workflow", {}).get("stage")
        lines.append(f"session: {session}")
        lines.append(f"stage: {stage}")
    except (OSError, ValueError):
        lines.append("session: (state.json unavailable)")

    job_files = sorted(
        JOB_MANAGER.job_dir.glob("job_*.json"), key=lambda p: p.name, reverse=True
    )[:5]
    lines.append(f"recent jobs ({len(job_files)}):")
    for path in job_files:
        try:
            job = _json.loads(path.read_text(encoding="utf-8"))
            display_status = (
                "recovering" if job.get("recovery_in_progress") else job["status"]
            )
            lines.append(
                f"  - {job['job_id']} [{display_status}] {job['command']} "
                f"pid={job.get('worker_pid')} recovery={job.get('recovery_attempt', 0)}"
            )
        except (OSError, ValueError, KeyError):
            continue

    last = ""
    try:
        last = paths["response_last"].read_text(encoding="utf-8")[:400]
    except OSError:
        pass
    lines.append("last_response_preview:")
    lines.append(last or "  (none)")

    return {
        "ok": True,
        "content": "\n".join(lines),
        "meta": {"project_root": str(project_root)},
    }


def run_worker(job_id: str) -> dict:
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        return {"ok": False, "job_id": job_id, "status": "not_found", "meta": {}}

    # The worker is authoritative for its own PID. Parent-side registration is only a
    # fast-path; this closes the small spawn-to-persist race after caller interruption.
    JOB_MANAGER.set_worker_pid(job_id, os.getpid())
    running = JOB_MANAGER.mark_running(job_id)
    if running.get("status") != "running":
        return make_error(
            "worker_died",
            f"job {job_id} became terminal before its worker started",
            next_action="Inspect the terminal job result; do not restart this worker.",
            meta={"job_id": job_id, "status": running.get("status")},
        )
    JOB_MANAGER.touch_heartbeat(job_id, {"phase": "starting", "elapsed_seconds": 0})

    def _beat(progress: dict) -> None:
        JOB_MANAGER.touch_heartbeat(job_id, progress)

    try:
        effective_task = job["task"]
        require_provider_session = int(job.get("recovery_attempt") or 0) > 0
        if require_provider_session:
            effective_task = (
                f"Continue the interrupted task for job {job_id}.\n"
                f"Original task: {job['task']}\n"
                "Inspect the existing session state. Resume unfinished work only; "
                "do not repeat completed findings. Return the normal evidence contract."
            )

        output = run(
            job["command"],
            effective_task,
            job["session_id"],
            job.get("work_dir"),
            job.get("model"),
            on_progress=_beat,
            allow_reuse=bool(job.get("allow_reuse", True)),
            require_provider_session=require_provider_session,
        )
        output.setdefault("meta", {}).setdefault("job_id", job_id)
        if output.get("ok"):
            JOB_MANAGER.complete_job(job_id, output)
        else:
            JOB_MANAGER.fail_job(
                job_id, output.get("content") or "worker failed", output=output
            )
        return output
    except Exception as exc:
        JOB_MANAGER.fail_job(job_id, str(exc))
        return {
            "ok": False,
            "job_id": job_id,
            "status": "failed",
            "content": str(exc),
            "meta": {},
        }


def _spawn_worker(job_id: str, work_dir: str | None = None) -> dict:
    args = [
        sys.executable,
        __file__,
        "--command",
        "worker",
        "--job-id",
        job_id,
    ]
    log_path = JOB_MANAGER.log_path(job_id)
    log_handle = subprocess.DEVNULL
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        log_handle = subprocess.DEVNULL  # never block spawning on logging
    try:
        proc = subprocess.Popen(
            args,
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=(
                subprocess.STDOUT
                if log_handle is not subprocess.DEVNULL
                else subprocess.DEVNULL
            ),
            **osutil.detached_popen_kwargs(),
        )
    except OSError as exc:
        return make_error(
            "unknown",
            str(exc),
            next_action="Check python is on PATH and the job dir is writable, then retry.",
            meta={"error": type(exc).__name__},
        )
    finally:
        if log_handle is not subprocess.DEVNULL:
            log_handle.close()  # child inherited its own fd; parent copy not needed
    meta = {"pid": proc.pid}
    if log_handle is not subprocess.DEVNULL:
        meta["log"] = str(log_path)
    return {"ok": True, "content": "worker started", "meta": meta}


_HEAVY_META_KEYS = frozenset(
    {"args", "stderr", "stderr_tail", "stdout", "cwd", "raw", "bootstrap"}
)


def _without_raw_args(value):
    if isinstance(value, dict):
        raw_args = value.get("args")
        clean = {
            key: _without_raw_args(child)
            for key, child in value.items()
            if key != "args"
        }
        if isinstance(raw_args, (list, tuple)):
            clean.setdefault("argv_count", len(raw_args))
            clean.setdefault("argv_chars", sum(len(str(arg)) for arg in raw_args))
        return clean
    if isinstance(value, list):
        return [_without_raw_args(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_without_raw_args(child) for child in value)
    return value


def _slim_result(result: dict) -> dict:
    """Redact every payload and trim bulky success-only diagnostics."""
    clean, redactions = redact_value(_without_raw_args(result))
    if not isinstance(clean, dict):
        return clean
    meta = clean.get("meta")
    if not isinstance(meta, dict):
        return clean
    if redactions:
        meta["boundary_redactions"] = redactions
        meta["boundary_redaction_count"] = sum(
            int(hit.get("count") or 0) for hit in redactions
        )
    if not clean.get("ok"):
        return clean
    slim_meta = {k: v for k, v in meta.items() if k not in _HEAVY_META_KEYS}
    return {**clean, "meta": slim_meta}


def _verify_exit_code(
    command: str, result: dict, job_command: str | None = None
) -> int:
    """Return nonzero when a completed verification is not a clean pass."""
    effective_command = job_command if command in {"await", "result"} else command
    if effective_command != "verify":
        return 0
    verification = result
    if command == "result" and isinstance(result, dict):
        stored_output = result.get("output")
        if isinstance(stored_output, dict):
            verification = stored_output
    if not isinstance(verification, dict) or not verification.get("ok"):
        return 2
    if command == "verify" and verification.get("status") in {"pending", "running"}:
        return 0
    verdict = (verification.get("meta") or {}).get("verdict")
    if verdict is None:
        verdict = validate_verification_contract(verification.get("content") or "")[
            "verdict"
        ]
    return 0 if verdict == "pass" else 2


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="agent-workflow CLI")
    parser.add_argument(
        "--command",
        "-c",
        required=True,
        choices=[
            "init",
            "upgrade",
            "doctor",
            "explore",
            "plan",
            "analyze",
            "verify",
            "sweep",
            "clean",
            "inspect",
            "submit",
            "await",
            "status",
            "result",
            "worker",
        ],
    )
    parser.add_argument("--prompt", "-p", default=None)
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="path to file containing the prompt (alternative to --prompt)",
    )
    parser.add_argument("--session", "-s", default="default")
    parser.add_argument(
        "--fresh-session",
        action="store_true",
        help="force a new main session ID and bypass evidence reuse",
    )
    parser.add_argument(
        "--work-dir",
        "-w",
        default=None,
        help="project directory context for cache keys (default: cwd)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="OpenCode model override: provider/model_key",
    )
    parser.add_argument(
        "--job-id", default=None, help="job ID for status/result/worker"
    )
    parser.add_argument(
        "--job-command",
        default="explore",
        choices=["explore", "plan", "analyze", "verify"],
        help="workflow command to execute asynchronously via submit",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_JOB_POLL_INTERVAL_SECONDS,
        help="seconds between job status polls for await mode",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
        help="max seconds to wait in await mode; 0 means wait forever",
    )
    parser.add_argument("--pretty", action="store_true", help="pretty print output")
    args = parser.parse_args()

    work_dir = str(Path(args.work_dir).resolve()) if args.work_dir else str(Path.cwd())

    prompt = args.prompt
    if args.prompt_file:
        if prompt:
            raise SystemExit("cannot use both --prompt and --prompt-file")
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise SystemExit(f"prompt file not found: {args.prompt_file}")
        except IsADirectoryError:
            raise SystemExit(f"prompt file is a directory: {args.prompt_file}")
        except OSError as exc:
            raise SystemExit(f"cannot read prompt file: {exc}")
    if (
        args.command in {"explore", "plan", "analyze", "verify", "submit", "await"}
        and not prompt
    ):
        raise SystemExit("--prompt or --prompt-file is required for this command")
    if args.command in {"status", "result", "worker"} and not args.job_id:
        raise SystemExit("--job-id is required for this command")

    effective_session = resolve_session_id(
        args.session, fresh=args.fresh_session, project_root=work_dir
    )
    allow_reuse = not args.fresh_session
    if args.command == "submit":
        result = submit(
            args.job_command,
            prompt,
            effective_session,
            work_dir,
            args.model,
            allow_reuse,
        )
    elif args.command == "await":
        result = await_job(
            args.job_command,
            prompt,
            effective_session,
            work_dir,
            args.model,
            args.poll_interval,
            args.poll_timeout,
            allow_reuse,
        )
    elif args.command == "status":
        result = get_status(args.job_id)
    elif args.command == "result":
        result = get_result(args.job_id)
    elif args.command == "worker":
        result = run_worker(args.job_id)
    elif should_run_in_background(args.command):
        result = submit(
            args.command,
            prompt,
            effective_session,
            work_dir,
            args.model,
            allow_reuse,
        )
    else:
        result = run(
            args.command,
            prompt,
            effective_session,
            work_dir,
            args.model,
            allow_reuse=allow_reuse,
        )
    exit_job_command = args.job_command
    if args.command == "result" and args.job_id:
        stored_job = JOB_MANAGER.get_job(args.job_id)
        if stored_job:
            exit_job_command = str(stored_job.get("command") or exit_job_command)
    result = _slim_result(result)
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
    raise SystemExit(_verify_exit_code(args.command, result, exit_job_command))
