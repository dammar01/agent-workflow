# EXP-001: Workflow × prompt strategy (2×2)

## Metadata

```yaml
id: EXP-001
date:
status: planned
hypothesis: H-002
version:
```

## Objective

Separate the effect of the **workflow** from the effect of the **prompt strategy**.
Comparing "direct" with "workflow" alone cannot do that: in everyday use, workflow users
also tend to write objective-level requests, so any difference could come from either.

## Setup

Four conditions:

| | Procedural requests | Objective-level requests |
| --- | --- | --- |
| **Direct coding agent** | A | B |
| **agent-workflow** | C | D |

- `B − A`: effect of prompt strategy without the workflow.
- `C − A`: effect of the workflow with procedural use.
- `D − C` versus `B − A`: whether the workflow and objective-level delegation interact.

## Method

To be designed. Requirements already known:

- Tasks labeled by complexity (see [H-001](../hypotheses/H-001-complexity-predicts-delegation-value.md)).
- Each run labeled with its prompt strategy; this label does not exist in any current data.
- Randomized or paired assignment. The existing 3-arm benchmark (`bench/`: direct Claude,
  native sub-agent, agent-workflow) has no prompt-strategy factor, and its observation
  design is neither randomized nor paired, so it cannot answer this question as is.

## Metrics

From [H-002](../hypotheses/H-002-objective-delegation-reduces-orchestration-overhead.md):
human active time, blocking time, interventions, follow-up prompts, completion, rework,
verification result. Human-attention metrics need new instrumentation first.

Also: time to completion (`main.py --command report`, `time_to_completion_seconds`),
accepted tasks, first-pass correctness.

## Results

None.

## Interpretation

None.

## Threats to validity

- The operator's skill with each strategy.
- Learning effects if the same person runs all four conditions.
- Provider and model variance.

## Outcome for the hypothesis

`pending`

## Related records

- [H-002](../hypotheses/H-002-objective-delegation-reduces-orchestration-overhead.md)
- [SYN-001](../synthesis/SYN-001-procedural-prompting-and-adoption-loop.md)
- [RQ-02, RQ-03](../questions.md)
