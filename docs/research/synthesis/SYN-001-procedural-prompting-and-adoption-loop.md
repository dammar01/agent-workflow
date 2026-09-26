# SYN-001: Procedural prompting and the adoption loop

## Metadata

```yaml
id: SYN-001
title: Procedural prompting as uncertainty management, and the adoption loop it creates
status: draft
```

## Question

Why do developers who have agent-workflow available keep decomposing tasks procedurally,
and why does the tool then "feel the same" as a plain coding agent?

## Sources

- [CASE-001](../real-cases/CASE-001-procedural-decomposition.md) — procedural decomposition
  persists in a team
- [CASE-002](../real-cases/CASE-002-latency-perception.md) — latency complaints
- AI-assisted discussion with the maintainer (research input, not evidence; see
  [methodology.md](../methodology.md))

No literature source yet.

## Shared ideas

Both cases describe the same team using the tool in a way that does not exercise its
exploration and planning, and paying its latency anyway.

## Differences

CASE-001 is about how requests are framed; CASE-002 is about the cost of each request. One
concerns benefit, the other cost.

## Synthesis

*Interpretation, not a claim of any individual source.*

**Procedural prompting is rational.** It gives predictability, control, fast feedback,
clear checkpoints, and low perceived risk. An objective-level request ("implement this
end-to-end") asks the developer to trust the agent to understand the repository, decompose
the task, find affected areas, and preserve the architecture. Procedural prompting is a way
of managing that uncertainty, not a lack of skill.

**The difference is the mental model, not prompt-writing skill.**

```text
AI as fast executor         developer decomposes → agent executes → developer decomposes again
AI as semi-autonomous       developer states objective + constraints → agent investigates and
problem solver              decomposes → developer supervises the outcome
```

The maintainer also carries a model of the harness itself (repository + coding agent +
agent-workflow); other developers mainly carry (current task + coding agent). Different
prompting follows naturally.

**This can form an adoption loop:**

```text
differentiated value not understood
  → used like an ordinary coding agent
  → little difference experienced ("feels the same")
  → return to direct, procedural use
  → fewer chances for the workflow to show value
```

Capability, mental model, interaction model, task complexity, and latency tolerance are
coupled; none of them can be judged alone from "it feels the same".

**Implication for how change happens.** Training first ("learn the model, then use it
well") may be the slow path. The faster path is likely *visible benefit first*, after which
behavior and mental model follow. The goal becomes making effective delegation easier than
ineffective delegation, not teaching every developer the harness internals.

## Implication for agent-workflow

- Onboarding should demonstrate a difference on a real task rather than explain the
  architecture ([team-guide/examples.md](../../team-guide/examples.md)).
- Framing guidance must present procedural prompting as valid for small tasks, or it will be
  rejected as dogma ([team-guide/task-framing.md](../../team-guide/task-framing.md)).
- A system that suggests the appropriate mode per task could shorten the loop
  ([H-001](../hypotheses/H-001-complexity-predicts-delegation-value.md)).
- In practice, capability is runtime capability × the user's mental model. A capability that
  users cannot discover naturally is practically unavailable, so ease of use is a condition
  for the capability to exist at all, not a finishing touch.
- A paradox follows: the users who could benefit most (those delegating large changes with
  little review) may be the least likely to use the workflow in the way that delivers the
  benefit. The mental model therefore has to be encoded into the system — defaults,
  routing, visible feedback, low-friction background execution — not only explained in
  documentation. Part of this exists: intent is detected from plain requests and announced
  with an `[INTENT]` line before a delegated command runs.
- Who should hold which part of the work is examined in
  [SYN-002](SYN-002-responsibility-allocation.md).

## Hypotheses generated

- [H-002](../hypotheses/H-002-objective-delegation-reduces-orchestration-overhead.md)

## Confidence

`low` — built on two anecdotal cases from one team and on discussion.

## Open questions

- How common is procedural interaction beyond this team? There is no evidence here to
  quantify it; popularity of "AI builds an app" narratives is not a measure of prevalence.
- Does a single visible success actually shift behavior, and for how long?
