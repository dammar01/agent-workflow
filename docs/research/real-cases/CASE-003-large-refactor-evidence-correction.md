# CASE-003: Large refactor — the primary agent corrects secondary-agent claims

## Metadata

```yaml
id: CASE-003
visibility: public
environment: private_production
repository_identity: undisclosed
recorded_on: 2026-09-26
evidence_tier: creator_observation   # see methodology.md, "Evidence tiers"
```

Scale figures are deliberately rounded; exact counts and the project identity come from a
private repository and are not published.

## Context

A mature production web frontend with a large number of source files and several large
modules that mixed data access, business logic, and UI responsibilities.

## Task

A structural refactor of the repository: reorganizing modules and separating
responsibilities without changing behavior.

## Workflow

```text
explore → re-explore / validate → plan → execute → verify
```

Exploration was repeated deliberately: a second pass was used to validate the first before
planning.

## Observation

- Repository-level structure could be investigated in the secondary agent's context,
  separately from the primary agent's implementation reasoning.
- The primary agent did not accept the secondary agent's findings wholesale. Several broad
  claims of code duplication were checked by direct measurement and turned out to be
  overstated; they were corrected before the plan was built on them.
- The resulting change was large: several hundred file moves or renames, over a hundred new
  files, and a handful of deletions.
- The result was checked with type checking, the test suite, and a production build.

## Evidence

Maintainer's session record and the repository's own checks (type check, tests, build) at
the time. Telemetry from the session showed the secondary agent's gross token volume
exceeding the primary agent's context, both in the hundreds of thousands of tokens, largely
because of repeated and cached context.

## Outcome

`positive` for the refactor itself, within the limits below.

## Unexpected behavior

The secondary agent produced plausible but overstated duplication claims. This is the
failure mode the "evidence candidates, not truth" rule exists for.

## Limitations

- One repository, one operator who is also the tool's author (creator observation tier).
- No comparison run without the workflow, so the outcome cannot be attributed to it.
- The token figures show that externalization **moves** exploration out of the primary
  context; it does **not** eliminate computation. Total compute and latency went up.

## Related research

- [H-002](../hypotheses/H-002-objective-delegation-reduces-orchestration-overhead.md)
- [EXP-001](../experiments/EXP-001-workflow-by-prompt-strategy.md)
- [RQ-01](../questions.md)

## Implementation reference

```yaml
version: 3.7.x
commit:
feature: explore / plan / verify on a large refactor; primary-agent verification of evidence
```
