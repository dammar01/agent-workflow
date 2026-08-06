"""What each second_agent provider ships, declared once.

Before v3.4.3 the answer was spelled out four times — `tools/gen_manifest.py` TARGETS,
`tools/extract_config.py` ALLOWLIST, `install.py` _targets, and the manifest itself —
each with the literal string "opencode" baked in. Adding a provider meant finding all
four and agreeing with yourself in every one.

A bundle entry is deliberately shallow: where the provider keeps its own config, which
files ship there, and which single file (if any) belongs at a project root rather than
in the user's home. Anything richer than that is provider policy, not packaging, and
lives with the adapter — `core/opencode_policy.py` is the example: enforcing OpenCode's
permission rules is not something a table of paths can express, and a second provider
will need its own equivalent rather than a shared abstraction over both. `install_module`
is how the installer reaches that equivalent: a name to dispatch through, not an attempt
to describe what it does.

Adding a provider means: a `dist/config/<name>/` folder, an entry here, an install module
answering `load_config`/`merge_policy`/`install_project_config`, and an adapter registered
in `adapters/registry.py`. No build-tool edits, no installer edits.
"""

import importlib
from pathlib import Path

PROVIDER_BUNDLES: dict[str, dict] = {
    "opencode": {
        # Where the provider reads its own config, relative to the user's home.
        "home_dir": ".config/opencode",
        # Instruction file for the delegated agent. dist name -> destination name.
        "instructions": ("AGENTS.md", "AGENTS.md"),
        # Subagent roster. Installed globally: the roster belongs to the workflow, so
        # every managed project reads the same set instead of copying it per worktree.
        "agents_dir": "agents",
        # The provider's global config. Merged, never overwritten.
        "global_config": ("opencode.template.json", "opencode.json"),
        # Secret-file boundary. Project-scoped on purpose — it must follow the projects
        # this workflow manages rather than rewrite how the user's other work behaves.
        "project_config": ("opencode.project.json", "opencode.json"),
        # Alternate spellings of `global_config[1]` that WIN when present. opencode reads
        # either from its config dir, and a user who keeps a .jsonc must not silently get
        # a second config file written beside it.
        "config_aliases": ("opencode.jsonc",),
        # Every file in `home_dir` worth reading for MCP declarations, in precedence
        # order. Wider than the merge target on purpose: the scan reads whatever the
        # provider might load, the merge writes exactly one file.
        "config_candidates": ("opencode.json", "opencode.jsonc", "config.json"),
        # Provider policy lives with the provider (see the module docstring). The module
        # answers `load_config(path)`, `merge_policy(current, incoming, warn)`, and
        # `install_project_config(project_root, tool_dir)`; how it enforces anything is
        # its own business.
        "install_module": "adapters.opencode_install",
    },
}


def bundle_for(provider: str) -> dict:
    try:
        return PROVIDER_BUNDLES[provider]
    except KeyError:
        raise ValueError(
            f"no dist bundle declared for provider '{provider}'; "
            f"known: {', '.join(sorted(PROVIDER_BUNDLES))}"
        ) from None


def bundled_providers() -> tuple[str, ...]:
    return tuple(sorted(PROVIDER_BUNDLES))


def provider_home(provider: str, home: Path) -> Path:
    """The provider's own config directory under `home`."""
    return home.joinpath(*bundle_for(provider)["home_dir"].split("/"))


def provider_config_path(provider: str, home: Path) -> Path:
    """The single config file to merge workflow policy into.

    An existing alias wins (opencode's common .jsonc default); otherwise the canonical
    name, which is also what gets created when nothing is there yet. Resolution stays
    here rather than in the installer so the check layer and the write layer cannot
    disagree about which file they are talking about.
    """
    bundle = bundle_for(provider)
    directory = provider_home(provider, home)
    for alias in bundle.get("config_aliases", ()):
        candidate = directory / alias
        if candidate.exists():
            return candidate
    return directory / bundle["global_config"][1]


def provider_config_candidates(provider: str, home: Path) -> list[Path]:
    """Every config file the provider might read, in precedence order."""
    directory = provider_home(provider, home)
    return [directory / name for name in bundle_for(provider)["config_candidates"]]


def provider_install_module(provider: str):
    """Import the module that owns this provider's install-time policy."""
    module = bundle_for(provider).get("install_module")
    if not module:
        raise ValueError(f"provider '{provider}' declares no install_module")
    return importlib.import_module(module)
