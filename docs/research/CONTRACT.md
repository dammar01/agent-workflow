# Development & Research Contract

This contract defines how significant changes to `agent-workflow` must be documented.

The goal is to preserve the reasoning and evidence behind project evolution without
requiring private conversation logs or confidential project data.

## 1. Scope

A research record is required for changes that affect:

- runtime architecture,
- agent orchestration,
- routing,
- provider behavior,
- evidence handling,
- facts or knowledge systems,
- graph behavior,
- verification,
- safety or policy,
- prompt contracts,
- task/context management,
- recovery behavior,
- performance characteristics,
- or other behavior that changes the project's core design.

Trivial changes such as formatting, typo fixes, documentation wording, or isolated
maintenance do not require a full research record.

## 2. Minimum record

Every significant change must have at least one of `DEC-XXX`, `H-XXX`, `EXP-XXX`, or
`CASE-XXX`.

The record must explain the problem, the decision or hypothesis, the reasoning, the
evidence, the implementation reference, the validation status, and the limitations.

## 3. Literature

When literature materially informs a change, record the source in
[`literature/`](literature/).

Do not claim that a paper caused an implementation unless that relationship is known.
Multiple papers may be represented through a [synthesis](synthesis/) record.

## 4. AI-assisted discussion

AI discussion may contribute to exploration, synthesis, hypothesis generation, and
implementation reasoning.

Private transcripts must not be committed to the repository when they contain confidential
information. AI-generated claims must not be treated as validated evidence by default.

## 5. Confidential information

Never commit private conversations, confidential source code, credentials, internal URLs,
customer information, business-sensitive metrics, proprietary architecture details, or
identifiable company information. Use sanitized abstractions instead.

## 6. Provenance

Every significant design claim should declare its provenance where known:
`direct`, `adapted`, `synthesized`, `observed`, or `unknown`.

`unknown` is an acceptable historical state.

## 7. Validation

Implementation does not equal validation. Each record distinguishes `implemented`,
`observed`, and `validated`. A feature may be implemented while the hypothesis behind it
remains unvalidated.

## 8. Negative results

Failures, regressions, unexpected behavior, rejected hypotheses, and failed approaches
should be documented when they provide useful engineering or research information. Do not
document only successful outcomes.

## 9. Knowledge promotion

Research observations must not automatically become operational knowledge.

Operational knowledge lives in `docs/project-knowledge/`, is written only through the
`promote-validate` → `promote-verify` → `promote-write` stages, and is injected into
exploration and reasoning prompts by the runtime. Promote a claim there only when it is sufficiently
supported and useful for future runtime behavior, and keep a reference to the originating
research record whenever possible.

## 10. Historical reconstruction

Historical records may be reconstructed from source code, git history, release history
(`CHANGELOG.md`, `prompt/`), existing documentation, and remembered observations.

Reconstructed history must be marked as reconstructed when certainty is limited. Missing
provenance must not be fabricated.

## 11. Default development flow

For significant changes the default sequence is: problem, research or literature (when
relevant), synthesis (when multiple sources are involved), hypothesis or decision,
implementation, validation, observation or experiment result, and finally an update to the
record. Not every change requires every stage.

## 12. New mechanisms

The runtime already has many subsystems, and infrastructure can grow faster than
capability. A harness that becomes too complex becomes the engineering problem itself.

Before adding a subsystem, mechanism, or record type, the change record answers:

1. What problem does this solve?
2. What measurable benefit is expected?
3. What complexity does it add?
4. Can an existing mechanism solve the same problem?

"Build first, justify later" is not accepted for significant changes.

## 13. Completion rule

A significant feature is not considered fully documented until a future maintainer can
answer:

1. Why was this introduced?
2. What informed the decision?
3. What was actually implemented?
4. What evidence supports it?
5. What are its limitations?
6. Under what conditions should it be reconsidered?

## 14. Disposition and validation authority

Every record carries a `disposition` block in its metadata, next to its type-specific
`status` or `outcome`. The two answer different questions: `status` says where the record is
in its own life cycle (a hypothesis is `proposed`, an experiment `planned`); `disposition`
says what exists and what has been shown, using the distinction in §7.

```yaml
disposition:
  implementation_status:  # not_implemented | partially_implemented | implemented | not_applicable
  validation_status:      # not_validated | observed | partially_validated | validated | rejected
  validated_by:
  validated_on:
```

- `observed` means there is real-use evidence at some tier in
  [methodology.md](methodology.md) "Evidence tiers", but no designed evaluation.
- `validated`, `partially_validated`, and `rejected` require an evaluation the record names
  (usually an `EXP-XXX`) and a result measured against the record's stated success criteria.
- Exception for decisions (`DEC-XXX`) only: a DEC is `validated` when the maintainer's
  direct use of the decided behavior runs clean. All three conditions must hold:
  1. The runtime verdict is `pass`, or the equivalent success outcome for that behavior.
  2. The run needed no manual workaround: no `ignore_repeat_brake`, no hand-edited request
     or scenario, and no runtime patch mid-run.
  3. The maintainer reported no issue with that run.

  The record's Validation section names the run: its date, its job id, and a sanitised
  description of what was exercised. Any condition missing makes it `observed`, not
  `validated`. This is `creator_observation` evidence (see methodology.md "Evidence
  tiers"). It shows the decision works in the maintainer's use, not that it generalises.
  Hypotheses (`H-XXX`) still require an `EXP-XXX`.
- Only the maintainer sets `validated_by` and `validated_on`. Setting any value other than
  `not_validated` or `observed` without them is incomplete.
- Every hypothesis states its success criteria: the primary metric, what counts as
  `supported` and as `rejected`, and whether the data is recorded today. A threshold not
  chosen yet is written `[PLACEHOLDER]`, never invented.

The research corpus counts as final when every record has a complete disposition, every
hypothesis has success criteria, and every research question in
[questions.md](questions.md) links at least one record. Final does not mean answered: a
record may be final and still `not_validated`.

## 15. Tracked records and drafts

A record is tracked in `docs/research/` only when it describes something that was carried
out and has a recorded result: an observation with its source stated, a decision
implemented in code, an experiment that ran. Figures in a tracked record come from a named
source that can be re-counted (for example a named telemetry file and line range); a figure
without one is removed, not rounded.

Proposed hypotheses, planned experiments, syntheses, and decisions without implementation
are drafts. Drafts live in `docs/research-drafts/` with the same subfolders. That directory
is git-ignored: it is the maintainer's local working area, not part of a release. Tracked
records refer to a draft by its ID in plain text, never by a link. A draft returns to
`docs/research/` when it has been carried out and its result is recorded in it.

Tracked records are frozen as of 3.7.3: their content changes only to add a new result or
to correct a factual error against its source, never to add untested reasoning.
