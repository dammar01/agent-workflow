# H-002: Objective-level delegation reduces human orchestration overhead

## Metadata

```yaml
id: H-002
status: proposed
provenance:
  type: synthesized   # team observation + maintainer experience + AI-assisted discussion
  confidence: low
```

## Statement

> For sufficiently complex coding tasks, objective-level delegation reduces developer
> orchestration overhead compared with procedural task decomposition, while keeping task
> quality acceptable.

## Motivation

The project has mostly been framed around context: keeping repository exploration out of
the primary agent's window. Procedural use also consumes **developer attention**:

```text
prompt → wait → inspect → prompt → wait → inspect → …
```

The developer is the synchronization point at every step. With objective-level delegation
(`objective → explore → plan → execute → verify`) the developer intervenes at fewer, higher
points, and may be able to work on something else in between.

If true, a stronger value proposition than "save premium context" is *delegate outcomes
instead of micromanaging steps*, moving the developer's role from remote-control operator
toward director and supervisor. Human oversight does not disappear; the level at which it
happens changes.

## Supporting sources

- [SYN-001](../synthesis/SYN-001-procedural-prompting-and-adoption-loop.md)
- [CASE-001](../real-cases/CASE-001-procedural-decomposition.md)
- [CASE-003](../real-cases/CASE-003-large-refactor-evidence-correction.md)

## Counter evidence

- Objective-level requests may need more review of the plan, moving attention rather than
  reducing it.
- Latency ([CASE-002](../real-cases/CASE-002-latency-perception.md)) may cancel the gain if
  the developer waits instead of switching tasks.
- Quality may drop when the developer's own decomposition carried knowledge the agent lacks.

## Expected effect

Fewer human interventions and follow-up prompts per completed task, and less time actively
blocked, at similar verification outcome and rework.

## Evaluation

Candidate metrics: human active time, blocking time, human interventions, follow-up
prompts, task completion, rework, files changed, verification result, parallel tasks
handled, subjective usefulness.

**Instrumentation does not exist yet.** The usage record has no fields for active time,
blocking time, interventions, or follow-up prompts; the benchmark observer only derives
proxies such as message turns and turns-to-first-edit from transcripts. Telemetry's
`rework` counts verification rounds beyond the first — it is **not** human rework.

Design: [EXP-001](../experiments/EXP-001-workflow-by-prompt-strategy.md).

## Limitations

The prompt strategy and the workflow are confounded in everyday use; see EXP-001.

## Outcome

`pending`

## Related decisions

None.
