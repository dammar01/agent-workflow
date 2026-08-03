"""Preflight scope guard. Best-effort — opencode enforces the hard boundary.

Scans a task string for explicit references to sensitive files or paths
outside the project root, so obviously-unsafe requests fail early with a
structured reason instead of a silent opencode permission error.
"""
import hashlib
import re
from pathlib import Path

_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_HASHED_COMPONENT_SUFFIX = re.compile(r"--[0-9a-f]{12}$")


def safe_path_component(value: str) -> str:
    """Map an identifier to one portable, collision-resistant path component."""
    raw = str(value or "")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
    device_stem = raw.casefold().split(".", 1)[0]
    portable_plain = (
        cleaned == raw
        and cleaned not in {"", ".", ".."}
        and len(cleaned) <= 120
        and raw == raw.casefold()
        and not raw.endswith((".", " "))
        and device_stem not in _WINDOWS_RESERVED_NAMES
        and not _HASHED_COMPONENT_SUFFIX.search(raw)
    )
    if portable_plain:
        return cleaned
    stem = cleaned.strip(". ")[:96] or "session"
    if stem.casefold().split(".", 1)[0] in _WINDOWS_RESERVED_NAMES:
        stem = f"id_{stem}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{stem}--{digest}"

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
_BARE_FILE = re.compile(
    r"^[^\\/]+\.[A-Za-z0-9][A-Za-z0-9._-]*?"
    r"(?:(?::\d+(?:[-:]\d+)?)|(?:#L\d+(?:-L?\d+)?))?$",
    re.IGNORECASE,
)


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
        if _PATH_SHAPE.search(token) or _BARE_FILE.match(token):
            patterns = SENSITIVE_PATTERNS + SENSITIVE_NAME_PATTERNS
        for pattern in patterns:
            if re.search(pattern, token, re.IGNORECASE):
                blocked.append(token)
                break

    for token in _PATH_TOKEN.findall(text):
        candidate = _clean_token(token)
        if candidate in {"/", "~"}:
            continue
        if candidate[:2] in ("~/", "~\\"):
            blocked.append(candidate)
            continue
        if candidate.startswith("~"):
            continue
        try:
            path_text = re.sub(r":\d+(?:(?:-|:)\d+)?$", "", candidate)
            path = Path(path_text)
            resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
            if root not in resolved.parents and resolved != root:
                blocked.append(candidate)
        except (OSError, ValueError):
            continue

    seen: set[str] = set()
    unique = [b for b in blocked if b and not (b in seen or seen.add(b))]
    return (len(unique) == 0, unique)
