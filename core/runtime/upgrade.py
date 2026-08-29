"""Workspace version reporting and the upgrade path between them."""

from adapters.contract.registry import provider_for
from adapters.install.opencode_install import _merge_provider_config
from core.workspace.workspace_paths import CONFIG_VERSION
from core.workspace.workspace_paths import WORKFLOW_DIRNAME
from core.workspace.workspace_paths import _tool_paths
from core.workspace.workspace_paths import atomic_write_json
from core.workspace.workspace_paths import atomic_write_text
from core.workspace.workspace_paths import detect_project_root
from core.workspace.workspace_paths import read_json_file
from core.workspace.workspace_paths import workflow_paths
from pathlib import Path
import json
import os
from core.runtime.config_defaults import default_config, diverged_defaults, merge_config_defaults
from core.runtime.scripts import _generate_run_scripts


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
    # All three are compared, not two. The runtime version was reported by
    # `workspace_versions` and then ignored here, which is harmless only for as long as
    # COMPONENT_VERSIONS["runtime"] keeps deriving from TOOL_VERSION — the moment the
    # runtime is versioned on its own, a runtime-only bump would ship to workspaces that
    # never learned they were stale. Comparing it now costs nothing and closes that.
    return (
        versions["installed_tool_version"] != versions["current_tool_version"]
        or versions["installed_config_version"] != versions["current_config_version"]
        or versions["installed_runtime_version"] != versions["current_runtime_version"]
    )

def _install_project_boundary(project_root: Path, tool_dir: str) -> dict:
    """Install the second_agent's project boundary through whichever provider owns it.

    Dispatched rather than called directly: the boundary is a provider-shaped file
    (opencode's is <project_root>/opencode.json), so hardcoding one provider's installer
    here would scaffold an opencode boundary into a workspace configured for something
    else. A provider with no bundle or no installer is reported, not guessed at — a
    boundary that silently did not install is the gap doctor exists to catch.

    The provider config is resolved and passed in the same way `main.py` resolves it
    before building an adapter. Reading only the workspace config.json here would let a
    project whose second_agent.json names another provider RUN that provider while being
    installed opencode's boundary — the exact mismatch this dispatch exists to prevent.
    """
    from config.providers import PROVIDER_BUNDLES, provider_install_module
    from config.settings import load_provider_config_for

    provider = provider_for(project_root, load_provider_config_for(project_root))
    skipped = {
        "path": None,
        "status": "provider_unsupported",
        "keys_added": 0,
        "permissions_enforced": 0,
        "warnings": [
            f"provider '{provider}' ships no project boundary installer; "
            f"<project_root> was left untouched"
        ],
    }
    if provider not in PROVIDER_BUNDLES:
        return skipped
    module = provider_install_module(provider)
    installer = getattr(module, "install_project_config", None)
    if installer is None:
        return skipped
    return installer(project_root, tool_dir)

def upgrade_workflow_workspace(
    project_root: Path,
    agent_workflow_path: str | None,
    _capacity_guarded: bool = False,
) -> dict:
    """Bring an existing .workflow/ up to the running build.

    Run on demand, and also by ensure_workflow_workspace when init lands on a workspace
    stamped by an older build — leaving that to the user meant every later fix reached
    only the workspaces whose owner remembered a second command.

    Regenerates the derived parts (run/inspect/check scripts, config defaults, adapter
    config keys) and leaves everything owned by the user or by a live flow alone —
    sessions/ above all: a job may be running against it right now, and rewriting its
    state mid-flight would lose the very evidence the caller is waiting for.
    """
    if not _capacity_guarded:
        from core.jobs.job_manager import JobManager

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

    # Directories init scaffolds, re-made here because upgrade is what people run on a
    # workspace that has been lived in. `reports/` and `sessions/` were created by init and
    # by nothing else, so a workspace that lost one — cleaned by hand, restored from a
    # partial backup, checked out without empty dirs — stayed broken through every upgrade
    # and only recovered by running init again. mkdir with exist_ok is free when they exist.
    restored_dirs = [
        str(directory)
        for directory in (paths["reports_dir"], paths["workflow_dir"] / "sessions")
        if not directory.exists()
    ]
    paths["reports_dir"].mkdir(parents=True, exist_ok=True)
    (paths["workflow_dir"] / "sessions").mkdir(parents=True, exist_ok=True)

    # Ahead of every read below: v3.4.3 renamed the provider keys with no read-side
    # alias, so a v3.4.2 workspace must be translated before anything interprets it.
    # Idempotent, so a workspace already on the new names passes straight through.
    from core.workspace.provider_migration import migrate_provider_keys

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
    project_boundary = _install_project_boundary(project_root, tool["tool_dir"])

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
        "project_opencode": project_boundary,
        "diverged_from_defaults": diverged_defaults(config),
        "regenerated_scripts": scripts,
        "gitignore_updated": gitignore_updated,
        "restored_dirs": restored_dirs,
        "preserved": [str(paths["workflow_dir"] / "sessions")],
        "tool": tool,
    }

def active_jobs_for_workspace(project_root: Path) -> list[dict]:
    """Pending/running jobs whose work_dir is this project. [] on any failure.

    Best-effort by design: this exists to stop an upgrade from landing under a live
    call, and a job store it cannot read is not a reason to block one.
    """
    try:
        from core.jobs.job_manager import DEAD, JobManager

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
