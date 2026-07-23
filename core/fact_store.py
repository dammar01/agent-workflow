"""Project-local, evidence-derived fact store (.workflow/facts.jsonl).

Memory-like knowledge distilled from delegated runs. Ingests ONLY durable facts
(config / pattern / invariant) or claims that recur across >= RECURRENCE_THRESHOLD
distinct sessions. Every fact is anchored to a file:line content hash so staleness
is detectable — at INGEST (skip facts whose anchor already vanished, i.e. no
stale-at-birth) and at READ (drop/flag facts whose anchored line changed).

Raw per-run logs keep everything; this store keeps only the durable/recurring subset.
"""
import hashlib
import json
import re
from pathlib import Path

from core.workflow_runtime import (
    _safe_component,
    now_iso,
    read_json_file,
    workflow_paths,
)

FACTS_FILENAME = "facts.jsonl"
RECURRENCE_THRESHOLD = 5   # distinct OTHER sessions a grounded claim must appear in to auto-promote
MAX_FACTS = 500
RELEVANT_LIMIT = 3
# Two claims anchored to the SAME file are treated as one when their word sets
# overlap this much — proxies rephrase the same fact every run, and the plain
# normalized-string key never catches that.
DUPLICATE_SIMILARITY = 0.5
# Short claims overlap by accident ("validates token" vs "handles token" is 0.5),
# so similarity is only trusted once both claims carry enough words to be specific.
# Below that, only an exact normalized match counts as a duplicate.
MIN_WORDS_FOR_SIMILARITY = 6
# Line proximity is a WEAK identity signal, so it is allowed only for read-time dedup
# (which merely trims what gets injected). A collapse that deletes a stored record
# always demands an identical anchor_hash — losing a real fact is unrecoverable.
LINE_PROXIMITY = 3

_FILELINE = re.compile(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+):(\d+)")
_CATEGORY = re.compile(r"^\[(config|pattern|invariant)\]", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9_]+")
# "X is cached" and "X is not cached" share almost every word — high similarity, opposite
# meaning. A polarity mismatch vetoes a collapse outright.
_NEGATION = re.compile(
    r"\b(not|no|never|without|non|un|cannot|tidak|bukan|tanpa|belum|jangan)\b"
)


def _facts_path(project_root: Path) -> Path:
    return workflow_paths(project_root)["workflow_dir"] / FACTS_FILENAME


def _policies(project_root: Path) -> dict:
    """Project-local tuning knobs; falls back to module constants when absent."""
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
    except (OSError, ValueError):
        return {}
    policies = config.get("policies")
    return policies if isinstance(policies, dict) else {}


def _policy_int(project_root: Path, key: str, fallback: int) -> int:
    value = _policies(project_root).get(key, fallback)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _normalize(claim: str) -> str:
    text = _FILELINE.sub("", claim)          # drop file:line
    text = re.sub(r"\[[^\]]*\]", "", text)   # drop [tags]
    text = text.replace("*", "").replace("`", "")  # drop markdown emphasis/code ticks
    return re.sub(r"\s+", " ", text).strip().lower()


def _words(normalized: str) -> set[str]:
    return set(_WORD.findall(normalized))


def _similar(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _signature(
    claim: str, category: str | None, anchor_hash: str | None, line: int | None
) -> dict:
    normalized = _normalize(claim)
    return {
        "words": _words(normalized),
        "negated": bool(_NEGATION.search(normalized)),
        "category": category,
        "anchor_hash": anchor_hash,
        "line": line,
    }


def _fact_signature(fact: dict) -> dict:
    return _signature(
        fact.get("claim", ""),
        fact.get("category"),
        fact.get("anchor_hash"),
        fact.get("line"),
    )


def _is_duplicate(
    sig: dict, others: list[dict], *, allow_line_proximity: bool = False
) -> bool:
    """Two same-file claims are the same fact only when EVERY guard agrees.

    Word similarity alone is not identity: it happily merges a claim with its own
    negation, or two unrelated facts that happen to share vocabulary. Each guard below
    exists to veto one of those false merges, and a veto always means "keep both".
    """
    for other in others:
        if sig["category"] != other["category"]:
            continue  # a [config] value and an [invariant] are not interchangeable
        if sig["negated"] != other["negated"]:
            continue  # opposite polarity — never the same fact
        same_anchor = (
            sig["anchor_hash"] is not None
            and sig["anchor_hash"] == other["anchor_hash"]
        )
        near_line = (
            allow_line_proximity
            and sig["line"] is not None
            and other["line"] is not None
            and abs(sig["line"] - other["line"]) <= LINE_PROXIMITY
        )
        if not (same_anchor or near_line):
            continue  # different anchor → treat as different facts
        if sig["words"] == other["words"]:
            return True
        if min(len(sig["words"]), len(other["words"])) < MIN_WORDS_FOR_SIMILARITY:
            continue
        if _similar(sig["words"], other["words"]) >= DUPLICATE_SIMILARITY:
            return True
    return False


# A sibling section starts at column 0 with a snake_case key and a colon — the shape
# every block in the agent's output format uses (`grounded:`, `external:`, `confidence:`).
# Trailing text after the colon still counts: `external: none (...)` ends the previous
# block, it is NOT a continuation of the last bullet.
_SECTION_HEADER = re.compile(r"^[a-z][a-z0-9_]{0,30}:(\s|$)")
_BULLET = re.compile(r"^[-*•]\s*")


def _parse_block(content: str, header: str) -> list[str]:
    """Bullet lines under `header:`, up to the next section header.

    Tolerant on purpose. The previous version stopped at the FIRST line that did not
    start with "-", so a blank line, an indented sub-bullet, a fenced code block or a
    "*" bullet silently truncated the section to nothing — and since ingest swallowed
    exceptions, that looked exactly like "the run had no facts". Anything the second
    agent plausibly emits should still parse; only a genuine new section ends the block.
    """
    out: list[str] = []
    collecting = False
    in_fence = False
    header_prefix = header.lower() + ":"

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if not collecting:
            if stripped.lower().startswith(header_prefix):
                collecting = True
                inline = stripped[len(header_prefix):].strip()
                if inline and inline.lower() != "none":
                    out.append(_BULLET.sub("", inline).strip())
            continue

        if not stripped:
            continue  # blank lines separate bullets, they do not end the section
        # Unindented on purpose: an indented `key:` is sub-structure of the current
        # bullet, only a column-0 key opens the next section.
        if _SECTION_HEADER.match(line) and not _BULLET.match(stripped):
            break
        if stripped.startswith("[") and stripped.endswith("]"):
            break  # a bracketed block marker, e.g. [DIGEST]
        if _BULLET.match(stripped):
            out.append(_BULLET.sub("", stripped).strip())
            continue
        if out:
            # Continuation of the previous bullet (wrapped line).
            out[-1] = f"{out[-1]} {stripped}".strip()

    return [o for o in out if o and o.lower() != "none"]


def _extract_fileline(text: str) -> tuple[str | None, int | None]:
    m = _FILELINE.search(text)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _extract_category(text: str) -> str | None:
    m = _CATEGORY.match(text.strip())
    return m.group(1).lower() if m else None


def _anchor_hash(project_root: Path, file: str | None, line: int | None) -> str | None:
    """Hash the referenced line's content. None if file/line is missing or out of range
    (used as the light ingest-time verify AND the read-time staleness check)."""
    if not file or not line:
        return None
    path = Path(project_root) / file
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if line < 1 or line > len(lines):
        return None
    return hashlib.sha256(lines[line - 1].strip().encode("utf-8")).hexdigest()[:16]


def _load_facts(project_root: Path) -> list[dict]:
    path = _facts_path(project_root)
    if not path.exists():
        return []
    facts: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            facts.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return facts


def _dedupe(facts: list[dict], *, allow_line_proximity: bool = False) -> list[dict]:
    """Collapse duplicate claims anchored to the same file, keeping the first.

    The store is append-only, so a rephrased-but-identical fact would otherwise be
    stored (and later injected) once per phrasing.

    `allow_line_proximity` is for READ-time trimming only, where dropping a near-duplicate
    costs nothing. Persistent collapse (save/ingest/prune) leaves it off, so a stored
    record is deleted only when another record carries the very same anchor.
    """
    kept: list[dict] = []
    per_file: dict[str | None, list[dict]] = {}
    for fact in facts:
        sig = _fact_signature(fact)
        if not sig["words"]:
            continue
        file = fact.get("file")
        if _is_duplicate(
            sig, per_file.get(file, []), allow_line_proximity=allow_line_proximity
        ):
            continue
        per_file.setdefault(file, []).append(sig)
        kept.append(fact)
    return kept


def _save_facts(project_root: Path, facts: list[dict]) -> None:
    path = _facts_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    facts = _dedupe(facts)[-MAX_FACTS:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in facts) + ("\n" if facts else ""),
        encoding="utf-8",
    )
    tmp.replace(path)


def _recurrence_counts(
    project_root: Path, exclude_session_id: str | None = None
) -> dict[str, int]:
    """normalized grounded claim -> number of DISTINCT sessions whose logs contain it.

    The CURRENT session is excluded: its output.raw.md is written before ingest runs,
    so counting it lets a fact that was injected as a KNOWN_FACT (and merely echoed
    back by second_agent) raise its own recurrence — evidence laundering, not support.
    """
    sessions = workflow_paths(project_root)["workflow_dir"] / "sessions"
    if not sessions.exists():
        return {}
    excluded = _safe_component(exclude_session_id) if exclude_session_id else None
    per_claim: dict[str, set[str]] = {}
    for sdir in sessions.iterdir():
        logs = sdir / "logs"
        if not (sdir.is_dir() and logs.exists()):
            continue
        if excluded and sdir.name == excluded:
            continue
        seen: set[str] = set()
        for run in logs.iterdir():
            out = run / "output.raw.md"
            if not out.exists():
                continue
            try:
                content = out.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for claim in _parse_block(content, "grounded"):
                norm = _normalize(claim)
                if norm:
                    seen.add(norm)
        for norm in seen:
            per_claim.setdefault(norm, set()).add(sdir.name)
    return {claim: len(dirs) for claim, dirs in per_claim.items()}


def ingest(project_root: Path, content: str, session_id: str) -> int:
    """Promote qualifying claims from one run's output into the fact store. Returns count added."""
    loaded = _load_facts(project_root)
    existing = _dedupe(loaded)
    collapsed = len(loaded) - len(existing)
    seen: dict[str | None, list[dict]] = {}
    for fact in existing:
        sig = _fact_signature(fact)
        if sig["words"]:
            seen.setdefault(fact.get("file"), []).append(sig)
    added = 0

    def _try_add(raw_claim: str, category: str) -> None:
        nonlocal added
        file, line = _extract_fileline(raw_claim)
        anchor = _anchor_hash(project_root, file, line)
        if anchor is None:
            return  # ingest-time verify: anchor gone/invalid → skip stale-at-birth
        claim = _CATEGORY.sub("", raw_claim).strip()
        sig = _signature(claim, category, anchor, line)
        if not sig["words"]:
            return
        if _is_duplicate(sig, seen.get(file, [])):
            return
        seen.setdefault(file, []).append(sig)
        existing.append(
            {
                "claim": claim,
                "category": category,
                "file": file,
                "line": line,
                "anchor_hash": anchor,
                "captured_at": now_iso(),
                "session": session_id,
            }
        )
        added += 1

    # 1) explicit durable facts (config/pattern/invariant)
    for item in _parse_block(content, "durable_facts"):
        _try_add(item, _extract_category(item) or "invariant")

    # 2) grounded claims recurring across >= threshold distinct OTHER sessions
    threshold = _policy_int(
        project_root, "fact_recurrence_threshold", RECURRENCE_THRESHOLD
    )
    counts = _recurrence_counts(project_root, exclude_session_id=session_id)
    for claim in _parse_block(content, "grounded"):
        if counts.get(_normalize(claim), 0) >= threshold:
            _try_add(claim, "recurring")

    if added or collapsed:
        _save_facts(project_root, existing)
    return added


def load_relevant(
    project_root: Path, task: str, limit: int | None = None
) -> list[dict]:
    """FRESH facts relevant to `task` (stale ones dropped — never served as fresh)."""
    if limit is None:
        limit = _policy_int(project_root, "fact_relevant_limit", RELEVANT_LIMIT)
    # read-time trimming: nothing is deleted here, so the weaker proximity signal is safe
    facts = _dedupe(_load_facts(project_root), allow_line_proximity=True)
    if not facts:
        return []
    task_words = set(re.findall(r"[a-z0-9_]{3,}", (task or "").lower()))
    scored: list[tuple[int, dict]] = []
    for f in facts:
        current = _anchor_hash(project_root, f.get("file"), f.get("line"))
        if current is None or current != f.get("anchor_hash"):
            continue  # stale/invalid → drop, do not inject
        blob = f"{f.get('claim', '')} {f.get('file') or ''}".lower()
        overlap = len(task_words & set(re.findall(r"[a-z0-9_]{3,}", blob)))
        scored.append((overlap, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def prune(project_root: Path) -> dict:
    """Drop stale/invalid facts (anchored line changed or vanished) and collapse duplicates."""
    facts = _load_facts(project_root)
    fresh = [
        f
        for f in facts
        if _anchor_hash(project_root, f.get("file"), f.get("line")) == f.get("anchor_hash")
        and f.get("anchor_hash") is not None
    ]
    deduped = _dedupe(fresh)
    if len(deduped) != len(facts):
        _save_facts(project_root, deduped)
    return {
        "kept": len(deduped),
        "removed": len(facts) - len(fresh),
        "duplicates_collapsed": len(fresh) - len(deduped),
    }
