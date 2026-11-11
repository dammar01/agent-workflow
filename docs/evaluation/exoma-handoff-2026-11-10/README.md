# Exoma regression handoff, 2026-11-10

This packet preserves the existing research follow-up for implementation in the
Exoma source repository. It adds no hypothesis or product feature and does not
declare another research package closed.

- [regression-cases.json](regression-cases.json): synthetic fixtures and scenarios.
- [test_exoma_research_regressions.py](test_exoma_research_regressions.py): portable
  native suite with 12 parser and four gate checks.
- [recheck-results.json](recheck-results.json): historical installed-source probes,
  source hashes and separate reviewer/runtime telemetry aggregates.

Run the suite in Exoma's existing development environment after applying the fixes:

```text
python -B test_exoma_research_regressions.py --policy-file PATH/TO/workflow_policy.py
```

At the historical handoff checkpoint the portable suite was syntax-checked, not executed
or integrated. A later private follow-up records native execution against the separately
identified a965cf20 source; that campaign packet is not shipped in the public checkout.
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
