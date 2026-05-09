from config.roles import VALID_ROLES
from config.settings import load_opencode_config


class Router:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_opencode_config()

    def route(self, command: str, model_override: str | None = None) -> dict:
        normalized = command.strip().lower()
        routes = self.config.get("routes", {})
        if normalized not in routes:
            raise ValueError(f"unsupported command: {command}")
        route = dict(routes[normalized])
        role = route.get("role")
        if role not in VALID_ROLES:
            raise ValueError(f"unsupported role for command {command}: {role}")
        route["command"] = normalized
        route["model"] = model_override or route.get("model") or self.config.get("default_model")
        route["opencode_command"] = self.config.get("opencode_command", "opencode")
        route["timeout_seconds"] = self.config.get("timeout_seconds", 300)
        return route
