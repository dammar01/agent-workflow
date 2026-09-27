"""What earlier browser runs learned about the application, reused by the next draft.

The browser-side counterpart of `core/evidence/fact_store.py`, at the same level: written
automatically from runs, anchored where it can be, aged out on its own, never tracked by
Git. Knowledge worth sharing is lifted into Git through `/.promote`
(`promotable_claims`), exactly as facts are.

What a run records, per application origin (scheme://host:port of base_url):

  auth.login    the steps from the login page's goto through the first assertion after the
                password was typed — how this app signs in
  auth.logout   the step that signed out, when a later assertion proved the login page came
                back
  navigation    a route a goto reached
  page_ready    a route's readiness conditions that held
  selector      a selector that matched exactly one element for an action on a route

Only what a run PROVED is recorded: a step that passed, in a run that passed on its first
attempt, that wrote nothing. A step that fails against a recorded entry counts against it,
and STALE_AFTER_FAILS consecutive failures retire it — the page changed, and a hint that
no longer matches is worse than no hint.

Nothing here holds a credential. Entries are built from the PLACEHOLDER scenario the
runner archives (`${E2E_USER}`, never its value), and a `fill` value that is not a
placeholder is dropped rather than stored: typed test data is not application knowledge.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from core.workspace.workspace_paths import now_iso, workflow_paths
from utils.owned_lock import OwnedFileLock

FILENAME = "e2e-knowledge.jsonl"
LOCK_FILENAME = "e2e-knowledge.jsonl.lock"
SIDECAR_NAME = "e2e_knowledge.json"
LOCK_TTL_SECONDS = 30
MAX_ENTRIES = 300
# Two failures in a row: once can be a slow page, twice at the same place is a changed page.
STALE_AFTER_FAILS = 2
# Proven this many times before it may leave the project as Git-tracked knowledge.
PROMOTE_AFTER_PASSES = 3
PER_KIND_LIMIT = 5
REPEAT_FAILURE = "repeat_failure"
KIND_ORDER = (REPEAT_FAILURE, "auth.login", "auth.logout", "navigation", "page_ready", "selector")
_PLACEHOLDER = re.compile(r"^\$\{[A-Z0-9_]+\}$")
_PASSWORD_PLACEHOLDER = "${E2E_PASS}"
_ASSERTIONS = frozenset({"expect_url", "expect_dom", "expect_title"})
# Only what locates an element or proves a page state. Values, markers, and write
# declarations are left behind.
_STEP_KEYS = ("action", "url", "selector", "selector_candidates", "within", "ready", "contains", "equals", "matches", "text", "key")


def _store_path(project_root: Path) -> Path:
    return workflow_paths(Path(project_root))["e2e_knowledge"]


class _Lock(OwnedFileLock):
    """O_EXCL lock around the read-modify-write, with the fact store's ownership rules."""

    def __init__(self, project_root: Path) -> None:
        super().__init__(_store_path(project_root).with_name(LOCK_FILENAME), LOCK_TTL_SECONDS)


def load(project_root: Path) -> list[dict]:
    path = _store_path(project_root)
    if not path.exists():
        return []
    entries: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            entries.append(entry)
    return entries


def _save(project_root: Path, entries: list[dict]) -> None:
    path = _store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Newest verification last, so the cap drops what has gone longest unproven.
    entries = sorted(entries, key=lambda e: str(e.get("last_verified") or e.get("first_seen") or ""))[-MAX_ENTRIES:]
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in entries), encoding="utf-8")
    os.replace(tmp, path)


def origin_of(base_url: str) -> str:
    parts = urlsplit(str(base_url or ""))
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}" if parts.scheme and parts.netloc else ""


def _route(url: object, base_url: str) -> str:
    try:
        return urlsplit(urljoin(base_url or "http://localhost/", str(url or "/"))).path or "/"
    except ValueError:
        return "/"


def _entry_id(kind: str, origin: str, key: str) -> str:
    return hashlib.sha256(f"{kind}\0{origin}\0{key}".encode("utf-8")).hexdigest()[:16]


def _step_view(step: dict) -> dict:
    """The part of a step worth remembering, in placeholder form only."""
    view = {key: step[key] for key in _STEP_KEYS if key in step}
    value = step.get("value")
    if isinstance(value, str) and _PLACEHOLDER.match(value):
        view["value"] = value
    return view


def _uses_password(step: dict) -> bool:
    return step.get("value") == _PASSWORD_PLACEHOLDER


def _anchor(project_root: Path, ref: object) -> tuple[str | None, int | None, str | None]:
    from core.evidence.fact_store import _anchor_hash

    match = re.match(r"^(.+):(\d+)$", str(ref or "").strip())
    if not match:
        return None, None, None
    path, line = match.group(1), int(match.group(2))
    return path, line, _anchor_hash(project_root, path, line)


def observations(report: dict, scenario: dict, base_url: str) -> list[dict]:
    """What one run proved and disproved, as `{kind, key, route, outcome, body}` rows.

    `scenario` is the placeholder form. Steps are matched to the report's trail by id, which
    the spec requires to be stable.
    """
    steps = [s for s in (scenario or {}).get("steps") or [] if isinstance(s, dict)]
    status = {str(row.get("step_id")): row for row in report.get("trail") or []}
    rows: list[dict] = []
    route = "/"
    login: dict | None = None
    for index, step in enumerate(steps):
        row = status.get(str(step.get("id")))
        if row is None or row.get("status") not in ("passed", "failed"):
            continue
        outcome = "pass" if row["status"] == "passed" else "fail"
        writes = bool(step.get("side_effect") or step.get("request"))
        action = step.get("action")
        if action == "goto":
            route = _route(step.get("url"), base_url)
            rows.append({"kind": "navigation", "key": route, "route": route, "outcome": outcome, "body": {"url": step.get("url")}})
            if isinstance(step.get("ready"), list) and step["ready"]:
                rows.append({"kind": "page_ready", "key": route, "route": route, "outcome": outcome, "body": {"ready": step["ready"]}})
        elif isinstance(step.get("ready"), list) and step["ready"] and not writes:
            rows.append({"kind": "page_ready", "key": route, "route": route, "outcome": outcome, "body": {"ready": step["ready"]}})
        selection = row.get("selection") or {}
        if action not in ("goto", "probe") and not writes and (step.get("selector") or step.get("selector_candidates")):
            key = json.dumps([route, action, step.get("within"), step.get("selector") or step.get("selector_candidates")], sort_keys=True)
            rows.append(
                {
                    "kind": "selector",
                    "key": key,
                    "route": route,
                    "outcome": outcome,
                    "body": {"step": _step_view(step), "source_ref": selection.get("source_ref")},
                }
            )
        if _uses_password(step) and outcome == "pass" and login is None:
            start = max((i for i in range(index + 1) if steps[i].get("action") == "goto"), default=None)
            if start is not None:
                login = {"start": start, "password_at": index, "route": _route(steps[start].get("url"), base_url)}
        if login is not None and "end" not in login and index > login["password_at"] and action in _ASSERTIONS:
            login["end"] = index
    if login is not None and "end" in login:
        sequence = steps[login["start"] : login["end"] + 1]
        passed = all(status.get(str(s.get("id")), {}).get("status") == "passed" for s in sequence)
        rows.append(
            {
                "kind": "auth.login",
                "key": login["route"],
                "route": login["route"],
                "outcome": "pass" if passed else "fail",
                "body": {"steps": [_step_view(s) for s in sequence]},
            }
        )
        # A later action followed by an assertion that the login page is back is the logout.
        for index in range(login["end"] + 1, len(steps) - 1):
            action_step, check = steps[index], steps[index + 1]
            if action_step.get("action") not in ("click", "press") or check.get("action") != "expect_url":
                continue
            expected = str(check.get("contains") or check.get("equals") or "")
            if not expected or login["route"] not in _route(expected, base_url):
                continue
            outcome_ok = all(status.get(str(s.get("id")), {}).get("status") == "passed" for s in (action_step, check))
            rows.append(
                {
                    "kind": "auth.logout",
                    "key": login["route"],
                    "route": login["route"],
                    "outcome": "pass" if outcome_ok else "fail",
                    "body": {"steps": [_step_view(action_step), _step_view(check)]},
                }
            )
            break
    return rows


def ingest(project_root: Path, report: dict, scenario: dict, base_url: str, session_id: str, *, clean_first_attempt: bool) -> dict:
    """Fold one run into the store. Returns `{added, confirmed, weakened, retired}`.

    A pass is recorded only when the run passed on its first attempt: a pass reached by a
    retry is the flaky result this package refuses to call clean. Failures always count —
    a failure against a recorded entry is information whatever the run's verdict.
    """
    origin = origin_of(base_url)
    summary = {"added": 0, "confirmed": 0, "weakened": 0, "retired": 0}
    if not origin:
        return summary
    rows = observations(report, scenario, base_url)
    if not rows:
        return summary
    now = now_iso()
    with _Lock(project_root):
        entries = {entry["id"]: entry for entry in load(project_root)}
        for row in rows:
            entry_id = _entry_id(row["kind"], origin, row["key"])
            entry = entries.get(entry_id)
            if row["outcome"] == "fail":
                if entry is None or entry.get("stale"):
                    continue
                entry["fail_count"] = int(entry.get("fail_count") or 0) + 1
                entry["consecutive_fails"] = int(entry.get("consecutive_fails") or 0) + 1
                summary["weakened"] += 1
                if entry["consecutive_fails"] >= STALE_AFTER_FAILS:
                    entry["stale"] = True
                    summary["retired"] += 1
                continue
            if not clean_first_attempt:
                continue
            if entry is None:
                entry = {
                    "id": entry_id,
                    "kind": row["kind"],
                    "origin": origin,
                    "route": row["route"],
                    "pass_count": 0,
                    "fail_count": 0,
                    "consecutive_fails": 0,
                    "stale": False,
                    "first_seen": now,
                }
                entries[entry_id] = entry
                summary["added"] += 1
            else:
                summary["confirmed"] += 1
            entry.update(row["body"])
            ref = row["body"].get("source_ref")
            if ref:
                path, line, anchor = _anchor(project_root, ref)
                entry.update({"file": path, "line": line, "anchor_hash": anchor})
            entry["pass_count"] = int(entry.get("pass_count") or 0) + 1
            entry["consecutive_fails"] = 0
            entry["stale"] = False
            entry["last_verified"] = now
            entry["last_session"] = session_id
        _save(project_root, list(entries.values()))
    return summary


def note_repeat_failure(project_root: Path, origin: str, record: dict, session_id: str) -> None:
    """Remember that runs against `origin` kept ending the same way.

    Written when the repeat brake reaches its limit. The brake itself lives in one
    session's state and lets go when the project changes; this entry is what outlives
    both, so the next draft — in this session or a new one — reads that the environment
    was failing, and why, before it proposes the same run again. A hint, never a gate.
    """
    bucket = str(record.get("bucket") or "")
    if not origin or not bucket:
        return
    now = now_iso()
    with _Lock(project_root):
        entries = {entry["id"]: entry for entry in load(project_root)}
        # One entry per distinct app failure, not one per bucket: two different failures
        # are two different things for the next draft to read. The environment buckets
        # carry no signature and keep one entry each.
        signature = str(record.get("signature") or "")
        entry_id = _entry_id(REPEAT_FAILURE, origin, f"{bucket}\0{signature}" if signature else bucket)
        entry = entries.get(entry_id) or {"id": entry_id, "kind": REPEAT_FAILURE, "origin": origin, "first_seen": now, "pass_count": 0}
        entry.update(
            {
                "bucket": bucket,
                "reason": record.get("reason"),
                "streak": int(record.get("streak") or 0),
                "source": record.get("source"),
                "fingerprint": record.get("fingerprint"),
                "signature": record.get("signature"),
                "stale": False,
                "last_seen": now,
                "last_verified": now,
                "last_session": session_id,
                "note": "Runs against this origin kept failing the same way; the cause was outside the scenario. Check the environment named by `bucket`/`reason` before drafting the same flow.",
            }
        )
        entries[entry_id] = entry
        _save(project_root, list(entries.values()))


def resolve_repeat_failures(project_root: Path, origin: str) -> int:
    """A passing run against `origin` retires its repeat-failure entries; returns how many."""
    if not origin or not _store_path(project_root).exists():
        return 0
    now = now_iso()
    resolved = 0
    with _Lock(project_root):
        entries = load(project_root)
        for entry in entries:
            if entry.get("kind") == REPEAT_FAILURE and entry.get("origin") == origin and not entry.get("stale"):
                entry.update({"stale": True, "resolved_at": now})
                resolved += 1
        if resolved:
            _save(project_root, entries)
    return resolved


def _uses_retired_selector(entry: dict) -> bool:
    """An entry recorded before `testid` was retired. Offering it would hand the draft a
    selector the validator now refuses, so it is left out rather than rewritten: whether
    the element also carries `data-e2e` is something only the source can say."""
    from core.evidence.e2e.spec import RETIRED_SELECTOR_KEYS

    found: list[bool] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            if any(key in RETIRED_SELECTOR_KEYS for key in item):
                found.append(True)
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    visit(entry)
    return bool(found)


def relevant(project_root: Path, base_url: str) -> list[dict]:
    """Live entries for this origin, the best proven first, bounded per kind."""
    origin = origin_of(base_url)
    live = [
        e for e in load(project_root)
        if e.get("origin") == origin and not e.get("stale") and not _uses_retired_selector(e)
    ]
    chosen: list[dict] = []
    for kind in KIND_ORDER:
        of_kind = sorted((e for e in live if e.get("kind") == kind), key=lambda e: (-int(e.get("pass_count") or 0), str(e.get("route"))))
        chosen.extend(of_kind[:PER_KIND_LIMIT])
    return [{k: v for k, v in entry.items() if k not in ("last_session",)} for entry in chosen]


def write_sidecar(project_root: Path, session_id: str, base_url: str) -> int:
    """Write the draft's `e2e_knowledge.json`; returns how many entries it offers."""
    entries = relevant(project_root, base_url)
    runtime_dir = workflow_paths(Path(project_root), session_id)["runtime_dir"]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "origin": origin_of(base_url),
        "note": (
            "Hints from earlier browser runs against this origin. Re-derive every selector "
            "from the code before using it; an entry whose file:line no longer matches is stale."
        ),
        "entries": entries,
    }
    tmp = runtime_dir / f"{SIDECAR_NAME}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, runtime_dir / SIDECAR_NAME)
    return len(entries)


def prune(project_root: Path) -> dict:
    """Drop retired entries and entries whose anchored source line is gone."""
    from core.evidence.fact_store import current_anchor_line

    if not _store_path(project_root).exists():
        return {"kept": 0, "removed": 0}
    with _Lock(project_root):
        entries = load(project_root)
        cache: dict = {}
        kept = []
        for entry in entries:
            if entry.get("stale"):
                continue
            if entry.get("anchor_hash"):
                placed = current_anchor_line(project_root, entry.get("file"), entry.get("line"), entry.get("anchor_hash"), cache)
                if placed is None:
                    continue
                entry["line"] = placed
            kept.append(entry)
        _save(project_root, kept)
        return {"kept": len(kept), "removed": len(entries) - len(kept)}


def promotable_claims(project_root: Path, base_url: str | None = None) -> list[dict]:
    """Entries proven often enough to become Git-tracked knowledge, as /.promote claims.

    Only anchored entries qualify: a claim /.promote writes must point at code it can
    re-check, and an unanchored route is a runtime observation with nothing to verify.
    """
    from core.knowledge.verify import anchor_for

    origin = origin_of(base_url) if base_url else None
    claims: list[dict] = []
    for entry in load(project_root):
        if entry.get("stale") or int(entry.get("pass_count") or 0) < PROMOTE_AFTER_PASSES:
            continue
        if origin and entry.get("origin") != origin:
            continue
        if not entry.get("file") or not entry.get("line"):
            continue
        path, line = str(entry["file"]), int(entry["line"])
        step = entry.get("step") or {}
        claims.append(
            {
                "id": f"e2e-{entry['kind'].replace('.', '-')}-{entry['id'][:8]}",
                "statement": (
                    f"On {entry.get('route')}, the {step.get('action', 'step')} target "
                    f"{json.dumps(step.get('selector') or step.get('selector_candidates'), ensure_ascii=False)} "
                    f"matched exactly one element in {entry['pass_count']} browser runs."
                ),
                "sources": [{"type": "code", "path": path, "line": line, "anchor": anchor_for(project_root, path, line)}],
            }
        )
    return claims
