"""Versioned workspace migrations, run by `upgrade` (and by init's auto-upgrade).

A migration is a step from one `LAYOUT_VERSION` to the next. Each is idempotent: it
decides from what is on disk whether there is anything to do, so a second upgrade is a
no-op and a half-finished one is finished or rolled back, never repeated on top of itself.

Layout 1 → 2 (3.7.0): everything internal moves from the `.workflow` root into
`.workflow/data/`, leaving the root to the files a person edits. In order:

  1. back the whole `.workflow` tree up to `data/backups/<stamp>/` (last BACKUPS_KEPT kept);
  2. take the fact, evidence, browser-knowledge and promote locks, so no writer is
     mid-rewrite while its file moves;
  3. move every internal item into `.workflow/data.migrating/`, then rename that to
     `data/` in one step — the moment `data/` exists every reader (runtime, hooks, run
     scripts) switches layout, so it must never exist half-filled;
  4. point the evidence index's archived artifact paths at their new location;
  5. delete the pre-session root leftovers (`state.json`, `scope.json`,
     `command-cache.json`, `runtime/`, `logs/`) and the old lock files;
  6. strip config.json to overrides (values equal to a default, unknown or retired keys);
  7. convert a `secrets.json` still in the pre-list shape, keeping its values.

A failure before step 3's rename moves everything back and removes the temporary
directory; the backup is kept either way and its path reported.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from pathlib import Path

from core.workspace.workspace_paths import (
    DATA_DIRNAME,
    WORKFLOW_DIRNAME,
    atomic_write_json,
    is_legacy_layout,
    read_json_file,
)

LAYOUT_VERSION = 2
BACKUPS_KEPT = 3
# Internal items a layout-1 workspace kept at the root.
_INTERNAL = (
    "sessions",
    "provider-sessions",
    "reports",
    "audit.jsonl",
    "usage.jsonl",
    "quality.jsonl",
    "redactions.jsonl",
    "evidence.jsonl",
    "facts.jsonl",
    "recurrence-cache.json",
    "capabilities.json",
    "graph-meta.json",
    "graph-stale.json",
    "e2e-knowledge.jsonl",
)
# Written before sessions existed and read by nothing since; kept only in the backup.
_OBSOLETE = ("state.json", "scope.json", "command-cache.json", "runtime", "logs")
_LOCK_FILES = ("facts.jsonl.lock", "evidence.jsonl.lock", "e2e-knowledge.jsonl.lock", "promote.lock")
# What a person edits, plus what the tooling regenerates in place.
_EDITABLE = ("config.json", "second_agent.json", "opencode.json", "e2e", ".gitignore", "current", DATA_DIRNAME)
_SCRIPT_STEMS = ("run", "check", "inspect")


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"


def _backup(workflow_dir: Path, target: Path) -> None:
    """Copy the whole workspace except a data directory (which is where backups live)."""
    target.mkdir(parents=True, exist_ok=True)
    for entry in workflow_dir.iterdir():
        if entry.name in (DATA_DIRNAME, f"{DATA_DIRNAME}.migrating"):
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)


def _prune_backups(backups: Path) -> None:
    try:
        stamps = sorted((p for p in backups.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError:
        return
    for old in stamps[:-BACKUPS_KEPT]:
        shutil.rmtree(old, ignore_errors=True)


@contextlib.contextmanager
def _store_locks(project_root: Path):
    """Every store writer's lock, taken at the paths the running layout uses."""
    from core.evidence.e2e.knowledge import _Lock as KnowledgeLock
    from core.evidence.evidence_store import _EvidenceLock
    from core.evidence.fact_store import _FactLock
    from core.knowledge.store import _KnowledgeLock

    with contextlib.ExitStack() as stack:
        for lock in (_FactLock, _EvidenceLock, KnowledgeLock, _KnowledgeLock):
            stack.enter_context(lock(project_root))
        yield


def _rewrite_evidence_paths(index: Path, old_root: Path, new_root: Path) -> int:
    """Point archived artifact paths at the data directory. Returns rows rewritten."""
    if not index.is_file():
        return 0
    old_prefix = str(old_root / "sessions")
    new_prefix = str(new_root / "sessions")
    rewritten = 0
    lines = []
    for raw in index.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            lines.append(raw)
            continue
        path = row.get("artifact_path") if isinstance(row, dict) else None
        if isinstance(path, str) and path.startswith(old_prefix):
            row["artifact_path"] = new_prefix + path[len(old_prefix):]
            rewritten += 1
        lines.append(json.dumps(row, ensure_ascii=False))
    tmp = index.with_name(f"{index.name}.{os.getpid()}.tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    os.replace(tmp, index)
    return rewritten


def _convert_secrets(path: Path, backup_dir: Path) -> bool:
    """Rewrite a pre-list `secrets.json` (`profiles` as an object) in the list shape.

    The one time the workflow writes this file, and only to carry the user's own values
    across a format change it made; the original is copied to the backup first.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        return False
    (backup_dir / "e2e").mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / "e2e" / "secrets.pre-list.json")
    data["profiles"] = [
        {"name": name, "credentials": values if isinstance(values, dict) else {}}
        for name, values in data["profiles"].items()
    ]
    atomic_write_json(path, data)
    return True


def migrate_to_data_layout(project_root: Path) -> dict:
    """Layout 1 → 2. Returns a report; raises only after putting everything back."""
    project_root = Path(project_root)
    workflow_dir = project_root / WORKFLOW_DIRNAME
    report: dict = {"from": 1, "to": 2, "moved": [], "removed": [], "stray": [], "config_stripped": [], "secrets_converted": False}
    if not is_legacy_layout(project_root):
        report["skipped"] = "already on the data layout"
        return report

    staging = workflow_dir / f"{DATA_DIRNAME}.migrating"
    if staging.exists():
        # A previous attempt died after staging and before its rename: put it back first.
        for entry in list(staging.iterdir()):
            if entry.name != "backups" and not (workflow_dir / entry.name).exists():
                shutil.move(str(entry), str(workflow_dir / entry.name))
        leftover_backups = staging / "backups"
    else:
        leftover_backups = None

    stamp = _stamp()
    staging.mkdir(parents=True, exist_ok=True)
    if leftover_backups is None:
        (staging / "backups").mkdir(exist_ok=True)
    backup_dir = staging / "backups" / stamp
    _backup(workflow_dir, backup_dir)
    report["backup"] = str(workflow_dir / DATA_DIRNAME / "backups" / stamp)

    moved: list[str] = []
    try:
        with _store_locks(project_root):
            # Layout markers last: while any is still at the root, every reader keeps the
            # old layout, so nothing can start writing into a data/ that does not exist yet.
            from core.workspace.workspace_paths import _LEGACY_MARKERS

            order = [n for n in _INTERNAL if n not in _LEGACY_MARKERS] + [n for n in _INTERNAL if n in _LEGACY_MARKERS]
            for name in order:
                source = workflow_dir / name
                if source.exists():
                    shutil.move(str(source), str(staging / name))
                    moved.append(name)
            os.replace(staging, workflow_dir / DATA_DIRNAME)
    except Exception:
        for name in moved:
            if (staging / name).exists():
                shutil.move(str(staging / name), str(workflow_dir / name))
        if staging.exists():
            # The backup is the one thing worth keeping from a failed attempt.
            keep = workflow_dir / f"migration-backup-{stamp}"
            if backup_dir.exists():
                shutil.move(str(backup_dir), str(keep))
            shutil.rmtree(staging, ignore_errors=True)
        raise
    report["moved"] = moved
    data = workflow_dir / DATA_DIRNAME

    report["evidence_paths_rewritten"] = _rewrite_evidence_paths(data / "evidence.jsonl", workflow_dir, data)

    for name in (*_OBSOLETE, *_LOCK_FILES):
        target = workflow_dir / name
        if not target.exists():
            continue
        try:
            shutil.rmtree(target) if target.is_dir() else target.unlink()
            report["removed"].append(name)
        except OSError:
            pass

    for entry in workflow_dir.iterdir():
        stem, _, suffix = entry.name.partition(".")
        if entry.name in _EDITABLE or (stem in _SCRIPT_STEMS and suffix in ("ps1", "sh")):
            continue
        report["stray"].append(entry.name)

    config_path = workflow_dir / "config.json"
    if config_path.is_file():
        from core.runtime.config_defaults import strip_to_overrides

        try:
            config = read_json_file(config_path)
        except (OSError, ValueError):
            config = None
        if isinstance(config, dict):
            report["config_stripped"] = strip_to_overrides(config)
            runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
            runtime["layout_version"] = LAYOUT_VERSION
            runtime["sessions_dir"] = f"{WORKFLOW_DIRNAME}/{DATA_DIRNAME}/sessions"
            config["runtime"] = runtime
            atomic_write_json(config_path, config)

    secrets = workflow_dir / "e2e" / "secrets.json"
    if secrets.is_file():
        report["secrets_converted"] = _convert_secrets(secrets, data / "backups" / stamp)

    _prune_backups(data / "backups")
    return report


_STEPS = ((2, migrate_to_data_layout),)


def run(project_root: Path) -> list[dict]:
    """Apply every step the workspace still needs, in order. Returns their reports."""
    reports = []
    for version, step in _STEPS:
        result = step(project_root)
        if not result.get("skipped"):
            reports.append(result)
    return reports
