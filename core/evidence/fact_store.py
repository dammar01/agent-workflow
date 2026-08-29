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
import os
import re
import time
from pathlib import Path

from core.workspace.workspace_paths import (
    _safe_component,
    now_iso,
    read_json_file,
    workflow_paths,
)

FACTS_FILENAME = "facts.jsonl"
LOCK_FILENAME = "facts.jsonl.lock"
# A held lock older than this is presumed orphaned (the writer crashed) and stolen. ingest
# is sub-second, so any lock older than this window is not a live writer; the generous
# margin only avoids stealing from a writer swapped out under heavy load.
LOCK_TTL_SECONDS = 30
RECURRENCE_THRESHOLD = 3   # distinct OTHER sessions a grounded claim must appear in to auto-promote
MAX_FACTS = 500

# Provenance of a stored fact. Only `discovered` (a run read code and grounded the claim)
# may raise cross-session recurrence; a fact that merely got injected and echoed back must
# never re-promote itself. user_provided is reserved for facts a human asserts directly.
ORIGIN_DISCOVERED = "discovered"
ORIGIN_MEMORY_INJECTED = "memory_injected"
ORIGIN_USER_PROVIDED = "user_provided"
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
# A fact with ZERO task-word overlap is noise: injecting it wastes prompt budget and can
# mislead the second agent with unrelated context. Read-time retrieval requires at least
# this much overlap — below it the fact is dropped, never served as "relevant".
MIN_RELEVANCE_OVERLAP = 1

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


class _FactLock:
    """Cross-process advisory lock around the facts.jsonl read-modify-write.

    ingest/prune both load the whole store, mutate it, and rewrite it. Two sessions doing
    that at once (the norm for one project) would each save their own view, and the second
    write would silently drop the first's additions. An O_EXCL lock-file serialises them.

    Failure posture is best-effort, never blocking forever: a lock older than LOCK_TTL is
    treated as orphaned and stolen, and if the lock is still not free by the deadline the
    stale one is removed and re-created. Facts are recoverable best-effort memory; a stuck
    lock starving every future ingest would be worse than a rare lost update. On Windows a
    LIVE holder's file cannot be unlinked (its handle is open), so a real writer is never
    stolen from — only a genuinely dead one is.
    """

    def __init__(self, project_root: Path):
        self.path = _facts_path(project_root).with_name(LOCK_FILENAME)
        self.fd: int | None = None

    def __enter__(self) -> "_FactLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + LOCK_TTL_SECONDS
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
                return self
            except FileExistsError:
                if self._is_orphaned() or time.time() > deadline:
                    self._steal()
                time.sleep(0.05)

    def _is_orphaned(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > LOCK_TTL_SECONDS
        except OSError:
            return True  # vanished between EEXIST and stat -> free to retry

    def _steal(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass  # live holder on Windows, or already gone — retry the O_EXCL open

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        self._steal()


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

    Blank lines, sub-bullets, fenced blocks, and alternate bullet markers do not end
    the section; only a genuine new section does.
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


def _line_hash(text: str) -> str:
    """The anchor value for one line of source. Whitespace is stripped first, so
    re-indenting a block does not invalidate every fact anchored inside it."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _read_lines(project_root: Path, file: str | None) -> list[str] | None:
    """Lines of a project-relative file, or None if it cannot be read as one.

    The containment check is the reason this is not just `read_text`: `file` arrives from a
    stored fact, and a fact claiming `../../etc/passwd` must not turn into a read outside
    the project root.
    """
    if not file:
        return None
    try:
        root = Path(project_root).resolve()
        path = (root / file).resolve()
    except (OSError, ValueError):
        return None
    if path != root and root not in path.parents:
        return None
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _anchor_hash(project_root: Path, file: str | None, line: int | None) -> str | None:
    """Hash the referenced line's content. None if file/line is missing or out of range
    (used as the light ingest-time verify AND the read-time staleness check)."""
    if not line:
        return None
    lines = _read_lines(project_root, file)
    if lines is None or line < 1 or line > len(lines):
        return None
    return _line_hash(lines[line - 1])


def _hash_index(
    project_root: Path, file: str | None, cache: dict | None = None
) -> dict[str, list[int]]:
    """Every line hash in a file mapped to the line numbers carrying it.

    Built once per file per call via `cache`: without it, a store where many facts point at
    the same edited file re-reads and re-hashes that file once per fact.
    """
    if cache is not None and file in cache:
        return cache[file]
    lines = _read_lines(project_root, file)
    index: dict[str, list[int]] = {}
    for number, text in enumerate(lines or [], 1):
        index.setdefault(_line_hash(text), []).append(number)
    if cache is not None:
        cache[file] = index
    return index


def current_anchor_line(
    project_root: Path,
    file: str | None,
    line: int | None,
    anchor_hash: str | None,
    cache: dict | None = None,
) -> int | None:
    """Where this fact's anchor text lives NOW, or None if the fact can no longer be placed.

    Staleness used to be decided by one question — does line N still hash to the recorded
    value? — which conflates two very different events. A line whose CONTENT changed is a
    fact that may well have stopped being true. A line that merely MOVED, because something
    was inserted above it, is the same source text at a new index: the fact is intact and
    only the lookup is wrong. The first question was answering both, so inserting a block at
    the top of a file silently invalidated every fact anchored below it.

    So: try the recorded line first, and if that fails, look for the recorded hash elsewhere
    in the file. A UNIQUE match is a relocation — same text, new position, nothing about the
    claim weakened. Anything else is still a drop.

    The uniqueness rule is what keeps this honest. Anchor text like a lone `)`, or a bare
    docstring delimiter, hashes identically on dozens of lines, and picking the first would
    hand back a fact pointing at unrelated code with full confidence. An anchor that cannot
    single out a line never identified anything in the first place; it is dropped exactly as
    before.
    """
    if not anchor_hash:
        return None
    if line and _anchor_hash(project_root, file, line) == anchor_hash:
        return line
    matches = _hash_index(project_root, file, cache).get(anchor_hash) or []
    return matches[0] if len(matches) == 1 else None


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
    # Per-writer tmp name: a fixed ".tmp" would let two concurrent savers clobber each
    # other's staging file before the atomic replace. The lock serialises the real writers,
    # but this keeps the swap safe even if a save ever runs outside the lock.
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in facts) + ("\n" if facts else ""),
        encoding="utf-8",
    )
    tmp.replace(path)


def _recurrence_counts(
    project_root: Path,
    exclude_session_id: str | None = None,
    injected: set[str] | None = None,
) -> dict[str, int]:
    """normalized grounded claim -> number of DISTINCT sessions whose logs contain it.

    The CURRENT session is excluded: its output.raw.md is written before ingest runs,
    so counting it lets a fact that was injected as a KNOWN_FACT (and merely echoed
    back by second_agent) raise its own recurrence — evidence laundering, not support.

    `injected` extends that guard ACROSS sessions: any claim already in the store gets
    injected as a KNOWN_FACT into every later run, so its reappearance in those runs'
    `grounded` blocks is an echo, not independent support. Such claims are skipped in all
    sessions, not just the current one.
    """
    workflow_dir = workflow_paths(project_root)["workflow_dir"]
    sessions = workflow_dir / "sessions"
    if not sessions.exists():
        return {}
    excluded = _safe_component(exclude_session_id) if exclude_session_id else None
    injected_norms = injected or set()
    cache = _load_claim_cache(workflow_dir)
    dirty = False
    per_claim: dict[str, set[str]] = {}
    for sdir in sessions.iterdir():
        logs = sdir / "logs"
        if not (sdir.is_dir() and logs.exists()):
            continue
        if excluded and sdir.name == excluded:
            continue
        runs = sorted((p for p in logs.iterdir() if p.is_dir()), key=lambda p: p.name)
        fingerprint_parts: list[str] = []
        for run in runs:
            out = run / "output.raw.md"
            try:
                stat = out.stat()
                fingerprint_parts.append(
                    f"{run.name}:{stat.st_mtime_ns}:{stat.st_size}"
                )
            except OSError:
                fingerprint_parts.append(f"{run.name}:missing")
        fingerprint = hashlib.sha256(
            "\n".join(fingerprint_parts).encode("utf-8")
        ).hexdigest()[:24]
        entry = cache.get(sdir.name)
        if entry and entry.get("fingerprint") == fingerprint:
            seen = set(entry.get("claims") or [])
        else:
            seen = set()
            for run in runs:
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
            cache[sdir.name] = {"fingerprint": fingerprint, "claims": sorted(seen)}
            dirty = True
        # The injected filter is applied AFTER the cache, never baked into it: it varies
        # per call, and a cache that folded it in would answer the wrong question later.
        for norm in seen - injected_norms:
            per_claim.setdefault(norm, set()).add(sdir.name)

    if dirty:
        _save_claim_cache(workflow_dir, cache, {p.name for p in sessions.iterdir()})
    return {claim: len(dirs) for claim, dirs in per_claim.items()}


_CLAIM_CACHE_FILE = "recurrence-cache.json"


def _load_claim_cache(workflow_dir: Path) -> dict:
    try:
        data = json.loads(
            (workflow_dir / _CLAIM_CACHE_FILE).read_text(encoding="utf-8")
        )
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_claim_cache(workflow_dir: Path, cache: dict, live: set[str]) -> None:
    """Best-effort write, dropping sessions that no longer exist.

    Pruning here rather than never: `prune_jobs` deletes old session dirs, and a cache
    that only grew would outlive the data it describes.
    """
    try:
        pruned = {k: v for k, v in cache.items() if k in live}
        path = workflow_dir / _CLAIM_CACHE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pruned), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def ingest(project_root: Path, content: str, session_id: str) -> int:
    """Promote qualifying claims from one run's output into the fact store. Returns count added.

    The whole read-modify-write runs under _FactLock so a concurrent ingest in another
    session cannot overwrite this one's additions.
    """
    with _FactLock(project_root):
        loaded = _load_facts(project_root)
        existing = _dedupe(loaded)
        collapsed = len(loaded) - len(existing)
        seen: dict[str | None, list[dict]] = {}
        for fact in existing:
            sig = _fact_signature(fact)
            if sig["words"]:
                seen.setdefault(fact.get("file"), []).append(sig)
        added = 0

        def _try_add(
            raw_claim: str, category: str, origin: str = ORIGIN_DISCOVERED
        ) -> None:
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
                    "origin": origin,
                    "captured_at": now_iso(),
                    "session": session_id,
                }
            )
            added += 1

        # Everything already stored is exactly what load_relevant injects as a KNOWN_FACT
        # into later runs, so its reappearance in their `grounded` is an echo, not support.
        # Feed that set to recurrence so an injected fact can never re-promote itself.
        injected = {_normalize(f.get("claim", "")) for f in existing}

        # 1) explicit durable facts (config/pattern/invariant) — read from THIS run's output
        for item in _parse_block(content, "durable_facts"):
            _try_add(item, _extract_category(item) or "invariant")

        # 2) grounded claims recurring across >= threshold distinct OTHER sessions
        threshold = _policy_int(
            project_root, "fact_recurrence_threshold", RECURRENCE_THRESHOLD
        )
        counts = _recurrence_counts(
            project_root, exclude_session_id=session_id, injected=injected
        )
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
    index_cache: dict = {}
    for f in facts:
        placed = current_anchor_line(
            project_root, f.get("file"), f.get("line"), f.get("anchor_hash"), index_cache
        )
        if placed is None:
            continue  # stale/invalid/unplaceable → drop, do not inject
        if placed != f.get("line"):
            # Serve the line the anchor text sits on today. Copied rather than mutated: the
            # dicts come from _load_facts and writing through them here would edit the store
            # from a read path. Persisting the move is `prune`'s job, which holds the lock.
            f = {**f, "line": placed}
        blob = f"{f.get('claim', '')} {f.get('file') or ''}".lower()
        overlap = len(task_words & set(re.findall(r"[a-z0-9_]{3,}", blob)))
        if overlap < MIN_RELEVANCE_OVERLAP:
            continue  # zero-overlap == irrelevant; do not pad the top-N with noise
        scored.append((overlap, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:limit]]


def prune(project_root: Path) -> dict:
    """Drop unplaceable facts, RELOCATE the ones that only moved, and collapse duplicates.

    This is the only path that deletes, so it is also the only sensible place to write a
    relocation back. `load_relevant` recomputes the move on every read and throws it away —
    correct for a read path, but it means a shifted fact pays the file scan forever. Here the
    lock is already held and the file is already being rewritten, so the new line number
    persists and later reads hit the fast path again.
    """
    with _FactLock(project_root):
        facts = _load_facts(project_root)
        index_cache: dict = {}
        fresh: list[dict] = []
        relocated = 0
        for f in facts:
            placed = current_anchor_line(
                project_root,
                f.get("file"),
                f.get("line"),
                f.get("anchor_hash"),
                index_cache,
            )
            if placed is None:
                continue
            if placed != f.get("line"):
                f = {**f, "line": placed}
                relocated += 1
            fresh.append(f)
        deduped = _dedupe(fresh)
        if len(deduped) != len(facts) or relocated:
            _save_facts(project_root, deduped)
        return {
            "kept": len(deduped),
            "removed": len(facts) - len(fresh),
            "relocated": relocated,
            "duplicates_collapsed": len(fresh) - len(deduped),
        }
