"""Readiness checks: doctor, sweep, bundle integrity, MCP scan, prune."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.opencode_install import provider_callable
from core.workspace_paths import (
    LOCK_TTL_SECONDS,
    PROVIDER_CONFIG_NAME,
    WORKFLOW_DIRNAME,
    atomic_write_json,
    atomic_write_text,
    now_iso,
    read_json_file,
    workflow_paths,
)
from utils import osutil

# Split out of this module; re-exported because core/workflow_runtime.py re-exports
# them FROM here, and callers still reach them through that chain.
from core.bundle_integrity import (  # noqa: E402,F401
    _bundle_integrity,
    _expand_home,
    _installed_intent_mode,
    _installed_path_for,
    _marker_block,
    _os_variant_skip,
    _select_intent_section,
)
from core.mcp_scan import (  # noqa: E402,F401
    _classify_mcp,
    _mcp_config_candidates,
    _mcp_reachable,
    _scan_mcp,
)


def check_writable(path: Path) -> tuple[bool, str | None]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
        return True, None
    except OSError as exc:
        return False, str(exc)


def python_callable() -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [shutil.which("python") or "python", "--version"],
            capture_output=True,
            text=True,
            check=False,
            **osutil.hidden_run_kwargs(),  # Windows: no console flash on readiness probe
        )
    except OSError as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def run_doctor(
    project_root: Path, provider_command: str, session_id: str | None = None
) -> dict:
    from core.workflow_runtime import needs_upgrade, resolve_agent_workflow_path, script_drift, validate_config, workspace_versions

    paths = workflow_paths(project_root, session_id)
    issues: list[str] = []
    recommended_fixes: list[str] = []
    checks: dict[str, object] = {}

    checks["project_root_valid"] = project_root.exists()
    if not paths["workflow_dir"].exists():
        issues.append(".workflow directory missing")
        recommended_fixes.append("Run init first")

    # config.json is static (strict); per-session state/scope/cache are created lazily
    # on first delegated call, so absence is normal, not a failure.
    try:
        checks["config_json_valid"] = paths["config"].exists() and isinstance(
            read_json_file(paths["config"]), dict
        )
    except ValueError as exc:
        checks["config_json_valid"] = False
        issues.append(str(exc))
        recommended_fixes.append(f"Fix invalid JSON at {paths['config']}")

    for key, path in {
        "state_json_valid": paths["state"],
        "scope_json_valid": paths["scope"],
        "command_cache_json_valid": paths["command_cache"],
    }.items():
        if not path.exists():
            checks[key] = "lazy (created on first delegated call)"
            continue
        try:
            checks[key] = isinstance(read_json_file(path), dict)
        except ValueError as exc:
            checks[key] = False
            issues.append(str(exc))
            recommended_fixes.append(f"Fix invalid JSON at {path}")

    runtime_writable, runtime_error = check_writable(paths["runtime_dir"] / ".touch")
    try:
        (paths["runtime_dir"] / ".touch").unlink()
    except FileNotFoundError:
        pass
    checks["runtime_folder_writable"] = runtime_writable
    if runtime_error:
        issues.append(f"runtime folder not writable: {runtime_error}")

    prompt_writable, prompt_error = check_writable(paths["prompt"])
    checks["prompt_writable"] = prompt_writable
    if prompt_error:
        issues.append(f"prompt.txt not writable: {prompt_error}")

    try:
        if paths["prompt_meta"].exists():
            read_json_file(paths["prompt_meta"])
        checks["prompt_meta_valid_or_creatable"] = True
    except ValueError as exc:
        checks["prompt_meta_valid_or_creatable"] = False
        issues.append(str(exc))

    lock_state = "missing"
    if paths["lock"].exists():
        try:
            lock_data = read_json_file(paths["lock"])
            created = datetime.fromisoformat(lock_data.get("created_at"))
            lock_state = (
                "active"
                if datetime.now(timezone.utc) - created
                <= timedelta(seconds=LOCK_TTL_SECONDS)
                else "stale"
            )
        except Exception:
            lock_state = "stale"
    checks["lock_state"] = lock_state

    reports_writable, reports_error = check_writable(paths["doctor_report"])
    checks["reports_folder_writable"] = reports_writable
    if reports_error:
        issues.append(f"reports folder not writable: {reports_error}")

    gitignore_ok = False
    gitignore_path = project_root / ".gitignore"
    if gitignore_path.exists():
        gitignore_ok = ".workflow/" in [
            line.strip()
            for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        ]
    checks["root_gitignore_ignores_workflow"] = gitignore_ok
    if not gitignore_ok:
        issues.append("root .gitignore does not ignore .workflow/")
        recommended_fixes.append("Add .workflow/ to root .gitignore or rerun init")

    resolver = resolve_agent_workflow_path(project_root)
    checks["agent_workflow_resolver"] = resolver
    configured_path = None
    try:
        configured_path = (
            read_json_file(paths["config"])
            .get("runtime", {})
            .get("agent_workflow_path")
        )
    except Exception:
        configured_path = None
    checks["runtime_agent_workflow_path_valid"] = (
        bool(configured_path and Path(configured_path).exists())
        if configured_path
        else None
    )
    env_agent_path = os.getenv("AGENT_PATH")
    checks["env_agent_path_valid"] = (
        bool(
            env_agent_path
            and Path(env_agent_path).exists()
            and Path(env_agent_path).suffix == ".py"
        )
        if env_agent_path
        else None
    )

    python_ok, python_output = python_callable()
    checks["python_callable"] = {"ok": python_ok, "output": python_output}
    if not python_ok:
        issues.append("python not callable")
        recommended_fixes.append("Ensure python is installed and available in PATH")

    opencode_ok, opencode_output = provider_callable(provider_command)
    checks["provider_callable"] = {"ok": opencode_ok, "output": opencode_output}
    if not opencode_ok:
        issues.append("opencode not callable")
        recommended_fixes.append(
            "Ensure opencode CLI is installed and available in PATH"
        )

    checks["graphify_out_exists"] = (project_root / "graphify-out").exists()

    # Config knob sanity: unknown keys / wrong types silently do nothing. Surfaced as a
    # recommended fix, not an issue — the runtime readers fall back safely, so a typo must
    # not make an otherwise-working workspace report NOT_READY.
    try:
        parsed_config = read_json_file(paths["config"])
    except (OSError, ValueError):
        parsed_config = None
    config_warnings = (
        validate_config(parsed_config) if isinstance(parsed_config, dict) else []
    )
    checks["config_warnings"] = config_warnings or "none"
    if config_warnings:
        recommended_fixes.append(
            f"Review .workflow/config.json: {len(config_warnings)} knob warning(s) "
            "(unknown key or wrong type — silently ignored)"
        )

    # Version drift: the workspace still works, but its generated scripts and config
    # defaults are the previous build's. Reported as its own status rather than as an
    # issue — calling a working workspace NOT_READY would block flows over staleness.
    checks["workspace_versions"] = workspace_versions(project_root)
    workspace_stale = needs_upgrade(project_root)
    checks["workspace_upgrade_needed"] = workspace_stale
    if workspace_stale:
        recommended_fixes.append(
            "Run `--command upgrade` to regenerate .workflow scripts and backfill new config keys"
        )

    # Script drift: the entry scripts are the only way in, so one that no longer matches the
    # generator routes commands the CLI has since stopped accepting. An issue, not a note —
    # a workspace whose front door rejects its own commands is not ready.
    tool_main_py = configured_path or resolver.get("path")
    if tool_main_py:
        drifted = script_drift(project_root, str(tool_main_py))
        checks["run_script_drift"] = drifted or "none"
        if drifted:
            issues.append(
                "entry script drift: "
                + ", ".join(f"{d['script']} ({d['state']})" for d in drifted)
            )
            if any(d["state"] != "foreign_os_leftover" for d in drifted):
                recommended_fixes.append(
                    "Run `--command upgrade` to rewrite the drifted .workflow entry script(s)"
                )
            # upgrade deletes these, but it cannot always: a read-only mount, a network
            # share, another process holding the handle. Naming the manual route as well
            # keeps a workspace from sitting at NOT_READY with only advice that failed.
            leftovers = [
                d["script"] for d in drifted if d["state"] == "foreign_os_leftover"
            ]
            if leftovers:
                recommended_fixes.append(
                    "Run `--command upgrade` to remove the other platform's leftover "
                    f"script(s): {', '.join(f'.workflow/{name}' for name in leftovers)} "
                    "— delete them by hand if upgrade has already run and they remain"
                )
    else:
        checks["run_script_drift"] = "SKIPPED — agent-workflow main.py path unresolved"

    # second_agent MCP safety: enumerate opencode MCP servers, flag any that exceed
    # the read-only evidence role (write/exec/fs/db/browser/etc).
    mcp = _scan_mcp(project_root)
    checks["mcp_second_agent"] = mcp
    active_risky = [
        s["name"]
        for s in mcp["servers"]
        if s["enabled"] and s["classification"] == "risk"
    ]
    active_unknown = [
        s["name"]
        for s in mcp["servers"]
        if s["enabled"] and s["classification"] == "unknown"
    ]
    if active_risky:
        issues.append(
            f"second_agent MCP risk: {', '.join(active_risky)} — write/exec-capable, exceeds read-only role"
        )
        recommended_fixes.append(
            "Disable write/exec-capable MCP for opencode (second_agent = read-only evidence), or confirm intended"
        )
    if active_unknown:
        issues.append(
            f"second_agent MCP unknown: {', '.join(active_unknown)} — capability unverified for read-only safety"
        )
        recommended_fixes.append(
            "Review the flagged MCP server(s); ensure second_agent stays read-only"
        )
    # Permitted DB/data-inspection servers (laravel-boost family) — reported, not flagged:
    # second_agent MAY use these for read-only DB evidence.
    active_inspect = [
        s["name"]
        for s in mcp["servers"]
        if s["enabled"] and s["classification"] == "inspect"
    ]
    if active_inspect:
        checks["mcp_inspect_permitted"] = active_inspect
    # Liveness: an enabled server whose launch command is missing can never answer.
    unreachable = [
        s["name"]
        for s in mcp["servers"]
        if s["enabled"] and s.get("reachable") is False
    ]
    if unreachable:
        issues.append(
            f"second_agent MCP unreachable: {', '.join(unreachable)} — declared but launch command not on PATH"
        )
        recommended_fixes.append(
            "Install/fix the server command, or remove the dead MCP entry from opencode config"
        )

    # Session continuation: is the current main session linked to an opencode session?
    # An unlinked session re-bootstraps opencode every call (breaks 1 main = 1 second).
    import re as _re
    from config.settings import SESSION_DIR

    session_id = None
    try:
        session_block = read_json_file(paths["state"]).get("session")
        if isinstance(session_block, dict):
            session_id = session_block.get("id")
    except (ValueError, OSError):
        session_id = None

    if not session_id:
        checks["session_continuation"] = "no active session (state.json)"
    else:
        safe = _re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
        session_file = Path(SESSION_DIR) / f"{safe}.json"
        if not session_file.exists():
            checks["session_continuation"] = (
                f"no session record for {session_id} (first delegated call will bootstrap)"
            )
        else:
            try:
                opencode_id = read_json_file(session_file).get("provider_session_id")
            except (ValueError, OSError):
                opencode_id = None
            if opencode_id:
                checks["session_continuation"] = (
                    f"linked: {session_id} -> {opencode_id}"
                )
            else:
                checks["session_continuation"] = (
                    f"BROKEN: {session_id} has no provider_session_id — continuation re-bootstraps each call"
                )
                issues.append(
                    "session continuation broken: provider_session_id not captured for active session"
                )
                recommended_fixes.append(
                    "Re-run a delegated command; if it keeps failing, opencode session capture is failing (check opencode `run` output for a ses_ id)"
                )

    # Release integrity: the installed bundle must match dist/manifest.json exactly, the
    # manifest must not be older than its dist sources, and required hooks must be installed.
    # A drifted/stale/incomplete bundle still "runs" but ships behaviour nobody reviewed.
    integrity: object = "skipped: agent path unresolved"
    if resolver.get("ok") and resolver.get("path"):
        repo_root = Path(resolver["path"]).parent
        integrity = _bundle_integrity(
            repo_root / "dist" / "config",
            repo_root / "dist" / "manifest.json",
            project_root,
        )
    checks["bundle_integrity"] = integrity
    if isinstance(integrity, dict):
        if integrity.get("error"):
            issues.append(f"bundle integrity uncheckable: {integrity['error']}")
            recommended_fixes.append(
                "Regenerate the manifest: python tools/gen_manifest.py"
            )
        # Name the file AND why it drifted. "2 files differ" is the same sentence whether
        # the user edited a block on purpose or an install never ran, and those need
        # opposite responses.
        drift_by_reason: dict[str, list[str]] = {}
        for item in integrity.get("drift") or []:
            drift_by_reason.setdefault(item["reason"], []).append(item["path"])
        for reason, paths_ in sorted(drift_by_reason.items()):
            issues.append(
                f"bundle {reason}: {len(paths_)} file(s) — {', '.join(paths_[:5])}"
            )
        if "locally_edited" in drift_by_reason or "content_differs" in drift_by_reason:
            recommended_fixes.append(
                "Re-run install/upgrade to reinstall the shipped bundle; if dist/ changed on "
                "purpose, regenerate the manifest (python tools/gen_manifest.py)"
            )
        if "not_installed" in drift_by_reason:
            recommended_fixes.append("Re-run install to place the missing bundle files")
        if integrity.get("manifest_fresh") is False:
            issues.append(
                "stale manifest: dist/ sources are newer than dist/manifest.json"
            )
            recommended_fixes.append("Run: python tools/gen_manifest.py")
        if integrity.get("hooks_installed") is False:
            issues.append(
                "required hooks missing from install "
                "(session-bind/intent-gate-set/intent-gate-check)"
            )
            recommended_fixes.append("Re-run install to place the hook scripts")

    if issues:
        status = "NOT_READY"
    elif workspace_stale:
        status = "NEEDS_UPGRADE"
    else:
        status = "READY"
    # `checked_at` is the only field that changes between two runs over identical state.
    # Kept out of `result` so `doctor` twice can be diffed directly — with it inline, every
    # comparison showed a difference and proved nothing.
    payload = {
        "meta": {"checked_at": now_iso()},
        "result": {
            "status": status,
            "project_root": str(project_root),
            "issues": issues,
            "recommended_fixes": recommended_fixes,
            "checks": checks,
        },
        # Flat copies kept so existing readers of doctor.json keep working.
        "status": status,
        "checked_at": now_iso(),
        "project_root": str(project_root),
        "issues": issues,
        "recommended_fixes": recommended_fixes,
        "checks": checks,
    }
    atomic_write_json(paths["doctor_report"], payload)
    return {
        # NEEDS_UPGRADE is still ok: the workspace runs, it is just built by an older
        # build. Only real issues make doctor fail.
        "ok": status != "NOT_READY",
        "content": f"{status}: {len(issues)} issue(s), {len(recommended_fixes)} recommended fix(es)",
        "meta": {
            "status": status,
            "issues": issues,
            "recommended_fixes": recommended_fixes,
            "doctor_report": str(paths["doctor_report"]),
            "project_root": str(project_root),
        },
    }


def run_sweep(project_root: Path, session_id: str | None = None) -> dict:
    from core.workflow_runtime import load_workspace_state, update_command_cache

    paths = workflow_paths(project_root, session_id)
    changed_files: list[str] = []
    diff_summary = ""

    def git_error(detail: str, operation: str) -> dict:
        from core.contract import make_error

        return make_error(
            "sweep_git_error",
            detail or f"{operation} failed",
            next_action=(
                "Run the reported Git command in the project, fix the repository or "
                "Git installation, then retry sweep."
            ),
            meta={"project_root": str(project_root), "operation": operation},
        )

    def git_run(argv: list[str]):
        return subprocess.run(
            argv,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            **osutil.hidden_run_kwargs(),
        )

    try:
        name_results = [
            ("unstaged diff", git_run(["git", "diff", "--name-only"])),
            ("staged diff", git_run(["git", "diff", "--cached", "--name-only"])),
            (
                "untracked files",
                git_run(["git", "ls-files", "--others", "--exclude-standard"]),
            ),
        ]
        for operation, result in name_results:
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                return git_error(detail, operation)
        changed_files = list(
            dict.fromkeys(
                line.strip()
                for _operation, result in name_results
                for line in result.stdout.splitlines()
                if line.strip()
            )
        )
        stat_results = [
            ("unstaged stat", git_run(["git", "diff", "--stat"])),
            ("staged stat", git_run(["git", "diff", "--cached", "--stat"])),
        ]
        for operation, result in stat_results:
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                return git_error(detail, operation)
        diff_summary = "\n".join(
            result.stdout.strip()
            for _operation, result in stat_results
            if result.stdout.strip()
        )
    except OSError as exc:
        return git_error(str(exc), "launch git")

    loaded = load_workspace_state(project_root, session_id)
    scope = loaded["scope"]
    impact_radius = scope.get("impact_radius") or []
    risk_hits = []
    for file_path in changed_files:
        lower = file_path.lower()
        if any(
            token in lower
            for token in (
                "config",
                "auth",
                "payment",
                "schema",
                "migration",
                ".env",
                "secret",
                "credential",
            )
        ):
            risk_hits.append(file_path)
        if impact_radius and any(
            target and target in file_path for target in impact_radius
        ):
            risk_hits.append(file_path)

    if not changed_files:
        verdict = "skipped"
        reason = "no file changes detected"
    elif risk_hits:
        verdict = "repair_required"
        reason = f"risk indicators found in {len(set(risk_hits))} changed file(s)"
    else:
        verdict = "pass"
        reason = "no obvious impact issues detected"

    lines = [
        f"# Sweep Report",
        "",
        f"- verdict: {verdict}",
        f"- reason: {reason}",
        f"- checked_at: {now_iso()}",
        "",
        "## Changed Files",
    ]
    if changed_files:
        lines.extend(f"- {item}" for item in changed_files)
    else:
        lines.append("- none")
    lines.extend(["", "## Diff Summary", diff_summary or "(empty)"])
    if impact_radius:
        lines.extend(
            ["", "## Scope Impact Radius", *[f"- {item}" for item in impact_radius]]
        )
    if risk_hits:
        lines.extend(
            ["", "## Risk Signals", *[f"- {item}" for item in sorted(set(risk_hits))]]
        )
    report = "\n".join(lines).strip() + "\n"
    atomic_write_text(paths["sweep_report"], report)
    update_command_cache(
        project_root,
        "last_sweep_result",
        {
            "verdict": verdict,
            "reason": reason,
            "changed_files": changed_files,
            "diff_summary": diff_summary,
        },
        (loaded["state"].get("session") or {}).get("id"),
    )
    return {
        "ok": True,
        "content": (
            f"sweep {verdict}: {reason}; "
            f"{len(changed_files)} changed file(s)"
        ),
        "meta": {
            "verdict": verdict,
            "reason": reason,
            "changed_files": changed_files,
            "report": str(paths["sweep_report"]),
            "project_root": str(project_root),
        },
    }


def prune_sessions(project_root: Path, ttl_days: int = 7, keep_last: int = 20) -> dict:
    """Delete per-session dirs older than ttl_days, always keeping the newest keep_last.
    Recent (active) sessions survive the TTL, so this never reaps a live session."""
    sessions_dir = workflow_paths(project_root)["workflow_dir"] / "sessions"
    provider_dir = workflow_paths(project_root)["workflow_dir"] / "provider-sessions"
    if not sessions_dir.exists() and not provider_dir.exists():
        return {"removed": 0, "kept": 0}
    dirs = sorted(
        (p for p in sessions_dir.iterdir() if p.is_dir())
        if sessions_dir.exists()
        else (),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    removed = 0
    for index, path in enumerate(dirs):
        if index < keep_last:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    provider_files = sorted(
        (p for p in provider_dir.glob("*.json") if p.is_file())
        if provider_dir.exists()
        else (),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    provider_removed = 0
    for index, path in enumerate(provider_files):
        if index < keep_last:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                path.unlink()
                provider_removed += 1
        except OSError:
            continue
    return {
        "removed": removed + provider_removed,
        "kept": min(len(dirs), keep_last) + min(len(provider_files), keep_last),
    }
