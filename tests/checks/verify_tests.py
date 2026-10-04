"""Runtime-run tests for /.verify: allowlisted, shell-free, and written into the verdict."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from core.evidence import verify_tests
from core.evidence.contract import validate_verification_contract
from core.runtime.state import ensure_workflow_workspace
from core.workspace.workspace_paths import read_json_file, workflow_paths
from tests.checks.support import assert_true

_REVIEW = """[VERIFICATION]
verdict: DONE
blocking_findings:
- none
escalations:
- none
notes:
- none
checks_run:
- traced the change and its callers
not_verified:
- none
confidence: high
[DIGEST]
summary: clean
"""


def _workspace(prefixes: list[str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="verify-tests-"))
    ensure_workflow_workspace(root, os.getenv("AGENT_PATH"))
    config_path = workflow_paths(root)["config"]
    config = read_json_file(config_path)
    config.setdefault("commands", {})["verify_test_commands"] = prefixes
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return root


def _request(root: Path, body) -> None:
    path = verify_tests.request_path(root, "s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _verify(root: Path) -> tuple[dict, dict]:
    result = verify_tests.apply(root, "s1", {"ok": True, "content": _REVIEW, "meta": {}})
    return result, validate_verification_contract(result["content"])


def _test_verify_runtime_tests() -> None:
    python = f'"{sys.executable}"'
    root = _workspace([f"{python} -c"])
    try:
        # --- green tests and a clean review: pass ------------------------------------------
        _request(root, {"commands": [f"{python} -c pass"], "reason": "covers the change"})
        result, verdict = _verify(root)
        assert_true(
            verdict["verdict"] == "pass" and "runtime:" in result["content"],
            f"green tests and a clean review derive pass: {verdict['verdict']}\n{result['content']}",
        )
        assert_true(
            not verify_tests.request_path(root, "s1").exists(),
            "the request is consumed, so a later verify never reruns an old choice",
        )
        runtime = result["meta"]["runtime_tests"]
        assert_true(
            runtime["requested"] and [o["status"] for o in runtime["outcomes"]] == ["passed"],
            f"the outcome rides on the result meta: {runtime}",
        )

        # --- a failing test is a blocking finding, whatever the review said ---------------
        _request(root, {"commands": [f"{python} -c exit(3)"], "reason": "covers the change"})
        result, verdict = _verify(root)
        assert_true(
            verdict["verdict"] == "fail" and verdict["declared_verdict"] == "NEEDS FIX",
            f"a red test fails the verify: {verdict}",
        )
        assert_true("exited 3" in result["content"], "the finding names the exit code")

        # --- no request: a gap, never a silent pass ----------------------------------------
        result, verdict = _verify(root)
        assert_true(
            verdict["verdict"] == "incomplete" and "no test request" in result["content"],
            f"a verify without a test request is incomplete: {verdict['verdict']}",
        )

        # --- nothing to test, said with a reason: pass --------------------------------------
        _request(root, {"commands": [], "reason": "documentation only"})
        result, verdict = _verify(root)
        assert_true(
            verdict["verdict"] == "pass" and "documentation only" in result["content"],
            f"an explicit 'no test covers this' keeps the review's verdict: {verdict['verdict']}",
        )

        # --- refused: outside the allowlist, or shell syntax ---------------------------------
        _request(root, {"commands": ["git status", f"{python} -c pass && whoami"], "reason": "r"})
        result, verdict = _verify(root)
        statuses = [(o["status"], o["detail"]) for o in result["meta"]["runtime_tests"]["outcomes"]]
        assert_true(
            [s for s, _ in statuses] == ["refused", "refused"]
            and "verify_test_commands" in statuses[0][1]
            and "shell" in statuses[1][1]
            and verdict["verdict"] == "incomplete",
            f"a command outside the allowlist or with shell syntax never runs, and is a gap: {statuses}",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # --- an empty allowlist (the default) runs nothing ---------------------------------------
    root = _workspace([])
    try:
        _request(root, {"commands": [f"{python} -c pass"], "reason": "r"})
        result, verdict = _verify(root)
        assert_true(
            result["meta"]["runtime_tests"]["outcomes"][0]["status"] == "refused"
            and verdict["verdict"] == "incomplete",
            "with no allowlist configured, no requested command runs",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
