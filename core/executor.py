from adapters.opencode_adapter import OpenCodeAdapter
from core.contract import extract_digest, make_error
from core import fact_store
from core.prompt_builder import build_prompt
from core.router import Router
from utils.path_guard import validate_scope
from core import quick_verify
from core.workflow_runtime import (
    bind_session,
    detect_project_root,
    release_runtime_lock,
    run_sweep,
    update_command_cache,
    update_plan_scope,
    update_state_from_agent_output,
    verify_mode,
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
        self._router_override = router is not None
        self.opencode = opencode or OpenCodeAdapter()
        self.session_manager = session_manager

    def _router_for(self, project_root) -> Router:
        """Use an injected router as-is; otherwise route via project-local opencode config."""
        if self._router_override:
            return self.router
        from config.settings import load_opencode_config_for

        return Router(load_opencode_config_for(project_root))

    def execute(
        self,
        command: str,
        task: str,
        session: dict,
        work_dir: str | None = None,
        model: str | None = None,
    ) -> dict:
        normalized_command = command.strip().lower()
        session_id = session["session_id"]
        project_root = detect_project_root(work_dir)

        scope_ok, blocked = validate_scope(task, project_root)
        if not scope_ok:
            return make_error(
                "path_out_of_scope",
                "task references sensitive files or paths outside the project",
                next_action="Remove the flagged paths or ask the user for explicit access, then retry.",
                meta={"command": normalized_command},
                blocked_paths=blocked,
            )

        mode = verify_mode(project_root) if normalized_command == "verify" else None
        if mode == "syntax":
            result = quick_verify.run(project_root, session_id)
            result["meta"]["verify_mode"] = mode
            result["meta"]["command"] = normalized_command
            return result

        try:
            route = self._router_for(project_root).route(
                normalized_command, model_override=model
            )
        except ValueError as exc:
            return make_error(
                "routing_error",
                str(exc),
                next_action="Use a supported command (explore/plan/analyze/verify/sweep).",
                meta={"command": normalized_command},
            )

        known_facts = None
        if route["role"] in ("exploration", "reasoning"):
            facts = fact_store.load_relevant(project_root, task)
            known_facts = [
                f"{f['claim']} [{f['file']}:{f['line']}]" for f in facts if f.get("file")
            ] or None

        prompt = build_prompt(
            role=route["role"],
            task=task,
            session_id=session["session_id"],
            command=normalized_command,
            project_root=str(project_root),
            known_facts=known_facts,
        )

        bound = bind_session(project_root, session_id)
        handoff = write_prompt_handoff(
            project_root, normalized_command, session_id, prompt
        )
        if not handoff.get("ok"):
            return handoff

        self.opencode.command = route.get(
            "opencode_command", getattr(self.opencode, "command", "opencode")
        )
        self.opencode.timeout_seconds = route.get(
            "timeout_seconds", getattr(self.opencode, "timeout_seconds", 0)
        )
        self.opencode.no_timeout = (
            self.opencode.timeout_seconds is None or self.opencode.timeout_seconds <= 0
        )
        try:
            result = self.opencode.run(
                prompt,
                session,
                route.get("model"),
                work_dir,
            )
        finally:
            release_runtime_lock(handoff["paths"]["lock"])

        write_response_snapshot(
            project_root,
            result.get("content") or "",
            prompt_id=handoff.get("meta", {}).get("prompt_id"),
            session_id=session_id,
        )

        opencode_session_id = result.get("meta", {}).get(
            "opencode_session_id"
        ) or session.get("opencode_session_id")
        if (
            result.get("ok")
            and opencode_session_id
            and not session.get("opencode_session_id")
            and self.session_manager
        ):
            self.session_manager.update_opencode_session_id(
                session, opencode_session_id
            )

        if not result.get("ok"):
            return result

        if route["role"] in ("exploration", "reasoning"):
            body = (result.get("content") or "").lower()
            markers = (
                "[evidence]",
                "[digest]",
                "[exploration result]",
                "entry_points",
                "grounded:",
                "assumptions:",
                "scope_covered",
            )
            if not any(m in body for m in markers):
                return make_error(
                    "invalid_evidence",
                    "second_agent returned non-evidence output (menu/refusal/question), not analysis",
                    next_action="STOP. Warn user [PROXY GAGAL]. Do NOT auto-fallback. Ask user: retry or /.local? (yes/no).",
                    meta=result.get("meta", {}),
                    raw_preview=(result.get("content") or "")[:240],
                )

        digest = extract_digest(result.get("content") or "")
        if digest is not None:
            result["digest"] = digest

        if route["role"] in ("exploration", "reasoning"):
            try:
                fact_store.ingest(project_root, result.get("content") or "", session_id)
            except Exception:
                pass  # fact store is best-effort; never fail the delegated call over it

        if normalized_command == "explore":
            update_command_cache(
                project_root, "last_explore_result", result.get("content"), session_id
            )
            update_state_from_agent_output(
                project_root,
                normalized_command,
                task,
                result.get("content") or "",
                session_id,
            )
        elif normalized_command == "analyze":
            update_command_cache(
                project_root, "last_analyze_result", result.get("content"), session_id
            )
            update_state_from_agent_output(
                project_root,
                normalized_command,
                task,
                result.get("content") or "",
                session_id,
            )
        elif normalized_command == "plan":
            update_command_cache(
                project_root, "last_plan_result", result.get("content"), session_id
            )
            update_state_from_agent_output(
                project_root,
                normalized_command,
                task,
                result.get("content") or "",
                session_id,
            )
            update_plan_scope(project_root, result.get("content") or "", session_id)
        result.setdefault("meta", {})["project_root"] = str(project_root)
        result["meta"]["session_reset"] = bool(bound.get("session_reset"))
        return result
