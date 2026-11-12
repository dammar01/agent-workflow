# Exoma regression handoff, 2026-11-10

This packet preserves the existing research follow-up for implementation in the
Exoma source repository. It adds no hypothesis or product feature and does not
declare another research package closed.

- [regression-cases.json](regression-cases.json): synthetic fixtures and scenarios.
- [test_exoma_research_regressions.py](test_exoma_research_regressions.py): portable
  native suite with 12 parser and four gate checks.
- [recheck-results.json](recheck-results.json): historical installed-source probes,
  source hashes and separate reviewer/runtime telemetry aggregates.
- [a965cf20-results.json](a965cf20-results.json): sanitized current-pin source results,
  suite commands, P/G case mapping, output hashes and verification limits.

## Current runtime and source results

At this source-check checkpoint, the research harness selected Exoma
`a965cf20bce03015cdf0f56952eb6c45e30cbfa4` through
[pyproject.toml](../../../pyproject.toml) and [uv.lock](../../../uv.lock).
[DEC-086](../../research/decisions/DEC-086-exoma-a965cf20-has-identity-specific-staging-evidence.yaml)
records the evidence for this source separately from
[DEC-084](../../research/decisions/DEC-084-exoma-1071c2f-staged-for-post-1-0-0-analysis.yaml),
whose staging and provider results belong to `1071c2f`. The latest pin and its
separate provider results are in the [2026-11-12 staging receipt](../exoma-staging-2026-11-12/README.md).

| Evidence layer | Status for `a965cf20` | Boundary |
| --- | --- | --- |
| Research-harness CI | The workflow installs the locked pin and runs schema checks plus the default suites; the source receipt's six-suite boundary is described below | Coverage definition; no remote run log or CI conclusion is newly verified here |
| Portable P01-P12 / G01-G04 | Private execution records report 16 tests, zero failures on 2026-11-10; aggregate result and case-to-method mapping are now in the public receipt | Windows, unmodified source, Claude policy only; individual raw test output remains private |
| Native Exoma integration suites | `codex-host`, `execution-chain`, `verify-reuse`, `metrics-contract`: exit 0 | Designed source/fixture checks, separate from this repository's default suites |
| `verify-runtime-tests` | Initial exit 1; fixture recheck exit 0 | `AGENT_HOME` hid the temporary-home fixture; recheck used disposable `HOME`/`USERPROFILE` without that override and changed no source or assertions |
| Real-provider E2E | Not established for this SHA by the available receipt | DEC-084's provider results concern `1071c2f`; passing source suites do not establish a later provider run |
| Deployed hooks | Not directly verified by the available receipt | Policy/source and isolated integration tests do not establish installed-hook parity, host trust/loading or live approval behavior |

At this source receipt's checkpoint, the [ordinary CI workflow](../../../.github/workflows/ci.yml) ran
`check_research.py --git` and `tests/run.py --keep-going --record .` on Linux and
Windows with Python 3.13. Its six default suites are `harnesses`, `research`,
`research-closure`, `fact-replay`, `real-use` and `blind-sample`.
`research-closure-measure` is opt-in. The portable packet here is not a default
suite, and [real-provider E2E](../../../.github/workflows/e2e-full.yml) is a
separate manual workflow. A successful ordinary CI run therefore has a narrower
claim than completion of every staging gate.

Subsequent harness work on 2026-11-11 adds the quota-free `e2e` suite to the default
registry ([DEC-087](../../research/decisions/DEC-087-engineering-readiness-keeps-technical-staging-separate-from-research-validation.yaml)).
The current seven-suite definition is in `tests/run.py`. That local harness change does not
rerun the historical source receipt, inherit provider results or establish deployed-hook
verification. Current layers are tracked in the [engineering checkpoint](../README.md#engineering-readiness-checkpoint-2026-11-11).

The public receipt restates existing private execution records; publication on
2026-11-11 is not a new runtime run. It retains the original source-check receipt
hash, test-script hash, policy hash, output hashes and both fixture attempts.
The portable 16/16 result comes from the private dated DEC-086; its case mapping
comes from the unchanged public script and inventory. Raw logs and campaign
administration remain private, so the receipt does not enable an independent
recount of those executions. Authority and approval integration scenarios marked
specification-only in the inventory remain outside this portable result.

## Reproducing the source checks

Run the suite in the development environment for the exact Exoma source under test:

```text
python -B test_exoma_research_regressions.py --policy-file PATH/TO/workflow_policy.py
```

For the recorded `a965cf20` run, the unmodified checkout's `src` directory was
first on `PYTHONPATH`, and the policy was
`<source>/src/exoma/_bundle/config/hosts/claude/hooks/workflow_policy.py`.
The native integration command, run from that Exoma checkout, was
`python -B tests/run.py --only <suite>` for each native suite named above.
The portable test strings are data; the suite does not execute their commands.

## Historical handoff boundary

At the historical handoff checkpoint the portable suite was syntax-checked, not executed
or integrated. A later private follow-up records native execution against the separately
identified a965cf20 source; its sanitized results are now published above, while the
complete campaign packet is not shipped in the public checkout.
The unchanged script's "current inspected sources" comment refers to this historical
handoff, not that newer pin.
Historical in-memory probes met eight of 12 parser expectations on each of two
installations and three of four gate expectations on each of three policies.
Four parser cases and the quoted-mention gate case exposed defects. These probe
results are not native-suite results or evidence of a fix in a newer version.
Main-agent authority and approval-flow scenarios marked specification-only still
need Exoma integration tests. Keep desired assertions and control cases intact.

The original source packet, repair handoff and maintainer-directive excerpt remain in
local research drafts. The synthetic fixture JSON and unchanged test script retain their
captured content. The public results omit absolute local paths, session/plan identifiers
and per-event projections.
The public packet excludes administrative directives, raw/base64 snapshots, protected-file
maps, working-tree metadata and the internal audit ledger. Each edited JSON records its
original source SHA-256. Private logs and original audit artifacts remain local.

Five previously accepted research closures retain their existing dispositions.
Historical counts and validation labels are preserved. A final main-agent decision,
a reviewer declaration and a runtime-derived verdict remain separate concepts;
the telemetry does not establish a main-agent final-success rate.
