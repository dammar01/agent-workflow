from pathlib import Path

from adapters.opencode_adapter import OpenCodeAdapter
from core.contract import (
    contract_warnings,
    detect_subagent_usage,
    extract_digest,
    make_error,
    reported_no_spawn_tool,
)
from core import fact_store
from core import evidence_store
from core import graph_index
from core.prompt_builder import build_prompt
from core.router import Router
from utils.path_guard import validate_scope
from core import quick_verify
from core.workflow_runtime import (
    auto_verify_after_execute,
    bind_session,
    detect_project_root,
    fanout_capability,
    graph_leads_enabled,
    release_runtime_lock,
    set_fanout_capability,
    subagent_fanout_enabled,
    run_sweep,
    update_command_cache,
    update_plan_scope,
    update_state_from_agent_output,
    verify_mode,
    workflow_paths,
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

    def _maybe_reuse(
        self, project_root: Path, command: str, task: str, session_id: str
    ) -> dict | None:
        """Serve a fresh, identical prior evidence artifact instead of re-delegating.

        Returns a complete result dict on a hit, or None to fall through to delegation.
        Fail-open: the reuse path must never be why a call breaks — on ANY error we return
        None and normal delegation proceeds. The freshness guarantee lives in
        evidence_store.find_fresh (exact query + all anchors unchanged).
        """
        try:
            hit = evidence_store.find_fresh(project_root, command, task)
            if not hit:
                return None
            ap = hit.get("artifact_path")
            if not ap or not Path(ap).is_file():
                return None  # artifact file gone -> re-delegate rather than serve nothing
            content = Path(ap).read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                return None
            result = {
                "ok": True,
                "content": content,
                "evidence_ref": {
                    "artifact_path": ap,
                    "anchors": len(hit.get("anchors") or []),
                    "reused": True,
                },
                "meta": {
                    "reused_evidence": True,
                    "reused_from_session": hit.get("session"),
                    "captured_at": hit.get("captured_at"),
                    "command": command,
                },
            }
            if hit.get("digest") is not None:
                result["digest"] = hit["digest"]
            if command == "explore":
                update_command_cache(
                    project_root, "last_explore_result", content, session_id
                )
            return result
        except Exception:
            return None

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

        # Cross-session reuse: an identical prior call whose cited code is unchanged can be
        # served from the evidence artifact instead of re-delegating (saves time + quota).
        # Fail-open — any hiccup here falls straight through to a normal delegation.
        if route["role"] in ("exploration", "reasoning"):
            reused = self._maybe_reuse(project_root, normalized_command, task, session_id)
            if reused is not None:
                return reused

        known_facts = None
        graph_leads = None
        fanout = False
        if graph_leads_enabled(project_root):
            graph_leads = graph_index.leads(project_root, task)
        if route["role"] in ("exploration", "reasoning"):
            facts = fact_store.load_relevant(project_root, task)
            known_facts = [
                f"{f['claim']} [{f['file']}:{f['line']}]"
                for f in facts
                if f.get("file")
            ] or None
            fanout = (
                subagent_fanout_enabled(project_root)
                and fanout_capability(project_root) is not False
            )

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
            release_runtime_lock(handoff["paths"]["lock"], session_id)
            self.opencode.on_progress = None
            # Archive exit, duration, kill, and stderr metadata for failure diagnosis.
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

        if route["role"] in ("exploration", "reasoning"):
            # Reported, never enforced: a contract miss is worth surfacing, but it says
            # nothing about whether the evidence underneath is correct.
            issues = contract_warnings(normalized_command, result.get("content") or "")
            if issues:
                result.setdefault("meta", {})["contract_warnings"] = issues

        if fanout:
            # Report what actually happened, not what was asked for. A run that was told
            # to fan out and did not is still a usable result — but calling it a fan-out
            # would make the next decision rest on work nobody did.
            content = result.get("content") or ""
            usage = detect_subagent_usage(content)
            meta = result.setdefault("meta", {})
            meta["subagent_used"] = usage["used"]
            meta["subagent_fanout_clusters"] = usage["fanout_clusters"]
            meta["covered_clusters"] = usage["covered_clusters"]
            if usage["mismatch"]:
                meta["subagent_warning"] = (
                    "second_agent declared sub-agents but tagged no claims with [cN]; "
                    "treat the fan-out as unconfirmed"
                )
            # Learn opencode's fan-out capability from what it just reported (P1.6): a
            # "no spawn tool" fallback flips it OFF for next time; a real fan-out confirms
            # ON (in case a prior probe was wrong). Best-effort — never fail the run on it.
            try:
                if reported_no_spawn_tool(content):
                    set_fanout_capability(project_root, False)
                elif usage["used"]:
                    set_fanout_capability(project_root, True)
            except Exception:
                pass

        if route["role"] in ("exploration", "reasoning"):
            # Fact ingestion is best-effort, but its failure remains visible.
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

            # Index the evidence artifact for cross-session reuse + attach a digest-first ref
            # so the main_agent can open the full evidence on demand instead of eating it now.
            # Best-effort: a store failure never fails the delegated call.
            try:
                artifact_path = workflow_paths(project_root, session_id)["response_last"]
                entry = evidence_store.record(
                    project_root,
                    normalized_command,
                    task,
                    session_id,
                    result.get("digest"),
                    artifact_path,
                    result.get("content") or "",
                )
                result["evidence_ref"] = {
                    "artifact_path": str(artifact_path),
                    "anchors": len(entry.get("anchors") or []),
                    "reused": False,
                }
            except Exception as exc:
                result.setdefault("meta", {})["evidence_store_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )

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
        # The two settings main_agent has to obey after a delegated call, carried on the
        # result itself. Neither can be enforced from here (/.execute has no Python
        # path), but shipping the values removes the other half of the problem: acting
        # on a remembered config instead of the one this project actually has.
        result["meta"]["policy"] = {
            "auto_verify_after_execute": auto_verify_after_execute(project_root),
            "verify_mode": verify_mode(project_root),
            "subagent_fanout_enabled": fanout,
        }
        return result
