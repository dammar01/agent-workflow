# DEC-005: The repeat brake releases on a project change and is remembered as knowledge

## Metadata

```yaml
id: DEC-005
date: 2026-09-27
status: accepted
provenance:
  type: observed      # maintainer report: repeat_failure forced a session clear after a fix
  confidence: medium
disposition:        # see CONTRACT.md §14
  implementation_status: implemented
  validation_status: not_validated
  validated_by: maintainer
  validated_on: 2026-09-27
```

## Problem

The `/.verify-browser` repeat brake refuses a run after three runs against one origin end
the same way. Its exits were a passing run (which the brake stood in front of), a per-request
`ignore_repeat_brake` the skill never taught, and a new session. A person who fixed the
application still hit the brake and cleared the session to get past it.

## Decision

- Every counted failure stores the project's fingerprint (`fingerprint.project_fingerprint`:
  Git HEAD, the tracked diff, and untracked non-ignored files, all outside `.workflow/`);
  the newest is kept.
- A streak at the limit whose fingerprint differs from the project's current one is cleared
  and the run proceeds, reported as `meta.e2e.repeat_released.by = change_detected`.
  Scenario and request edits live under `.workflow/` and do not count.
- When the brake refuses, a `repeat_failure` entry is written to the browser knowledge store,
  offered first to later drafts for the origin in any session, and retired by a passing run.
- The skill stops suggesting a new session; a fix that changes no file still uses
  `ignore_repeat_brake` once.

## Alternatives considered

- **A reset command or skill.** Rejected by the maintainer: no new skill; the brake itself
  should recognise a fix.
- **Only teach `ignore_repeat_brake` in the skill.** Rejected: it relies on the operator
  declaring a fix, while a file change is observable.

## Reasoning

The brake exists to stop scenario edits against an unchanged environment. A change to the
application's source is the fix it asks for, and it is observable without trusting anyone's
word. Keeping the scenario out of the fingerprint preserves the loop it was built to break.

## Supporting evidence

- Code at the time: `core/runtime/state.py` (`note_e2e_outcome`), `core/evidence/e2e/runner.py`
  (two brake points), `dist/config/claude/skills/verify-browser.md` ("Streak hilang … di sesi
  baru").

## Implementation

```yaml
version: 3.7.3
commit: unknown   # not committed when this record was written
components:
  - core/evidence/e2e/fingerprint.py (new)
  - core/runtime/state.py (fingerprint on the record)
  - core/evidence/e2e/runner.py (release, knowledge write, resolve on pass)
  - core/evidence/e2e/knowledge.py (repeat_failure kind)
  - dist/config/claude/skills/verify-browser.md
  - tests/checks/{e2e_routing,e2e_hardening}.py
```

## Validation

`not_validated`. The routing suite shows an unchanged project braked, a scenario edit braked,
a source change released, and the knowledge entry written and retired, on a temporary Git
repository with the fake player. No real application run.

## Trade-offs

Better: no session clear after a real fix; the failure outlives the session as a hint.
Worse: every counted failure runs a few Git commands; a fix that changes no file is not seen;
an unrelated file change also releases the brake.

## Limitations

Outside Git there is no fingerprint and only `ignore_repeat_brake` releases the brake. A
change unrelated to the failure (a README edit) releases it too — at most three more runs
before it brakes again.

## Revisit condition

If released runs keep failing the same way right after unrelated changes, narrow the
fingerprint (for example to paths the failing steps' `ref`s point at).
