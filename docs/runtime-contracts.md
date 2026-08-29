# Runtime contracts

Which parts of `.workflow/config.json` the Python runtime actually obeys, and which
contracts it structurally cannot enforce. Both lists lived in `core/workflow_runtime.py`
as tuples no code ever read — they were documentation wearing a data structure. Moved
here during the folder restructure so they stay findable without pretending to be code.

## Keys the runtime reads

Everything else under `commands` and `policies` is an instruction to main_agent only.
Those keys are inert in this process: renaming one changes nothing here. The list is
kept explicit so that "configured" is never mistaken for "enforced".

- `commands.verify_mode`
- `policies.fact_relevant_limit`
- `policies.fact_recurrence_threshold`
- `policies.graph_leads_enabled`
- `policies.subagent_fanout_enabled`

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

What *is* checkable is the second agent's output, because it comes back through the
runtime: see `core.evidence.contract.contract_warnings`. Those are reported, never fatal.
