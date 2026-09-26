# SYN-002: Responsibility allocation across a coding-agent system

## Metadata

```yaml
id: SYN-002
title: Autonomy is allocated per responsibility, and the boundaries are the design
status: draft
```

## Question

How should responsibility be divided between the human, the primary agent, the secondary
agent, and the runtime — and how is it divided in agent-workflow today?

## Sources

- The repository itself: the allocation table below is derived from the code, not from
  discussion ([architecture](../../architecture/README.md), [reference](../../reference.md)).
- [SYN-001](SYN-001-procedural-prompting-and-adoption-loop.md) — how developers delegate
- [CASE-003](../real-cases/CASE-003-large-refactor-evidence-correction.md) — the primary
  agent correcting the secondary agent
- AI-assisted discussion with the maintainer (research input, not evidence)

## Shared ideas

"How autonomous should the agent be?" treats autonomy as one dial:

```text
manual ←──────────────────→ autonomous
```

The sources instead point to autonomy being **allocated per responsibility**: some parts of
the work can be handed over entirely, others must stay with the human, and the useful
question is which is which.

## Differences

SYN-001 looks at the human side (why developers keep decomposing tasks themselves). This
record looks at the system side (what each component is responsible for).

## Synthesis

*Interpretation, not a claim of any individual source.*

**The boundaries are the design.** What distinguishes agent-workflow is not the secondary
agent but where the lines are drawn: semantic reasoning stays with a model, repository
search goes to a specialist, and state, policy, provenance, and contract checks go to
deterministic software. The underlying question is *what an LLM should control and what
software should control*, which is more fundamental than which model is used.

This is also why "multi-agent system" is the wrong label. The project is not peers
reasoning together; it is a primary intelligence, a specialized external worker, and a
deterministic control plane: an **agent harness** with a **delegated specialist**.

**Current allocation, derived from the repository:**

| Responsibility | Who holds it today | Basis |
| --- | --- | --- |
| Explore the repository | Secondary agent | `explore`, `analyze` |
| Decompose the task | Secondary agent gathers evidence; primary agent reasons the plan | `plan` |
| Decide | **Human**: answers open questions, picks an option, approves with `-y` | plan gate; `/.execute -y` |
| Execute (write code) | Primary agent | `/.execute` has no runtime path |
| Verify | Secondary agent (`verify_mode: delegated`), or the runtime (`syntax`: parse checks only) | `verify` |
| Remember | Runtime (facts, evidence reuse); human-gated for promoted knowledge | fact store; `promote-write` |
| Learn | **No one** | [RQ-08](../questions.md) |

**Correction: verification is not independent.** It is tempting to describe verification as
a deterministic or independent component. In agent-workflow it is not. Delegated
verification is performed by the configured secondary agent — with an unchanged provider, the
same one that explored the repository, often in the same session thread (threads are kept
per provider, so switching provider changes the agent but not the method); the
runtime checks only the *shape* of its verdict (required fields, tags, anchors), not whether
the verdict is true. `syntax` mode is deterministic but proves only that files parse. An
error the secondary agent makes while exploring can therefore survive its own
verification. CASE-003 shows the primary agent catching such errors; nothing in the system
guarantees that it will.

## Implication for agent-workflow

- Documentation should describe verification as "a verdict with evidence from the secondary
  agent", not as an independent check.
- Each future change can be described as moving one row of the table, which makes it
  reviewable: "this change moves *decompose* further toward the agent", or "this change
  makes *verify* independent of *explore*".
- The empty row (*learn*) is where the adaptive-harness questions live.

## Hypotheses generated

None yet. Questions: [RQ-10, RQ-11](../questions.md).

## Confidence

`low` for the synthesis; the allocation table itself reflects the code as of v3.7.2.

## Open questions

- Should the verifier be independent of the explorer (a different agent, provider, or
  method)? ([RQ-11](../questions.md))
- Who, if anyone, should hold *learn*? ([RQ-08](../questions.md))
- Does the unit of delegable work grow — instruction, task, objective, engineering
  outcome — as agents improve? Speculative; no evidence in this project.
