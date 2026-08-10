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
* Codex has no project-root config with a permission model. OpenCode's boundary works
  because `opencode.json` denies reading secret files; codex expresses nothing equivalent
  that has been verified, so this module reports `not_applicable` rather than installing a
  file the provider would ignore. That is a real difference in coverage, not a formality —
  see the note in `install_project_config`.
"""

from pathlib import Path

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
    """No project-root boundary file for codex — reported, not silently skipped.

    OpenCode's `<project_root>/opencode.json` denies reading `.env` and friends, and that
    denial is what makes its second agent safe to point at a repository holding secrets.
    Codex's sandbox modes govern WRITES; the read-side equivalent would be its execpolicy
    `.rules` files, whose format this workflow has not verified. Shipping a guessed one
    would install a boundary that does not bind.

    So the honest state is: under codex, second_agent runs read-only but is NOT prevented
    from reading secret files in the project. Returned as `not_applicable` with the reason
    attached so doctor and init report the gap instead of printing a clean line.
    """
    return {
        "path": None,
        "status": "not_applicable",
        "keys_added": 0,
        "permissions_enforced": 0,
        "warnings": [
            "codex ships no project-root boundary file: its sandbox blocks writes, but "
            "reading secret files is not denied the way opencode.json denies it"
        ],
    }
