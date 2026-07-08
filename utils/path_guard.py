"""Preflight scope guard. Best-effort — opencode enforces the hard boundary.

Scans a task string for explicit references to sensitive files or paths
outside the project root, so obviously-unsafe requests fail early with a
structured reason instead of a silent opencode permission error.
"""
import re
from pathlib import Path

# Filename / substring patterns that should never be touched.
SENSITIVE_PATTERNS = (
    r"\.env(\.|$|\b)",
    r"id_rsa",
    r"\.ssh\b",
    r"\.pem\b",
    r"\.key\b",
    r"secret",
    r"credential",
    r"\.aws\b",
    r"\.npmrc\b",
    r"passwd\b",
)

# Token that looks like a filesystem path (absolute win/posix or with a separator).
_PATH_TOKEN = re.compile(r"(?:[A-Za-z]:[\\/]|~|/)[^\s\"']*|[^\s\"']+[\\/][^\s\"']+")


def validate_scope(task: str, project_root: str | Path) -> tuple[bool, list[str]]:
    """Return (ok, blocked_paths). ok=False if the task references sensitive
    files or paths resolving outside project_root."""
    blocked: list[str] = []
    text = task or ""
    root = Path(project_root).resolve()

    for pattern in SENSITIVE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            blocked.append(text[max(0, match.start() - 12): match.end() + 4].strip())

    for token in _PATH_TOKEN.findall(text):
        candidate = token.strip("\"'.,);")
        if candidate in {"/", "~"}:
            continue
        try:
            resolved = Path(candidate).expanduser()
            if resolved.is_absolute():
                resolved = resolved.resolve()
                if root not in resolved.parents and resolved != root:
                    blocked.append(candidate)
        except (OSError, ValueError):
            continue

    # De-dup, keep order.
    seen: set[str] = set()
    unique = [b for b in blocked if b and not (b in seen or seen.add(b))]
    return (len(unique) == 0, unique)
