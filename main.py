import secrets
import sys

# Looks unused to a linter and is NOT: core/job_lifecycle.py reaches it as
# `_main().subprocess`, and test_scenario.py patches `main.subprocess.Popen` to
# intercept worker spawn. Deleting it breaks both, silently and at runtime only.
import subprocess  # noqa: F401
from pathlib import Path

from core.contract import make_error
from core.executor import Executor
from core.job_manager import JobManager
from core.session_manager import SessionManager
from utils.path_guard import safe_path_component
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
    # These two are read as `_main().DEFAULT_*` from core/result_shaping.py, and the
    # harness lowers them on `main` to exercise the ref_only threshold. Imported here
    # so that indirection has something to find — not unused, just used elsewhere.
    DEFAULT_CONTENT_PREVIEW_CHARS,  # noqa: F401
    DEFAULT_SLIM_CONTENT_MIN_CHARS,  # noqa: F401
    DEFAULT_JOB_POLL_INTERVAL_SECONDS,
    DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    load_provider_config,
    load_provider_config_for,
    MAIN_SESSION_CACHE_TTL_SECONDS,
    cached_main_session_age,
    get_cached_main_session_id,
    set_cached_main_session_id,
)
from utils.parser import generate_main_session_id

SESSION_MANAGER = SessionManager()
_DEFAULT_SESSION_MANAGER = SESSION_MANAGER
EXECUTOR = Executor(session_manager=SESSION_MANAGER)
JOB_MANAGER = JobManager()
BACKGROUND_COMMANDS = {"explore", "plan", "analyze", "verify"}

# Split out in v3.4.3. Re-exported so every existing caller of `main.<name>`
# keeps working; the singletons above stay here because this is the entry point.
from core.job_lifecycle import (  # noqa: E402,F401
    _apply_job_thresholds,
    _job_config,
    _max_probes,
    _probe_recheck_seconds,
    _spawn_worker,
    await_job,
    check_stalled_job,
    get_result,
    get_status,
    run_worker,
    should_run_in_background,
    submit,
)
from core.result_shaping import (  # noqa: E402,F401
    _as_ref_only,
    _finalize_verify_result,
    _HEAVY_META_KEYS,
    _slim_result,
    _verify_exit_code,
    _without_raw_args,
)


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
        if cached and _cached_session_still_current(cached, cache_root):
            # Restamp: a session that keeps resolving must not age out mid-use.
            set_cached_main_session_id(cached, cache_root)
            return cached
    new_id = f"{generate_main_session_id()}_{secrets.token_hex(3)}"
    if session_id == "default":
        set_cached_main_session_id(new_id, cache_root)
    return new_id


def _cached_session_still_current(session_id: str, cache_root) -> bool:
    """Whether a cached "default" session may still claim new work.

    The cache used to be written once and read forever, so the first session ever run
    against a project captured every later call that fell back to "default" — its jobs,
    its lock, its logs. A cached session stays current only while it is still working,
    or while it was resolved recently enough to still be the one at the keyboard.
    """
    try:
        from core.job_manager import JobManager

        if JobManager().active_job_for_session(session_id):
            return True
    except (OSError, ValueError):
        # Job state unreadable — fall through to the age check rather than guessing.
        pass
    age = cached_main_session_age(cache_root)
    if age is None:
        # Pre-TTL cache file with no stamp. Honour it once; the restamp above gives it one.
        return True
    return age < MAIN_SESSION_CACHE_TTL_SECONDS


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
        # init scaffolds; it deliberately never rewrites an existing config.json — that is
        # upgrade's job, and only upgrade holds the active-job guard for it. Say so, or a
        # workspace built by an older tool looks freshly initialized until the next
        # delegated call prints the drift warning out of nowhere.
        _warn_if_workspace_stale(work_dir)
        return {"ok": True, "content": "workflow workspace initialized", "meta": meta}

    if normalized_command == "doctor":
        config = load_provider_config()
        return run_doctor(
            project_root, config.get("provider_command", "opencode"), session_id
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
        if require_provider_session and not session.get("provider_session_id"):
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
    config = load_provider_config_for(project_root)
    from adapters.registry import resolve_adapter

    adapter = resolve_adapter(
        project_root=project_root,
        config=config,
        command=config.get("provider_command", "opencode"),
    )
    return adapter.probe(
        model=model or config.get("default_model"),
        work_dir=str(project_root),
        timeout_seconds=int(
            config.get("probe_timeout_seconds", DEFAULT_PROBE_TIMEOUT_SECONDS)
        ),
    )


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
