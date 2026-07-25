"""Persistent, staleness-checked index over delegated-evidence artifacts (v3.4.0, Opsi A).

The full evidence a second_agent produces is already written to
`.workflow/sessions/<sid>/runtime/response.last.md` (the artifact). This module adds a thin
INDEX over that artifact — `.workflow/evidence.jsonl` at the workflow root, shared across
sessions — so an identical delegated call can be answered from a prior run instead of
re-spending time and quota.

Design mirrors fact_store deliberately:
  * staleness is anchor_hash based — an artifact cites file:line locations; if the content
    at any cited line changed, the artifact is stale and must NOT be served as fresh. We
    reuse fact_store._anchor_hash so the two stores agree on what "stale" means.
  * append-only jsonl with a FIFO cap, atomic rewrite (temp + os.replace).
  * reuse is EXACT-query only (same command + normalized task): different wording -> a
    different query_hash -> never a false hit. Correctness rests on exact match AND the
    anchor freshness check, so a match that drifted is dropped rather than served.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from core.fact_store import _anchor_hash, _FILELINE
from core.workflow_runtime import now_iso, workflow_paths

EVIDENCE_FILENAME = "evidence.jsonl"
# FIFO ceiling: bound disk + scan cost. Old artifacts age out; staleness already drops the
# rest at read time, so this only caps sheer volume.
MAX_EVIDENCE = 200
# Cap anchors tracked per artifact — the freshness check is O(anchors) file reads, so a huge
# evidence dump does not turn one reuse probe into hundreds of stat/reads.
MAX_ANCHORS_PER_ARTIFACT = 40


def _path(project_root: Path) -> Path:
    return workflow_paths(Path(project_root))["workflow_dir"] / EVIDENCE_FILENAME


def _query_hash(command: str, task: str | None) -> str:
    norm = re.sub(r"\s+", " ", (task or "").strip().lower())
    return hashlib.sha256(f"{command}|{norm}".encode("utf-8")).hexdigest()[:16]


def _extract_anchors(project_root: Path, content: str) -> list[dict]:
    """Distinct file:line anchors cited in `content`, each with its current line hash.

    Only anchors that resolve NOW are kept — an anchor we cannot hash today gives us no
    staleness signal, so it would only weaken the freshness guarantee.
    """
    seen: set[tuple[str, int]] = set()
    anchors: list[dict] = []
    for m in _FILELINE.finditer(content or ""):
        file, line = m.group(1), int(m.group(2))
        key = (file, line)
        if key in seen:
            continue
        seen.add(key)
        h = _anchor_hash(project_root, file, line)
        if h is None:
            continue
        anchors.append({"file": file, "line": line, "hash": h})
        if len(anchors) >= MAX_ANCHORS_PER_ARTIFACT:
            break
    return anchors


def _load(project_root: Path) -> list[dict]:
    p = _path(project_root)
    if not p.is_file():
        return []
    out: list[dict] = []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            continue  # skip a corrupt line rather than lose the whole index
        if isinstance(row, dict):
            out.append(row)
    return out


def _save(project_root: Path, rows: list[dict]) -> None:
    p = _path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    tmp.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    os.replace(tmp, p)  # atomic on same volume — a reader never sees a half-written index


def _is_fresh(project_root: Path, entry: dict) -> bool:
    """An artifact is fresh only if EVERY cited anchor still hashes to the recorded value.

    No anchors -> cannot certify -> not fresh (never serve unverifiable cached evidence).
    """
    anchors = entry.get("anchors") or []
    if not anchors:
        return False
    for a in anchors:
        if _anchor_hash(project_root, a.get("file"), a.get("line")) != a.get("hash"):
            return False
    return True


def record(
    project_root: Path,
    command: str,
    task: str | None,
    session_id: str,
    digest,
    artifact_path,
    content: str,
) -> dict:
    """Index one delegated evidence artifact. Returns the stored entry."""
    entry = {
        "query_hash": _query_hash(command, task),
        "command": command,
        "task_preview": (task or "")[:200],
        "digest": digest,
        "artifact_path": str(artifact_path) if artifact_path else None,
        "anchors": _extract_anchors(project_root, content),
        "captured_at": now_iso(),
        "session": session_id,
    }
    rows = _load(project_root)
    rows.append(entry)
    if len(rows) > MAX_EVIDENCE:
        rows = rows[-MAX_EVIDENCE:]  # FIFO: drop oldest
    _save(project_root, rows)
    return entry


def find_fresh(project_root: Path, command: str, task: str | None) -> dict | None:
    """Most-recent artifact for this EXACT query whose anchors are all still fresh.

    If the most-recent match is stale, return None (re-delegate) rather than reaching for
    an older, necessarily-staler entry.
    """
    qh = _query_hash(command, task)
    rows = _load(project_root)
    for entry in reversed(rows):
        if entry.get("query_hash") != qh:
            continue
        return entry if _is_fresh(project_root, entry) else None
    return None
