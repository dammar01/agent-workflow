# Agent-Workflow Research

This directory documents the research, reasoning, experimentation, and design evolution
behind `agent-workflow`.

It does not archive private conversations or confidential project data. It records the
**generalizable reasoning and evidence** that can be safely shared as part of the
open-source project.

How significant changes must be recorded: [CONTRACT.md](CONTRACT.md).
How the project treats evidence and AI-assisted reasoning: [methodology.md](methodology.md).

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
  and the runtime injects it into delegated prompts. Research records never go there
  directly; see "Knowledge promotion" in [CONTRACT.md](CONTRACT.md).
- `prompt/` holds historical release notes and prompt snapshots.
- `bench/` holds the frozen benchmark harness; results are summarized in
  [docs/evaluation/](../evaluation/).

## Principle

The project should preserve not only **what was built**, but also why it was built, what
informed it, what evidence supported it, what happened in practice, and what remained
uncertain.
