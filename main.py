from core.executor import Executor
from core.session_manager import SessionManager

SESSION_MANAGER = SessionManager()
EXECUTOR = Executor(session_manager=SESSION_MANAGER)


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
    result = run(args.command, args.prompt, args.session, work_dir, args.model)
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
