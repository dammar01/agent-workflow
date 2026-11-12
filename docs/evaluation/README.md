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
| [Exoma 38fb1df staging](exoma-staging-2026-11-12/README.md) | Exact-pin technical staging on 2026-11-12 with a sanitized Codex receipt | Seven default suites, research checks, candidate bundle integrity, green Exoma CI and real-provider explore/sweep; installed-host drift remains unresolved |
| [Exoma regression handoff](exoma-handoff-2026-11-10/README.md) | Historical installed-source probes, portable fixtures and a sanitized source receipt (DEC-086) | Reported P01-P12/G01-G04 16/16 and named native suite outcomes on `a965cf20`; no provider E2E, deployed-hook verification or real-task effectiveness established by that historical receipt |
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

## Engineering readiness checkpoint (2026-11-11)

[DEC-087](../research/decisions/DEC-087-engineering-readiness-keeps-technical-staging-separate-from-research-validation.yaml)
records the local staging and documentation changes against Exoma
`a965cf20bce03015cdf0f56952eb6c45e30cbfa4`. This checkpoint separates implementation,
technical evidence and research conclusions:

This table records the initial implementation checkpoint. Subsequent verification
results are tracked separately by the runtime; the table does not assert a current verdict.

| Surface | Known / implemented | Evidence and remaining boundary | Actionable follow-up |
| --- | --- | --- | --- |
| Staging acceptance | Required stages, all-model preflight, process/payload checks, pin identity and configurable deadlines | Quota-free fixtures cover refusals and successful orchestration; metadata is not byte-level source proof | Re-run default suites when harness or pin changes |
| Local regression | Seven default suites, including isolated `e2e` tests | `uv run python tests/run.py --keep-going` returned exit 0 locally on Windows, 2026-11-11; no remote CI conclusion inferred | Run the same checks on supported CI platforms |
| Runtime verification | Separate from this initial implementation checkpoint | Local test evidence does not replace runtime review and its verdict | Consult the latest runtime verification result |
| Real-provider acceptance | Harness available; current-pin provider receipt not established | Offline fake dispatch does not exercise a real provider | Use an explicit available provider/model and retain its JSON receipt; see [staging](../../RELEASE.md) |
| Deployed host | Hook loading/trust and real approval lifecycle not verified here | Provider CLI and policy fixtures are different evidence layers | Observe a genuine scratch-host session using [separate host acceptance](../../RELEASE.md#separate-host-acceptance) |
| Research | Historical accepted closures and open questions retained | No new causal workflow benefit, generalisation or validation disposition established | Choose targeted evaluation when a feature's benefit or risk needs it |

During local testing the calling runtime's `PYTHONPATH` exposed its global Exoma package
despite the project interpreter. The exact-pin guard rejected that identity. The test
entry point and isolated regression child now use the project's interpreter installation;
canonical provider commands also use `-I`. This does not waive wrong-pin refusal.

Follow-up verification identified that parent isolation alone did not isolate CLI
children. Each child CLI now also uses `-I`. A real local-init regression with a synthetic
shadow Exoma on inherited `PYTHONPATH` checks child isolation and the generated runner's
package/interpreter binding without calling a provider. Stdout JSON and stderr diagnostics
are kept separate so benign warnings do not invalidate init/provider payloads.

Practical improvements may proceed directly, or research may precede implementation when
uncertainty warrants it. The research backlog is not automatically a product-development
backlog. See [the development relationship](../research/CHARTER.md#development-and-research-relationship).
Outcomes above do not raise any historical RQ/DEC status.

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
