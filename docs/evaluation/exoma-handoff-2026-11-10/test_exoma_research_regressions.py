"""Portable regressions for Exoma. All command strings are test DATA, never executed.

Run in Exoma's existing development environment:
    python -B test_exoma_research_regressions.py --policy-file PATH/TO/workflow_policy.py
The current inspected sources are expected to fail four parser cases and the quoted-mention
gate case. Do not change expectations merely to make this suite green.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

VALIDATOR_CASES = [
    ("plain_none", "none", "pass"),
    ("period_none", "none.", "pass"),
    ("indonesian_none", "tidak ada", "pass"),
    ("neutral_dash", "none - clean", "pass"),
    ("neutral_semicolon", "none; all clean", "pass"),
    ("finding_after_dash", "none - caller X fails", "fail"),
    ("tagged_finding", "[severity:high] [origin:introduced] [scope_relation:in_scope] caller fails [fixture.py:1]", "fail"),
    ("period_continuation", "none.\n  caller X fails [fixture.py:1]", "fail"),
    ("problem_parenthetical", "none (caller X fails)", "fail"),
    ("plain_continuation", "none\n  caller X fails [fixture.py:1]", "fail"),
]

def fixture(item):
    return ("[VERIFICATION]\nverdict: DONE\nconfidence: high\nblocking_findings:\n- " + item +
            "\nescalations:\n- none\nnotes:\n- none\nchecks_run:\n- read fixture.py:1\nnot_verified:\n- none\n")

def interfaces():
    # This is an isolated test import, never the user's Exoma execution/approval home.
    with tempfile.TemporaryDirectory(prefix="exoma-regression-import-") as scratch:
        with patch.dict(os.environ, {"EXOMA_HOME": scratch}, clear=False):
            import exoma
            from exoma.core.evidence.contract import validate_verification_contract, verify_exit_status
            from exoma.core.evidence.e2e import normalize
    return Path(exoma.__file__).resolve().parent, validate_verification_contract, verify_exit_status, normalize

class ParserRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, validate, exit_status, normalize = interfaces()
        cls.validate = staticmethod(validate)
        cls.exit_status = staticmethod(exit_status)
        cls.normalize = normalize

    def test_normalizer_period_continuation_preserves_finding(self):
        actual = self.normalize._reviewer_sections(fixture("none.\n  caller X fails [fixture.py:1]"))
        self.assertTrue(actual["blocking_findings"])

    def test_normalizer_plain_continuation_preserves_finding(self):
        actual = self.normalize._reviewer_sections(fixture("none\n  caller X fails [fixture.py:1]"))
        self.assertTrue(actual["blocking_findings"])

def validator_test(item, expected):
    def run(self):
        actual = self.validate(fixture(item))
        self.assertEqual(actual["verdict"], expected)
        self.assertEqual(self.exit_status(actual["verdict"], actual), 0 if expected == "pass" else 2)
    return run

for name, item, expected in VALIDATOR_CASES:
    setattr(ParserRegressions, "test_validator_" + name, validator_test(item, expected))

class GateRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package, _, _, _ = interfaces()
        supplied = os.environ.get("EXOMA_REGRESSION_POLICY_FILE")
        policy_path = Path(supplied) if supplied else package / "_bundle/config/hosts/claude/hooks/workflow_policy.py"
        if not policy_path.is_file():
            raise RuntimeError("Pass --policy-file pointing to the Exoma source policy under test")
        spec = importlib.util.spec_from_file_location("exoma_research_policy_under_test", policy_path)
        cls.policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.policy)
        cls.scratch = tempfile.TemporaryDirectory(prefix="exoma-gate-regression-")
        cls.addClassCleanup(cls.scratch.cleanup)

    def action_text(self):
        return " ".join(("git", "commit"))

    def test_authorization_is_scoped_to_session(self):
        a = self.policy.runtime_dir(self.scratch.name, "session-A")
        b = self.policy.runtime_dir(self.scratch.name, "session-B")
        self.assertNotEqual(a, b)
        marker_a = os.path.join(a, "commit.marker")
        with patch.object(self.policy, "marker_fresh", side_effect=lambda p: p == marker_a):
            self.assertIsNone(self.policy.commit_refusal(self.action_text(), a))
            self.assertIsNotNone(self.policy.commit_refusal(self.action_text(), b))

    def test_unapproved_action_still_refused(self):
        rt = self.policy.runtime_dir(self.scratch.name, "session-A")
        with patch.object(self.policy, "marker_fresh", return_value=False):
            actual = self.policy.commit_refusal(self.action_text(), rt)
        self.assertIsNotNone(actual)
        self.assertEqual(actual[0], "commit_unapproved")

    def test_quoted_mention_is_not_an_action(self):
        rt = self.policy.runtime_dir(self.scratch.name, "session-A")
        harmless = "python -c " + json.dumps("print(" + repr(self.action_text()) + ")")
        # Never execute harmless or action_text: only pass strings to the policy.
        with patch.object(self.policy, "marker_fresh", return_value=False):
            self.assertIsNone(self.policy.commit_refusal(harmless, rt))

    def test_existing_directory_scope_still_matches(self):
        self.assertTrue(self.policy.in_scope("docs/research/schema.yaml", ["docs/research/"]))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--policy-file")
    args, remaining = parser.parse_known_args()
    if args.policy_file:
        os.environ["EXOMA_REGRESSION_POLICY_FILE"] = str(Path(args.policy_file).resolve())
    unittest.main(argv=[__file__, *remaining])
