# EXP-XXX: <Title>

## Metadata

```yaml
id: EXP-XXX
date:
status:            # planned | running | completed | abandoned
hypothesis: H-XXX
version:           # agent-workflow version or tag under test
disposition:        # see CONTRACT.md §14
  implementation_status: not_implemented   # not_implemented | partially_implemented | implemented | not_applicable
  validation_status: not_validated         # not_validated | observed | partially_validated | validated | rejected
  validated_by:                            # maintainer; required once validation_status is not not_validated
  validated_on:
```

## Objective

What question does this experiment answer?

## Setup

Repository or corpus, provider and model, configuration, and anything held constant.
Sanitize anything confidential.

## Method

Steps, arms or conditions, number of runs, and how results are judged.

## Metrics

What is measured, and how each number is defined. State units explicitly (for example
work units versus provider rows) so totals can be audited.

## Results

Raw results or a link to them, followed by the summary.

## Interpretation

What the results support, and what they do not.

## Threats to validity

Sample size, operator influence, provider variance, prompt sensitivity, and anything else
that limits generalization.

## Outcome for the hypothesis

`supported` | `partially_supported` | `rejected` | `inconclusive`

## Related records

- H-XXX
- DEC-XXX
