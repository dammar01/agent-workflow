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

from tests.checks.adapters import (  # noqa: E402
    _test_adapter_error_normalization,
    _test_stdin_failure_reaches_call_meta,
    _test_adapter_redaction_is_shared,
)
from tests.checks.audit import (  # noqa: E402
    _test_audit_is_not_telemetry,
    _test_audit_report,
    _test_audit_survives_a_torn_row,
)
from tests.checks.prompt import (  # noqa: E402
    _test_permitted_tools_line,
    _test_prompt_contract_blocks,
    _test_task_cap_follows_the_provider_transport,
    _test_task_cap_is_visible_in_and_out_of_band,
    _test_unknown_role_is_refused,
    _test_verify_branch_carries_routing_contract,
)
from tests.checks.bundle_sync import (  # noqa: E402
    _test_bundle_registry_bijection,
    _test_every_shipped_hook_has_both_os_flavours,
)
from tests.checks.continuation import (  # noqa: E402
    _test_contract_continuation,
    _test_continuation_prompt_is_bounded,
)
from tests.checks.contracts import _test_workflow_contracts  # noqa: E402
from tests.checks.deps import _test_runtime_is_stdlib_only  # noqa: E402
from tests.checks.governance import _test_governance_controls  # noqa: E402
from tests.checks.graph_verification import _test_graph_verification  # noqa: E402
from tests.checks.telemetry import _test_telemetry_metrics  # noqa: E402
from tests.checks.facts import (  # noqa: E402
    _test_anchor_relocation,
    _test_evidence_anchor_relocation,
    _test_evidence_reuse,
    _test_facts_concurrency,
)
from tests.checks.installer import (  # noqa: E402
    _test_installer_drift_check,
    _test_installer_rollback_receipt,
    _test_installer_settings_are_additive,
    _test_installer_settings_merge,
    _test_installer_text_merging,
)
from tests.checks.jobs import _test_submit_admission  # noqa: E402
from tests.checks.messages import _test_no_code_in_messages  # noqa: E402
from tests.checks.provider import (  # noqa: E402
    _test_agy_provider,
    _test_provider_seam,
    _test_provider_selection,
)
from tests.checks.usage_tokens import _test_usage_token_accounting  # noqa: E402
from tests.checks.redaction import _test_redaction_boundary  # noqa: E402
from tests.checks.registry import _test_every_check_is_registered  # noqa: E402
from tests.checks.verify_gaps import (  # noqa: E402
    _test_quick_verify_gaps,
    _test_verification_routing,
)
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
    "registry": (_test_every_check_is_registered, "every check here is reachable from both entry points"),
    "bundle-sync": (_test_bundle_registry_bijection, "skills/ and intent-map.json agree with CLAUDE.md"),
    "hook-flavours": (_test_every_shipped_hook_has_both_os_flavours, "every shipped hook ships .ps1 and .sh"),
    "provider-seam": (_test_provider_seam, "adapter registry and provider resolution"),
    "provider-selection": (_test_provider_selection, "interactive provider/model/effort write"),
    "agy": (_test_agy_provider, "agy parsing, argv, and its read-boundary guard"),
    "messages": (_test_no_code_in_messages, "AST scan: no code leaks into user-facing text"),
    "facts-concurrency": (_test_facts_concurrency, "fact store under concurrent writers"),
    "anchor-relocation": (_test_anchor_relocation, "facts survive a line moving"),
    "evidence-anchor-relocation": (_test_evidence_anchor_relocation, "evidence anchors survive a line moving"),
    "evidence-reuse": (_test_evidence_reuse, "identical query served from a fresh artifact"),
    "redaction": (_test_redaction_boundary, "secret boundary on outbound payloads"),
    "verify-gaps": (_test_quick_verify_gaps, "quick verify reports gaps as incomplete"),
    "verify-routing": (_test_verification_routing, "the routing table decides what blocks, both ways"),
    "jobs": (_test_submit_admission, "job admission, capacity, and lock"),
    "workspace-release": (_test_workspace_release_guards, "lock release guards"),
    "session-isolation": (_test_project_session_isolation, "one project's session cannot read another's"),
    "init-upgrade": (_test_init_upgrade_and_session_guard, "init/upgrade and the session guard"),
    "continuation": (_test_contract_continuation, "bounded continuation keeps the first reply's evidence"),
    "continuation-size": (_test_continuation_prompt_is_bounded, "the recovery prompt fits the command line it travels on"),
    "contracts": (_test_workflow_contracts, "workflow contracts round-trip and the usage stream derives honestly"),
    "deps": (_test_runtime_is_stdlib_only, "shipped code imports nothing outside the stdlib"),
    "telemetry": (_test_telemetry_metrics, "P1 metrics count tasks, not calls, and report their denominators"),
    "usage-tokens": (_test_usage_token_accounting, "provider token counts reach the row, and breakdowns never become addends"),
    "governance": (_test_governance_controls, "provider allowlist, budget ceiling, tool policy, local-first streams"),
    "graph-verification": (_test_graph_verification, "per-node graph provenance, drift vs move, subgraph slicing"),
    "adapters": (_test_adapter_error_normalization, "every adapter normalises errors and counts redactions alike"),
    "adapters-shared": (_test_adapter_redaction_is_shared, "no adapter carries a private copy of the redaction helpers"),
    "adapters-stdin": (_test_stdin_failure_reaches_call_meta, "a failed stdin handover names its cause in the call meta"),
    "prompt-blocks": (_test_prompt_contract_blocks, "every role/command branch asks for a shape the runtime parses"),
    "prompt-verify": (_test_verify_branch_carries_routing_contract, "verify prompt carries the severity routing triple"),
    "prompt-tools": (_test_permitted_tools_line, "declared tool policy reaches the prompt, absence stays absent"),
    "prompt-cap": (_test_task_cap_is_visible_in_and_out_of_band, "task truncation is reported in band and out"),
    "prompt-transport-cap": (_test_task_cap_follows_the_provider_transport, "the task cap is derived from the provider transport, not one shared constant"),
    "prompt-role": (_test_unknown_role_is_refused, "an unknown role is refused instead of building an unparseable prompt"),
    "installer-text": (_test_installer_text_merging, "lenient decode, intent stanzas, managed-block splice"),
    "installer-settings": (_test_installer_settings_merge, "hook refresh keeps user hooks; POSIX rewrite ships bash"),
    "installer-additive": (_test_installer_settings_are_additive, "settings merge adds missing keys and keeps the user's"),
    "installer-rollback": (_test_installer_rollback_receipt, "receipted rollback restores, deletes, and refuses drift"),
    "installer-check": (_test_installer_drift_check, "settings drift detection: missing, current, unparseable"),
    "audit": (_test_audit_report, "the governance trail reads back and keeps a null provider visible"),
    "audit-torn": (_test_audit_survives_a_torn_row, "a partial final line does not hide the readable trail"),
    "audit-separate": (_test_audit_is_not_telemetry, "audit and usage stay separate readers over separate files"),
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
    parser.add_argument(
        "--record",
        metavar="PROJECT_ROOT",
        default=None,
        help=(
            "append the outcome to PROJECT_ROOT/.workflow/quality.jsonl so "
            "`--command report` can show a test pass rate. Off by default: a local run "
            "while debugging is not a data point about the repo's health."
        ),
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

    Records which suites ran, not just pass/fail. A green `--only contracts` and a green
    full run are both "ok", and a pass rate that cannot tell them apart would let a narrow
    run stand in for a broad one.
    """
    try:
        from datetime import datetime, timezone

        from core.evidence.runtime_io import write_quality_record

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
