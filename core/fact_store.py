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

from core.workflow_runtime import now_iso, workflow_paths

FACTS_FILENAME = "facts.jsonl"
RECURRENCE_THRESHOLD = 5   # distinct sessions a grounded claim must appear in to auto-promote
MAX_FACTS = 500
RELEVANT_LIMIT = 8

_FILELINE = re.compile(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_]+):(\d+)")
_CATEGORY = re.compile(r"^\[(config|pattern|invariant)\]", re.IGNORECASE)


def _facts_path(project_root: Path) -> Path:
    return workflow_paths(project_root)["workflow_dir"] / FACTS_FILENAME


def _normalize(claim: str) -> str:
    text = _FILELINE.sub("", claim)          # drop file:line
    text = re.sub(r"\[[^\]]*\]", "", text)   # drop [tags]
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_block(content: str, header: str) -> list[str]:
    """Bullet lines under `header:` until the first non-bullet line."""
    out: list[str] = []
    collecting = False
    for line in content.splitlines():
        s = line.strip()
        if not collecting:
            if s.lower().startswith(header.lower() + ":"):
                collecting = True
            continue
        if s.startswith("-"):
            out.append(s[1:].strip())
        else:
            break
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


def _save_facts(project_root: Path, facts: list[dict]) -> None:
    path = _facts_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    facts = facts[-MAX_FACTS:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in facts) + ("\n" if facts else ""),
        encoding="utf-8",
    )
    tmp.replace(path)


def _recurrence_counts(project_root: Path) -> dict[str, int]:
    """normalized grounded claim -> number of DISTINCT sessions whose logs contain it."""
    sessions = workflow_paths(project_root)["workflow_dir"] / "sessions"
    if not sessions.exists():
        return {}
    per_claim: dict[str, set[str]] = {}
    for sdir in sessions.iterdir():
        logs = sdir / "logs"
        if not (sdir.is_dir() and logs.exists()):
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
    existing = _load_facts(project_root)
    seen = {(_normalize(f.get("claim", "")), f.get("file")) for f in existing}
    added = 0

    def _try_add(raw_claim: str, category: str) -> None:
        nonlocal added
        file, line = _extract_fileline(raw_claim)
        anchor = _anchor_hash(project_root, file, line)
        if anchor is None:
            return  # ingest-time verify: anchor gone/invalid → skip stale-at-birth
        claim = _CATEGORY.sub("", raw_claim).strip()
        key = (_normalize(claim), file)
        if not key[0] or key in seen:
            return
        seen.add(key)
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

    # 2) grounded claims recurring across >= threshold distinct sessions
    counts = _recurrence_counts(project_root)
    for claim in _parse_block(content, "grounded"):
        if counts.get(_normalize(claim), 0) >= RECURRENCE_THRESHOLD:
            _try_add(claim, "recurring")

    if added:
        _save_facts(project_root, existing)
    return added


def load_relevant(project_root: Path, task: str, limit: int = RELEVANT_LIMIT) -> list[dict]:
    """FRESH facts relevant to `task` (stale ones dropped — never served as fresh)."""
    facts = _load_facts(project_root)
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
    """Drop stale/invalid facts (anchored line changed or vanished)."""
    facts = _load_facts(project_root)
    fresh = [
        f
        for f in facts
        if _anchor_hash(project_root, f.get("file"), f.get("line")) == f.get("anchor_hash")
        and f.get("anchor_hash") is not None
    ]
    if len(fresh) != len(facts):
        _save_facts(project_root, fresh)
    return {"kept": len(fresh), "removed": len(facts) - len(fresh)}
