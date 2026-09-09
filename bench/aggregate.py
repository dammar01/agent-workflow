"""ledger.jsonl -> a table per arm.

The one metric this exists for is premium tokens per ACCEPTED task: every premium token
the arm consumed across every attempt, divided by the number of tasks that passed the
oracle. Failed attempts stay in the numerator. That is what makes the figure
quality-adjusted rather than a tokens-per-call with better marketing — an arm that is
thrifty per call and needs three attempts is not thrifty.

There is no money here. The study measures tokens: a subscription plan does not bill per
token, so a USD column could only have been an API-equivalent figure worn as a cost, and
every comparison built on it would have inherited that fiction.

Paired by task, never pooled. Arms see the same corpus, so the honest comparison is
per-task differences; averaging each arm separately and subtracting lets an arm that
happened to draw easier tasks look better than it is. With 15 tasks a p-value would be
theatre, so this reports effect sizes and the pairs they came from (§6 of the plan).
"""

import json
from pathlib import Path

ARMS = ("A", "B", "C")
TIERS = ("unit", "integration", "e2e")


def load_ledger(path) -> list[dict]:
    rows: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _premium_total(row: dict) -> int:
    """Every premium token the unit consumed. The headline denominator's numerator."""
    return int(row.get("premium_total_tokens") or 0)


def _premium_tokens(row: dict) -> int:
    """Premium CONTEXT only — the cached window, which is what delegation displaces.

    Deliberately narrower than `_premium_total`: the claim under test is that the second
    agent keeps code out of the premium context window, and cache traffic is where that
    shows. Kept separate so "spent less overall" and "carried less context" stay two
    findings rather than one blurred one.
    """
    return int(row.get("premium_cache_read_tokens") or 0) + int(
        row.get("premium_cache_write_tokens") or 0
    )


def _mean(values: list) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _cell_key(row: dict) -> str:
    """`tier/arm/provider` — the cell a unit belongs to.

    Reported as its own breakdown rather than folded into `per_arm`, because an arm
    headline pooled across three tiers is an average over three different kinds of task.
    """
    return "/".join(
        str(row.get(field) or "?") for field in ("test_tier", "arm", "provider")
    )


def _stage_pass_rate(units: list[dict]) -> dict:
    """Oracle stages passed over stages run, pooled across the units given."""
    passed = sum(int(row.get("stages_passed") or 0) for row in units)
    run = sum(int(row.get("stages_run") or 0) for row in units)
    return {
        "stages_passed": passed,
        "stages_run": run,
        # None, not 0.0, when nothing ran. A rate over an empty denominator is not zero
        # per cent — it is no measurement at all.
        "stage_pass_rate": round(passed / run, 3) if run else None,
    }


def per_cell(rows: list[dict]) -> dict:
    """One entry per `tier/arm/provider`, so no average spans two kinds of task."""
    cells: dict[str, list[dict]] = {}
    for row in rows:
        cells.setdefault(_cell_key(row), []).append(row)
    out: dict[str, dict] = {}
    for key in sorted(cells):
        units = cells[key]
        accepted = {row["task_id"] for row in units if row.get("verdict") == "accepted"}
        premium = sum(_premium_total(row) for row in units)
        turns = [
            int(row["message_turns"]) for row in units if row.get("message_turns") is not None
        ]
        out[key] = {
            "units": len(units),
            "tasks_accepted": len(accepted),
            "total_premium_tokens": premium,
            "premium_tokens_per_accepted_task": (
                round(premium / len(accepted)) if accepted else None
            ),
            "premium_context_tokens": sum(_premium_tokens(row) for row in units),
            "worker_tokens": sum(
                int(row.get("worker_input_tokens") or 0)
                + int(row.get("worker_output_tokens") or 0)
                for row in units
            ),
            "mean_message_turns": _mean(turns),
            "total_context_input_tokens": sum(
                int(row.get("context_input_tokens") or 0) for row in units
            ),
            "transcript_measured_on": len(turns),
            "first_pass_correctness": round(
                sum(1 for row in units if row.get("first_pass_accepted")) / len(units), 3
            ),
            **_stage_pass_rate(units),
        }
    return out


def per_arm(rows: list[dict]) -> dict:
    """Headline figures for each arm, each carrying its own denominator."""
    out: dict[str, dict] = {}
    for arm in ARMS:
        units = [row for row in rows if row.get("arm") == arm]
        if not units:
            out[arm] = {"units": 0}
            continue
        accepted = {row["task_id"] for row in units if row.get("verdict") == "accepted"}
        first_pass = [row for row in units if row.get("first_pass_accepted")]
        durations = [
            float(row["t_accepted"]) - float(row["t_start"])
            for row in units
            if row.get("t_accepted") and row.get("t_start")
        ]
        premium = sum(_premium_total(row) for row in units)
        # `is not None`, not truthiness. A transcript with a measured zero is a
        # measurement, and dropping it from the denominator would quietly report a mean
        # over the subset that happened to be non-zero.
        turns = [int(row["message_turns"]) for row in units if row.get("message_turns") is not None]
        peaks = [
            int(row["context_peak_tokens"])
            for row in units
            if row.get("context_peak_tokens") is not None
        ]
        to_edit = [
            int(row["turns_to_first_edit"])
            for row in units
            if row.get("turns_to_first_edit") is not None
        ]
        out[arm] = {
            "units": len(units),
            "tasks_accepted": len(accepted),
            "total_premium_tokens": premium,
            # None, not zero and not infinity. An arm that accepted nothing has no token
            # count per accepted task; printing a number there invents a comparison.
            "premium_tokens_per_accepted_task": (
                round(premium / len(accepted)) if accepted else None
            ),
            "premium_context_tokens": sum(_premium_tokens(row) for row in units),
            "worker_tokens": sum(
                int(row.get("worker_input_tokens") or 0)
                + int(row.get("worker_output_tokens") or 0)
                for row in units
            ),
            "mean_message_turns": _mean(turns),
            "mean_turns_to_first_edit": _mean(to_edit),
            "mean_context_peak_tokens": _mean(peaks),
            # The cumulative premium context the arm carried, and the part of it that was
            # a native subagent. Reported here as well as per row: an arm's context load is
            # a headline number, and leaving it only in the raw ledger meant the report
            # promised a metric it did not print.
            "total_context_input_tokens": sum(
                int(row.get("context_input_tokens") or 0) for row in units
            ),
            "total_context_sidechain_tokens": sum(
                int(row.get("context_sidechain_tokens") or 0) for row in units
            ),
            # Its own denominator: transcript columns are null for any unit whose
            # transcript was never recorded, and those units are not in these means.
            "transcript_measured_on": len(turns),
            "first_pass_correctness": round(len(first_pass) / len(units), 3),
            "mean_rework_cycles": round(
                sum(int(row.get("rework_cycles") or 0) for row in units) / len(units), 2
            ),
            "mean_seconds_to_accepted": (
                round(sum(durations) / len(durations), 1) if durations else None
            ),
            "seconds_measured_on": len(durations),
            # Counted separately from the rejected pile. The accepted filter above is
            # already exclusive, so a security violation drops out of the numerator on its
            # own — but dropping out silently is how it would stop being reported at all.
            "security_violations": sum(
                1 for row in units if row.get("verdict") == "security_violation"
            ),
            "main_agent_rewrote": sum(1 for row in units if row.get("main_agent_rewrote")),
            "evidence_reused_hits": sum(int(row.get("evidence_reused_hits") or 0) for row in units),
            "providers": sorted({str(row.get("provider")) for row in units if row.get("provider")}),
            "tiers": sorted({str(row.get("test_tier")) for row in units if row.get("test_tier")}),
            **_stage_pass_rate(units),
        }
    return out


def paired(
    rows: list[dict], baseline: str = "A", arm: str = "C", provider: str | None = None
) -> dict:
    """Per-task differences between two arms, over tasks BOTH of them attempted.

    `provider` narrows the right-hand arm to one second agent. Without it, an arm C run
    across two providers pools them, and the pair then describes neither.
    """
    def by_task(name: str, want_provider: str | None = None) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            if row.get("arm") != name or not row.get("task_id"):
                continue
            if want_provider is not None and row.get("provider") != want_provider:
                continue
            grouped.setdefault(row["task_id"], []).append(row)
        return grouped

    left, right = by_task(baseline), by_task(arm, provider)
    shared = sorted(set(left) & set(right))
    pairs: list[dict] = []
    for task_id in shared:
        base_premium = sum(_premium_total(row) for row in left[task_id])
        arm_premium = sum(_premium_total(row) for row in right[task_id])
        pairs.append(
            {
                "task_id": task_id,
                "premium_token_delta": arm_premium - base_premium,
                "premium_tokens_avoided": (
                    sum(_premium_tokens(row) for row in left[task_id])
                    - sum(_premium_tokens(row) for row in right[task_id])
                ),
                "accepted_baseline": any(r.get("verdict") == "accepted" for r in left[task_id]),
                "accepted_arm": any(r.get("verdict") == "accepted" for r in right[task_id]),
            }
        )
    deltas = [pair["premium_token_delta"] for pair in pairs]
    return {
        "baseline": baseline,
        "arm": arm,
        "provider": provider,
        "paired_tasks": len(pairs),
        # Named so nobody mistakes it for a population claim. 15 tasks is an effect size,
        # not a significance test.
        "mean_premium_token_delta": round(sum(deltas) / len(deltas)) if deltas else None,
        "tasks_fewer_premium_tokens_in_arm": sum(1 for value in deltas if value < 0),
        "tasks_more_premium_tokens_in_arm": sum(1 for value in deltas if value > 0),
        "premium_tokens_avoided_total": sum(pair["premium_tokens_avoided"] for pair in pairs),
        "pairs": pairs,
    }


def report(ledger_path) -> dict:
    rows = load_ledger(ledger_path)
    providers = sorted(
        {
            str(row.get("provider"))
            for row in rows
            if row.get("arm") == "C" and row.get("provider")
        }
    )
    return {
        "units": len(rows),
        "per_arm": per_arm(rows),
        # The headline breakdown. `per_arm` pools tiers and providers together, which is
        # the right shape for the top-line claim and the wrong shape for anything else.
        "per_tier_arm_provider": per_cell(rows),
        "providers_in_arm_C": providers,
        "paired_A_vs_C": paired(rows, "A", "C"),
        "paired_B_vs_C": paired(rows, "B", "C"),
        # Per provider as well as pooled: arm C run under two second agents is two
        # different arms wearing one letter, and a pooled delta hides which one moved.
        "paired_by_provider": {
            name: {
                "A_vs_C": paired(rows, "A", "C", name),
                "B_vs_C": paired(rows, "B", "C", name),
            }
            for name in providers
        },
        # Carried in the output so the framing travels with the numbers. Premium and
        # worker tokens are different resources at different prices; a total across both
        # would be an addition of unlike units.
        "token_framing": (
            "Token counts, not money. Premium tokens (Claude Code) and worker tokens "
            "(second agent) are separate resources and are never summed together."
        ),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="aggregate the benchmark ledger")
    parser.add_argument("--ledger", default=str(Path(__file__).parent / "ledger.jsonl"))
    args = parser.parse_args()
    print(json.dumps(report(args.ledger), indent=2))
