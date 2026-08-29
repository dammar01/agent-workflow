"""Shaping and sanitising a delegated call result before it is returned."""

from core.workspace.workspace_paths import CONFIG_VERSION
from utils.redact import redact_value


def _scope_incomplete(content: str) -> bool:
    """True when the run itself reported ground it did not cover.

    Read off the agent's own `scope_not_covered` section: it is the one admission of
    incompleteness that arrives without lowering the confidence line beside it.
    """
    lines = (content or "").splitlines()
    collecting = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("scope_not_covered"):
            collecting = True
            continue
        if collecting:
            if not stripped:
                continue
            if not stripped.startswith("-"):
                break
            body = stripped.lstrip("-").strip().lower()
            if body and body not in {"none", "(none)", "n/a"}:
                return True
    return False

def _attach_redactions(meta: dict, hits: list[dict]) -> None:
    if not hits:
        return
    counts: dict[str, int] = {}
    for hit in [*(meta.get("redactions") or []), *hits]:
        if not isinstance(hit, dict) or not hit.get("kind"):
            continue
        kind = str(hit["kind"])
        counts[kind] = counts.get(kind, 0) + int(hit.get("count") or 0)
    meta["redactions"] = [
        {"kind": kind, "count": count} for kind, count in counts.items()
    ]
    meta["redaction_count"] = sum(counts.values())

def _without_raw_args(value):
    """Remove argv payloads from injected/legacy adapter data."""
    if isinstance(value, dict):
        raw_args = value.get("args")
        clean = {
            key: _without_raw_args(child)
            for key, child in value.items()
            if key != "args"
        }
        if isinstance(raw_args, (list, tuple)):
            clean.setdefault("argv_count", len(raw_args))
            clean.setdefault("argv_chars", sum(len(str(arg)) for arg in raw_args))
        return clean
    if isinstance(value, list):
        return [_without_raw_args(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_without_raw_args(child) for child in value)
    return value

def _sanitize_result(result):
    clean, hits = redact_value(_without_raw_args(result))
    if isinstance(clean, dict):
        meta = clean.setdefault("meta", {})
        if isinstance(meta, dict):
            _attach_redactions(meta, hits)
    return clean, hits

def _evidence_context(
    route: dict,
    fanout: bool,
    graph_leads: dict | None,
    known_facts: list[str] | None,
) -> dict:
    """Inputs that can materially change an otherwise identical delegated answer."""
    return {
        "schema": 2,
        "runtime_config_version": CONFIG_VERSION,
        "route": dict(route),
        "fanout": bool(fanout),
        "graph_leads": graph_leads,
        "known_facts": known_facts or [],
    }
