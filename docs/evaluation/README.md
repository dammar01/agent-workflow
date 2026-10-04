# Evaluation

How well `agent-workflow` works, and under which conditions. Two kinds of material live
here, and they must not be read as the same kind of evidence:

| Document | Kind | What it can support |
| --- | --- | --- |
| [benchmark.md](benchmark.md) | Where controlled studies are recorded (`H` + `EXP`); the earlier `bench/` harness was removed in 3.7.3 without results | Nothing yet: no controlled study has a result |
| [real-use-benchmark.md](real-use-benchmark.md) | Real coding tasks from one developer's sessions on nine private projects, graded by what reached `main` | How often real work was accepted and fixed, and what it cost in prompts, time, and context; not that the workflow caused it |
| [observed-usage.md](observed-usage.md) | Uncontrolled telemetry from one machine | How much text the digest contract kept out of the primary context in practice |

## Net value

The evaluation question is not "does the workflow work?" but **"under what conditions does
it provide net value?"** Conceptually:

```text
net value = engineering benefit
          − latency cost
          − compute cost
          − human attention cost
          − complexity cost
          − failure risk
```

The target is therefore neither minimum latency nor maximum autonomy, but **the minimum total
human cost for an acceptable engineering outcome**. More autonomy (explore, plan, verify
before returning) lengthens the wait; more procedural use shortens each wait but raises the
number of human interactions.

This is a framing, not a measured formula: several of its terms (human attention in
particular) have no instrumentation yet. Today `main.py --command report` gives aggregate
durations, reuse, and acceptance. How often a human prompted and corrected, and whether the
work reached `main`, is counted after the fact from transcripts and Git
([real-use-benchmark.md](real-use-benchmark.md)); what the agents read and how often
exploration repeated is not observed per task.

Model benchmarks alone cannot evaluate a system like this: outcomes depend on the
interaction of human behavior, agent behavior, harness behavior, and the repository. See
RQ-02 and RQ-03 in [research/questions.md](../research/questions.md).

## Open questions

Open questions the evaluation is meant to answer (the full list with status is in
[research/questions.md](../research/questions.md)):

- Which task types benefit from external exploration?
- When does orchestration overhead outweigh its benefit?
- Does externalization reduce the primary agent's context burden?
- Does it improve repository-level correctness?
- What additional latency or compute does it introduce?
- Which failures are caused by the agent, and which by the harness?

New experiments are drafted locally as `EXP-XXX` and tracked in
[docs/research/experiments/](../research/experiments/) once they have run and have a result
([CONTRACT.md](../research/CONTRACT.md) §15).
