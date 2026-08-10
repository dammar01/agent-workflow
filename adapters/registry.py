"""Name -> adapter resolution for second_agent.

Before v3.4.3 there was no selection step at all: `core/executor.py` named
`OpenCodeAdapter` in its own constructor, and config.json's `second_agent` key was
written but never read. This module is the missing half — the key is now the input to
a lookup, so adding a provider means registering a class here rather than editing the
executor.

Selection order, first hit wins:
  1. explicit `name` argument (tests, CLI overrides)
  2. `second_agent` in the workspace .workflow/config.json
  3. `provider` in the workspace .workflow/second_agent.json
  4. AI_PROXY_PROVIDER / the built-in default
"""

from pathlib import Path

from adapters.codex_adapter import CodexAdapter
from adapters.opencode_adapter import OpenCodeAdapter
from config.settings import DEFAULT_PROVIDER

_ADAPTERS: dict[str, type] = {
    OpenCodeAdapter.adapter: OpenCodeAdapter,
    CodexAdapter.adapter: CodexAdapter,
}


class UnknownProviderError(ValueError):
    """Raised for a configured provider with no registered adapter.

    Deliberately loud rather than falling back to the default: a typo in
    `second_agent` that silently ran OpenCode would look like the config took effect.
    """


def register(adapter_cls: type) -> type:
    """Register an adapter class under its own `adapter` name. Usable as a decorator."""
    name = getattr(adapter_cls, "adapter", None)
    if not name:
        raise ValueError(f"{adapter_cls!r} has no `adapter` name to register under")
    _ADAPTERS[name] = adapter_cls
    return adapter_cls


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


def provider_for(project_root=None, config: dict | None = None) -> str:
    """Resolve which provider a project uses, without building it."""
    if isinstance(config, dict):
        configured = config.get("provider")
        if configured:
            return str(configured)
    if project_root is not None:
        from core.workspace_paths import WORKFLOW_DIRNAME, read_json_file

        workspace = Path(project_root) / WORKFLOW_DIRNAME / "config.json"
        try:
            runtime = read_json_file(workspace).get("runtime") or {}
        except (OSError, ValueError):
            runtime = {}
        configured = runtime.get("second_agent")
        if configured:
            return str(configured)
    return DEFAULT_PROVIDER


def adapter_class(name: str) -> type:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise UnknownProviderError(
            f"unknown second_agent provider '{name}'; registered: "
            f"{', '.join(available_providers())}"
        ) from None


def resolve_adapter(
    name: str | None = None,
    project_root=None,
    config: dict | None = None,
    **kwargs,
):
    """Build the adapter this project's config selects."""
    resolved = name or provider_for(project_root, config)
    return adapter_class(resolved)(**kwargs)
