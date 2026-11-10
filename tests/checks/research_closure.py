"""Closure harness integrity; expensive designed measurements are explicitly selected."""
from __future__ import annotations

import copy

from tests.checks.support import assert_true
from tools.maintain.research_closure import OUTPUT, REPO, aggregate, confined, measure


def _test_research_closure_harness() -> None:
    good = {"case_id": "a", "status": "pass", "evidence_kind": "designed-fixture",
            "provenance": {"exoma_pin": "fixture"}, "checks": [{"passed": True}]}
    assert_true(aggregate([good], ["a"])["status"] == "pass", "complete evidence is a technical pass")
    for status, expected in (("fail", "fail"), ("blocked", "incomplete"), ("not_run", "incomplete")):
        changed = {**good, "status": status}
        assert_true(aggregate([changed], ["a"])["status"] == expected, f"{status} cannot become pass")
    for name, rows in (("missing", []), ("duplicate", [good, good]), ("unexpected", [{**good, "case_id": "b"}]),
                       ("no provenance", [{**good, "provenance": {}}]),
                       ("unsupported pass", [{**good, "checks": []}]),
                       ("failed assertion", [{**good, "checks": [{"passed": False}]}])):
        assert_true(aggregate(rows, ["a"])["status"] == "fail", f"{name} fails closed")
    original = copy.deepcopy(good)
    aggregate([good], ["a"])
    assert_true(good == original, "aggregation does not rewrite recorded evidence")
    assert_true(confined(OUTPUT / "fixture") == (OUTPUT / "fixture").resolve(), "fixture output is accepted")
    for path in (REPO / "outside.json", OUTPUT / "../escape.json"):
        try:
            confined(path)
        except ValueError:
            pass
        else:
            raise AssertionError("output outside fixture boundary must be refused")


def _measure_research_closure() -> None:
    """Opt-in measurement; fixture failures remain visible as a nonzero test result."""
    result = measure()
    failed = {case["case_id"]: {
        "status": case["status"], "error": case.get("error"),
        "failed_checks": [row for row in case.get("checks", []) if not row["passed"]],
    } for case in result["cases"] if case["status"] != "pass"}
    assert_true(result["summary"]["status"] == "pass", f"designed measurement failures (not a product patch request): {failed}; artifacts: {result['artifact_root']}")
