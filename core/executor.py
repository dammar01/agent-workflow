from adapters.opencode_adapter import OpenCodeAdapter
from core.contract import normalize_output
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
            return normalize_output(
                status="error",
                adapter="opencode",
                model=model,
                role="reasoning",
                session_id=session_id,
                opencode_session_id=session.get("opencode_session_id"),
                content=str(exc),
                confidence="low",
                notes="routing_error",
            )

        prompt = build_prompt(
            role=route["role"],
            task=task,
            session_id=session["session_id"],
        )
        self.opencode.command = route.get("opencode_command", getattr(self.opencode, "command", "opencode"))
        self.opencode.timeout_seconds = route.get("timeout_seconds", getattr(self.opencode, "timeout_seconds", 300))
        result = self.opencode.run(prompt, session, route.get("model"))
        opencode_session_id = result.get("meta", {}).get("opencode_session_id") or session.get("opencode_session_id")
        if result.get("ok") and opencode_session_id and not session.get("opencode_session_id") and self.session_manager:
            self.session_manager.update_opencode_session_id(session, opencode_session_id)
        return self._contract_from_adapter(
            result=result,
            model=route.get("model"),
            role=route["role"],
            session_id=session["session_id"],
            opencode_session_id=opencode_session_id,
        )

    @staticmethod
    def _contract_from_adapter(
        *,
        result: dict,
        model: str | None,
        role: str,
        session_id: str,
        opencode_session_id: str | None,
        notes: str = "",
    ) -> dict:
        ok = bool(result.get("ok"))
        adapter_notes = result.get("meta", {})
        return normalize_output(
            status="success" if ok else "error",
            adapter="opencode",
            model=model,
            role=role,
            session_id=session_id,
            opencode_session_id=opencode_session_id,
            content=result.get("content", ""),
            confidence="medium" if ok else "low",
            notes=notes or str(adapter_notes),
            extra_meta=adapter_notes,
        )
