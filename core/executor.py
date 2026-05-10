from adapters.opencode_adapter import OpenCodeAdapter
from core.prompt_builder import build_prompt
from core.router import Router


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
        )
        self.opencode.command = route.get("opencode_command", getattr(self.opencode, "command", "opencode"))
        self.opencode.timeout_seconds = route.get("timeout_seconds", getattr(self.opencode, "timeout_seconds", 0))
        self.opencode.no_timeout = self.opencode.timeout_seconds is None or self.opencode.timeout_seconds <= 0
        result = self.opencode.run(prompt, session, route.get("model"), work_dir)
        opencode_session_id = result.get("meta", {}).get("opencode_session_id") or session.get("opencode_session_id")
        if result.get("ok") and opencode_session_id and not session.get("opencode_session_id") and self.session_manager:
            self.session_manager.update_opencode_session_id(session, opencode_session_id)
        return result
