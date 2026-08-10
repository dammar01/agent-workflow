from config.roles import VALID_ROLES
from config.routing import COMMAND_ROUTES
from config.settings import (
    DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    load_provider_config,
)


class Router:
    """Role is code-authoritative (config/routing.py); opencode.json only tunes model.

    So opencode.json needs no `role` key, and local commands (e.g. execute) are
    absent from COMMAND_ROUTES and correctly reject delegation.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_provider_config()

    def route(self, command: str, model_override: str | None = None) -> dict:
        normalized = command.strip().lower()
        base = COMMAND_ROUTES.get(normalized)
        if not base:
            raise ValueError(f"unsupported command: {command}")
        role = base.get("role")
        if role not in VALID_ROLES:
            raise ValueError(f"unsupported role for command {command}: {role}")
        cfg_route = self.config.get("routes", {}).get(normalized, {})
        model = model_override or cfg_route.get("model") or base.get("model") or self.config.get("default_model")
        # A per-route timeout wins over the global one, but only when it is set —
        # `null` in opencode.json means "inherit", never "no limit".
        timeout = cfg_route.get("timeout_seconds")
        if timeout is None:
            timeout = self.config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        return {
            "command": normalized,
            "role": role,
            "model": model,
            # No literal provider name as a fallback: `provider_command` is always present
            # (default_provider_config fills it for the SELECTED provider), and a hardcoded
            # one here would mean a config that somehow lost the key silently runs a
            # different provider's binary.
            "provider_command": self.config.get("provider_command"),
            # `agent` may legitimately be None — codex has no persona to select — so the
            # `or` chain must not fall through to opencode's `plan` for every provider.
            "provider_agent": cfg_route.get("agent") or self.config.get("provider_agent"),
            "timeout_seconds": timeout,
            "bootstrap_timeout_seconds": self.config.get(
                "bootstrap_timeout_seconds", DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS
            ),
            "poll_interval_seconds": self.config.get(
                "job_poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS
            ),
        }
