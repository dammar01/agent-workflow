# Agent-Workflow Research

This directory documents the research, reasoning, experimentation, and design evolution
behind `agent-workflow`.

It does not archive private conversations or confidential project data. It records the
**generalizable reasoning and evidence** that can be safely shared as part of the
open-source project.

How significant changes must be recorded: [CONTRACT.md](CONTRACT.md). The record format:
[schema.yaml](schema.yaml), checked by `tools/maintain/check_research.py`.
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
(maintainer latency: CASE-004, a draft). It
is context *allocation*, not context reduction.

The right label is agent harness with a delegated specialist, not a multi-agent
collaboration system: a primary intelligence, a specialized external worker, and a
deterministic control plane. How responsibility should be divided among them is still a
draft (SYN-002, see "Drafts" below).

A direction, not a present claim: with its provider abstraction, audit trail, evidence
records, and experiment records (`EXP-XXX`, where benchmark method now lives), the project can
also serve as experimental infrastructure for studying agentic software engineering.

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
| [questions/](questions/) | `RQ-XX` | Research questions, what exists for each today, and the records serving them |

Every record is YAML. Each directory holds a `_TEMPLATE.yaml`: copy it, name the file
`<ID>-<kebab-slug>.yaml`, fill it in, and run `python tools/maintain/check_research.py`. The
fields, their enums, and the rules the check enforces are in [schema.yaml](schema.yaml) and
[CONTRACT.md](CONTRACT.md) §16. `H` and `EXP` records are external to the code and carry no
commit; an implemented `DEC` names the commit(s) that implement it. A record at schema
version 2 names the research question(s) it serves under `questions`, and the `RQ` lists it
back (DEC-017); records tracked at version 1 are frozen without it.

[questions.md](questions.md) is the index of the `RQ` records, generated like the inventory
below, and [logs/](logs/) holds fixed summaries of research periods that link to the records
they produced.

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

Generated from the records by `python tools/maintain/check_research.py --write-inventory`;
the check fails when it is stale. Research questions per record: [questions.md](questions.md).

<!-- research-inventory:begin (generated by tools/maintain/check_research.py --write-inventory) -->
| ID | Title | Type status | Implementation | Validation | Commit |
| --- | --- | --- | --- | --- | --- |
| [CASE-005](real-cases/CASE-005-verify-browser-drafts-never-ready.yaml) | `/.verify-browser` drafts never reached `ready` | negative | implemented | observed | — |
| [CASE-006](real-cases/CASE-006-latency-and-cached-input-in-maintainer-use.yaml) | Latency by project and cached input share in maintainer use | inconclusive | implemented | observed | — |
| [CASE-007](real-cases/CASE-007-accumulating-one-byte-lock-guards.yaml) | One-byte lock guards accumulate and look like a defect | mixed | implemented | observed | — |
| [CASE-008](real-cases/CASE-008-graph-refresh-hook-cost-on-two-projects.yaml) | On two projects whose source differs sevenfold, the graph-refresh hook scan costs about 2 s on each, and graphify update fails slowly on the large graph | mixed | implemented | observed | — |
| [CASE-009](real-cases/CASE-009-verify-rarely-passes-and-evidence-is-never-reused.yaml) | Across fourteen projects, 2% of verify rows pass and no command is served from reused evidence | negative | implemented | observed | — |
| [DEC-001](decisions/DEC-001-verify-browser-delegated.yaml) | `/.verify-browser` is a delegated command | accepted | implemented | validated | dd70afd |
| [DEC-004](decisions/DEC-004-data-e2e-only-test-attribute.yaml) | `data-e2e` is the only test attribute | accepted | implemented | observed | dd70afd |
| [DEC-005](decisions/DEC-005-repeat-brake-releases-on-change.yaml) | The repeat brake releases on a project change and is remembered as knowledge | accepted | implemented | observed | dd70afd |
| [DEC-006](decisions/DEC-006-stage-one-repair-continuation.yaml) | A stage-1 spec that fails validation gets one repair in the same thread | accepted | implemented | observed | dd70afd |
| [DEC-007](decisions/DEC-007-poll-waits-inside-playwright.yaml) | The browser player's poll waits inside Playwright, not beside it | accepted | implemented | validated | dd70afd |
| [DEC-008](decisions/DEC-008-app-repeat-streak-keyed-by-failure-signature.yaml) | An app repeat streak is keyed by failure signature, and a runtime change releases it | accepted | implemented | observed | dd70afd |
| [DEC-009](decisions/DEC-009-literal-credentials-scrubbed-by-lookup.yaml) | Literal credentials in the task, draft and request are found by value and put back to placeholders | accepted | implemented | observed | c584748 |
| [DEC-010](decisions/DEC-010-scenario-origin-and-structured-diagnosis.yaml) | Scenario-caused failures get their own origin, stay out of the repeat brake, and every non-pass carries a structured diagnosis | accepted | implemented | validated | c584748 |
| [DEC-011](decisions/DEC-011-research-records-yaml-changelog-split-bench-removed.yaml) | Research records are schema-checked YAML, release notes live per version, and bench/ is retired | accepted | implemented | validated | 665bb4c, c584748 |
| [DEC-012](decisions/DEC-012-graph-refresh-hook-ownership-and-timing.yaml) | The Stop hook is the only graph-refresh trigger, prunes before it walks, and records its cost with project size | accepted | implemented | observed | 98449fe |
| [DEC-017](decisions/DEC-017-research-questions-as-yaml-records.yaml) | Research questions become YAML records, linked to records both ways | accepted | implemented | observed | 7150b0d |
<!-- research-inventory:end -->

No `H-XXX`, `EXP-XXX`, or `SYN-XXX` record is tracked: none has been run or has a result.

### Drafts

Proposed hypotheses, planned experiments, syntheses, and decisions without a result are
kept in `docs/research-drafts/` (same subfolders). That directory is git-ignored and exists
only on the maintainer's machine; tracked records mention draft IDs as plain text, never as
links. A draft moves back here only when it has been carried out and has a recorded result.
Drafts currently held: DEC-012..DEC-016, H-004 and H-008 (the v3.8.0 work, each naming its
research question), CASE-004 (moved back on 2026-09-28: the records it relates to are all
drafts), and H-005, EXP-002, CASE-002, SYN-002, DEC-002 (Markdown, still referenced).
H-001..H-003, H-006, H-007, EXP-001, EXP-003, CASE-001, CASE-003, SYN-001 and DEC-003 are
kept in `docs/research-drafts/_archive/`, which the check does not read. Drafts use the
same YAML format; the Markdown ones are listed by `check_research.py --drafts` until
converted. A draft returns here only as a YAML record that passes the check.

No `LIT-XXX` or `ARC-XXX` record exists yet. For literature this is deliberate: papers
remembered during research were not recorded because their metadata was not available
([logs/2026-09-26.md](logs/2026-09-26.md), "Deliberately not recorded"). No historical
decision has been reconstructed as an `ARC` record so far.

## Relationship to other documentation

- `docs/research/` is a human-readable research record. It is **not** read by the runtime.
- `docs/project-knowledge/` is where `promote-write` stores verified operational knowledge,
  and the runtime injects it into exploration and reasoning prompts. Research records never go there
  directly; see "Knowledge promotion" in [CONTRACT.md](CONTRACT.md).
- `prompt/` holds historical release notes and prompt snapshots up to v3.7.2; later release
  notes are in [docs/releases/](../releases/).
- Benchmark method is recorded as `H-XXX` + `EXP-XXX` records. The earlier frozen harness
  (`bench/`, SUT v3.4.5) was removed in 3.7.3 (DEC-011) without having produced results;
  see [docs/evaluation/benchmark.md](../evaluation/benchmark.md).

## Principle

The project should preserve not only **what was built**, but also why it was built, what
informed it, what evidence supported it, what happened in practice, and what remained
uncertain.
