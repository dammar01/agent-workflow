"""OpenCode-specific workspace installation.

Held apart from core/ on purpose: this is the only place that knows
the provider's config filenames and CLI name, so a second provider
gets a sibling module instead of edits scattered through the runtime.

`config/providers.py` names this module as opencode's `install_module`, so the two
functions the installer dispatches through — `load_config` and `merge_policy` — are the
provider-facing contract. Everything else here is opencode's own business."""

import json
import shutil
from pathlib import Path

from core.workspace_paths import (
    JSON_INDENT,
    PROVIDER_CONFIG_NAME,
    WORKFLOW_DIRNAME,
    atomic_write_json,
    atomic_write_text,
    read_json_file,
)

def load_config(path: Path):
    """Read one of this provider's config files. Tolerates JSONC, because opencode does."""
    from core.opencode_policy import load_json_or_jsonc

    return load_json_or_jsonc(path)


def merge_policy(current: dict, incoming: dict, warn) -> tuple[dict, int, int]:
    """Merge shipped config into an existing one, enforcing workflow-owned permissions.

    The installer reaches this by name, never by importing `core.opencode_policy`
    directly — that module encodes opencode's `agent.plan.permission` + root `permission`
    shape, which is one provider's answer rather than a contract every provider can meet.
    """
    from core.opencode_policy import merge_opencode_policy

    return merge_opencode_policy(current, incoming, warn)


def _copy_provider_config(project_root: Path, tool_dir: str) -> str | None:
    """Copy the tool's second_agent.json into .workflow so it is project-local and overridable."""
    dest = project_root / WORKFLOW_DIRNAME / PROVIDER_CONFIG_NAME
    if dest.exists():
        return str(dest)  # already project-local; never overwrite user edits
    src = Path(tool_dir) / "config" / PROVIDER_CONFIG_NAME
    if not src.exists():
        src = Path(tool_dir) / "config" / "second_agent.example.json"
    if src.exists():
        shutil.copyfile(src, dest)
        return str(dest)
    return None


def _install_project_opencode(project_root: Path, tool_dir: str) -> dict:
    """Install/refresh <project_root>/opencode.json — the secret-file boundary the
    second_agent runs under.

    Init owns this rather than install.py: the boundary belongs to a workspace, and a
    project scaffolded without it is exactly the gap doctor reports. Permissions are
    ENFORCED on every call (init and upgrade alike), so a boundary someone edited loose is
    repaired; every other key in the file stays the user's.
    """
    from config.providers import bundle_for

    bundle = bundle_for("opencode")
    src_name, dest_name = bundle["project_config"]
    src = Path(tool_dir) / "dist" / "config" / "opencode" / src_name
    dest = project_root / dest_name
    result: dict = {
        "path": str(dest),
        "status": "source_missing",
        "keys_added": 0,
        "permissions_enforced": 0,
        "warnings": [],
    }
    if not src.exists():
        result["path"] = None
        return result

    warnings: list[str] = result["warnings"]
    incoming = json.loads(src.read_text(encoding="utf-8"))
    current: dict = {}
    if dest.exists():
        try:
            current = load_config(dest)
        except json.JSONDecodeError:
            warnings.append(
                f"{dest} is not valid JSON/JSONC — left untouched (fix or remove it, then rerun)"
            )
            result["status"] = "invalid_json"
            return result
        if not isinstance(current, dict):
            warnings.append(
                f"{dest} root is not a JSON object — left untouched (replace it with an object, then rerun)"
            )
            result["status"] = "invalid_root"
            return result

    merged, added, enforced = merge_policy(current, incoming, warnings.append)
    result["keys_added"] = added
    result["permissions_enforced"] = enforced
    if merged == current and enforced == 0:
        result["status"] = "unchanged"
        return result
    # Trailing newline to match what install.py writes for the global config: the two
    # produce the same bytes for the same content, so moving this writer does not show up
    # as drift.
    atomic_write_text(dest, json.dumps(merged, indent=JSON_INDENT) + "\n")
    result["status"] = "created" if not current else "merged"
    return result



def _merge_provider_config(project_root: Path, tool_dir: str) -> list[str]:
    """Backfill adapter keys the running build knows about into an existing
    .workflow/second_agent.json. Additive only — an existing value is the user's tuning.

    Without this, keys introduced by a later build (idle_stall_seconds, probe cadence)
    never reach a project that was initialized once and left alone.
    """
    from config.settings import default_provider_config

    dest = project_root / WORKFLOW_DIRNAME / PROVIDER_CONFIG_NAME
    if not dest.exists():
        copied = _copy_provider_config(project_root, tool_dir)
        return ["(created)"] if copied else []
    try:
        current = read_json_file(dest)
    except (OSError, ValueError):
        return []  # malformed user config: report nothing, never clobber it
    added = [key for key in default_provider_config() if key not in current]
    if not added:
        return []
    defaults = default_provider_config()
    current.update({key: defaults[key] for key in added})
    atomic_write_json(dest, current)
    return added


def provider_callable(command_name: str) -> tuple[bool, str]:
    # Presence check ONLY — do not invoke. `opencode --help` can resolve to a native
    # .exe shim that false-negatives ("not compatible with this Windows version"),
    # while `opencode.cmd run` (what the workflow actually uses) works fine.
    resolved = shutil.which(f"{command_name}.cmd") or shutil.which(command_name)
    if resolved:
        return True, resolved
    return False, f"{command_name} not found in PATH"


