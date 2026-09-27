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
externalization moves exploration elsewhere at the cost of extra execution and latency
(maintainer latency: [CASE-004](real-cases/CASE-004-maintainer-telemetry-snapshot.md)). It
is context *allocation*, not context reduction.

The right label is agent harness with a delegated specialist, not a multi-agent
collaboration system: a primary intelligence, a specialized external worker, and a
deterministic control plane. How responsibility should be divided among them is still a
draft (SYN-002, see "Drafts" below).

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

Every record states two dimensions separately in its `disposition` block, next to its
type-specific `status` or `outcome`:

```text
implementation_status   not_implemented | partially_implemented | implemented | not_applicable
validation_status       not_validated | observed | partially_validated | validated | rejected
```

Only the maintainer marks a record validated or rejected. The fields, their values, and when the corpus counts as final:
[CONTRACT.md](CONTRACT.md) §14.

A design being implemented does not mean that its underlying hypothesis has been
validated. Likewise, a literature source being related to a design does not imply that the
source directly caused that design.

## Record inventory

Only records that describe something that actually happened stay here. Everything else is
a draft (see below).

| ID | Title | Type status | Implementation | Validation | RQ |
| --- | --- | --- | --- | --- | --- |
| [CASE-004](real-cases/CASE-004-maintainer-telemetry-snapshot.md) | Maintainer telemetry snapshot | inconclusive | implemented | observed | RQ-05, RQ-06 |
| [CASE-005](real-cases/CASE-005-verify-browser-drafts-never-ready.md) | `/.verify-browser` drafts never reached `ready` | negative | implemented | observed | — |
| [DEC-001](decisions/DEC-001-verify-browser-delegated.md) | `/.verify-browser` is a delegated command | accepted | implemented | not_validated | — |
| [DEC-004](decisions/DEC-004-data-e2e-only-test-attribute.md) | `data-e2e` is the only test attribute | accepted | implemented | not_validated | — |
| [DEC-005](decisions/DEC-005-repeat-brake-releases-on-change.md) | The repeat brake releases on a project change and is remembered as knowledge | accepted | implemented | not_validated | — |
| [DEC-006](decisions/DEC-006-stage-one-repair-continuation.md) | A stage-1 spec that fails validation gets one repair in the same thread | accepted | implemented | not_validated | — |
| [DEC-007](decisions/DEC-007-poll-waits-inside-playwright.md) | The browser player's poll waits inside Playwright, not beside it | accepted | implemented | not_validated | — |
| [DEC-008](decisions/DEC-008-app-repeat-streak-keyed-by-failure-signature.md) | An app repeat streak is keyed by failure signature, and a runtime change releases it | accepted | implemented | not_validated | — |

No `H-XXX`, `EXP-XXX`, or `SYN-XXX` record is tracked: none has been run or has a result.

### Drafts

Proposed hypotheses, planned experiments, syntheses, and decisions without a result are
kept in `docs/research-drafts/` (same subfolders). That directory is git-ignored and exists
only on the maintainer's machine; tracked records mention draft IDs as plain text, never as
links. A draft moves back here only when it has been carried out and has a recorded result.
Drafts currently held: H-001..H-007, EXP-001..EXP-003, SYN-001..SYN-002, DEC-002, DEC-003,
and CASE-001..CASE-003 (real observations without a kept artifact, so they cannot be
re-checked).

No `LIT-XXX` or `ARC-XXX` record exists yet. For literature this is deliberate: papers
remembered during research were not recorded because their metadata was not available
([logs/2026-09-26.md](logs/2026-09-26.md), "Deliberately not recorded"). No historical
decision has been reconstructed as an `ARC` record so far.

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
