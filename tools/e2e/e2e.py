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
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.e2e.e2e_delegated import delegated_checks  # noqa: E402
from tools.e2e.e2e_support import (  # noqa: E402
    DELEGATED_TIMEOUT, FAIL, PASS, PROVIDER_STAGES, SETUP_TIMEOUT, SKIP, Report,
    dependency_identity, staging_environment,
)

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
        if target not in known:
            raise ValueError(f"unknown default provider {target!r}; select --provider")
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


def known_providers() -> list[str]:
    from exoma.config.providers import bundled_providers
    return list(bundled_providers())


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-workflow staging end-to-end run")
    parser.add_argument("--full", action="store_true", help="run delegated commands (real quota)")
    parser.add_argument("--session", default="e2e-session")
    parser.add_argument("--provider", help=f"provider for a bare --model (default {DEFAULT_PROVIDER})")
    parser.add_argument("--model", action="append", default=[], help="model id or provider=model pairs; required with --full")
    parser.add_argument("--all", action="store_true", help="run every bundled provider; each requires a model")
    parser.add_argument("--setup-timeout", type=positive_seconds, default=SETUP_TIMEOUT, help="seconds per setup process (default: 120)")
    parser.add_argument("--delegated-timeout", type=positive_seconds, default=DELEGATED_TIMEOUT, help="seconds per runner process (default: 900)")
    parser.add_argument("--report-json", type=Path, help="write a structured staging receipt to this path")
    args = parser.parse_args(argv)

    with staging_environment():
        try:
            runs = plan_runs(args.provider, args.model, args.all, known_providers())
        except ValueError as exc:
            parser.error(str(exc))
        report = Report()
        if args.full:
            report.require("dependency identity")
            for provider, _ in runs:
                report.require(*(f"{provider}: {stage}" for stage in PROVIDER_STAGES))
            missing = [provider for provider, model in runs if not model]
            if not runs or missing:
                report.record("provider/model preflight", FAIL, "no providers or missing models: " + ", ".join(missing))
            else:
                report.identity = dependency_identity()
                verified = report.identity.get("verified") is True
                report.stage(
                    "dependency identity", PASS if verified else FAIL,
                    error_type="" if verified else report.identity.get("error_type", "dependency_identity_unavailable"),
                )
                if verified:
                    for provider, model in runs:
                        try:
                            delegated_checks(
                                report, args.session, provider, model,
                                setup_timeout=args.setup_timeout,
                                delegated_timeout=args.delegated_timeout,
                            )
                        except (OSError, ValueError, TypeError) as exc:
                            # Record failure without echoing raw process output or environment.
                            report.record(f"{provider}: staging error", FAIL, type(exc).__name__)
            report.complete_required()
        else:
            report.record("delegation not run", SKIP, "informational only; opt in via --full")

        receipt = report.receipt(
            full=args.full, requests=runs,
            limits={"setup": args.setup_timeout, "delegated": args.delegated_timeout},
        )
        total = len(report.rows)
        passed = total - report.failed - report.skipped
        print(f"\n[E2E REPORT] {receipt['verdict']}")
        print(f"  {passed} passed | {report.failed} failed | {report.skipped} skipped ({total} checks)")
        print("  Coverage: delegated explore and local sweep only; host approval/deployment are separate.")
        if args.report_json:
            try:
                args.report_json.parent.mkdir(parents=True, exist_ok=True)
                args.report_json.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError:
                print("  FAIL report_write_failed")
                return 1
        return 1 if args.full and receipt["verdict"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
