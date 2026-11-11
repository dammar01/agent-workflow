# Evaluation

Evidence about Exoma and its historical `agent-workflow` releases, with the scope and
limitations needed to interpret it. Start with the real-use report and its metric definitions;
use the research inventory for record status. Controlled plans and observations have
separate boundaries:

| Document | Kind | What it can support |
| --- | --- | --- |
| [benchmark.md](benchmark.md) | Controlled repository-level benchmark plans (`H` + `EXP`); the earlier `bench/` harness was removed in 3.7.3 without results | No controlled repository-level benchmark result yet; bounded launcher/retrieval results are indexed in the research records |
| [real-use-benchmark.md](real-use-benchmark.md) | Real coding tasks from one developer's sessions on nine private projects, graded by what reached `main` | Acceptance, later edits, prompts, timestamp-derived session activity, final context and a token-difference proxy; no causal effect or measured human-time saving |
| [observed-usage.md](observed-usage.md) | Uncontrolled telemetry from one machine | Recorded delegate and digest payload sizes; historical and current badge formulas are distinguished |
| [metrics.md](metrics.md) | The calculation contract: every figure's ID, version, method, denominator and missing-data rule | How a figure on the other pages was computed, and whether two figures measure the same thing |
| [Exoma regression handoff](exoma-handoff-2026-11-10/README.md) | Historical installed-source probes, portable fixtures and a sanitized current-pin source receipt (DEC-086) | Reported P01-P12/G01-G04 16/16 and named native suite outcomes on `a965cf20`; no current-pin provider E2E, deployed-hook verification or real-task effectiveness established |
| [Research inventory](../research/README.md#record-inventory) and [questions](../research/questions.md) | Public record dispositions, accepted bounded results and unanswered questions | Experiment outcome, implementation validation and RQ answers remain separate |

## Current evidence boundary

The public reports describe one maintainer's historical use and several bounded fixture,
launcher and retrieval studies. They do not establish causal workflow effectiveness,
an optimal delegation count or current deployment behavior. Accepted bounded results
retain the sample, rubric and platform limits recorded in the research inventory.

Detailed campaign reviews, calibration preparation and administrative completion reports
are local research drafts. Private transcripts, source captures and per-event material
are required to reproduce some recounts; the public checkout contains their sanitized
findings and method descriptions, not those inputs. Moving supporting material to local
drafts does not change a record's outcome or scientific validation.

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
particular) have no instrumentation yet. Today `exoma --command report` gives aggregate
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
