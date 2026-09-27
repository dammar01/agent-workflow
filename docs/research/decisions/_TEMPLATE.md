# DEC-XXX: <Title>

## Metadata

```yaml
id: DEC-XXX
date:
status:            # proposed | accepted | superseded | deprecated
provenance:
  type:            # direct | adapted | synthesized | observed | unknown
  confidence:      # low | medium | high
disposition:        # see CONTRACT.md §14
  implementation_status: not_implemented   # not_implemented | partially_implemented | implemented | not_applicable
  validation_status: not_validated         # not_validated | observed | partially_validated | validated | rejected
  validated_by:                            # maintainer; required once validation_status is not not_validated
  validated_on:
```

## Problem

What problem required a design or behavioral decision?

## Decision

What was decided?

## Alternatives considered

What other approaches were considered?

## Reasoning

Why was this approach selected?

## Supporting evidence

- CASE-XXX
- EXP-XXX
- SYN-XXX
- LIT-XXX

## Implementation

```yaml
version:
commit:
components:
```

## Validation

`not_validated` | `observed` | `partially_validated` | `validated`

What evidence currently supports the decision? A DEC is `validated` by a clean direct-use
run (CONTRACT.md §14): verdict pass, no manual workaround, no issue reported — name its
date, job id, and a sanitised description of what was exercised.

## Trade-offs

What became better? What became worse?

## Limitations

Under what conditions might this decision be wrong?

## Revisit condition

What future evidence should cause this decision to be reconsidered?
