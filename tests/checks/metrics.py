"""The calculation contract: registry, its documentation, and the provenance stamp."""

import re
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.audit import metrics, telemetry
from tests.checks.support import assert_true

DOC = Path(__file__).resolve().parents[2] / "docs" / "evaluation" / "metrics.md"
_REQUIRED = {"version", "producer", "unit", "statistic", "definition", "denominator", "missing"}


def _test_metrics_contract() -> None:
    # --- every metric is complete, named apart, and documented at its version ------------
    for mid, metric in metrics.REGISTRY.items():
        assert_true(re.fullmatch(r"(runtime|real_use|outcome)\.[a-z_]+", mid), f"metric id shape: {mid}")
        assert_true(_REQUIRED <= set(metric), f"{mid} misses {_REQUIRED - set(metric)}")
        assert_true(metric["statistic"] in metrics.STATISTICS, f"{mid}: unknown statistic {metric['statistic']}")
    doc = DOC.read_text(encoding="utf-8")
    missing = [f"{mid}@{m['version']}" for mid, m in metrics.REGISTRY.items() if f"`{mid}@{m['version']}`" not in doc]
    assert_true(not missing, f"docs/evaluation/metrics.md is behind the registry: {missing}")
    assert_true(f"Registry version: **{metrics.METRICS_VERSION}**" in doc, "the page names the registry version")

    # --- the input hash depends on contents only, and on every byte of them --------------
    root = Path(tempfile.mkdtemp(prefix="metrics-"))
    try:
        a, b = root / "a.jsonl", root / "b.jsonl"
        a.write_text("one\n", encoding="utf-8")
        b.write_text("two\n", encoding="utf-8")
        first = metrics.inputs_digest([a, b])
        assert_true(first == metrics.inputs_digest([b, a]), "input order does not change the hash")
        assert_true(str(root) not in str(first), "the stamp carries no path")
        b.write_text("two!\n", encoding="utf-8")
        assert_true(metrics.inputs_digest([a, b])["sha256"] != first["sha256"], "a changed input changes the hash")

        # --- the runtime report stamps itself --------------------------------------------
        (root / ".workflow" / "data").mkdir(parents=True)
        stamp = telemetry.report(root)["provenance"]
        assert_true(
            stamp["metrics_version"] == metrics.METRICS_VERSION
            and set(stamp["metrics"]) == {m for m in metrics.REGISTRY if m.startswith("runtime.")}
            and "maintainer machine only" in stamp["recount"],
            f"report() carries its provenance: {stamp}",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # --- the offline outcome rule: defaults as documented, overridable, stamped ----------
    from tools.maintain import measure_task_outcomes as outcomes

    assert_true(
        (outcomes.SOLVED_SHARE, outcomes.PARTIAL_SHARE, outcomes.FIX_WINDOW.total_seconds())
        == (0.8, 0.3, 86400.0),
        "the outcome rule's defaults are the ones metrics.md documents",
    )
    assert_true(
        [outcomes.grade(s, True, f) for s, f in ((0.8, False), (0.8, True), (0.79, False), (0.3, False), (0.29, False))]
        == ["solved_clean", "solved_fixed", "partial", "partial", "not_in_main"]
        and outcomes.grade(1.0, False, False) == "no_change",
        "grade boundaries: 0.8 solved, 0.3 partial, no lines left is no_change",
    )
    saved = (outcomes.SOLVED_SHARE, outcomes.PARTIAL_SHARE, outcomes.FIX_WINDOW, outcomes.SEARCH_WINDOW,
             outcomes.IDLE_SECONDS, outcomes.AUTHOR_BEFORE, outcomes.AUTHOR_AFTER)
    try:
        outcomes._apply_rule(SimpleNamespace(
            solved_share=0.9, partial_share=0.5, fix_hours=48, search_days=7, idle_minutes=10,
            author_before_minutes=2, author_after_minutes=3,
        ))
        assert_true(
            outcomes.grade(0.85, True, False) == "partial" and outcomes.IDLE_SECONDS == 600,
            "a rule set on the command line is the rule the run applies",
        )
    finally:
        (outcomes.SOLVED_SHARE, outcomes.PARTIAL_SHARE, outcomes.FIX_WINDOW, outcomes.SEARCH_WINDOW,
         outcomes.IDLE_SECONDS, outcomes.AUTHOR_BEFORE, outcomes.AUTHOR_AFTER) = saved

    # --- the two percentile conventions stay what the registry says ---------------------
    from tools.maintain.measure_real_use import dist

    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert_true(dist(values)["median"] == 5 and dist(values)["p90"] == 9, "nearest_rank: round(p * (n - 1)), half to even (round(4.5) == 4)")
    assert_true(outcomes.p90(values) == 9 and outcomes.med(values) == 5.5, "p90_floor and median_stdlib")
