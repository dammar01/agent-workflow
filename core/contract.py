def normalize_output(*, ok: bool, content: str, meta: dict | None = None) -> dict:
    return {
        "ok": ok,
        "content": content,
        "meta": meta or {},
    }
