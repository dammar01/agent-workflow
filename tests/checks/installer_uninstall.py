"""The install stays additive at both ends: written as one block, removed as one block.

A first install used to write the whole shipped CLAUDE.md, header included, and nothing
could later tell that header from the user's own text. Uninstall had no entry point at all.
These checks pin the block-only first write, the uninstall cut that keeps the user's text
around it, the settings strip that keeps the user's hooks, and the install-time drop of a
shipped hook from an event the release no longer registers it on.
"""

import shutil
import tempfile
from pathlib import Path

import contextlib
import io

from installer import rollback as rollback_mod
from installer.base import Plan, _begin_receipt, _flush_receipt, _install_text, _reset_receipt
from installer.settings import _drop_relocated_hooks
from installer.uninstall import _remove_receipted, _strip_settings, _without_block
from tests.checks.support import assert_true

_START = "WORKFLOW-MAIN-AGENT:START"
_END = "WORKFLOW-MAIN-AGENT:END"
_BLOCK = f"<!-- {_START} — v9, do not edit manually -->\nworkflow rules\n<!-- {_END} -->"


def _cmd(stem: str) -> dict:
    return {"type": "command", "command": f'powershell -File "C:\\h\\.claude\\hooks\\{stem}.ps1"'}


def _test_installer_uninstall() -> None:
    root = Path(tempfile.mkdtemp(prefix="installer-uninstall-"))
    try:
        # --- a first install writes the managed block alone --------------------------------
        src = root / "CLAUDE.md"
        src.write_text(f"# Shipped header outside the markers\n\n{_BLOCK}\n", encoding="utf-8")
        dest = root / "home" / "CLAUDE.md"
        _reset_receipt()
        plan = Plan()
        _install_text(src, dest, "claude/CLAUDE.md", plan, True, root / "backup", None)
        written = dest.read_text(encoding="utf-8")
        assert_true(
            "Shipped header" not in written and _START in written and _END in written,
            f"a first install writes only the managed block: {written!r}",
        )
        assert_true(
            plan.actions and plan.actions[0][0] == "create",
            f"and reports it as created: {plan.actions}",
        )
        _reset_receipt()

        # --- uninstall cuts the block and keeps the user's text around it --------------------
        mine = f"# My rules\nalways answer in English\n\n{_BLOCK}\n\n## More of mine\nno emoji\n"
        rest = _without_block(mine, _START, _END)
        assert_true(
            rest is not None
            and "always answer in English" in rest
            and "no emoji" in rest
            and _START not in rest
            and "workflow rules" not in rest,
            f"the user's text above and below the block survives: {rest!r}",
        )
        assert_true(_without_block(f"{_BLOCK}\n", _START, _END) == "", "a file holding only the block is left empty")
        assert_true(_without_block("# no block here\n", _START, _END) is None, "a file without the block is not ours to touch")

        # --- settings: workflow hooks and statusLine go, the user's stay ---------------------
        settings = {
            "model": "the-users-choice",
            "statusLine": {"type": "command", "command": _cmd("workflow-statusline")["command"]},
            "hooks": {
                "SessionStart": [{"matcher": "startup", "hooks": [_cmd("session-bind"), _cmd("my-own-hook")]}],
                "Stop": [{"hooks": [_cmd("graph-refresh")]}],
            },
        }
        stripped, changes = _strip_settings(settings, {"session-bind", "graph-refresh", "workflow-statusline"})
        assert_true(
            stripped.get("model") == "the-users-choice" and "statusLine" not in stripped,
            f"keys the user owns stay; the workflow statusLine goes: {stripped}",
        )
        assert_true(
            stripped["hooks"] == {"SessionStart": [{"matcher": "startup", "hooks": [_cmd("my-own-hook")]}]},
            f"only the user's hook survives, in its entry: {stripped.get('hooks')}",
        )
        assert_true(len(changes) == 3, f"each removal is named: {changes}")

        # --- install: a shipped script leaves the events the template moved it off -----------
        current = {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_cmd("task-events")]},
                {"matcher": "Bash", "hooks": [_cmd("my-own-hook")]},
            ],
            "PostToolUse": [{"matcher": "Edit", "hooks": [_cmd("task-events")]}],
        }
        template = {"PostToolUse": [{"matcher": "Edit", "hooks": [_cmd("task-events")]}]}
        moved, removed = _drop_relocated_hooks(current, template)
        assert_true(
            removed == 1
            and moved["PreToolUse"] == [{"matcher": "Bash", "hooks": [_cmd("my-own-hook")]}]
            and moved["PostToolUse"] == current["PostToolUse"],
            f"task-events leaves PreToolUse, the user's hook and the new registration stay: {moved}",
        )
        # --- bookkeeping the uninstall removes comes back with --rollback -----------------
        mode = root / "home" / ".workflow-install-mode.json"
        mode.write_text('{"only_command": true}\n', encoding="utf-8")
        fake_home = root / "fake-home"
        backup_dir = fake_home / ".claude" / "backups" / "install_uninstall"
        _reset_receipt()
        _begin_receipt(backup_dir / "install_receipt.json", {"uninstall": True})
        _remove_receipted(mode, "claude/.workflow-install-mode.json", Plan(), True, backup_dir, "bookkeeping")
        _flush_receipt(complete=True)
        _reset_receipt()
        assert_true(not mode.exists(), "uninstall removes the mode file")
        original = rollback_mod.HOME
        rollback_mod.HOME = fake_home
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                code = rollback_mod._run_rollback(None, True)
        finally:
            rollback_mod.HOME = original
        assert_true(
            code == 0 and mode.read_text(encoding="utf-8") == '{"only_command": true}\n',
            f"--rollback restores the intent mode an uninstall removed: code={code}",
        )
    finally:
        _reset_receipt()
        shutil.rmtree(root, ignore_errors=True)
