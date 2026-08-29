"""agent-workflow end-to-end checks.

    python tools/e2e.py           # local + integrity + installer
    python tools/e2e.py --full    # also the delegated calls (spends quota)

Check bodies live in tools/e2e_*.py; this module owns the CLI and the order they
run in.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.e2e.e2e_delegated import delegated_checks  # noqa: E402
from tools.e2e.e2e_installer import installer_checks  # noqa: E402
from tools.e2e.e2e_integrity import integrity_checks  # noqa: E402
from tools.e2e.e2e_local import local_checks  # noqa: E402
from tools.e2e.e2e_support import Report, SKIP, _json_from, _run_cli  # noqa: E402,F401


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-workflow end-to-end checks")
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run delegated commands (minutes + real quota)",
    )
    parser.add_argument(
        "--session", default="e2e-session", help="session id for delegated runs"
    )
    args = parser.parse_args()

    report = Report()
    local_checks(report)
    integrity_checks(report)
    installer_checks(report)
    if args.full:
        delegated_checks(report, args.session)
    else:
        report.record("delegated commands", SKIP, "opt-in via --full")

    total = len(report.rows)
    passed = total - report.failed - report.skipped
    print("\n[E2E REPORT]")
    print(
        f"  {passed} passed | {report.failed} failed | {report.skipped} skipped "
        f"({total} checks)"
    )
    if report.skipped:
        print("  skipped checks are NOT passes — see the reasons above")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
