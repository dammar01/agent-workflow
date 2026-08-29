"""Secret redaction at the adapter boundary."""

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

def _test_redaction_boundary() -> None:
    """Secrets and raw prompt argv must not survive any adapter result path."""
    secret = "sk-" + "A" * 36
    adapter = OpenCodeAdapter(command="opencode", timeout_seconds=5)

    def outcome(*, returncode: int, timed_out: bool) -> dict:
        return {
            "stdout": "safe response" if not timed_out else "",
            "stderr": f"provider diagnostic {secret}",
            "returncode": returncode,
            "timed_out": timed_out,
            "duration_seconds": 0.1,
            "idle_seconds": 0.0,
            "kill": None,
        }

    for returncode, timed_out in ((0, False), (1, False), (1, True)):
        adapter._popen_capture = (  # type: ignore[method-assign]
            lambda *args, rc=returncode, to=timed_out, **kwargs: outcome(
                returncode=rc, timed_out=to
            )
        )
        result = adapter._run_args(["opencode", "run", f"prompt {secret}"])
        serialized = json.dumps(result)
        assert_true(secret not in serialized, f"secret leaked from adapter result: {result}")
        assert_true(
            "\"args\"" not in serialized and "prompt " not in serialized,
            f"raw prompt argv leaked from adapter metadata: {result}",
        )
        assert_true(
            (result.get("meta") or {}).get("redaction_count", 0) > 0,
            f"redaction must remain auditable on every result path: {result}",
        )

    existing = RecordingOpenCodeAdapter()
    callback_calls: list[str] = []
    existing.on_session_created = callback_calls.append
    existing_result = existing.run(
        "prompt", {"session_id": "workflow", "provider_session_id": "ses_existing"}
    )
    assert_true(
        existing_result["ok"] and not callback_calls,
        "an existing OpenCode session must not invoke the new-session persistence callback",
    )

    bootstrap = OpenCodeAdapter(command="opencode", timeout_seconds=5)
    bootstrap._popen_capture = lambda *args, **kwargs: outcome(  # type: ignore[method-assign]
        returncode=1, timed_out=False
    )
    _session_id, bootstrap_meta = bootstrap.init_session()
    assert_true(
        secret not in json.dumps(bootstrap_meta)
        and bool(bootstrap_meta.get("stderr_tail")),
        f"bootstrap failures must retain sanitized diagnostics: {bootstrap_meta}",
    )
