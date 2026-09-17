# Runtime contracts

Which parts of `.workflow/config.json` the Python runtime actually obeys, and which
contracts it structurally cannot enforce. Both lists lived in `core/workflow_runtime.py`
as tuples no code ever read — they were documentation wearing a data structure. Moved
here during the folder restructure so they stay findable without pretending to be code.

## Keys the runtime reads

config.json holds OVERRIDES only. An absent key in `commands`, `policies` or `e2e` is the
shipped default, applied by the reader; nothing writes defaults into the file, so a default
changed in a later build reaches every project (`config_defaults.merge_config_defaults`,
`effective_section`). The upgrade to layout 2 strips values equal to a default and unknown
or retired keys, and reports each.

Everything else under `commands` and `policies` is an instruction to main_agent only.
Those keys are inert in this process: renaming one changes nothing here. The list is
kept explicit so that "configured" is never mistaken for "enforced".

- `commands.verify_mode` (`delegated` | `syntax`; anything else warns and falls back to `delegated`)
- `policies.fact_relevant_limit`
- `policies.fact_recurrence_threshold`
- `policies.graph_leads_enabled`
- `policies.subagent_fanout_enabled`

`/.verify-browser` reads one section of config.json, `e2e`, as the project's default
settings; everything else about a run travels in a per-session request (see below).

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

The request is `.workflow/data/sessions/<session>/e2e/request.json` (`.workflow/sessions/…` in a workspace not yet on layout 2)
(`core/evidence/e2e/request.py`):

```json
{"version": 1, "phase": "draft" | "run", "settings": {...},
 "scenario": {...}, "existing_tests": [...], "spec_notes": [...]}
```

Settings resolve in three layers, each overriding the one before it: the shipped defaults
in `default_settings()`, the project's `e2e` section in `.workflow/config.json`
(`request.config_settings()`), then the request's own `settings`. The middle layer is what
lets a configured project run without being interviewed again. The two layers are validated
differently on purpose: a malformed value in `config.json` is a WARNING that falls back to
the shipped default (named in `meta.e2e.config_warnings`), because config.json is shared by
every command and one typo there must not stop work that never reads it; the same value in a
request is `request_invalid`, because the user confirmed it for this run.

`settings` keys (defaults are smoke-test starting points, not calibrated limits):
`base_url`, `browser`, `headless`, `slow_mo_ms` (pause after each browser action for a
watchable headed run; 0..5000, counts against `total_timeout_s`), `nav_timeout_ms`,
`step_timeout_ms`, `idle_timeout_s`, `total_timeout_s`, `probe_max_elements`,
`allow_remote`, `allowed_origins`, `allow_side_effects` (judged by the addresses the write
hosts resolve to; see below), `allowed_read_only_requests` (`"POST /path"` or `"POST http(s)://host/path"`,
exact path, no wildcard or query, at most 20; see below), `secrets_profile` (a
`secrets.json` profile name; empty = the file's `default`), `secrets_template_profiles`
(profile names a newly created `secrets.json` gets; at most 20, unique),
`fail_on_console_error`, `max_retries` (how many extra browsers one run may start; see
below), `artifact_max_mb`, `existing_test_command` (an argv template run
without a shell; `{files}` and `{base_url}` expand), `existing_test_allowlist`,
`existing_test_timeout_s`. An unknown key or a wrong type is `request_invalid`, not a silent
fallback; `allowed_mutation_paths`, removed, is `request_invalid` with a message naming
what replaced it. Hybrid review is not a setting: it always runs.

| Phase | Stage | Command / route | Reply contract | Ends when |
| --- | --- | --- | --- | --- |
| both | request | — | — | missing or malformed → `incomplete: request_missing | request_invalid` (not recorded) |
| both | preflight | — | — | `base_url` policy, write policy (`allow_side_effects` with a write host that is not loopback or `.test` and resolves public, link-local or nowhere → `spec_invalid`), Playwright, browser, or URL reachability fails → draft `blocked`, run `incomplete` with that reason |
| draft | 1 | `e2e_spec` (internal route, role exploration; refused by `main.run()` and absent from the CLI) | standard `[EVIDENCE]` … `[DIGEST]` with an `[E2E SPEC]` section inside | a proxy failure (returned as-is); otherwise always — `[E2E DRAFT]` with status `ready` or `invalid` (spec missing after one targeted continuation, refused by policy, ungrounded) |
| run | spec | the request's `scenario` | — | refused by validation → `incomplete: spec_invalid`; malformed secrets.json, an unknown profile, or several profiles and none selected → `secrets_invalid`; an unresolved `${ENV}` → `env_missing` |
| run | existing tests | `settings.existing_test_command` over allow-listed files | exit code | never by itself — covered claims become proven (exit 0) or `unknown` (anything else) |
| run | 2 | local player child process (`python -m core.evidence.e2e.player` → `browser.run_scenario`) | JSONL events (`progress`, `request`, `cleanup`, `observation`, `artifact`, `harness`, `heartbeat`, `result`; shapes in `classify.py`) | never by itself — its report decides; skipped when the scenario has no steps; retried under the rules below |
| run | 3 | `verify` with an `[E2E EVIDENCE]` block in the prompt | ordinary `[VERIFICATION]` | skipped when the browser could not finish; a failed review is a declared gap, never a softer verdict |
| run | normalise | — | canonical `[VERIFICATION]` on `result.content`; `meta.verdict` set before `_finalize_verify_result` | — |

Origin and write policy are two different questions, answered by two different things
(`core/evidence/e2e/preflight.py`). WHERE a run may point is about names: a loopback host, a
`.test` name (reserved for local development by RFC 6761, so it cannot be a public host, and
therefore needs no `allow_remote`), or an origin listed in `allowed_origins` behind
`allow_remote`. Crossing to any other origin mid-run still needs that origin listed,
whatever class base_url belongs to.

WHAT may be written to treats loopback and `.test` alike (`is_local_dev_host`): both are
local development by definition, take writes with no lookup, and get no pin — the one thing
that can still point a `.test` name elsewhere is the user's own resolver. Every other write
host is judged by address: with `allow_side_effects` true, `write_network()` resolves
base_url's host and every allow-listed origin's, and refuses the run (`spec_invalid`) if any
of them has a public address, a link-local one (169.254.0.0/16 is the cloud metadata
service), or none at all. A name that resolves to both private and public addresses is
refused on the public one. Each approved name is pinned to the address it resolved to —
including a name that resolved to loopback, such as `127.0.0.1.nip.io`, whose owner can
answer differently on the browser's own lookup; only a loopback NAME is left unpinned. The
approved names and pins travel to the player as `write_hosts` and `host_pins`; chromium is
launched with `--host-resolver-rules` (an IPv6 pin in brackets) so a name cannot resolve
elsewhere between the check and the write, and a browser whose resolver cannot be pinned is
refused a pinned write for exactly that reason. The guard never resolves anything itself —
asking DNS a second time is what rebinding exists to exploit.

Redirects never reach the route handler: Chromium follows them itself (measured against
Playwright 1.60 — a POST answered 307 re-sent its body to the new address unseen). So an
approved write is not continued but fetched by the guard with `route.fetch(max_redirects=0)`.
A 307/308, which keeps the method and body, is judged by the same write rule before each hop
is sent (at most `browser.MAX_WRITE_REDIRECTS`); a 301/302/303 is handed back to the browser
as a GET once its target passes the navigation policy; a refused hop aborts the request and
enters the ledger as blocked with the reason. `route.fetch` runs outside Chromium's resolver
rules, so a pinned host over http is fetched at its pinned address with its name in `Host`,
and a pinned host over https — which cannot be held to an address that way — is refused
unsent. A TOP-LEVEL navigation redirected off policy cannot be stopped this way without
breaking the page URL: it is detected from the `request` event after the browser followed it
and fails the step as `navigation_blocked`, saying so.

Tag proposals (`core/evidence/e2e/tagging.py`): after a run, every step that PASSED whose
winning selector had provenance `source` and a `selector_provenance.ref` of `path:line`
becomes a proposal to add `data-e2e="<step id>"` to that line. Each is validated against the
repository as it is — inside the project root, a template suffix, not git-ignored, the line
exists, holds exactly ONE opening tag and no commented-out markup (a line addresses a line,
not an element, so `<div><button>` is refused rather than resolved as the div), and does not
already carry the attribute — and the whole
plan, `ready` and `skipped` with reasons, is written to `tag-proposals.json` and summarised
in `meta.e2e.tags`. The runtime never edits a template: the user confirms the batch and the
main agent applies `old_line` → `new_line`. `apply()` re-checks both the line AND the path: the
plan was made before the user was asked, which is time enough for the path to become a symlink
out of the project. Selector VALUES never enter the event stream resolved: `selection` carries
key names only, and what a step expected (`selector`, `text`, readiness `unmet`, `contains` /
`equals` / `matches`) is reported from the PLACEHOLDER scenario the runner hands the player
as `display_scenario`, since a value can be a resolved credential shorter than
`redact.MIN_SCRUB_CHARS` and therefore unscrubbable. Elements found by
heuristic or by probing the live DOM are never proposed — there is no map from a runtime
selector back to the template that rendered it, and the rendered DOM is framework output,
not source. `knowledge_claims()` turns applied tags into anchored claims for `/.promote`.

Retry (stage 2 only, `runner.RETRYABLE_REASONS`): a run that ends `incomplete` for an
environmental reason — `harness_error`, `unknown_origin`, `stuck`, `timeout`,
`output_truncated`, `launch_failed` — starts the browser again, up to `settings.max_retries`
(default 2, hard ceiling `runner.MAX_RETRIES`). Two consecutive attempts whose verdict,
reason and per-step outcomes all match stop the loop and are marked `stable`: the run is
reproducible, not flaky, which is the one thing a retry was there to establish. Never
retried: a `fail` verdict (the application broke the claim, and re-rolling a real bug until
it hides is what this package exists to prevent), the reasons a rerun cannot change
(`playwright_missing`, `browser_missing`, `base_url_unreachable`, `spec_invalid`,
`env_missing`, `player_unavailable`), and any run with `allow_side_effects` true — its
first attempt's write may already have reached the server. Attempt 1 owns the run's `e2e/`
directory; each retry writes to `e2e/retry<n>/`, and `meta.e2e.attempts` lists them. One
`artifact_max_mb` budget covers the run directory and every `retry<n>/` together. A run that
PASSES only on a later attempt is not a clean pass: the verdict carries a gap naming the
reasons the earlier attempts ended on (`passed only on attempt N of N`), so it is
`incomplete` — `timeout` and `not_ready` are also what an intermittently broken app looks like.

Headed is the default. On a Linux machine with neither `DISPLAY` nor `WAYLAND_DISPLAY` set
(CI, a container, SSH) a headed run is launched headless instead, with a
`meta.e2e.config_warnings` entry; Windows and macOS always have a session to open a window in.

A job running `verify-browser` whose worker dies is not recovered: recovery would replay the
whole scenario, writes included, on top of what the dead attempt already sent. The worker
fails the job with `worker_died`, `meta.reason: not_recoverable`, and releases the lock.

A draft's `meta.command` is `verify-browser`: it carries no verdict, exits like any
non-verify command, is finalised under its own name (never counted as a verification),
and writes `draft.json` beside the request for the run to copy. A run's `meta.command` is
`verify` with `meta.invocation: verify-browser`, so its verdict, exit code, and acceptance
metrics are the ones every verification uses.

Credentials: `${NAME}` placeholders resolve from one profile of `.workflow/e2e/secrets.json`,
then from the process environment. The names are a registry in code
(`core/evidence/e2e/request.py` `CREDENTIAL_KEYS`: `E2E_USER`, `E2E_PASS`); another
credential is a new registry entry, never a name a scenario or second_agent invents:

```json
{"default": "qa",
 "profiles": [{"name": "qa", "credentials": {"E2E_USER": "...", "E2E_PASS": "..."}},
              {"name": "admin", "credentials": {"E2E_USER": "...", "E2E_PASS": "..."}}]}
```

`profiles` is a list of uniquely named profiles; `credentials` holds registered names with
string values; `default` names a listed profile. The pre-list object shape
(`"profiles": {"qa": {...}}`) is `secrets_invalid` with the new shape in the message — it
is never read and never converted. The request selects a profile by name
(`settings.secrets_profile`); empty means the file's `default`, or its only profile. Several
profiles with neither is `secrets_invalid`, never a guess — trying another account is
another run with another name. An empty string is an unfilled slot, not an empty
credential. When a draft finds a placeholder unset and the file does not exist, the draft
creates it from code — the registry's names, empty, in one profile per
`secrets_template_profiles` name (the selected profile first; `default` when none), with a
JSON serializer — and says so. Creation writes a private temporary file and publishes it
with a hard link, which fails if the name exists, so an existing file, one saved meanwhile,
or one a concurrent draft created first is never rewritten. Errors name a profile, a
credential position, or a JSON line — never a value, and never a key that failed validation
(a value pasted into the key slot is what that error reports), except that a registered
name in the wrong case is pointed at its spelling. The process environment is consulted for
registered names only. The whole selected profile reaches the existing-test command's
environment and is scrubbed from its output. The resolved values reach the player only
inside the scenario on stdin. A resolved value shorter than the scrubber's 4-character
minimum cannot be mapped back to its placeholder, so that run keeps no page HTML
(`meta.e2e.secrets.unscrubbable` names the credential); structured events are still
scrubbed only for longer values.

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
  `allowed_origins` (a remote origin also needs `allow_remote`; a `.test` origin does not);
  backslashes, whitespace and non-http schemes are refused. The player applies the same rule
  at runtime to top-level navigations: a click that leaves the origin is blocked before the
  request goes out, and a redirect that does is detected after the browser followed it —
  both fail the step as `navigation_blocked` (harness);
- every step and cleanup step has an `id` (kebab, unique across both lists); events, the
  request ledger and the report refer to it;
- a step that changes data declares `side_effect` (`creates_test_data`,
  `modifies_test_data`, `deletes_test_data`), `test_environment_required: true`,
  `test_data.marker` (the value that tells this run's data apart), optionally `request`
  (`{"method": POST|PUT|PATCH|DELETE, "path": "/items/:id"}`, `:name` matching one segment),
  and is still refused unless `allow_side_effects` is true. A descriptive `cleanup` string
  on the step is refused with the new shape named. Cleanup is the scenario's `cleanup`
  list: executable steps under the same rules, each `cleans: <step id>` naming a
  side-effect step, each group ending in at least one assertion (no `claim_id`: it proves
  the cleanup, not a claim). Created data must have a cleanup group; modified or deleted data
  needs one or a `no_cleanup_reason`. That declaration is the scenario's own word, so the
  player does not rely on it. While `allow_side_effects` is true a request whose method is
  not `GET`, `HEAD` or `OPTIONS` passes only when the request's own host is loopback
  (`localhost`, `*.localhost`, 127.0.0.0/8, `::1`), a `.test` name, or a host preflight
  approved and pinned — this catches an API on a remote host called from a local page, and
  its redirects are followed by the guard (above). While it is false every such request is aborted, on any origin (an API on another
  port is still the app's data). A POST that only reads (a search, a GraphQL query) may pass as a confirmed read:
  stage 1 proposes it under `read_only_requests` (`method | endpoint | source_refs | reason`,
  the handler's `path:line` required and grounded, `req:` refused, an ungrounded proposal
  makes the draft `invalid`), the draft lists it, the user confirms, and only an entry the
  request's `allowed_read_only_requests` names is admitted. A proposal never writes itself
  into settings. Even then the body is read: a GraphQL `mutation` or `subscription` (JSON,
  batch, form `query=`, or raw), a persisted query whose operation is only a hash, or a body
  that cannot be read as text is still refused, with the reason in the step's detail. String
  literals and comments are blanked before that check; a field literally named `mutation`
  taking arguments is refused too. Admitted reads become one `read_only_request_allowed`
  observation per method and origin, with a count — a warning-class record, never an app
  error. A refused write inside a step fails that step as `mutation_blocked` (harness,
  so the run is `incomplete`), whatever the step saw after it; a refused beacon (`ping`)
  or a write fired outside any step becomes a `mutation_blocked` warning observation.
  Those records keep the method and origin only, never path, query or body. The guard is
  method-only: a `GET` that mutates, a WebSocket message, or a service worker's own
  request is not seen;
- every non-GET/HEAD/OPTIONS request the guard sees, sent or refused, becomes one `request`
  event: id, `phase` (`steps` | `cleanup`), `step_id` when it was sent inside that step's
  window (readiness wait plus action), otherwise `attribution: uncertain` with `after_step`
  — never pinned to the nearest step — method, `endpoint` sanitised (scheme, host, port,
  path; query, fragment and credentials dropped; numeric, UUID, long-hex, long-token, JWT,
  mixed-case-with-digits token and `@` segments become `:id`), resource type, `read_only`,
  `blocked`, `planned` (the step's `request` matched; `false` for an extra write; `null` when
  unattributed or a confirmed read), `status` from the
  response event, `failure`, `duration_ms` from request to `requestfinished` /
  `requestfailed`. A write still waiting when the run ends is reported with
  `no response before the run ended`, including when the player crashes. Bodies, cookies and
  tokens are never recorded. An
  HTTP 2xx proves nothing on its own — the assertions do;
- a step uses one `selector` or `selector_candidates` (1–5, strongest first: role, label,
  testid, text, css), each with a `selector_provenance` (optionally `ref`, a
  `path[:line]`), optionally scoped by `within` (a selector for the modal, form or table);
  the player tries only those candidates, needs exactly one match, and reports the
  candidate used, each candidate's match count and whether a fallback was used;
- `ready` (1–5 conditions, each one of `hidden` / `visible` / `enabled` with a selector,
  `text`, or `url`) is awaited before the action — after navigation for a `goto` — within
  `step_timeout_ms`; unmet conditions fail the step as `not_ready` (origin `unknown`) and
  are named. Page stability on failure is `load` plus the step's own conditions, never
  `networkidle`. An action runs once: a timeout after dispatch is a failed step, never a
  re-sent write. Test steps stop early (skipped, with the reason) to leave cleanup
  `2 × step_timeout × cleanup steps` of `total_timeout_s`, at most half;
- credentials only as `${ENV_NAME}` with a registered name, spelled exactly; any other
  `${...}` is `spec_invalid`.

Cleanup runs after the test steps, whether they passed or failed. A cleanup step is
`not_needed` when the step it cleans never ran, `skipped` after an earlier failure in its
group; a planned step with no event (the player was killed or ran out of time) is
`not_run`. The report carries `cleanup: {status, groups, steps, unplanned}`, `requests`, and
`trail` (per step: selection, readiness, request ids, result), and the evidence block shows
all three. Cleanup never changes the test's `browser_verdict`: a `failed` or `not_run`
cleanup turns an otherwise passing verification into `INCOMPLETE` (exit non-zero, never
the gap-only exit 0) with the group in `not_verified`; a `fail` stays `fail`; a declared
`no_cleanup_reason` is a note.

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

Browser knowledge (`core/evidence/e2e/knowledge.py`, `.workflow/e2e-knowledge.jsonl`) sits
at the fact store's level: written automatically, anchored, aged out on its own, never in
Git. Per base_url origin a run records `auth.login` (the login page's goto through the first
assertion after `${E2E_PASS}`), `auth.logout` (an action followed by an `expect_url` back to
that page), `navigation`, `page_ready` and `selector` (matched exactly one element) —
built from the placeholder scenario, with a non-placeholder `fill` value dropped. Only proof
counts: a passed, non-writing step in a run that passed on its FIRST attempt. A failed step
weakens the matching entry; `STALE_AFTER_FAILS` (2) in a row retire it. A selector with a
`source_ref` is anchored like a fact; `--command clean` relocates a moved anchor and drops
retired entries and vanished anchors. A draft gets the live entries for its origin (bounded
per kind) in the sidecar `sessions/<session>/runtime/e2e_knowledge.json`, named in the prompt
only when it holds something, as hints that still have to be grounded in code;
`meta.e2e.knowledge` reports `offered` on a draft and `added | confirmed | weakened |
retired` on a run. `promotable_claims()` turns anchored entries proven in
`PROMOTE_AFTER_PASSES` (3) runs into `/.promote` claims.

Metrics: every run (not a draft) appends one `kind: e2e_run` row to
`.workflow/data/quality.jsonl` — raw counts, the stage prompt ids, a scenario hash, `run_kind`
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

## Second agent: provider config, model, and prompt transport

A project runs on its own `.workflow/second_agent.json` and nothing else
(`config.settings.resolve_provider_config_for`). There is no machine-wide fallback: a missing
file resolves `source: missing` and an unparseable one `source: invalid`, and the executor
refuses both before any provider call (`provider_config_missing`, `provider_config_invalid`,
each with a next_action). The tool-level `config/second_agent.seed.json` is a template `init`
copies into a new workspace and `install.py --apply` rebuilds from
`second_agent.example.json` on every run, carrying only the previous selection (provider,
default model, per-route models). The old `config/second_agent.json`, written once and never
refreshed, is renamed `.retired` and its selection is not carried over.

Selectable routes are `explore plan analyze verify e2e_spec`. An opencode call whose route
resolves no model is refused as `model_unset`: without `-m`, opencode answers with the model
it used last on the machine. `/.doctor` names `-free` models as a recommended fix.

opencode's prompt travels as a FILE: it is written to
`sessions/<session>/runtime/opencode-prompt.md` and attached with `-f`, and argv carries one
static sentence (transport `file` in `config/providers.py`, so the task cap is the policy
cap, not a command-line remainder). On Windows `opencode` is the npm shim `opencode.cmd`,
parsed by cmd.exe, which does not understand `subprocess`'s `\"` escaping: with the prompt on
argv, a quoted JSON example became an input redirect (every verify-browser draft failed with
"The system cannot find the file specified") and an odd quote in a task was command
injection. Any remaining argument that cmd.exe would interpret (`" & | < > ^ %` or a newline
in the command path, prompt-file path, model, agent or effort) is refused before spawning
as `unsafe_command_line`.

## install.py --apply and leftovers

`--apply` refuses a `dist/` that does not match `dist/manifest.json` (dry run warns). After
installing, it removes files an earlier release installed and this one no longer ships —
only a file an install recorded writing (the ledger `~/.claude/.workflow-installed.json`, or
any install receipt), in a family installed file by file (skills, commands, hooks, provider
agents), and still byte-for-byte what was written. A file edited since is kept and named.
Each removal is backed up under `backups/install_*/stale/` and receipted as `remove`, which
`--rollback` restores. settings.json loses the hook commands that run a retired script and
has a `statusLine` that runs a workflow script refreshed; a statusLine or hook running the
user's own script is theirs. `--check` reports leftovers. `init`/`upgrade` point a workspace
at the build running the command first, then `$AGENT_PATH`, and only then the path recorded
in config.json.

## Workspace layout

`.workflow/` holds what a person edits: `config.json`, `second_agent.json`,
`e2e/secrets.json`, the `run`/`check`/`inspect` scripts, and `current/`. Everything else —
`sessions/`, `provider-sessions/`, `reports/`, the usage/audit/quality/redactions streams,
the evidence, fact and browser-knowledge stores, caches, backups — lives in
`.workflow/data/`. `workspace_paths.workflow_paths` is the only place any of those paths is
spelled.

`data_dir()` decides the layout from the disk: `data/` once it exists; the `.workflow` root
while a 3.6 workspace still keeps its data there (any of `_LEGACY_MARKERS` present); `data/`
otherwise. The hooks (`intent-gate-set`, `intent-gate-check`, `workflow-statusline`) and the
generated run scripts apply the same rule when they run, so every reader agrees at any
moment. `upgrade` (and init's auto-upgrade) runs `core/runtime/migrations.py`: backup to
`data/backups/<stamp>/`, store locks held, internal items staged in `data.migrating/` and
renamed to `data/` in one step, evidence artifact paths rewritten, pre-session leftovers and
old locks removed, config stripped to overrides, a pre-list `secrets.json` converted.
A failure before the rename puts everything back and keeps the copy as
`migration-backup-<stamp>/`. A live job refuses the upgrade before anything moves. `doctor`
reports `workspace_layout` (layout version, data dir, unrecognised root files).

`current/` is a mirror of the latest delegated dispatch, written by `Executor.execute` and the
browser runner and never read back: `session.json` (session, command, status, phase,
`other_active` sessions holding a runtime lock), `progress.jsonl` (reset per dispatch), and
for a browser run `e2e/events.jsonl` (scrubbed like the stored events), `e2e/last.png`, and
the run's `report.json`, `verification.md`, `evidence.md`. A newer dispatch takes it over and
an older session stops writing into it.
