"""Quota-free staging regressions, executed in a separate interpreter."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def _test_e2e_staging() -> None:
    from tests.checks.support import assert_true

    with tempfile.TemporaryDirectory(prefix="staging-test-home-") as home:
        result = subprocess.run(
            # The Exoma runner may prepend its own installation to PYTHONPATH.
            # -I makes this child exercise the project's interpreter/installed pin.
            [sys.executable, "-I", "-B", str(Path(__file__).resolve())],
            cwd=ROOT, env={**os.environ, "EXOMA_HOME": home, "PYTHONUTF8": "1"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
    assert_true(result.returncode == 0, result.stdout + result.stderr)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.entry = importlib.import_module("tools.e2e.e2e")
        self.support = importlib.import_module("tools.e2e.e2e_support")
        self.delegated = importlib.import_module("tools.e2e.e2e_delegated")
        self.temporary = tempfile.TemporaryDirectory(prefix="staging-regression-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.silence = redirect_stdout(io.StringIO())
        self.silence.__enter__()
        self.addCleanup(self.silence.__exit__, None, None, None)

    def run_main(self, arguments, dispatch=None, identity=None, known=None):
        report_path = self.root / "receipt.json"
        calls = []

        def complete(report, session, provider, model, **kwargs):
            calls.append((provider, model))
            if dispatch is not None:
                return dispatch(report, session, provider, model, **kwargs)
            for stage in self.support.PROVIDER_STAGES:
                report.stage(f"{provider}: {stage}", self.support.PASS, exit_code=0)

        identity = identity if identity is not None else {"verified": True, "expected_pin": "a" * 40}
        with patch.object(self.entry, "known_providers", return_value=known if known is not None else ["codex", "claude"]), \
             patch.object(self.entry, "dependency_identity", return_value=identity) as probe, \
             patch.object(self.entry, "delegated_checks", side_effect=complete):
            code = self.entry.main([*arguments, "--report-json", str(report_path)])
        return code, json.loads(report_path.read_text(encoding="utf-8")), calls, probe.call_count

    def test_all_missing_model_refuses_before_any_dispatch(self):
        code, receipt, calls, probes = self.run_main(["--full", "--all", "--model", "codex=fixture"])
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertEqual(probes, 0)
        self.assertEqual(receipt["verdict"], "fail")
        self.assertTrue(all(not stage["executed"] for stage in receipt["stages"].values()))

    def test_full_skip_and_missing_coverage_cannot_pass(self):
        for dispatch in (
            lambda *args, **kwargs: None,
            lambda report, *args, **kwargs: report.stage("codex: explore", self.support.SKIP, executed=False),
        ):
            with self.subTest(dispatch=dispatch):
                code, receipt, _, _ = self.run_main(["--full", "--provider", "codex", "--model", "fixture"], dispatch)
                self.assertEqual(code, 1)
                self.assertEqual(receipt["verdict"], "fail")

    def test_single_and_multi_provider_complete_coverage(self):
        for arguments, expected in (
            (["--full", "--provider", "codex", "--model", "fixture"], [("codex", "fixture")]),
            (["--full", "--all", "--model", "codex=one,claude=two"], [("codex", "one"), ("claude", "two")]),
        ):
            with self.subTest(arguments=arguments):
                code, receipt, calls, _ = self.run_main(arguments)
                self.assertEqual(code, 0)
                self.assertEqual(calls, expected)
                self.assertEqual(receipt["verdict"], "pass")
                self.assertEqual(receipt["coverage"]["deployed_hooks"], "not_verified")
                self.assertEqual(receipt["coverage"]["plan_approval_execute_verify"], "not_run")

    def test_identity_failure_and_empty_matrix_do_not_dispatch(self):
        for options in (
            {"identity": {"verified": False, "error_type": "dependency_identity_mismatch"}},
            {"known": []},
        ):
            with self.subTest(options=options):
                code, receipt, calls, probes = self.run_main(["--full", "--all", "--model", "codex=fixture,claude=fixture"] if "known" not in options else ["--full", "--all"], **options)
                self.assertEqual(code, 1)
                self.assertEqual(calls, [])
                self.assertEqual(receipt["verdict"], "fail")
                if "identity" in options:
                    self.assertEqual(probes, 1)
                    self.assertEqual(receipt["stages"]["dependency identity"]["status"], self.support.FAIL)

    def test_informational_run_does_not_claim_acceptance(self):
        inherited = os.environ.get("EXOMA_HOME")
        code, receipt, calls, probes = self.run_main([])
        self.assertEqual((code, calls, probes), (0, [], 0))
        self.assertEqual(receipt["verdict"], "not_run")
        self.assertEqual(os.environ.get("EXOMA_HOME"), inherited)

    def test_staging_isolates_and_restores_host_interpreter_pin(self):
        host = self.root / "host"
        hooks = host / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "python.path").write_text(sys.executable, encoding="utf-8")
        from exoma.utils.osutil import pinned_python

        with patch.dict(os.environ, {"AGENT_HOME": str(host)}):
            self.assertEqual(pinned_python(), sys.executable)
            with self.support.staging_environment():
                self.assertIsNone(pinned_python())
                self.assertEqual(os.environ["AGENT_HOME"], os.environ["EXOMA_HOME"])
            self.assertEqual(os.environ["AGENT_HOME"], str(host))
            self.assertEqual(pinned_python(), sys.executable)

    def test_selection_errors_and_timeout_options(self):
        for provider, models, run_all in (
            ("unknown", [], False),
            (None, ["codex=one,codex=two"], False),
            (None, ["unknown=one"], False),
            (None, ["codex="], False),
            (None, ["one", "two"], False),
            (None, ["one"], False),
        ):
            with self.subTest(models=models):
                with self.assertRaises(ValueError):
                    self.entry.plan_runs(provider, models, run_all, ["codex", "claude"])
        for value in ("0", "-1", "nan", "inf", "text"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                self.entry.positive_seconds(value)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
            self.entry.main(["--full", "--provider", "unknown"])
        self.assertEqual(refused.exception.code, 2)

    def test_json_object_boundary(self):
        for text in ('[]', '[{"ok": true}]', '[\n{"ok": true}', 'null', 'true', '"text"', '42', '{"ok":', '{"outer":\n{"ok":true}', '[]\n{"ok": true}'):
            with self.subTest(text=text):
                self.assertIsNone(self.support._json_from(text))
        self.assertEqual(self.support._json_from('[WAIT] preparing\n{"ok": true}'), {"ok": True})
        self.assertEqual(self.support._json_from('log line\n{\n"ok": true\n}'), {"ok": True})

    def test_identity_requires_manifest_lock_and_installed_source(self):
        pin = "a" * 40
        (self.root / "pyproject.toml").write_text(f'[project]\ndependencies = ["exoma @ git+https://example.invalid/exoma.git@{pin}"]\n', encoding="utf-8")
        lock = self.root / "uv.lock"
        lock.write_text(f'[[package]]\nname = "exoma"\nsource = {{ git = "https://example.invalid/exoma.git#{pin}" }}\n', encoding="utf-8")
        installed = {"installed_pin": pin, "package_path": str(self.root / "package/__init__.py"), "distribution_path": str(self.root / "package")}
        with patch.object(self.support, "_installed_identity", return_value=installed):
            self.assertTrue(self.support.dependency_identity(self.root)["verified"])
            lock.write_text(f'[[package]]\nname = "exoma"\nsource = {{ git = "https://example.invalid/exoma.git#{"b" * 40}" }}\n', encoding="utf-8")
            self.assertFalse(self.support.dependency_identity(self.root)["verified"])
        lock.write_text(f'[[package]]\nname = "exoma"\nsource = {{ git = "https://example.invalid/exoma.git#{pin}" }}\n', encoding="utf-8")
        with patch.object(self.support, "_installed_identity", return_value={**installed, "installed_pin": "b" * 40}):
            self.assertFalse(self.support.dependency_identity(self.root)["verified"])
        with patch.object(self.support, "_installed_identity", return_value={**installed, "package_path": str(self.root / "shadow/__init__.py")}):
            self.assertFalse(self.support.dependency_identity(self.root)["verified"])
        lock.write_text("broken TOML !", encoding="utf-8")
        self.assertEqual(self.support.dependency_identity(self.root)["error_type"], "dependency_identity_unavailable")

    def test_installed_runtime_matches_the_locked_git_pin(self):
        identity = self.support.dependency_identity()
        self.assertTrue(identity["verified"], json.dumps(identity, sort_keys=True))

    def exercise_delegation(self, scenario="success"):
        from exoma.core.evidence.contract import REQUIRED_FIELDS

        report = self.support.Report()
        report.require(*(f"codex: {stage}" for stage in self.support.PROVIDER_STAGES))
        launched = []

        def cli(*args, **kwargs):
            self.assertEqual(kwargs["timeout"], 1.5)
            command = args[args.index("--command") + 1]
            if command == "init":
                if scenario == "init_failure":
                    return 1, '{"ok":true}', ""
                if scenario != "missing_runner":
                    workflow = Path(args[args.index("--work-dir") + 1]) / ".workflow"
                    workflow.mkdir()
                    (workflow / "run.sh").write_text("# fixture; never executed", encoding="utf-8")
            if command == "provider" and scenario == "provider_failure":
                return 0, "[]", ""
            # A benign stderr warning must not corrupt the stdout JSON contract.
            return 0, '{"ok":true}', "fixture warning on stderr\n"

        def process(args, **kwargs):
            if args[0] == "git":
                self.assertEqual(kwargs["timeout"], 1.5)
                return (1 if scenario == "git_failure" else 0), "", ""
            self.assertEqual(kwargs["timeout"], 2.5)
            command = args[2]
            launched.append(command)
            payload = {"ok": True, "content": "[evidence]\n" + "\n".join(REQUIRED_FIELDS.get("explore", ())) if command == "explore" else "no changes",
                       "evidence_ref": {"reused": scenario == "reused"}}
            if scenario == "failed_payload":
                payload["ok"] = False
            if scenario == "non_text":
                payload["content"] = []
            if scenario == "missing_fields":
                payload["content"] = "[evidence]"
            output = "[]" if scenario == "bad_json" else json.dumps(payload)
            code = None if scenario == "timeout" else (7 if scenario == "nonzero" else 0)
            return code, output, ""

        with patch.object(self.delegated, "_run_cli", side_effect=cli), \
             patch.object(self.delegated, "_run_process", side_effect=process), \
             patch.object(self.delegated, "find_bash", return_value=None if scenario == "missing_bash" else "fixture-bash"):
            self.delegated.delegated_checks(report, "fixture", "codex", "fixture-model", setup_timeout=1.5, delegated_timeout=2.5)
        report.complete_required()
        return report, launched

    def test_delegation_requires_successful_process_and_contract(self):
        report, launched = self.exercise_delegation()
        self.assertEqual(report.failed, 0)
        self.assertEqual(launched, ["explore", "sweep"])
        for scenario in ("git_failure", "init_failure", "missing_runner", "provider_failure", "missing_bash", "bad_json", "nonzero", "failed_payload", "non_text", "missing_fields", "reused", "timeout"):
            with self.subTest(scenario=scenario):
                report, _ = self.exercise_delegation(scenario)
                self.assertGreater(report.failed, 0)
                self.assertTrue(any(row["status"] != self.support.PASS for row in report.stages.values()))
                if scenario == "nonzero":
                    self.assertEqual(report.stages["codex: explore"]["exit_code"], 7)
                if scenario == "timeout":
                    self.assertTrue(report.stages["codex: explore"]["timed_out"])

    def test_bounded_timeout_cleans_owned_descendant(self):
        from exoma.utils.osutil import process_alive

        # This unrelated child must survive cleanup of the invocation being tested.
        outsider = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); time.sleep(30)"
            started = time.monotonic()
            status, stdout, _ = self.support._run_process([sys.executable, "-u", "-c", code], timeout=0.8)
            self.assertIsNone(status)
            self.assertLess(time.monotonic() - started, 6)
            child = int(stdout.strip().splitlines()[0])
            deadline = time.monotonic() + 2
            while process_alive(child) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(process_alive(child))
            self.assertIsNone(outsider.poll())
        finally:
            outsider.terminate()
            outsider.wait(timeout=5)
        status, stdout, _ = self.support._run_process([sys.executable, "-c", "print('ready')"], timeout=5)
        self.assertEqual((status, stdout.strip()), (0, "ready"))

    def test_missing_executable_has_a_named_failure(self):
        status, stdout, stderr = self.support._run_process([str(self.root / "missing-executable")], timeout=5)
        self.assertEqual((status, stdout, stderr), (127, "", "launch_error"))

    def test_real_cli_ignores_shadow_pythonpath_and_binds_generated_runner(self):
        shadow = self.root / "shadow"
        package = shadow / "exoma"
        package.mkdir(parents=True)
        marker = self.root / "shadow-cli-used"
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('wrong runtime')\nraise SystemExit(73)\n",
            encoding="utf-8",
        )
        project = self.root / "project"
        project.mkdir()
        # Real local init only: no provider configuration or network delegation.
        with self.support.staging_environment(), patch.dict(os.environ, {"PYTHONPATH": str(shadow)}):
            status, _, stderr = self.support._run_process(
                ["git", "init", "-q", str(project)], timeout=10,
            )
            self.assertEqual(status, 0, stderr)
            self.assertTrue(self.support.dependency_identity()["verified"])
            status, stdout, stderr = self.support._run_cli(
                "--command", "init", "--work-dir", str(project), "--pretty",
                cwd=project, timeout=30,
            )
            self.assertEqual(status, 0, stderr)
            self.assertIs(self.support._json_from(stdout)["ok"], True)
            self.assertFalse(marker.exists(), "child CLI imported the shadow runtime")
            script = (project / ".workflow/run.sh").read_text(encoding="utf-8")
            installed = self.support._installed_identity()
            self.assertIn(Path(sys.executable).as_posix(), script)
            package_root = Path(installed["distribution_path"]).parent.as_posix()
            self.assertIn('export PYTHONPATH="' + package_root, script)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    before = os.environ.get("EXOMA_HOME")
    importlib.import_module("tools.e2e.e2e")
    if os.environ.get("EXOMA_HOME") != before:
        raise AssertionError("importing staging changed EXOMA_HOME")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
