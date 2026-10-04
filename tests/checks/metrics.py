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
            and "machine holding the inputs only" in stamp["recount"],
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

    # --- the implementation computes what the registry defines ---------------------------
    # outcome.context_saved: fresh input (input - cached) minus returned (chars // 4), floored
    # at 0; reasoning is reported apart and never counted.
    net = outcomes.saved_net([
        {"actual_input_tokens": 1000, "actual_cached_input_tokens": 300, "actual_reasoning_tokens": 500,
         "response_chars": 400},
        {"actual_input_tokens": 200, "actual_cached_input_tokens": 0, "response_chars": 40},
    ])
    assert_true(
        (net["fresh"], net["returned"], net["reasoning"], net["net"]) == (900, 110, 500, 790),
        f"outcome.context_saved is fresh minus returned, reasoning apart: {net}",
    )
    assert_true("minus what returned" in metrics.REGISTRY["outcome.context_saved"]["definition"]
                and "plus reasoning" not in metrics.REGISTRY["outcome.context_saved"]["definition"],
                "the registry states the formula the tool runs")
    assert_true(outcomes.saved_net([{"actual_input_tokens": 10, "response_chars": 4000}])["net"] == 0,
                "the net is floored at 0")

    # --- a usage row belongs to one transcript: the earliest that binds its session -------
    owners = metrics.session_owners([
        {"start": "2026-10-02T00:00:00", "sessions": {"main_a"}},
        {"start": "2026-10-01T00:00:00", "sessions": {"main_a", "main_b"}},
        {"start": None, "sessions": {"main_c", "main_a"}},
    ])
    assert_true(owners == {"main_a": 1, "main_b": 1, "main_c": 2},
                f"a resumed copy binding the same id does not own its rows: {owners}")

    # --- an export carries no path and no project name ------------------------------------
    root = Path(tempfile.mkdtemp(prefix="metrics-export-"))
    try:
        dest = root / "out.json"
        metrics.write_export({"projects": 2, "per_project": {"PA": {"sessions": 3}}}, dest, forbidden=["shop-api"])
        assert_true('"schema": 1' in dest.read_text(encoding="utf-8"), "an export is written with its schema")
        for bad, why in (({"note": "shop-api"}, "a project name"), ({"note": r"E:\Work\x"}, "a Windows path"),
                         ({"note": "/home/u/p"}, "a POSIX path")):
            try:
                metrics.write_export(bad, root / "bad.json", forbidden=["shop-api"])
            except ValueError:
                pass
            else:
                raise AssertionError(f"an export carrying {why} is refused")
        assert_true(not (root / "bad.json").exists(), "a refused export writes nothing")
    finally:
        shutil.rmtree(root, ignore_errors=True)
