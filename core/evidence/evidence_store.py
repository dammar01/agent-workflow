"""Persistent, staleness-checked index over immutable evidence artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from core.evidence.fact_store import _anchor_hash, _FILELINE, current_anchor_line
from core.workspace.workspace_paths import _safe_component, now_iso, workflow_paths
from utils.redact import redact, redact_value

EVIDENCE_FILENAME = "evidence.jsonl"
LOCK_FILENAME = "evidence.jsonl.lock"
LOCK_TIMEOUT_SECONDS = 30
MAX_EVIDENCE = 200
MAX_ANCHORS_PER_ARTIFACT = 40

_EVIDENCE_THREAD_LOCK = threading.Lock()


def _path(project_root: Path) -> Path:
    return workflow_paths(Path(project_root))["workflow_dir"] / EVIDENCE_FILENAME


class _EvidenceLock:
    """Serialize evidence mutations with a persistent native file lock."""

    def __init__(self, project_root: Path):
        self.path = _path(project_root).with_name(LOCK_FILENAME)
        self.handle = None
        self.thread_locked = False
        self.file_locked = False

    def __enter__(self) -> "_EvidenceLock":
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        remaining = max(0.0, deadline - time.monotonic())
        if not _EVIDENCE_THREAD_LOCK.acquire(timeout=remaining):
            raise TimeoutError("timed out waiting for evidence index lock")
        self.thread_locked = True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"\0")
                self.handle.flush()

            while not self.file_locked:
                try:
                    self.handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(
                            self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                        )
                    self.file_locked = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for evidence index lock")
                    time.sleep(0.01)
            return self
        except Exception:
            self._release()
            raise

    def __exit__(self, *exc) -> None:
        self._release()

    def _release(self) -> None:
        try:
            if self.file_locked and self.handle is not None:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.file_locked = False
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            if self.thread_locked:
                self.thread_locked = False
                _EVIDENCE_THREAD_LOCK.release()


def _context_json(context: dict | None) -> str:
    return json.dumps(
        context or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _context_hash(context: dict | None) -> str:
    return hashlib.sha256(_context_json(context).encode("utf-8")).hexdigest()[:24]


def _query_hash(command: str, task: str | None, context: dict | None = None) -> str:
    """Hash the literal task plus the effective delegated execution context."""
    payload = {
        "command": str(command or "").strip().lower(),
        "task": task or "",
        "context": context or {},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _immutable_artifact_path(project_root: Path, entry: dict) -> Path | None:
    value = entry.get("artifact_path")
    if not value or not entry.get("artifact_hash"):
        return None
    try:
        artifact = Path(value).resolve()
        workflow_dir = workflow_paths(Path(project_root))["workflow_dir"].resolve()
        relative = artifact.relative_to(workflow_dir)
    except (OSError, ValueError):
        return None
    parts = relative.parts
    expected_session = _safe_component(str(entry.get("session") or ""))
    if (
        len(parts) != 5
        or parts[0] != "sessions"
        or parts[1] != expected_session
        or parts[2] != "logs"
        or not parts[3]
        or parts[4] != "output.raw.md"
    ):
        return None
    return artifact


def read_artifact_with_redactions(
    project_root: Path, entry: dict
) -> tuple[str | None, list[dict]]:
    """Read a hash-checked artifact and report any defensive redactions."""
    artifact = _immutable_artifact_path(project_root, entry)
    if artifact is None or not artifact.is_file():
        return None, []
    try:
        content = artifact.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, []
    if _content_hash(content) != entry.get("artifact_hash"):
        return None, []
    return redact(content)


def read_artifact(project_root: Path, entry: dict) -> str | None:
    """Read an indexed artifact only when its location and digest are unchanged."""
    return read_artifact_with_redactions(project_root, entry)[0]


def _extract_anchors(project_root: Path, content: str) -> tuple[list[dict], bool]:
    """Return hashed unique anchors and whether every citation was certifiable."""
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
            return anchors, False
        if len(anchors) >= MAX_ANCHORS_PER_ARTIFACT:
            return anchors, False
        anchors.append({"file": file, "line": line, "hash": h})
    return anchors, True


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
    tmp = p.with_name(
        f"{p.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    try:
        tmp.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _is_fresh(project_root: Path, entry: dict) -> bool:
    """An artifact is fresh only if EVERY cited anchor still hashes to the recorded value.

    No anchors -> cannot certify -> not fresh (never serve unverifiable cached evidence).
    """
    if read_artifact(project_root, entry) is None:
        return False
    anchors = entry.get("anchors") or []
    if not anchors or entry.get("anchors_complete") is not True:
        return False
    # An anchor that merely MOVED still points at the same source text, so it does not make
    # the artifact stale — see `fact_store.current_anchor_line`. This matters more here than
    # it does for a single fact: freshness demands EVERY anchor hold, so one insertion above
    # one cited line was enough to discard a whole piece of evidence and re-run the call.
    index_cache: dict = {}
    for a in anchors:
        if (
            current_anchor_line(
                project_root, a.get("file"), a.get("line"), a.get("hash"), index_cache
            )
            is None
        ):
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
    context: dict | None = None,
) -> dict:
    """Index one delegated evidence artifact. Returns the stored entry."""
    _, content_redactions = redact(content)
    if content_redactions:
        raise ValueError("evidence content must be redacted before indexing")
    safe_session, _ = redact(session_id)
    resolved_artifact = None
    if artifact_path:
        raw_artifact = Path(artifact_path)
        resolved_artifact = (
            raw_artifact.resolve()
            if raw_artifact.is_absolute()
            else (Path(project_root) / raw_artifact).resolve()
        )
    provisional = {
        "artifact_path": str(resolved_artifact) if resolved_artifact else None,
        "artifact_hash": _content_hash(content),
        "session": safe_session,
    }
    if _immutable_artifact_path(project_root, provisional) is None:
        raise ValueError("evidence artifact must be an immutable logs/<run>/output.raw.md")
    try:
        stored_content = resolved_artifact.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError("evidence artifact is not readable") from exc
    if _content_hash(stored_content) != provisional["artifact_hash"]:
        raise ValueError("evidence artifact content does not match delegated output")
    task_preview, _ = redact((task or "")[:200])
    safe_digest, _ = redact_value(digest)
    safe_command, _ = redact(command)
    anchors, anchors_complete = _extract_anchors(project_root, content)
    entry = {
        "query_hash": _query_hash(command, task, context),
        "context_hash": _context_hash(context),
        "command": safe_command,
        "task_preview": task_preview,
        "digest": safe_digest,
        **provisional,
        "anchors": anchors,
        "anchors_complete": anchors_complete,
        "captured_at": now_iso(),
        "session": safe_session,
    }
    with _EvidenceLock(project_root):
        rows = _load(project_root)
        rows.append(entry)
        if len(rows) > MAX_EVIDENCE:
            rows = rows[-MAX_EVIDENCE:]
        _save(project_root, rows)
    return entry


def find_fresh(
    project_root: Path,
    command: str,
    task: str | None,
    context: dict | None = None,
) -> dict | None:
    """Most-recent artifact for this EXACT query whose anchors are all still fresh.

    If the most-recent match is stale, return None (re-delegate) rather than reaching for
    an older, necessarily-staler entry.
    """
    qh = _query_hash(command, task, context)
    with _EvidenceLock(project_root):
        rows = _load(project_root)
        usable = [
            entry
            for entry in rows
            if _immutable_artifact_path(project_root, entry) is not None
        ]
        if len(usable) != len(rows):
            _save(project_root, usable)
    for entry in reversed(usable):
        if entry.get("query_hash") == qh:
            return entry if _is_fresh(project_root, entry) else None
    return None
