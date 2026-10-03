"""Tasks as sequences of skill calls, closed by a commit (DEC-015).

Events come from two writers into `tasks.jsonl`: the runtime records each delegated
command it runs, and the `task-events` hook records what never passes through it — a
local skill being loaded, a file edited, a commit made. Nothing is derived at write time;
the hook stays a few lines of append, and a better reading of the same history needs no
migration.

A task is the run of events in one MAIN_SESSION_ID from its first event until a commit
that includes a file it edited. Its state:

- `completed`: committed after a verify whose declared verdict was DONE, with no edit since.
- `unverified_closed`: committed without such a verify (none, NEEDS FIX, INCOMPLETE, or
  edited after the DONE).
- `ready_to_commit`: DONE verify, no edit since, not committed yet.
- `open`: edits not yet verified DONE and not committed. Uncommitted work is unfinished.
- `read_only`: skills ran, nothing was edited; nothing for a commit to close.
- `unknown`: events without a session identity, which cannot be put in a task.

The declared verdict is read, not the runtime's derived one: a read-only second agent
lists the tests it could not run, and the derived verdict turns a clean DONE into
`incomplete` (CASE-009, DEC-015 limitations).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.evidence.contracts import TASK_EVENT_VERSION, TASK_STREAM_NAME
from core.evidence.runtime_io import write_task_event
from core.workspace.workspace_paths import data_dir, now_iso

RECENT = 10


def record_skill(project_root, session_id: str | None, skill: str, *, verdict: str | None = None,
                 next_action: str | None = None, prompt_id: str | None = None) -> None:
    """The runtime's event for one delegated command. Never raises."""
    try:
        write_task_event(Path(project_root), {
            "v": TASK_EVENT_VERSION,
            "at": now_iso(),
            "source": "runtime",
            "session_id": session_id,
            "kind": "skill",
            "skill": skill,
            "verdict": verdict,
            "next_action": next_action,
            "prompt_id": prompt_id,
        })
    except Exception:
        pass


def load_events(project_root) -> list[dict]:
    """Every event on disk, oldest first; a torn or foreign line is skipped."""
    try:
        text = (data_dir(Path(project_root)) / TASK_STREAM_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("kind") in ("skill", "edit", "commit"):
            events.append(event)
    return events


def _new_task(session_id, at) -> dict:
    return {"session_id": session_id, "started_at": at, "ended_at": at, "sequence": [],
            "edited": set(), "verdict": None, "commit": None, "state": None}


def derive_tasks(events: list[dict]) -> tuple[list[dict], int]:
    """Tasks in the order they started, and the number of commits no task claimed."""
    tasks: list[dict] = []
    current: dict[str | None, dict] = {}
    seen_commits: set[str] = set()
    unclaimed = 0
    for event in events:
        session = event.get("session_id") or None
        at = event.get("at")
        kind = event["kind"]
        if kind == "commit":
            commit = str(event.get("commit") or "")
            if not commit or commit in seen_commits:
                continue
            seen_commits.add(commit)
            task = current.get(session)
            listed = event.get("paths") or []
            paths = {str(p) for p in ([listed] if isinstance(listed, str) else listed)}
            if session is None or task is None or not (task["edited"] & paths):
                unclaimed += 1
                continue
            task["sequence"].append("commit")
            task["commit"] = commit
            task["ended_at"] = at
            task["state"] = "completed" if task["verdict"] == "DONE" else "unverified_closed"
            del current[session]
            continue
        task = current.get(session)
        if task is None:
            task = current[session] = _new_task(session, at)
            tasks.append(task)
        task["ended_at"] = at
        if kind == "edit":
            task["edited"].add(str(event.get("path") or ""))
            # A DONE vouches for the tree it saw; an edit after it is unverified work.
            task["verdict"] = None
            if not task["sequence"] or task["sequence"][-1] != "edit":
                task["sequence"].append("edit")
            continue
        skill = str(event.get("skill") or "unknown")
        task["sequence"].append(skill)
        if skill == "verify":
            verdict = str(event.get("verdict") or "").strip().upper().replace("_", " ")
            task["verdict"] = verdict or None
    for task in current.values():
        if task["session_id"] is None:
            task["state"] = "unknown"
        elif not task["edited"]:
            task["state"] = "read_only"
        elif task["verdict"] == "DONE":
            task["state"] = "ready_to_commit"
        else:
            task["state"] = "open"
    return tasks, unclaimed


def report(project_root) -> dict:
    tasks, unclaimed = derive_tasks(load_events(project_root))
    by_state: dict[str, int] = {}
    skill_counts: dict[str, int] = {}
    for task in tasks:
        by_state[task["state"]] = by_state.get(task["state"], 0) + 1
        for step in task["sequence"]:
            skill_counts[step] = skill_counts.get(step, 0) + 1
    return {
        "tasks": len(tasks),
        "by_state": dict(sorted(by_state.items())),
        "skill_counts": dict(sorted(skill_counts.items())),
        "unclaimed_commits": unclaimed,
        "recent": [
            {
                "session_id": task["session_id"],
                "started_at": task["started_at"],
                "ended_at": task["ended_at"],
                "state": task["state"],
                "sequence": task["sequence"],
                "files_edited": len(task["edited"]),
                "commit": task["commit"],
            }
            for task in tasks[-RECENT:]
        ],
    }
