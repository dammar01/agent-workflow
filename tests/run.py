"""Test entry point for the research and staging checks.

    python tests/run.py                    # every suite
    python tests/run.py --list             # what can be run on its own
    python tests/run.py --only research
    python tests/run.py --keep-going       # report every failure, not just the first

Exoma's own suite covers the runtime; what runs here is what this repository owns: the
research records, the measurement harnesses, and their fit to the pinned Exoma.
"""

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.checks.blind_sample import _test_blind_sample  # noqa: E402
from tests.checks.harnesses import _test_harnesses_import_against_the_pin  # noqa: E402
from tests.checks.replay import _test_fact_replay  # noqa: E402
from tests.checks.research import _test_research_records_follow_the_schema  # noqa: E402


SUITES: dict[str, tuple] = {
    "harnesses": (_test_harnesses_import_against_the_pin, "every research and staging harness imports against the pinned Exoma"),
    "research": (_test_research_records_follow_the_schema, "research records are YAML the schema accepts; every rule the check claims is refused on a broken fixture"),
    "fact-replay": (_test_fact_replay, "EXP-013 replay: one-token and weighted arms over archived calls, facts learned later kept out, store untouched"),
    "blind-sample": (_test_blind_sample, "blind-judge export: gate blocks read from transcripts, EXP-008 strata, the key kept apart, category labels merged"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-workflow research and staging checks")
    parser.add_argument(
        "--only",
        action="append",
        metavar="NAME",
        help="run just this suite; repeatable. Default runs every suite.",
    )
    parser.add_argument("--list", action="store_true", help="print suite names and exit")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="run every selected suite even after one fails, then report all failures",
    )
    parser.add_argument(
        "--record",
        metavar="PROJECT_ROOT",
        default=None,
        help=(
            "append the outcome to PROJECT_ROOT/.workflow/quality.jsonl so "
            "`exoma --command report` can show a test pass rate. Off by default: a local run "
            "while debugging is not a data point about the repo's health."
        ),
    )
    args = parser.parse_args()

    if args.list:
        width = max(len(name) for name in SUITES)
        for name, (_, description) in SUITES.items():
            print(f"  {name.ljust(width)}  {description}")
        return 0

    selected = args.only or list(SUITES)
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

    if args.record:
        _record_outcome(args.record, selected, failures)

    if failures:
        print(f"\n{len(failures)} of {len(selected)} suite(s) failed:")
        for name, exc in failures:
            print(f"  - {name}: {type(exc).__name__}: {exc}")
        return 1
    print(f"\ntests: success ({len(selected)} suite(s))")
    return 0


def _record_outcome(project_root: str, selected: list, failures: list) -> None:
    """Append this run to the quality stream. Never fails the run it is recording.

    Records which suites ran, not just pass/fail. A green `--only research` and a green
    full run are both "ok", and a pass rate that cannot tell them apart would let a narrow
    run stand in for a broad one.
    """
    try:
        from datetime import datetime, timezone

        from exoma.core.evidence.runtime_io import write_quality_record

        write_quality_record(
            Path(project_root),
            {
                "kind": "tests",
                "at": datetime.now(timezone.utc).isoformat(),
                "ok": not failures,
                "suites": list(selected),
                "failed": [name for name, _ in failures],
            },
        )
    except Exception as exc:  # noqa: BLE001 — recording must never mask the result
        print(f"  (quality record not written: {type(exc).__name__}: {exc})")


if __name__ == "__main__":
    raise SystemExit(main())
