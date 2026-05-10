import re


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


def extract_opencode_session_id(text: str) -> str | None:
    match = re.search(r"(?:session\.id=|service=session\s+id=)(ses_[A-Za-z0-9]+)", ensure_text(text))
    return match.group(1) if match else None


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
