# DEC-008: An app repeat streak is keyed by failure signature, and a runtime change releases it

## Metadata

```yaml
id: DEC-008
date: 2026-09-27
status: accepted
provenance:
  type: observed      # maintainer runs: three different login failures braked as one streak
  confidence: high
disposition:        # see CONTRACT.md §14
  implementation_status: implemented
  validation_status: not_validated
  validated_by: maintainer
  validated_on: 2026-09-27
```

Revises part of DEC-005. DEC-005 is frozen and not edited. Its project-fingerprint release,
its knowledge entry and its environment buckets still hold.

## Problem

The repeat brake keyed on origin plus a coarse bucket (`timeout`, `harness`, `app`). In
maintainer use on a private project, three consecutive `/.verify-browser` login runs
failed in `app`, each in a different way:
1. A form field was missing because the page had not rendered its form yet.
2. `expect_url` failed because the runtime itself held back the redirect (DEC-007).
3. The same `expect_url` failure again.

The fourth run was refused. It was the first run in which both the scenario and the runtime
were right.

Two gaps compounded:
- A different app failure is a different thing to fix, but the bucket counted all three as one loop.
- The fix for failures 2 and 3 was in the runtime. The project fingerprint cannot see the
  runtime, so the brake did not release.

## Decision

- `classify.failure_signature(report, scenario)` signs an `app` failure as `v1:<digest>`
  over the page, the action, the field and the condition:
  - page: `url_after` through `sanitize_endpoint`, with query and fragment dropped and
    identifier segments folded to `:id`;
  - field: the step's selectors from the placeholder scenario;
  - condition: the error kind, the step's expectation, and the unmet readiness conditions.

  It excludes the step id and the free-text detail. A `fail` with no failed step is signed
  by the run's reason.
- An `app` streak continues only when the signature matches. `timeout` and `harness` stay
  keyed on the bucket.
- Every counted outcome records `runtime` (`TOOL_VERSION` plus a digest of
  `core/evidence/e2e/*.py`). A streak at the limit written by a different runtime is
  released: `repeat_released.by = runtime_changed`.
- Migration: an `app` record without a signature is read as no streak. A record without
  `runtime` is not released by the runtime rule.
- `repeat_failure` knowledge keeps one entry per distinct app signature.

## Alternatives considered

- **Exact signature for every bucket.** Rejected. One environment problem answers to
  several reasons in turn, and the loop the brake exists for edits the scenario between
  runs. An exact key would reset on every edit and never brake that loop
  (`classify.py`, `state.note_e2e_outcome` docstring).
- **Reset when the failing step moves later in the scenario.** Simpler, but it does not tell fields or conditions apart,
  and a reordered scenario looks like progress.
- **Version only for runtime identity.** It misses fixes made during development without a
  version bump, which is exactly how the originating fix was made.

## Reasoning

What the brake is for differs by bucket. An environment loop looks different on every run,
so it has to be counted coarsely. An application failure is one precise thing, so it has
to be counted precisely. The runtime is part of what a run depends on, so it needs its own
release, like the project does.

## Supporting evidence

- Four maintainer runs on 2026-09-27 against one private project and one origin. The
  failure kinds and steps are in the Problem section. The job files are git-ignored and
  kept on the maintainer's machine only.
- `tests/checks/e2e_hardening.py` `_check_failure_signature_names_page_field_and_condition`
  and the streak checks: three different signatures give streaks 1, 1, 2, and the
  environment buckets still reach the limit. An `app` record without a signature reads as
  no streak.
- `tests/checks/e2e_routing.py`:
  - a real app failure through the runner is recorded with its signature and runtime;
  - a streak from another runtime is released with `runtime_changed`;
  - a streak from this runtime still brakes.

## Implementation

```yaml
version: 3.7.3 (working tree, uncommitted)
commit:
components:
  - core/evidence/e2e/classify.py (failure_signature, failure row fields)
  - core/runtime/state.py (note_e2e_outcome signature/runtime, legacy app migration)
  - core/evidence/e2e/runner.py (runtime_identity, runtime_changed release)
  - core/evidence/e2e/knowledge.py (repeat_failure entry per signature)
  - docs/runtime-contracts.md, docs/reference.md, dist skill verify-browser
```

## Validation

`not_validated`. Offline suites only. Per CONTRACT.md §14, this becomes `validated` after
a clean direct-use run that exercises it:
- the provie-style login re-run proceeds without `ignore_repeat_brake`, released by
  `runtime_changed` or with no streak;
- and a later identical app failure brakes.

## Trade-offs

- The brake fires less often on the `app` bucket. A loop that keeps producing *different*
  app failures (a flaky app failing in a new place each run) is no longer braked. It relies
  on the in-run retry comparison and on the person reading the failures.
- Every edit to the runtime's browser package releases at-limit streaks during development.

## Limitations

- The signature relies on the placeholder scenario and on the report's `url_after`. A
  single-page app that never changes its URL signs every failure on one page by field and
  condition alone.
- The runtime digest covers `core/evidence/e2e/` only. A runtime fix elsewhere (the
  provider or the prompt) does not release a streak.
- A runtime-caused failure is still classified `app` (`expect_url` is always `app`). The
  signature only stops *different* ones from adding up.

## Revisit condition

A real loop of distinct app failures that the brake should have stopped, or a runtime
fix outside `core/evidence/e2e/` that should have released a streak.
