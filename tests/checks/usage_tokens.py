"""Provider-reported token counts, asserted where getting it wrong stays invisible.

Every check here is aimed at one mistake: treating a breakdown as an addend. Reasoning is
reported INSIDE the output count and cached input INSIDE the input count, so any layer
that helpfully sums the fields it finds produces a bill that grows the harder a model
thinks about the same answer. That failure is silent — the number still looks like a
number — which is why it is asserted at every layer that could introduce it rather than
once at the top.

The second theme is absence. A provider that reports nothing must leave a row estimated
rather than zeroed, because a zero reads as a measurement and averages into the report as
one.
"""

from adapters.providers.codex_adapter import CodexAdapter
from adapters.providers.opencode_adapter import OpenCodeAdapter
from adapters.shared.usage import merge_usage, normalize_usage, token_source_for
from core.audit import telemetry
from core.evidence.contracts import UsageRecord, billable_input, billable_output
from core.policy.governance import budget_state
from tests.checks.support import assert_true


def _normalisation_reads_the_shapes_providers_send() -> None:
    nested = normalize_usage(
        {
            "input_tokens": 1000,
            "output_tokens": 800,
            "output_tokens_details": {"reasoning_tokens": 600},
            "input_tokens_details": {"cached_tokens": 900},
        }
    )
    assert_true(
        nested
        == {
            "input_tokens": 1000,
            "output_tokens": 800,
            "reasoning_tokens": 600,
            "cached_input_tokens": 900,
        },
        "a breakdown nested in a *_details container must be found; providers report it "
        "there as often as at the top level",
    )
    assert_true(
        nested["reasoning_tokens"] < nested["output_tokens"]
        and nested["cached_input_tokens"] < nested["input_tokens"],
        "the breakdowns must stay smaller than the totals they are part of — a reasoning "
        "count above the output count is the signature of the two being added",
    )

    alias = normalize_usage({"prompt_tokens": 10, "completion_tokens": 20})
    assert_true(
        alias["input_tokens"] == 10 and alias["output_tokens"] == 20,
        "the other common spelling must map to the same two fields, or one provider's "
        "rows silently read as unmeasured",
    )
    assert_true(
        normalize_usage({"unrelated": 5}) is None and normalize_usage(None) is None,
        "an object with nothing recognisable is no measurement at all; returning zeros "
        "would put a fabricated count on the row",
    )
    assert_true(
        normalize_usage({"input_tokens": True}) is None,
        "a bool is not a token count even though Python will happily add it to one",
    )


def _merging_adds_turns_but_never_fields() -> None:
    merged = merge_usage(
        {
            "input_tokens": 10,
            "output_tokens": 20,
            "reasoning_tokens": 15,
            "cached_input_tokens": None,
        },
        {
            "input_tokens": 5,
            "output_tokens": 7,
            "reasoning_tokens": None,
            "cached_input_tokens": 3,
        },
    )
    assert_true(
        merged["input_tokens"] == 15 and merged["output_tokens"] == 27,
        "two turns of one run spent both turns' tokens; keeping the last would report a "
        "fraction of the bill as the whole of it",
    )
    assert_true(
        merged["reasoning_tokens"] == 15 and merged["cached_input_tokens"] == 3,
        "a count present on one side only must carry through rather than be zeroed into "
        "a sum — absent means unreported, not none spent",
    )
    assert_true(
        merge_usage(None, {"output_tokens": 4})["output_tokens"] == 4
        and merge_usage({"output_tokens": 4}, None)["output_tokens"] == 4,
        "merging with nothing must return the something",
    )


def _token_source_admits_a_half_measurement() -> None:
    assert_true(
        token_source_for({"input_tokens": 1, "output_tokens": 2}) == "provider",
        "both directions measured is the only state that may claim to be provider-counted",
    )
    assert_true(
        token_source_for({"output_tokens": 2, "input_tokens": None}) == "mixed",
        "a provider reporting only its output leaves the input estimated — calling that "
        "row either provider or estimated misdescribes half of it",
    )
    assert_true(
        token_source_for(None) == "estimated" and token_source_for({}) == "estimated",
        "no measurement means the row still carries its chars//4 estimate, and says so",
    )


def _adapters_read_usage_out_of_their_own_streams() -> None:
    stream = "\n".join(
        [
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":300,'
            '"output_tokens_details":{"reasoning_tokens":250}}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}',
            '{"type":"turn.completed","usage":{"input_tokens":50,"output_tokens":20}}',
        ]
    )
    usage = CodexAdapter.extract_usage(stream)
    assert_true(
        usage["input_tokens"] == 150 and usage["output_tokens"] == 320,
        "every turn.completed in a run must be summed; a multi-turn run that reports one "
        "turn's tokens understates the call it measures",
    )
    assert_true(
        CodexAdapter.clean_output(stream) == "answer",
        "reading usage must not disturb what the adapter returns as the answer",
    )
    assert_true(
        CodexAdapter.extract_usage("plain text, no events") is None,
        "a build that emits no usage must leave the row estimated, not zeroed",
    )
    assert_true(
        OpenCodeAdapter.extract_usage("some log line\nthe answer") is None,
        "OpenCode reports no usage today, and inventing one would be worse than saying so",
    )
    assert_true(
        OpenCodeAdapter.extract_usage('{"usage":{"input_tokens":5,"output_tokens":7}}')
        == {
            "input_tokens": 5,
            "output_tokens": 7,
            "reasoning_tokens": None,
            "cached_input_tokens": None,
        },
        "the day an OpenCode build does emit usage it must be picked up without a code "
        "change, which is the only reason the reader exists ahead of the evidence",
    )


def _billing_prefers_measurement_and_refuses_to_double_count() -> None:
    measured = UsageRecord(
        estimated_input_tokens=10,
        estimated_output_tokens=10,
        actual_input_tokens=1000,
        actual_output_tokens=800,
        actual_reasoning_tokens=600,
        actual_cached_input_tokens=900,
    )
    assert_true(
        billable_input(measured) == 1000 and billable_output(measured) == 800,
        "a measured count must win over the estimate beside it",
    )
    assert_true(
        billable_input(measured) + billable_output(measured) == 1800,
        "the breakdowns must not enter the total — adding reasoning and cached input here "
        "would bill 3300 for a call that spent 1800",
    )
    estimated = UsageRecord(estimated_input_tokens=10, estimated_output_tokens=10)
    assert_true(
        billable_input(estimated) == 10 and billable_output(estimated) == 10,
        "an unmeasured row falls back to its estimate rather than to zero",
    )


def _report_and_budget_read_the_measured_rows() -> None:
    rows = [
        UsageRecord(
            session_id="s",
            command="explore",
            prompt_id="p1",
            estimated_input_tokens=10,
            estimated_output_tokens=10,
            actual_input_tokens=1000,
            actual_output_tokens=800,
            actual_reasoning_tokens=600,
            token_source="provider",
            provider_call_index=0,
        ),
        UsageRecord(
            session_id="s",
            command="explore",
            prompt_id="p1",
            estimated_input_tokens=5,
            estimated_output_tokens=5,
            token_source="estimated",
            provider_call_index=1,
        ),
    ]
    cost = telemetry._cost(rows)
    assert_true(
        cost["total_tokens"] == 1000 + 800 + 5 + 5,
        "a history mixing measured and estimated rows must charge each row by what it "
        "actually knows, not fall back to one source for all of them",
    )
    assert_true(
        cost["reasoning_tokens_within_output"] == 600
        and cost["reasoning_tokens_within_output"] < cost["output_tokens"],
        "reasoning is reported beside the total as a share of the output, never added to it",
    )
    assert_true(
        sorted(cost["token_source"]) == ["estimated", "provider"],
        "a mixed history must show both sources; collapsing them hides that half the "
        "figure is an estimate",
    )
    assert_true(
        cost["measured_calls"] == 1,
        "the denominator for the measured half of a report has to be readable too",
    )
    assert_true(
        telemetry._cost([])["reasoning_tokens_within_output"] is None,
        "no provider having reported reasoning is not the same as none being spent",
    )

    state = budget_state(rows, "s", 5000)
    assert_true(
        state["spent"] == 1810,
        "the ceiling must be measured against provider counts where they exist — a budget "
        "read off chars//4 stops watching the tokens a model spends thinking",
    )


def _a_continuation_records_each_call_and_counts_the_saving_once() -> None:
    """The end-to-end shape: two provider calls, two rows, one saving.

    Written against the writer rather than the helper because the failure it guards is a
    seam failure. Every part can be individually right while the rows still double-count
    the digest or credit one call's tokens to both.
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path

    from core.provider.executor import Executor
    from core.runtime.state import ensure_workflow_workspace

    root = Path(tempfile.mkdtemp(prefix="aw-usage-rows-"))
    try:
        ensure_workflow_workspace(root, str(Path("main.py").resolve()))
        executor = Executor.__new__(Executor)
        executor._call_metas = [
            {
                "adapter_meta": {
                    "duration_seconds": 1.0,
                    "provider_usage": {
                        "input_tokens": 100,
                        "output_tokens": 300,
                        "reasoning_tokens": 250,
                        "cached_input_tokens": None,
                    },
                },
                "prompt_chars": 400,
                "response_chars": 800,
            },
            {
                "adapter_meta": {
                    "duration_seconds": 2.0,
                    "provider_usage": {
                        "input_tokens": 50,
                        "output_tokens": 20,
                        "reasoning_tokens": None,
                        "cached_input_tokens": None,
                    },
                },
                "prompt_chars": 200,
                "response_chars": 100,
            },
        ]
        executor._last_call_meta = {
            "command": "explore",
            "role": "exploration",
            "prompt_id": "pid-1",
            "response_chars": 900,
            "token_source": "provider",
        }
        executor._record_usage(
            {
                "ok": True,
                "content": "x" * 900,
                "digest": {"summary": "s" * 100, "key_findings": ["f" * 20]},
                "meta": {},
            },
            root,
            "explore",
            "a task",
            "sid-1",
        )
        rows = [
            json.loads(line)
            for line in (root / ".workflow" / "usage.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        assert_true(
            [row["provider_call_index"] for row in rows] == [0, 1],
            "a continuation ran the adapter twice and must leave two rows, indexed in the "
            "order the calls happened",
        )
        assert_true(
            rows[0]["actual_output_tokens"] == 300 and rows[1]["actual_output_tokens"] == 20,
            "each row carries the tokens ITS call spent; repeating the aggregate on both "
            "would bill the retry for the first attempt as well",
        )
        assert_true(
            rows[0]["digest_chars"] is None and rows[1]["digest_chars"] == 120,
            "the digest belongs to the answer the command returned, so it lands on the "
            "final row alone — on both, the saving is counted twice",
        )
        assert_true(
            rows[1]["premium_context_avoided_tokens"] == (900 - 120) // 4,
            "the saving is measured against the MERGED answer main_agent did not read, "
            "not against the retry's fragment — that comparison reports near zero and "
            "quietly erases the digest contract's whole justification",
        )
        assert_true(
            telemetry.report(root)["calls"] == 2
            and telemetry.report(root)["commands"] == 1,
            "the report must be able to say two provider calls served one command; a "
            "single figure for both makes cost per task move when a retry happens",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_usage_token_accounting() -> None:
    _normalisation_reads_the_shapes_providers_send()
    _merging_adds_turns_but_never_fields()
    _token_source_admits_a_half_measurement()
    _adapters_read_usage_out_of_their_own_streams()
    _billing_prefers_measurement_and_refuses_to_double_count()
    _report_and_budget_read_the_measured_rows()
    _a_continuation_records_each_call_and_counts_the_saving_once()
