"""Stage orchestration for /.verify-browser, called from `Executor.execute()`.

Every run reads one request (`core/evidence/e2e/request.py`), which itself starts from the
project's pinned `e2e` section in config.json. The request's phase decides how far it goes:

  draft  preflight  → a draft with status `blocked` and the failing check
         stage 1    → second_agent writes `[E2E SPEC]`; a proxy failure is returned as-is
                      so main_agent sees [PROXY GAGAL]; the spec is validated and written
                      to `draft.json` for the user to confirm. No browser starts.
  run    preflight  → `incomplete` with the failing check as reason
         spec       → the confirmed scenario from the request, validated again
         stage 2    → the supervised player; never ends the run by itself, its report does.
                      An environmental `incomplete` is retried up to settings.max_retries,
                      never a `fail` and never while allow_side_effects is true
         stage 3    → hybrid review over the compact evidence (skipped only when the
                      browser could not finish — nothing a reviewer says changes `incomplete`)
         normalise  → one canonical `[VERIFICATION]`, verdict set on meta before the shared
                      finaliser reads it

A draft is not a verification: its meta.command is `verify-browser` and it carries no
verdict. A run's meta.command is `verify`, so the verdict, exit code, and acceptance
metrics are the ones every verification uses.

The executor's `_run_delegated` is the only way a stage reaches a provider. Nothing
here calls `execute()` again, so the lock this call holds is the lock the stages use.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from core.evidence.e2e import existing_tests as e2e_existing
from core.evidence.e2e import knowledge as e2e_knowledge
from core.workspace import current as e2e_current
from core.evidence.e2e import redact as e2e_redact
from core.evidence.e2e import request as e2e_request
from core.evidence.e2e import tagging as e2e_tagging
from core.evidence.e2e.classify import build_report
from core.evidence.e2e.normalize import evidence_block, to_verification
from core.evidence.e2e.preflight import display_available, preflight
from core.evidence.e2e.spec import (
    env_references,
    ground_claims,
    parse_spec,
    spec_continuation_prompt,
    spec_gap,
    substitute_env,
    validate_existing_tests,
    validate_scenario,
)
from core.evidence.e2e.spec import validate_read_only_requests
from core.evidence.e2e.supervisor import run_player
from core.provider.result_prep import _sanitize_result
from core.evidence.contracts import correlation_id_for
from core.evidence.runtime_io import write_quality_record
from core.workspace.workspace_paths import atomic_write_text, now_iso, workflow_paths
from utils.redact import redact_value

FAKE_ENV = "WORKFLOW_E2E_FAKE"
SMOKE_ENV = "WORKFLOW_E2E_SMOKE"
# Free-form tag for evaluation datasets (plan Fase 0/5): set it while verifying one of the
# real changes under study, and the run's quality row carries it.
LABEL_ENV = "WORKFLOW_E2E_LABEL"
INVOCATION = "verify-browser"
# Hard ceiling on browsers started for one run, whatever settings.max_retries says. A
# retry only ever buys another sample of an environment that may be flaky; past a few
# samples the answer is not "run it again", it is that the environment is the finding.
MAX_RETRIES = 5
# Incomplete reasons a second attempt could plausibly resolve: the browser or the machine
# misbehaved. Deliberately excluded are playwright_missing, browser_missing,
# base_url_unreachable, spec_invalid, env_missing and player_unavailable — nothing about
# rerunning those changes, so a retry would only spend the user's time. `fail` is never
# retried at all: that verdict means the application broke the claim, and re-rolling a
# real bug until it hides is the one outcome this whole package exists to prevent.
RETRYABLE_REASONS = frozenset(
    {"harness_error", "unknown_origin", "stuck", "timeout", "output_truncated", "launch_failed"}
)
_AGENT_ROOT = Path(__file__).resolve().parents[3]
# What the player child may inherit. Credentials reach it only inside the resolved
# scenario on stdin; the parent's environment is otherwise not its business.
_INHERITED_ENV = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LC_ALL",
    "PLAYWRIGHT_BROWSERS_PATH",
    "DISPLAY",
)
_UNPROVEN = "unproven"


def _result(content: str, verdict: str, e2e_meta: dict, warnings: list[dict]) -> dict:
    meta = {"command": "verify", "invocation": INVOCATION, "phase": "run", "verdict": verdict, "e2e": e2e_meta}
    if warnings:
        meta["contract_warnings"] = warnings
    return _sanitize_result({"ok": True, "content": content, "meta": meta})[0]


def _child_env(extra_path: str) -> dict:
    env = {name: os.environ[name] for name in _INHERITED_ENV if name in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = extra_path
    return env


def _run_kind(fake: str | None) -> str:
    """fake proves the pipeline, smoke proves the browser, project is the only kind that
    says anything about whether browser verification is worth having."""
    if fake:
        return "fake"
    if os.environ.get(SMOKE_ENV) == "1":
        return "smoke"
    return "project"


def _record_run(
    project_root: Path,
    session_id: str,
    task: str,
    e2e_meta: dict,
    verdict: str,
    started: float,
    report: dict | None,
    scenario: object,
) -> None:
    """One `kind: e2e_run` row on the quality stream (plan §24 metrics, derived later).

    Raw counts only; every rate is computed at read time in `core/audit/telemetry.py`, so a
    better definition re-reads history instead of invalidating it. Runs only — a draft
    exercised nothing.
    """
    report = report or {}
    claims = report.get("claims") or {}
    failures = report.get("failures") or []
    artifacts = report.get("artifacts") or []
    statuses = [claim.get("status") for claim in claims.values()]
    declared = len(claims) or (len(scenario.get("claims") or []) if isinstance(scenario, dict) else 0)
    stages = e2e_meta.get("stages") or []
    kept = [a for a in artifacts if a.get("kind") in ("html", "screenshot", "trace") and not a.get("pruned")]
    write_quality_record(
        project_root,
        {
            "kind": "e2e_run",
            "recorded_at": now_iso(),
            "session_id": session_id,
            "correlation_id": correlation_id_for(project_root, session_id, task),
            "run_kind": _run_kind(e2e_meta.get("fake")),
            "label": os.environ.get(LABEL_ENV) or None,
            "verdict": verdict,
            "browser_verdict": e2e_meta.get("browser_verdict"),
            "reason": e2e_meta.get("reason"),
            "spec_source": e2e_meta.get("spec_source"),
            "replay_used": False,
            "existing_tests": (e2e_meta.get("existing_tests") or {}).get("status"),
            "claims": {
                "total": declared,
                "proven": statuses.count("proven"),
                "failed": statuses.count("failed"),
                "unproven": declared - statuses.count("proven") - statuses.count("failed"),
            },
            "failure_origins": {origin: sum(1 for f in failures if f.get("origin") == origin) for origin in ("app", "harness", "unknown")},
            "app_errors": len(report.get("app_errors") or []),
            "steps": report.get("steps") or {},
            "browser_runs": sum(1 for s in stages if s.get("stage") == 2 and s.get("returncode") is not None),
            "probes": len(report.get("probes") or []) + sum(1 for f in failures if f.get("probe")),
            "cleanup": (report.get("cleanup") or {}).get("status"),
            "requests": {
                "total": len(report.get("requests") or []),
                "blocked": sum(1 for r in report.get("requests") or [] if r.get("blocked")),
                "unplanned": sum(1 for r in report.get("requests") or [] if r.get("planned") is False),
                "uncertain": sum(1 for r in report.get("requests") or [] if r.get("attribution") == "uncertain"),
            },
            "artifacts": {
                "kept": len(kept),
                "screenshots": sum(1 for a in kept if a.get("kind") == "screenshot"),
                "traces": sum(1 for a in kept if a.get("kind") == "trace"),
                "skipped": sum(1 for a in artifacts if a.get("kind") == "skipped"),
                "pruned": sum(1 for a in artifacts if a.get("pruned")),
            },
            # No stage hands a screenshot or a trace to a model. Recorded anyway, so the
            # plan's screenshot-analysis rate is a measurement rather than an assumption.
            "screenshots_sent_to_model": 0,
            "provider_prompt_ids": [s["prompt_id"] for s in stages if s.get("prompt_id")],
            "provider_calls": sum(1 for s in stages if s.get("command")),
            "scenario_hash": (
                hashlib.sha256(json.dumps(scenario, sort_keys=True).encode("utf-8")).hexdigest()[:16]
                if isinstance(scenario, dict)
                else None
            ),
            "duration_seconds": round(time.monotonic() - started, 3),
        },
    )


# Heaviest and least readable first. The runner's own files — spec, events, report,
# evidence, verification — are never candidates: they ARE the evidence.
_PRUNABLE_SUFFIXES = (".zip", ".png", ".html")


def _enforce_artifact_budget(directory: Path, max_mb: int) -> list[str]:
    """Drop player artifacts until the whole run fits `settings.artifact_max_mb`.

    Counted across the run directory AND its `retryN/` subdirectories: one budget per
    attempt let a run with retries keep up to (1 + max_retries) times the configured size.
    Names are relative to `directory`, so a pruned retry artifact is told apart from the
    first attempt's file of the same name.
    """
    budget = max(0, max_mb) * 1024 * 1024
    try:
        files = [p for p in directory.rglob("*") if p.is_file()]
    except OSError:
        return []
    total = sum(p.stat().st_size for p in files)
    removed: list[str] = []
    for suffix in _PRUNABLE_SUFFIXES:
        for path in sorted((p for p in files if p.suffix == suffix), key=lambda p: p.stat().st_size, reverse=True):
            if total <= budget:
                return removed
            size = path.stat().st_size
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed.append(path.relative_to(directory).as_posix())
    return removed


def _claim_ids(scenario: object) -> list[dict]:
    if not isinstance(scenario, dict):
        return []
    return [c for c in scenario.get("claims") or [] if isinstance(c, dict) and c.get("id")]


def _unproven(scenario: object) -> dict:
    return {c["id"]: {"severity": c.get("severity", "blocking"), "status": _UNPROVEN} for c in _claim_ids(scenario)}


def _spec_errors(scenario: dict, existing_tests: list, config: dict, project_root: Path) -> tuple[list[str], list[dict], list[dict]]:
    """Policy, coverage, and grounding — checked before any value is resolved or any
    browser exists; the player trusts what it is handed."""
    # Existing tests the project can actually run stand in for assertions on the claims
    # they cover; a listed test that will not run covers nothing.
    runnable, skipped = e2e_existing.select(existing_tests, config, project_root)
    covered = {cid for test in runnable for cid in test["covers"]}
    errors = validate_scenario(scenario, config, covered=covered)
    if not errors:
        errors = validate_existing_tests(existing_tests, {c["id"] for c in _claim_ids(scenario)})
    if not errors:
        errors = ground_claims(scenario, project_root)
    return errors, runnable, skipped


def _draft_result(
    project_root: Path,
    session_id: str,
    e2e_meta: dict,
    *,
    status: str,
    parsed: dict | None = None,
    errors: list[str] | tuple = (),
    env_names: list[str] | tuple = (),
    missing_env: list[str] | tuple = (),
) -> dict:
    """The draft the user confirms. Placeholder form only; written for the run phase to copy."""
    parsed = parsed or {}
    scenario = parsed.get("scenario")
    draft = {
        "version": 1,
        "status": status,
        "reason": e2e_meta.get("reason"),
        "scenario": scenario,
        "existing_tests": parsed.get("existing_tests") or [],
        "spec_notes": parsed.get("uncertainties") or [],
        "read_only_requests": parsed.get("read_only_requests") or [],
        "errors": list(errors),
        "env": list(env_names),
        "missing_env": list(missing_env),
        "created_at": now_iso(),
    }
    path = e2e_request.draft_path(project_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Secret scan only — NOT `e2e_redact.write_json`, which drops typed values as it would
    # for player events. The run copies this scenario, so its `${NAME}` placeholders and
    # literal inputs have to survive; a secret-shaped literal is still scrubbed.
    clean, hits = redact_value(draft)
    atomic_write_text(path, json.dumps(clean, indent=2, ensure_ascii=False, default=str))
    if hits:
        e2e_meta["artifact_redactions"] = hits
    e2e_meta["draft"] = {**draft, "path": str(path)}

    lines = ["[E2E DRAFT]", f"status: {status}", f"reason: {draft['reason'] or 'none'}", f"draft_file: {path}", "", "claims:"]
    claims = _claim_ids(scenario)
    lines += [
        f"- {c['id']} | {c.get('severity', 'blocking')} | {c.get('description', '')} | source_refs: {', '.join(map(str, c.get('source_refs') or [])) or 'none'}"
        for c in claims
    ] or ["- none"]
    lines += ["", "steps:"]
    steps = scenario.get("steps") if isinstance(scenario, dict) else None
    lines += [f"- {n}. {json.dumps(step, ensure_ascii=False)[:240]}" for n, step in enumerate(steps or [], 1)] or ["- none"]
    lines += ["", "cleanup (runs after the steps, pass or fail; reported apart from the test):"]
    cleanup_steps = scenario.get("cleanup") if isinstance(scenario, dict) else None
    lines += [
        f"- {n}. cleans {step.get('cleans')}: {json.dumps(step, ensure_ascii=False)[:240]}"
        for n, step in enumerate(cleanup_steps or [], 1)
        if isinstance(step, dict)
    ] or ["- none"]
    lines += ["", "existing_tests:"]
    lines += [f"- {t.get('path')} covers {', '.join(map(str, t.get('covers') or []))}" for t in draft["existing_tests"] if isinstance(t, dict)] or ["- none"]
    lines += ["", "env_placeholders:", *([f"- {name}" + (" (not set)" if name in missing_env else "") for name in env_names] or ["- none"])]
    secrets = e2e_meta.get("secrets") or {}
    if env_names and secrets.get("file"):
        state = (
            "created now with empty slots — fill the values"
            if secrets.get("template_created")
            else f"profile {secrets.get('profile')}" if secrets.get("profile") else "missing" if not secrets.get("exists") else "no profile selected"
        )
        lines += [f"secrets_file: {secrets['file']} ({state})"]
    lines += ["", "read_only_requests (proposals; confirm each before adding it to settings.allowed_read_only_requests):"]
    lines += [
        f"- {r.get('method')} {r.get('endpoint')} | source_refs: {', '.join(map(str, r.get('source_refs') or [])) or 'none'} | reason: {r.get('reason') or 'none'}"
        for r in draft["read_only_requests"]
        if isinstance(r, dict)
    ] or ["- none"]
    lines += ["", "errors:", *([f"- {e}" for e in draft["errors"]] or ["- none"])]
    lines += ["", "spec_uncertainties:", *([f"- {u}" for u in draft["spec_notes"]] or ["- none"])]
    meta = {"command": INVOCATION, "invocation": INVOCATION, "phase": "draft", "e2e": e2e_meta}
    return _sanitize_result({"ok": True, "content": "\n".join(lines) + "\n", "meta": meta})[0]


def _retryable(report: dict) -> bool:
    """Whether this report describes a run worth attempting again."""
    return report["browser_verdict"] == "incomplete" and report["reason"] in RETRYABLE_REASONS


def _run_signature(report: dict) -> tuple:
    """What "the same failure twice" means: same verdict, same reason, same step outcomes.

    Compared between consecutive attempts only. Two matching signatures say the run is
    reproducible rather than flaky, which is the one piece of information a retry was
    there to get.
    """
    return (
        report["browser_verdict"],
        report["reason"],
        tuple((row.get("step_id"), row.get("status"), row.get("error")) for row in report["trail"]),
    )


def run(
    executor,
    *,
    project_root: Path,
    session_id: str,
    session: dict,
    task: str,
    work_dir: str | None,
    on_progress,
    session_manager,
    lock_claim: dict | None,
    verify_route: dict,
) -> dict:
    started = time.monotonic()
    fake = os.environ.get(FAKE_ENV) or None
    stages: list[dict] = []
    request, request_reason, request_errors = e2e_request.load_request(project_root, session_id)
    e2e_meta: dict = {
        "fake": fake,
        "stages": stages,
        "request": str(e2e_request.request_path(project_root, session_id)),
    }
    if request is None:
        # Nothing was attempted, so nothing is recorded; the reason is the whole answer.
        e2e_meta["reason"] = request_reason
        norm = to_verification(
            {"claims": {}, "browser_verdict": None},
            preflight={"ok": False, "reason": request_reason, "detail": "; ".join(request_errors)},
        )
        return _result(norm["content"], norm["verdict"], e2e_meta, warnings=norm["warnings"])

    phase = request["phase"]
    config = request["settings"]
    e2e_meta["phase"] = phase
    e2e_meta["config"] = {k: config[k] for k in ("base_url", "browser", "headless", "slow_mo_ms", "allow_remote", "allow_side_effects", "max_retries")}
    # Pinned defaults that were dropped for being malformed. A warning, not an error (see
    # request.config_settings), but silent would mean a knob the user set and the runtime
    # ignored, which reads from the outside exactly like the knob not working.
    if request.get("config_warnings"):
        e2e_meta["config_warnings"] = request["config_warnings"]
    run_state: dict = {"report": None, "scenario": None}

    def _finish(norm: dict) -> dict:
        # One quality row per run, whatever ended it: the incomplete runs are exactly what
        # an evaluation has to count. Observes the run, so it can never fail it.
        try:
            _record_run(project_root, session_id, task, e2e_meta, norm["verdict"], started, run_state["report"], run_state["scenario"])
        except Exception:
            pass
        return _result(norm["content"], norm["verdict"], e2e_meta, warnings=norm["warnings"])

    pre = preflight(config, fake=bool(fake))
    e2e_meta["preflight"] = pre["checks"]
    network = pre.get("network") or {"write_hosts": [], "pins": {}, "hosts": []}
    if network.get("hosts"):
        e2e_meta["network"] = network
    if not pre["ok"]:
        e2e_meta["reason"] = pre["reason"]
        if phase == "draft":
            return _draft_result(project_root, session_id, e2e_meta, status="blocked", errors=[f"{pre['reason']}: {pre['detail']}"])
        return _finish(to_verification({"claims": {}, "browser_verdict": None}, preflight=pre))

    secrets, secret_errors, secret_info = e2e_request.load_secrets(project_root, config.get("secrets_profile") or "")
    e2e_meta["secrets"] = {key: secret_info[key] for key in ("file", "exists", "profile", "profiles")}
    # Registered names only: the process environment is a fallback for a credential, not a
    # way for a scenario to read any variable the runtime happens to have.
    lookup = {**{name: os.environ[name] for name in e2e_request.CREDENTIAL_KEYS if name in os.environ}, **secrets}

    if phase == "draft":
        return _draft(executor, project_root, session_id, session, task, work_dir, on_progress, session_manager, lock_claim, config, e2e_meta, stages, lookup, secret_errors)

    # ---- the confirmed spec ------------------------------------------------------
    scenario = request["scenario"]
    parsed = {"scenario": scenario, "existing_tests": request["existing_tests"], "uncertainties": request["spec_notes"]}
    e2e_meta["spec_source"] = "request"
    stages.append({"stage": 1, "command": None, "source": "request"})
    run_state["scenario"] = scenario
    errors, runnable_tests, skipped_tests = _spec_errors(scenario, parsed["existing_tests"], config, project_root)
    if errors:
        e2e_meta["reason"] = "spec_invalid"
        return _finish(
            to_verification(
                {"claims": _unproven(scenario), "browser_verdict": None},
                preflight={"ok": False, "reason": "spec_invalid", "detail": "; ".join(errors)},
            )
        )
    if secret_errors:
        e2e_meta["reason"] = "secrets_invalid"
        return _finish(
            to_verification(
                {"claims": _unproven(scenario), "browser_verdict": None},
                preflight={"ok": False, "reason": "secrets_invalid", "detail": "; ".join(secret_errors)},
            )
        )

    names = env_references(scenario)
    missing = [name for name in names if name not in lookup]
    if missing:
        e2e_meta["reason"] = "env_missing"
        return _finish(
            to_verification(
                {"claims": _unproven(scenario), "browser_verdict": None},
                preflight={
                    "ok": False,
                    "reason": "env_missing",
                    "detail": (
                        f"not set in .workflow/e2e/{e2e_request.SECRETS_FILE} "
                        f"(profile: {secret_info.get('profile') or 'none'}) or the environment: {', '.join(missing)}"
                    ),
                },
            )
        )
    resolved, _ = substitute_env(scenario, lookup)
    resolved_values = {name: lookup[name] for name in names}
    # A value shorter than the scrubber's minimum cannot be put back to its placeholder by
    # substring, so the player keeps no page HTML for this run (screenshots and traces are
    # already off whenever values resolve). Named, never shown.
    unscrubbable = [name for name, value in resolved_values.items() if len(value) < e2e_redact.MIN_SCRUB_CHARS]
    if unscrubbable:
        e2e_meta["secrets"]["unscrubbable"] = unscrubbable

    # ---- artifacts ----------------------------------------------------------------
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_e2e"
    e2e_dir = workflow_paths(project_root, session_id)["logs_dir"] / run_id / "e2e"
    redaction_hits: list[dict] = []
    redaction_hits += e2e_redact.write_json(e2e_dir / "spec.json", scenario)  # placeholder form, never resolved
    e2e_meta["artifacts"] = str(e2e_dir)

    # ---- existing tests (plan §8): the project's own, through the user's command ------
    existing_result: dict | None = None
    existing_rows: list[dict] = list(skipped_tests)
    if runnable_tests:
        # The command gets the whole selected profile, so its output is scrubbed of all of
        # it, not only of the names the scenario happened to reference.
        existing_result = e2e_existing.run(runnable_tests, config, project_root, {**secrets, **resolved_values}, extra_env=secrets)
        redaction_hits += existing_result.get("redactions") or []
        redaction_hits += e2e_redact.write_text(e2e_dir / "existing_tests.log", existing_result["output_tail"])
        result_label = {"passed": "pass", "failed": "fail", "timeout": "timeout"}.get(existing_result["status"], "not run (launch failed)")
        existing_rows = [{**test, "result": result_label} for test in runnable_tests] + existing_rows
        stages.append(
            {
                "stage": "existing_tests",
                "command": None,
                "status": existing_result["status"],
                "returncode": existing_result["returncode"],
                "duration_seconds": existing_result["duration_seconds"],
                "files": existing_result["files"],
            }
        )
        e2e_meta["existing_tests"] = {k: existing_result[k] for k in ("status", "files", "covers")}

    # ---- stage 2: the player ------------------------------------------------------
    if not config.get("headless") and not display_available():
        # Headed is the shipped default, and a Linux box with no X11/Wayland (CI, a
        # container, SSH) cannot open a window: the launch would fail, be retried as an
        # environment problem, and end `incomplete` having proved nothing about the app.
        config = {**config, "headless": True}
        e2e_meta["config"]["headless"] = True
        e2e_meta.setdefault("config_warnings", []).append(
            "headless: false but no display is available (DISPLAY/WAYLAND_DISPLAY unset); ran headless"
        )

    def progress(event: dict) -> None:
        # The live mirror gets the event itself, scrubbed exactly like the stored events.
        mirror_event = e2e_redact.scrub_resolved([event], resolved_values)[0]
        e2e_current.e2e_event(project_root, session_id, mirror_event)
        if on_progress is None:
            return
        on_progress(
            {
                "phase": "e2e_player",
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "idle_seconds": 0.0,
                "step": event.get("step"),
            }
        )

    def _play(attempt_dir: Path) -> dict:
        if not scenario.get("steps"):
            # Every claim is covered by an existing test the project ran: no browser to start.
            return {
                "events": [{"type": "result", "status": "finished"}],
                "stderr_tail": [],
                "malformed": [],
                "launch_error": None,
                "timed_out": False,
                "stalled": False,
                "truncated": False,
                "returncode": None,
                "duration_seconds": 0.0,
            }
        payload = json.dumps(
            {
                "scenario": resolved,
                # The same scenario in placeholder form. The player reports what a step
                # expected from this one, so a resolved value never has to be scrubbed back
                # out of an event — which fails for one shorter than MIN_SCRUB_CHARS.
                "display_scenario": scenario,
                # The write decision preflight made, not the settings it made it from: the
                # player is told which hosts were approved and at which addresses, and never
                # repeats the lookup that approved them.
                "config": {
                    **config,
                    "capture_html": not unscrubbable,
                    "write_hosts": network.get("write_hosts") or [],
                    "host_pins": network.get("pins") or {},
                },
                "artifacts_dir": str(attempt_dir),
                "fake": fake,
                # The player takes no screenshot and no trace once real values are in play:
                # binary captures cannot be scrubbed afterwards.
                "has_secrets": bool(resolved_values),
            }
        )
        return run_player(
            [sys.executable, "-m", "core.evidence.e2e.player"],
            stdin_payload=payload,
            env=_child_env(str(_AGENT_ROOT)),
            cwd=str(_AGENT_ROOT),
            idle_timeout_s=float(config["idle_timeout_s"]),
            total_timeout_s=float(config["total_timeout_s"]),
            on_progress=progress,
        )

    # How many more browsers this run may start. `allow_side_effects` turns retries off
    # outright: the first attempt's write may already have reached the server, so a second
    # would either double it or run against the state the first one left behind. That is
    # the same reason the skill tells the user never to re-run a timed-out write by hand.
    budget = 0 if config["allow_side_effects"] else max(0, min(int(config["max_retries"]), MAX_RETRIES))
    attempts: list[dict] = []
    previous_signature: tuple | None = None
    attempt = 0
    while True:
        attempt += 1
        # Attempt 1 owns e2e_dir so a run that never retried looks exactly as it did
        # before; each retry gets its own directory rather than overwriting the evidence
        # of what it is retrying.
        final_dir = e2e_dir if attempt == 1 else e2e_dir / f"retry{attempt - 1}"
        supervised = _play(final_dir)
        # Everything the child sent back is scrubbed of resolved values BEFORE it is used
        # for anything: classification, artifacts, the reviewer prompt, or the result.
        events = e2e_redact.scrub_resolved(list(supervised["events"]), resolved_values)
        stderr_tail = e2e_redact.scrub_resolved(list(supervised["stderr_tail"]), resolved_values)
        malformed = e2e_redact.scrub_resolved(list(supervised["malformed"]), resolved_values)
        if supervised.get("launch_error"):
            events.append(
                {
                    "type": "harness",
                    "reason": "launch_failed",
                    "detail": e2e_redact.scrub_resolved(supervised["launch_error"], resolved_values),
                }
            )
        # Player-written files: text scrubbed exactly like the events were, then the run's
        # size budget. Both before the report names them, so what it lists is what exists.
        redaction_hits += e2e_redact.scrub_text_files(final_dir, resolved_values)
        pruned = _enforce_artifact_budget(e2e_dir, int(config["artifact_max_mb"]))
        if pruned:
            e2e_meta["artifacts_pruned"] = sorted(set(e2e_meta.get("artifacts_pruned") or []) | set(pruned))
            prefix = "" if final_dir == e2e_dir else f"{final_dir.relative_to(e2e_dir).as_posix()}/"
            events = [
                {**event, "pruned": True} if event.get("type") == "artifact" and f"{prefix}{event.get('name')}" in pruned else event
                for event in events
            ]
        report = build_report(
            events,
            scenario,
            run_meta={
                "timed_out": supervised["timed_out"],
                "stalled": supervised["stalled"],
                "truncated": supervised["truncated"],
                "malformed": bool(malformed),
                "existing_tests": existing_result,
            },
        )
        signature = _run_signature(report)
        attempts.append(
            {
                "attempt": attempt,
                "artifacts": str(final_dir),
                "browser_verdict": report["browser_verdict"],
                "reason": report["reason"],
                "duration_seconds": supervised["duration_seconds"],
            }
        )
        if attempt > budget or not _retryable(report):
            break
        if signature == previous_signature:
            # Two attempts in a row ended the same way, step for step. The flake theory is
            # spent: another browser would cost a minute and prove the same thing again.
            attempts[-1]["stable"] = True
            break
        previous_signature = signature

    report["existing_tests"] = existing_rows
    run_state["report"] = report
    if len(attempts) > 1:
        e2e_meta["attempts"] = attempts
    stages.append(
        {
            "stage": 2,
            "command": None,
            "returncode": supervised["returncode"],
            "duration_seconds": supervised["duration_seconds"],
            "events": len(events),
            "attempts": len(attempts),
            "timed_out": supervised["timed_out"],
            "stalled": supervised["stalled"],
            "malformed": len(malformed),
        }
    )
    redaction_hits += e2e_redact.write_jsonl(e2e_dir / "events.jsonl", events)

    redaction_hits += e2e_redact.write_json(
        e2e_dir / "report.json",
        {**report, "stderr_tail": stderr_tail, "malformed": malformed},
    )
    # ---- tag proposals: recorded, never applied -----------------------------------
    # The runner's half of the tagging pass ends here. It knows which elements were driven
    # and whether each one could be tagged; it does not edit a template, because that is a
    # write to code the user owns and the user has not been asked yet.
    try:
        tag_entries = e2e_tagging.plan(project_root, e2e_tagging.proposals(report))
    except Exception as exc:  # observing the run must never be able to fail it
        tag_entries = []
        e2e_meta["tags"] = {"error": f"{type(exc).__name__}: {exc}"}
    if tag_entries:
        redaction_hits += e2e_redact.write_json(e2e_dir / "tag-proposals.json", tag_entries)
        e2e_meta["tags"] = {
            "file": str(e2e_dir / "tag-proposals.json"),
            "ready": len(e2e_tagging.ready(tag_entries)),
            "skipped": len(tag_entries) - len(e2e_tagging.ready(tag_entries)),
        }
    # ---- knowledge: what this run proved, for the next draft -------------------------
    # From the placeholder scenario, never the resolved one. A pass that needed a retry is
    # not recorded as proof; failures always count against what the store believed.
    try:
        e2e_meta["knowledge"] = e2e_knowledge.ingest(
            project_root, report, scenario, str(config.get("base_url") or ""), session_id,
            clean_first_attempt=len(attempts) == 1,
        )
    except Exception as exc:  # observing the run must never be able to fail it
        e2e_meta["knowledge"] = {"error": f"{type(exc).__name__}: {exc}"}
    e2e_meta["browser_verdict"] = report["browser_verdict"]
    e2e_meta["reason"] = report["reason"]
    e2e_meta["cleanup"] = {key: report["cleanup"][key] for key in ("status", "groups")}

    # ---- stage 3: hybrid review ---------------------------------------------------
    block, block_hits = e2e_redact.redact_text(
        evidence_block(report, artifacts=str(final_dir), spec_notes=parsed.get("uncertainties") or [])
    )
    redaction_hits += block_hits
    redaction_hits += e2e_redact.write_text(e2e_dir / "evidence.md", block)
    reviewer_content: str | None = None
    reviewer_error: str | None = None
    if report["browser_verdict"] != "incomplete":
        prompt, prompt_meta = executor._build_delegated_prompt(
            verify_route, "verify", task, session_id, project_root, e2e_evidence=block
        )
        review = executor._run_delegated(
            verify_route, "verify", task, session, session_id, project_root, work_dir,
            on_progress, session_manager, prompt=prompt, prompt_meta=prompt_meta, lock_claim=lock_claim,
            record_failure=False,
        )
        stages.append(
            {
                "stage": 3,
                "command": "verify",
                "prompt_id": (executor._last_call_meta or {}).get("prompt_id"),
                "ok": bool(review.get("ok")),
            }
        )
        if review.get("ok"):
            reviewer_content = review.get("content") or ""
        else:
            reviewer_error = str((review.get("meta") or {}).get("error_type") or "provider_failure")

    norm = to_verification(
        report,
        reviewer_content=reviewer_content,
        reviewer_error=reviewer_error,
        existing_tests=existing_rows,
        attempts=attempts,
    )
    redaction_hits += e2e_redact.write_text(e2e_dir / "verification.md", norm["content"])
    e2e_current.e2e_finish(project_root, session_id, e2e_dir, final_dir)
    if redaction_hits:
        e2e_meta["artifact_redactions"] = redaction_hits
    return _finish(norm)


def _draft(
    executor,
    project_root: Path,
    session_id: str,
    session: dict,
    task: str,
    work_dir: str | None,
    on_progress,
    session_manager,
    lock_claim: dict | None,
    config: dict,
    e2e_meta: dict,
    stages: list[dict],
    lookup: dict,
    secret_errors: list[str],
) -> dict:
    """Stage 1 only: second_agent proposes, the runtime validates, the user decides."""
    spec_route = executor._router_for(project_root).route("e2e_spec")
    # What earlier runs against this origin proved. A sidecar, not prompt text: the draft
    # reads it the way it reads facts, and an empty store offers nothing rather than an
    # empty section the model might fill in itself.
    try:
        offered = e2e_knowledge.write_sidecar(project_root, session_id, str(config.get("base_url") or ""))
        e2e_meta["knowledge"] = {"offered": offered}
    except Exception as exc:  # reuse is an optimisation; a draft never fails for it
        offered = 0
        e2e_meta["knowledge"] = {"offered": 0, "error": f"{type(exc).__name__}: {exc}"}
    prompt, prompt_meta = executor._build_delegated_prompt(
        spec_route, "e2e_spec", task, session_id, project_root, has_e2e_knowledge=offered > 0
    )
    first = executor._run_delegated(
        spec_route, "e2e_spec", task, session, session_id, project_root, work_dir,
        on_progress, session_manager, prompt=prompt, prompt_meta=prompt_meta, lock_claim=lock_claim,
        record_failure=False,
    )
    stages.append({"stage": 1, "command": "e2e_spec", "prompt_id": (executor._last_call_meta or {}).get("prompt_id"), "ok": bool(first.get("ok"))})
    if not first.get("ok"):
        # Returned without finalisation, so the spend is recorded here — once.
        first.setdefault("meta", {}).update({"invocation": INVOCATION, "phase": "draft", "e2e": e2e_meta})
        return executor._record_failed_call(first, project_root, INVOCATION, task, session_id)
    content = first.get("content") or ""
    gap = spec_gap(content)
    if gap and gap.get("recoverable") and session.get("provider_session_id"):
        follow_up = executor._run_delegated(
            spec_route, "e2e_spec", task, session, session_id, project_root, work_dir,
            on_progress, session_manager, prompt=spec_continuation_prompt(gap), prompt_meta={}, lock_claim=lock_claim,
            record_failure=False,
        )
        stages.append({"stage": 1, "command": "e2e_spec", "continuation": True, "ok": bool(follow_up.get("ok"))})
        if follow_up.get("ok"):
            content = content.rstrip() + "\n\n" + (follow_up.get("content") or "")
            gap = spec_gap(content)
    if gap:
        e2e_meta["reason"] = "spec_invalid"
        return _draft_result(project_root, session_id, e2e_meta, status="invalid", errors=list(gap.get("missing") or [gap.get("reason", "")]))
    parsed = parse_spec(content)
    e2e_meta["spec_source"] = "e2e_spec"
    scenario = parsed["scenario"]
    spec_errors, _runnable, _skipped = _spec_errors(scenario, parsed.get("existing_tests") or [], config, project_root)
    # A proposed read-only POST is only a proposal: the draft lists it for the user to
    # confirm, and nothing reaches the guard until the request's settings name it. An
    # ungrounded proposal still makes the draft invalid, like an ungrounded claim.
    read_only_errors = validate_read_only_requests(parsed.get("read_only_requests") or [], project_root)
    errors = [*spec_errors, *read_only_errors, *secret_errors]
    names = env_references(scenario)
    # Not an error at draft time: the user may fill secrets.json after reading the draft.
    # Nothing ever created that file, so a user asked to "fill it" had nowhere to start;
    # the draft writes it — shape and names from the credential registry, profile names from
    # the request settings, never from the scenario second_agent proposed.
    missing = [name for name in names if name not in lookup]
    if missing and not (e2e_meta.get("secrets") or {}).get("exists"):
        e2e_meta.setdefault("secrets", {})["template_created"] = e2e_request.ensure_secrets_template(
            project_root, e2e_request.template_profiles(config)
        )
    if spec_errors or read_only_errors:
        e2e_meta["reason"] = "spec_invalid"
    elif secret_errors:
        e2e_meta["reason"] = "secrets_invalid"
    return _draft_result(
        project_root,
        session_id,
        e2e_meta,
        status="invalid" if errors else "ready",
        parsed=parsed,
        errors=errors,
        env_names=names,
        missing_env=missing,
    )
