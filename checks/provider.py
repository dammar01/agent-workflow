"""The v3.4.3 provider seam: registry selection and key migration."""

import json
import os
import shutil
import subprocess
import sys
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
    # A key the runtime does not know is a THIRD outcome, distinct from both of the above:
    # the file parses, its valid keys apply, and only the misspelled one is inert. Reported
    # as warnings rather than an error precisely so it does not discard the working keys —
    # asserted here because "unknown key" silently succeeding is the failure this closes.
    typo_root = Path(tempfile.mkdtemp(prefix="provider-typo-"))
    typo_dir = typo_root / ".workflow"
    typo_dir.mkdir(parents=True)
    atomic_write_json(
        typo_dir / "second_agent.json",
        {"timeout_second": 3600, "max_probes": "five", "idle_stall_seconds": 111},
    )
    typo = resolve_provider_config_for(typo_root)
    assert_true(
        typo["error"] is None and typo["source"] == "project",
        f"a typo must not be treated as an unreadable file: {typo!r}",
    )
    assert_true(
        any("timeout_second" in w for w in typo["warnings"]),
        f"an unknown key must be reported, not silently accepted: {typo['warnings']!r}",
    )
    assert_true(
        any("max_probes" in w for w in typo["warnings"]),
        f"a wrong-typed knob must be reported too: {typo['warnings']!r}",
    )
    assert_true(
        typo["config"].get("idle_stall_seconds") == 111,
        "the keys that ARE correct must still apply alongside the warnings",
    )

    # Every knob in config/settings.py is parsed at import time, so a mistyped env var used
    # to raise before argparse ran — taking `doctor` down with it, the one command whose job
    # is to say what broke. Asserted through a real subprocess import: the guarantee is about
    # module import, and calling the helper directly would not prove it.
    env = dict(os.environ, AI_PROXY_TIMEOUT_SECONDS="45m", AI_PROXY_MAX_PROBES="-3")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config.settings import DEFAULT_TIMEOUT_SECONDS, DEFAULT_MAX_PROBES;"
            "print(DEFAULT_TIMEOUT_SECONDS, DEFAULT_MAX_PROBES)",
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env,
        capture_output=True,
        text=True,
    )
    assert_true(
        probe.returncode == 0,
        f"a mistyped env var must not crash the import: {probe.stderr.strip()}",
    )
    assert_true(
        probe.stdout.split() == ["1800", "3"],
        f"a rejected env value must fall back to the built-in default: {probe.stdout!r}",
    )
    assert_true(
        "AI_PROXY_TIMEOUT_SECONDS" in probe.stderr
        and "AI_PROXY_MAX_PROBES" in probe.stderr,
        f"the warning must name the offending env var: {probe.stderr!r}",
    )

    for stray in (legacy_root, bare_root, broken_root, typo_root):
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
    _assert_codex_provider()
    shutil.rmtree(temp_root, ignore_errors=True)


def _assert_codex_provider() -> None:
    """Codex: the second real provider, and the proof the seam holds for shipped code.

    `_ProbeProvider` above shows a provider CAN be added without touching core/. Codex is
    the same claim tested against a provider that actually runs: a different session-id
    shape (a UUID in a JSON event, not a prefixed token), a different output format (JSONL
    events, not log lines), and a resume subcommand instead of a session flag.

    Everything here is argv- and parser-level. Nothing spawns codex — the CLI may not be
    installed, and a test that needs it would be skipped exactly where it matters.
    """
    from adapters.base import SecondAgentAdapter
    from adapters.codex_adapter import CodexAdapter
    from adapters.registry import available_providers, resolve_adapter
    from config.providers import bundle_for, provider_install_module

    assert_true(
        "codex" in available_providers(),
        f"codex must be registered: {available_providers()}",
    )
    assert_true(
        resolve_adapter("codex").adapter == "codex",
        "an explicit provider name must resolve to that provider's adapter",
    )
    assert_true(
        isinstance(CodexAdapter(), SecondAgentAdapter),
        "CodexAdapter must satisfy the Protocol without inheriting from it",
    )

    started = '{"type":"thread.started","thread_id":"019fe9cb-5a19-7303-b571-38d04f2d395a"}'
    assert_true(
        CodexAdapter.extract_session_id(started)
        == "019fe9cb-5a19-7303-b571-38d04f2d395a",
        "the thread id must be read out of the first JSONL event",
    )
    stream = "\n".join(
        [
            started,
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"[EVIDENCE]\\nfinal"}}',
            '{"type":"turn.completed","usage":{"output_tokens":5}}',
        ]
    )
    assert_true(
        CodexAdapter.clean_output(stream) == "[EVIDENCE]\nfinal",
        "clean_output must return the LAST agent_message, not the first or the raw stream",
    )
    assert_true(
        CodexAdapter.clean_output("plain text, no events") == "plain text, no events",
        "a non-JSONL stream must degrade to its text rather than to an empty answer",
    )

    adapter = CodexAdapter(command="codex")
    fresh = adapter._build_args(
        resume_id=None, model=None, cwd="/tmp/project", last_message=None
    )
    assert_true(
        "-" in fresh and "--sandbox" in fresh and "read-only" in fresh,
        f"a fresh call must read the prompt from stdin under read-only: {fresh}",
    )
    assert_true("-C" in fresh, f"a fresh call must set the working root: {fresh}")

    resumed = adapter._build_args(
        resume_id="019fe9cb-5a19-7303-b571-38d04f2d395a",
        model=None,
        cwd="/tmp/project",
        last_message=None,
    )
    assert_true(
        "resume" in resumed and "019fe9cb-5a19-7303-b571-38d04f2d395a" in resumed,
        f"a session must be continued via `exec resume <id>`: {resumed}",
    )
    assert_true(
        "-C" not in resumed and "--sandbox" not in resumed,
        f"`exec resume` accepts neither flag — passing them would fail the call: {resumed}",
    )
    assert_true(
        'sandbox_mode="read-only"' in resumed,
        f"a resumed call must re-assert the sandbox through -c: {resumed}",
    )

    # The seam that has not been fixed yet: config/settings.py defaults provider_command to
    # opencode's binary for every provider. Pointed at the wrong CLI, this adapter must
    # refuse rather than run it — a silent substitution would return evidence from a
    # provider nobody selected.
    misconfigured = CodexAdapter(command="opencode")
    refused = misconfigured.run("anything", {}, None, None)
    assert_true(
        not refused["ok"] and refused["meta"]["error_type"] == "command_not_found",
        f"codex must refuse a non-codex provider_command: {refused}",
    )
    assert_true(
        "provider_command" in refused["meta"]["next_action"],
        "the refusal must name the key that fixes it",
    )

    # The seam itself: defaults must follow the SELECTED provider. Before this, a config
    # naming codex came back holding opencode's binary and opencode's `plan` persona, and
    # the workspace backfill then wrote those wrong values into the file permanently.
    from config.settings import default_provider_config

    codex_defaults = default_provider_config("codex")
    assert_true(
        codex_defaults["provider_command"] == "codex"
        and codex_defaults["provider_agent"] is None,
        f"codex defaults must be codex's, not opencode's: {codex_defaults}",
    )
    opencode_defaults = default_provider_config("opencode")
    assert_true(
        opencode_defaults["provider_command"] == "opencode"
        and opencode_defaults["provider_agent"] == "plan",
        f"opencode defaults must be unchanged: {opencode_defaults}",
    )
    assert_true(
        set(codex_defaults) == set(opencode_defaults),
        "the key SET must stay provider-independent — validate_provider_config uses it "
        "to decide which keys are known",
    )
    assert_true(
        default_provider_config("provider-that-is-not-registered")["provider_command"]
        == "provider-that-is-not-registered",
        "an unregistered provider must not silently inherit another provider's binary",
    )

    # A project that selects codex must resolve to codex through the same path the
    # executor uses, without any env var set.
    selected = Path(tempfile.mkdtemp(prefix="agent-workflow-select-"))
    try:
        (selected / ".workflow").mkdir(parents=True, exist_ok=True)
        (selected / ".workflow" / "second_agent.json").write_text(
            json.dumps({"provider": "codex"}), encoding="utf-8"
        )
        from config.settings import (
            load_provider_config_for,
            resolve_provider_config_for,
        )
        from core.executor import Executor

        effective = load_provider_config_for(selected)
        assert_true(
            effective["provider_command"] == "codex",
            f"a file naming codex must not carry opencode's command: {effective['provider_command']}",
        )
        assert_true(
            resolve_provider_config_for(selected)["provider_explicit"],
            "a file that names a provider must be distinguishable from one that defaults",
        )
        assert_true(
            Executor()._adapter_for(selected).adapter == "codex",
            "the executor must resolve the provider the project selected, not the "
            "import-time default",
        )
        assert_true(
            Executor(adapter=OpenCodeAdapter())._adapter_for(selected).adapter
            == "opencode",
            "an injected adapter must still win outright — late resolution must not "
            "override what a caller handed in",
        )
        assert_true(
            Executor(provider="opencode")._adapter_for(selected).adapter == "opencode",
            "an explicitly pinned provider must win over the project's own selection",
        )
    finally:
        shutil.rmtree(selected, ignore_errors=True)

    # config.json alone must select too, and must outrank the TOOL-default file. The
    # tool default also names a provider; counting that as a project choice put it above
    # config.json and made the key inert again.
    via_config_json = Path(tempfile.mkdtemp(prefix="agent-workflow-cfg-"))
    try:
        (via_config_json / ".workflow").mkdir(parents=True, exist_ok=True)
        (via_config_json / ".workflow" / "config.json").write_text(
            json.dumps({"runtime": {"second_agent": "codex"}}), encoding="utf-8"
        )
        assert_true(
            Executor()._adapter_for(via_config_json).adapter == "codex",
            "`runtime.second_agent` in .workflow/config.json must select the provider "
            "the executor runs, not just the one install and probe look at",
        )
    finally:
        shutil.rmtree(via_config_json, ignore_errors=True)

    bundle = bundle_for("codex")
    module = provider_install_module("codex")
    for function in ("load_config", "merge_policy", "install_project_config"):
        assert_true(
            callable(getattr(module, function, None)),
            f"codex install module must answer {function}()",
        )
    assert_true(
        module.install_project_config(Path("."), ".")["status"] == "not_applicable",
        "codex ships no project boundary; that must be REPORTED, not silently skipped",
    )
    assert_true(
        bundle["instructions"] == ("AGENTS.md", "AGENTS.md") and not bundle["agents_dir"],
        f"codex ships instructions and no agent roster: {bundle}",
    )
