import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils import osutil


def _safe_component(value: str) -> str:
    """Filesystem-safe single path component for a session id."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


WORKFLOW_DIRNAME = ".workflow"
LOCK_TTL_SECONDS = 300
JSON_INDENT = 2
ARCHIVE_KEEP = 20
CONFIG_VERSION = "3.4.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: Path, content: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_text(path, json.dumps(payload, indent=JSON_INDENT))


def detect_project_root(work_dir: str | None = None) -> Path:
    start = Path(work_dir).resolve() if work_dir else Path.cwd().resolve()
    current = start

    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def slugify_project_name(name: str) -> str:
    safe = []
    for char in name.lower():
        safe.append(char if char.isalnum() else "-")
    slug = "".join(safe).strip("-")
    return slug or "project"


def workflow_paths(
    project_root: Path, session_id: str | None = None
) -> dict[str, Path]:
    """Resolve workflow paths. Mutable per-flow state (state/scope/cache/runtime)
    lives under sessions/<sid>/ so concurrent main agents on the SAME project never
    clobber each other; static config/logs/reports stay shared at the .workflow root.
    session_id=None → legacy root fallback (init scaffolding / no-session tooling)."""
    workflow_dir = project_root / WORKFLOW_DIRNAME
    reports_dir = workflow_dir / "reports"
    session_dir = (
        workflow_dir / "sessions" / _safe_component(session_id)
        if session_id
        else workflow_dir
    )
    runtime_dir = session_dir / "runtime"
    logs_dir = (session_dir / "logs") if session_id else (workflow_dir / "logs")
    sweep_report = (
        session_dir / "reports" / "sweep.last.md"
        if session_id
        else reports_dir / "sweep.last.md"
    )
    return {
        "project_root": project_root,
        "workflow_dir": workflow_dir,
        "config": workflow_dir / "config.json",
        "session_dir": session_dir,
        "state": session_dir / "state.json",
        "scope": session_dir / "scope.json",
        "command_cache": session_dir / "command-cache.json",
        "gitignore": workflow_dir / ".gitignore",
        "runtime_dir": runtime_dir,
        "prompt": runtime_dir / "prompt.txt",
        "prompt_meta": runtime_dir / "prompt.meta.json",
        "response_last": runtime_dir / "response.last.md",
        # Evidence sidecars: dynamic leads/facts the second agent reads for itself,
        # instead of them riding in the (8191-capped) command-line prompt.
        "leads": runtime_dir / "leads.json",
        "facts": runtime_dir / "facts.json",
        "lock": runtime_dir / "lock",
        "reports_dir": reports_dir,
        "doctor_report": reports_dir / "doctor.json",
        "sweep_report": sweep_report,
        "logs_dir": logs_dir,
    }


def _tool_paths(agent_workflow_path: str | None) -> dict:
    """Resolve absolute tool paths (main.py/check.py) so .workflow is self-contained."""
    from config.settings import CHECK_PY, COMPONENT_VERSIONS, MAIN_PY, TOOL_VERSION

    main_py = Path(agent_workflow_path).resolve() if agent_workflow_path else MAIN_PY
    tool_dir = main_py.parent
    check_py = tool_dir / "check.py"
    if not check_py.exists():
        check_py = CHECK_PY
    return {
        "main_py_path": str(main_py),
        "check_py_path": str(check_py),
        "tool_dir": str(tool_dir),
        "tool_version": TOOL_VERSION,
        "runtime_version": COMPONENT_VERSIONS["runtime"],
    }


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
        # 3 was that same build's default. A deliberate 3 is indistinguishable from it, so
        # this does overwrite one — accepted because the window was a single unreleased
        # build, and the effect is tuning (slower auto-promotion), not correctness.
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
    project_root: Path, agent_workflow_path: str | None
) -> dict:
    """Bring an existing .workflow/ up to the running build. Manual-run, never automatic.

    Regenerates the derived parts (run/inspect/check scripts, config defaults, adapter
    config keys) and leaves everything owned by the user or by a live flow alone —
    sessions/ above all: a job may be running against it right now, and rewriting its
    state mid-flight would lose the very evidence the caller is waiting for.
    """
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

    config_changed = False
    fresh_config = False
    main_py_changed = False
    try:
        config = read_json_file(paths["config"])
    except (OSError, ValueError):
        config = default_config(project_root, agent_workflow_path)
        config_changed = True
        fresh_config = True
    else:
        config, config_changed = merge_config_defaults(config)
        runtime = config.setdefault("runtime", {})
        # Scripts embed main_py_path; a repo move must regenerate them even when the
        # runtime component version did not bump.
        main_py_changed = runtime.get("main_py_path") != tool["main_py_path"]
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

    opencode_added = _merge_opencode_config(project_root, tool["tool_dir"])

    # Lazy runtime component (P0.7): the run/inspect/check scripts are a pure function of
    # main_py_path + platform + the generator's version. Regenerate only when one of those
    # could have moved — the runtime component bumped, the repo relocated, or the config was
    # created fresh — instead of rewriting identical files on every upgrade.
    runtime_bumped = before.get("installed_runtime_version") != tool["runtime_version"]
    if fresh_config or main_py_changed or runtime_bumped:
        scripts = _generate_run_scripts(project_root, tool["main_py_path"])
    else:
        scripts = []
    gitignore_updated = ensure_root_gitignore_entry(project_root)

    return {
        "project_root": str(project_root),
        "from": before,
        "to": workspace_versions(project_root),
        "config_updated": config_changed,
        "opencode_keys_added": opencode_added,
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
            if job.get("status") not in {"pending", "running"}:
                continue
            work_dir = job.get("work_dir")
            if work_dir and str(Path(work_dir).resolve()) != target:
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
    """Learned opencode fan-out capability for this project (P1.6).

    True/False once a delegated run has revealed it; None while unprobed. Default fan-out
    stays ON — this only flips it OFF after opencode itself reports no spawn tool, so the
    prompt stops carrying a fan-out plan opencode cannot execute.
    """
    try:
        data = read_json_file(_capabilities_path(project_root))
    except (OSError, ValueError):
        return None
    value = data.get("subagent_fanout_capable") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else None


def set_fanout_capability(project_root: Path, capable: bool) -> None:
    """Persist the observed fan-out capability. No-op when unchanged (avoids churn)."""
    path = _capabilities_path(project_root)
    try:
        data = read_json_file(path)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if data.get("subagent_fanout_capable") == capable:
        return
    data["subagent_fanout_capable"] = capable
    data["subagent_fanout_capable_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def validate_config(config: dict) -> list[str]:
    """Structural sanity of config.json's user-tunable sections (P0.10).

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


def _merge_opencode_config(project_root: Path, tool_dir: str) -> list[str]:
    """Backfill adapter keys the running build knows about into an existing
    .workflow/opencode.json. Additive only — an existing value is the user's tuning.

    Without this, keys introduced by a later build (idle_stall_seconds, probe cadence)
    never reach a project that was initialized once and left alone.
    """
    from config.settings import default_opencode_config

    dest = project_root / WORKFLOW_DIRNAME / "opencode.json"
    if not dest.exists():
        copied = _copy_opencode_config(project_root, tool_dir)
        return ["(created)"] if copied else []
    try:
        current = read_json_file(dest)
    except (OSError, ValueError):
        return []  # malformed user config: report nothing, never clobber it
    added = [key for key in default_opencode_config() if key not in current]
    if not added:
        return []
    defaults = default_opencode_config()
    current.update({key: defaults[key] for key in added})
    atomic_write_json(dest, current)
    return added


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
            "second_agent": "opencode",
            "main_agent": "agnostic",
            "opencode_config": ".workflow/opencode.json",
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


def read_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object in {path}")
    return data


def ensure_valid_json_or_create(path: Path, factory) -> tuple[str, dict]:
    if path.exists():
        return "existing", read_json_file(path)
    payload = factory()
    atomic_write_json(path, payload)
    return "created", payload


def _copy_opencode_config(project_root: Path, tool_dir: str) -> str | None:
    """Copy the tool's opencode.json into .workflow so it is project-local and overridable."""
    dest = project_root / WORKFLOW_DIRNAME / "opencode.json"
    if dest.exists():
        return str(dest)  # already project-local; never overwrite user edits
    src = Path(tool_dir) / "config" / "opencode.json"
    if not src.exists():
        src = Path(tool_dir) / "config" / "opencode.example.json"
    if src.exists():
        shutil.copyfile(src, dest)
        return str(dest)
    return None


def _generate_run_scripts(project_root: Path, main_py: str) -> list[str]:
    """Emit run.ps1 + run.sh + inspect.ps1 + inspect.sh so main_agent calls one script.

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
    written: list[str] = []

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
        "$bg = @('explore','plan','analyze','verify','sweep')\n"
        "if ($bg -contains $Command) {\n"
        # Pre-flight gate: dispatching a delegated run satisfies the gate -> clear the marker
        # so the PreToolUse hook stops blocking gather tools for the rest of this turn.
        f'  $mk = Join-Path "{root}" ".workflow\\sessions\\$Session\\runtime\\delegated.marker"\n'
        "  if (Test-Path -LiteralPath $mk) { Remove-Item -LiteralPath $mk -Force -ErrorAction SilentlyContinue }\n"
        f'  & "{ps_py}" "{main_py}" --command await --job-command $Command '
        f'--prompt $Task --session $Session --work-dir "{root}" --pretty\n'
        "} else {\n"
        f'  & "{ps_py}" "{main_py}" --command $Command --work-dir "{root}" --pretty\n'
        "}\n"
    )
    run_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'COMMAND="${1:?command required}"\n'
        'TASK="${2:-}"\n'
        'SESSION="${3:-${MAIN_SESSION_ID:-default}}"\n'
        '[ "$SESSION" = "default" ] && echo "[workflow] WARN: session=default - pass MAIN_SESSION_ID for concurrent-safe isolation" >&2\n'
        f'[ "${{#TASK}}" -gt {DEFAULT_MAX_TASK_CHARS} ] && echo "[workflow] WARN: task is ${{#TASK}} chars > {DEFAULT_MAX_TASK_CHARS}-char cap; it WILL be truncated. Shorten the instruction rather than pre-splitting." >&2\n'
        'case " explore plan analyze verify sweep " in\n'
        '  *" $COMMAND "*)\n'
        # Pre-flight gate: clear the marker before dispatching (delegation satisfies the gate).
        f'    MK="{root}/.workflow/sessions/$SESSION/runtime/delegated.marker"\n'
        '    [ -f "$MK" ] && rm -f "$MK"\n'
        f'    exec "{sh_py}" "{main_py}" --command await --job-command "$COMMAND" '
        f'--prompt "$TASK" --session "$SESSION" --work-dir "{root}" --pretty ;;\n'
        "  *)\n"
        f'    exec "{sh_py}" "{main_py}" --command "$COMMAND" --work-dir "{root}" --pretty ;;\n'
        "esac\n"
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
    for name, content in (
        ("run.ps1", run_ps1),
        ("run.sh", run_sh),
        ("inspect.ps1", inspect_ps1),
        ("inspect.sh", inspect_sh),
        ("check.ps1", check_ps1),
        ("check.sh", check_sh),
    ):
        if name.rsplit(".", 1)[-1] != want_ext:
            continue
        path = workflow_dir / name
        if name.endswith(".ps1"):
            # UTF-8 BOM: Windows PowerShell 5.1 reads a no-BOM file as ANSI/Win-1252,
            # which corrupts any non-ASCII byte (em-dash, accented path) -> parse error.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8-sig")
            tmp.replace(path)
        else:
            atomic_write_text(
                path, content
            )  # .sh stays plain UTF-8 (BOM breaks the shebang)
        if name.endswith(".sh"):
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
    opencode_copied = _copy_opencode_config(project_root, tool["tool_dir"])
    generated_scripts = _generate_run_scripts(project_root, tool["main_py_path"])

    gitignore_updated = ensure_root_gitignore_entry(project_root)
    return {
        "project_root": str(project_root),
        "workflow_dir": str(paths["workflow_dir"]),
        "created_files": created_files,
        "existing_files": existing_files,
        "gitignore_updated": gitignore_updated,
        "opencode_config": opencode_copied,
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
    state["context"]["open_questions"] = extract_lines_by_prefix(
        content, ("question:", "uncertainty:")
    )
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


def acquire_runtime_lock(lock_path: Path, command: str, session_id: str) -> dict:
    if lock_path.exists():
        try:
            payload = read_json_file(lock_path)
        except ValueError:
            payload = None
        if payload:
            created_at = payload.get("created_at")
            try:
                created = datetime.fromisoformat(created_at)
            except (TypeError, ValueError):
                created = None
            if created and datetime.now(timezone.utc) - created <= timedelta(
                seconds=LOCK_TTL_SECONDS
            ):
                return {"ok": False, "stale": False, "payload": payload}
    stale_payload = None
    if lock_path.exists():
        try:
            stale_payload = read_json_file(lock_path)
        except ValueError:
            stale_payload = {"invalid": True}
    atomic_write_json(
        lock_path,
        {"command": command, "session_id": session_id, "created_at": now_iso()},
    )
    return {"ok": True, "stale": bool(stale_payload), "payload": stale_payload}


def release_runtime_lock(lock_path: Path, session_id: str | None = None) -> None:
    """Release the runtime lock. When `session_id` is given, only the OWNER may release:
    a lock held by a different session is left in place rather than yanked out from under
    its owner. Single-writer-per-session makes a cross-owner release rare, but the guard is
    cheap and turns a silent foot-gun into a no-op. A legacy lock with no session_id, an
    unreadable/absent lock, or a matching owner all release as before."""
    if session_id is not None:
        try:
            payload = read_json_file(lock_path)
        except (ValueError, FileNotFoundError, OSError):
            payload = None
        if isinstance(payload, dict) and payload.get("session_id") not in (
            None,
            session_id,
        ):
            return  # not our lock — leave it for its owner / the TTL stealer
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _prune_archive(logs_dir: Path, keep: int = ARCHIVE_KEEP) -> None:
    """Keep only the newest `keep` per-run archive folders."""
    if not logs_dir.exists():
        return
    runs = sorted(
        (p for p in logs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def _archive_prompt(logs_dir: Path, prompt_id: str, prompt: str) -> None:
    run_dir = logs_dir / prompt_id
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(run_dir / "prompt.md", prompt)
    atomic_write_text(
        run_dir / "prompt.sha256", hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    )
    _prune_archive(logs_dir)


def write_prompt_handoff(
    project_root: Path, command: str, session_id: str, prompt: str
) -> dict:
    loaded = load_workspace_state(project_root, session_id)
    loaded["paths"]["runtime_dir"].mkdir(parents=True, exist_ok=True)
    lock_result = acquire_runtime_lock(loaded["paths"]["lock"], command, session_id)
    if not lock_result["ok"]:
        holder = lock_result["payload"].get("session_id")
        return {
            "ok": False,
            "content": f"runtime lock active for session {holder}",
            "meta": {
                "error_type": "runtime_lock",
                "next_action": "Wait for the in-flight delegated call on this session to finish, then retry; if it is stuck, clear .workflow/sessions/<sid>/runtime/lock.",
                "lock": lock_result["payload"],
                "lock_path": str(loaded["paths"]["lock"]),
            },
        }

    state = loaded["state"]
    prompt_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{command}"
    meta = {
        "prompt_id": prompt_id,
        "session_id": session_id,
        "project_root": str(project_root),
        "command": command,
        "state_version": state.get("guards", {}).get("state_version", 1),
        "scope_version": state.get("guards", {}).get("scope_version", 0),
        "created_at": now_iso(),
        "status": "ready",
    }
    if lock_result["stale"]:
        meta["stale_lock_replaced"] = True

    prompt_tmp = loaded["paths"]["prompt"].with_suffix(".tmp")
    prompt_meta_tmp = loaded["paths"]["prompt_meta"].with_suffix(".tmp")
    prompt_tmp.write_text(prompt, encoding="utf-8")
    prompt_meta_tmp.write_text(json.dumps(meta, indent=JSON_INDENT), encoding="utf-8")
    prompt_tmp.replace(loaded["paths"]["prompt"])
    prompt_meta_tmp.replace(loaded["paths"]["prompt_meta"])

    _archive_prompt(loaded["paths"]["logs_dir"], prompt_id, prompt)

    state["guards"]["last_prompt_id"] = prompt_id
    atomic_write_json(loaded["paths"]["state"], state)
    return {"ok": True, "meta": meta, "paths": loaded["paths"]}


def write_evidence_sidecars(
    project_root: Path,
    session_id: str | None,
    graph_leads: dict | None,
    known_facts: list[str] | None,
) -> dict:
    """Persist the task-ranked leads and facts to runtime files for the second agent.

    These used to be injected into the command-line prompt; on Windows that prompt is
    one argv capped at 8191 chars, and an uncapped graph-lead list is what pushed real
    calls over it. The ranking is still computed here (main_agent's runtime), only the
    TRANSPORT moves: the second agent reads leads.json/facts.json itself, keeping the
    prompt focused on the task.

    Overwrites unconditionally every call — a stale file from a prior task must never
    be read as this task's leads. Returns the two paths that were written.
    """
    paths = workflow_paths(project_root, session_id)
    paths["runtime_dir"].mkdir(parents=True, exist_ok=True)
    # `null`/`[]` are meaningful: they say "computed, nothing relevant", which the
    # second agent must be able to tell apart from a leftover file.
    atomic_write_json(paths["leads"], graph_leads)
    atomic_write_json(paths["facts"], known_facts or [])
    return {"leads": str(paths["leads"]), "facts": str(paths["facts"])}


def write_response_snapshot(
    project_root: Path,
    content: str,
    prompt_id: str | None = None,
    session_id: str | None = None,
) -> None:
    paths = workflow_paths(project_root, session_id)
    atomic_write_text(paths["response_last"], content)
    if prompt_id:
        run_dir = paths["logs_dir"] / prompt_id
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(run_dir / "output.raw.md", content)


def write_call_meta(
    project_root: Path,
    prompt_id: str | None,
    session_id: str | None,
    meta: dict,
) -> None:
    """Archive one delegated call's raw outcome next to its prompt/output.

    Exit code, duration, whether it timed out, how it was killed, stderr tail —
    the ground truth needed to characterise real failure modes (rate limits,
    hangs, orphaned children) instead of guessing at them.
    """
    if not prompt_id:
        return
    try:
        paths = workflow_paths(project_root, session_id)
        run_dir = paths["logs_dir"] / prompt_id
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(meta or {})
        payload["recorded_at"] = now_iso()
        atomic_write_text(
            run_dir / "call.meta.json", json.dumps(payload, indent=JSON_INDENT)
        )
    except (OSError, TypeError, ValueError):
        pass  # instrumentation must never break the call it is measuring


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


def opencode_callable(command_name: str) -> tuple[bool, str]:
    # Presence check ONLY — do not invoke. `opencode --help` can resolve to a native
    # .exe shim that false-negatives ("not compatible with this Windows version"),
    # while `opencode.cmd run` (what the workflow actually uses) works fine.
    resolved = shutil.which(f"{command_name}.cmd") or shutil.which(command_name)
    if resolved:
        return True, resolved
    return False, f"{command_name} not found in PATH"


_MCP_SAFE = ("context7", "docs", "documentation", "read-only", "readonly", "search")
_MCP_RISK = (
    "shell",
    "exec",
    "bash",
    "run-command",
    "runcommand",
    "filesystem",
    "file-system",
    "write",
    "postgres",
    "mysql",
    "mongo",
    "sqlite",
    "git",
    "playwright",
    "puppeteer",
    "browser",
    "selenium",
    "kubernetes",
    "docker",
    "ssh",
)
# DB/data-inspection families that ARE permitted for second_agent (read-only evidence
# extended to DB). Matched by NAME so a scoped inspector like laravel-boost is not caught
# by the generic mysql/postgres RISK keywords. Permission is behavioral: the AGENTS.md
# contract restricts second_agent to read queries + forbids the write tools below — same
# way file-write is forbidden by prompt, not sandbox.
_MCP_INSPECT = ("laravel-boost", "laravel_boost", "laravelboost")
# Write/exec tools that force RISK even inside an inspect family: they break read-only.
_MCP_WRITE_TOOLS = ("tinker", "migrate", "seed", "db:wipe", "eval")


def _mcp_config_candidates(project_root: Path) -> list[Path]:
    try:
        home = Path.home()
    except RuntimeError:
        home = Path(
            os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(project_root)
        )
    oc = home / ".config" / "opencode"
    return [
        project_root / WORKFLOW_DIRNAME / "opencode.json",
        oc / "opencode.json",
        oc / "opencode.jsonc",
        oc / "config.json",
    ]


def _classify_mcp(name: str, spec) -> tuple[str, bool, str]:
    """Classify one MCP server for second_agent (read-only evidence) safety.

    Tiers: risk (write/exec — disable), inspect (read-only DB/data — PERMITTED), safe
    (docs/search), unknown (review). Order matters: a write TOOL forces risk even for an
    inspect family, and the inspect family is checked BEFORE the generic mysql/postgres
    RISK keywords so a scoped inspector is not mislabelled by a keyword its command names.
    """
    enabled = (
        bool(spec["enabled"]) if isinstance(spec, dict) and "enabled" in spec else True
    )
    payload = json.dumps(spec) if isinstance(spec, (dict, list)) else str(spec)
    blob = f"{name} {payload}".lower()
    # (1) A write/exec tool named in the spec breaks read-only regardless of family.
    wtool = next((k for k in _MCP_WRITE_TOOLS if k in blob), None)
    if wtool:
        return (
            "risk",
            enabled,
            f"exposes write/exec tool '{wtool}' — exceeds read-only role (disable it or the server)",
        )
    # (2) Named DB/data-inspection family — permitted, behavioral read-only contract.
    if any(k in name.lower() for k in _MCP_INSPECT):
        return (
            "inspect",
            enabled,
            "read-only DB/data inspection (laravel-boost family) — PERMITTED for second_agent; "
            "AGENTS.md contract limits it to read queries + forbids tinker/migrate/seed",
        )
    # (3) Generic write/exec/raw-DB keyword — still risk (an unscoped SQL MCP can write).
    risk = next((k for k in _MCP_RISK if k in blob), None)
    if risk:
        return (
            "risk",
            enabled,
            f"matches write/exec keyword '{risk}' — exceeds read-only second_agent role",
        )
    if any(k in blob for k in _MCP_SAFE):
        return "safe", enabled, "read-only (docs/search)"
    return "unknown", enabled, "capability unknown — review manually"


def _mcp_reachable(spec) -> tuple[bool | None, str]:
    """Light liveness for one MCP server: is its launch command resolvable on PATH?

    A real signal short of invoking the server (which spends quota and can hang): a local
    server whose command is missing can never answer, so doctor should say so. Remote
    servers and command-less specs are reported unprobed, never faked as reachable — an
    unrun check is not a pass.
    """
    if not isinstance(spec, dict):
        return None, "spec not an object — cannot resolve command"
    if spec.get("type") == "remote" or spec.get("url"):
        return None, "remote server (liveness not probed)"
    cmd = spec.get("command")
    if isinstance(cmd, list) and cmd:
        exe = str(cmd[0])
    elif isinstance(cmd, str) and cmd.strip():
        exe = cmd.split()[0]
    else:
        return None, "no launch command in spec — cannot probe"
    resolved = shutil.which(exe) or shutil.which(f"{exe}.cmd")
    if resolved:
        return True, resolved
    return False, f"'{exe}' not found on PATH — second_agent cannot start this server"


def _scan_mcp(project_root: Path) -> dict:
    """Enumerate MCP servers opencode exposes to second_agent + a safety verdict."""
    servers: list[dict] = []
    sources: list[str] = []
    seen: set[str] = set()
    for path in _mcp_config_candidates(project_root):
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            cleaned = re.sub(r"(?m)^\s*//.*$", "", raw)  # tolerate // comments (jsonc)
            data = json.loads(cleaned)
        except (OSError, ValueError):
            continue
        mcp = data.get("mcp") if isinstance(data, dict) else None
        if not isinstance(mcp, dict) or not mcp:
            continue
        sources.append(str(path))
        for name, spec in mcp.items():
            if name in seen:  # first config wins (project overrides global)
                continue
            seen.add(name)
            cls, enabled, reason = _classify_mcp(name, spec)
            reachable, reach_detail = _mcp_reachable(spec)
            servers.append(
                {
                    "name": name,
                    "enabled": enabled,
                    "classification": cls,
                    "reason": reason,
                    "reachable": reachable,
                    "reachable_detail": reach_detail,
                }
            )
    active = [s for s in servers if s["enabled"]]
    if not servers:
        verdict = "none"
    elif any(s["classification"] == "risk" for s in active):
        verdict = "risk"
    elif any(s["classification"] == "unknown" for s in active):
        verdict = "review"
    else:
        verdict = "safe"
    return {"sources": sources, "servers": servers, "verdict": verdict}


def _expand_home(template: str) -> str:
    return str(template).replace("{{HOME}}", os.path.expanduser("~"))


def _installed_path_for(rel: str, targets: dict) -> Path | None:
    """Map a manifest dist-relative path to its installed location via the targets map.

    A targets key is either an exact file (claude/CLAUDE.md) or a directory (claude/skills).
    The longest matching key wins, so a file under a mapped dir resolves to
    <target_dir>/<remainder>.
    """
    if rel in targets:
        return Path(_expand_home(str(targets[rel])))
    best_key = None
    for key in targets:
        if rel == key or rel.startswith(key + "/"):
            if best_key is None or len(key) > len(best_key):
                best_key = key
    if best_key is None:
        return None
    remainder = rel[len(best_key) :].lstrip("/")
    return Path(_expand_home(str(targets[best_key]))) / remainder


def _bundle_integrity(dist_config_dir: Path, manifest_path: Path) -> dict:
    """Verify the installed bundle matches dist/manifest.json (release-integrity).

    For each file the manifest records, hash the INSTALLED copy the same way gen_manifest
    does (sha256 of read_text().encode(), CRLF-normalised by universal newlines) and compare.
    Also flags a manifest older than its dist sources (stale) and any missing required hook.
    """
    result: dict = {
        "manifest": str(manifest_path),
        "checked": 0,
        "mismatched": [],
        "missing": [],
        "manifest_fresh": None,
        "hooks_installed": None,
    }
    try:
        manifest = read_json_file(manifest_path)
    except (OSError, ValueError):
        result["error"] = "manifest missing or invalid"
        return result
    files = manifest.get("files") if isinstance(manifest, dict) else None
    targets = manifest.get("targets") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not isinstance(targets, dict):
        result["error"] = "manifest has no files/targets"
        return result

    for entry in files:
        rel = entry.get("path")
        installed = _installed_path_for(rel, targets)
        if installed is not None and installed.name == "opencode.json":
            jsonc = installed.with_name("opencode.jsonc")
            if jsonc.is_file():
                installed = jsonc
        if installed is None or not installed.is_file():
            result["missing"].append(rel)
            continue
        # *.template.json is installed via {{HOME}} placeholder substitution + key-merge
        # into the user's settings.json/opencode.json, so its bytes legitimately diverge
        # from the shipped template. Verify presence only — an exact hash would false-alarm.
        if str(rel).endswith(".template.json"):
            result["checked"] += 1
            continue
        try:
            blob = installed.read_text(encoding="utf-8").encode("utf-8")
        except OSError:
            result["missing"].append(rel)
            continue
        result["checked"] += 1
        if hashlib.sha256(blob).hexdigest() != entry.get("sha256"):
            result["mismatched"].append(rel)

    try:
        newest_src = max(
            (p.stat().st_mtime for p in dist_config_dir.rglob("*") if p.is_file()),
            default=0.0,
        )
        result["manifest_fresh"] = manifest_path.stat().st_mtime >= newest_src
    except OSError:
        result["manifest_fresh"] = None

    required_hooks = [
        "session-bind.ps1",
        "intent-gate-set.ps1",
        "intent-gate-check.ps1",
    ]
    hook_target = _installed_path_for("claude/hooks", targets)
    if hook_target is not None:
        result["hooks_installed"] = all(
            (hook_target / h).is_file() for h in required_hooks
        )
    return result


def run_doctor(
    project_root: Path, opencode_command: str, session_id: str | None = None
) -> dict:
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

    opencode_ok, opencode_output = opencode_callable(opencode_command)
    checks["opencode_callable"] = {"ok": opencode_ok, "output": opencode_output}
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
                opencode_id = read_json_file(session_file).get("opencode_session_id")
            except (ValueError, OSError):
                opencode_id = None
            if opencode_id:
                checks["session_continuation"] = (
                    f"linked: {session_id} -> {opencode_id}"
                )
            else:
                checks["session_continuation"] = (
                    f"BROKEN: {session_id} has no opencode_session_id — continuation re-bootstraps each call"
                )
                issues.append(
                    "session continuation broken: opencode_session_id not captured for active session"
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
            repo_root / "dist" / "config", repo_root / "dist" / "manifest.json"
        )
    checks["bundle_integrity"] = integrity
    if isinstance(integrity, dict):
        if integrity.get("error"):
            issues.append(f"bundle integrity uncheckable: {integrity['error']}")
            recommended_fixes.append(
                "Regenerate the manifest: python tools/gen_manifest.py"
            )
        if integrity.get("mismatched"):
            issues.append(
                f"bundle drift: {len(integrity['mismatched'])} installed file(s) differ from manifest "
                f"({', '.join(integrity['mismatched'][:5])})"
            )
            recommended_fixes.append(
                "Re-run install/upgrade to reinstall the shipped bundle; if dist/ changed on "
                "purpose, regenerate the manifest (python tools/gen_manifest.py)"
            )
        if integrity.get("missing"):
            issues.append(
                f"bundle incomplete: {len(integrity['missing'])} manifest file(s) not installed "
                f"({', '.join(integrity['missing'][:5])})"
            )
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
    payload = {
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
    paths = workflow_paths(project_root, session_id)
    changed_files: list[str] = []
    diff_summary = ""
    try:
        names = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            **osutil.hidden_run_kwargs(),  # Windows: no console flash
        )
        changed_files = [
            line.strip() for line in names.stdout.splitlines() if line.strip()
        ]
        summary = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            **osutil.hidden_run_kwargs(),  # Windows: no console flash
        )
        diff_summary = summary.stdout.strip()
    except OSError as exc:
        return {
            "ok": False,
            "content": str(exc),
            "meta": {"error_type": type(exc).__name__},
        }

    loaded = load_workspace_state(project_root, session_id)
    scope = loaded["scope"]
    impact_radius = scope.get("impact_radius") or []
    risk_hits = []
    for file_path in changed_files:
        lower = file_path.lower()
        if any(
            token in lower
            for token in ("config", "auth", "payment", "schema", "migration")
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
        "content": f"sweep {verdict}: {reason}",
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
    if not sessions_dir.exists():
        return {"removed": 0, "kept": 0}
    dirs = sorted(
        (p for p in sessions_dir.iterdir() if p.is_dir()),
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
    return {"removed": removed, "kept": min(len(dirs), keep_last)}
