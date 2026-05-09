from config.routing import COMMAND_ROUTES


class Router:
    def route(self, command: str) -> tuple[str, ...]:
        normalized = command.strip().lower()
        if normalized not in COMMAND_ROUTES:
            raise ValueError(f"unsupported command: {command}")
        return COMMAND_ROUTES[normalized]
