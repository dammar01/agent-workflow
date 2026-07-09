"""Output contract. Every result is `ok:true` or a structured error.

Shape stays `{ok, content, meta}` (backward compatible). Errors carry
`error_type` + mandatory `next_action` inside `meta`. Optional `digest`
rides at the top level for main_agent to relay.
"""

ERROR_TYPES = {
    "permission_denied",
    "path_out_of_scope",
    "empty_output",
    "job_already_running",
    "session_capture_failed",
    "invalid_evidence",
    "timeout",
    "command_not_found",
    "routing_error",
    "worker_died",
    "workflow_init_error",
    "job_submit_error",
    "runtime_lock",
    "unknown",
}

# Required fields per command, checked by validate_fields.
REQUIRED_FIELDS = {
    "explore": ("entry_points", "uncertainties"),
    "analyze": ("grounded", "uncertainties"),
    "plan": ("grounded", "uncertainties"),
}


def make_ok(content: str, meta: dict | None = None, digest: dict | None = None) -> dict:
    payload = {"ok": True, "content": content or "", "meta": meta or {}}
    if digest is not None:
        payload["digest"] = digest
    return payload


def make_error(error_type: str, message: str, next_action: str, meta: dict | None = None, **fields) -> dict:
    if error_type not in ERROR_TYPES:
        raise ValueError(f"unknown error_type: {error_type}")
    if not next_action or not str(next_action).strip():
        raise ValueError(f"next_action is required for error_type {error_type}")
    merged = dict(meta or {})
    merged["error_type"] = error_type
    merged["next_action"] = next_action
    merged.update(fields)
    return {"ok": False, "content": message or error_type, "meta": merged}


def normalize_output(*, ok: bool, content: str, meta: dict | None = None) -> dict:
    """Legacy shim — kept for callers still passing raw ok/content/meta."""
    return {"ok": ok, "content": content, "meta": meta or {}}


def validate_fields(command: str, content: str) -> list[str]:
    """Return names of required fields missing from an evidence payload."""
    required = REQUIRED_FIELDS.get(command, ())
    lowered = (content or "").lower()
    return [field for field in required if field not in lowered]


def extract_digest(content: str) -> dict | None:
    """Parse a [DIGEST] block from second_agent output. None if absent/unusable.

    Graceful: main_agent falls back to the full content (contract_detail) when
    this returns None.
    """
    import re

    if not content or "[DIGEST]" not in content:
        return None
    block = content.split("[DIGEST]", 1)[1]
    # Stop at the next bracketed section, if any.
    block = re.split(r"\n\[[A-Z_]+\]", block, 1)[0]

    def _field(name: str) -> str:
        match = re.search(rf"{name}\s*:\s*(.+)", block, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    summary = _field("summary")
    if not summary:
        return None  # a digest without a summary is not worth relaying

    findings = []
    fm = re.search(r"key_findings\s*:\s*(.*?)(?:\nrisk_level|\nrecommended_next_action|\nconfidence|$)", block, re.IGNORECASE | re.DOTALL)
    if fm:
        for line in fm.group(1).splitlines():
            line = line.strip().lstrip("-").strip()
            if line:
                findings.append(line)

    risk = _field("risk_level").lower()
    return {
        "summary": summary,
        "key_findings": findings[:3],
        "risk_level": risk if risk in {"low", "medium", "high"} else "unknown",
        "recommended_next_action": _field("recommended_next_action"),
        "confidence": _field("confidence").lower() or "unknown",
    }
