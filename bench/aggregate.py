"""ledger.jsonl -> a table per arm.

The one metric this exists for is cost per ACCEPTED task: total spend across every
attempt, divided by the number of tasks that passed the oracle. Failed attempts stay in
the numerator. That is what makes the figure quality-adjusted rather than a cost-per-call
with better marketing — an arm that is cheap per call and needs three attempts is not
cheap.

Paired by task, never pooled. Arms see the same corpus, so the honest comparison is
per-task differences; averaging each arm separately and subtracting lets an arm that
happened to draw easier tasks look better than it is. With 15 tasks a p-value would be
theatre, so this reports effect sizes and the pairs they came from (§6 of the plan).
"""

import json
from pathlib import Path

ARMS = ("A", "B", "C")


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


def _spend(row: dict) -> float:
    return float(row.get("premium_cost_usd") or 0.0) + float(row.get("worker_cost_usd") or 0.0)


def _premium_tokens(row: dict) -> int:
    return int(row.get("premium_cache_read_tokens") or 0) + int(
        row.get("premium_cache_write_tokens") or 0
    )


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
        spend = sum(_spend(row) for row in units)
        out[arm] = {
            "units": len(units),
            "tasks_accepted": len(accepted),
            "total_cost_usd": round(spend, 4),
            # None, not zero and not infinity. An arm that accepted nothing has no cost
            # per accepted task; printing a number there invents a comparison.
            "cost_per_accepted_task_usd": (
                round(spend / len(accepted), 4) if accepted else None
            ),
            "premium_context_tokens": sum(_premium_tokens(row) for row in units),
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
        }
    return out


def paired(rows: list[dict], baseline: str = "A", arm: str = "C") -> dict:
    """Per-task differences between two arms, over tasks BOTH of them attempted."""
    def by_task(name: str) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            if row.get("arm") == name and row.get("task_id"):
                grouped.setdefault(row["task_id"], []).append(row)
        return grouped

    left, right = by_task(baseline), by_task(arm)
    shared = sorted(set(left) & set(right))
    pairs: list[dict] = []
    for task_id in shared:
        base_cost = sum(_spend(row) for row in left[task_id])
        arm_cost = sum(_spend(row) for row in right[task_id])
        pairs.append(
            {
                "task_id": task_id,
                "cost_delta_usd": round(arm_cost - base_cost, 4),
                "premium_tokens_avoided": (
                    sum(_premium_tokens(row) for row in left[task_id])
                    - sum(_premium_tokens(row) for row in right[task_id])
                ),
                "accepted_baseline": any(r.get("verdict") == "accepted" for r in left[task_id]),
                "accepted_arm": any(r.get("verdict") == "accepted" for r in right[task_id]),
            }
        )
    deltas = [pair["cost_delta_usd"] for pair in pairs]
    return {
        "baseline": baseline,
        "arm": arm,
        "paired_tasks": len(pairs),
        # Named so nobody mistakes it for a population claim. 15 tasks is an effect size,
        # not a significance test.
        "mean_cost_delta_usd": round(sum(deltas) / len(deltas), 4) if deltas else None,
        "tasks_cheaper_in_arm": sum(1 for value in deltas if value < 0),
        "tasks_dearer_in_arm": sum(1 for value in deltas if value > 0),
        "premium_tokens_avoided_total": sum(pair["premium_tokens_avoided"] for pair in pairs),
        "pairs": pairs,
    }


def report(ledger_path) -> dict:
    rows = load_ledger(ledger_path)
    return {
        "units": len(rows),
        "per_arm": per_arm(rows),
        "paired_A_vs_C": paired(rows, "A", "C"),
        "paired_B_vs_C": paired(rows, "B", "C"),
        # Subscription pricing means costUSD is an API-equivalent figure, not money
        # billed. Carried in the output so the framing travels with the numbers.
        "cost_framing": "API-equivalent USD under a subscription plan, not amounts billed",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="aggregate the benchmark ledger")
    parser.add_argument("--ledger", default=str(Path(__file__).parent / "ledger.jsonl"))
    args = parser.parse_args()
    print(json.dumps(report(args.ledger), indent=2))
