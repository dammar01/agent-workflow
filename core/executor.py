from adapters.opencode_adapter import OpenCodeAdapter
from core.contract import detect_subagent_usage, extract_digest, make_error
from core import fact_store
from core import graph_index
from core.prompt_builder import build_prompt
from core.router import Router
from utils.path_guard import validate_scope
from core import quick_verify
from core.workflow_runtime import (
    bind_session,
    detect_project_root,
    graph_leads_enabled,
    release_runtime_lock,
    subagent_fanout_enabled,
    run_sweep,
    update_command_cache,
    update_plan_scope,
    update_state_from_agent_output,
    verify_mode,
    write_call_meta,
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
        on_progress=None,
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
        graph_leads = None
        fanout = False
        if route["role"] in ("exploration", "reasoning"):
            facts = fact_store.load_relevant(project_root, task)
            known_facts = [
                f"{f['claim']} [{f['file']}:{f['line']}]" for f in facts if f.get("file")
            ] or None
            # Graph-first: hand the second agent a ranked shortlist before it starts
            # reading. These are LEADS, not findings — the prompt says so, because a
            # graph edge is not evidence until the file backs it up.
            if graph_leads_enabled(project_root):
                graph_leads = graph_index.leads(project_root, task)
            # Fan-out needs clusters to fan out over; without a graph there is nothing
            # to partition and the instruction would be noise.
            fanout = bool(graph_leads) and subagent_fanout_enabled(project_root)

        prompt = build_prompt(
            role=route["role"],
            task=task,
            session_id=session["session_id"],
            command=normalized_command,
            project_root=str(project_root),
            known_facts=known_facts,
            graph_leads=graph_leads,
            subagent_fanout=fanout,
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
        if route.get("bootstrap_timeout_seconds") is not None:
            self.opencode.bootstrap_timeout_seconds = route["bootstrap_timeout_seconds"]
        if route.get("poll_interval_seconds") is not None:
            self.opencode.poll_interval = route["poll_interval_seconds"]
        # Only the adapter's poll loop can emit liveness while opencode blocks.
        self.opencode.on_progress = on_progress
        try:
            result = self.opencode.run(
                prompt,
                session,
                route.get("model"),
                work_dir,
            )
        finally:
            release_runtime_lock(handoff["paths"]["lock"])
            self.opencode.on_progress = None
            # Record what the call actually did (exit code, duration, kill, stderr tail).
            # Without this, "opencode behaves oddly under rate limits" stays folklore.
            write_call_meta(
                project_root,
                handoff.get("meta", {}).get("prompt_id"),
                session_id,
                {
                    "command": normalized_command,
                    "model": route.get("model"),
                    "timeout_seconds": self.opencode.timeout_seconds,
                    **(getattr(self.opencode, "last_call_meta", None) or {}),
                },
            )

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

        if fanout:
            # Report what actually happened, not what was asked for. A run that was told
            # to fan out and did not is still a usable result — but calling it a fan-out
            # would make the next decision rest on work nobody did.
            usage = detect_subagent_usage(result.get("content") or "")
            meta = result.setdefault("meta", {})
            meta["subagent_used"] = usage["used"]
            meta["subagent_fanout_clusters"] = usage["fanout_clusters"]
            meta["covered_clusters"] = usage["covered_clusters"]
            if usage["mismatch"]:
                meta["subagent_warning"] = (
                    "second_agent declared sub-agents but tagged no claims with [cN]; "
                    "treat the fan-out as unconfirmed"
                )

        if route["role"] in ("exploration", "reasoning"):
            # Ingest stays best-effort — it must never fail a delegated call — but the
            # failure is now REPORTED. Swallowing it silently let the fact store stop
            # accepting facts for days without a single visible symptom.
            try:
                added = fact_store.ingest(
                    project_root, result.get("content") or "", session_id
                )
                result.setdefault("meta", {})["facts_ingested"] = added
            except Exception as exc:
                result.setdefault("meta", {})["fact_ingest_error"] = {
                    "error_type": "fact_ingest_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "next_action": "Report this: the fact store rejected the run's output; facts are not being learned.",
                }

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
