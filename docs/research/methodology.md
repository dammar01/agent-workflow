# Research Methodology

`agent-workflow` is developed through an iterative engineering and research process.

The project does not assume that every implementation originates from a single research
paper. Design decisions may emerge from a combination of:

1. engineering observations,
2. literature review,
3. synthesis across multiple sources,
4. AI-assisted discussion,
5. implementation experiments,
6. real-world usage,
7. subsequent evaluation and revision.

## AI-assisted research

AI systems may be used during literature exploration, hypothesis generation, conceptual
synthesis, architecture discussion, implementation, debugging, and experiment
interpretation.

AI-generated reasoning is treated as **research input**, not authoritative evidence.
Claims produced during AI discussion must not automatically become project knowledge.

A claim should be promoted only after it has an identifiable basis such as literature,
observation, implementation evidence, experiment results, or explicit human reasoning.

## Literature relationship

Literature may relate to the project in different ways:

- the project directly adapts a mechanism from the source,
- the source independently supports an existing design direction,
- the source challenges an existing assumption,
- the source inspires future work,
- the relationship is indirect or conceptual.

A paper discovered after implementation must not be presented as the original source of
that implementation unless historical evidence supports that claim.

## Real-world cases

Real-world usage may provide valuable evidence, but company-specific information may be
confidential. Public research records therefore use abstraction.

```text
Private:
"The ERP module X in company Y had file A with 2,000 lines."

Public:
"A mature production repository contained large modules
combining data access, business logic, and UI responsibilities."
```

The public record preserves the engineering phenomenon while removing confidential
identity and implementation details.

## Limitations of observations

Real-world observations may be uncontrolled, affected by operator behavior, specific to a
repository, affected by a particular model or provider, affected by prompt formulation, or
limited in sample size.

Such observations are labeled accordingly and are not automatically generalized.

## Research integrity

The project prefers an incomplete but accurate history over a complete-looking history
built from uncertain reconstruction.

When historical provenance is unavailable:

```yaml
provenance:
  type: unknown
```

is preferred over assigning an unsupported literature source.
