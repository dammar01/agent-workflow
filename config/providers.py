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
        # What `provider_command` / `provider_agent` default to for THIS provider, and the
        # env vars that override them. Declared per bundle because the defaults used to be
        # two module constants in config/settings.py: selecting codex still handed you
        # opencode's binary and opencode's `plan` persona, silently.
        "default_command": "opencode",
        "command_env": "OPENCODE_COMMAND",
        # `plan` is opencode's own read-only primary; the workflow adds no second primary.
        "default_agent": "plan",
        "agent_env": "AI_PROXY_OPENCODE_AGENT",
        # Provider policy lives with the provider (see the module docstring). The module
        # answers `load_config(path)`, `merge_policy(current, incoming, warn)`, and
        # `install_project_config(project_root, tool_dir)`; how it enforces anything is
        # its own business.
        "install_module": "adapters.opencode_install",
    },
    "codex": {
        "home_dir": ".codex",
        "instructions": ("AGENTS.md", "AGENTS.md"),
        # No subagent roster. OpenCode's `wf-*` files are per-agent markdown it loads from
        # a directory; codex's subagent configuration has not been verified, so nothing is
        # shipped rather than a guessed roster that would never load.
        "agents_dir": None,
        # Declared, but deliberately NOT shipped in dist/config/codex/. Codex's config is
        # TOML and every value in it is overridable per invocation (`-c key=value`, `-s`),
        # so the adapter asserts the read-only boundary on every call instead of merging a
        # file once — see adapters/codex_install.py. The keys stay because install.py and
        # installer/check.py index them directly; both guard the FILE with .exists(), so an
        # unshipped template is a supported "this provider has none".
        "global_config": ("codex.template.toml", "config.toml"),
        # A placeholder, and knowingly so. Codex loads NO project-root config layer — an
        # unknown key planted in a project's `.codex/config.toml` passes `--strict-config`
        # untouched, which is only possible if the file is never read. So there is no
        # project boundary file to ship and never will be one; the read boundary is argv,
        # asserted per call from core/secret_patterns.py.
        #
        # The names stay because `installer/check.py` indexes this key positionally for
        # every bundle, and it guards the FILE with .exists() rather than the key. Dropping
        # the tuple would turn "this provider has no project file" into a KeyError.
        "project_config": ("codex.project.json", "codex.project.json"),
        "config_aliases": (),
        # What codex actually reads. The MCP scanner parses JSON only, so a server declared
        # in this TOML is skipped rather than classified — listed anyway so the limitation
        # is visible here instead of looking like codex declares nothing.
        "config_candidates": ("config.toml",),
        "default_command": "codex",
        "command_env": "CODEX_COMMAND",
        # Codex selects no named persona; `exec` runs the model directly.
        "default_agent": None,
        "agent_env": None,
        "install_module": "adapters.codex_install",
    },
}


def provider_command_default(provider: str, getenv) -> str:
    """The CLI name this provider is invoked through, env override applied."""
    bundle = bundle_for(provider)
    fallback = bundle.get("default_command") or provider
    name = bundle.get("command_env")
    return getenv(name, fallback) if name else fallback


def provider_agent_default(provider: str, getenv) -> str | None:
    """The provider-side persona delegated calls run as, or None where there is none."""
    bundle = bundle_for(provider)
    fallback = bundle.get("default_agent")
    name = bundle.get("agent_env")
    return getenv(name, fallback) if name else fallback


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
