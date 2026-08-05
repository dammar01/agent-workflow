"""The v3.4.3 provider seam: registry selection and key migration."""

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import check
import main
from adapters.opencode_adapter import OpenCodeAdapter
from core.executor import Executor
from core.job_manager import JobManager
from core.prompt_builder import build_prompt
from core.workflow_runtime import ensure_workflow_workspace

from checks.support import (
    FakeJobProcess,
    FakeOpenCodeAdapter,
    RecordingOpenCodeAdapter,
    assert_true,
    clean_output,
    extract_session_id,
)


def _test_provider_seam() -> None:
    """The v3.4.3 seam: registry selection plus the hard-rename migration.

    Both are new load-bearing paths with no coverage anywhere else. The migration in
    particular is the only thing between a v3.4.2 workspace and silent loss of the
    user's tuning, so it is asserted on values, not just on the absence of a crash.
    """
    from adapters.registry import (
        UnknownProviderError,
        available_providers,
        provider_for,
        register,
        resolve_adapter,
    )
    from core.provider_migration import migrate_provider_keys
    from core.workspace_paths import atomic_write_json

    assert_true(
        "opencode" in available_providers(),
        f"opencode must stay registered: {available_providers()}",
    )
    assert_true(
        resolve_adapter().adapter == "opencode",
        "default resolution must yield the opencode adapter",
    )

    raised = False
    try:
        resolve_adapter("gemini-that-does-not-exist")
    except UnknownProviderError:
        raised = True
    assert_true(
        raised,
        "an unregistered provider must raise, not silently fall back to the default — "
        "a typo that ran OpenCode anyway would look like the config took effect",
    )

    temp_root = Path(tempfile.mkdtemp(prefix="provider-migration-"))
    workflow_dir = temp_root / ".workflow"
    workflow_dir.mkdir(parents=True)
    sessions = temp_root / "sessions"
    sessions.mkdir()

    # A v3.4.2 workspace: legacy file, legacy keys, and a value the user tuned.
    atomic_write_json(
        workflow_dir / "opencode.json",
        {"opencode_command": "opencode-custom", "timeout_seconds": 999},
    )
    atomic_write_json(
        workflow_dir / "config.json",
        {"runtime": {"opencode_config": ".workflow/opencode.json"}},
    )
    atomic_write_json(
        sessions / "s1.json",
        {"session_id": "s1", "opencode_session_id": "ses_keepme"},
    )
    # The store the runtime actually reads is project-local. The first version of the
    # migration only walked the fallback dir, so a real workspace kept its legacy key,
    # every call re-bootstrapped, and bootstrap is what times out.
    local_sessions = workflow_dir / "provider-sessions"
    local_sessions.mkdir()
    atomic_write_json(
        local_sessions / "s2.json",
        {"session_id": "s2", "opencode_session_id": "ses_local"},
    )

    report = migrate_provider_keys(temp_root, sessions)

    migrated = json.loads(
        (workflow_dir / "second_agent.json").read_text(encoding="utf-8")
    )
    assert_true(
        migrated.get("provider_command") == "opencode-custom",
        f"tuned value must survive the rename, got {migrated!r}",
    )
    assert_true(
        "opencode_command" not in migrated,
        "hard rename means the legacy key is gone, not shadowed",
    )
    assert_true(
        migrated.get("timeout_seconds") == 999,
        "untouched keys must carry over verbatim",
    )
    assert_true(
        not (workflow_dir / "opencode.json").exists(),
        "the legacy file must be retired, or the next upgrade migrates it again",
    )

    config = json.loads((workflow_dir / "config.json").read_text(encoding="utf-8"))
    assert_true(
        config["runtime"].get("provider_config") == ".workflow/second_agent.json",
        f"runtime pointer must follow the rename: {config['runtime']!r}",
    )

    session = json.loads((sessions / "s1.json").read_text(encoding="utf-8"))
    assert_true(
        session.get("provider_session_id") == "ses_keepme",
        "a stored provider session must survive, or every session re-bootstraps on quota",
    )
    local = json.loads((local_sessions / "s2.json").read_text(encoding="utf-8"))
    assert_true(
        local.get("provider_session_id") == "ses_local"
        and "opencode_session_id" not in local,
        f"the PROJECT-LOCAL session store must migrate too: {local!r}",
    )

    # Second line of defence: nothing forces a user to run upgrade before their next
    # delegated call, so SessionManager repairs a legacy record on read as well.
    from core.session_manager import SessionManager

    stray = workflow_dir / "stray-sessions"
    stray.mkdir()
    atomic_write_json(
        stray / "s3.json", {"session_id": "s3", "opencode_session_id": "ses_onread"}
    )
    loaded = SessionManager(stray).load_or_create("s3")
    assert_true(
        loaded.get("provider_session_id") == "ses_onread",
        f"a legacy record must be readable without an upgrade first: {loaded!r}",
    )
    assert_true(
        "opencode_session_id"
        not in json.loads((stray / "s3.json").read_text(encoding="utf-8")),
        "the repair must be persisted, not re-done on every load",
    )

    # Idempotence: upgrade runs this on every invocation, including already-migrated ones.
    again = migrate_provider_keys(temp_root, sessions)
    assert_true(
        again["provider_config"]["status"] in {"nothing_to_do", "keys_migrated"},
        f"second run must be a no-op, got {again['provider_config']}",
    )
    assert_true(
        json.loads((workflow_dir / "second_agent.json").read_text(encoding="utf-8"))
        == migrated,
        "a second migration must not alter an already-migrated config",
    )
    assert_true(
        provider_for(temp_root) in available_providers(),
        "a migrated workspace must still resolve to a registered provider",
    )

    # Read the migrated config back THROUGH the resolver the runtime actually uses.
    # Everything above asserts the write side, and the write side was never the bug: the
    # v3.4.3 rename produced a correct second_agent.json that no reader opened, because
    # the resolver still named the v3.4.2 file. Every project silently ran on tool
    # defaults — wrong model, wrong timeouts, wrong quota — and this suite stayed green.
    from config.settings import (
        PROVIDER_CONFIG_FILE,
        load_provider_config_for,
        resolve_provider_config_for,
    )

    resolved = resolve_provider_config_for(temp_root)
    assert_true(
        resolved["source"] == "project",
        f"the project's own config must win over the tool default: {resolved!r}",
    )
    assert_true(
        Path(resolved["path"]) == workflow_dir / "second_agent.json",
        f"the resolver must open the CURRENT filename: {resolved['path']}",
    )
    assert_true(
        resolved["error"] is None,
        f"a well-formed config must resolve without error: {resolved['error']!r}",
    )
    assert_true(
        load_provider_config_for(temp_root).get("timeout_seconds") == 999,
        "the user's tuned value must reach the reader, not just the file on disk",
    )
    assert_true(
        load_provider_config_for(temp_root).get("provider_command")
        == "opencode-custom",
        "a migrated key must be readable through the resolver too",
    )

    # A v3.4.2 workspace that never ran upgrade still has the legacy filename. It must
    # keep working: the fallback is what makes fixing the resolver safe to ship.
    legacy_root = Path(tempfile.mkdtemp(prefix="provider-legacy-"))
    legacy_dir = legacy_root / ".workflow"
    legacy_dir.mkdir(parents=True)
    atomic_write_json(legacy_dir / "opencode.json", {"timeout_seconds": 777})
    legacy_resolved = resolve_provider_config_for(legacy_root)
    assert_true(
        legacy_resolved["source"] == "project_legacy"
        and legacy_resolved["config"].get("timeout_seconds") == 777,
        f"an un-upgraded workspace must still be read: {legacy_resolved!r}",
    )

    # No project config at all is a legitimate state, not an error.
    bare_root = Path(tempfile.mkdtemp(prefix="provider-bare-"))
    (bare_root / ".workflow").mkdir(parents=True)
    bare = resolve_provider_config_for(bare_root)
    assert_true(
        bare["source"] == "tool_default"
        and bare["error"] is None
        and Path(bare["path"]) == Path(PROVIDER_CONFIG_FILE),
        f"absence must fall back cleanly, without inventing an error: {bare!r}",
    )

    # Malformed is NOT absence. The runtime stays up on defaults, but the substitution
    # has to be recorded — an unreported swap is the failure mode this whole check exists
    # to catch, just arriving through a stray comma instead of a rename.
    broken_root = Path(tempfile.mkdtemp(prefix="provider-broken-"))
    broken_dir = broken_root / ".workflow"
    broken_dir.mkdir(parents=True)
    (broken_dir / "second_agent.json").write_text(
        '{"timeout_seconds": }', encoding="utf-8"
    )
    broken = resolve_provider_config_for(broken_root)
    assert_true(
        broken["error"] is not None and broken["source"] == "tool_default",
        f"an unreadable config must report the fallback, not hide it: {broken!r}",
    )
    assert_true(
        broken["config"].get("timeout_seconds") is not None,
        "the runtime must survive a malformed config rather than lose its settings",
    )
    for stray in (legacy_root, bare_root, broken_root):
        shutil.rmtree(stray, ignore_errors=True)

    # The acceptance criterion for the whole v3.4.3 refactor: a provider with its OWN
    # session-id shape and log format can be added without touching core/. Asserted
    # rather than argued, because "the code looks general enough" is how it regresses.
    class _ProbeProvider:
        adapter = "test-provider"

        def __init__(self, command="probe-cli", timeout_seconds=0, on_progress=None):
            self.command = command
            self.timeout_seconds = timeout_seconds
            self.no_timeout = True
            self.on_progress = on_progress
            self.poll_interval = 1
            self.bootstrap_timeout_seconds = 30
            self.agent = None
            self.last_call_meta = {}
            self.on_session_created = None

        @staticmethod
        def extract_session_id(text):
            for line in (text or "").splitlines():
                if line.startswith("sid="):
                    return line[4:].strip()
            return None

        @staticmethod
        def clean_output(text):
            return "\n".join(
                l for l in (text or "").splitlines() if not l.startswith("sid=")
            ).strip()

        def probe(
            self, session_id=None, model=None, work_dir=None, timeout_seconds=None
        ):
            return {"ok": True, "content": "alive", "meta": {}}

        def run(self, prompt, session, model=None, work_dir=None):
            return {"ok": True, "content": "[EVIDENCE]", "meta": {}}

    from adapters.base import SecondAgentAdapter

    register(_ProbeProvider)
    assert_true(
        isinstance(_ProbeProvider(), SecondAgentAdapter),
        "a provider implementing run/probe/parsers must satisfy the contract without "
        "inheriting anything — the Protocol must not demand OpenCode's internal shape",
    )
    atomic_write_json(
        workflow_dir / "config.json", {"runtime": {"second_agent": "test-provider"}}
    )
    assert_true(
        provider_for(temp_root) == "test-provider",
        "config.json `second_agent` must SELECT the provider; it was inert before v3.4.3",
    )
    assert_true(
        resolve_adapter(project_root=temp_root).adapter == "test-provider",
        "resolution must follow the workspace config, not the built-in default",
    )
    assert_true(
        _ProbeProvider.extract_session_id("sid=abc123") == "abc123",
        "a provider's own session-id shape must be honoured, not OpenCode's ses_ prefix",
    )
    shutil.rmtree(temp_root, ignore_errors=True)
