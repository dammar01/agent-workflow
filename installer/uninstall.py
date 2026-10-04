"""`install.py --uninstall`: take out what the workflow installed, leave what the user wrote.

An install is additive: CLAUDE.md and every provider AGENTS.md get one marker-delimited
block, settings.json gets hook entries and a statusLine, and skills/commands/hooks/agents
are files the installer recorded writing. Uninstall reverses exactly that much:

- the managed block is cut out of each instruction file; a file left with nothing else in
  it is removed, a file with the user's own text keeps that text;
- a managed file goes only when its content is still what an install wrote (the ledger or a
  receipt says so); an edited one is kept and named;
- settings.json loses the commands that run a shipped (or retired) workflow script and a
  workflow statusLine. Every other key stays, including ones an older release seeded
  (`model`, plugins): nothing records whether the user has come to rely on them.

Everything goes through the same backup + receipt an install uses, so `--rollback` undoes an
uninstall. Dry run unless `--apply`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from installer.base import (
    HOME,
    MARKERS,
    _RECEIPT,
    Plan,
    _backup,
    _begin_receipt,
    _discard_empty_receipt,
    _file_sha256,
    _flush_receipt,
    _managed_block,
    _read_text_lenient,
    _record,
    _targets,
)
from installer.settings import _hook_script_ids
from installer.stale import (
    LEDGER,
    managed_prefixes,
    previously_installed,
    retired_hook_stems,
    shipped_hook_stems,
)

MODE_FILE = HOME / ".claude" / ".workflow-install-mode.json"


def _without_block(text: str, start: str, end: str) -> str | None:
    """`text` with its managed block cut out, or None when it has none."""
    block = _managed_block(text, start, end)
    if block is None:
        return None
    rest = text.replace(block, "", 1)
    # The install separated its block from the user's text with one blank line.
    return re.sub(r"\n{3,}", "\n\n", rest).strip("\n") + "\n" if rest.strip() else ""


def _remove_receipted(path: Path, key: str, plan: Plan, apply: bool, backup_root: Path, why: str) -> None:
    pre_sha256 = _file_sha256(path)
    saved = _backup(path, backup_root, plan, apply, f"uninstall/{key}")
    plan.add("remove", path, why)
    if apply:
        path.unlink()
        _RECEIPT.append(
            {
                "action": "remove",
                "key": f"uninstall/{key}",
                "dest": str(path),
                "backup": str(saved) if saved else None,
                "pre_sha256": pre_sha256,
                "post_sha256": None,
            }
        )
        _flush_receipt()


def _uninstall_text(dest: Path, key: str, plan: Plan, apply: bool, backup_root: Path) -> None:
    if not dest.is_file():
        return
    start, end = MARKERS[key]
    remaining = _without_block(_read_text_lenient(dest), start, end)
    if remaining is None:
        plan.add("unchanged", dest, "no workflow block")
        return
    if not remaining:
        _remove_receipted(dest, key, plan, apply, backup_root, "held only the workflow block")
        return
    pre_sha256 = _file_sha256(dest)
    saved = _backup(dest, backup_root, plan, apply, f"uninstall/{key}")
    plan.add("merge", dest, "removed the workflow block, kept your text")
    if apply:
        dest.write_text(remaining, encoding="utf-8")
        _record("merge", dest, f"uninstall/{key}", saved, pre_sha256)


def _uninstall_files(plan: Plan, apply: bool, backup_root: Path) -> None:
    prefixes = managed_prefixes()
    known = previously_installed()
    candidates = {str(dest): key for _src, dest, key in _targets() if key.startswith(prefixes)}
    candidates.update({dest: entry["key"] for dest, entry in known.items()})
    for dest, key in sorted(candidates.items()):
        path = Path(dest)
        if not path.is_file():
            continue
        hashes = (known.get(dest) or {}).get("hashes") or set()
        if _file_sha256(path) in hashes:
            _remove_receipted(path, key, plan, apply, backup_root, "installed by the workflow")
        else:
            plan.warn(f"{path} looks like a workflow file but is not what an install wrote — kept")


def _strip_settings(settings: dict, stems: set[str]) -> tuple[dict, list[str]]:
    out = json.loads(json.dumps(settings))
    changes: list[str] = []
    hooks = out.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            entries = hooks[event]
            if not isinstance(entries, list):
                continue
            kept_entries = []
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                    kept_entries.append(entry)
                    continue
                kept_hooks = []
                for hook in entry["hooks"]:
                    ids = _hook_script_ids({"hooks": [hook]})
                    if ids and ids <= stems:
                        changes.append(f"hooks.{event}: {', '.join(sorted(ids))}")
                    else:
                        kept_hooks.append(hook)
                if kept_hooks:
                    kept_entries.append({**entry, "hooks": kept_hooks})
            if kept_entries:
                hooks[event] = kept_entries
            else:
                del hooks[event]
        if not hooks:
            del out["hooks"]
    status = out.get("statusLine")
    if isinstance(status, dict):
        ids = _hook_script_ids({"hooks": [status]})
        if ids and ids <= stems:
            del out["statusLine"]
            changes.append("statusLine")
    return out, changes


def _uninstall_settings(plan: Plan, apply: bool, backup_root: Path) -> None:
    dest = HOME / ".claude" / "settings.json"
    if not dest.is_file():
        return
    try:
        current = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan.warn(f"{dest} is not readable JSON — left as is; remove the workflow hooks by hand")
        return
    if not isinstance(current, dict):
        return
    stripped, changes = _strip_settings(current, shipped_hook_stems() | retired_hook_stems())
    if not changes:
        plan.add("unchanged", dest, "no workflow hooks")
        return
    pre_sha256 = _file_sha256(dest)
    saved = _backup(dest, backup_root, plan, apply, "uninstall/claude/settings.json")
    plan.add("merge", dest, "removed " + "; ".join(changes))
    if apply:
        dest.write_text(json.dumps(stripped, indent=2) + "\n", encoding="utf-8")
        _record("merge", dest, "uninstall/claude/settings.json", saved, pre_sha256)


def run_uninstall(apply: bool) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_root = HOME / ".claude" / "backups" / f"install_{stamp}"
    plan = Plan()
    print(f"[UNINSTALL] agent-workflow ({'APPLY' if apply else 'DRY RUN'})")
    print(f"  home:   {HOME}")
    print(f"  backup: {backup_root}")
    print()
    if apply:
        _begin_receipt(
            backup_root / "install_receipt.json",
            {"installed_at": datetime.now(timezone.utc).isoformat(), "uninstall": True},
        )
    for _src, dest, key in _targets():
        if key in MARKERS:
            _uninstall_text(dest, key, plan, apply, backup_root)
    _uninstall_files(plan, apply, backup_root)
    _uninstall_settings(plan, apply, backup_root)
    # Receipted like everything else: a rollback that restored the files but not the mode
    # would reinstall the other intent mode, and one without the ledger could no longer tell
    # the workflow's files from the user's.
    for path, key in ((LEDGER, "claude/.workflow-installed.json"), (MODE_FILE, "claude/.workflow-install-mode.json")):
        if path.is_file():
            _remove_receipted(path, key, plan, apply, backup_root, "installer bookkeeping")
    plan.warn(
        "not touched: config/second_agent.seed.json, an AGENT_PATH persisted by --set-env, "
        "and every project's .workflow/ — remove those yourself if you want them gone"
    )
    if apply and _RECEIPT:
        _flush_receipt(complete=True)
    elif apply:
        _discard_empty_receipt()

    counts: dict[str, int] = {}
    for verb, target, detail in plan.actions:
        counts[verb] = counts.get(verb, 0) + 1
        suffix = f"  — {detail}" if detail else ""
        print(f"  {verb:9} {target}{suffix}")
    print()
    print("[UNINSTALL REPORT]")
    print("  " + (" | ".join(f"{verb}: {n}" for verb, n in sorted(counts.items())) or "nothing to do"))
    for warning in plan.warnings:
        print(f"    ! {warning}")
    print()
    if apply:
        print("  undo with: python install.py --rollback --apply")
    else:
        print("  DRY RUN — nothing was written. Re-run with --apply to uninstall.")
    return 0
