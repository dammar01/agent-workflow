# H-XXX: <Title>

## Metadata

```yaml
id: H-XXX
status: proposed
provenance:
  type: synthesized   # direct | adapted | synthesized | observed | unknown
  confidence: low
disposition:        # see CONTRACT.md §14
  implementation_status: not_implemented   # not_implemented | partially_implemented | implemented | not_applicable
  validation_status: not_validated         # not_validated | observed | partially_validated | validated | rejected
  validated_by:                            # maintainer; required once validation_status is not not_validated
  validated_on:
```

## Statement

> State one testable hypothesis.

## Motivation

Why is this hypothesis being considered?

## Supporting sources

- LIT-XXX
- SYN-XXX
- CASE-XXX

## Counter evidence

What observations or literature could contradict the hypothesis?

## Expected effect

What should change if the hypothesis is correct?

## Evaluation

How will the hypothesis be tested? Link the `EXP-XXX` record once it exists.

## Success criteria

The primary metric, and the result that would count as `supported` and as `rejected`.
Name the data each one needs and whether that data is recorded today. A threshold not yet
chosen is written `[PLACEHOLDER]`, never invented.

## Limitations

What could make the result unreliable?

## Outcome

`pending` | `supported` | `partially_supported` | `rejected`

## Related decisions

- DEC-XXX
