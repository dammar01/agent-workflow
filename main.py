from core.contract import mark_cache_hit
from core.executor import Executor
from core.session_manager import SessionManager
from utils.cache import SimpleCache

SESSION_MANAGER = SessionManager()
CACHE = SimpleCache()
EXECUTOR = Executor(session_manager=SESSION_MANAGER)


def run(command: str, task: str, session_id: str, work_dir: str | None = None) -> dict:
    session = SESSION_MANAGER.load_or_create(session_id)
    cache_key = CACHE.make_key(command, task, work_dir)

    cached = CACHE.get(cache_key)
    if cached:
        SESSION_MANAGER.record_run(session, command, cache_hit=True)
        return mark_cache_hit(cached, session_id)

    output = EXECUTOR.execute(command, task, session, work_dir)
    if output["status"] == "success":
        CACHE.set(cache_key, output)

    SESSION_MANAGER.record_run(session, command, cache_hit=False)
    return output


if __name__ == "__main__":
    import argparse
    import json

    from pathlib import Path

    parser = argparse.ArgumentParser(description="ai-proxy CLI")
    parser.add_argument("--command", "-c", required=True, choices=["explore", "plan", "analyze", "execute", "verify"])
    parser.add_argument("--prompt", "-p", required=True)
    parser.add_argument("--session", "-s", default="default")
    parser.add_argument("--work-dir", "-w", default=None, help="project directory for Kimi to explore (default: cwd)")
    parser.add_argument("--pretty", action="store_true", help="pretty print output")
    args = parser.parse_args()

    work_dir = str(Path(args.work_dir).resolve()) if args.work_dir else str(Path.cwd())
    result = run(args.command, args.prompt, args.session, work_dir)
    print(json.dumps(result, indent=2) if args.pretty else json.dumps(result))
