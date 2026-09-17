"""Upgrading a 3.6 workspace to the 3.7 layout, and what the 3.7 layout adds.

The migration moves a workspace's whole history, so each property that makes that safe is
pinned: everything moves and nothing is lost (a backup first, archived evidence still
found), a second upgrade does nothing, a live job blocks it, a failure puts everything
back. Beside it: config.json holds overrides only, and `.workflow/current/` mirrors the
latest dispatch without ever carrying a resolved credential.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from core.workspace.workspace_paths import is_legacy_layout, workflow_paths
from tests.checks.support import assert_true


def _legacy_workspace() -> Path:
    root = Path(tempfile.mkdtemp(prefix="aw-migrate-"))
    wf = root / ".workflow"
    wf.mkdir()
    (wf / "config.json").write_text(json.dumps({
        "version": "3.6.0",
        "commands": {"verify_mode": "delegated", "auto_verify_after_execute": True},
        "policies": {"graph_leads_enabled": True},
        "e2e": {"base_url": "http://localhost:8000", "headless": True, "allowed_mutation_paths": ["/x"]},
        "runtime": {"tool_version": "3.6.0"},
    }), encoding="utf-8")
    (wf / "second_agent.json").write_text(json.dumps({"provider": "codex", "default_model": "gpt-5.6-luna"}), encoding="utf-8")
    run = wf / "sessions" / "s1" / "logs" / "r1"
    run.mkdir(parents=True)
    (run / "output.raw.md").write_text("[EVIDENCE]\n", encoding="utf-8")
    (wf / "usage.jsonl").write_text('{"command": "explore"}\n', encoding="utf-8")
    (wf / "audit.jsonl").write_text('{"command": "explore"}\n', encoding="utf-8")
    (wf / "facts.jsonl").write_text("", encoding="utf-8")
    (wf / "evidence.jsonl").write_text(
        json.dumps({"artifact_path": str(run / "output.raw.md"), "artifact_hash": "x", "session": "s1"}) + "\n", encoding="utf-8"
    )
    (wf / "evidence.jsonl.lock").write_text("x", encoding="utf-8")
    (wf / "state.json").write_text("{}", encoding="utf-8")
    (wf / "runtime").mkdir()
    (wf / "provider-sessions").mkdir()
    (wf / "notes.txt").write_text("the user's", encoding="utf-8")
    (wf / "e2e").mkdir()
    (wf / "e2e" / "secrets.json").write_text(
        json.dumps({"default": "qa", "profiles": {"qa": {"E2E_USER": "u", "E2E_PASS": "p"}}}), encoding="utf-8"
    )
    return root


def _check_migration_moves_everything_once() -> None:
    from core.runtime.upgrade import needs_upgrade, upgrade_workflow_workspace

    root = _legacy_workspace()
    wf = root / ".workflow"
    try:
        assert_true(is_legacy_layout(root) and needs_upgrade(root), "fixture assumption: a 3.6 workspace needs an upgrade")
        result = upgrade_workflow_workspace(root, None)
        report = result["migrations"][0]
        data = wf / "data"
        assert_true(not is_legacy_layout(root) and data.is_dir(), "the workspace is on the data layout")
        for name in ("sessions", "provider-sessions", "usage.jsonl", "audit.jsonl", "facts.jsonl", "evidence.jsonl"):
            assert_true((data / name).exists() and not (wf / name).exists(), f"{name} moved into data/")
        assert_true(
            (data / "usage.jsonl").read_text(encoding="utf-8") == '{"command": "explore"}\n',
            "history moves intact, not recreated empty",
        )
        assert_true(not (wf / "state.json").exists() and not (wf / "runtime").exists() and not (wf / "evidence.jsonl.lock").exists(),
                    f"pre-session leftovers and old locks are removed: {report['removed']}")
        backup = Path(report["backup"])
        assert_true((backup / "usage.jsonl").exists() and (backup / "state.json").exists() and (backup / "sessions").is_dir(),
                    "the backup holds the workspace as it was")
        index = json.loads((data / "evidence.jsonl").read_text(encoding="utf-8"))
        assert_true(Path(index["artifact_path"]).is_file() and "data" in Path(index["artifact_path"]).parts,
                    f"archived evidence still resolves after the move: {index['artifact_path']}")
        config = json.loads((wf / "config.json").read_text(encoding="utf-8"))
        assert_true(
            config.get("commands") == {"auto_verify_after_execute": True} and config.get("e2e") == {"headless": True}
            and "policies" not in config and config["runtime"]["layout_version"] == 2,
            f"config.json keeps only real overrides: {config}",
        )
        assert_true(any("allowed_mutation_paths" in item for item in report["config_stripped"]), "a retired key is removed and reported")
        secrets = json.loads((wf / "e2e" / "secrets.json").read_text(encoding="utf-8"))
        assert_true(
            secrets["profiles"] == [{"name": "qa", "credentials": {"E2E_USER": "u", "E2E_PASS": "p"}}] and report["secrets_converted"],
            f"the pre-list secrets.json is converted with its values: {secrets}",
        )
        assert_true(report["stray"] == ["notes.txt"] and (wf / "notes.txt").exists(), f"an unrecognised file is named and left alone: {report['stray']}")
        again = upgrade_workflow_workspace(root, None)
        assert_true(again["migrations"] == [] and not is_legacy_layout(root),
                    f"a second upgrade migrates nothing: {again['migrations']}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _check_live_job_blocks_migration() -> None:
    import core.runtime.upgrade as upgrade

    root = _legacy_workspace()
    saved = upgrade.active_jobs_for_workspace
    upgrade.active_jobs_for_workspace = lambda project_root: [{"job_id": "job-1", "command": "explore"}]
    try:
        try:
            upgrade.upgrade_workflow_workspace(root, None)
            refused = False
        except ValueError:
            refused = True
        assert_true(refused and is_legacy_layout(root) and not (root / ".workflow" / "data").exists(),
                    "a live job refuses the upgrade before anything moves")
    finally:
        upgrade.active_jobs_for_workspace = saved
        shutil.rmtree(root, ignore_errors=True)


def _check_failed_migration_is_rolled_back() -> None:
    import core.runtime.migrations as migrations
    from core.runtime.upgrade import upgrade_workflow_workspace

    root = _legacy_workspace()
    wf = root / ".workflow"
    saved = migrations.os.replace

    def failing_replace(src, dst):
        if str(src).endswith("data.migrating"):
            raise OSError("simulated rename failure")
        return saved(src, dst)

    migrations.os.replace = failing_replace
    try:
        try:
            upgrade_workflow_workspace(root, None)
            raised = False
        except ValueError as exc:
            raised = "rolled back" in str(exc)
        assert_true(raised, "a failed migration is reported as rolled back")
        assert_true(
            is_legacy_layout(root) and (wf / "usage.jsonl").exists() and (wf / "sessions" / "s1").is_dir()
            and not (wf / "data").exists() and not (wf / "data.migrating").exists(),
            "and everything is back where it was",
        )
        assert_true(any(p.name.startswith("migration-backup-") for p in wf.iterdir()), "with its backup kept beside it")
    finally:
        migrations.os.replace = saved
        shutil.rmtree(root, ignore_errors=True)


def _check_config_holds_overrides_only() -> None:
    from core.runtime.config_defaults import effective_section, merge_config_defaults
    from core.runtime.state import ensure_workflow_workspace

    root = Path(tempfile.mkdtemp(prefix="aw-overrides-"))
    try:
        ensure_workflow_workspace(root, os.getenv("AGENT_PATH"))
        config = json.loads(workflow_paths(root)["config"].read_text(encoding="utf-8"))
        assert_true(config.get("commands") == {} and config.get("policies") == {} and "e2e" not in config,
                    f"a new config.json copies no defaults: {config.get('commands')} {config.get('policies')}")
        merged, _ = merge_config_defaults({"commands": {"verify_mode": "syntax"}})
        assert_true(merged["commands"] == {"verify_mode": "syntax"}, f"loading never backfills defaults: {merged}")
        effective = effective_section(merged, "commands")
        assert_true(effective["verify_mode"] == "syntax" and effective["auto_verify_after_execute"] is False,
                    f"the effective view is defaults plus overrides: {effective}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _check_current_mirrors_the_latest_dispatch() -> None:
    from core.workspace import current
    from tests.checks.e2e_routing import _adapter, _flow, _workspace

    root = _workspace("aw-current-")
    try:
        _flow(root, _adapter(), "pass", {"E2E_USER": "user@example.test"})
        directory = root / ".workflow" / "current"
        session = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        assert_true(session["status"] == "done" and session["command"] == "verify-browser", f"the mirror shows the finished run: {session}")
        events = (directory / "e2e" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert_true(any(json.loads(line).get("type") == "progress" for line in events), "browser steps arrive in current/e2e/events.jsonl")
        assert_true((directory / "e2e" / "report.json").is_file() and (directory / "e2e" / "verification.md").is_file(),
                    "the run's readable results are copied in")
        mirrored = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in directory.rglob("*") if p.is_file())
        assert_true("user@example.test" not in mirrored, "the mirror never carries a resolved credential")

        current.start(root, "another-session", "explore", "map it")
        current.e2e_event(root, "e2e-session", {"type": "progress", "step": 9})
        assert_true(
            json.loads((directory / "session.json").read_text(encoding="utf-8"))["session_id"] == "another-session"
            and not (directory / "e2e").exists(),
            "a newer dispatch takes the mirror over, and the older session stops writing into it",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_workspace_migration() -> None:
    _check_migration_moves_everything_once()
    _check_live_job_blocks_migration()
    _check_failed_migration_is_rolled_back()
    _check_config_holds_overrides_only()
    _check_current_mirrors_the_latest_dispatch()
