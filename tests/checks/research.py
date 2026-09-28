"""Research records are YAML the schema accepts, and the check refuses each rule it claims.

`tools/maintain/check_research.py` is run over the real corpus (every tracked record, the
templates, the generated README inventory), then over a temporary corpus built from one
valid record per rule, each broken the one way that rule forbids. A check that only ever saw
valid records would prove nothing about the rules: this is where each one is shown to bite.
"""

from __future__ import annotations

import contextlib
import copy
import io
import shutil
import sys
import tempfile
from pathlib import Path

from tests.checks.support import assert_true

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_DEC = {
    "schema_version": 1,
    "id": "DEC-900",
    "title": "A decision",
    "date": "2026-09-28",
    "status": "accepted",
    "provenance": {"type": "observed", "confidence": "medium"},
    "problem": "p",
    "decision": "d",
    "rationale": "r",
    "alternatives": [{"option": "o", "rejected_because": "b"}],
    "evidence": [],
    "implementation": {"version": "3.7.3", "commits": ["dd70afda3e1a70f85c90d7cdbaa52ae3feb80bb2"], "components": ["x.py"]},
    "disposition": {"implementation_status": "implemented", "validation_status": "not_validated"},
    "validation": {"summary": "s"},
    "trade_offs": {"better": ["b"], "worse": ["w"]},
    "limitations": ["l"],
    "revisit_when": ["r"],
}
_HYP = {
    "schema_version": 1,
    "id": "H-900",
    "title": "A hypothesis",
    "date": "2026-09-28",
    "status": "closed",
    "outcome": "supported",
    "provenance": {"type": "synthesized", "confidence": "low"},
    "statement": "s",
    "motivation": "m",
    "expected_effect": "e",
    "success_criteria": {"primary_metric": "m", "supported_when": "a", "rejected_when": "b", "data_recorded": True},
    "limitations": ["l"],
    "disposition": {"implementation_status": "not_applicable", "validation_status": "not_validated"},
}


_EXP = {
    "schema_version": 1,
    "id": "EXP-900",
    "title": "An experiment",
    "date": "2026-09-28",
    "status": "completed",
    "hypothesis": "H-900",
    "version_under_test": "3.7.3",
    "objective": "o",
    "setup": "s",
    "method": "m",
    "metrics": [{"name": "n", "definition": "d", "unit": "u"}],
    "results": {"summary": "r", "data_source": "file"},
    "threats_to_validity": ["t"],
    "outcome": "supported",
    "disposition": {"implementation_status": "not_applicable", "validation_status": "not_validated"},
}


def _run_tool(module, argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = module.main(argv)
    return code, buffer.getvalue()


def _test_research_records_follow_the_schema() -> None:
    try:
        import yaml
    except ImportError:
        raise AssertionError("PyYAML missing: pip install -r requirements-dev.txt (a development dependency)")
    from tools.maintain import check_research

    code, out = _run_tool(check_research, [])
    assert_true(code == 0, f"the tracked corpus passes its own schema:\n{out}")
    assert_true(not list((REPO_ROOT / "docs" / "research").rglob("*_TEMPLATE.md")), "templates are YAML now")

    # A temporary corpus: the real schema and README, one record per case.
    root = Path(tempfile.mkdtemp(prefix="research-check-"))
    saved = (check_research.RESEARCH, check_research.DRAFTS, check_research.SCHEMA, check_research.README, check_research.REPO_ROOT)
    try:
        research = root / "docs" / "research"
        research.mkdir(parents=True)
        shutil.copy(saved[2], research / "schema.yaml")
        (research / "README.md").write_text(
            f"# r\n\n{check_research.INVENTORY_BEGIN}\n{check_research.INVENTORY_END}\n", encoding="utf-8"
        )
        schema = yaml.safe_load((research / "schema.yaml").read_text(encoding="utf-8"))
        for type_spec in schema["types"].values():
            (research / type_spec["directory"]).mkdir()
            shutil.copy(saved[0] / type_spec["directory"] / "_TEMPLATE.yaml", research / type_spec["directory"] / "_TEMPLATE.yaml")
        check_research.REPO_ROOT, check_research.RESEARCH = root, research
        check_research.DRAFTS, check_research.SCHEMA, check_research.README = root / "docs" / "research-drafts", research / "schema.yaml", research / "README.md"

        def write(directory: str, name: str, record: dict) -> Path:
            path = research / directory / name
            path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
            return path

        def errors_for(directory: str, name: str, record: dict, *argv: str) -> str:
            path = write(directory, name, record)
            try:
                code, out = _run_tool(check_research, ["--write-inventory", *argv])
                return "" if code == 0 else out
            finally:
                path.unlink()

        assert_true(errors_for("decisions", "DEC-900-a-decision.yaml", _DEC) == "", "a complete DEC passes")
        assert_true(errors_for("hypotheses", "H-900-a-hypothesis.yaml", _HYP) == "", "a complete, carried-out H passes")

        cases = {
            "file name": ("decisions", "DEC-900_bad.yaml", _DEC, "file name must be"),
            "id matches file": ("decisions", "DEC-901-a.yaml", _DEC, "does not match the file name"),
            "required field": ("decisions", "DEC-900-a.yaml", {k: v for k, v in _DEC.items() if k != "rationale"}, "rationale: required"),
            "unknown key": ("decisions", "DEC-900-a.yaml", {**_DEC, "notes": "x"}, "unknown key"),
            "enum": ("decisions", "DEC-900-a.yaml", {**_DEC, "status": "done"}, "not in dec_status"),
            "nested enum": ("decisions", "DEC-900-a.yaml", {**_DEC, "provenance": {"type": "vibes", "confidence": "low"}}, "not in provenance_type"),
            "date": ("decisions", "DEC-900-a.yaml", {**_DEC, "date": "28/09/2026"}, "must be a date"),
            "commit shape": ("decisions", "DEC-900-a.yaml", {**_DEC, "implementation": {**_DEC["implementation"], "commits": ["HEAD"]}}, "is not a commit hash"),
            "implemented DEC names a commit": ("decisions", "DEC-900-a.yaml", {**_DEC, "implementation": {**_DEC["implementation"], "commits": []}}, "needs at least 1"),
            "tracked DEC is implemented": ("decisions", "DEC-900-a.yaml", {**_DEC, "disposition": {"implementation_status": "not_implemented", "validation_status": "not_validated"}}, "research-drafts"),
            "validated needs the maintainer": ("decisions", "DEC-900-a.yaml", {**_DEC, "disposition": {"implementation_status": "implemented", "validation_status": "validated"}}, "needs validated_by and validated_on"),
            "validated DEC needs a clean run": (
                "decisions", "DEC-900-a.yaml",
                {**_DEC, "disposition": {"implementation_status": "implemented", "validation_status": "validated", "validated_by": "maintainer", "validated_on": "2026-09-28"}},
                "clean direct-use run",
            ),
            "H carries no commit": ("hypotheses", "H-900-a.yaml", {**_HYP, "success_criteria": {**_HYP["success_criteria"], "commit": "abc1234"}}, "unknown key"),
            "tracked H has an outcome": ("hypotheses", "H-900-a.yaml", {**_HYP, "outcome": "pending"}, "research-drafts"),
            "reference prefix": ("hypotheses", "H-900-a.yaml", {**_HYP, "experiments": ["DEC-001"]}, "must reference one of: EXP"),
            "wrong directory": ("real-cases", "DEC-900-a.yaml", _DEC, "DEC records live in decisions/"),
        }
        for rule, (directory, name, record, expected) in cases.items():
            out = errors_for(directory, name, copy.deepcopy(record))
            assert_true(expected in out, f"rule '{rule}' is enforced (expected '{expected}'):\n{out}")

        # `uncommitted` is a warning, and an error only under --strict (a release).
        loose = {**_DEC, "implementation": {**_DEC["implementation"], "commits": ["uncommitted"]}}
        assert_true(errors_for("decisions", "DEC-900-a.yaml", loose) == "", "uncommitted passes while in the working tree")
        assert_true("(strict)" in errors_for("decisions", "DEC-900-a.yaml", loose, "--strict"), "and fails --strict")

        # An unresolved reference: an error when drafts are present to resolve it against.
        (root / "docs" / "research-drafts" / "hypotheses").mkdir(parents=True)
        dangling = {**_DEC, "evidence": ["CASE-777"]}
        assert_true("neither tracked nor a known draft" in errors_for("decisions", "DEC-900-a.yaml", dangling), "a reference that resolves nowhere is refused")
        (root / "docs" / "research-drafts" / "hypotheses" / "H-777-draft.md").write_text("# draft\n", encoding="utf-8")
        assert_true(errors_for("decisions", "DEC-900-a.yaml", {**_DEC, "evidence": ["H-777"]}) == "", "a draft ID resolves while drafts exist")

        # H and EXP name each other, both ways.
        hyp_path = write("hypotheses", "H-900-a-hypothesis.yaml", {**_HYP, "experiments": ["EXP-900"]})
        assert_true(errors_for("experiments", "EXP-900-an-experiment.yaml", _EXP) == "", "an EXP and the H that lists it pass together")
        one_way = errors_for("experiments", "EXP-900-an-experiment.yaml", {**_EXP, "hypothesis": "H-901"})
        assert_true("experiments lists EXP-900, whose hypothesis is H-901" in one_way, f"an H listing an EXP that names another H is refused:\n{one_way}")
        hyp_path.unlink()
        write("hypotheses", "H-900-a-hypothesis.yaml", _HYP)
        unlisted = errors_for("experiments", "EXP-900-an-experiment.yaml", _EXP)
        (research / "hypotheses" / "H-900-a-hypothesis.yaml").unlink()
        assert_true("does not list EXP-900 under experiments" in unlisted, f"an EXP its H does not list back is refused:\n{unlisted}")

        # Drafts are YAML too, once drafts are checked.
        code, out = _run_tool(check_research, ["--write-inventory", "--drafts"])
        assert_true(code == 1 and "drafts are YAML too" in out, f"a Markdown draft is refused under --drafts:\n{out}")

        # Markdown records are refused; the inventory is generated and checked.
        stray = research / "decisions" / "DEC-902-old.md"
        stray.write_text("# DEC-902\n", encoding="utf-8")
        code, out = _run_tool(check_research, [])
        stray.unlink()
        assert_true(code == 1 and "research records are YAML" in out, f"a Markdown record is refused:\n{out}")
        write("decisions", "DEC-900-a-decision.yaml", _DEC)
        code, out = _run_tool(check_research, [])
        assert_true(code == 1 and "inventory is stale" in out, f"an inventory that misses a record fails:\n{out}")
        code, _ = _run_tool(check_research, ["--write-inventory"])
        readme = (research / "README.md").read_text(encoding="utf-8")
        assert_true(code == 0 and "[DEC-900](decisions/DEC-900-a-decision.yaml)" in readme and "dd70afd" in readme, f"--write-inventory lists it with its commit:\n{readme}")
    finally:
        (check_research.RESEARCH, check_research.DRAFTS, check_research.SCHEMA, check_research.README, check_research.REPO_ROOT) = saved
        shutil.rmtree(root, ignore_errors=True)
