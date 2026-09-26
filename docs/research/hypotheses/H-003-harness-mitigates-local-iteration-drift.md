# H-003: Exploration-first workflows mitigate drift from local iteration

## Metadata

```yaml
id: H-003
status: proposed
provenance:
  type: synthesized   # maintainer reasoning + AI-assisted discussion
  confidence: low
```

## Statement

> On mature repositories, a workflow that explores the repository and plans before
> implementing mitigates some failure modes of highly procedural, locally-verified agent
> iteration — in particular duplication, implicit coupling, and architectural drift.

The claim is deliberately limited. It is **not** that agent-workflow makes highly
autonomous ("vibe") coding safe.

## Motivation

Iteration that checks only local output:

```text
idea → generate → works locally → next request → generate → works locally → …
```

can accumulate local correctness at the expense of the whole: duplicated logic, coupling
nobody chose, and drift from the existing architecture, raising future complexity.
Exploration and planning surface existing code and conventions before new code is written.

## Supporting sources

None yet beyond reasoning. [CASE-003](../real-cases/CASE-003-large-refactor-evidence-correction.md)
shows that repository-level investigation is possible, not that it prevents drift.

## Counter evidence

- Exploration can itself be wrong: CASE-003 records overstated duplication claims.
- A plan does not bind the implementation; drift can still be introduced during execution.

## Expected effect

Fewer duplicated implementations and fewer changes that bypass existing modules, in work
done through the workflow compared with purely procedural work.

## Evaluation

Not designed. Would require a drift measure (for example duplicate detection or
architecture-rule violations) applied to comparable changes.

## Limitations

Drift appears over many changes, not in one task; short experiments may not show it.

## Outcome

`pending`

## Related decisions

None.
