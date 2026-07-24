import subprocess
import sys
import time
from pathlib import Path

from core.contract import make_error
from core.executor import Executor
from core.job_manager import JobManager
from core.session_manager import SessionManager
from utils import osutil
from core.workflow_runtime import (
    active_jobs_for_workspace,
    detect_project_root,
    ensure_workflow_workspace,
    needs_upgrade,
    prune_sessions,
    resolve_agent_workflow_path,
    run_doctor,
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
EXECUTOR = Executor(session_manager=SESSION_MANAGER)
JOB_MANAGER = JobManager()
BACKGROUND_COMMANDS = {"explore", "plan", "analyze", "verify", "sweep"}


def resolve_session_id(session_id: str, fresh: bool = False) -> str:
    """Resolve the effective session ID, using cache or generating a new one."""
    if session_id != "default":
        return session_id
    if not fresh:
        cached = get_cached_main_session_id()
        if cached:
            return cached
    new_id = generate_main_session_id()
    set_cached_main_session_id(new_id)
    return new_id


def run(
    command: str,
    task: str,
    session_id: str,
    work_dir: str | None = None,
    model: str | None = None,
    on_progress=None,
) -> dict:
    normalized_command = command.strip().lower()
    project_root = detect_project_root(work_dir)

    if normalized_command == "init":
        resolver = resolve_agent_workflow_path(project_root)
        agent_workflow_path = resolver.get("path")
        try:
            meta = ensure_workflow_workspace(project_root, agent_workflow_path)
        except ValueError as exc:
            return {
                "ok": False,
                "content": str(exc),
                "meta": {
                    "project_root": str(project_root),
                    "error_type": "workflow_init_error",
                },
            }
        return {"ok": True, "content": "workflow workspace initialized", "meta": meta}

    if normalized_command == "upgrade":
        active = active_jobs_for_workspace(project_root)
        if active:
            return make_error(
                "job_already_running",
                f"{len(active)} job(s) still running against {project_root}; "
                "upgrading now can lose their session state",
                next_action=(
                    "Wait for the active job(s) to finish, or attach with "
                    f".workflow/check.{osutil.script_ext()} <job_id> --wait, then rerun upgrade."
                ),
                meta={"project_root": str(project_root)},
                active_job_ids=[job["job_id"] for job in active],
            )
        resolver = resolve_agent_workflow_path(project_root)
        try:
            meta = upgrade_workflow_workspace(project_root, resolver.get("path"))
        except ValueError as exc:
            return {
                "ok": False,
                "content": str(exc),
                "meta": {
                    "project_root": str(project_root),
                    "error_type": "workflow_init_error",
                    "next_action": "Run `--command init` to scaffold .workflow/ first.",
                },
            }
        moved = (
            meta["from"]["installed_tool_version"]
            != meta["to"]["installed_tool_version"]
        )
        lines = [
            (
                f"workflow workspace upgraded "
                f"{meta['from']['installed_tool_version']} -> {meta['to']['installed_tool_version']}"
                if moved
                else f"workflow workspace already at {meta['to']['installed_tool_version']} "
                f"(scripts and config keys refreshed)"
            )
        ]
        for row in meta["diverged_from_defaults"]:
            lines.append(
                f"  kept your {row['key']}={row['yours']} "
                f"(this build ships {row['shipped_default']})"
            )
        return {"ok": True, "content": "\n".join(lines), "meta": meta}

    if normalized_command == "doctor":
        config = load_opencode_config()
        return run_doctor(
            project_root, config.get("opencode_command", "opencode"), session_id
        )

    if normalized_command == "clean":
        from core import fact_store

        summary = JOB_MANAGER.prune_jobs()
        facts = fact_store.prune(project_root)
        sessions = prune_sessions(project_root)
        return {
            "ok": True,
            "content": (
                f"pruned {summary['removed']} job(s), kept {summary['kept']}; "
                f"facts kept {facts['kept']}, dropped {facts['removed']} stale; "
                f"sessions removed {sessions['removed']}, kept {sessions['kept']}"
            ),
            "meta": {**summary, "facts": facts, "sessions": sessions},
        }

    if normalized_command == "inspect":
        return _inspect(project_root, session_id)

    session = SESSION_MANAGER.load_or_create(session_id)
    output = EXECUTOR.execute(
        command, task, session, work_dir, model, on_progress=on_progress
    )
    SESSION_MANAGER.record_run(session, command)
    output["session_id"] = session_id
    return output


def submit(
    command: str,
    task: str,
    session_id: str,
    work_dir: str | None = None,
    model: str | None = None,
) -> dict:
    if command in {"submit", "status", "result", "worker"}:
        return make_error(
            "job_submit_error",
            f"unsupported submit target: {command}",
            next_action="Use a delegated command (explore/plan/analyze/verify/sweep).",
            meta={"command": command},
        )

    try:
        job = JOB_MANAGER.create_job(command, task, session_id, work_dir, model)
    except ValueError as exc:
        active = JOB_MANAGER.active_job_for_session(session_id)
        active_id = active["job_id"] if active else None
        next_action = (
            f"Wait for {active_id} or run: .workflow/check.{osutil.script_ext()} {active_id} --wait"
            if active_id
            else "Wait for the active job to finish, then retry."
        )
        return make_error(
            "job_already_running",
            str(exc),
            next_action=next_action,
            meta={"command": command},
            active_job_id=active_id,
        )

    if job.get("worker_pid") is not None or job.get("status") == "running":
        return {
            "ok": True,
            "job_id": job["job_id"],
            "status": job["status"],
            "submitted_at": job["created_at"],
            "meta": {"pid": job.get("worker_pid"), "reused": True},
        }

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
        "meta": {"pid": worker["meta"]["pid"]},
    }


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
    """One stderr line when .workflow/ was scaffolded by a different build.

    Warn, never act. Regenerating scripts under a caller that is mid-flow is exactly
    the kind of surprise the workflow exists to avoid — so the fix (`--command upgrade`)
    stays a thing the user runs deliberately. Emitted from Python rather than from the
    generated shell scripts so both OSes report it identically, and so a workspace whose
    scripts are themselves outdated still gets the warning.
    """
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
) -> dict:
    _apply_job_thresholds(work_dir)
    _warn_if_workspace_stale(work_dir)
    submitted = submit(command, task, session_id, work_dir, model)
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
        if (result.get("meta") or {}).get("error_type") == "worker_stalled":
            now = time.monotonic()
            if probe_count < max_probes and (
                last_probe_at is None or (now - last_probe_at) >= recheck
            ):
                last_probe_at = now
                probe_count += 1
                reaped = check_stalled_job(job_id, work_dir, model)
                if reaped is not None:
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
            lines.append(
                f"  - {job['job_id']} [{job['status']}] {job['command']} pid={job.get('worker_pid')}"
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

    JOB_MANAGER.mark_running(job_id)
    JOB_MANAGER.touch_heartbeat(job_id, {"phase": "starting", "elapsed_seconds": 0})

    def _beat(progress: dict) -> None:
        JOB_MANAGER.touch_heartbeat(job_id, progress)

    try:
        output = run(
            job["command"],
            job["task"],
            job["session_id"],
            job.get("work_dir"),
            job.get("model"),
            on_progress=_beat,
        )
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
    # Preserve detached-worker tracebacks, including pre-handler crashes.
    log_path = JOB_MANAGER.job_dir / "logs" / f"{job_id}.log"
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


# The heavy diagnostic keys: the echoed prompt argv and the full (unbounded) opencode
# logs. On a successful run these are noise the main_agent never reads — but they are
# the bulk of the payload (100KB+ on a long verify). The full meta is already archived
# to .workflow by write_call_meta, so dropping them from stdout loses nothing.
_HEAVY_META_KEYS = frozenset(
    {"args", "stderr", "stderr_tail", "stdout", "cwd", "raw", "bootstrap"}
)


def _slim_result(result: dict) -> dict:
    """Trim the stdout payload to what the main_agent actually consumes.

    Only on success, and only the heavy diagnostic keys inside meta: top-level keys
    (content, digest, job_id, status, session_id) and the small actionable meta
    (policy, session_reset, ...) are kept, so submit/status/result payloads stay intact.
    Failures pass through untouched — their stderr/args/next_action IS the diagnosis.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return result
    slim_meta = {k: v for k, v in meta.items() if k not in _HEAVY_META_KEYS}
    return {**result, "meta": slim_meta}


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
        help="force a new main session ID, bypassing cache",
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
        choices=["explore", "plan", "analyze", "verify", "sweep"],
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
        args.command
        in {"explore", "plan", "analyze", "execute", "verify", "submit", "await"}
        and not prompt
    ):
        raise SystemExit("--prompt or --prompt-file is required for this command")
    if args.command in {"status", "result", "worker"} and not args.job_id:
        raise SystemExit("--job-id is required for this command")

    effective_session = resolve_session_id(args.session, fresh=args.fresh_session)
    if args.command == "submit":
        result = submit(
            args.job_command, prompt, effective_session, work_dir, args.model
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
        )
    elif args.command == "status":
        result = get_status(args.job_id)
    elif args.command == "result":
        result = get_result(args.job_id)
    elif args.command == "worker":
        result = run_worker(args.job_id)
    elif should_run_in_background(args.command):
        result = submit(args.command, prompt, effective_session, work_dir, args.model)
    else:
        result = run(args.command, prompt, effective_session, work_dir, args.model)
    result = _slim_result(result)
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
