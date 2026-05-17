## UPDATE_CONFIG_PROMPT — v3.1.0 to v3.1.1 Migration

Use this prompt only when the global OpenCode config is already on v3.1.0 and needs to be migrated to v3.1.1 with bounded changes only.

Do not use this as first setup prompt.

Target behavior:

- Plain chat mode vs workflow mode separated.
- Input starting with `/.` = workflow mode.
- Plain text input = chat mode.
- Slash command aliases without dot may exist (`/doctor`, `/init`, `/explore`, etc.), but they must internally inject the canonical workflow command form (`/.doctor`, `/.init`, `/.explore`, etc.) instead of creating a separate path.
- Canonical documentation and examples should still prefer the `/.` form.
- `/.init` calls runtime `init`.
- `/.doctor` calls runtime `doctor`.
- `/.sweep` calls runtime `sweep`.
- Resolver must read `project/.workflow/config.json` field `runtime.agent_workflow_path` before env `AGENT_PATH`.
- Prompt handoff must use `project/.workflow/runtime/prompt.txt`.
- Main agent still creates `MAIN_SESSION_ID`.
- `MAIN_SESSION_ID` is the source of truth for workflow runtime session via `--session`.
- One main-agent session equals one workflow-agent session.
- New main-agent session must create new workflow-agent session.
- Do not reuse stale state when incoming `--session` differs from `.workflow/state.json` snapshot.
- `/.analyze` and `/.explore` are both valid entry points toward `/.plan`.
- `/.execute -y` must auto-run sweep after successful execute.
- `/.audit` is deferred / disabled in v3.1.1.
- No `/.repair` command. Repair uses `/.execute -y` with latest sweep context.
- During workflow execution, do not install/update/uninstall dependencies unless the user explicitly asks for it.
- During workflow execution, do not run git write operations (`push`, `pull`, `fetch` updating refs, `merge`, `rebase`, `commit`, `stash`, etc.) unless the user explicitly asks for it.

Project-local workspace contract:

```text
.workflow/
├─ config.json
├─ state.json
├─ scope.json
├─ command-cache.json
├─ .gitignore
├─ runtime/
│  ├─ prompt.txt
│  ├─ prompt.meta.json
│  ├─ response.last.md
│  └─ lock
└─ reports/
   ├─ doctor.json
   └─ sweep.last.md
```

Rules:

- Do not create `.workflow/sessions/current.json`.
- Do not make `.workflow` the owner of the primary session.
- Session id always comes from main agent through `--session`.
- `.workflow/state.json` is snapshot/validation only.
- If `state.session.id != --session`, treat state as stale and reset active workflow state.
- Root `.gitignore` must contain `.workflow/`.
- `.workflow/.gitignore` content must be exactly `*`.
- Keep legacy commands working where possible: `explore`, `analyze`, `plan`, `execute`, `verify`, `submit`, `await`, `status`, `result`, `worker`.
- Add new commands: `init`, `doctor`, `sweep`.
- Add matching global command files and skill files for `/.init`, `/.doctor`, and `/.sweep`.
- If global `/command` files are kept for compatibility, make them inject the matching `/.command` workflow command directly instead of creating a separate path.
- Do not implement `audit` yet.
- All CLI responses must keep stable JSON contract:

```json
{
  "ok": true,
  "content": "string",
  "meta": {}
}
```

Required runtime behavior:

1. `init`
- create `.workflow` structure
- create default files if missing
- do not overwrite valid existing JSON files without strong reason
- if existing JSON invalid, return `ok: false` with clear error
- ensure root `.gitignore` contains `.workflow/`
- global command/skill path must invoke runtime `init` directly; do not allow agent to manually scaffold substitute files

2. `doctor`
- local command only
- action-oriented result: `READY` or `NOT_READY`
- write report to `.workflow/reports/doctor.json`
- global command/skill path must treat runtime JSON as source of truth for `READY`/`NOT_READY`

3. `sweep`
- local command only
- lightweight post-edit check
- write markdown report to `.workflow/reports/sweep.last.md`
- verdict: `skipped`, `pass`, or `repair_required`
- global command/skill path must invoke runtime `sweep` directly; do not replace it with ad-hoc manual diff inspection by the agent

4. prompt handoff
- use project-local runtime files
- write prompt and meta atomically
- use runtime lock with TTL
- save last response to `response.last.md`

5. session binding
- `--session` is source of truth
- reset state/scope/cache when incoming session changes

6. execute
- still edits through OpenCode
- runtime Python only orchestrates
- successful execute auto-runs sweep internally

7. audit
- deferred and disabled in v3.1.1

Expected prompt changelog summary for v3.1.1:

- add project-local `.workflow` workspace
- add `init` / `doctor` / `sweep`
- add project-local prompt handoff
- add session-bound state reset
- auto sweep after execute
- audit deferred
