# Exoma regression handoff, 2026-11-10

This packet preserves the existing research follow-up for implementation in the
Exoma source repository. It adds no hypothesis or product feature and does not
declare another research package closed.

- [EXOMA-HANDOFF.txt](EXOMA-HANDOFF.txt): defects, repair targets, decisions and limits.
- [regression-cases.json](regression-cases.json): synthetic fixtures and scenarios.
- [test_exoma_research_regressions.py](test_exoma_research_regressions.py): portable
  native suite with 12 parser and four gate checks.
- [recheck-results.json](recheck-results.json): historical installed-source probes,
  source hashes and separate reviewer/runtime telemetry aggregates.
- [maintainer-decisions.json](maintainer-decisions.json): selected maintainer decisions
  and the four updated research records.

Run the suite in Exoma's existing development environment after applying the fixes:

```text
python -B test_exoma_research_regressions.py --policy-file PATH/TO/workflow_policy.py
```

The portable suite was syntax-checked, not executed or integrated in Exoma here.
Historical in-memory probes met eight of 12 parser expectations on each of two
installations and three of four gate expectations on each of three policies.
Four parser cases and the quoted-mention gate case exposed defects. These probe
results are not native-suite results or evidence of a fix in a newer version.
Main-agent authority and approval-flow scenarios marked specification-only still
need Exoma integration tests. Keep desired assertions and control cases intact.

The source packet remains unchanged in the ignored local research archive at
`docs/research-drafts/_data/research-closure-2026-11-10/remaining/exoma-handoff-2026-11-10/`.
The overview, fixture JSON and test script are byte-identical copies. The results
omit absolute local paths, session/plan identifiers and per-event projections.
The decision excerpt omits raw/base64 snapshots, protected-file maps, working-tree
metadata and the internal audit ledger. Each edited JSON records its original
source SHA-256. Private logs and original audit artifacts remain local.

Five previously accepted research closures retain their existing dispositions.
Historical counts and validation labels are preserved. A final main-agent decision,
a reviewer declaration and a runtime-derived verdict remain separate concepts;
the telemetry does not establish a main-agent final-success rate.
