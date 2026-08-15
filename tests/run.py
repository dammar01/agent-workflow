"""Test entry point.

    python tests/run.py                    # everything, same order as before
    python tests/run.py --list             # what can be run on its own
    python tests/run.py --only continuation --only jobs
    python tests/run.py --keep-going       # report every failure, not just the first

Why a registry rather than a bare script: the scenario suite is one stateful sequence —
later assertions read workspaces earlier ones built — so a failure partway through used to
hide everything behind it, and there was no way to re-run just the part being debugged.
The standalone checks each build their own temp workspace, so they genuinely can run alone,
and those are what this exposes. `scenario` stays whole because splitting it would mean
inventing independence it does not have.
"""

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.checks.continuation import _test_contract_continuation  # noqa: E402
from tests.checks.facts import (  # noqa: E402
    _test_anchor_relocation,
    _test_evidence_anchor_relocation,
    _test_evidence_reuse,
    _test_facts_concurrency,
)
from tests.checks.jobs import _test_submit_admission  # noqa: E402
from tests.checks.messages import _test_no_code_in_messages  # noqa: E402
from tests.checks.provider import (  # noqa: E402
    _test_provider_seam,
    _test_provider_selection,
)
from tests.checks.redaction import _test_redaction_boundary  # noqa: E402
from tests.checks.verify_gaps import _test_quick_verify_gaps  # noqa: E402
from tests.checks.workspace import (  # noqa: E402
    _test_init_upgrade_and_session_guard,
    _test_project_session_isolation,
    _test_workspace_release_guards,
)
from tests.scenario import run_tests  # noqa: E402

# name -> (callable, one-line description). `scenario` runs the standalone checks itself
# at the end of its own sequence, so a default run does not repeat them here.
SUITES: dict[str, tuple] = {
    "scenario": (run_tests, "stateful sequence + every standalone check (the full run)"),
    "provider-seam": (_test_provider_seam, "adapter registry and provider resolution"),
    "provider-selection": (_test_provider_selection, "interactive provider/model/effort write"),
    "messages": (_test_no_code_in_messages, "AST scan: no code leaks into user-facing text"),
    "facts-concurrency": (_test_facts_concurrency, "fact store under concurrent writers"),
    "anchor-relocation": (_test_anchor_relocation, "facts survive a line moving"),
    "evidence-anchor-relocation": (_test_evidence_anchor_relocation, "evidence anchors survive a line moving"),
    "evidence-reuse": (_test_evidence_reuse, "identical query served from a fresh artifact"),
    "redaction": (_test_redaction_boundary, "secret boundary on outbound payloads"),
    "verify-gaps": (_test_quick_verify_gaps, "quick verify reports gaps as incomplete"),
    "jobs": (_test_submit_admission, "job admission, capacity, and lock"),
    "workspace-release": (_test_workspace_release_guards, "lock release guards"),
    "session-isolation": (_test_project_session_isolation, "one project's session cannot read another's"),
    "init-upgrade": (_test_init_upgrade_and_session_guard, "init/upgrade and the session guard"),
    "continuation": (_test_contract_continuation, "bounded continuation keeps the first reply's evidence"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-workflow tests")
    parser.add_argument(
        "--only",
        action="append",
        metavar="NAME",
        help="run just this suite; repeatable. Default runs `scenario`, which covers all.",
    )
    parser.add_argument("--list", action="store_true", help="print suite names and exit")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="run every selected suite even after one fails, then report all failures",
    )
    args = parser.parse_args()

    if args.list:
        width = max(len(name) for name in SUITES)
        for name, (_, description) in SUITES.items():
            print(f"  {name.ljust(width)}  {description}")
        return 0

    selected = args.only or ["scenario"]
    unknown = [name for name in selected if name not in SUITES]
    if unknown:
        print(f"unknown suite(s): {', '.join(unknown)}", file=sys.stderr)
        print("run with --list to see the names", file=sys.stderr)
        return 2

    failures: list[tuple[str, BaseException]] = []
    for name in selected:
        run, _ = SUITES[name]
        started = time.monotonic()
        try:
            run()
        except BaseException as exc:  # noqa: BLE001 — a failed check is any exception
            failures.append((name, exc))
            print(f"  FAIL  {name}  ({type(exc).__name__}: {exc})")
            if not args.keep_going:
                break
        else:
            print(f"  PASS  {name}  ({time.monotonic() - started:.1f}s)")

    if failures:
        print(f"\n{len(failures)} of {len(selected)} suite(s) failed:")
        for name, exc in failures:
            print(f"  - {name}: {type(exc).__name__}: {exc}")
        return 1
    print(f"\ntests: success ({len(selected)} suite(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
