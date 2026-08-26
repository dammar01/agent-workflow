"""Locking tests for the oracle's verdict function and the run policy it reads.

Deliberately NOT registered in `tests/run.py`. The oracle runs `tests/run.py` as its own
stage 2, so a bench test living in that suite would make the oracle grade itself: a change
that broke the verdict logic could fail stage 2, and the unit would be marked down for the
instrument's fault rather than its own. Standalone, run by hand or by the harness:

    python bench/test_oracle.py

`_verdict` is tested rather than `judge` on purpose. `judge` shells out to a worktree and a
test suite; what needs pinning here is the mapping from stage outcomes to a verdict, and
that mapping is pure.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parent.parent

import policy  # noqa: E402
from oracle import (  # noqa: E402
    _LIST_LINE,
    STAGE_CHECKS,
    STAGE_SUITE,
    STAGE_SYNTAX,
    STAGE_TASK_TESTS,
    VERDICT_ACCEPTED,
    VERDICT_INCOMPLETE,
    VERDICT_REJECTED,
    VERDICT_SECURITY_VIOLATION,
    _suite_command,
    _verdict,
)

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  PASS  {name}")
        return
    FAILURES.append(name)
    print(f"  FAIL  {name}\n        got  {got!r}\n        want {want!r}")


def _all_ran(ok: bool = True) -> dict:
    return {
        STAGE_SYNTAX: {"ran": True, "ok": True},
        STAGE_SUITE: {"ran": True, "ok": True},
        STAGE_TASK_TESTS: {"ran": True, "ok": True},
        STAGE_CHECKS: {"ran": True, "ok": ok},
    }


def main() -> int:
    print("bench/test_oracle: verdict mapping")

    result = _verdict(_all_ran(), None)
    check("every stage ran and passed -> accepted", result["verdict"], VERDICT_ACCEPTED)
    check("accepted flag tracks the verdict", result["accepted"], True)

    result = _verdict(_all_ran(ok=False), STAGE_CHECKS)
    check(
        "the security stage failing -> security_violation, not rejected",
        result["verdict"],
        VERDICT_SECURITY_VIOLATION,
    )
    check("a security violation is not accepted", result["accepted"], False)

    stages = _all_ran()
    stages[STAGE_SUITE] = {"ran": True, "ok": False}
    check(
        "an ordinary stage failing stays rejected",
        _verdict(stages, STAGE_SUITE)["verdict"],
        VERDICT_REJECTED,
    )

    stages = _all_ran()
    del stages[STAGE_TASK_TESTS]
    check(
        "a stage that never ran caps the verdict at incomplete",
        _verdict(stages, None)["verdict"],
        VERDICT_INCOMPLETE,
    )

    # The specific verdict has to beat the generic one in both directions: a run that both
    # failed the security stage AND skipped an earlier one is a violation, not an
    # incomplete. Reading it as incomplete would file a crossed boundary under "we did not
    # finish looking".
    stages = {
        STAGE_SYNTAX: {"ran": True, "ok": True},
        STAGE_SUITE: {"ran": True, "ok": True},
        STAGE_CHECKS: {"ran": True, "ok": False},
    }
    result = _verdict(stages, STAGE_CHECKS)
    check(
        "security_violation outranks incomplete",
        result["verdict"],
        VERDICT_SECURITY_VIOLATION,
    )
    check(
        "the unrun stage is still reported",
        result["stages_not_run"],
        [STAGE_TASK_TESTS],
    )

    print("bench/test_oracle: run policy")

    original = policy.QUARANTINED_SUITES
    try:
        policy.QUARANTINED_SUITES = ()
        args, excluded = _suite_command(Path("."))
        check(
            "nothing quarantined -> plain tests/run.py",
            args[1:],
            ["tests/run.py"],
        )
        check("nothing quarantined -> nothing excluded", excluded, [])

        policy.QUARANTINED_SUITES = ("scenario",)
        args, excluded = _suite_command(REPO_ROOT)
        check("a quarantined suite is reported", excluded, ["scenario"])
        check("the quarantined suite is not selected", "scenario" in args, False)
        check("other suites are still selected", "--only" in args, True)
        check("failures are collected, not short-circuited", "--keep-going" in args, True)

        # Every suite excluded must NOT fall through to `tests/run.py` with no --only, which
        # defaults to `scenario` — the suite that was just excluded.
        listing = subprocess.run(
            [sys.executable, "tests/run.py", "--list"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        all_names = tuple(
            m.group(1) for m in (_LIST_LINE.match(line) for line in listing.stdout.splitlines()) if m
        )
        policy.QUARANTINED_SUITES = all_names
        args, excluded = _suite_command(REPO_ROOT)
        check("everything quarantined -> no command at all", args, [])
    finally:
        policy.QUARANTINED_SUITES = original

    check(
        "over_time uses the unit cap",
        (policy.over_time(policy.UNIT_TIMEOUT_SECONDS + 1), policy.over_time(1.0)),
        (True, False),
    )
    check(
        "an unharvested cost is unknown, not over budget",
        policy.over_budget_unit(None),
        False,
    )
    check(
        "a harvested cost over the cap is over budget",
        policy.over_budget_unit(policy.UNIT_BUDGET_USD + 0.01),
        True,
    )

    print()
    if FAILURES:
        print(f"bench/test_oracle: FAILED ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("bench/test_oracle: success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
