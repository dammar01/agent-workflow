# CASE-001: Team members keep decomposing tasks procedurally

## Metadata

```yaml
id: CASE-001
visibility: public
environment: private_production
repository_identity: undisclosed
recorded_on: 2026-09-26
evidence_strength: anecdotal   # maintainer's subjective observation, no structured data
```

## Context

A small in-house development team began using agent-workflow with Claude Code on a mature
production codebase. The maintainer introduced the tool and observed its use over roughly
one month. Team members were experienced with coding agents but new to an external harness.

## Task

Everyday feature and maintenance work, with agent-workflow available for all of it.

## Workflow

Team members sent requests to Claude Code with agent-workflow installed. Delegated
exploration and planning were available through intent detection or explicit commands.

## Observation

Team members continued to decompose work procedurally, as they did with a plain coding
agent. The scope of each instruction grew slightly ("create the table, then its CRUD"
instead of one table at a time), but the developer still decided the steps and instructed
the agent to implement them one after another.

Feedback on the tool was mostly "it feels the same as the normal agent", which made
concrete feedback on its specific value hard to obtain.

## Evidence

Direct observation and informal conversations only. No usage logs, prompt samples, or task
outcomes were collected for this record.

## Outcome

`inconclusive`

## Unexpected behavior

None attributable to the runtime. The pattern concerns how the tool was used.

## Limitations

The "feels the same" result cannot be attributed to the tool alone. At least five factors
interact, and any one of them being unfavorable can produce the same impression:

```text
workflow capability × user mental model × prompting behavior × task complexity × latency tolerance
```

Specifically:

- When the developer has already decomposed the task, the workflow's exploration and
  planning are barely exercised, so their benefit cannot show.
- The observation is subjective, from one team, over a short period, without a baseline.
- Task complexity was not recorded; many tasks may have been small enough that direct use
  was the right choice.
- The maintainer is also the tool's author, which can bias the interpretation in either
  direction.

This record must not be read as "agent-workflow provides no benefit", nor as "the team is
using it wrong".

## Related research

- [CASE-002](CASE-002-latency-perception.md): latency complaints from the same team
- [H-001](../hypotheses/H-001-complexity-predicts-delegation-value.md)

## Implementation reference

```yaml
version: 3.7.x
commit:
feature: team adoption; response is docs/team-guide/ (task-framing.md, when-to-use.md)
```
