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
    detect_project_root,
    ensure_workflow_workspace,
    resolve_agent_workflow_path,
    run_doctor,
    run_sweep,
    workflow_paths,
)
from config.settings import (
    DEFAULT_JOB_POLL_INTERVAL_SECONDS,
    DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
    load_opencode_config,
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
                "meta": {"project_root": str(project_root), "error_type": "workflow_init_error"},
            }
        return {"ok": True, "content": "workflow workspace initialized", "meta": meta}

    if normalized_command == "doctor":
        config = load_opencode_config()
        return run_doctor(project_root, config.get("opencode_command", "opencode"))

    if normalized_command == "sweep":
        return run_sweep(project_root)

    if normalized_command == "clean":
        summary = JOB_MANAGER.prune_jobs()
        return {
            "ok": True,
            "content": f"pruned {summary['removed']} job(s), kept {summary['kept']}",
            "meta": summary,
        }

    if normalized_command == "inspect":
        return _inspect(project_root)

    session = SESSION_MANAGER.load_or_create(session_id)
    output = EXECUTOR.execute(command, task, session, work_dir, model)
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

    # Idempotent reuse: identical request already has a running worker.
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


def await_job(
    command: str,
    task: str,
    session_id: str,
    work_dir: str | None = None,
    model: str | None = None,
    poll_interval: float = DEFAULT_JOB_POLL_INTERVAL_SECONDS,
    poll_timeout: int = DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
) -> dict:
    submitted = submit(command, task, session_id, work_dir, model)
    if not submitted.get("ok"):
        return submitted

    job_id = submitted["job_id"]
    started_at = time.monotonic()
    interval = poll_interval if poll_interval > 0 else DEFAULT_JOB_POLL_INTERVAL_SECONDS

    while True:
        result = get_result(job_id)
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
                "meta": {"job_id": job_id, "submitted_at": submitted.get("submitted_at")},
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


def _inspect(project_root) -> dict:
    """Human-readable snapshot: session, workflow stage, recent jobs, last response."""
    import json as _json

    paths = workflow_paths(project_root)
    lines: list[str] = [f"# .workflow inspect — {project_root}"]

    try:
        state = _json.loads(paths["state"].read_text(encoding="utf-8"))
        session = (state.get("session") or {}).get("id")
        stage = state.get("workflow", {}).get("stage")
        lines.append(f"session: {session}")
        lines.append(f"stage: {stage}")
    except (OSError, ValueError):
        lines.append("session: (state.json unavailable)")

    job_files = sorted(JOB_MANAGER.job_dir.glob("job_*.json"), key=lambda p: p.name, reverse=True)[:5]
    lines.append(f"recent jobs ({len(job_files)}):")
    for path in job_files:
        try:
            job = _json.loads(path.read_text(encoding="utf-8"))
            lines.append(f"  - {job['job_id']} [{job['status']}] {job['command']} pid={job.get('worker_pid')}")
        except (OSError, ValueError, KeyError):
            continue

    last = ""
    try:
        last = paths["response_last"].read_text(encoding="utf-8")[:400]
    except OSError:
        pass
    lines.append("last_response_preview:")
    lines.append(last or "  (none)")

    return {"ok": True, "content": "\n".join(lines), "meta": {"project_root": str(project_root)}}


def run_worker(job_id: str) -> dict:
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        return {"ok": False, "job_id": job_id, "status": "not_found", "meta": {}}

    JOB_MANAGER.mark_running(job_id)
    try:
        output = run(
            job["command"],
            job["task"],
            job["session_id"],
            job.get("work_dir"),
            job.get("model"),
        )
        if output.get("ok"):
            JOB_MANAGER.complete_job(job_id, output)
        else:
            JOB_MANAGER.fail_job(job_id, output.get("content") or "worker failed", output=output)
        return output
    except Exception as exc:
        JOB_MANAGER.fail_job(job_id, str(exc))
        return {"ok": False, "job_id": job_id, "status": "failed", "content": str(exc), "meta": {}}


def _spawn_worker(job_id: str, work_dir: str | None = None) -> dict:
    args = [
        sys.executable,
        __file__,
        "--command",
        "worker",
        "--job-id",
        job_id,
    ]
    try:
        proc = subprocess.Popen(
            args,
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **osutil.detached_popen_kwargs(),
        )
    except OSError as exc:
        return make_error(
            "unknown",
            str(exc),
            next_action="Check python is on PATH and the job dir is writable, then retry.",
            meta={"error": type(exc).__name__},
        )
    return {"ok": True, "content": "worker started", "meta": {"pid": proc.pid}}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="agent-workflow CLI")
    parser.add_argument(
        "--command",
        "-c",
        required=True,
        choices=["init", "doctor", "explore", "plan", "analyze", "verify", "sweep", "clean", "inspect", "submit", "await", "status", "result", "worker"],
    )
    parser.add_argument("--prompt", "-p", default=None)
    parser.add_argument("--prompt-file", default=None, help="path to file containing the prompt (alternative to --prompt)")
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
    parser.add_argument("--job-id", default=None, help="job ID for status/result/worker")
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
    if args.command in {"explore", "plan", "analyze", "execute", "verify", "submit", "await"} and not prompt:
        raise SystemExit("--prompt or --prompt-file is required for this command")
    if args.command in {"status", "result", "worker"} and not args.job_id:
        raise SystemExit("--job-id is required for this command")

    effective_session = resolve_session_id(args.session, fresh=args.fresh_session)
    if args.command == "submit":
        result = submit(args.job_command, prompt, effective_session, work_dir, args.model)
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
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
