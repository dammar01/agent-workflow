"""Staging end-to-end run against the pinned Exoma.

    python tools/e2e/e2e.py --full --model <id> [--provider opencode|codex|agy|claude]
                                      # one provider (spends quota)
    python tools/e2e/e2e.py --full --model codex=<id> --model claude=<id>
                                      # the named providers, one run each
    python tools/e2e/e2e.py --full --all --model codex=<id>,claude=<id>,opencode=<id>,agy=<id>
                                      # every provider the pinned Exoma ships

The model is per provider, so several providers take `provider=model` pairs (repeat
--model or separate pairs with commas). With --all, a provider left without a model is a
FAIL before any call, not a skip. Exoma's own suite covers the local, integrity, installer
and promote checks; what staging adds is the real delegated run, against the exact commit
pyproject.toml pins. Check bodies live in tools/e2e/e2e_*.py; this module owns the CLI.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.e2e.e2e_delegated import delegated_checks  # noqa: E402
from tools.e2e.e2e_support import FAIL, Report, SKIP  # noqa: E402
from exoma.config.providers import bundled_providers  # noqa: E402 - after EXOMA_HOME is set

DEFAULT_PROVIDER = "opencode"


def plan_runs(
    provider: str | None, models: list[str], run_all: bool, known: list[str]
) -> list[tuple[str, str | None]]:
    """Resolve the CLI into (provider, model) runs. Raises ValueError on a bad request."""
    paired: dict[str, str] = {}
    bare: list[str] = []
    for entry in models:
        for item in (part.strip() for part in entry.split(",")):
            if not item:
                continue
            if "=" not in item:
                bare.append(item)
                continue
            name, _, model = (piece.strip() for piece in item.partition("="))
            if name not in known:
                raise ValueError(f"unknown provider {name!r}; known: {', '.join(known)}")
            if not model:
                raise ValueError(f"no model after {name}=")
            if name in paired:
                raise ValueError(f"provider {name} given twice")
            paired[name] = model
    if len(bare) > 1:
        raise ValueError("more than one model without a provider; use provider=model pairs")
    if provider is not None and provider not in known:
        raise ValueError(f"unknown provider {provider!r}; known: {', '.join(known)}")
    if bare:
        target = provider or DEFAULT_PROVIDER
        if target in paired:
            raise ValueError(f"provider {target} given twice")
        paired[target] = bare[0]
    if run_all:
        return [(name, paired.get(name)) for name in known]
    if not paired:
        # No model at all: one run, refused inside delegated_checks before any call.
        return [(provider or DEFAULT_PROVIDER, None)]
    if provider is not None and provider not in paired:
        paired[provider] = None
    return list(paired.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-workflow staging end-to-end run")
    parser.add_argument(
        "--full",
        action="store_true",
        help="run the delegated commands (minutes + real quota)",
    )
    parser.add_argument(
        "--session", default="e2e-session", help="session id for delegated runs"
    )
    parser.add_argument(
        "--provider",
        help=f"second agent for a bare --model (default {DEFAULT_PROVIDER})",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="model id, or provider=model pairs (repeatable, comma-separated); required with --full",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="run every provider the pinned Exoma ships; each needs a provider=model pair",
    )
    args = parser.parse_args()

    known = list(bundled_providers())
    try:
        runs = plan_runs(args.provider, args.model, args.all, known)
    except ValueError as exc:
        parser.error(str(exc))

    report = Report()
    if args.full:
        for provider, model in runs:
            delegated_checks(report, args.session, provider, model)
    else:
        report.record("delegated commands", SKIP, "opt-in via --full")

    total = len(report.rows)
    passed = total - report.failed - report.skipped
    print("\n[E2E REPORT]")
    if args.full and len(runs) > 1:
        for provider, _ in runs:
            rows = [status for name, status, _ in report.rows if name.startswith(f"{provider}: ")]
            failed = rows.count(FAIL)
            skipped = rows.count(SKIP)
            print(
                f"  {provider:9} {len(rows) - failed - skipped} passed | {failed} failed | "
                f"{skipped} skipped"
            )
    print(
        f"  {passed} passed | {report.failed} failed | {report.skipped} skipped "
        f"({total} checks)"
    )
    if report.skipped:
        print("  skipped checks are NOT passes — see the reasons above")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
