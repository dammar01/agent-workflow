# Evaluation

How well `agent-workflow` works, and under which conditions. Two kinds of material live
here, and they must not be read as the same kind of evidence:

| Document | Kind | What it can support |
| --- | --- | --- |
| [benchmark.md](benchmark.md) | Controlled study (3 arms, frozen SUT, fixed oracle) | Comparisons between direct Claude, native sub-agents, and agent-workflow |
| [observed-usage.md](observed-usage.md) | Uncontrolled telemetry from one machine | How much text the digest contract kept out of the primary context in practice |

Open questions the evaluation is meant to answer:

- Which task types benefit from external exploration?
- When does orchestration overhead outweigh its benefit?
- Does externalization reduce the primary agent's context burden?
- Does it improve repository-level correctness?
- What additional latency or compute does it introduce?
- Which failures are caused by the agent, and which by the harness?

New experiments are recorded as `EXP-XXX` in [docs/research/experiments/](../research/experiments/).
