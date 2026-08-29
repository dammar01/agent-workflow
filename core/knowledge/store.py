"""Reading, deterministic serialisation, and the single gated write path.

Everything that ends up in a Git-tracked knowledge file goes through `write()`. That is
the whole design: one door, with the gates on it, so a future caller cannot reach the
file by a route that skips validation.

Determinism is not cosmetic here. The file is reviewed as a diff and merged between
developers, so a promotion that changes nothing must produce zero bytes of change.
Rebuilding every document against a fixed key order and sorting every array is what
makes "no semantic change" and "no diff" the same event.
"""

import json
import os
import re
import time
from pathlib import Path

from core.knowledge import schema
from core.workspace.workspace_paths import (
    atomic_write_text,
    read_json_file,
    workflow_paths,
)
from utils import git
from utils.redact import scan

DEFAULT_KNOWLEDGE_DIR = "docs/project-knowledge"
SCHEMA_FILENAME = "promoted-knowledge.schema.json"
LOCK_FILENAME = "promote.lock"
LOCK_TTL_SECONDS = 30

_JSON_INDENT = 2


class _KnowledgeLock:
    """Cross-process advisory lock around one knowledge directory.

    Same shape and same failure posture as core/evidence/fact_store.py's `_FactLock`,
    for the same reason: two sessions promoting the same subject each read, mutate, and
    rewrite a document, and without serialisation the second write silently drops the
    first. Deliberately a copy of that pattern rather than an import — fact_store's lock
    is bound to facts.jsonl's own path, and generalising it would mean editing a verified
    file to serve a caller it was not written for.

    Best-effort, never blocking forever: a lock older than the TTL is treated as orphaned
    and stolen. On Windows a live holder's file cannot be unlinked while its handle is
    open, so only a genuinely dead lock is ever taken.

    The lock file lives under .workflow/, not beside the documents it guards. The
    knowledge directory is Git-tracked, and a lock file there would surface in every
    `git status` and eventually in someone's commit.
    """

    def __init__(self, project_root: Path):
        self.path = workflow_paths(Path(project_root))["workflow_dir"] / LOCK_FILENAME
        self.fd: int | None = None

    def __enter__(self) -> "_KnowledgeLock":
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
            return True

    def _steal(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        self._steal()


def knowledge_dir(project_root: Path) -> Path:
    """Where promoted knowledge lives, from policies.knowledge_dir.

    Read from config on every call rather than cached: the same runtime serves several
    projects, and a directory resolved once would follow the first one seen.
    """
    name = DEFAULT_KNOWLEDGE_DIR
    try:
        config = read_json_file(workflow_paths(project_root)["config"])
        policies = config.get("policies")
        if isinstance(policies, dict):
            candidate = policies.get("knowledge_dir")
            if isinstance(candidate, str) and candidate.strip():
                name = candidate.strip()
    except (OSError, ValueError):
        pass
    return Path(project_root) / name


def doc_path(project_root: Path, doc_id: str) -> Path:
    return knowledge_dir(project_root) / f"{doc_id}.json"


def _sorted_evidence_ids(value) -> list[str]:
    return sorted({str(item) for item in value}) if isinstance(value, list) else []


def _source_sort_key(source: dict) -> tuple:
    if source.get("type") == "code":
        lines = source.get("lines") or {}
        start = lines.get("start") if isinstance(lines, dict) else 0
        end = lines.get("end") if isinstance(lines, dict) else 0
        return (0, str(source.get("path", "")), int(start or 0), int(end or 0))
    return (1, str(source.get("session_id") or ""), str(source.get("text", "")))


def _canonical_source(source: dict) -> dict:
    keys = (
        schema.CODE_SOURCE_KEYS
        if source.get("type") == "code"
        else schema.USER_SOURCE_KEYS
    )
    out: dict = {}
    for key in keys:
        if key not in source:
            continue
        if key == "evidence_ids":
            ids = _sorted_evidence_ids(source[key])
            if ids:
                out[key] = ids
            continue
        if key == "lines" and isinstance(source[key], dict):
            out[key] = {k: source[key][k] for k in schema.LINES_KEYS if k in source[key]}
            continue
        out[key] = source[key]
    return out


def _canonical_claim(claim: dict) -> dict:
    out: dict = {}
    for key in schema.CLAIM_KEYS:
        if key not in claim:
            continue
        if key == "sources" and isinstance(claim[key], list):
            sources = [_canonical_source(s) for s in claim[key] if isinstance(s, dict)]
            out[key] = sorted(sources, key=_source_sort_key)
            continue
        out[key] = claim[key]
    return out


def _canonical_exclusion(exclusion: dict) -> dict:
    out: dict = {}
    for key in schema.EXCLUSION_KEYS:
        if key not in exclusion:
            continue
        if key == "affected_claims":
            affected = _sorted_evidence_ids(exclusion[key])
            if affected:
                out[key] = affected
            continue
        out[key] = exclusion[key]
    return out


def canonical(doc: dict) -> dict:
    """The document rebuilt in fixed key order with every array sorted.

    Applied on the way in AND on the way out, so a document assembled in any order by
    any caller serialises to the same bytes as the one already on disk.
    """
    out: dict = {}
    for key in schema.DOC_KEYS:
        if key not in doc:
            continue
        value = doc[key]
        if key == "production" and isinstance(value, dict):
            out[key] = {k: value[k] for k in schema.PRODUCTION_KEYS if k in value}
        elif key == "anchors" and isinstance(value, dict):
            paths = value.get("paths")
            out[key] = {
                "paths": sorted({str(p) for p in paths}) if isinstance(paths, list) else []
            }
        elif key == "claims" and isinstance(value, list):
            claims = [_canonical_claim(c) for c in value if isinstance(c, dict)]
            out[key] = sorted(claims, key=lambda c: str(c.get("id", "")))
        elif key == "applicability" and isinstance(value, dict):
            exclusions = value.get("excluded_branches")
            items = (
                [_canonical_exclusion(e) for e in exclusions if isinstance(e, dict)]
                if isinstance(exclusions, list)
                else []
            )
            out[key] = {
                "excluded_branches": sorted(items, key=lambda e: str(e.get("pattern", "")))
            }
        else:
            out[key] = value
    return out


def serialize(doc: dict) -> str:
    """Canonical JSON text, newline-terminated.

    `ensure_ascii=False` so a statement containing non-ASCII stays readable in the diff
    instead of turning into escape sequences a reviewer has to decode.
    """
    return json.dumps(canonical(doc), indent=_JSON_INDENT, ensure_ascii=False) + "\n"


def load(project_root: Path, doc_id: str) -> dict:
    """Read one knowledge document.

    Returns {"ok": True, "doc": ...}, {"ok": True, "doc": None} when absent, or
    {"ok": False, "error": ...} when the file exists but cannot be parsed. A malformed
    existing document must surface — silently treating it as absent would overwrite a
    colleague's work with a fresh file on the next promotion.
    """
    path = doc_path(project_root, doc_id)
    if not path.exists():
        return {"ok": True, "doc": None, "path": str(path)}
    try:
        return {"ok": True, "doc": json.loads(path.read_text(encoding="utf-8")), "path": str(path)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"{path} exists but cannot be read as JSON: {exc}", "path": str(path)}


def list_docs(project_root: Path) -> list[str]:
    directory = knowledge_dir(project_root)
    if not directory.is_dir():
        return []
    return sorted(
        p.stem for p in directory.glob("*.json") if p.name != SCHEMA_FILENAME
    )


def _ignored_sources(project_root: Path, doc: dict) -> list[str]:
    """Code-source paths git is told to ignore.

    Pulled forward from the hardening phase deliberately. Everything else in this file
    can be added later without consequence; a write path that will happily record a line
    from an ignored file into a Git-tracked document cannot, because the damage is
    committed history and history is the thing this feature makes permanent.
    """
    ignored = []
    for claim in doc.get("claims") or []:
        for source in claim.get("sources") or []:
            path = source.get("path")
            if source.get("type") == "code" and isinstance(path, str):
                if git.is_ignored(project_root, path) and path not in ignored:
                    ignored.append(path)
    return sorted(ignored)


def _secret_shaped(doc: dict) -> list[str]:
    """Credential shapes found in the text a human will read out of Git.

    Scanned and REFUSED rather than redacted, which is the opposite of what the evidence
    pipeline does with the same patterns — and deliberately so. Redaction is right for an
    artifact that is transient and machine-read: scrub the value, keep the run going. A
    promoted document is neither. A claim whose statement reads
    "the key is [REDACTED: openai-style key]" is not knowledge with a hole in it; it is a
    mistake that got through review wearing the appearance of having been handled.

    Only free text is scanned. Paths, ids and hashes are structural, and a repository
    path that happens to match a credential shape would block a legitimate promotion.
    """
    hits: list[str] = []
    for claim in doc.get("claims") or []:
        claim_id = claim.get("id", "?")
        hits += scan(str(claim.get("statement", "")), f"claims[{claim_id}].statement")
        for index, source in enumerate(claim.get("sources") or []):
            if isinstance(source, dict) and source.get("type") == "user":
                hits += scan(str(source.get("text", "")), f"claims[{claim_id}].sources[{index}].text")
    hits += scan(str(doc.get("summary", "")), "summary")
    return hits


def ensure_schema_file(project_root: Path) -> str:
    """Write the JSON Schema next to the documents when it is not already there.

    Shipped from a Python constant rather than through dist/ and the installer: the
    schema describes documents that live in the CONSUMER's repository, and reaching it
    through the tool's own install tree would make the contract available only to
    developers who installed the tool.
    """
    directory = knowledge_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SCHEMA_FILENAME
    want = json.dumps(schema.JSON_SCHEMA, indent=_JSON_INDENT, ensure_ascii=False) + "\n"
    try:
        if path.read_text(encoding="utf-8") == want:
            return str(path)
    except (OSError, ValueError):
        pass
    atomic_write_text(path, want)
    return str(path)


def write(project_root: Path, doc: dict) -> dict:
    """Validate, gate, and write one knowledge document.

    The only path to disk. Refuses on schema errors, on a source git is ignoring, and on
    a destination git is ignoring — the last because a knowledge directory that never
    reaches a commit turns the whole feature into a local cache nobody else can see,
    and does it without a single error message.
    """
    errors = schema.validate(doc)
    if errors:
        return {"ok": False, "error_type": "invalid_knowledge", "errors": errors}

    secrets_found = _secret_shaped(doc)
    if secrets_found:
        return {
            "ok": False,
            "error_type": "secret_shaped_content",
            "errors": [
                f"{hit}: refusing to write credential-shaped text into a Git-tracked file"
                for hit in secrets_found
            ],
        }

    ignored = _ignored_sources(project_root, doc)
    if ignored:
        return {
            "ok": False,
            "error_type": "ignored_source",
            "errors": [
                f"{path}: git ignores this path, so its content must not become "
                "Git-tracked knowledge"
                for path in ignored
            ],
        }

    directory = knowledge_dir(project_root)
    relative = directory.relative_to(project_root).as_posix() if directory.is_relative_to(project_root) else str(directory)
    if git.is_ignored(project_root, relative):
        return {
            "ok": False,
            "error_type": "ignored_destination",
            "errors": [
                f"{relative}: git ignores the knowledge directory, so nothing written "
                "there would ever be shared"
            ],
        }

    text = serialize(doc)
    path = doc_path(project_root, doc["id"])
    directory.mkdir(parents=True, exist_ok=True)

    # The compare-then-write is a read-modify-write like every other store here, so it
    # takes the lock for the same reason: two sessions promoting one subject would each
    # decide independently that their version is the one to keep.
    with _KnowledgeLock(project_root):
        unchanged = False
        try:
            unchanged = path.read_text(encoding="utf-8") == text
        except (OSError, ValueError):
            pass
        if not unchanged:
            atomic_write_text(path, text)
        ensure_schema_file(project_root)

    return {
        "ok": True,
        "path": str(path),
        "written": not unchanged,
        "claims": len(doc.get("claims") or []),
    }


def matches_branch(pattern: str, branch: str) -> bool:
    """Whether a branch-exclusion pattern covers this branch.

    Anchored on both ends so `feature/auth` excludes that branch and not
    `feature/authorization-rework`, which a bare `re.search` would have swept in.
    """
    try:
        return re.fullmatch(pattern, branch) is not None
    except re.error:
        return False
