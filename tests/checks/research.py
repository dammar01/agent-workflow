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


_RQ = {
    "schema_version": 3,
    "id": "RQ-90",
    "question": "Does it hold?",
    "date": "2026-09-29",
    "status": "unanswered",
    "what_exists": "w",
    "records": [],
}

_VALIDATED = {
    "implementation_status": "implemented", "validation_status": "validated",
    "validated_by": "maintainer", "validated_on": "2026-10-03",
}
_PREDICATES = {"uses": 3, "consistent": True, "maintainer_decision": "kept validated"}
_DECISION = {"date": "2026-10-03", "method": "critical_interview", "summary": "s"}


def _yaml(record: dict) -> str:
    import yaml

    return yaml.safe_dump(record, sort_keys=False, allow_unicode=True)


def _check_research_questions(check_research, research: Path, root: Path, write, errors_for) -> None:
    """RQ records (DEC-017, DEC-019): ID grammar, versions, binary status, and the links both ways."""
    dec2 = {**_DEC, "schema_version": 3, "questions": ["RQ-90"]}

    # The current version names its questions; a frozen version-1 or version-2 record keeps
    # the format it was tracked in.
    no_questions = errors_for("decisions", "DEC-900-a.yaml", {**_DEC, "schema_version": 3})
    assert_true("name the research question" in no_questions, f"a current record without questions is refused:\n{no_questions}")
    assert_true(errors_for("decisions", "DEC-900-a.yaml", _DEC) == "", "a tracked version-1 record stays valid (frozen)")
    unfrozen = errors_for("decisions", "DEC-907-a.yaml", {**_DEC, "id": "DEC-907"})
    assert_true("schema_version must be 3" in unfrozen, f"a version-1 record outside the frozen list is refused:\n{unfrozen}")
    frozen_v2 = {**_DEC, "id": "DEC-903", "schema_version": 2, "questions": ["RQ-91"]}
    rq91 = write("questions", "RQ-91-a.yaml", {**_RQ, "id": "RQ-91", "records": ["DEC-903"]})
    assert_true(errors_for("decisions", "DEC-903-a.yaml", frozen_v2) == "", "a tracked version-2 record on the frozen list stays valid")
    rq91.unlink()
    unfrozen_v2 = errors_for("decisions", "DEC-904-a.yaml", {**frozen_v2, "id": "DEC-904"})
    assert_true("schema_version must be 3" in unfrozen_v2, f"a version-2 record outside the frozen list is refused:\n{unfrozen_v2}")
    old_rq = errors_for("questions", "RQ-90-a.yaml", {**_RQ, "schema_version": 2})
    assert_true("schema_version must be 3" in old_rq, f"an RQ is always the current version:\n{old_rq}")
    half = errors_for("questions", "RQ-90-a.yaml", {**_RQ, "status": "partial"})
    assert_true("not in rq_status" in half, f"a question is never half-answered:\n{half}")

    # ID grammar: RQ-NN with an optional letter; the other types keep three digits.
    assert_true(errors_for("questions", "RQ-07a-sub.yaml", {**_RQ, "id": "RQ-07a"}) == "", "RQ-07a is a valid question ID")
    for directory, bad in (("questions", "RQ-7-a.yaml"), ("questions", "RQ-900-a.yaml"), ("decisions", "DEC-90-a.yaml")):
        out = errors_for(directory, bad, _RQ)
        assert_true("file name must be" in out, f"{bad} is refused:\n{out}")

    # Both ways: the RQ lists the record, and the record names the RQ.
    rq_path = write("questions", "RQ-90-a.yaml", {**_RQ, "records": ["DEC-900"]})
    assert_true(errors_for("decisions", "DEC-900-a.yaml", dec2) == "", "an RQ and the record it lists pass together")
    one_way = errors_for("decisions", "DEC-900-a.yaml", {**dec2, "questions": ["RQ-91"]})
    assert_true("whose questions do not name RQ-90" in one_way, f"a listed record that does not name the RQ is refused:\n{one_way}")
    rq_path.unlink()
    rq_path = write("questions", "RQ-90-a.yaml", _RQ)
    unlisted = errors_for("decisions", "DEC-900-a.yaml", dec2)
    assert_true("whose records do not list DEC-900" in unlisted, f"a tracked record its RQ does not list is refused:\n{unlisted}")
    rq_path.unlink()

    # An RQ lists tracked records only, and an answered one lists at least one.
    drafted = errors_for("questions", "RQ-90-a.yaml", {**_RQ, "records": ["H-777"]})
    assert_true("is not a tracked record" in drafted, f"an RQ listing a draft is refused:\n{drafted}")
    answered = errors_for("questions", "RQ-90-a.yaml", {**_RQ, "status": "answered"})
    assert_true("answered question lists at least one" in answered, f"an answered RQ without records is refused:\n{answered}")

    # An answered RQ (DEC-019): a validated record and the maintainer's decision.
    dec_path = write("decisions", "DEC-900-a.yaml", dec2)
    unproven = errors_for("questions", "RQ-90-a.yaml", {**_RQ, "status": "answered", "records": ["DEC-900"], "decision": _DECISION})
    assert_true("at least one validated record" in unproven, f"an answered RQ whose records are not validated is refused:\n{unproven}")
    dec_path.unlink()
    dec_path = write("decisions", "DEC-900-a.yaml", {
        **dec2, "disposition": _VALIDATED,
        "validation": {"summary": "s", "runs": [{"date": "2026-10-03", "job_id": "j", "description": "d", "clean": True}], **_PREDICATES},
    })
    undecided = errors_for("questions", "RQ-90-a.yaml", {**_RQ, "status": "answered", "records": ["DEC-900"]})
    assert_true("maintainer's decision" in undecided, f"an answered RQ without a decision is refused:\n{undecided}")
    settled = {**_RQ, "status": "answered", "records": ["DEC-900"], "decision": _DECISION}
    assert_true(errors_for("questions", "RQ-90-a.yaml", settled) == "", "a validated record plus a decision answers the question")
    dec_path.unlink()

    # The questions index is generated in RQ order; the README inventory leaves RQs out.
    write("questions", "RQ-07a-sub.yaml", {**_RQ, "id": "RQ-07a"})
    write("questions", "RQ-10-b.yaml", {**_RQ, "id": "RQ-10"})
    code, out = _run_tool(check_research, [])
    assert_true(code == 1 and "questions index is stale" in out, f"a stale questions index fails:\n{out}")
    code, out = _run_tool(check_research, ["--write-inventory"])
    index = (research / "questions.md").read_text(encoding="utf-8")
    readme = (research / "README.md").read_text(encoding="utf-8")
    assert_true(code == 0 and index.index("RQ-07a") < index.index("RQ-10"), f"--write-inventory lists RQs in order:\n{out}\n{index}")
    assert_true("RQ-07a" not in readme, "the README inventory leaves RQ records out")

    # Drafts are version 2 and never duplicate a tracked ID; _archive is not read.
    drafts = root / "docs" / "research-drafts"
    (drafts / "decisions").mkdir(parents=True, exist_ok=True)
    draft = drafts / "decisions" / "DEC-905-d.yaml"
    draft.write_text(_yaml({**_DEC, "id": "DEC-905"}), encoding="utf-8")
    code, out = _run_tool(check_research, ["--drafts"])
    assert_true("DEC-905-d.yaml: schema_version must be 3" in out, f"a version-1 draft is refused:\n{out}")
    draft.unlink()
    duplicate = drafts / "questions" / "RQ-10-b.yaml"
    duplicate.parent.mkdir(exist_ok=True)
    duplicate.write_text(_yaml({**_RQ, "id": "RQ-10"}), encoding="utf-8")
    code, out = _run_tool(check_research, [])
    assert_true("duplicate id RQ-10" in out, f"a draft duplicating a tracked ID is refused:\n{out}")
    duplicate.unlink()
    archived = drafts / "decisions" / "_archive" / "DEC-906-old.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("# old\n", encoding="utf-8")
    code, out = _run_tool(check_research, ["--drafts"])
    assert_true("DEC-906" not in out, f"an archived draft is not read:\n{out}")


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
    saved = (check_research.RESEARCH, check_research.DRAFTS, check_research.SCHEMA, check_research.README, check_research.REPO_ROOT, check_research.QUESTIONS)
    saved_frozen = (check_research.FROZEN_V1, check_research.FROZEN_V2)
    # The fixtures below are version-1 records; they stand in for the frozen list.
    check_research.FROZEN_V1 = frozenset({"DEC-900", "DEC-901", "DEC-902", "H-900", "H-901", "EXP-900"})
    check_research.FROZEN_V2 = frozenset({"DEC-903"})
    try:
        research = root / "docs" / "research"
        research.mkdir(parents=True)
        shutil.copy(saved[2], research / "schema.yaml")
        (research / "README.md").write_text(
            f"# r\n\n{check_research.INVENTORY_BEGIN}\n{check_research.INVENTORY_END}\n", encoding="utf-8"
        )
        (research / "questions.md").write_text(
            f"# q\n\n{check_research.QUESTIONS_BEGIN}\n{check_research.QUESTIONS_END}\n", encoding="utf-8"
        )
        check_research.QUESTIONS = research / "questions.md"
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
            "partially validated DEC needs a clean run": (
                "decisions", "DEC-900-a.yaml",
                {**_DEC, "disposition": {**_VALIDATED, "validation_status": "partially_validated"}},
                "clean direct-use run",
            ),
            "validated DEC was used repeatedly": ("decisions", "DEC-900-a.yaml", {**_DEC, "disposition": _VALIDATED}, "validation.uses >= 2"),
            "validated DEC is consistent": ("decisions", "DEC-900-a.yaml", {**_DEC, "disposition": _VALIDATED}, "validation.consistent: true"),
            "validated DEC names the decision": ("decisions", "DEC-900-a.yaml", {**_DEC, "disposition": _VALIDATED}, "validation.maintainer_decision"),
            "validated DEC is measured": (
                "decisions", "DEC-900-a.yaml",
                {**_DEC, "disposition": _VALIDATED, "validation": {"summary": "s", **_PREDICATES}},
                "a validated DEC is measured",
            ),
            "one use is not repeated": (
                "decisions", "DEC-900-a.yaml",
                {**_DEC, "disposition": _VALIDATED, "validation": {"summary": "s", **_PREDICATES, "uses": 1}},
                "validation.uses >= 2",
            ),
            "H carries no commit": ("hypotheses", "H-900-a.yaml", {**_HYP, "success_criteria": {**_HYP["success_criteria"], "commit": "abc1234"}}, "unknown key"),
            "tracked H has an outcome": ("hypotheses", "H-900-a.yaml", {**_HYP, "outcome": "pending"}, "research-drafts"),
            "reference prefix": ("hypotheses", "H-900-a.yaml", {**_HYP, "experiments": ["DEC-001"]}, "must reference one of: EXP"),
            "wrong directory": ("real-cases", "DEC-900-a.yaml", _DEC, "DEC records live in decisions/"),
        }
        for rule, (directory, name, record, expected) in cases.items():
            out = errors_for(directory, name, copy.deepcopy(record))
            assert_true(expected in out, f"rule '{rule}' is enforced (expected '{expected}'):\n{out}")

        # A validated DEC (DEC-019): the four predicates, measured by a clean run or by a
        # named telemetry figure.
        measured = {**_DEC, "disposition": _VALIDATED, "validation": {
            "summary": "s", **_PREDICATES, "measurements": [{"source": "quality.jsonl rows 1-9", "value": "9 runs"}],
        }}
        assert_true(errors_for("decisions", "DEC-900-a.yaml", measured) == "", "a validated DEC measured by telemetry passes")
        by_run = {**_DEC, "disposition": _VALIDATED, "validation": {
            "summary": "s", **_PREDICATES, "runs": [{"date": "2026-10-03", "job_id": "j", "description": "d", "clean": True}],
        }}
        assert_true(errors_for("decisions", "DEC-900-a.yaml", by_run) == "", "a validated DEC measured by a clean run passes")

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
        (research / "decisions" / "DEC-900-a-decision.yaml").unlink()
        _check_research_questions(check_research, research, root, write, errors_for)
    finally:
        (check_research.RESEARCH, check_research.DRAFTS, check_research.SCHEMA, check_research.README, check_research.REPO_ROOT, check_research.QUESTIONS) = saved
        check_research.FROZEN_V1, check_research.FROZEN_V2 = saved_frozen
        shutil.rmtree(root, ignore_errors=True)
