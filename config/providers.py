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
will need its own equivalent rather than a shared abstraction over both.

Adding a provider means: a `dist/config/<name>/` folder, an entry here, and an adapter
registered in `adapters/registry.py`. No build-tool edits.
"""

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
