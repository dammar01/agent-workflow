"""Codex-specific workspace installation.

The three functions `config/providers.py` dispatches through — `load_config`,
`merge_policy`, `install_project_config` — are the provider-facing contract. Everything
else here is codex's own business.

Codex's answer to those three is mostly "not applicable", and that is a design choice
rather than a stub:

* Codex keeps its config in `~/.codex/config.toml`. Every value in it can be overridden
  per invocation with `-c key=value`, and the sandbox has its own `-s` flag, so
  `adapters/codex_adapter.py` asserts the read-only boundary on EVERY call instead of
  writing it into a file once. A merged config file can be edited afterwards and nothing
  would notice; an argv flag cannot. There is therefore no global config to merge, and
  the bundle ships no template for one.
* Codex has no project-root config LAYER at all, so there is no project file to install
  either. Its permission model is real (`[permissions.<profile>.filesystem]`) but reachable
  only through the user's `config.toml` or a `-c` override, so the read boundary follows the
  same route as the sandbox: asserted on every call. `install_project_config` reports that
  as `enforced_per_call` and counts the patterns, rather than reporting a path that does not
  exist or a gap that is no longer there.
"""

from pathlib import Path

from core.secret_patterns import (
    CODEX_PERMISSION_PROFILE,
    codex_filesystem_permissions,
)
from core.workspace_paths import read_json_file


def load_config(path: Path):
    """Read a JSON config file this provider manages.

    Plain JSON only. Codex's own `config.toml` is deliberately outside what the installer
    manages (see the module docstring), so nothing routed here is ever TOML.
    """
    return read_json_file(path)


def merge_policy(current: dict, incoming: dict, warn) -> tuple[dict, int, int]:
    """Additive merge. Returns (merged, keys_added, permissions_enforced).

    `permissions_enforced` is always 0: codex's permissions are argv flags on every call,
    not file contents, so there is nothing in a config file for this layer to repair. An
    existing value is always the user's — this only backfills keys that are absent.
    """
    merged = dict(current or {})
    added = 0
    for key, value in (incoming or {}).items():
        if key not in merged:
            merged[key] = value
            added += 1
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            nested, nested_added, _ = merge_policy(merged[key], value, warn)
            merged[key] = nested
            added += nested_added
    return merged, added, 0


def install_project_config(project_root: Path, tool_dir: str) -> dict:
    """No file is installed, and the boundary still holds — reported as `enforced_per_call`.

    OpenCode's `<project_root>/opencode.json` denies reading `.env` and friends, and that
    denial is what makes its second agent safe to point at a repository holding secrets.
    Codex cannot be given the same file: a `.codex/config.toml` sitting in a project root is
    not part of any config layer codex loads, verified by planting an unknown key in one and
    watching `--strict-config` accept the run without complaint.

    What codex does honour is `[permissions.<profile>.filesystem]`, and every key of it can
    be set per invocation with `-c`. So the boundary is asserted on the argv of each call by
    `adapters/codex_adapter.py`, out of the same secret list opencode denies — see
    `core/secret_patterns.py`. That is strictly harder to drift out from under than a file,
    since there is no file for anyone to edit; the cost is that it protects THIS workflow's
    calls and not a codex the user runs by hand.

    `permissions_enforced` counts the patterns riding on each call, so a boundary that
    silently shrinks to nothing shows up as a zero in doctor rather than as a clean line.
    `path` stays None: nothing was written, and claiming a path would invite someone to go
    looking for it.
    """
    denies = codex_filesystem_permissions()
    warnings = [
        "codex has no project-root config layer, so its read boundary is asserted as "
        f"`-c permissions.{CODEX_PERMISSION_PROFILE}.filesystem` on every workflow "
        "call instead of installed as a file; a codex run started by hand outside this "
        "workflow carries none of it",
        "the boundary overrides `default_permissions` for the duration of a workflow "
        "call, so a permission profile configured in ~/.codex/config.toml is not the "
        "one in effect while the second agent runs",
    ]
    warnings.extend(_workspace_config_warnings(project_root))
    return {
        "path": None,
        "status": "enforced_per_call",
        "keys_added": 0,
        "permissions_enforced": len(denies),
        "warnings": warnings,
    }


def _workspace_config_warnings(project_root: Path) -> list[str]:
    """Settings this workspace carries that codex will not act on.

    Init is the moment a user is actually looking, so it is the moment worth saying it. A
    workspace switched over from opencode keeps every key it had — the backfill only adds
    what is missing — so `provider_agent: "plan"` and an `opencode/`-namespaced model sit
    there reading like configuration while codex ignores both.

    Nothing is rewritten here. A value the user set stays theirs; what changes is that they
    are told it is inert instead of discovering it by tuning a knob attached to nothing.
    """
    from config.settings import foreign_provider_values
    from core.workspace_paths import WORKFLOW_DIRNAME

    config_path = Path(project_root) / WORKFLOW_DIRNAME / "second_agent.json"
    try:
        config = read_json_file(config_path)
    except (OSError, ValueError):
        return []
    if not isinstance(config, dict):
        return []

    notes: list[str] = []
    inert_set = [key for key in inert_config_keys() if config.get(key) is not None]
    if inert_set:
        notes.append(
            "codex ignores these keys in second_agent.json: "
            + ", ".join(inert_set)
            + " (no persona to select, no bootstrap call to time, and the JSONL stream is "
            "read as it arrives rather than polled)"
        )
    notes.extend(
        f"{key} looks stale: {why}"
        for key, why in foreign_provider_values(config).items()
    )
    return notes


def inert_config_keys() -> tuple[str, ...]:
    """Keys `second_agent.json` accepts that mean nothing under codex.

    They are not errors and they are not removed — a workspace switched back to opencode
    needs them intact. They are reported so a user who tunes one and sees no effect learns
    why from doctor instead of from a long afternoon.

    * `provider_agent` — codex `exec` runs the model directly; it selects no named persona.
    * `bootstrap_timeout_seconds` — there is no bootstrap call to time out. Codex hands over
      its thread id in the first event of the run itself.
    * `job_poll_interval_seconds` / `job_poll_timeout_seconds` — nothing is polled; the
      adapter reads a JSONL stream as it arrives.
    """
    return (
        "provider_agent",
        "bootstrap_timeout_seconds",
        "job_poll_interval_seconds",
        "job_poll_timeout_seconds",
    )
