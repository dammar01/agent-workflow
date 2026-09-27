# DEC-004: `data-e2e` is the only test attribute

## Metadata

```yaml
id: DEC-004
date: 2026-09-27
status: accepted
provenance:
  type: observed      # maintainer report: drafts suggested data-testid while tags said data-e2e
  confidence: high
disposition:        # see CONTRACT.md §14
  implementation_status: implemented
  validation_status: not_validated
  validated_by: maintainer
  validated_on: 2026-09-27
```

## Problem

Two test attributes coexisted. Tagging proposed `data-e2e` (`tagging.TAG_ATTRIBUTE`), but
the stage-1 prompt ranked `data-testid` among selectors and its JSON example used
`{"testid": ...}`; the selector schema, the page probe and the player's locator all read
`data-testid`. Drafts therefore recommended `data-testid`, contrary to the project's rule,
and a tag the runtime proposed was not the attribute its own selectors looked for.

## Decision

One attribute, `data-e2e`, reached through the selector key `e2e`
(`[data-e2e="<value>"]`, `spec.e2e_css`). `testid` is removed from `SELECTOR_KEYS` and
`SELECTOR_RANK`; `testid`, `data-testid` and `data-e2e` as selector keys are refused with an
error naming `e2e` (`spec.RETIRED_SELECTOR_KEYS`). The prompt, the probe, the player and the
knowledge store follow; knowledge entries recorded with `testid` are no longer offered.

## Alternatives considered

- **Keep `testid` as a fallback key.** Rejected by the maintainer: it keeps the second
  attribute alive, and drafts would keep reaching for it.
- **Configure Playwright's test-id attribute to `data-e2e`.** Rejected: it would keep the key
  name `testid` meaning a different attribute than its name says.

## Reasoning

What tagging writes and what selectors read must be the same thing, or a proposed tag never
becomes a usable selector. A retired key refused with its replacement is repairable by the
stage-1 repair continuation, so the breaking change costs a draft one repair rather than a
dead draft.

## Supporting evidence

- Code at the time: `core/prompt/prompt_builder.py` (selector ranking and example),
  `core/evidence/e2e/spec.py` (`SELECTOR_KEYS`), `core/evidence/e2e/browser.py`
  (probe and `get_by_test_id`), against `core/evidence/e2e/tagging.py` (`data-e2e`).

## Implementation

```yaml
version: 3.7.3
commit: unknown   # not committed when this record was written
components:
  - core/evidence/e2e/spec.py (E2E_ATTRIBUTE, e2e_css, RETIRED_SELECTOR_KEYS)
  - core/evidence/e2e/browser.py (probe, locator)
  - core/evidence/e2e/normalize.py
  - core/evidence/e2e/knowledge.py (retired entries not offered)
  - core/prompt/prompt_builder.py
  - tests/checks/{e2e_spec,e2e_browser,e2e_tagging,e2e_routing}.py, tests/fixtures/e2e_app/
```

## Validation

`not_validated`. The offline suites pass with the fake browser; no real Chromium run
(`e2e-smoke`) or real application has exercised the `e2e` locator yet.

## Trade-offs

Better: one attribute everywhere; drafts, tags and selectors agree. Worse: breaking for a
project whose markup carries only `data-testid` — its drafts must fall back to role, label or
text until the proposed `data-e2e` tags are applied.

## Limitations

Applications that cannot add attributes (third-party widgets) have no test attribute at all
and rely on role, label, text or css.

## Revisit condition

If real runs show drafts falling back to weak `css`/`text` selectors because `data-e2e` is
absent and cannot be added, reconsider reading an existing `data-testid` read-only.
