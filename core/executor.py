from adapters.claude_adapter import ClaudeAdapter
from adapters.kimi_adapter import KimiAdapter
from config.roles import MODEL_CLAUDE, MODEL_KIMI, ROLE_EXPLORATION, ROLE_REASONING
from core.contract import normalize_output
from core.prompt_builder import build_prompt
from core.router import Router


class Executor:
    def __init__(
        self,
        router: Router | None = None,
        kimi: KimiAdapter | None = None,
        claude: ClaudeAdapter | None = None,
        session_manager=None,
    ) -> None:
        self.router = router or Router()
        self.kimi = kimi or KimiAdapter()
        self.claude = claude or ClaudeAdapter()
        self.session_manager = session_manager

    def execute(self, command: str, task: str, session: dict, work_dir: str | None = None) -> dict:
        normalized_command = command.strip().lower()
        session_id = session["session_id"]

        try:
            route = self.router.route(normalized_command)
        except ValueError as exc:
            return normalize_output(
                status="error",
                model=MODEL_CLAUDE,
                role=ROLE_REASONING,
                session_id=session_id,
                content=str(exc),
                confidence="low",
                notes="routing_error",
            )

        if route == (MODEL_KIMI,):
            return self._run_kimi(task, session, work_dir)

        if route == (MODEL_CLAUDE,):
            return self._run_claude(task, session)

        return normalize_output(
            status="error",
            model=MODEL_CLAUDE,
            role=ROLE_REASONING,
            session_id=session_id,
            content=f"unsupported route: {route}",
            confidence="low",
            notes="routing_error",
        )

    def _maybe_link_kimi_session(self, session: dict, work_dir: str | None = None) -> None:
        if session.get("kimi_session_id") is not None:
            return
        if self.session_manager is None:
            return
        kimi_id = self.kimi.read_last_session_id(work_dir)
        if kimi_id:
            self.session_manager.update_kimi_session_id(session, kimi_id)

    def _run_kimi(self, task: str, session: dict, work_dir: str | None = None) -> dict:
        prompt = build_prompt(
            role=ROLE_EXPLORATION,
            task=task,
            session_id=session["session_id"],
        )
        result = self.kimi.run(prompt, session, work_dir)
        if result.get("ok"):
            self._maybe_link_kimi_session(session, work_dir)
        return self._contract_from_adapter(
            result=result,
            model=MODEL_KIMI,
            role=ROLE_EXPLORATION,
            session_id=session["session_id"],
        )

    def _run_claude(self, task: str, session: dict) -> dict:
        prompt = build_prompt(
            role=ROLE_REASONING,
            task=task,
            session_id=session["session_id"],
        )
        result = self.claude.run(prompt, session)
        return self._contract_from_adapter(
            result=result,
            model=MODEL_CLAUDE,
            role=ROLE_REASONING,
            session_id=session["session_id"],
        )

    @staticmethod
    def _contract_from_adapter(
        *,
        result: dict,
        model: str,
        role: str,
        session_id: str,
        notes: str = "",
    ) -> dict:
        ok = bool(result.get("ok"))
        adapter_notes = result.get("meta", {})
        return normalize_output(
            status="success" if ok else "error",
            model=model,
            role=role,
            session_id=session_id,
            content=result.get("content", ""),
            confidence="medium" if ok else "low",
            notes=notes or str(adapter_notes),
        )
