# Runtime contracts

Which parts of `.workflow/config.json` the Python runtime actually obeys, and which
contracts it structurally cannot enforce. Both lists lived in `core/workflow_runtime.py`
as tuples no code ever read — they were documentation wearing a data structure. Moved
here during the folder restructure so they stay findable without pretending to be code.

## Keys the runtime reads

Everything else under `commands` and `policies` is an instruction to main_agent only.
Those keys are inert in this process: renaming one changes nothing here. The list is
kept explicit so that "configured" is never mistaken for "enforced".

- `commands.verify_mode` (`delegated` | `syntax`; anything else warns and falls back to `delegated`)
- `policies.fact_relevant_limit`
- `policies.fact_recurrence_threshold`
- `policies.graph_leads_enabled`
- `policies.subagent_fanout_enabled`

`/.verify-browser` reads nothing from config.json: its settings travel in a per-session
request (see below).

Timeout, stall, and probe settings are **not** here. They live in `opencode.json`, where
the adapter and the job manager read them. The doctor report names that location so their
home is not a guessing game.

## Contracts the runtime cannot enforce

Written down because the absence of enforcement keeps getting read as an oversight to fix
rather than as a property of where the data lives. In every case the runtime never sees
the bytes it would have to check.

| Contract | Why it is unreachable from Python |
| --- | --- |
| `[OPTIONS]` block in `/.plan` | Appears in main_agent's **output**. This process produces evidence for it and never sees what it writes back to the user. |
| Per-claim attribution tags | Same: output-side, never routed back through the runtime. |
| The confidence triple | Same. |
| Intent detection without the `/.` prefix | Matches on the **user's** message. No Python path receives one. |
| `/.execute` and its `-y` gate | `/.execute` is implemented entirely by main_agent editing files. There is no Python entry point to hook. |
| `commands.auto_verify_after_execute` | Same — which is why the config key ships with that caveat inline rather than as a promise. |
| `/.verify-browser` interview and confirmation | The questions and the user's "run it" happen in main_agent's thread. The runtime only sees the request file written afterwards. |

What *is* checkable is the second agent's output, because it comes back through the
runtime: see `core.evidence.contract.contract_warnings`. Those are reported, never fatal.

## /.verify-browser

`--command verify-browser` (a background command like `verify`), dispatched from
`Executor.execute()` to `core/evidence/e2e/runner.py` under the one runtime lock the call
holds. Stages that need a provider go through `Executor._run_delegated` directly — never
back through `execute()`, whose fact ingest, evidence indexing and fan-out bookkeeping
belong to public commands.

The request is `.workflow/sessions/<session>/e2e/request.json`
(`core/evidence/e2e/request.py`):

```json
{"version": 1, "phase": "draft" | "run", "settings": {...},
 "scenario": {...}, "existing_tests": [...], "spec_notes": [...]}
```

`settings` keys (defaults are smoke-test starting points, not calibrated limits):
`base_url`, `browser`, `headless`, `slow_mo_ms` (pause after each browser action for a
watchable headed run; 0..5000, counts against `total_timeout_s`), `nav_timeout_ms`,
`step_timeout_ms`, `idle_timeout_s`, `total_timeout_s`, `probe_max_elements`,
`allow_remote`, `allowed_origins`, `allow_side_effects`, `allowed_mutation_paths` (write
guard exceptions, see below: `/path` on `base_url`'s origin or a full `http(s)://host/path`,
exact path, no wildcard or query, at most 20), `fail_on_console_error`,
`artifact_max_mb`, `existing_test_command` (an argv template run without a shell; `{files}`
and `{base_url}` expand), `existing_test_allowlist`, `existing_test_timeout_s`. An unknown
key or a wrong type is `request_invalid`, not a silent fallback. Hybrid review is not a
setting: it always runs.

| Phase | Stage | Command / route | Reply contract | Ends when |
| --- | --- | --- | --- | --- |
| both | request | — | — | missing or malformed → `incomplete: request_missing | request_invalid` (not recorded) |
| both | preflight | — | — | `base_url` policy, Playwright, browser, or URL reachability fails → draft `blocked`, run `incomplete` with that reason |
| draft | 1 | `e2e_spec` (internal route, role exploration; refused by `main.run()` and absent from the CLI) | standard `[EVIDENCE]` … `[DIGEST]` with an `[E2E SPEC]` section inside | a proxy failure (returned as-is); otherwise always — `[E2E DRAFT]` with status `ready` or `invalid` (spec missing after one targeted continuation, refused by policy, ungrounded) |
| run | spec | the request's `scenario` | — | refused by validation → `incomplete: spec_invalid`; malformed secrets.env → `secrets_invalid`; an unresolved `${ENV}` → `env_missing` |
| run | existing tests | `settings.existing_test_command` over allow-listed files | exit code | never by itself — covered claims become proven (exit 0) or `unknown` (anything else) |
| run | 2 | local player child process (`python -m core.evidence.e2e.player` → `browser.run_scenario`) | JSONL events (`progress`, `observation`, `artifact`, `harness`, `heartbeat`, `result`) | never by itself — its report decides; skipped when the scenario has no steps |
| run | 3 | `verify` with an `[E2E EVIDENCE]` block in the prompt | ordinary `[VERIFICATION]` | skipped when the browser could not finish; a failed review is a declared gap, never a softer verdict |
| run | normalise | — | canonical `[VERIFICATION]` on `result.content`; `meta.verdict` set before `_finalize_verify_result` | — |

A draft's `meta.command` is `verify-browser`: it carries no verdict, exits like any
non-verify command, is finalised under its own name (never counted as a verification),
and writes `draft.json` beside the request for the run to copy. A run's `meta.command` is
`verify` with `meta.invocation: verify-browser`, so its verdict, exit code, and acceptance
metrics are the ones every verification uses.

Credentials: `${NAME}` placeholders resolve from `.workflow/e2e/secrets.env` (`KEY=VALUE`
lines, `#` comments, optional quotes), then from the process environment. The same values
reach the existing-test command's environment. A malformed line is reported by number
only. The resolved values reach the player only inside the scenario on stdin.

`[E2E SPEC]` sections: `claims` (`id | severity | description | source_refs`),
`existing_tests` (`path | covers | confidence`), `coverage_gap`, `scenario_json` (a
fenced JSON scenario), `spec_uncertainties`. Validation runs in both phases, before any
`${ENV}` value is resolved or any browser exists (`core/evidence/e2e/spec.py`):

- actions from the allowlist `goto click fill select press wait_dom expect_dom
  expect_url expect_title probe`; every assertion names a `claim_id`; every claim is
  asserted at least once unless an existing test that will actually run covers it (a
  scenario may then have no steps at all);
- every claim carries `source_refs`: `path[:line[-line]]` naming a file and line that
  exist in the project, or `req:<id>`;
- `goto` targets are relative paths under `base_url`, or absolute URLs on its origin or in
  `allowed_origins` (remote origins also need `allow_remote`, which is how a virtual host
  such as `http://app.test` is admitted); backslashes, whitespace and non-http schemes are
  refused. The player applies the same rule at runtime to top-level navigations, so a
  redirect or click that leaves the origin is blocked (`navigation_blocked`, harness);
- a step that changes data declares `side_effect` (`creates_test_data`,
  `modifies_test_data`, `deletes_test_data`), `test_environment_required: true` and
  `cleanup`, and is still refused unless `allow_side_effects` is true. That declaration is
  the scenario's own word, so the player does not rely on it: while `allow_side_effects`
  is false it aborts every request whose method is not `GET`, `HEAD` or `OPTIONS`, on any
  origin (an API on another port is still the app's data), unless the request's scheme,
  host, port and exact path match an `allowed_mutation_paths` entry (the query is
  ignored). A refused write inside a step fails that step as `mutation_blocked` (harness,
  so the run is `incomplete`), whatever the step saw after it; a refused beacon (`ping`)
  or a write fired outside any step becomes a `mutation_blocked` warning observation.
  Records keep the method and origin only, never path, query or body. The guard is
  method-only: a `GET` that mutates, a WebSocket message, or a service worker's own
  request is not seen, and an asynchronous write can land in the step after the one that
  caused it;
- a step uses one `selector` or `selector_candidates` (1–5, strongest first: role, label,
  testid, text, css), each with a `selector_provenance`;
- credentials only as `${ENV_NAME}`.

Verdict combination is fail-closed: browser `fail` → `fail` whatever the reviewer says
(a reviewer `DONE` is recorded as `reviewer_verdict_overridden`); browser `pass` +
reviewer blocking → `fail`; browser `pass` + reviewer incomplete or unavailable →
`incomplete`; browser `incomplete` → `incomplete`. The `origin` tag on a finding keeps its
temporal meaning; the player's `app | harness | unknown` classification travels as
`evidence_source: e2e_runtime` and in `not_verified` reasons. `unknown` is never promoted
to `harness`.

Classification: a heuristic selector that misses is the harness's; a grounded selector
missing from a settled page is the app's, from an unsettled page `unknown`; timeouts are
`unknown`. A same-origin 5xx on the main request, or an uncaught page error thrown by a
same-origin script (attributed by the script URL in its stack, not the page URL), fails
the run even when every assertion passed. Same-origin console errors fail it only with
`fail_on_console_error`. Third-party noise stays a warning. A passing existing test proves
the claims it covers unless the browser already failed them; a failing one makes them
`unknown`.

Artifacts live under `sessions/<session>/logs/<run>/e2e/`: `spec.json` (placeholder
form), `events.jsonl` (typed values omitted), `report.json`, `evidence.md`,
`verification.md`, `existing_tests.log`, and for a failed step `stepNN.html` (bounded,
input values cleared before capture) and `stepNN.png`, plus `trace.zip` for a failed run.
A heuristic selector miss keeps a bounded DOM probe instead of a screenshot. When the
scenario resolved any `${ENV}` value, no screenshot and no trace is taken: binary captures
cannot be scrubbed afterwards. Resolved values are scrubbed from events and text artifacts
(`.html .htm .txt .log .json .jsonl .md`, in any subdirectory of the run's `e2e/`)
raw, URL-encoded, HTML- and JSON-escaped (values shorter than 4 characters excepted). A
run over `settings.artifact_max_mb` loses traces, then screenshots, then HTML. The
directory is pruned with its run and never entered into `evidence.jsonl`.

Metrics: every run (not a draft) appends one `kind: e2e_run` row to
`.workflow/quality.jsonl` — raw counts, the stage prompt ids, a scenario hash, `run_kind`
(`fake`, `smoke`, `project`) and an optional `WORKFLOW_E2E_LABEL`. `main.py --command
report` derives `e2e` from those rows, split by run kind: verdict, incomplete-by-reason
and origin rates, browser runs per claim, probes, screenshots kept against screenshots
sent to a model, tokens per run (joined through `usage.jsonl`), reproducibility across
repeated scenarios, and a delegated-verify baseline from the same workspace. No
visual-browser-loop baseline is recorded, and none is estimated.

Dependency policy: Playwright is an optional extra, pinned in `requirements-e2e.txt` and
installed only by `python install.py --apply --with-e2e` (pip, then
`python -m playwright install chromium`). The runtime stays stdlib-only;
`core/evidence/e2e/preflight.py` and `browser.py` import it inside the call, under an
`ImportError` guard, and `tests/checks/deps.py` permits exactly that shape in exactly that
package. `/.doctor` reports `e2e_readiness` from package metadata only (installed and
pinned versions, never blocking); the browser and the URL are each run's preflight.
`WORKFLOW_E2E_FAKE=<mode>` makes the player emit a deterministic run (`pass`, `app_fail`,
`harness_fail`, `unknown`, `launch_fail`, `malformed`, `crash`, `stall`,
`heartbeat_forever`, `artifacts`) so the lifecycle is testable without a browser;
`WORKFLOW_E2E_SMOKE=1 python tests/run.py --only e2e-smoke` drives real Chromium against
`tests/fixtures/e2e_app/`.
