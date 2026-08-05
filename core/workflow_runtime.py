import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import DEFAULT_PROVIDER, TOOL_VERSION
from utils import osutil


# Moved out in the v3.4.3 split; re-exported so existing importers of
# core.workflow_runtime keep working unchanged.
from adapters.opencode_install import (  # noqa: E402,F401
    _copy_provider_config,
    _install_project_opencode,
    _merge_provider_config,
    provider_callable,
)
from core.diagnostics import (  # noqa: E402,F401
    _bundle_integrity,
    _classify_mcp,
    _expand_home,
    _installed_intent_mode,
    _installed_path_for,
    _marker_block,
    _mcp_config_candidates,
    _mcp_reachable,
    _os_variant_skip,
    _scan_mcp,
    _select_intent_section,
    check_writable,
    prune_sessions,
    python_callable,
    run_doctor,
    run_sweep,
)
from core.runtime_io import (  # noqa: E402,F401
    write_call_meta,
    write_evidence_sidecars,
    write_prompt_handoff,
    write_redaction_audit,
    write_response_snapshot,
)
from core.runtime_lock import (  # noqa: E402,F401
    _process_identity,
    _runtime_lock_age_seconds,
    _runtime_lock_is_active,
    _runtime_lock_payload,
    _RuntimeTransitionGuard,
    acquire_runtime_lock,
    release_runtime_lock,
    runtime_lock_owned,
)
from core.workspace_paths import (  # noqa: E402,F401
    ARCHIVE_KEEP,
    CONFIG_VERSION,
    JSON_INDENT,
    LOCK_TTL_SECONDS,
    WORKFLOW_DIRNAME,
    _safe_component,
    _tool_paths,
    atomic_write_json,
    atomic_write_text,
    detect_project_root,
    ensure_valid_json_or_create,
    now_iso,
    read_json_file,
    slugify_project_name,
    workflow_paths,
)

VERIFY_MODES = ("delegated", "syntax")

# Keys the Python runtime actually reads. Everything else in commands/policies is an
# instruction to main_agent only — it is inert here, and renaming it changes nothing
# in this process. Kept explicit so "configured" is never mistaken for "enforced".
RUNTIME_CONSUMED_KEYS = (
    "commands.verify_mode",
    "policies.fact_relevant_limit",
    "policies.fact_recurrence_threshold",
    "policies.graph_leads_enabled",
    "policies.subagent_fanout_enabled",
    # Timeout/stall/probe live in opencode.json (adapter + job manager read them there),
    # not here — listed in the doctor report so their home is not a guessing game.
)

# Contracts that CANNOT be moved into this runtime, and why. Written down because the
# absence of enforcement keeps getting read as an oversight to fix rather than as a
# property of where the data lives. In every case the runtime never sees the bytes it
# would have to check:
#
#   [OPTIONS] block, per-claim attribution, the confidence triple
#       -> these appear in main_agent's OUTPUT. This process produces evidence for it
#          and never sees what it writes back to the user.
#   intent detection (running a command without the "/." prefix)
#       -> matches on the USER's message. No Python path receives one.
#   /.execute and its `-y` gate, commands.auto_verify_after_execute
#       -> /.execute is implemented entirely by main_agent editing files. There is no
#          Python entry point to hook, which is why the config key ships with that
#          caveat inline rather than as a promise.
#
# What IS checkable is the second agent's output, because it comes back through here:
# see core.contract.contract_warnings. Those are reported, never fatal.
UNENFORCEABLE_PROMPT_CONTRACTS = (
    "[OPTIONS] block in /.plan",
    "per-claim attribution tags",
    "confidence triple",
    "intent detection without the /. prefix",
    "/.execute -y approval gate",
    "commands.auto_verify_after_execute",
)


def default_commands() -> dict:
    return {
        # --- prompt-only: read by main_agent, NOT by this runtime ---
        "allow_analyze_to_plan": True,
        "allow_explore_to_plan": True,
        "auto_sweep_after_execute": True,
        # whether /.execute chains into /.verify on its own. false => /.execute reports
        # `verification: not_run` and must never call itself done. Prompt-only: /.execute
        # has no Python path at all, so this runtime cannot enforce it.
        "auto_verify_after_execute": False,
        # --- runtime-consumed ---
        # how deep /.verify goes when it does run:
        #   delegated -> full verification by second_agent
        #   syntax    -> local parse/name check on changed files, no test suite
        "verify_mode": "delegated",
    }


def default_policies() -> dict:
    return {
        # --- prompt-only ---
        "workflow_prefix": "/.",
        "chat_mode_for_plain_text": True,
        "fallback_requires_confirmation": True,
        "max_active_job_per_session": 1,
        # --- runtime-consumed (core/fact_store.py) ---
        "fact_relevant_limit": 3,
        "fact_recurrence_threshold": 5,
        # --- runtime-consumed (core/graph_index.py) ---
        # inject a ranked file shortlist from graphify-out/graph.json into evidence prompts
        "graph_leads_enabled": True,
        # --- runtime-consumed (core/prompt_builder.py) ---
        # ask second_agent to spawn one sub-agent per graph cluster (or per role slice
        # when there is no graph). ON: a delegated call that reads the codebase serially
        # is the slow path, and the fan-out instruction is what makes the second agent
        # use the parallel tools it already has. It does cost more quota per call and
        # produces a larger response — set false to go back to a single serial reader.
        "subagent_fanout_enabled": True,
    }


def _rewrite_superseded_keys(config: dict) -> bool:
    """Carry a config written by an earlier build onto the current key names.

    Additive backfill alone cannot do this: it would leave the retired key in place next
    to its replacement, and the user could not tell which one the runtime obeys.
    """
    commands = config.get("commands")
    if not isinstance(commands, dict) or "autoverify" not in commands:
        return False
    # `autoverify` was the single boolean that later split into two settings.
    legacy = commands.pop("autoverify")
    commands.setdefault("verify_mode", "delegated" if legacy else "syntax")
    policies = config.get("policies")
    if isinstance(policies, dict) and policies.get("fact_recurrence_threshold") == 3:
        # This compatibility rewrite only affects the short-lived schema that shipped
        # the old default; current user values are otherwise preserved.
        policies["fact_recurrence_threshold"] = default_policies()[
            "fact_recurrence_threshold"
        ]
    return True


def merge_config_defaults(config: dict) -> tuple[dict, bool]:
    """Additively backfill new keys into an existing config. User values always win.

    `ensure_valid_json_or_create` only writes a config when it is MISSING, so without
    this an already-initialized project would never see keys added by a later version.
    """
    changed = _rewrite_superseded_keys(config)
    for section, defaults in (
        ("commands", default_commands()),
        ("policies", default_policies()),
    ):
        current = config.get(section)
        if not isinstance(current, dict):
            current = {}
            config[section] = current
            changed = True
        for key, value in defaults.items():
            if key not in current:
                current[key] = value
                changed = True
    if config.get("version") != CONFIG_VERSION:
        config["version"] = CONFIG_VERSION
        changed = True

    # tool_version is derived, not a user setting: left alone it pins an upgraded
    # workspace to the version that first wrote it, and `doctor` then reports a
    # tool version the runtime is no longer running.
    from config.settings import COMPONENT_VERSIONS, TOOL_VERSION

    runtime = config.get("runtime")
    if isinstance(runtime, dict):
        if runtime.get("tool_version") != TOOL_VERSION:
            runtime["tool_version"] = TOOL_VERSION
            changed = True
        # runtime_version is derived like tool_version; backfill so an existing config
        # gains the component stamp the lazy-upgrade gate reads.
        if runtime.get("runtime_version") != COMPONENT_VERSIONS["runtime"]:
            runtime["runtime_version"] = COMPONENT_VERSIONS["runtime"]
            changed = True
    return config, changed


def workspace_versions(project_root: Path) -> dict:
    """What the workspace was built by vs what is running now.

    `installed`/`installed_config` are what .workflow/config.json records; `current` is
    what this process is. They drift the moment the tool is updated without re-running
    init, and nothing else in the runtime notices — the generated run scripts keep the
    paths and flags they were written with.
    """
    from config.settings import COMPONENT_VERSIONS, TOOL_VERSION

    installed = None
    installed_config = None
    installed_runtime = None
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
        runtime = config.get("runtime")
        if isinstance(runtime, dict):
            installed = runtime.get("tool_version")
            installed_runtime = runtime.get("runtime_version")
        installed_config = config.get("version")
    except (OSError, ValueError):
        pass
    return {
        "installed_tool_version": installed,
        "installed_config_version": installed_config,
        "installed_runtime_version": installed_runtime,
        "current_tool_version": TOOL_VERSION,
        "current_config_version": CONFIG_VERSION,
        "current_runtime_version": COMPONENT_VERSIONS["runtime"],
    }


def needs_upgrade(project_root: Path) -> bool:
    """True when the workspace was scaffolded by a different build than this one.

    Unknown (config unreadable or version absent) counts as needing an upgrade: a
    workspace we cannot identify is exactly the one most likely to be stale.
    """
    if not (project_root / WORKFLOW_DIRNAME).exists():
        return False  # not initialized at all — that is `init`, not `upgrade`
    versions = workspace_versions(project_root)
    return (
        versions["installed_tool_version"] != versions["current_tool_version"]
        or versions["installed_config_version"] != versions["current_config_version"]
    )


def upgrade_workflow_workspace(
    project_root: Path,
    agent_workflow_path: str | None,
    _capacity_guarded: bool = False,
) -> dict:
    """Bring an existing .workflow/ up to the running build. Manual-run, never automatic.

    Regenerates the derived parts (run/inspect/check scripts, config defaults, adapter
    config keys) and leaves everything owned by the user or by a live flow alone —
    sessions/ above all: a job may be running against it right now, and rewriting its
    state mid-flight would lose the very evidence the caller is waiting for.
    """
    if not _capacity_guarded:
        from core.job_manager import JobManager

        with JobManager().capacity_guard():
            return upgrade_workflow_workspace(
                project_root, agent_workflow_path, _capacity_guarded=True
            )

    paths = workflow_paths(project_root)
    if not paths["workflow_dir"].exists():
        raise ValueError(
            f"no {WORKFLOW_DIRNAME}/ at {project_root} — run init first, upgrade only "
            "refreshes an existing workspace"
        )

    active = active_jobs_for_workspace(project_root)
    if active:
        # Refuse rather than warn. The regenerated scripts and rewritten config.json are
        # read by the very flow that is mid-call, and its session state is the evidence
        # the caller is currently waiting on. "Preserved sessions/" is only true if
        # nothing is writing to them while this runs.
        listed = ", ".join(f"{j['job_id']} ({j['command']})" for j in active[:3])
        raise ValueError(
            f"{len(active)} job(s) still running against {project_root}: {listed}. "
            "Wait for them to finish (or fail them) before upgrading — regenerating the "
            "workspace under a live call can lose its session state."
        )

    before = workspace_versions(project_root)
    tool = _tool_paths(agent_workflow_path)

    # Ahead of every read below: v3.4.3 renamed the provider keys with no read-side
    # alias, so a v3.4.2 workspace must be translated before anything interprets it.
    # Idempotent, so a workspace already on the new names passes straight through.
    from core.provider_migration import migrate_provider_keys

    provider_migration = migrate_provider_keys(project_root)

    config_changed = False
    if not paths["config"].exists():
        config = default_config(project_root, agent_workflow_path)
        config_changed = True
    else:
        try:
            config = read_json_file(paths["config"])
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"refusing to overwrite unreadable workflow config {paths['config']}: {exc}"
            ) from exc
        if not isinstance(config, dict):
            raise ValueError(
                f"workflow config must be a JSON object: {paths['config']}"
            )
        if "runtime" in config and not isinstance(config.get("runtime"), dict):
            raise ValueError(
                f"workflow config runtime must be an object: {paths['config']}"
            )
        config, config_changed = merge_config_defaults(config)
        runtime = config.setdefault("runtime", {})
        # Tool paths are absolute and machine-specific: an upgrade after the repo moved
        # must repoint them, or the regenerated scripts call a main.py that is gone.
        for key in (
            "main_py_path",
            "check_py_path",
            "tool_dir",
            "tool_version",
            "runtime_version",
        ):
            if runtime.get(key) != tool[key]:
                runtime[key] = tool[key]
                config_changed = True
        if (
            agent_workflow_path
            and runtime.get("agent_workflow_path") != agent_workflow_path
        ):
            runtime["agent_workflow_path"] = agent_workflow_path
            config_changed = True
    if config_changed:
        atomic_write_json(paths["config"], config)

    opencode_added = _merge_provider_config(project_root, tool["tool_dir"])
    # Refresh the project boundary too. Skipping it here would mean a deny-rule added in a
    # newer build never reaches a workspace that was scaffolded on an older one.
    project_opencode = _install_project_opencode(project_root, tool["tool_dir"])

    # Always render; the generator writes only what actually differs. The old gate keyed on
    # the runtime version bumping, which meant a generator change shipped without a version
    # bump never reached an existing workspace — the script on disk stayed wrong until
    # someone re-ran init. Content is the honest signal; the version number was a proxy for
    # it that could silently disagree.
    scripts = _generate_run_scripts(project_root, tool["main_py_path"])
    gitignore_updated = ensure_root_gitignore_entry(project_root)

    return {
        "project_root": str(project_root),
        "from": before,
        "to": workspace_versions(project_root),
        "config_updated": config_changed,
        "provider_migration": provider_migration,
        "opencode_keys_added": opencode_added,
        "project_opencode": project_opencode,
        "diverged_from_defaults": diverged_defaults(config),
        "regenerated_scripts": scripts,
        "gitignore_updated": gitignore_updated,
        "preserved": [str(paths["workflow_dir"] / "sessions")],
        "tool": tool,
    }


def active_jobs_for_workspace(project_root: Path) -> list[dict]:
    """Pending/running jobs whose work_dir is this project. [] on any failure.

    Best-effort by design: this exists to stop an upgrade from landing under a live
    call, and a job store it cannot read is not a reason to block one.
    """
    try:
        from core.job_manager import DEAD, JobManager

        manager = JobManager()
        target = str(Path(project_root).resolve())
        out: list[dict] = []
        for path in manager.job_dir.glob("job_*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") not in {"pending", "running", "recovering"}:
                continue
            work_dir = job.get("work_dir")
            if work_dir:
                try:
                    job_root = str(detect_project_root(work_dir).resolve())
                except (OSError, ValueError):
                    job_root = str(Path(work_dir).resolve())
                if job_root != target:
                    continue
            # A job whose worker is gone is not "active", it is unreaped. Blocking the
            # upgrade on one would make a crashed worker permanently jam the command.
            if manager.liveness(job) == DEAD:
                continue
            out.append(job)
        return out
    except Exception:
        return []


def diverged_defaults(config: dict) -> list[dict]:
    """Settings whose stored value differs from what this build ships as the default.

    Reported, never rewritten. A value already in the file may be a deliberate choice
    or may just be the previous build's default frozen in place, and this code cannot
    tell them apart — the backfill is additive precisely so it never has to guess. What
    it can do is stop the difference from being invisible: a default that changed
    between builds otherwise reaches only projects that never ran the old one.
    """
    out: list[dict] = []
    for section, defaults in (
        ("commands", default_commands()),
        ("policies", default_policies()),
    ):
        current = config.get(section)
        if not isinstance(current, dict):
            continue
        for key, shipped in defaults.items():
            if key in current and current[key] != shipped:
                out.append(
                    {
                        "key": f"{section}.{key}",
                        "yours": current[key],
                        "shipped_default": shipped,
                    }
                )
    return out


def _capabilities_path(project_root: Path) -> Path:
    return workflow_paths(project_root)["workflow_dir"] / "capabilities.json"


def fanout_capability(project_root: Path) -> bool | None:
    """Learned opencode fan-out capability for this project.

    True/False once a delegated run has revealed it; None while unprobed. Default fan-out
    stays ON — this only flips it OFF after opencode itself reports no spawn tool, so the
    prompt stops carrying a fan-out plan opencode cannot execute.

    A False verdict EXPIRES. It rests on one sentence the second agent wrote about itself,
    and that sentence is not always true. Worse, it is self-sealing: with fan-out off the
    prompt no longer asks for fan-out, so no later run can produce the evidence that would
    turn it back on. Ageing it out means a wrong verdict costs one stale window, not the
    life of the project. True never expires — a capability that was demonstrated once does
    not need re-proving.
    """
    from config.settings import DEFAULT_FANOUT_RECHECK_HOURS

    try:
        data = read_json_file(_capabilities_path(project_root))
    except (OSError, ValueError):
        return None
    value = data.get("subagent_fanout_capable") if isinstance(data, dict) else None
    if not isinstance(value, bool):
        return None
    if value or DEFAULT_FANOUT_RECHECK_HOURS <= 0:
        return value
    stamped = data.get("subagent_fanout_capable_at")
    if not isinstance(stamped, str):
        # No timestamp means it was written by an older build. Retry rather than honour a
        # verdict whose age cannot be established.
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(stamped)
    except (TypeError, ValueError):
        return None
    if age.total_seconds() >= DEFAULT_FANOUT_RECHECK_HOURS * 3600:
        return None
    return value


def set_fanout_capability(project_root: Path, capable: bool) -> None:
    """Persist the observed fan-out capability, refreshing when it is merely reconfirmed.

    An unchanged verdict used to be a no-op. Once False verdicts expire, that silently
    broke them: a run that observed the same limitation again left the old timestamp in
    place, so the verdict aged out despite being confirmed every single time. The stamp
    records when the evidence was last SEEN, not when the answer last changed.
    """
    path = _capabilities_path(project_root)
    try:
        data = read_json_file(path)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["subagent_fanout_capable"] = capable
    data["subagent_fanout_capable_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def validate_config(config: dict) -> list[str]:
    """Structural sanity of config.json's user-tunable sections.

    Two silent failure modes this surfaces: an UNKNOWN key (a misspelled knob does nothing,
    yet reads as "configured"), and a TYPE mismatch (a bool knob set to a string is ignored
    by the reader that expects a bool, again silently). Reported, never fatal or rewritten —
    same posture as diverged_defaults; the runtime readers all fall back safely. Only
    commands/ and policies/ are user knobs; version/project/runtime are structural.
    """
    warnings: list[str] = []
    for section, section_defaults in (
        ("commands", default_commands()),
        ("policies", default_policies()),
    ):
        current = config.get(section)
        if current is None:
            continue  # an absent section is backfilled by merge_config_defaults, not an error
        if not isinstance(current, dict):
            warnings.append(f"{section}: {type(current).__name__}, expected object")
            continue
        for key, value in current.items():
            if key not in section_defaults:
                warnings.append(
                    f"{section}.{key}: unknown key (typo? the runtime ignores it)"
                )
                continue
            default = section_defaults[key]
            # bool is a subclass of int, so an int knob set to True (or a bool knob set to
            # 0/1) would slip past a plain isinstance — compare bool-ness explicitly.
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    warnings.append(
                        f"{section}.{key}: {type(value).__name__}, expected bool"
                    )
            elif isinstance(value, bool) or not isinstance(value, type(default)):
                warnings.append(
                    f"{section}.{key}: {type(value).__name__}, expected {type(default).__name__}"
                )
    commands = config.get("commands")
    if isinstance(commands, dict):
        mode = commands.get("verify_mode")
        if mode is not None and mode not in VERIFY_MODES:
            warnings.append(
                f"commands.verify_mode: '{mode}' not in {VERIFY_MODES} (falls back to delegated)"
            )
    return warnings



def verify_mode(project_root: Path) -> str:
    """commands.verify_mode. Anything unreadable or unrecognised falls back to
    'delegated' — an unclear setting must not silently downgrade verification."""
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
    except (OSError, ValueError):
        return "delegated"
    commands = config.get("commands")
    if not isinstance(commands, dict):
        return "delegated"
    mode = commands.get("verify_mode", "delegated")
    return mode if mode in VERIFY_MODES else "delegated"


def auto_verify_after_execute(project_root: Path) -> bool:
    """commands.auto_verify_after_execute.

    Still prompt-only in effect — /.execute has no Python path, so nothing here can
    make it chain into /.verify. What this does buy: the value now rides out on every
    delegated result, so main_agent reads the project's actual setting instead of
    recalling what the config said.
    """
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
    except (OSError, ValueError):
        return bool(default_commands()["auto_verify_after_execute"])
    commands = config.get("commands")
    if not isinstance(commands, dict):
        return bool(default_commands()["auto_verify_after_execute"])
    return bool(
        commands.get(
            "auto_verify_after_execute",
            default_commands()["auto_verify_after_execute"],
        )
    )


def graph_leads_enabled(project_root: Path) -> bool:
    """policies.graph_leads_enabled. Defaults to on; an unreadable config must not
    silently disable the shortlist (a missing graph already degrades to no leads)."""
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
    except (OSError, ValueError):
        return True
    policies = config.get("policies")
    if not isinstance(policies, dict):
        return True
    return bool(policies.get("graph_leads_enabled", True))


def subagent_fanout_enabled(project_root: Path) -> bool:
    """policies.subagent_fanout_enabled. Defaults to ON.

    An unreadable config falls back to the default rather than to off: the previous
    fail-closed behaviour meant a malformed config silently downgraded every call to a
    serial read, with nothing in the output saying so.
    """
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
    except (OSError, ValueError):
        return True
    policies = config.get("policies")
    if not isinstance(policies, dict):
        return True
    return bool(policies.get("subagent_fanout_enabled", True))


def default_config(project_root: Path, agent_workflow_path: str | None) -> dict:
    project_name = project_root.name
    tool = _tool_paths(agent_workflow_path)
    return {
        "version": CONFIG_VERSION,
        "project": {
            "name": project_name,
            "slug": slugify_project_name(project_name),
            "root": str(project_root),
        },
        "runtime": {
            "agent_workflow_path": agent_workflow_path,
            "main_py_path": tool["main_py_path"],
            "check_py_path": tool["check_py_path"],
            "tool_dir": tool["tool_dir"],
            "tool_version": tool["tool_version"],
            "runtime_version": tool["runtime_version"],
            # Live selector as of v3.4.3, not a label: adapters.registry resolves the
            # adapter from this value. Was inert metadata in every earlier version.
            "second_agent": DEFAULT_PROVIDER,
            "main_agent": "agnostic",
            "provider_config": ".workflow/second_agent.json",
            "sessions_dir": ".workflow/sessions",
            "per_session_layout": "sessions/<session_id>/: state.json, scope.json, command-cache.json, runtime/{prompt.txt,response.last.md,prompt.meta.json,lock}, logs/",
        },
        "commands": default_commands(),
        "policies": default_policies(),
    }


def default_state(project_root: Path) -> dict:
    project_name = project_root.name
    return {
        "version": 1,
        "project": {
            "name": project_name,
            "root": str(project_root),
            "slug": slugify_project_name(project_name),
        },
        "session": None,
        "workflow": {
            "stage": "initialized",
            "last_command": None,
            "objective": None,
            "plan_readiness": "unknown",
        },
        "context": {
            "evidence_summary": [],
            "affected_files": [],
            "affected_symbols": [],
            "assumptions": [],
            "risks": [],
            "open_questions": [],
        },
        "guards": {
            "state_version": 1,
            "scope_version": 0,
            "last_prompt_id": None,
        },
    }


def default_scope() -> dict:
    return {
        "session_id": None,
        "editable_scope": [],
        "readable_scope": [],
        "impact_radius": [],
        "out_of_scope": [],
        "verification_targets": [],
        "post_edit_checks": [
            "git_diff",
            "changed_symbol_reference_search",
            "caller_callee_check",
            "type_contract_check",
            "config_flag_check",
            "ui_api_consumer_check",
        ],
    }


def default_command_cache() -> dict:
    return {
        "session_id": None,
        "last_explore_result": None,
        "last_analyze_result": None,
        "last_plan_result": None,
        "last_execute_diff": None,
        "last_sweep_result": None,
    }



def _build_run_scripts(project_root: Path, main_py: str) -> list[tuple[Path, str]]:
    """Compose run/inspect/check scripts so main_agent calls one script.

    Building is separated from writing so doctor can compare what is on disk against what
    this function would produce — a script that drifted out of step with the generator is
    invisible to a check that only asks whether the file exists.

    Each script uses a python resolvable on ITS OWN platform: the current-OS script
    gets the exact interpreter; the cross-OS script gets a generic name (python/python3)
    resolved via PATH on the target machine — so a project copied across OSes still runs.
    """
    from config.settings import DEFAULT_MAX_TASK_CHARS

    ps_py = osutil.python_exe() if osutil.IS_WINDOWS else "python"
    sh_py = osutil.python_exe() if not osutil.IS_WINDOWS else "python3"
    check_py = str(Path(main_py).parent / "check.py")
    root = str(project_root)
    workflow_dir = project_root / WORKFLOW_DIRNAME

    # Background (job) commands go through await+job-command; the rest run directly.
    run_ps1 = (
        "param([Parameter(Mandatory=$true)][string]$Command,"
        '[string]$Task="",'
        "[string]$Session=$env:MAIN_SESSION_ID)\n"
        'if (-not $Session) { $Session = "default" }\n'
        'if ($Session -eq "default") { [Console]::Error.WriteLine("[workflow] WARN: session=default - pass MAIN_SESSION_ID (arg 3) for concurrent-safe isolation") }\n'
        # Pre-dispatch task-size warning: the runtime truncates the task at
        # DEFAULT_MAX_TASK_CHARS, silently. Surface it BEFORE dispatch so main_agent shortens
        # the instruction instead of blindly pre-splitting into two calls.
        f'if ($Task.Length -gt {DEFAULT_MAX_TASK_CHARS}) {{ [Console]::Error.WriteLine("[workflow] WARN: task is $($Task.Length) chars > {DEFAULT_MAX_TASK_CHARS}-char cap; it WILL be truncated. Shorten the instruction (do not paste evidence into the task) rather than pre-splitting into multiple calls.") }}\n'
        "$bg = @('explore','plan','analyze','verify')\n"
        "if ($bg -contains $Command) {\n"
        # Pre-flight gate: dispatching a delegated run satisfies the gate -> clear the marker
        # so the PreToolUse hook stops blocking gather tools for the rest of this turn.
        f'  $mk = Join-Path "{root}" ".workflow\\sessions\\$Session\\runtime\\delegated.marker"\n'
        "  if (Test-Path -LiteralPath $mk) { Remove-Item -LiteralPath $mk -Force -ErrorAction SilentlyContinue }\n"
        f'  $a = @("{main_py}", "--command", "await", "--job-command", $Command)\n'
        "} else {\n"
        f'  $a = @("{main_py}", "--command", $Command)\n'
        "}\n"
        # PowerShell drops empty-string arguments on their way to a native exe, so a literal
        # `--prompt $Task` with no task reaches argparse as a bare `--prompt` and it errors
        # with "expected one argument". Local commands do not need a prompt at all, so the
        # flag is only appended when there is something to put after it.
        'if ($Task) { $a += @("--prompt", $Task) }\n'
        f'$a += @("--session", $Session, "--work-dir", "{root}", "--pretty")\n'
        f'& "{ps_py}" @a\n'
    )
    run_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'COMMAND="${1:?command required}"\n'
        'TASK="${2:-}"\n'
        'SESSION="${3:-${MAIN_SESSION_ID:-default}}"\n'
        '[ "$SESSION" = "default" ] && echo "[workflow] WARN: session=default - pass MAIN_SESSION_ID for concurrent-safe isolation" >&2\n'
        f'[ "${{#TASK}}" -gt {DEFAULT_MAX_TASK_CHARS} ] && echo "[workflow] WARN: task is ${{#TASK}} chars > {DEFAULT_MAX_TASK_CHARS}-char cap; it WILL be truncated. Shorten the instruction rather than pre-splitting." >&2\n'
        'case " explore plan analyze verify " in\n'
        '  *" $COMMAND "*)\n'
        # Pre-flight gate: clear the marker before dispatching (delegation satisfies the gate).
        f'    MK="{root}/.workflow/sessions/$SESSION/runtime/delegated.marker"\n'
        '    [ -f "$MK" ] && rm -f "$MK"\n'
        f'    ARGS=("{main_py}" --command await --job-command "$COMMAND") ;;\n'
        "  *)\n"
        f'    ARGS=("{main_py}" --command "$COMMAND") ;;\n'
        "esac\n"
        # Kept in step with the PowerShell branch: no task, no --prompt. Local commands do
        # not take one, and an empty value buys nothing on either platform.
        'if [ -n "$TASK" ]; then ARGS+=(--prompt "$TASK"); fi\n'
        f'ARGS+=(--session "$SESSION" --work-dir "{root}" --pretty)\n'
        f'exec "{sh_py}" "${{ARGS[@]}}"\n'
    )
    inspect_ps1 = (
        f'& "{ps_py}" "{main_py}" --command inspect --work-dir "{root}" --pretty\n'
    )
    inspect_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{sh_py}" "{main_py}" --command inspect --work-dir "{root}" --pretty\n'
    )
    # Attach to an existing job by id (recovery after a foreground timeout). Passes through
    # flags like --wait --result to check.py, which polls without spawning a new run.
    check_ps1 = (
        "param([Parameter(Mandatory=$true)][string]$JobId,"
        "[Parameter(ValueFromRemainingArguments=$true)]$Rest)\n"
        f'& "{ps_py}" "{check_py}" $JobId @Rest\n'
    )
    check_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{sh_py}" "{check_py}" "$@"\n'
    )

    # Generate only the current OS's flavour: Windows gets .ps1, POSIX gets .sh. The other
    # flavour is dead weight on this machine and only confuses the Bash-allowlist matcher.
    want_ext = osutil.script_ext()
    return [
        (workflow_dir / name, content)
        for name, content in (
            ("run.ps1", run_ps1),
            ("run.sh", run_sh),
            ("inspect.ps1", inspect_ps1),
            ("inspect.sh", inspect_sh),
            ("check.ps1", check_ps1),
            ("check.sh", check_sh),
        )
        if name.rsplit(".", 1)[-1] == want_ext
    ]


def _read_script(path: Path) -> str | None:
    """Current on-disk text, or None when it is missing or unreadable.

    utf-8-sig strips a BOM if present and is harmless when absent, so every comparison
    against generated content is about the content, never about how a previous writer
    (or an editor, or PowerShell) chose to encode it.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _foreign_os_scripts(project_root: Path) -> list[Path]:
    """Entry scripts for the OTHER platform that are still sitting in .workflow/.

    Earlier builds wrote both flavours, so a workspace can carry a .sh that no generator
    has touched since. Nothing on this machine runs it, so nothing notices when it falls
    out of step — and a copy of the project handed to a colleague on Linux would run that
    stale file. Left for the caller to delete rather than silently repaired here.
    """
    want_ext = osutil.script_ext()
    other = "sh" if want_ext == "ps1" else "ps1"
    workflow_dir = project_root / WORKFLOW_DIRNAME
    return [
        path
        for path in (
            workflow_dir / f"{stem}.{other}" for stem in ("run", "inspect", "check")
        )
        if path.exists()
    ]


def script_drift(project_root: Path, main_py: str) -> list[dict]:
    """Scripts on disk that no longer match what the generator produces.

    Each entry is {'script', 'state'} with state 'missing', 'content_differs', or
    'foreign_os_leftover'. A drifted script keeps working right up until the CLI it calls
    changes shape underneath it — the on-disk run.sh routed `sweep` through `--job-command`
    for a whole release cycle after the generator stopped doing so, because nothing
    compared the two.
    """
    drifted: list[dict] = []
    for path, content in _build_run_scripts(project_root, main_py):
        current = _read_script(path)
        if current is None:
            drifted.append({"script": path.name, "state": "missing"})
        elif current != content:
            drifted.append({"script": path.name, "state": "content_differs"})
    drifted.extend(
        {"script": path.name, "state": "foreign_os_leftover"}
        for path in _foreign_os_scripts(project_root)
    )
    return drifted


def _generate_run_scripts(project_root: Path, main_py: str) -> list[str]:
    """Write the scripts _build_run_scripts composes; return the paths actually rewritten.

    Also deletes leftovers for the other platform: keeping a script no generator maintains
    is worse than not having one, because it looks usable.
    """
    written: list[str] = []
    for path in _foreign_os_scripts(project_root):
        try:
            path.unlink()
            written.append(f"removed {path}")
        except OSError:
            pass  # not ours to force; doctor keeps reporting it
    for path, content in _build_run_scripts(project_root, main_py):
        if _read_script(path) == content:
            continue
        if path.suffix == ".ps1":
            # UTF-8 BOM: Windows PowerShell 5.1 reads a no-BOM file as ANSI/Win-1252,
            # which corrupts any non-ASCII byte (em-dash, accented path) -> parse error.
            atomic_write_text(path, content, encoding="utf-8-sig")
        else:
            atomic_write_text(
                path, content
            )  # .sh stays plain UTF-8 (BOM breaks the shebang)
            osutil.make_executable(path)
        written.append(str(path))
    return written


def ensure_workflow_workspace(
    project_root: Path, agent_workflow_path: str | None
) -> dict:
    """Create STATIC scaffolding only. Per-session state/scope/cache/runtime are
    created lazily under sessions/<sid>/ on first delegated call (see load_workspace_state).
    """
    paths = workflow_paths(project_root)
    paths["workflow_dir"].mkdir(parents=True, exist_ok=True)
    paths["reports_dir"].mkdir(parents=True, exist_ok=True)
    # logs are per-session: created lazily under sessions/<sid>/logs on first delegated
    # call. Init only scaffolds the sessions/ root — no vestigial .workflow/logs.
    (paths["workflow_dir"] / "sessions").mkdir(parents=True, exist_ok=True)

    created_files: list[str] = []
    existing_files: list[str] = []

    status, _payload = ensure_valid_json_or_create(
        paths["config"], lambda: default_config(project_root, agent_workflow_path)
    )
    (created_files if status == "created" else existing_files).append(
        str(paths["config"])
    )

    if paths["gitignore"].exists():
        existing_files.append(str(paths["gitignore"]))
    else:
        atomic_write_text(paths["gitignore"], "*\n")
        created_files.append(str(paths["gitignore"]))

    tool = _tool_paths(agent_workflow_path)
    opencode_copied = _copy_provider_config(project_root, tool["tool_dir"])
    project_opencode = _install_project_opencode(project_root, tool["tool_dir"])
    generated_scripts = _generate_run_scripts(project_root, tool["main_py_path"])

    gitignore_updated = ensure_root_gitignore_entry(project_root)
    return {
        "project_root": str(project_root),
        "workflow_dir": str(paths["workflow_dir"]),
        "created_files": created_files,
        "existing_files": existing_files,
        "gitignore_updated": gitignore_updated,
        "provider_config": opencode_copied,
        "project_opencode": project_opencode,
        "generated_scripts": generated_scripts,
        "tool": tool,
    }


def ensure_root_gitignore_entry(project_root: Path) -> bool:
    gitignore_path = project_root / ".gitignore"
    desired = ".workflow/"
    if not gitignore_path.exists():
        atomic_write_text(gitignore_path, f"{desired}\n")
        return True

    content = gitignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    if desired in lines:
        return False
    suffix = "" if not content or content.endswith("\n") else "\n"
    atomic_write_text(gitignore_path, f"{content}{suffix}{desired}\n")
    return True


def load_workspace_state(project_root: Path, session_id: str | None = None) -> dict:
    """Load workspace state for a session, lazily creating per-session defaults.
    config.json is static (must exist from init); state/scope/cache are per-session."""
    paths = workflow_paths(project_root, session_id)
    paths["session_dir"].mkdir(parents=True, exist_ok=True)
    _, state = ensure_valid_json_or_create(
        paths["state"], lambda: default_state(project_root)
    )
    _, scope = ensure_valid_json_or_create(paths["scope"], default_scope)
    _, command_cache = ensure_valid_json_or_create(
        paths["command_cache"], default_command_cache
    )
    config, config_changed = merge_config_defaults(read_json_file(paths["config"]))
    if config_changed:
        atomic_write_json(paths["config"], config)
    return {
        "config": config,
        "state": state,
        "scope": scope,
        "command_cache": command_cache,
        "paths": paths,
    }


def reset_active_workflow_state(project_root: Path, session_id: str) -> dict:
    loaded = load_workspace_state(project_root, session_id)
    state = default_state(project_root)
    state["session"] = {"id": session_id, "bound_at": now_iso()}
    scope = default_scope()
    scope["session_id"] = session_id
    command_cache = default_command_cache()
    command_cache["session_id"] = session_id
    atomic_write_json(loaded["paths"]["state"], state)
    atomic_write_json(loaded["paths"]["scope"], scope)
    atomic_write_json(loaded["paths"]["command_cache"], command_cache)
    return {
        "state": state,
        "scope": scope,
        "command_cache": command_cache,
        "paths": loaded["paths"],
    }


def bind_session(project_root: Path, session_id: str) -> dict:
    """Bind a session to its OWN per-session state dir. Because state/scope/cache are
    keyed by session_id (sessions/<sid>/), two concurrent main agents on the same
    project never collide — so there is no cross-session mismatch and no reset. The
    session record is stamped on first bind."""
    loaded = load_workspace_state(project_root, session_id)
    state = loaded["state"]
    current = state.get("session") or {}
    current_id = current.get("id") if isinstance(current, dict) else None
    if current_id != session_id:
        state["session"] = {"id": session_id, "bound_at": now_iso()}
        atomic_write_json(loaded["paths"]["state"], state)
        scope = loaded["scope"]
        scope["session_id"] = session_id
        atomic_write_json(loaded["paths"]["scope"], scope)
        command_cache = loaded["command_cache"]
        command_cache["session_id"] = session_id
        atomic_write_json(loaded["paths"]["command_cache"], command_cache)
        loaded["state"] = state
        loaded["scope"] = scope
        loaded["command_cache"] = command_cache
    loaded["session_reset"] = False
    return loaded


def extract_lines_by_prefix(text: str, prefixes: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        value = line[1:].strip()
        for prefix in prefixes:
            if value.startswith(prefix):
                results.append(value[len(prefix) :].strip())
                break
    return [item for item in results if item]


_QUESTION_NUM = re.compile(r"^(\d+)\s*[.)]\s*(.+)$", re.DOTALL)
# ` | A) ... | B) ...` — enumerated answers on the same line as the question.
_QUESTION_OPT = re.compile(r"\s\|\s")
# ` label :: what it means` — the option's own explanation, split from its label.
_QUESTION_OPT_DESC = re.compile(r"\s::\s")


def parse_questions(text: str) -> dict:
    """Split the agent's questions into the two kinds that need different handling.

    `question:` blocks — the answer changes what gets built, so the user must see it.
    `uncertainty:` does not — it is closed by stating an assumption and carrying on.
    Both used to land in one `open_questions` list, which made every uncertainty look
    like it needed a decision and buried the ones that actually did.

    Numbering and ` | ` options are parsed out when present so a caller can render a real
    choice instead of a paragraph. Unnumbered lines still parse — older state files and
    any agent that ignores the format keep working, they just get positional ids.

    An option may carry its own explanation after ` :: `. A bare label tells the reader what
    to click, not what it costs them, and the renderers this feeds (Claude Code's
    AskUserQuestion among them) have a description slot that would otherwise sit empty.
    Options with no ` :: ` keep an empty description rather than changing shape, so callers
    never have to branch on which form they got.

    Returns {'open_questions': [...], 'resolvable_uncertainties': [...]} where each entry
    is {'id', 'text', 'options'} and each option is {'label', 'description'}.
    """

    def _collect(prefixes: tuple[str, ...]) -> list[dict]:
        out: list[dict] = []
        for raw in extract_lines_by_prefix(text, prefixes):
            body = raw
            number = None
            match = _QUESTION_NUM.match(body)
            if match:
                number = int(match.group(1))
                body = match.group(2).strip()
            parts = [p.strip() for p in _QUESTION_OPT.split(body) if p.strip()]
            question, raw_options = (parts[0], parts[1:]) if parts else (body, [])
            if not question:
                continue
            options = []
            for raw_option in raw_options:
                halves = _QUESTION_OPT_DESC.split(raw_option, 1)
                label = halves[0].strip()
                description = halves[1].strip() if len(halves) > 1 else ""
                options.append({"label": label, "description": description})
            out.append(
                {
                    "id": number if number is not None else len(out) + 1,
                    "text": question,
                    "options": options,
                }
            )
        return out

    return {
        "open_questions": _collect(("question:",)),
        "resolvable_uncertainties": _collect(("uncertainty:",)),
    }


def maybe_extract_plan_readiness(text: str) -> str:
    lowered = text.lower()
    if "ready" in lowered and "not ready" not in lowered:
        return "ready"
    if "partial" in lowered:
        return "partial"
    if "not ready" in lowered:
        return "not_ready"
    return "unknown"


def update_state_from_agent_output(
    project_root: Path, command: str, objective: str, content: str, session_id: str
) -> dict:
    loaded = bind_session(project_root, session_id=session_id)
    state = loaded["state"]
    state["workflow"]["stage"] = command
    state["workflow"]["last_command"] = command
    state["workflow"]["objective"] = objective
    state["workflow"]["plan_readiness"] = maybe_extract_plan_readiness(content)
    state["context"]["evidence_summary"] = [
        line.strip() for line in content.splitlines() if line.strip()
    ][:10]
    state["context"]["affected_files"] = extract_lines_by_prefix(
        content, ("file:", "path:")
    )
    state["context"]["affected_symbols"] = extract_lines_by_prefix(
        content, ("symbol:",)
    )
    state["context"]["assumptions"] = extract_lines_by_prefix(content, ("assumption:",))
    state["context"]["risks"] = extract_lines_by_prefix(content, ("risk:",))
    questions = parse_questions(content)
    state["context"]["open_questions"] = questions["open_questions"]
    state["context"]["resolvable_uncertainties"] = questions["resolvable_uncertainties"]
    state["guards"]["state_version"] = int(state["guards"].get("state_version", 0)) + 1
    atomic_write_json(loaded["paths"]["state"], state)
    return state


def update_plan_scope(project_root: Path, content: str, session_id: str) -> dict:
    loaded = load_workspace_state(project_root, session_id)
    scope = loaded["scope"]
    before = json.dumps(scope, sort_keys=True)
    scope["session_id"] = session_id
    scope["editable_scope"] = extract_lines_by_prefix(content, ("editable:",))
    scope["readable_scope"] = extract_lines_by_prefix(content, ("readable:",))
    scope["impact_radius"] = extract_lines_by_prefix(content, ("impact:",))
    scope["out_of_scope"] = extract_lines_by_prefix(content, ("out_of_scope:",))
    scope["verification_targets"] = extract_lines_by_prefix(content, ("verify:",))
    after = json.dumps(scope, sort_keys=True)

    state = loaded["state"]
    if before != after:
        state["guards"]["scope_version"] = (
            int(state["guards"].get("scope_version", 0)) + 1
        )
        atomic_write_json(loaded["paths"]["state"], state)
    atomic_write_json(loaded["paths"]["scope"], scope)
    return scope


def update_command_cache(project_root: Path, key: str, value, session_id: str) -> dict:
    loaded = load_workspace_state(project_root, session_id)
    command_cache = loaded["command_cache"]
    command_cache["session_id"] = session_id
    command_cache[key] = value
    atomic_write_json(loaded["paths"]["command_cache"], command_cache)
    return command_cache


def resolve_agent_workflow_path(project_root: Path) -> dict:
    paths = workflow_paths(project_root)
    config_path = paths["config"]
    candidates = []

    config_candidate = None
    if config_path.exists():
        try:
            config = read_json_file(config_path)
            runtime = config.get("runtime") if isinstance(config, dict) else None
            if isinstance(runtime, dict):
                config_candidate = runtime.get("agent_workflow_path")
        except ValueError:
            config_candidate = None
    if config_candidate:
        candidates.append({"source": "workflow_config", "path": config_candidate})

    env_candidate = os.getenv("AGENT_PATH")
    if env_candidate:
        candidates.append({"source": "env", "path": env_candidate})

    for candidate in candidates:
        path = Path(candidate["path"]).expanduser()
        if path.exists() and path.suffix == ".py":
            return {
                "ok": True,
                "path": str(path.resolve()),
                "source": candidate["source"],
                "candidates": candidates,
            }
    return {"ok": False, "path": None, "source": None, "candidates": candidates}


