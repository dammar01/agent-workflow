# H-001: Task complexity predicts whether delegation is worth its latency

## Metadata

```yaml
id: H-001
status: proposed
provenance:
  type: synthesized   # team observations + AI-assisted discussion + maintainer reasoning
  confidence: low
```

## Statement

> For a given task, an observable measure of complexity (such as the number of modules or
> domains involved, or whether the affected areas are known in advance) predicts whether
> delegating exploration to the secondary agent produces enough benefit to justify its
> latency.

## Motivation

[CASE-002](../real-cases/CASE-002-latency-perception.md) records latency as a recurring
complaint. The obvious response is to make the secondary agent faster. The more fundamental
question is when secondary exploration is necessary at all: if the workflow could tell that
a task does not need it, the latency would not be paid on those tasks.

Today that decision is made entirely by the developer (docs/team-guide/when-to-use.md).

## Supporting sources

- [CASE-001](../real-cases/CASE-001-procedural-decomposition.md): procedural framing leaves
  the workflow's benefit unexercised
- [CASE-002](../real-cases/CASE-002-latency-perception.md): latency complaints
- AI-assisted discussion (research input, not evidence)

## Counter evidence

- Complexity may not be observable from the request text before exploration: the reason to
  explore is often that the affected areas are unknown.
- The benefit of exploration may come from surfacing unexpected coupling in tasks that look
  simple, which a complexity predictor would skip.

## Expected effect

If supported, a complexity signal could route small, local tasks away from delegation (or to
local commands) and keep delegation for broad ones, lowering average latency without
losing the benefit where it matters.

## Evaluation

Not designed yet. A first step is data: record task size or affected-module count alongside
`time_to_completion_seconds` from `main.py --command report`, and whether the delegated
evidence changed the plan. An `EXP-XXX` record will describe the method.

## Limitations

- Current evidence is anecdotal and from one team.
- "Benefit" is not yet defined in measurable terms.

## Outcome

`pending`

## Related decisions

None yet. No runtime change has been made on the basis of this hypothesis.
