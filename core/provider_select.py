"""Read and write the second_agent selection: provider, model, effort.

The picker that drives this lives in the main agent (`AskUserQuestion` has no subagent
form), so the model would otherwise be the thing editing `second_agent.json` by hand.
It is not: the model passes one string and every decision about whether that string is
writable happens here, in code that `checks/provider.py` can run.

Two modes, told apart by whether a payload was given:

    provider ""                       -> the catalog plus what is selected now
    provider "codex|gpt-5.6-luna|high" -> validate, then write

`|` separates the fields because opencode model ids contain `/`.

An apply writes FIVE keys together — provider, provider_command, provider_agent,
default_model, effort. Writing `provider` alone is exactly the failure
`config.settings.foreign_provider_values` exists to report: codex selected, opencode's
binary and opencode's `plan` persona still on disk, and a workspace describing something
that does not exist. So the write is whole, and the report runs afterwards as proof.
"""

import json
import os
from pathlib import Path

from config.providers import (
    bundled_providers,
    model_efforts,
    model_is_listed,
    provider_agent_default,
    provider_command_default,
    provider_models,
)
from config.settings import (
    DEFAULT_PROVIDER,
    foreign_provider_values,
    resolve_provider_config_for,
)
from core.workspace_paths import (
    JSON_INDENT,
    PROVIDER_CONFIG_NAME,
    atomic_write_text,
    read_json_file,
    resolve_provider_config,
    workflow_paths,
)
from utils import osutil

FIELD_SEPARATOR = "|"


def run(project_root: Path, payload: str | None = None) -> dict:
    """Entry point for the `provider` command."""
    if not (payload or "").strip():
        return _catalog(project_root)
    return _apply(project_root, str(payload))


# ------------------------------------------------------------------ read


def _catalog(project_root: Path) -> dict:
    resolved = resolve_provider_config_for(project_root)
    config = resolved["config"]

    providers = []
    for name in bundled_providers():
        command = provider_command_default(name, os.getenv)
        installed, detail = osutil.provider_callable(command)
        providers.append(
            {
                "name": name,
                "command": command,
                "installed": installed,
                # The resolved path when found, the reason when not. Both are worth
                # showing: a provider that is "not installed" because PATH points at a
                # different binary is a different problem from one never installed.
                "detail": detail,
                "models": [
                    {"id": entry["id"], "efforts": list(entry.get("efforts") or ())}
                    for entry in provider_models(name)
                ],
            }
        )

    return {
        "ok": True,
        "content": _catalog_summary(providers, config),
        "meta": {
            "providers": providers,
            "current": {
                "provider": config.get("provider"),
                "model": config.get("default_model"),
                "effort": config.get("effort"),
                "provider_command": config.get("provider_command"),
                "provider_agent": config.get("provider_agent"),
            },
            "source": resolved["source"],
            "path": resolved["path"],
            "warnings": resolved["warnings"],
            "error": resolved["error"],
        },
    }


def _catalog_summary(providers: list[dict], config: dict) -> str:
    installed = [entry["name"] for entry in providers if entry["installed"]]
    return (
        f"provider={config.get('provider')} "
        f"model={config.get('default_model')} "
        f"effort={config.get('effort')}; "
        f"installed: {', '.join(installed) or 'none'}"
    )


# ----------------------------------------------------------------- write


def _apply(project_root: Path, payload: str) -> dict:
    parts = [part.strip() for part in payload.split(FIELD_SEPARATOR)]
    if len(parts) > 3:
        return _refuse(
            f"expected at most 3 fields separated by {FIELD_SEPARATOR!r}, "
            f"got {len(parts)}",
            next_action=f"Use: provider{FIELD_SEPARATOR}model{FIELD_SEPARATOR}effort",
        )
    provider = parts[0]
    model = parts[1] if len(parts) > 1 and parts[1] else None
    effort = parts[2] if len(parts) > 2 and parts[2] else None

    known = bundled_providers()
    if provider not in known:
        return _refuse(
            f"unknown provider {provider!r}",
            next_action=f"Known providers: {', '.join(known)}",
        )

    warnings: list[str] = []
    on_menu = model_is_listed(provider, model)
    allowed = model_efforts(provider, model)

    if model and not on_menu and provider_models(provider):
        # Not an error. The shortlist is a menu, not the set of models that exist, and
        # pinning something off-menu is a supported choice — it just means the effort
        # below cannot be checked against anything.
        warnings.append(
            f"{model!r} is not in the shortlist for {provider}; "
            "its accepted effort values are unknown here"
        )

    if effort and on_menu and not allowed:
        # Listed with an empty effort set is a declaration, not a gap: this model takes
        # none. Refusing is the whole point of declaring it — the alternative is an argv
        # the upstream rejects, discovered a minute later inside a delegated run.
        return _refuse(
            f"{model} takes no reasoning effort",
            next_action=f"Select {model} with no effort, or pick a model that takes one.",
        )
    if effort and allowed and effort not in allowed:
        return _refuse(
            f"{effort!r} is not accepted by {model}",
            next_action=f"{model} takes: {', '.join(allowed)}",
        )
    if effort and not on_menu:
        warnings.append(
            f"effort {effort!r} was not verified: no accepted values are declared "
            f"for {model or 'the selected model'}"
        )

    command = provider_command_default(provider, os.getenv)
    agent = provider_agent_default(provider, os.getenv)

    path, _ = resolve_provider_config(project_root)
    if path is None:
        path = workflow_paths(Path(project_root))["workflow_dir"] / PROVIDER_CONFIG_NAME

    # The file on disk, NOT the defaults-merged view: merging first would write every
    # tool default into the workspace as though the user had chosen it, and the next
    # default that changes upstream would then be pinned to the old value here.
    existing: dict = {}
    if Path(path).exists():
        try:
            existing = read_json_file(Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _refuse(
                f"cannot read the existing config at {path}: {exc}",
                next_action="Fix or remove that file, then retry.",
            )

    updated = dict(existing)
    updated.update(
        {
            "provider": provider,
            "provider_command": command,
            "provider_agent": agent,
            "default_model": model,
            "effort": effort,
        }
    )
    try:
        atomic_write_text(
            Path(path), json.dumps(updated, indent=JSON_INDENT) + "\n"
        )
    except OSError as exc:
        return _refuse(
            f"cannot write {path}: {exc}",
            next_action="Check the workspace is writable, then retry.",
        )

    hint = _sync_config_hint(project_root, provider)

    # Proof rather than assertion: re-read through the normal resolution path and ask
    # the same function doctor asks whether the file now describes one provider.
    resolved = resolve_provider_config_for(project_root)
    foreign = foreign_provider_values(resolved["config"])
    warnings.extend(resolved["warnings"])

    return {
        "ok": True,
        "content": (
            f"second_agent set to {provider} "
            f"(model={model or 'provider default'}, effort={effort or 'provider default'})"
        ),
        "meta": {
            "path": str(path),
            "written": {
                "provider": provider,
                "provider_command": command,
                "provider_agent": agent,
                "default_model": model,
                "effort": effort,
            },
            "config_hint": hint,
            "foreign_values": foreign,
            "warnings": warnings,
        },
    }


def _sync_config_hint(project_root: Path, provider: str) -> dict:
    """Keep `runtime.second_agent` in config.json agreeing with the real selection.

    That key selects nothing (`adapters/registry.py` reads the provider config), but
    doctor prints it and users read it. Left behind, it is a second answer to "which
    provider is this workspace on" that contradicts the first.

    Best-effort: the selection is already written and valid, so a missing or unreadable
    config.json must not turn a successful apply into a failure.
    """
    path = workflow_paths(Path(project_root))["config"]
    if not path.exists():
        return {"updated": False, "reason": "config.json not found"}
    try:
        config = read_json_file(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"updated": False, "reason": f"unreadable: {exc}"}

    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
        config["runtime"] = runtime
    if runtime.get("second_agent") == provider:
        return {"updated": False, "reason": "already in step"}
    runtime["second_agent"] = provider
    try:
        atomic_write_text(path, json.dumps(config, indent=JSON_INDENT) + "\n")
    except OSError as exc:
        return {"updated": False, "reason": f"unwritable: {exc}"}
    return {"updated": True, "path": str(path)}


def _refuse(message: str, next_action: str) -> dict:
    """Nothing is written on any refusal — a half-applied selection is the one state
    this command must never leave behind."""
    from core.contract import make_error

    return make_error(
        "invalid_provider_selection",
        message,
        next_action=next_action,
        meta={"known_providers": list(bundled_providers()), "default": DEFAULT_PROVIDER},
    )
