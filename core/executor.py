from adapters.opencode_adapter import OpenCodeAdapter
from core.prompt_builder import build_prompt
from core.router import Router
from core.workflow_runtime import (
    bind_session,
    detect_project_root,
    release_runtime_lock,
    run_sweep,
    update_command_cache,
    update_plan_scope,
    update_state_from_agent_output,
    write_prompt_handoff,
    write_response_snapshot,
)


class Executor:
    def __init__(
        self,
        router: Router | None = None,
        opencode: OpenCodeAdapter | None = None,
        session_manager=None,
    ) -> None:
        self.router = router or Router()
        self.opencode = opencode or OpenCodeAdapter()
        self.session_manager = session_manager

    def execute(self, command: str, task: str, session: dict, work_dir: str | None = None, model: str | None = None) -> dict:
        normalized_command = command.strip().lower()
        session_id = session["session_id"]
        project_root = detect_project_root(work_dir)

        try:
            route = self.router.route(normalized_command, model_override=model)
        except ValueError as exc:
            return {
                "ok": False,
                "content": str(exc),
                "meta": {"error_type": "routing_error", "command": normalized_command},
            }

        prompt = build_prompt(
            role=route["role"],
            task=task,
            session_id=session["session_id"],
            command=normalized_command,
            project_root=str(project_root),
        )

        bound = bind_session(project_root, session_id)
        handoff = write_prompt_handoff(project_root, normalized_command, session_id, prompt)
        if not handoff.get("ok"):
            return handoff

        self.opencode.command = route.get("opencode_command", getattr(self.opencode, "command", "opencode"))
        self.opencode.timeout_seconds = route.get("timeout_seconds", getattr(self.opencode, "timeout_seconds", 0))
        self.opencode.no_timeout = self.opencode.timeout_seconds is None or self.opencode.timeout_seconds <= 0
        try:
            result = self.opencode.run(
                prompt,
                session,
                route.get("model"),
                work_dir,
            )
        finally:
            release_runtime_lock(handoff["paths"]["lock"])

        write_response_snapshot(project_root, result.get("content") or "")

        opencode_session_id = result.get("meta", {}).get("opencode_session_id") or session.get("opencode_session_id")
        if result.get("ok") and opencode_session_id and not session.get("opencode_session_id") and self.session_manager:
            self.session_manager.update_opencode_session_id(session, opencode_session_id)

        if not result.get("ok"):
            return result

        if normalized_command == "explore":
            update_command_cache(project_root, "last_explore_result", result.get("content"), session_id)
            update_state_from_agent_output(project_root, normalized_command, task, result.get("content") or "", session_id)
        elif normalized_command == "analyze":
            update_command_cache(project_root, "last_analyze_result", result.get("content"), session_id)
            update_state_from_agent_output(project_root, normalized_command, task, result.get("content") or "", session_id)
        elif normalized_command == "plan":
            update_command_cache(project_root, "last_plan_result", result.get("content"), session_id)
            update_state_from_agent_output(project_root, normalized_command, task, result.get("content") or "", session_id)
            update_plan_scope(project_root, result.get("content") or "", session_id)
        elif normalized_command == "execute":
            execute_diff = {"content": result.get("content"), "meta": result.get("meta", {})}
            update_command_cache(project_root, "last_execute_diff", execute_diff, session_id)
            sweep_result = run_sweep(project_root)
            result.setdefault("meta", {})["auto_sweep"] = sweep_result

        result.setdefault("meta", {})["project_root"] = str(project_root)
        result["meta"]["session_reset"] = bool(bound.get("session_reset"))
        return result
