import json
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


def clean_json_string(s: str) -> str:
    if not s:
        return "{}"
    s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", s)
    s = re.sub(r'(?<!\\)"([^"]*)\n([^"]*)"', r'"\1\\n\2"', s)
    s = re.sub(r'(?<!\\)"([^"]*)\t([^"]*)"', r'"\1\\t\2"', s)
    s = re.sub(r'(?<!\\)"([^"]*)\r([^"]*)"', r'"\1\\r\2"', s)
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    s = re.sub(r"(\w+)(\s*:\s*)", r'"\1"\2', s)
    return s.strip()


def extract_json_from_text(text: str) -> str | None:
    if not text:
        return None
    patterns = [
        r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
        r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.DOTALL):
            candidate = match.group().strip()
            if (candidate.startswith("{") and candidate.endswith("}")) or (
                candidate.startswith("[") and candidate.endswith("]")
            ):
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue
    return None


def _extract_key_value_pairs(text: str) -> dict:
    if not text:
        return {}
    result = {}
    patterns = [
        r'["\']?(\w+)["\']?\s*[:\=]\s*["\']([^"\']*)["\']',
        r"(\w+)\s*[:\=]\s*([^\n,}]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
            key = match.group(1).strip()
            value = match.group(2).strip().rstrip(",}]").strip()
            result[key] = value
    return result


def safe_parse_json(value) -> dict:
    if not isinstance(value, str):
        return {"raw_content": str(value)}

    for attempt in (value, clean_json_string(value)):
        try:
            result = json.loads(attempt)
            return result if isinstance(result, dict) else {"parsed_content": result}
        except json.JSONDecodeError:
            pass

    extracted = extract_json_from_text(value)
    if extracted:
        try:
            result = json.loads(extracted)
            return result if isinstance(result, dict) else {"parsed_content": result}
        except json.JSONDecodeError:
            pass

    kv = _extract_key_value_pairs(value)
    if kv:
        return kv

    return {"raw_content": value}


def clean_structured_output(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    best_json = None
    best_len = 0
    for start, opener in ((i, ch) for i, ch in enumerate(cleaned) if ch in "{["):
        stack: list[str] = []
        for end in range(start, len(cleaned)):
            c = cleaned[end]
            if c in "{[":
                stack.append(c)
            elif c in "}]":
                if not stack:
                    break
                opening = stack.pop()
                if (opening == "{" and c != "}") or (opening == "[" and c != "]"):
                    break
                if not stack:
                    candidate = cleaned[start : end + 1]
                    if len(candidate) > best_len:
                        best_json = candidate
                        best_len = len(candidate)
                    break
    return best_json.strip() if best_json else cleaned


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
