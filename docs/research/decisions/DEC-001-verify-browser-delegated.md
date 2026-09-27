# DEC-001: `/.verify-browser` is a delegated command

## Metadata

```yaml
id: DEC-001
date: 2026-09-27
status: accepted
provenance:
  type: observed      # maintainer decision after reading the runtime's stage split
  confidence: medium
disposition:        # see CONTRACT.md §14
  implementation_status: implemented
  validation_status: not_validated
  validated_by: maintainer
  validated_on: 2026-09-27
```

## Problem

Three layers classified `/.verify-browser` differently. The shipped `CLAUDE.md` put it in the
LOCAL registry and its skill said "LOCAL command: no pre-flight gate"; the reference called
it delegated; the runtime contracts described a local browser with delegated draft and review
stages. The intent map listed only `explore`, `plan`, `analyze`, `verify`, and its prefix
pattern `/\.(…|verify)\b` matched `/.verify-browser` as `verify`, so an explicit
`/.verify-browser` set the gate marker under the wrong command name.

## Decision

`/.verify-browser` is DELEGATED everywhere: in the `CLAUDE.md` registry, in `intent-map.json`
(delegated list, prefix pattern, trigger phrases), and in the pre-flight gate. The runtime
work is described as three stages: the secondary agent drafts the spec (stage 1) and reviews
the browser evidence (stage 3); a **runtime player** — a child process the runtime starts —
drives Playwright (stage 2). The skill's flow is reversed accordingly: write a minimal
request, run the draft, and interview only when the draft reports the target unconfigured or
unreachable. `config.json` is read by the runtime, not by main_agent before the run.

## Alternatives considered

- **Label only**: call it delegated in the registry and documents, but keep it out of the
  intent map and the gate. Rejected: `tools/maintain/sync_intent_map.py` requires the
  registry and the intent map to match, and a delegated command without the gate is the
  half-state the inconsistency came from.
- **Keep it LOCAL**: rejected because two of its three stages are secondary-agent calls and
  its result is a `verify` verdict.

## Reasoning

The command's reasoning — which claims to test, which scenario proves them, whether the
evidence proves them — is done by the secondary agent. Only the browser run is local, and it
is run by the runtime, not by main_agent. Delegated routing matches that division of work,
and the gate stops main_agent from gathering evidence itself before the run.

## Supporting evidence

- Code: `core/provider/executor.py` dispatches `verify-browser` to `core/evidence/e2e/runner.py`;
  stages 1 and 3 call `Executor._run_delegated`; stage 2 starts
  `python -m core.evidence.e2e.player`. `main.py` lists `verify-browser` in
  `BACKGROUND_COMMANDS`.
- Draft SYN-002 (responsibility allocation) discusses the same split; it is kept locally in
  `docs/research-drafts/` and is not tracked.

## Implementation

```yaml
version: 3.7.3
commit: unknown   # not committed when this record was written
components:
  - dist/config/claude/CLAUDE.md (registry, gate lists, verify-browser rule)
  - dist/config/claude/hooks/intent-map.json (delegated list, prefix_regex, patterns)
  - dist/config/claude/skills/verify-browser.md (gate note, reversed STEP 0/1)
  - tools/maintain/sync_intent_map.py (command names may contain '-')
  - docs/reference.md, docs/runtime-contracts.md, README.md
```

## Validation

`not_validated`. `sync_intent_map.py --check` and `sync_skills.py --check` pass, and the new
prefix pattern resolves `/.verify-browser` correctly under both Python `re` and .NET. The
reversed skill flow has not been run end to end.

## Trade-offs

Better: one classification in every layer; the gate covers browser verification; the wrong
marker name is fixed. Worse: main_agent can no longer read `config.json` before the first
draft, so an unconfigured project costs one draft call before the interview.

## Limitations

The gate is enforced only in auto-intent mode and fails open. In command-only mode the rule
is prompt-level only.

## Revisit condition

If the draft call regularly fails for lack of configuration that main_agent could have read
first, reconsider letting a named config read through the gate.
