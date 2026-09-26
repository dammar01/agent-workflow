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
delegated prompts by the runtime. Promote a claim there only when it is sufficiently
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

## 12. Completion rule

A significant feature is not considered fully documented until a future maintainer can
answer:

1. Why was this introduced?
2. What informed the decision?
3. What was actually implemented?
4. What evidence supports it?
5. What are its limitations?
6. Under what conditions should it be reconsidered?
