# DEC-006: A stage-1 spec that fails validation gets one repair in the same thread

## Metadata

```yaml
id: DEC-006
date: 2026-09-27
status: accepted
provenance:
  type: observed      # CASE-005: no draft ever reached ready
  confidence: high
disposition:        # see CONTRACT.md §14
  implementation_status: implemented
  validation_status: not_validated
  validated_by: maintainer
  validated_on: 2026-09-27
```

## Problem

`/.verify-browser` stage 1 asked the secondary agent for an `[E2E SPEC]` section. Only a
MISSING section earned a follow-up; a section that arrived with a broken scenario (no fence,
invalid JSON, a value outside an enum, an unknown selector key, an ungrounded claim) ended
the draft as `spec_invalid` on that attempt. In CASE-005 no draft ever reached `ready`.

## Decision

A section that arrived but did not validate — parse errors and validation errors alike —
gets exactly one repair in the same provider thread (`spec.spec_repair_prompt`), quoting
the validator's messages (at most `MAX_REPAIR_ERRORS`) and asking for the complete corrected
section. The repair reply replaces the first section and is validated from scratch; still
invalid, the draft is `invalid`. `meta.e2e.repair` records `{attempted, errors, recovered}`;
without a `provider_session_id` nothing is asked and it says `no_provider_session`. The draft
prompt also names the allowed `side_effect` values and the fence rule.

## Alternatives considered

- **Prompt changes only.** Rejected: the model still produces the shapes in CASE-005, and the
  runtime still would not let it fix them.
- **Unbounded or repeated repair.** Rejected: a second failure after the validator's own
  words is the model's answer, and each repair is a paid provider call.
- **A fresh call instead of the thread.** Rejected: it would re-explore the repository from
  nothing.

## Reasoning

The validator already says exactly what is wrong. The agent that wrote the spec still holds
the evidence in its thread, so one targeted repair is the cheapest way from an almost-right
spec to a usable one.

## Supporting evidence

- [CASE-005](../real-cases/CASE-005-verify-browser-drafts-never-ready.md)

## Implementation

```yaml
version: 3.7.3
commit: unknown   # not committed when this record was written
components:
  - core/evidence/e2e/spec.py (spec_gap repairable, spec_repair_prompt)
  - core/evidence/e2e/runner.py (_draft repair)
  - core/prompt/prompt_builder.py (side_effect enum, fence rule)
  - tests/checks/{e2e_spec,e2e_routing}.py
```

## Validation

`not_validated`. The routing suite shows a lost fence and a retired selector key repaired to
`ready`, and a repair that did not help ending the draft after exactly one extra call, with a
fake provider.

First real use (maintainer's machine, one draft, job `job_20260927_145604_584464`, same
private project as CASE-005): the repair fired (`meta.e2e.repair.attempted: true`) and quoted
the validator's error, which was `claims never asserted` for three claims; the draft stayed
`invalid` (`recovered: false`). The mechanism runs in real use; for a scenario missing
assertions, one repair was not enough. One observation, not a rate: whether real drafts now
reach `ready` more often needs a recount of `storage/jobs` over more use.

## Trade-offs

Better: a draft is no longer lost to a formatting or schema slip. Worse: an invalid draft now
costs one more provider call, minutes on a slow provider.

## Limitations

Only one repair; drafts with many independent errors may need more. Grounding errors
(missing `source_refs`) need evidence the model may not have gathered.

## Revisit condition

If a recount after real use still shows few `ready` drafts, or repairs mostly failing,
reconsider the spec format itself rather than the repair.
