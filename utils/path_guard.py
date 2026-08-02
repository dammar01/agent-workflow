"""Preflight scope guard. Best-effort — opencode enforces the hard boundary.

Scans a task string for explicit references to sensitive files or paths
outside the project root, so obviously-unsafe requests fail early with a
structured reason instead of a silent opencode permission error.
"""
import re
from pathlib import Path

# Filename patterns specific enough to flag wherever they appear as a token.
SENSITIVE_PATTERNS = (
    r"\.env(\.|$|\b)",
    r"id_rsa",
    r"\.ssh\b",
    r"\.pem\b",
    r"\.key\b",
    r"\.aws\b",
    r"\.npmrc\b",
    r"passwd\b",
)

# Words that also occur in ordinary prose ("analyze the secret manager module",
# "add credential redaction"). Only meaningful inside a path-shaped token, so
# they are checked against those alone — matching them against free text blocks
# legitimate tasks that merely talk about credential handling.
SENSITIVE_NAME_PATTERNS = (
    r"secret",
    r"credential",
)

# Token that looks like a filesystem path (absolute win/posix or with a separator).
_PATH_TOKEN = re.compile(r"(?:[A-Za-z]:[\\/]|~|/)[^\s\"']*|[^\s\"']+[\\/][^\s\"']+")

# Whitespace-delimited word, and the shape that makes one look like a path.
_WORD = re.compile(r"[^\s\"'`]+")
_PATH_SHAPE = re.compile(r"[\\/]|^[A-Za-z]:|^~")


def _clean_token(token: str) -> str:
    """Trim surrounding punctuation without eating a leading dot (".env")."""
    return token.strip("\"'`,;:()[]{}<>").rstrip(".")


def validate_scope(task: str, project_root: str | Path) -> tuple[bool, list[str]]:
    """Return (ok, blocked_paths). ok=False if the task references sensitive
    files or paths resolving outside project_root."""
    blocked: list[str] = []
    text = task or ""
    root = Path(project_root).resolve()

    # Match patterns against tokens, never against free text: the blocked entry
    # must be the thing that looked like a file, not a window of prose around it.
    for raw in _WORD.findall(text):
        token = _clean_token(raw)
        if not token:
            continue
        patterns = SENSITIVE_PATTERNS
        if _PATH_SHAPE.search(token):
            patterns = SENSITIVE_PATTERNS + SENSITIVE_NAME_PATTERNS
        for pattern in patterns:
            if re.search(pattern, token, re.IGNORECASE):
                blocked.append(token)
                break

    for token in _PATH_TOKEN.findall(text):
        candidate = token.strip("\"'.,);")
        if candidate in {"/", "~"}:
            continue
        # Home-relative path (~/ or ~\) is outside project_root by definition —
        # flag it without resolving, so we never depend on the home env being set.
        if candidate[:2] in ("~/", "~\\"):
            blocked.append(candidate)
            continue
        # A "~" not followed by a separator (e.g. "~:1127" line-number shorthand)
        # is not a filesystem path — skip it. Never call expanduser(): on a worker
        # with no USERPROFILE/HOMEPATH it raises RuntimeError and crashes the run.
        if candidate.startswith("~"):
            continue
        try:
            resolved = Path(candidate)
            if resolved.is_absolute():
                resolved = resolved.resolve()
                if root not in resolved.parents and resolved != root:
                    blocked.append(candidate)
        except (OSError, ValueError):
            continue

    seen: set[str] = set()
    unique = [b for b in blocked if b and not (b in seen or seen.add(b))]
    return (len(unique) == 0, unique)
