from core.executor import Executor
from core.session_manager import SessionManager
from config.settings import get_cached_main_session_id, set_cached_main_session_id
from utils.parser import generate_main_session_id

SESSION_MANAGER = SessionManager()
EXECUTOR = Executor(session_manager=SESSION_MANAGER)


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
    session = SESSION_MANAGER.load_or_create(session_id)
    output = EXECUTOR.execute(command, task, session, work_dir, model)
    SESSION_MANAGER.record_run(session, command)
    output["session_id"] = session_id
    return output


if __name__ == "__main__":
    import argparse
    import json

    from pathlib import Path

    parser = argparse.ArgumentParser(description="agent-workflow CLI")
    parser.add_argument(
        "--command",
        "-c",
        required=True,
        choices=["explore", "plan", "analyze", "execute", "verify"],
    )
    parser.add_argument("--prompt", "-p", required=True)
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
    parser.add_argument("--pretty", action="store_true", help="pretty print output")
    args = parser.parse_args()

    work_dir = str(Path(args.work_dir).resolve()) if args.work_dir else str(Path.cwd())
    effective_session = resolve_session_id(args.session, fresh=args.fresh_session)
    result = run(args.command, args.prompt, effective_session, work_dir, args.model)
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
