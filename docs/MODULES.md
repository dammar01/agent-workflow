# Module Reference — agent-workflow v2

## `main.py`

Entry point CLI dan orchestrator.

Flow: session -> cache -> executor -> cache write -> session record.

CLI:

```text
python main.py -c <command> -p <prompt> -s <session> [-w <work_dir>] [-m <provider/model>] [--pretty]
```

## `core/executor.py`

Dispatch command ke OpenCode.

Responsibilities:

- route command via `Router`
- build prompt via `build_prompt()`
- call `OpenCodeAdapter.run()`
- store first `opencode_session_id`
- normalize adapter output

## `core/router.py`

Reads `config/opencode.json` and returns route dict.

Route contains:

```json
{
  "command": "explore",
  "role": "exploration",
  "model": null,
  "opencode_command": "opencode",
  "timeout_seconds": 300
}
```

## `core/prompt_builder.py`

Builds `[WORKFLOW_CONTEXT]` + `[TASK]` prompt.

Supported roles:

- `exploration`
- `reasoning`
- `execution`
- `verification`

## `core/contract.py`

Normalizes output schema.

```json
{
  "status": "success | error",
  "adapter": "opencode",
  "model": "provider/model | null",
  "role": "exploration | reasoning | execution | verification",
  "session_id": "proxy-session",
  "opencode_session_id": "ses_...",
  "content": "...",
  "meta": {
    "confidence": "low | medium | high",
    "notes": "..."
  }
}
```

## `core/session_manager.py`

Persists sessions to `storage/sessions/<id>.json`.

Session shape:

```json
{
  "session_id": "finance-auth",
  "opencode_session_id": "ses_...",
  "history": {
    "created_at": "...",
    "updated_at": "...",
    "runs": []
  }
}
```

## `adapters/opencode_adapter.py`

Subprocess wrapper for OpenCode.

First run:

```text
opencode run <prompt> --print-logs
```

Resume:

```text
opencode run <prompt> -s <session_id>
```

Custom model:

```text
opencode run <prompt> -m <provider/model_key>
```

## `config/opencode.json`

User-editable config for command routes and default model.

`model: null` means OpenCode active default.

## `config/settings.py`

Loads config and env defaults.

Env vars:

- `OPENCODE_COMMAND`
- `AI_PROXY_TIMEOUT_SECONDS`

## `config/roles.py`

Role constants and `VALID_ROLES`.

## `utils/cache.py`

File cache in `storage/cache.json`.

Key includes command, model, work dir hash, and task hash.

## `utils/parser.py`

Parsing helpers.

OpenCode helpers:

- `extract_opencode_session_id(text)`
- `clean_opencode_output(text)`

## `test_scenario.py`

Scenario tests using fake OpenCode adapter.

Validates:

- OpenCode route
- session id capture
- session reuse
- model override
- cache hit
- log cleanup
- error contract
