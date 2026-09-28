# Open research questions

Each question is a separate research program; none is a roadmap commitment. No
implementation starts from a question alone: it goes through the cycle in
[CONTRACT.md](CONTRACT.md) (question, literature, synthesis, hypothesis, experiment,
decision).

"Status" describes what exists in the repository today, not whether the question is
answered.

| ID | Question | What exists today | Status | Records |
| --- | --- | --- | --- | --- |
| RQ-01 | When does external repository exploration provide net engineering value? | Usage telemetry; the 3-arm harness was removed in 3.7.3 without results (DEC-011); no net-value measure | proposed | CASE-003 (draft), [evaluation](../evaluation/README.md) |
| RQ-02 | Does objective-level delegation reduce human orchestration overhead? | No human-attention instrumentation | proposed | H-002 (draft) |
| RQ-03 | How much of the perceived benefit comes from the workflow versus the prompt abstraction? | Benchmark has no prompt-strategy factor | proposed | EXP-001 (draft) |
| RQ-04 | Can task complexity predict when workflow activation is worthwhile? | `scope_width` and fan-out computed per call but only in transient result metadata, not persisted; no routing on them | proposed | H-001 (draft) |
| RQ-05 | Can delegated latency become parallel human work instead of blocking time? | Runtime jobs are asynchronous and the runner starts as a background task; the primary agent still waits for the digest before reasoning | partial | EXP-002 (draft), CASE-002 (draft), [CASE-004](real-cases/CASE-004-maintainer-telemetry-snapshot.yaml) |
| RQ-06 | Does persistent repository knowledge reduce repeated exploration and change later behavior? | Fact store (recurrence and durable facts), anchored evidence reuse, graph leads, promoted knowledge; facts and promoted knowledge are injected into exploration and reasoning prompts, but there is no outcome → update loop and the effect is not measured | partial | H-004 (draft), [CASE-004](real-cases/CASE-004-maintainer-telemetry-snapshot.yaml) |
| RQ-07a | Can recorded trajectories support post-hoc failure diagnosis? | Audit, usage, quality, per-call metadata, progress, job logs, and continuation metadata exist, but are scattered and have no diagnosis tooling | partial | H-005 (draft) |
| RQ-07b | Can a workflow be replayed to validate a candidate intervention? | No general replay; browser verification is deliberately never re-run | proposed | H-006 (draft) |
| RQ-08 | Can an adaptive harness improve future coding-agent behavior without changing model weights? | Stateful orchestration and memory (facts) exist; experience does not — see below | proposed | SYN-002 (draft) |
| RQ-09 | Which delegation mode fits a task: direct, assisted, or autonomous? | Direct and assisted exist; `/.execute` always requires explicit approval, so autonomous does not | proposed | SYN-001 (draft), [team guide](../team-guide/when-to-use.md) |
| RQ-10 | How should responsibility be allocated across the human, the agents, and the runtime? | An allocation exists (explore, decompose, decide, execute, verify, remember) but has not been evaluated | partial | SYN-002 (draft), DEC-002 (draft) |
| RQ-11 | Should the verifier be independent of the explorer? | Delegated verify uses the configured secondary agent, which is the one that explored unless the provider is switched; the runtime checks only the verdict's shape; `syntax` mode proves only that files parse | proposed | SYN-002 (draft), DEC-003 (draft), H-007 (draft), EXP-003 (draft) |
| RQ-12 | Does exploring and planning before implementation keep a mature repository's architecture intact (less duplication, unchosen coupling, drift) compared with locally verified iteration? | `explore` and `plan` precede `/.execute`; no drift measure exists | proposed | H-003 (draft) |

RQ-06: knowledge that never changes later behavior is only an archive. The question is not
whether knowledge is stored but whether it is operationalized. A first measurable signal
already exists: usage rows record evidence reuse and avoided provider calls, so their rate
can be read before any new mechanism is proposed. In the maintainer's telemetry from
2026-08-15 to 2026-09-27 it read zero ([CASE-004](real-cases/CASE-004-maintainer-telemetry-snapshot.yaml)).

RQ-07a: trajectory data (task, routing, exploration, evidence, plan, execution, failure,
verification, intervention, outcome) accumulates only through real use, which makes it
valuable for research. Only part of it is recorded today: the runtime keeps routing,
evidence, verification verdicts, and continuation and recovery events, but not the plan,
the execution, human interventions, or the task outcome
([CASE-004](real-cases/CASE-004-maintainer-telemetry-snapshot.yaml)). What is recorded is stored
in each user's own `.workflow/` and contains their code and prompts. Any collection across users requires their consent and sanitization under
[CONTRACT.md](CONTRACT.md) §5; nothing in the runtime collects it.

RQ-08: **stateful orchestration is not the same as learning from experience**, and memory is
not experience:

```text
memory      "The repository follows pattern X."
experience  "On task X, strategy Y was tried and failed because of Z; strategy W worked."
```

Experience carries state, action, context, outcome, and a causal interpretation. The fact
store holds memory; no representation of experience exists yet.
