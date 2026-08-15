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

`models` and `effort_arg` are the same idea applied to selection rather than packaging:
what the provider will accept, declared once, so the picker and the adapter cannot
disagree about it.
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
        # How this provider spells reasoning effort on the command line. Rendered with
        # `{value}`; an empty/absent template means the provider takes none.
        "effort_arg": ("--variant", "{value}"),
        # A SHORTLIST, not a catalog. `opencode models` returns 137 entries and the
        # picker that consumes this renders four options, so a full mirror would be
        # unusable and stale at once. Refresh by hand from `opencode models`.
        #
        # `efforts` is per MODEL, not per provider: opencode's `--variant` values are
        # decided by whoever built the model, not by opencode. Offering Anthropic's
        # `max` on a Google model would build a command line the upstream rejects.
        #
        # Values copied from `reasoning_options[].values` in the models.dev catalog
        # opencode caches at ~/.cache/opencode/models.json — the same table the CLI
        # itself reads, so this is the provider's own answer rather than a reading of
        # its docs. 44 of its 89 models declare no effort knob at all; `()` is how one
        # of those is spelled here, and it is a statement, not a gap.
        "models": (
            {
                "id": "opencode/deepseek-v4-flash-free",
                "efforts": ("low", "high", "max"),
            },
            # `reasoning: true` but `reasoning_options: []` — it reasons, and exposes no
            # dial for how much. Passing --variant here is an error, not a no-op.
            {"id": "opencode/mimo-v2.5-free", "efforts": ()},
        ),
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
        # codex has no effort FLAG; every config key is overridable per invocation and
        # `-c` is declared global across subcommands, so the same knob rides argv.
        "effort_arg": ("-c", 'model_reasoning_effort="{value}"'),
        # Copied from the codex CLI's own `models_cache.json`: `slug` plus the `effort`
        # of each entry in `supported_reasoning_levels`. codex ships no `models`
        # subcommand, so unlike opencode there is no live list to fall back on.
        #
        # Only the models that cache marks `visibility: list` are here. It also carries
        # `gpt-5.6-sol-wm` and `codex-auto-review` as `visibility: hide` — internal
        # routes, offered to nobody, and putting them in a picker would invite selecting
        # one. Same model name can differ per provider: opencode's own `gpt-5.6-luna`
        # entry adds `none`, which codex's does not accept.
        "models": (
            {
                "id": "gpt-5.6-sol",
                "efforts": ("low", "medium", "high", "xhigh", "max", "ultra"),
            },
            {
                "id": "gpt-5.6-terra",
                "efforts": ("low", "medium", "high", "xhigh", "max", "ultra"),
            },
            {
                "id": "gpt-5.6-luna",
                "efforts": ("low", "medium", "high", "xhigh", "max"),
            },
            {"id": "gpt-5.5", "efforts": ("low", "medium", "high", "xhigh")},
            {"id": "gpt-5.4", "efforts": ("low", "medium", "high", "xhigh")},
            {"id": "gpt-5.4-mini", "efforts": ("low", "medium", "high", "xhigh")},
        ),
        "install_module": "adapters.codex_install",
    },
    "agy": {
        # agy keeps nothing under the user's home but `bin/` — no config directory, no
        # policy file, nothing to merge. The keys below are declared anyway because
        # `install.py` and `installer/check.py` index every bundle positionally and guard
        # the FILE with .exists(); dropping them turns "this provider ships none" into a
        # KeyError. The paths point at files that are deliberately absent.
        "home_dir": ".agy",
        "instructions": ("AGENTS.md", "AGENTS.md"),
        # No subagent roster: `agy agents` on a stock install prints an empty list, so
        # there is no persona to ship and none to select.
        "agents_dir": None,
        "global_config": ("agy.template.json", "agy.json"),
        # A placeholder, knowingly. agy has no project-root config layer to install a
        # boundary into — see the read-boundary note below.
        "project_config": ("agy.project.json", "agy.project.json"),
        "config_aliases": (),
        "config_candidates": (),
        "default_command": "agy",
        "command_env": "AGY_COMMAND",
        # `--agent` exists and lists nothing; selecting a persona that does not exist
        # would fail the call for no gain.
        "default_agent": None,
        "agent_env": None,
        # `--effort low|medium|high`, straight from `agy --help`.
        "effort_arg": ("--effort", "{value}"),
        # THE READ-ONLY BOUNDARY DOES NOT EXIST FOR THIS PROVIDER, and the flag names
        # actively suggest otherwise. Probed against the installed binary: `--sandbox` and
        # `--mode plan` both leave 56 tools enabled and `permission_mode: always-proceed`,
        # `write_to_file` and `run_command` among them. Removing
        # `--dangerously-skip-permissions` gives `request-review`, which refuses every
        # tool — writes AND reads — leaving a second_agent that cannot gather evidence.
        # So the adapter takes the permissive side and pairs it with `core/agy_guard.py`,
        # which diffs the working tree around each call. That DETECTS a write; it does not
        # stop one. `adapters/agy_install.py` reports `not_enforceable` with both counts at
        # zero and says all of this where a user installing a workspace will read it.
        #
        # The full list from `agy models` — eleven entries, short enough to mirror whole
        # rather than shortlist. Refresh by hand from that subcommand.
        #
        # `efforts` is empty for every one of them, and that is a statement about what has
        # been VERIFIED, not a claim that agy refuses the flag. `--effort` is global and
        # takes low|medium|high; whether a model whose name already ends in `-high` also
        # honours it has not been tested, and declaring efforts that turn out to be
        # rejected would build a command line the provider refuses. Empty means the picker
        # offers none until someone tests one.
        "models": (
            {"id": "gemini-3.6-flash-high", "efforts": ()},
            {"id": "gemini-3.6-flash-medium", "efforts": ()},
            {"id": "gemini-3.6-flash-low", "efforts": ()},
            {"id": "gemini-3.5-flash-high", "efforts": ()},
            {"id": "gemini-3.5-flash-medium", "efforts": ()},
            {"id": "gemini-3.5-flash-low", "efforts": ()},
            {"id": "gemini-3.1-pro-high", "efforts": ()},
            {"id": "gemini-3.1-pro-low", "efforts": ()},
            {"id": "claude-sonnet-4-6", "efforts": ()},
            {"id": "claude-opus-4-6-thinking", "efforts": ()},
            {"id": "gpt-oss-120b-medium", "efforts": ()},
        ),
        "install_module": "adapters.agy_install",
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


def provider_models(provider: str) -> tuple[dict, ...]:
    """The shortlist of models offered for `provider`, each with its own `efforts`."""
    return tuple(bundle_for(provider).get("models") or ())


def model_is_listed(provider: str, model: str | None) -> bool:
    """Is `model` on this provider's shortlist at all?

    The companion to `model_efforts`, and the reason both exist: an empty effort tuple
    means two opposite things depending on this answer. Listed with no efforts is a
    statement — this model takes none, so sending one is an error. Not listed is an
    absence of information, and the shortlist is a picker's menu rather than the set of
    models that exist, so a pin the menu never mentioned must still work.
    """
    if not model:
        return False
    return any(entry.get("id") == model for entry in provider_models(provider))


def model_efforts(provider: str, model: str | None) -> tuple[str, ...]:
    """Reasoning-effort values `model` accepts.

    () from an unlisted model means "unknown"; () from a listed one means "none". Ask
    `model_is_listed` to tell them apart — every caller that turns this into a refusal
    has to, because refusing on "unknown" would refuse working configurations.
    """
    if not model:
        return ()
    for entry in provider_models(provider):
        if entry.get("id") == model:
            return tuple(entry.get("efforts") or ())
    return ()


def effort_args(provider: str, effort: str | None) -> list[str]:
    """argv fragment carrying `effort` for `provider`; empty when unset or unsupported."""
    if not effort:
        return []
    try:
        template = bundle_for(provider).get("effort_arg")
    except ValueError:
        return []
    if not template:
        return []
    return [part.format(value=effort) for part in template]


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
