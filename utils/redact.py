"""Credential-shaped content: detection and redaction.

Shared on purpose. These patterns started life inside `tools/extract_config.py`, where
they guarded the maintainer's publish step — so the one path that never had them was the
runtime evidence path, the only one that reads arbitrary output from another agent every
single call.

Deliberately broad: a false positive costs one manual look, a false negative costs a
permanent leak — into an artifact, a fact store, and a transcript at once.
"""
import re

SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "openai-style key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "github token"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "github token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "slack token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}"), "bearer token"),
    (
        re.compile(
            r"(?i)\"?(api[_-]?key|secret|password|access[_-]?token)\"?\s*[:=]\s*\"[^\"]{12,}\""
        ),
        "inline credential",
    ),
]


def scan(text: str, label: str = "") -> list[str]:
    """Names of the credential shapes present. Empty when the text is clean."""
    prefix = f"{label}: " if label else ""
    return [f"{prefix}{why}" for pattern, why in SECRET_PATTERNS if pattern.search(text or "")]


def redact(text: str) -> tuple[str, list[dict]]:
    """Replace credential-shaped runs with a labelled marker.

    Returns (clean_text, hits) where each hit is {'kind', 'count'} — the KIND of thing
    found and how often, never the value. The whole point is that the value stops here;
    recording it in the audit trail would move the leak rather than close it.

    The marker keeps the surrounding evidence readable: a claim about where a token is
    configured is still useful once the token itself is gone.
    """
    if not text:
        return text or "", []
    hits: list[dict] = []
    out = text
    for pattern, why in SECRET_PATTERNS:
        out, count = pattern.subn(f"[REDACTED:{why}]", out)
        if count:
            hits.append({"kind": why, "count": count})
    return out, hits
