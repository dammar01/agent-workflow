from copy import deepcopy

from config.roles import VALID_ROLES


def normalize_output(
    *,
    status: str,
    adapter: str,
    model: str | None,
    role: str,
    session_id: str,
    opencode_session_id: str | None = None,
    content: str,
    confidence: str = "medium",
    notes: str = "",
    extra_meta: dict | None = None,
) -> dict:
    if status not in {"success", "error"}:
        status = "error"
        notes = _join_notes(notes, "invalid status normalized to error")

    if adapter != "opencode":
        status = "error"
        notes = _join_notes(notes, f"invalid adapter: {adapter}")

    if role not in VALID_ROLES:
        status = "error"
        notes = _join_notes(notes, f"invalid role: {role}")

    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
        notes = _join_notes(notes, "invalid confidence normalized to low")

    meta = {
        "confidence": confidence,
        "notes": notes,
    }
    if extra_meta:
        meta.update(extra_meta)

    return {
        "status": status,
        "adapter": adapter,
        "model": model,
        "role": role,
        "session_id": session_id,
        "opencode_session_id": opencode_session_id,
        "content": str(content or ""),
        "meta": meta,
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
