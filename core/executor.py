from pathlib import Path

from adapters.opencode_adapter import OpenCodeAdapter
from config.settings import (
    DEFAULT_MAX_TASK_CHARS,
    DEFAULT_TASK_TRUNCATION_HARD_RATIO,
)
from core.contract import (
    FANOUT_DECLINED,
    FANOUT_DENIED,
    FANOUT_INCAPABLE,
    FANOUT_MISMATCH,
    FANOUT_UNREPORTED,
    STRUCTURAL_KINDS,
    cap_confidence,
    contract_warnings,
    detect_subagent_usage,
    extract_digest,
    make_error,
    readable_claims,
)
from core import fact_store
from core import evidence_store
from core import graph_index
from core.prompt_builder import build_prompt
from core.router import Router
from utils.redact import redact_value
from core import quick_verify
from core.workflow_runtime import (
    CONFIG_VERSION,
    acquire_runtime_lock,
    auto_verify_after_execute,
    bind_session,
    detect_project_root,
    fanout_capability,
    graph_leads_enabled,
    parse_questions,
    release_runtime_lock,
    runtime_lock_owned,
    set_fanout_capability,
    subagent_fanout_enabled,
    run_sweep,
    update_command_cache,
    update_plan_scope,
    update_state_from_agent_output,
    verify_mode,
    workflow_paths,
    write_call_meta,
    write_evidence_sidecars,
    write_prompt_handoff,
    write_redaction_audit,
    write_response_snapshot,
)


def _scope_incomplete(content: str) -> bool:
    """True when the run itself reported ground it did not cover.

    Read off the agent's own `scope_not_covered` section: it is the one admission of
    incompleteness that arrives without lowering the confidence line beside it.
    """
    lines = (content or "").splitlines()
    collecting = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("scope_not_covered"):
            collecting = True
            continue
        if collecting:
            if not stripped:
                continue
            if not stripped.startswith("-"):
                break
            body = stripped.lstrip("-").strip().lower()
            if body and body not in {"none", "(none)", "n/a"}:
                return True
    return False


def _attach_redactions(meta: dict, hits: list[dict]) -> None:
    if not hits:
        return
    counts: dict[str, int] = {}
    for hit in [*(meta.get("redactions") or []), *hits]:
        if not isinstance(hit, dict) or not hit.get("kind"):
            continue
        kind = str(hit["kind"])
        counts[kind] = counts.get(kind, 0) + int(hit.get("count") or 0)
    meta["redactions"] = [
        {"kind": kind, "count": count} for kind, count in counts.items()
    ]
    meta["redaction_count"] = sum(counts.values())


def _without_raw_args(value):
    """Remove argv payloads from injected/legacy adapter data."""
    if isinstance(value, dict):
        raw_args = value.get("args")
        clean = {
            key: _without_raw_args(child)
            for key, child in value.items()
            if key != "args"
        }
        if isinstance(raw_args, (list, tuple)):
            clean.setdefault("argv_count", len(raw_args))
            clean.setdefault("argv_chars", sum(len(str(arg)) for arg in raw_args))
        return clean
    if isinstance(value, list):
        return [_without_raw_args(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_without_raw_args(child) for child in value)
    return value


def _sanitize_result(result):
    clean, hits = redact_value(_without_raw_args(result))
    if isinstance(clean, dict):
        meta = clean.setdefault("meta", {})
        if isinstance(meta, dict):
            _attach_redactions(meta, hits)
    return clean, hits


def _evidence_context(
    route: dict,
    fanout: bool,
    graph_leads: dict | None,
    known_facts: list[str] | None,
) -> dict:
    """Inputs that can materially change an otherwise identical delegated answer."""
    return {
        "schema": 2,
        "runtime_config_version": CONFIG_VERSION,
        "route": dict(route),
        "fanout": bool(fanout),
        "graph_leads": graph_leads,
        "known_facts": known_facts or [],
    }


# One line per way fan-out can fail to happen. Each says what to DO about it, because
# "no fan-out" alone left the reader unable to tell a repairable config wall from a
# permanent limitation.
_FANOUT_WARNINGS = {
    FANOUT_MISMATCH: (
        "second_agent declared sub-agents but tagged no claims with [cN], so the "
        "dispatch cannot be corroborated — see meta.declared_clusters for what it "
        "claimed. The sub-agents may well have run; the missing tags are what make it "
        "unprovable. Treat the fan-out as unconfirmed, and read the merged claims as "
        "the primary agent's own work"
    ),
    FANOUT_DENIED: (
        "sub-agent spawn was refused by a permission rule, not missing. Check the "
        "opencode agent's `permission.task` — the runtime calls `--agent plan`, which "
        "may not spawn write-capable subagents like `general`; `explore` is allowed"
    ),
    FANOUT_INCAPABLE: (
        "second_agent reports no spawn tool; fan-out is off for this project and will be "
        "retried automatically after AI_PROXY_FANOUT_RECHECK_HOURS (default 24h). To "
        "retry now, delete .workflow/capabilities.json"
    ),
    FANOUT_DECLINED: (
        "second_agent chose not to fan out; the answer is a sequential read"
    ),
    FANOUT_UNREPORTED: (
        "second_agent omitted the required `subagents:` line, so whether it fanned out "
        "is unknown; treat the result as a sequential read"
    ),
}


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

    @staticmethod
    def _audit_redactions(
        project_root: Path, session_id: str, command: str, redactions: list[dict]
    ) -> None:
        if not redactions:
            return
        try:
            write_redaction_audit(project_root, session_id, command, redactions)
        except Exception:
            pass

    def _finalize_runtime_result(
        self,
        result: dict,
        project_root: Path,
        command: str,
        task: str,
        session_id: str,
        bound: dict,
        fanout: bool,
        *,
        audit_existing_redactions: bool = False,
    ) -> dict:
        result, new_redactions = _sanitize_result(result)
        existing_redactions = (result.get("meta") or {}).get("redactions") or []
        self._audit_redactions(
            project_root,
            session_id,
            command,
            existing_redactions if audit_existing_redactions else new_redactions,
        )

        content = result.get("content") or ""
        cache_names = {
            "explore": "last_explore_result",
            "analyze": "last_analyze_result",
            "plan": "last_plan_result",
        }
        cache_name = cache_names.get(command)
        if cache_name:
            update_command_cache(project_root, cache_name, content, session_id)
            update_state_from_agent_output(
                project_root, command, task, content, session_id
            )
        if command == "plan":
            update_plan_scope(project_root, content, session_id)

        meta = result.setdefault("meta", {})
        meta["project_root"] = str(project_root)
        meta["session_reset"] = bool(bound.get("session_reset"))
        meta["policy"] = {
            "auto_verify_after_execute": auto_verify_after_execute(project_root),
            "verify_mode": verify_mode(project_root),
            "subagent_fanout_enabled": fanout,
        }
        result, metadata_redactions = _sanitize_result(result)
        self._audit_redactions(
            project_root, session_id, command, metadata_redactions
        )
        return result

    def _maybe_reuse(
        self,
        project_root: Path,
        command: str,
        task: str,
        session_id: str,
        context: dict,
    ) -> dict | None:
        """Serve a fresh, identical prior evidence artifact instead of re-delegating.

        Returns a complete result dict on a hit, or None to fall through to delegation.
        Fail-open: the reuse path must never be why a call breaks — on ANY error we return
        None and normal delegation proceeds. The freshness guarantee lives in
        evidence_store.find_fresh (exact query + all anchors unchanged).
        """
        try:
            hit = evidence_store.find_fresh(project_root, command, task, context)
            if not hit:
                return None
            ap = hit.get("artifact_path")
            content, reuse_redactions = evidence_store.read_artifact_with_redactions(
                project_root, hit
            )
            if not content or not content.strip():
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
            _attach_redactions(result["meta"], reuse_redactions)
            if hit.get("digest") is not None:
                result["digest"] = hit["digest"]
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
        allow_reuse: bool = True,
        session_manager=None,
        workflow_session_id: str | None = None,
        *,
        _runtime_lock: dict | None = None,
        _resolved_route: dict | None = None,
    ) -> dict:
        normalized_command = command.strip().lower()
        provider_session_id = session["session_id"]
        session_id = str(workflow_session_id or provider_session_id)
        effective_session_manager = (
            session_manager if session_manager is not None else self.session_manager
        )
        project_root = detect_project_root(work_dir)

        # No secret-path preflight here any more. It scanned the TASK TEXT, so it blocked
        # talking about a file rather than reading one — an audit that named `.env` in its
        # instructions was rejected while nothing stopped a run that never mentioned it.
        # The boundary now sits where it can be enforced: opencode `permission.read`/`grep`
        # deny rules, shipped in the project's opencode.json, which act on the actual tool
        # call. See dist/config/opencode/opencode.project.json.
        mode = verify_mode(project_root) if normalized_command == "verify" else None
        if mode == "syntax":
            result = quick_verify.run(project_root, session_id)
            result["meta"]["verify_mode"] = mode
            result["meta"]["command"] = normalized_command
            return _sanitize_result(result)[0]

        if _resolved_route is None:
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
        else:
            route = dict(_resolved_route)

        lock_path = workflow_paths(project_root, session_id)["lock"]
        if _runtime_lock is None:
            lock_claim = acquire_runtime_lock(
                lock_path, normalized_command, session_id
            )
            if not lock_claim.get("ok"):
                payload = lock_claim.get("payload") or {}
                holder = payload.get("session_id") or "unknown"
                result = make_error(
                    "runtime_lock",
                    f"runtime lock active for session {holder}",
                    next_action=(
                        "Wait for the in-flight delegated call on this session to finish, "
                        "then retry; if its owner is gone, inspect the runtime lock."
                    ),
                    meta={"lock": payload, "lock_path": str(lock_path)},
                )
                return _sanitize_result(result)[0]
            try:
                return self.execute(
                    command,
                    task,
                    session,
                    work_dir,
                    model,
                    on_progress,
                    allow_reuse,
                    effective_session_manager,
                    session_id,
                    _runtime_lock=lock_claim,
                    _resolved_route=route,
                )
            finally:
                release_runtime_lock(
                    lock_path, session_id, lock_claim.get("token")
                )

        lock_token = str(_runtime_lock.get("token") or "")
        if not lock_token or not runtime_lock_owned(
            lock_path, session_id, lock_token
        ):
            result = make_error(
                "runtime_lock",
                "runtime lock ownership was lost before delegation",
                next_action="Retry the command after the current session owner finishes.",
                meta={"lock_path": str(lock_path)},
            )
            return _sanitize_result(result)[0]

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
        evidence_context = _evidence_context(
            route, fanout, graph_leads, known_facts
        )

        # Sidecars keep dynamic evidence off the Windows-limited command line; the
        # command prompt carries only the task and paths needed to read them.
        write_evidence_sidecars(
            project_root, session_id, graph_leads, known_facts
        )
        runtime_dir = str(workflow_paths(project_root, session_id)["runtime_dir"])

        prompt_meta: dict = {}
        prompt = build_prompt(
            role=route["role"],
            task=task,
            session_id=session_id,
            command=normalized_command,
            project_root=str(project_root),
            runtime_dir=runtime_dir,
            has_facts=bool(known_facts),
            has_leads=bool(graph_leads and graph_leads.get("files")),
            subagent_fanout=fanout,
            meta_sink=prompt_meta,
        )

        # A cut instruction is answered in full confidence, so the only place it can be
        # caught is before the call. Past the hard ratio the second agent would be
        # answering a materially different question — cheaper to refuse than to deliver
        # a confident answer to it.
        if prompt_meta.get("task_truncated"):
            lost = prompt_meta["task_original_chars"] - prompt_meta["task_kept_chars"]
            if lost / prompt_meta["task_original_chars"] > DEFAULT_TASK_TRUNCATION_HARD_RATIO:
                return make_error(
                    "task_truncated",
                    f"task lost {lost} of {prompt_meta['task_original_chars']} chars "
                    f"(cap {DEFAULT_MAX_TASK_CHARS}); too much of the instruction is gone "
                    "to trust an answer to it",
                    next_action=(
                        "Shorten the task to instructions only — move evidence, dumps, and "
                        "file contents out of it; the second agent gathers those itself."
                    ),
                    meta=dict(prompt_meta),
                )

        bound = bind_session(project_root, session_id)
        if allow_reuse and route["role"] in ("exploration", "reasoning"):
            reused = self._maybe_reuse(
                project_root,
                normalized_command,
                task,
                session_id,
                evidence_context,
            )
            if reused is not None:
                return self._finalize_runtime_result(
                    reused,
                    project_root,
                    normalized_command,
                    task,
                    session_id,
                    bound,
                    fanout,
                    audit_existing_redactions=True,
                )

        handoff = write_prompt_handoff(
            project_root,
            normalized_command,
            session_id,
            prompt,
            lock_claim=_runtime_lock,
        )
        if not handoff.get("ok"):
            return handoff

        self.opencode.command = route.get(
            "opencode_command", getattr(self.opencode, "command", "opencode")
        )
        self.opencode.agent = route.get(
            "opencode_agent", getattr(self.opencode, "agent", None)
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

        adapter_session = dict(session)
        adapter_session["session_id"] = session_id

        def persist_new_session(opencode_session_id: str) -> None:
            adapter_session["opencode_session_id"] = opencode_session_id
            session["opencode_session_id"] = opencode_session_id
            if effective_session_manager is not None:
                effective_session_manager.update_opencode_session_id(
                    session, opencode_session_id
                )

        session_callback_bound = False
        try:
            if hasattr(self.opencode, "on_session_created"):
                session_callback_bound = True
                self.opencode.on_session_created = persist_new_session
        except Exception:
            session_callback_bound = False
        try:
            self.opencode.last_call_meta = {}
        except Exception:
            pass
        try:
            result = self.opencode.run(
                prompt,
                adapter_session,
                route.get("model"),
                work_dir,
            )
            result, _ = _sanitize_result(result)
        finally:
            self.opencode.on_progress = None
            if session_callback_bound:
                try:
                    self.opencode.on_session_created = None
                except Exception:
                    pass
            # Archive exit, duration, kill, and stderr metadata for failure diagnosis,
            # plus minimal cost telemetry. Char counts are exact; token counts are
            # rough len/4 estimates and are labelled token_source=estimated so they are
            # never confused with provider-reported actuals (those would land in
            # actual_input_tokens/actual_output_tokens with token_source=provider).
            _result = locals().get("result")
            _content = _result.get("content") if isinstance(_result, dict) else None
            _prompt_chars = len(prompt)
            _resp_chars = len(_content) if isinstance(_content, str) else None
            adapter_meta, meta_redactions = redact_value(
                _without_raw_args(
                    getattr(self.opencode, "last_call_meta", None) or {}
                )
            )
            if not isinstance(adapter_meta, dict):
                adapter_meta = {}
            call_meta = {
                "command": normalized_command,
                "role": route.get("role"),
                "model": route.get("model"),
                "timeout_seconds": self.opencode.timeout_seconds,
                "prompt_chars": _prompt_chars,
                "response_chars": _resp_chars,
                "estimated_input_tokens": _prompt_chars // 4,
                "estimated_output_tokens": (
                    _resp_chars // 4 if _resp_chars is not None else None
                ),
                "token_source": "estimated",
                **prompt_meta,
                **adapter_meta,
            }
            call_meta, persisted_meta_redactions = redact_value(call_meta)
            _attach_redactions(
                call_meta, [*meta_redactions, *persisted_meta_redactions]
            )
            write_call_meta(
                project_root,
                handoff.get("meta", {}).get("prompt_id"),
                session_id,
                call_meta,
            )

        write_response_snapshot(
            project_root,
            result.get("content") or "",
            prompt_id=handoff.get("meta", {}).get("prompt_id"),
            session_id=session_id,
        )

        opencode_session_id = result.get("meta", {}).get(
            "opencode_session_id"
        ) or adapter_session.get("opencode_session_id") or session.get(
            "opencode_session_id"
        )
        if (
            result.get("ok")
            and opencode_session_id
            and not session.get("opencode_session_id")
        ):
            session["opencode_session_id"] = opencode_session_id
            if effective_session_manager is not None:
                effective_session_manager.update_opencode_session_id(
                    session, opencode_session_id
                )

        redactions = (result.get("meta") or {}).get("redactions")
        self._audit_redactions(
            project_root, session_id, normalized_command, redactions or []
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
            if prompt_meta.get("task_truncated"):
                issues.append(
                    {
                        "kind": "task_truncated",
                        "detail": (
                            f"{prompt_meta['task_original_chars'] - prompt_meta['task_kept_chars']}"
                            f" chars cut from the instruction (cap {DEFAULT_MAX_TASK_CHARS})"
                        ),
                    }
                )
            if issues:
                result.setdefault("meta", {})["contract_warnings"] = issues
                result["meta"].update(
                    {k: v for k, v in prompt_meta.items() if k.startswith("task_")}
                )

            # A damaged payload needs its own field, not a line inside a warnings list.
            # The confidence cap below cannot carry this one: capping edits the digest, and
            # the worst case here IS a missing digest — the run that returned content cut
            # mid-word had nothing left to cap, so the only surviving signal was ok:true.
            damaged = [issue["kind"] for issue in issues if issue["kind"] in STRUCTURAL_KINDS]
            if damaged:
                result.setdefault("meta", {})["content_incomplete"] = damaged

            # Conditions the second agent cannot grade itself, applied to the number
            # main_agent reads. Each signal is already computed above; this is the wiring.
            caps: list[tuple[str, str]] = []
            if prompt_meta.get("task_truncated"):
                caps.append(("medium", "task was truncated before the call"))
            if (graph_leads or {}).get("stale"):
                caps.append(("medium", "dependency graph is stale"))
            for issue in issues:
                if issue["kind"] == "grounded_without_evidence":
                    caps.append(("low", "grounded claims carry no file:line"))
                elif issue["kind"] == "missing_fields":
                    caps.append(("medium", f"contract: {issue['detail']}"))
                elif issue["kind"] in STRUCTURAL_KINDS:
                    # Truncated output is not partially right — the reasoning that would
                    # have qualified the conclusion is the part that went missing.
                    caps.append(("low", f"output structurally incomplete: {issue['kind']}"))
                elif issue["kind"] == "trailing_non_evidence":
                    caps.append(
                        ("medium", "output closes by addressing the user, not on evidence")
                    )
            if _scope_incomplete(result.get("content") or ""):
                caps.append(("medium", "scope_not_covered is non-empty"))
            if caps and digest is not None:
                result["digest"] = cap_confidence(digest, caps)

            # Keep prose and anchors derived from the same claims so presentation cannot
            # become a second source of truth.
            claims = readable_claims(result.get("content") or "")
            if claims:
                result.setdefault("meta", {})["claims"] = claims
            questions = parse_questions(result.get("content") or "")
            if questions["open_questions"] or questions["resolvable_uncertainties"]:
                result.setdefault("meta", {})["questions"] = questions

        if fanout:
            # Report what actually happened, not what was asked for. A run that was told
            # to fan out and did not is still a usable result — but calling it a fan-out
            # would make the next decision rest on work nobody did.
            content = result.get("content") or ""
            usage = detect_subagent_usage(content)
            meta = result.setdefault("meta", {})
            meta["subagent_used"] = usage["used"]
            meta["fanout_mode"] = usage["mode"]
            meta["subagent_fanout_clusters"] = usage["fanout_clusters"]
            meta["covered_clusters"] = usage["covered_clusters"]
            if usage["declared_clusters"] and not usage["used"]:
                meta["declared_clusters"] = usage["declared_clusters"]
            warning = _FANOUT_WARNINGS.get(usage["mode"])
            if usage.get("false_incapable_report"):
                # Say it plainly. The agent broke a rule its own prompt states, and the
                # reader needs to know the "declined" below is a downgrade, not a report.
                meta["false_incapable_report"] = True
                warning = (
                    "second_agent claimed it has no spawn tool while listing one in its "
                    "own tool inventory; counted as a decline, and fan-out was NOT "
                    "disabled. Spawning a read-only subagent writes nothing — being in a "
                    "read-only agent is not a reason to refuse `task`"
                )
            if warning:
                meta["subagent_warning"] = warning
            # Only a genuine tool absence latches capability off. A permission refusal is
            # repairable configuration and must not disable fan-out permanently.
            try:
                if usage["mode"] == FANOUT_INCAPABLE:
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

            # Index the immutable per-run copy; response.last.md is mutable and cannot
            # identify which delegated response an evidence row certifies.
            try:
                prompt_id = handoff.get("meta", {}).get("prompt_id")
                if not prompt_id:
                    raise ValueError("prompt_id missing; immutable artifact unavailable")
                artifact_path = (
                    workflow_paths(project_root, session_id)["logs_dir"]
                    / str(prompt_id)
                    / "output.raw.md"
                )
                entry = evidence_store.record(
                    project_root,
                    normalized_command,
                    task,
                    session_id,
                    result.get("digest"),
                    artifact_path,
                    result.get("content") or "",
                    evidence_context,
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

        return self._finalize_runtime_result(
            result,
            project_root,
            normalized_command,
            task,
            session_id,
            bound,
            fanout,
        )
