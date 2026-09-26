# Agent-Workflow Research

This directory documents the research, reasoning, experimentation, and design evolution
behind `agent-workflow`.

It does not archive private conversations or confidential project data. It records the
**generalizable reasoning and evidence** that can be safely shared as part of the
open-source project.

How significant changes must be recorded: [CONTRACT.md](CONTRACT.md).
How the project treats evidence and AI-assisted reasoning: [methodology.md](methodology.md).

## Current thesis and positioning

`agent-workflow` is an external harness, a control plane, for coding agents. It separates
repository-oriented work from the primary agent's reasoning and implementation. Semantic
reasoning stays with the model; deterministic software handles state, policy, contracts,
provenance, and execution control. The secondary agent is an evidence-producing specialist,
not an equally trusted second brain.

Working thesis:

> Coding agents may work more effectively on complex software-engineering tasks when
> repository exploration, evidence acquisition, and workflow control are externalized from
> the primary reasoning context.

The goal is context and responsibility separation, not necessarily less total computation:
externalization moves exploration elsewhere at the cost of extra execution, compute, and
latency ([CASE-003](real-cases/CASE-003-large-refactor-evidence-correction.md)). It is
context *allocation*, not context reduction.

The right label is agent harness with a delegated specialist, not a multi-agent
collaboration system: a primary intelligence, a specialized external worker, and a
deterministic control plane. How responsibility is divided among them, and where the
current division falls short (verification is not independent of exploration), is in
[SYN-002](synthesis/SYN-002-responsibility-allocation.md).

A direction, not a present claim: with its benchmark harness (`bench/`), provider
abstraction, audit trail, and evidence records, the project can also serve as experimental
infrastructure for studying agentic software engineering.

Claims this project does **not** make: that it makes coding agents smarter, that it is a
state-of-the-art coding agent, or that the workflow is better for every task. It is an
engineering exploration of harness architecture, not a proven research result.

Open questions: [questions.md](questions.md). Period summaries: [logs/](logs/).

## Research model

The project evolves through a cycle of observation, question, literature, synthesis,
hypothesis, experiment or implementation, real-world observation, decision, and knowledge,
which in turn raises the next question.

Not every development follows this cycle completely. Some decisions originate from direct
engineering experience, some from literature, and some from synthesis across multiple
sources.

## Evidence sources

Research records may use the following source types:

- `literature` — published papers or other documented research
- `synthesis` — conclusions derived from multiple sources
- `observation` — direct observation from real-world usage
- `experiment` — controlled or semi-controlled evaluation
- `implementation` — evidence produced through actual system implementation
- `discussion` — exploratory reasoning during research
- `unknown` — historical origin cannot be reconstructed reliably

## Provenance

Each research claim or design decision should distinguish its provenance:

- `direct` — directly derived from a known source
- `adapted` — a known source was adapted into the project
- `synthesized` — multiple sources and/or reasoning contributed
- `observed` — primarily derived from real-world observation
- `unknown` — historical origin is no longer known

Unknown provenance is valid. Historical uncertainty must not be replaced with an invented
citation.

## Confidentiality

Private company data, internal project names, source code, credentials, business
information, private transcripts, or other confidential material must not be committed to
this repository.

Real-world observations may be documented only after abstraction and sanitization. The
public record describes the engineering phenomenon, not the confidential environment in
which it occurred.

## Record types

| Directory | ID | Contents |
| --- | --- | --- |
| [literature/](literature/) | `LIT-XXX` | What a source actually claims and how it relates to `agent-workflow` |
| [synthesis/](synthesis/) | `SYN-XXX` | Conclusions produced by combining multiple sources |
| [hypotheses/](hypotheses/) | `H-XXX` | Claims proposed for testing but not yet established |
| [experiments/](experiments/) | `EXP-XXX` | How a hypothesis or design assumption is evaluated |
| [real-cases/](real-cases/) | `CASE-XXX` | Sanitized real-world usage and implementation observations |
| [decisions/](decisions/) | `DEC-XXX` | Architectural or behavioral decisions and their rationale |
| [design-archaeology/](design-archaeology/) | `ARC-XXX` | Historical decisions whose original provenance may be incomplete |

Each directory holds a `_TEMPLATE.md`. Copy it, name the file `<ID>-<short-slug>.md`, and
fill it in.

Two further files tie records together without being records themselves:
[questions.md](questions.md) lists open research questions (`RQ-XX`) with what already
exists for each, and [logs/](logs/) holds fixed summaries of research periods that link to
the records they produced.

## Epistemic status

Research statements use explicit status where relevant:

```text
proposed | observed | implemented | validated | partially_validated | rejected | deprecated | unknown
```

A design being implemented does not mean that its underlying hypothesis has been
validated. Likewise, a literature source being related to a design does not imply that the
source directly caused that design.

## Relationship to other documentation

- `docs/research/` is a human-readable research record. It is **not** read by the runtime.
- `docs/project-knowledge/` is where `promote-write` stores verified operational knowledge,
  and the runtime injects it into exploration and reasoning prompts. Research records never go there
  directly; see "Knowledge promotion" in [CONTRACT.md](CONTRACT.md).
- `prompt/` holds historical release notes and prompt snapshots.
- `bench/` holds the frozen benchmark harness; results are summarized in
  [docs/evaluation/](../evaluation/).

## Principle

The project should preserve not only **what was built**, but also why it was built, what
informed it, what evidence supported it, what happened in practice, and what remained
uncertain.
