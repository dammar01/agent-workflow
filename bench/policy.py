"""Run policy: the limits a unit runs under, in one place instead of five.

Every number here is a decision, not a measurement. They are gathered in one file so that
changing one is a visible edit rather than a constant quietly drifting inside whichever
module happened to need it. Where a value was chosen rather than derived, the comment says
so — a benchmark that presents its own arbitrary cutoffs as findings is the failure mode
this file exists to avoid.

Enforcement is split, and the split is not cosmetic:

- **Time** is enforced live. `driver.py` knows when a unit started, so it can refuse to
  stamp one that ran past its deadline.
- **Retry** is enforced live. `driver.py` refuses to close a unit whose rework count ran
  past the cap.
- **Budget is NOT enforced live.** Cost arrives from tokenburn after the fact, so nothing
  here can stop a run mid-flight. `collect.py` reports the overrun instead. Calling that
  enforcement would be a lie about what the harness can see.
- **Quarantine** is read by `oracle.py` when it builds its stage-2 command.
"""

# --- Time -------------------------------------------------------------------------------

# BENCHMARK-PLAN.md §9 estimates 5-20 minutes per run. 30 is that range with room, chosen
# so an ordinary slow unit is not killed and a hung one does not sit forever. Not derived
# from measurement: no unit has been run yet.
UNIT_TIMEOUT_SECONDS = 1800

# Matches the default already compiled into `oracle._run`. Named here so the two cannot
# drift apart silently.
ORACLE_STAGE_TIMEOUT_SECONDS = 900


# --- Retry ------------------------------------------------------------------------------

# A unit the operator sent back three times has told us what it is going to tell us. The
# cap exists so "rework until it passes" cannot quietly turn every arm into a pass and
# hide the difference the study is looking for.
MAX_REWORK_CYCLES = 3

# Delegated calls per unit, arm C. Guards against a session that loops on the second agent
# and charges the arm for it.
MAX_DELEGATED_CALLS = 12


# --- Budget -----------------------------------------------------------------------------

# Operator-set ceilings. NOT derived from any run: nothing has been harvested, and a
# ceiling computed from zero observations would be a number pretending to be evidence.
# Revisit both after the first batch, and say in the report if they were revised.
UNIT_BUDGET_USD = 5.00
RUN_BUDGET_USD = 400.00


# --- Flaky quarantine -------------------------------------------------------------------

# Suite names excluded from oracle stage 2. Empty today: four consecutive green runs of
# `python tests/run.py` on 2026-08-20 produced no flake, and four runs on one machine is
# not evidence of stability — it is only an absence of evidence of instability. Nothing is
# quarantined on suspicion.
#
# Candidates if one ever does flake, from the timing-sensitive code already in the suite:
#   scenario                 (time.sleep at tests/scenario.py:394, :725, :781)
#   workspace-release        (time.sleep at tests/checks/workspace.py:139)
#
# Adding a name here removes that suite from the acceptance gate for every unit in the
# study. Record why, and when, next to the name.
QUARANTINED_SUITES: tuple[str, ...] = ()


def over_time(seconds: float) -> bool:
    return seconds > UNIT_TIMEOUT_SECONDS


def over_budget_unit(cost_usd: float | None) -> bool:
    """None means unharvested, which is not over budget — it is unknown."""
    return cost_usd is not None and cost_usd > UNIT_BUDGET_USD


def over_budget_run(total_usd: float) -> bool:
    return total_usd > RUN_BUDGET_USD


def summary() -> dict:
    return {
        "unit_timeout_seconds": UNIT_TIMEOUT_SECONDS,
        "oracle_stage_timeout_seconds": ORACLE_STAGE_TIMEOUT_SECONDS,
        "max_rework_cycles": MAX_REWORK_CYCLES,
        "max_delegated_calls": MAX_DELEGATED_CALLS,
        "unit_budget_usd": UNIT_BUDGET_USD,
        "run_budget_usd": RUN_BUDGET_USD,
        "quarantined_suites": list(QUARANTINED_SUITES),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(summary(), indent=2))
