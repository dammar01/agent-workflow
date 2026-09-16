"""Redaction for everything the e2e stages write down.

Three rules, in order of how often they matter:
  1. the resolved scenario (placeholders replaced by real values) is handed to the
     player's stdin/argv only and never to disk, a prompt, or a result;
  2. every artifact this package writes (spec, events, report, evidence block) goes
     through the shared secret scanner first, the same one delegated output goes
     through, so a value that leaks through a page title or an error message is
     caught by the same patterns everywhere;
  3. input values recorded by the player are dropped, not redacted — a `fill` step's
     evidence is that it ran, never what it typed;
  4. a resolved value is scrubbed in every form a page echoes it in (raw, URL-encoded,
     HTML- and JSON-escaped), in events and in player-written text files alike. Binary
     files cannot be scrubbed, so the player does not write them once values resolved.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote, urlsplit

from core.workspace.workspace_paths import atomic_write_text
from utils.redact import redact_value

_VALUE_KEYS = frozenset({"value", "password", "secret", "token"})
# Shorter resolved values are not scrubbed by substring: replacing every "ab" in a
# report would corrupt URLs and selectors while protecting nothing a 2-character
# credential could not already be guessed from.
MIN_SCRUB_CHARS = 4
# Every text form a player could leave behind, not only the HTML it writes today: a file
# this list misses is stored with whatever resolved value it holds. Binaries (PNG, ZIP)
# stay out — a byte-level replace would corrupt them, so secret runs never capture them.
TEXT_ARTIFACT_SUFFIXES = (".html", ".htm", ".txt", ".log", ".json", ".jsonl", ".md")


# A path segment that is an identifier, not a route: a number, a UUID, a long hex or token
# string, a JWT, a mixed-case token with digits, or anything with an `@` (an address used
# as a key). A reset link's token lives exactly here, so the request ledger keeps the route
# and never the value. The JWT and mixed-case forms were missing: `eyJ....` carries dots,
# and a 16–23 character invite code slipped under the 24-character floor.
_ID_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{16,}|[A-Za-z0-9_\-]{24,}"
    r"|[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
    r"|(?=[^/]*[a-z])(?=[^/]*[A-Z])(?=[^/]*\d)[A-Za-z0-9_\-]{16,}"
    r"|.*@.*)$"
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def sanitize_endpoint(url: str) -> str:
    """`scheme://host[:port]/route` for the request ledger: query, fragment and credentials in
    the authority dropped, identifier-shaped path segments replaced by `:id`."""
    try:
        parts = urlsplit(str(url))
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "[unparseable url]"
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    segments = [":id" if segment and _ID_SEGMENT.match(unquote(segment)) else segment for segment in (parts.path or "/").split("/")]
    return f"{scheme}://{netloc}{'/'.join(segments) or '/'}"


def _variants(value: str) -> set[str]:
    """The forms a page is likely to echo a typed value back in: raw, URL-encoded (path,
    component and form style, either hex case), HTML-escaped, JSON-escaped."""
    forms = {
        value,
        quote(value, safe=""),
        quote(value),
        quote_plus(value),
        escape(value, quote=True),
        json.dumps(value)[1:-1],
    }
    for form in list(forms):
        if "%" in form:
            forms.add(re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), form))
    return {form for form in forms if len(form) >= MIN_SCRUB_CHARS}


def scrub_resolved(payload: object, env_values: dict[str, str]) -> object:
    """Put `${NAME}` back wherever a resolved placeholder value came back out.

    The player receives real values and the page can echo them — into a URL, a title,
    an assertion's `actual`. The secret scanner only catches values that LOOK like
    secrets; an email address used as a login does not. Anything the scenario resolved
    is therefore mapped back to its placeholder by value before the payload is written,
    shown to a reviewer, or returned. Longest values first, so one value that contains
    another is restored whole.
    """
    pairs = sorted(
        (
            (form, f"${{{name}}}")
            for name, value in env_values.items()
            if isinstance(value, str) and len(value) >= MIN_SCRUB_CHARS
            for form in _variants(value)
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if not pairs:
        return payload

    def visit(item):
        if isinstance(item, str):
            for value, placeholder in pairs:
                if value in item:
                    item = item.replace(value, placeholder)
            return item
        if isinstance(item, dict):
            return {visit(k) if isinstance(k, str) else k: visit(v) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, tuple):
            return tuple(visit(child) for child in item)
        return item

    return visit(payload)


def scrub_text_files(directory: Path, env_values: dict[str, str]) -> list[dict]:
    """Scrub player-written text artifacts in place: resolved values back to placeholders,
    then the shared secret scanner. Returns the scanner's hits for the audit trail.

    Recursive: a subdirectory is not a place a secret can hide from this pass."""
    try:
        paths = sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_ARTIFACT_SUFFIXES)
    except OSError:
        return []
    hits_all: list[dict] = []
    for path in paths:
        try:
            text = path.read_bytes().decode("utf-8", errors="ignore")  # a bounded capture may cut a character
            clean, hits = redact_value(scrub_resolved(text, env_values))
            atomic_write_text(path, str(clean))
        except OSError:
            continue  # one unreadable file must not leave the rest unscrubbed
        hits_all.extend(hits)
    return hits_all


def redact_text(text: str) -> tuple[str, list[dict]]:
    clean, hits = redact_value(text)
    return str(clean), hits


def strip_input_values(event: object) -> object:
    """Drop typed values from a player event before it is stored or shown."""
    if isinstance(event, dict):
        return {
            key: ("[omitted]" if key in _VALUE_KEYS and isinstance(child, str) else strip_input_values(child))
            for key, child in event.items()
        }
    if isinstance(event, list):
        return [strip_input_values(child) for child in event]
    return event


def redact_payload(payload: object) -> tuple[object, list[dict]]:
    """Secret-scan a JSON-like payload after dropping input values."""
    return redact_value(strip_input_values(payload))


def write_json(path: Path, payload: object) -> list[dict]:
    """Write a redacted JSON artifact; returns the redaction hits for the audit trail."""
    clean, hits = redact_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(clean, indent=2, ensure_ascii=False, default=str))
    return hits


def write_jsonl(path: Path, events: list[dict]) -> list[dict]:
    hits_all: list[dict] = []
    lines: list[str] = []
    for event in events:
        clean, hits = redact_payload(event)
        hits_all.extend(hits)
        lines.append(json.dumps(clean, ensure_ascii=False, default=str))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return hits_all


def write_text(path: Path, text: str) -> list[dict]:
    clean, hits = redact_value(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, str(clean))
    return hits
