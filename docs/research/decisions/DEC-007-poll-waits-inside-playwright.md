# DEC-007: The browser player's poll waits inside Playwright, not beside it

## Metadata

```yaml
id: DEC-007
date: 2026-09-27
status: accepted
provenance:
  type: observed      # maintainer runs: login POST duration tracked the step timeout
  confidence: high
disposition:        # see CONTRACT.md §14
  implementation_status: implemented
  validation_status: not_validated
  validated_by: maintainer
  validated_on: 2026-09-27
```

## Problem

A `/.verify-browser` login scenario on a private project failed at `expect_url` twice: the
login POST returned 200, but the URL assertion never saw the redirect. Raising
`step_timeout_ms` from 8 s to 30 s did not help. The POST's recorded duration was
8,805 ms in the first run and 30,763 ms in the second — each about 0.8 s past the step
timeout — and the page captured at failure already showed the application's
post-login "redirecting" state.

The player uses the sync Playwright API, which dispatches events (route handlers,
`framenavigated`, responses) only while the calling thread is inside a Playwright call.
The polling loops waited with `time.sleep`, and `expect_url` and the `url` readiness
condition read `page.url`, a cached attribute with no round trip. So nothing dispatched for
the whole step: the POST, which passes through the write guard's `context.route` handler,
stayed unrouted until the assertion had already failed and the failure path's
`wait_for_load_state` let events through.

Checks that go through the browser (`count()`, `get_by_text`, `title()`) were not affected,
because each of those is a Playwright call.

## Decision

- The three polling loops (selector resolution, readiness, text polls) wait through
  `Session.pause()`, which spends the interval in `page.wait_for_timeout(POLL_S * 1000)`.
- `Session(sleep=...)` defaults to `None`, meaning "wait through the page". A test that
  drives its own clock still passes `sleep`, and keeps its instant waits.
- If `wait_for_timeout` raises (for example, a page that has closed), it falls back to `time.sleep`.

## Alternatives considered

- Raise the timeout: rejected by the evidence — the POST finished just after the timeout, whatever it was.
- Replace `expect_url` with `page.wait_for_url`: fixes one action, leaves the `url`
  readiness condition and any future cached read starving events the same way.
- Make `_page_url` evaluate `location.href`: a round trip does dispatch, but it ties the fix
  to how each read is written rather than to how the loop waits.

## Reasoning

The fault is in how the loop waits, not in what it reads. Putting the wait inside Playwright
covers every poll in one place, and it covers cached reads that get added later.

## Supporting evidence

- Two maintainer runs on 2026-09-27 (private project, not reproducible outside that machine):
  POST durations 8,805 ms at an 8 s timeout and 30,763 ms at a 30 s timeout; `step05.html`
  showed the post-login state in both.
- `tests/checks/e2e_browser.py` `_check_polling_keeps_events_flowing`: a fake page that
  delivers queued events only inside `wait_for_timeout`. `expect_url` and a `url` readiness
  condition pass with the new wait. The same step fails when the wait is a plain sleep.
  Reverting `pause()` to `time.sleep` makes the check fail.

## Implementation

```yaml
version: 3.7.3 (working tree, uncommitted)
commit:
components:
  - core/evidence/e2e/browser.py (Session.pause, sleep default)
  - tests/checks/e2e_browser.py (_DispatchPage, _TickingClock, _check_polling_keeps_events_flowing)
```

## Validation

`not_validated`. The offline suites pass, including the dispatch regression check. The
login scenario that exposed the problem has not been re-run against the real application
since the change.

## Trade-offs

- Every poll interval is now a round trip to the Playwright driver. It's small at 100 ms, but it isn't free.
- Events now reach the page during polls, so dialogs, route handlers and observers run
  mid-step instead of in a burst after the step. That is the intended behaviour, but
  observers now see timing they did not see before.

## Limitations

- The dispatch model is checked against a fake page that imitates it, not against Playwright
  itself; `e2e-smoke` (real Chromium) does not yet include a guarded write followed by `expect_url`.
- The application in the originating case was also slow to navigate after login in manual
  use (dev-server compile), so a pass after this fix may still need a longer timeout.

## Revisit condition

A real run where a guarded request's duration again tracks `step_timeout_ms`, or a move of
the player to the async Playwright API (which dispatches on its own event loop).
