from config.roles import VALID_ROLES
from config.routing import COMMAND_ROUTES
from config.settings import load_opencode_config


class Router:
    """Role is code-authoritative (config/routing.py); opencode.json only tunes model.

    So opencode.json needs no `role` key, and local commands (e.g. execute) are
    absent from COMMAND_ROUTES and correctly reject delegation.
    """

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_opencode_config()

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
        return {
            "command": normalized,
            "role": role,
            "model": model,
            "opencode_command": self.config.get("opencode_command", "opencode"),
            "timeout_seconds": self.config.get("timeout_seconds", 300),
        }
