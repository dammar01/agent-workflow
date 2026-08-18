"""Dedicated units for the installer's four concern modules.

Until now the installer was covered only where provider.py happened to pass through it,
which left its most consequential code — the atomic rollback, the drift detection, the
cross-platform hook rewrite — proven by nothing. These checks pin the behaviours a
release actually leans on: an install must be undoable, a check must notice drift, and
a POSIX install must not ship PowerShell commands.

Each check builds its own temp fixture and patches module globals (HOME, _MODE_FILE)
rather than the process environment, so nothing here can touch the real ~/.claude.
"""

import contextlib
import io
import json
import shutil
import tempfile
from pathlib import Path

from installer import rollback as rollback_mod
from installer import settings as settings_mod
from installer.base import (
    _apply_intent_mode,
    _file_sha256,
    _merge_managed,
    _read_text_lenient,
)
from installer.check import _settings_would_change
from installer.rollback import _run_rollback
from installer.settings import _merge_hook_entries, _rewrite_hooks_for_posix
from tests.checks.support import assert_true


def _test_installer_text_merging() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-inst-base-"))
    try:
        # _read_text_lenient: every encoding an install has actually met in the field.
        bom = root / "bom.md"
        bom.write_bytes(b"\xef\xbb\xbfhello")
        assert_true(
            _read_text_lenient(bom) == "hello",
            "a UTF-8 BOM must be stripped, not surface as \\ufeff in the merged doc",
        )
        cp1252 = root / "cp1252.md"
        cp1252.write_bytes(b"caf\x97e")  # 0x97 = cp1252 em dash, invalid UTF-8 start
        assert_true(
            _read_text_lenient(cp1252) == "caf\u2014e",
            "a cp1252 em dash must decode as an em dash, not crash strict UTF-8",
        )
        stray = root / "stray.md"
        stray.write_bytes(b"\x81abc")  # undefined in cp1252; only latin-1 maps it
        assert_true(
            _read_text_lenient(stray) == "\x81abc",
            "a byte no preferred encoding accepts must fall back to latin-1, never raise",
        )
        crlf = root / "crlf.md"
        crlf.write_bytes(b"a\r\nb\rc")
        assert_true(
            _read_text_lenient(crlf) == "a\nb\nc",
            "line endings must be normalised, or every merge diff is whitespace noise",
        )

        # _apply_intent_mode: exactly one stanza survives, and no selector markers ship.
        both = (
            "<!-- AUTO-INTENT:START -->\nauto stanza\n<!-- AUTO-INTENT:END -->\n"
            "<!-- COMMAND-ONLY:START -->\ncommand stanza\n<!-- COMMAND-ONLY:END -->\n"
            "shared tail"
        )
        command_only = _apply_intent_mode(both, True)
        assert_true(
            "auto stanza" not in command_only and "command stanza" in command_only,
            "only_command must keep the COMMAND-ONLY stanza and drop AUTO-INTENT",
        )
        auto = _apply_intent_mode(both, False)
        assert_true(
            "auto stanza" in auto and "command stanza" not in auto,
            "auto-intent must keep the AUTO-INTENT stanza and drop COMMAND-ONLY",
        )
        assert_true(
            "<!--" not in command_only and "<!--" not in auto,
            "the surviving stanza's own markers are selector plumbing and must not ship",
        )
        assert_true(
            "shared tail" in command_only and "shared tail" in auto,
            "content outside both stanzas must survive either mode",
        )

        # _merge_managed: replace in place, append when absent, accept a bare incoming.
        managed = "<!-- M:START v2 -->\nnew body\n<!-- M:END -->"
        existing = f"user prose\n\n<!-- M:START v1 -->\nold body\n<!-- M:END -->\nmore prose"
        merged, how = _merge_managed(existing, managed, "M:START", "M:END")
        assert_true(
            how == "replaced managed block"
            and "new body" in merged
            and "old body" not in merged
            and "user prose" in merged
            and "more prose" in merged,
            "an existing managed block must be replaced without touching the user's prose",
        )
        appended, how = _merge_managed("user prose", managed, "M:START", "M:END")
        assert_true(
            how == "appended managed block" and appended.startswith("user prose\n\n"),
            "a file with no managed block must gain one after the user's content",
        )
        bare, _how = _merge_managed("", "  no markers here  ", "M:START", "M:END")
        assert_true(
            "no markers here" in bare,
            "incoming text without markers must be spliced as-is, not dropped",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_installer_settings_merge() -> None:
    shipped = {
        "hooks": [
            {
                "type": "command",
                "command": 'powershell -NoProfile -ExecutionPolicy Bypass -File '
                '"C:\\Users\\x\\.claude\\hooks\\session-bind.ps1"',
            }
        ]
    }
    shipped_v2 = json.loads(json.dumps(shipped))
    shipped_v2["hooks"][0]["command"] += " -Refreshed"
    foreign = {"hooks": [{"type": "command", "command": 'python "C:\\mine\\my-hook.py"'}]}
    new_shipped = {
        "hooks": [
            {
                "type": "command",
                "command": 'powershell -NoProfile -ExecutionPolicy Bypass -File '
                '"C:\\Users\\x\\.claude\\hooks\\intent-gate-set.ps1"',
            }
        ]
    }

    merged, updated = _merge_hook_entries(
        [foreign, shipped], [shipped_v2, new_shipped]
    )
    assert_true(
        foreign in merged,
        "a user hook that runs none of our scripts must never be modified",
    )
    assert_true(
        shipped_v2 in merged and shipped not in merged,
        "a shipped entry must be refreshed to the template's version, not duplicated",
    )
    assert_true(
        new_shipped in merged and updated == 2,
        "a shipped entry we don't yet have must be appended and counted",
    )

    # An entry mixing our hook with the user's own: ours refreshes, theirs survives.
    mixed = {
        "hooks": [
            dict(shipped["hooks"][0]),
            {"type": "command", "command": "echo user-owned"},
        ]
    }
    merged, _updated = _merge_hook_entries([mixed], [shipped_v2])
    survivors = [
        hook["command"]
        for entry in merged
        for hook in entry.get("hooks", [])
    ]
    assert_true(
        "echo user-owned" in survivors,
        "a user hook sharing an entry with ours must survive the refresh",
    )
    assert_true(
        shipped_v2["hooks"][0]["command"] in survivors
        and shipped["hooks"][0]["command"] not in survivors,
        "the shipped half of a mixed entry must still be refreshed",
    )

    template = {
        "hooks": {
            "SessionStart": [json.loads(json.dumps(shipped)), json.loads(json.dumps(foreign))]
        }
    }
    original_os_name = settings_mod.os.name
    try:
        settings_mod.os.name = "posix"
        rewritten = _rewrite_hooks_for_posix(json.loads(json.dumps(template)))
        ours = rewritten["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        theirs = rewritten["hooks"]["SessionStart"][1]["hooks"][0]["command"]
        assert_true(
            ours == 'bash "C:/Users/x/.claude/hooks/session-bind.sh"',
            f"a POSIX install must swap interpreter, extension, and slashes; got {ours!r}",
        )
        assert_true(
            theirs == foreign["hooks"][0]["command"],
            "a foreign hook must keep its command verbatim, backslashes included",
        )
        settings_mod.os.name = "nt"
        untouched = _rewrite_hooks_for_posix(json.loads(json.dumps(template)))
        assert_true(
            untouched == template,
            "on Windows the template must pass through unchanged",
        )
    finally:
        settings_mod.os.name = original_os_name


def _rollback_fixture(root: Path) -> tuple[Path, Path, Path]:
    """A fake HOME holding one receipted install: one restorable file, one created file."""
    home = root / "home"
    dest_root = root / "dest"
    backup_dir = home / ".claude" / "backups" / "install_20260101_000000"
    (backup_dir / "claude").mkdir(parents=True)
    dest_root.mkdir()

    restored_dest = dest_root / "CLAUDE.md"
    restored_dest.write_text("installed content", encoding="utf-8")
    backup_file = backup_dir / "claude" / "CLAUDE.md"
    backup_file.write_text("original content", encoding="utf-8")

    created_dest = dest_root / "created.md"
    created_dest.write_text("created by install", encoding="utf-8")

    receipt = {
        "schema_version": 2,
        "entries": [
            {
                "action": "replace",
                "key": "claude/CLAUDE.md",
                "dest": str(restored_dest),
                "backup": str(backup_file),
                "pre_sha256": _file_sha256(backup_file),
                "post_sha256": _file_sha256(restored_dest),
            },
            {
                "action": "create",
                "key": "claude/created.md",
                "dest": str(created_dest),
                "backup": None,
                "pre_sha256": None,
                "post_sha256": _file_sha256(created_dest),
            },
        ],
    }
    (backup_dir / "install_receipt.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    return home, restored_dest, created_dest


def _run_quiet(which, apply) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = _run_rollback(which, apply)
    return code, out.getvalue()


def _test_installer_rollback_receipt() -> None:
    original_home = rollback_mod.HOME
    original_mode_file = rollback_mod._MODE_FILE
    root = Path(tempfile.mkdtemp(prefix="aw-inst-rb-"))
    try:
        # Dry run: report everything, write nothing.
        home, restored, created = _rollback_fixture(root / "dry")
        rollback_mod.HOME = home
        code, _output = _run_quiet(None, False)
        assert_true(
            code == 0
            and restored.read_text(encoding="utf-8") == "installed content"
            and created.exists(),
            "a dry-run rollback must report cleanly and change nothing",
        )

        # Apply: the backed-up file comes back, the created file goes away.
        code, _output = _run_quiet(None, True)
        assert_true(
            code == 0
            and restored.read_text(encoding="utf-8") == "original content"
            and not created.exists(),
            "an applied rollback must restore backups and delete created files",
        )

        # A destination edited since the install must abort the WHOLE rollback.
        home, restored, created = _rollback_fixture(root / "edited")
        rollback_mod.HOME = home
        restored.write_text("hand edited after install", encoding="utf-8")
        code, output = _run_quiet(None, True)
        assert_true(
            code == 2 and "ABORTED" in output,
            "an edited destination must refuse the rollback, not overwrite the edit",
        )
        assert_true(
            restored.read_text(encoding="utf-8") == "hand edited after install"
            and created.exists(),
            "a refused rollback must leave every file exactly as it found it",
        )

        # An unknown receipt schema is refused rather than replayed unverified.
        home, _restored, _created = _rollback_fixture(root / "schema")
        receipt_path = (
            home / ".claude" / "backups" / "install_20260101_000000" / "install_receipt.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["schema_version"] = 1
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        rollback_mod.HOME = home
        code, output = _run_quiet(None, True)
        assert_true(
            code == 1 and "unsupported receipt schema" in output,
            "an unsupported schema must be refused, never guessed at",
        )

        # A pre-receipt backup dir can only be restored by hand; say so.
        home, _restored, _created = _rollback_fixture(root / "noreceipt")
        (
            home / ".claude" / "backups" / "install_20260101_000000" / "install_receipt.json"
        ).unlink()
        rollback_mod.HOME = home
        code, output = _run_quiet(None, True)
        assert_true(
            code == 1 and "no install_receipt.json" in output,
            "a backup dir without a receipt must be refused with manual instructions",
        )

        # _stored_only_command: the persisted intent mode, with a safe default.
        mode_file = root / "mode.json"
        rollback_mod._MODE_FILE = mode_file
        assert_true(
            rollback_mod._stored_only_command() is False,
            "no stored mode must read as auto-intent, the shipped default",
        )
        mode_file.write_text(json.dumps({"only_command": True}), encoding="utf-8")
        assert_true(
            rollback_mod._stored_only_command() is True,
            "a stored only_command=true must be honoured by later upgrades",
        )
        mode_file.write_text("not json", encoding="utf-8")
        assert_true(
            rollback_mod._stored_only_command() is False,
            "a corrupt mode file must fall back to the default, not crash the install",
        )
    finally:
        rollback_mod.HOME = original_home
        rollback_mod._MODE_FILE = original_mode_file
        shutil.rmtree(root, ignore_errors=True)


def _test_installer_drift_check() -> None:
    root = Path(tempfile.mkdtemp(prefix="aw-inst-check-"))
    try:
        template = {
            "model": "workflow-default",
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'powershell -NoProfile -ExecutionPolicy Bypass '
                                '-File "C:\\Users\\x\\.claude\\hooks\\session-bind.ps1"',
                            }
                        ]
                    }
                ]
            },
        }
        src = root / "settings.template.json"
        src.write_text(json.dumps(template, indent=2), encoding="utf-8")

        missing_dest = root / "missing" / "settings.json"
        assert_true(
            _settings_would_change(src, missing_dest, False) is True,
            "a missing settings.json is drift — an install would create it",
        )

        # A dest already matching the template (only on Windows does the template apply
        # verbatim; on POSIX the hook rewrite makes the shipped form itself differ).
        original_os_name = settings_mod.os.name
        try:
            settings_mod.os.name = "nt"
            current_dest = root / "settings.json"
            current_dest.write_text(json.dumps(template, indent=2), encoding="utf-8")
            assert_true(
                _settings_would_change(src, current_dest, False) is False,
                "a settings.json already carrying the shipped keys and hooks is not drift",
            )
        finally:
            settings_mod.os.name = original_os_name

        broken_dest = root / "broken.json"
        broken_dest.write_text("{not json", encoding="utf-8")
        assert_true(
            _settings_would_change(src, broken_dest, False) is True,
            "an unparseable settings.json must count as drift, not as clean",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
