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

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent

import collect  # noqa: E402
import driver  # noqa: E402
import policy  # noqa: E402
from config.settings import default_provider_config  # noqa: E402
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


def _refused(arm: str, provider: str) -> bool:
    """True when the driver refused this arm/provider pair."""
    try:
        driver.check_provider(arm, provider)
    except driver.DriverError:
        return True
    return False


def _seeded_for(provider: str) -> dict:
    """A second_agent.json as `init` would leave it for `provider`, plus local tuning."""
    seeded = dict(default_provider_config(provider))
    seeded["default_model"] = "opencode/deepseek-v4-flash-free"
    seeded["routes"] = {
        name: {**route, "model": "opencode/deepseek-v4-flash-free"}
        for name, route in seeded["routes"].items()
    }
    # Provider-neutral: an operator's own tuning, which pinning must not clobber.
    seeded["timeout_seconds"] = 4242
    return seeded


def _pin_into(config: dict, provider: str) -> dict:
    """Run `_pin_provider` over a temporary worktree holding `config`."""
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp)
        (worktree / ".workflow").mkdir()
        path = worktree / ".workflow" / "second_agent.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        outcome = driver._pin_provider(worktree, provider)
        if not outcome["pinned"]:
            raise AssertionError(f"pin failed: {outcome['reason']}")
        return json.loads(path.read_text(encoding="utf-8"))


def _schema_gap() -> tuple[list, list]:
    """(written but undeclared, declared but never written) for one representative row."""
    record = {
        "task_id": "T01", "arm": "C", "provider": "codex", "test_tier": "unit",
        "repeat": 1, "base_sha": "abc", "session_id": "bench_T01_C_codex_1",
        "worktree": "bench/worktrees/x", "workflow_installed": True,
        "status": "finished", "t_start": 1.0, "t_first_submit": 2.0,
        "t_accepted": 3.0, "t_end": 4.0, "delegated_calls": 1,
        "evidence_reused_hits": 0, "first_pass_accepted": True, "rework_cycles": 0,
        "oracle_stage_failed": None, "verdict": "accepted", "main_agent_rewrote": False,
        "files_touched": [], "unit_seconds": 3.0, "timed_out": False,
        "last_oracle": {"quarantined": [], "stages_passed": 4, "stages_run": 4},
        "transcript_path": None,
    }
    row = collect.build_row(record, None, [])
    declared, written = set(collect.LEDGER_FIELDS), set(row)
    return sorted(written - declared), sorted(declared - written)


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
        "a stage that never ran is not a failed stage",
        (
            _verdict(_all_ran(), None)["stages_passed"],
            _verdict(_all_ran(), None)["stages_run"],
        ),
        (4, 4),
    )
    stopped_early = {STAGE_SYNTAX: {"ran": True, "ok": False}}
    check(
        "a unit stopped at stage 1 reads 0 of 1, not 0 of 4",
        (
            _verdict(stopped_early, STAGE_SYNTAX)["stages_passed"],
            _verdict(stopped_early, STAGE_SYNTAX)["stages_run"],
        ),
        (0, 1),
    )
    check(
        "the tier travels with the verdict so a row can be grouped by it",
        _verdict(_all_ran(), None, "integration")["test_tier"],
        "integration",
    )

    check(
        "an unharvested premium token count is unknown, not over budget",
        policy.over_budget_unit(None),
        False,
    )
    check(
        "a harvested premium token count over the cap is over budget",
        policy.over_budget_unit(policy.UNIT_BUDGET_PREMIUM_TOKENS + 1),
        True,
    )
    check(
        "the budget is denominated in tokens, with no currency left in the policy",
        [name for name in dir(policy) if "USD" in name or "usd" in name],
        [],
    )

    print()
    print("bench/test_oracle: arm x provider")
    check(
        "arms A and B run no second agent, so a worker provider is refused",
        _refused("A", "opencode"),
        True,
    )
    check(
        "arm C runs a second agent, so the baseline provider is refused",
        _refused("C", "claude"),
        True,
    )
    check(
        "an unknown provider is refused rather than carried into the ledger",
        _refused("C", "gemini"),
        True,
    )
    check(
        "the allowed pairs are allowed",
        (
            driver.check_provider("A", "claude"),
            driver.check_provider("C", "opencode"),
            driver.check_provider("C", "codex"),
        ),
        ("claude", "opencode", "codex"),
    )
    check(
        "provider is part of the unit id, so one task/arm can run under two providers",
        (
            driver.unit_id("T01", "C", 1, "opencode"),
            driver.unit_id("T01", "C", 1, "codex"),
        ),
        ("T01_C_opencode_1", "T01_C_codex_1"),
    )

    # `routes[<command>].model` wins over `default_model` in core/prompt/router.py, so a
    # worktree seeded for one provider and pinned to another would keep sending the first
    # provider's model names on every delegated call — labelled codex, running on
    # opencode's models. Pinning has to rebuild the routes, not just the two name fields.
    pinned = _pin_into(_seeded_for("opencode"), "codex")
    expected = default_provider_config("codex")
    check(
        "pinning rebuilds EVERY provider-owned key, asserted key by key",
        {key: pinned[key] for key in driver.PROVIDER_OWNED_KEYS},
        {key: expected[key] for key in driver.PROVIDER_OWNED_KEYS},
    )
    check(
        "no provider-owned value of the previous provider survives anywhere in the routes",
        sorted(
            {
                str(value)
                for route in pinned["routes"].values()
                if isinstance(route, dict)
                for value in route.values()
                if isinstance(value, str) and "opencode" in value
            }
        ),
        [],
    )
    check(
        "provider-neutral workspace tuning is left as init wrote it",
        pinned["timeout_seconds"],
        4242,
    )
    check(
        "the pinned key set is exactly what default_provider_config owns",
        sorted(driver.PROVIDER_OWNED_KEYS),
        sorted(
            key
            for key in expected
            if key in ("provider", "provider_command", "provider_agent",
                       "default_model", "effort", "routes")
        ),
    )

    print()
    print("bench/test_oracle: transcript lookup")
    # The driver mints `bench_<unit_id>`; `session-bind` mints `main_<slug>_<ts>_<rand>`
    # of its own. The two id spaces never meet, so matching a registry entry on
    # `main_session_id` found nothing, always — and found it silently. The worktree is the
    # real link, and it works for arms A and B too, which install no `.workflow` at all.
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "T01_A_claude_1"
        worktree.mkdir()
        other = Path(tmp) / "T02_A_claude_1"
        other.mkdir()
        registry = {
            "claude-old": {
                "main_session_id": "main_x_1_aaaa",
                "cwd": str(worktree),
                "bound_at": "2026-09-09T01:00:00+00:00",
            },
            "claude-new": {
                "main_session_id": "main_x_2_bbbb",
                "cwd": str(worktree),
                "bound_at": "2026-09-09T02:00:00+00:00",
            },
            "claude-elsewhere": {
                "main_session_id": "main_x_3_cccc",
                "cwd": str(other),
                "bound_at": "2026-09-09T03:00:00+00:00",
            },
        }
        original = driver._load_registry
        driver._load_registry = lambda: registry
        try:
            found, _ = driver._registry_entry({"worktree": str(worktree)})
            check(
                "the registry is matched on the worktree, not on an id the hook never writes",
                found,
                "claude-new",
            )
            check(
                "a session bound in another unit's worktree is not this unit's",
                driver._registry_entry({"worktree": str(other)})[0],
                "claude-elsewhere",
            )
            check(
                "no worktree, no lookup",
                driver._registry_entry({})[0],
                None,
            )
            # Windows hands the same directory back with either separator and either drive
            # letter case. A string compare would miss the binding and report the unit as
            # having no transcript at all.
            odd = str(worktree).replace("\\", "/")
            if odd[:1].isalpha() and odd[1:2] == ":":
                odd = odd[0].swapcase() + odd[1:]
            check(
                "a cwd spelled with the other separator and drive case still matches",
                driver._registry_entry({"worktree": odd})[0],
                "claude-new",
            )

            # An exact `bound_at` tie must resolve the same way every run. Whichever entry
            # the dict yields last is not an answer: the same registry would then point at
            # two different transcripts on two runs, and nobody could reproduce either.
            tied = {
                "claude-bbb": {"cwd": str(worktree), "bound_at": "2026-09-09T05:00:00+00:00"},
                "claude-aaa": {"cwd": str(worktree), "bound_at": "2026-09-09T05:00:00+00:00"},
            }
            driver._load_registry = lambda: tied
            first = driver._registry_entry({"worktree": str(worktree)})[0]
            driver._load_registry = lambda: dict(reversed(list(tied.items())))
            second = driver._registry_entry({"worktree": str(worktree)})[0]
            check(
                "an exact bound_at tie resolves the same way regardless of dict order",
                (first, second),
                ("claude-bbb", "claude-bbb"),
            )
        finally:
            driver._load_registry = original

    print()
    print("bench/test_oracle: ledger schema")
    check(
        "every field build_row writes is declared in LEDGER_FIELDS, and vice versa",
        _schema_gap(),
        ([], []),
    )

    print()
    if FAILURES:
        print(f"bench/test_oracle: FAILED ({len(FAILURES)}): {', '.join(FAILURES)}")
        return 1
    print("bench/test_oracle: success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
