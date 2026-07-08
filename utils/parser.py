import re
from datetime import datetime, timezone


def generate_main_session_id() -> str:
    """Generate a timestamp-based session ID for the main agent session."""
    now = datetime.now(timezone.utc)
    return f"main_{now.strftime('%Y%m%d_%H%M%S')}"


def ensure_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def first_non_empty(*values) -> str:
    for value in values:
        text = ensure_text(value).strip()
        if text:
            return text
    return ""


_SESSION_ID_PATTERNS = (
    r"(?:session\.id=|service=session\s+id=)(ses_[A-Za-z0-9]+)",
    r"\bid=(ses_[A-Za-z0-9]+)",           # generic key=value log
    r"\b(ses_[A-Za-z0-9]{6,})\b",          # bare session token, last resort
)


def extract_opencode_session_id(text: str) -> str | None:
    body = ensure_text(text)
    for pattern in _SESSION_ID_PATTERNS:
        match = re.search(pattern, body)
        if match:
            return match.group(1)
    return None


def clean_opencode_output(text: str) -> str:
    lines = ensure_text(text).splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\s+\d{4}-\d{2}-\d{2}T", stripped):
            continue
        if re.match(r"^>\s+", stripped):
            continue
        kept.append(line)
    return "\n".join(kept).strip()
