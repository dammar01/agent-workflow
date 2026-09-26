# CASE-002: Latency is a recurring complaint

## Metadata

```yaml
id: CASE-002
visibility: public
environment: private_production
repository_identity: undisclosed
recorded_on: 2026-09-26
evidence_strength: anecdotal   # complaints reported to the maintainer; no latency data collected
```

## Context

Same team and period as [CASE-001](CASE-001-procedural-decomposition.md).

## Task

Everyday feature and maintenance work.

## Workflow

Delegated commands (`explore`, `analyze`, `plan`, `verify`) run the secondary agent, which
reads the repository before returning evidence.

## Observation

Several team members complained that delegated requests were slow compared with asking the
coding agent directly.

For scale only, not as team data: in one maintainer session on this repository
(2026-09-26), six delegated `explore`/`analyze`/`plan` calls took between about 4 and 8
minutes each (`meta.duration_seconds` 238–488). The README at that time described a
delegated call as taking "tens of seconds to a few minutes", which understates broad
analysis and planning.

## Evidence

Informal complaints. No `report` output (`time_to_completion_seconds`) was collected from
team workspaces.

## Outcome

`inconclusive`

## Unexpected behavior

None identified. The duration is the secondary agent doing its work, not a hang or retry
that was observed.

## Limitations

- Whether latency was acceptable depends on task size, which was not recorded. Latency
  spent on a task that did not need exploration is pure cost; the same latency on a
  cross-module feature may be worth it.
- Combined with [CASE-001](CASE-001-procedural-decomposition.md): if tasks were framed
  procedurally, the team paid the latency without receiving the exploration benefit.
- The maintainer's session numbers come from one repository and one provider.

## Related research

- [CASE-001](CASE-001-procedural-decomposition.md)
- [H-001](../hypotheses/H-001-complexity-predicts-delegation-value.md)

## Implementation reference

```yaml
version: 3.7.x
commit:
feature: delegated call latency; measured by `main.py --command report`
```
