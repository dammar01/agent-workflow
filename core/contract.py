from copy import deepcopy

from config.roles import VALID_MODELS, VALID_ROLES


def normalize_output(
    *,
    status: str,
    model: str,
    role: str,
    session_id: str,
    content: str,
    confidence: str = "medium",
    notes: str = "",
) -> dict:
    if status not in {"success", "error"}:
        status = "error"
        notes = _join_notes(notes, "invalid status normalized to error")

    if model not in VALID_MODELS:
        status = "error"
        notes = _join_notes(notes, f"invalid model: {model}")

    if role not in VALID_ROLES:
        status = "error"
        notes = _join_notes(notes, f"invalid role: {role}")

    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
        notes = _join_notes(notes, "invalid confidence normalized to low")

    return {
        "status": status,
        "model": model,
        "role": role,
        "session_id": session_id,
        "content": str(content or ""),
        "meta": {
            "confidence": confidence,
            "notes": notes,
        },
    }


def mark_cache_hit(output: dict, session_id: str) -> dict:
    cached = deepcopy(output)
    cached["session_id"] = session_id
    cached.setdefault("meta", {})
    cached["meta"]["notes"] = _join_notes(cached["meta"].get("notes", ""), "cache_hit")
    return cached


def _join_notes(current: str, extra: str) -> str:
    if current and extra:
        return f"{current}; {extra}"
    return current or extra
