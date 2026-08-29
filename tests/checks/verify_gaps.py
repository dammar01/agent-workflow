"""Quick-verify honesty: unchecked files must not read as a pass."""

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import check
import main
from adapters.providers.opencode_adapter import OpenCodeAdapter
from core.provider.executor import Executor
from core.jobs.job_manager import JobManager
from core.prompt.prompt_builder import build_prompt
from core.runtime.state import ensure_workflow_workspace

from tests.checks.support import (
    FakeJobProcess,
    FakeOpenCodeAdapter,
    RecordingOpenCodeAdapter,
    assert_true,
    clean_output,
    extract_session_id,
)

def _test_quick_verify_gaps() -> None:
    """Unavailable, deleted, and name-check failures cannot produce a pass verdict."""
    import shutil as _shutil

    from core.evidence import quick_verify

    root = Path(tempfile.mkdtemp(prefix="quick-verify-"))
    original_discover = quick_verify._discover_changed_files
    original_which = quick_verify.shutil.which
    original_run = quick_verify._run
    try:
        (root / "sample.php").write_text("<?php echo 1;\n", encoding="utf-8")
        quick_verify._discover_changed_files = lambda _root: (["sample.php"], [])
        quick_verify.shutil.which = lambda _tool: None
        result = quick_verify.run(root, "verify-gaps")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete",
            f"missing toolchain must be incomplete: {result}",
        )

        quick_verify._discover_changed_files = lambda _root: (["deleted.py"], [])
        result = quick_verify.run(root, "verify-deleted")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete",
            f"deleted changed files must be incomplete: {result}",
        )

        (root / "names.py").write_text("print(missing_name)\n", encoding="utf-8")
        quick_verify._discover_changed_files = lambda _root: (["names.py"], [])
        quick_verify.shutil.which = lambda _tool: None
        result = quick_verify.run(root, "verify-name-tool-missing")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete",
            f"missing Python name checker must be incomplete: {result}",
        )

        quick_verify.shutil.which = lambda _tool: "pyflakes"
        quick_verify._run = lambda _argv, _root: (1, "undefined name 'missing_name'")
        result = quick_verify.run(root, "verify-names")
        assert_true(
            result.get("meta", {}).get("verdict") == "fail",
            f"name findings must fail verification: {result}",
        )

        quick_verify._discover_changed_files = lambda _root: (
            [],
            ["git diff --name-only --: not a git repository"],
        )
        result = quick_verify.run(root, "verify-discovery-error")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete"
            and result.get("meta", {}).get("discovery_errors"),
            f"change-discovery failures must be incomplete: {result}",
        )

        quick_verify._discover_changed_files = original_discover
        quick_verify._run = original_run
        quick_verify.shutil.which = original_which
        unborn = Path(tempfile.mkdtemp(prefix="quick-unborn-"))
        try:
            initialized = main.subprocess.run(
                ["git", "init", "-q", str(unborn)], capture_output=True, text=True
            )
            assert_true(initialized.returncode == 0, "quick verify git init failed")
            (unborn / "staged.json").write_text('{"ok": true}\n', encoding="utf-8")
            staged = main.subprocess.run(
                ["git", "add", "staged.json"], cwd=unborn, capture_output=True, text=True
            )
            assert_true(staged.returncode == 0, f"quick verify git add failed: {staged.stderr}")
            result = quick_verify.run(unborn, "verify-unborn-staged")
            assert_true(
                result.get("meta", {}).get("verdict") == "pass"
                and "staged.json" in result.get("meta", {}).get("quick_verify", {}).get("passed", []),
                f"unborn staged files must be verified: {result}",
            )
        finally:
            _shutil.rmtree(unborn, ignore_errors=True)
    finally:
        quick_verify._discover_changed_files = original_discover
        quick_verify.shutil.which = original_which
        quick_verify._run = original_run
        _shutil.rmtree(root, ignore_errors=True)
