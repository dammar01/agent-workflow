# Business Logic Report — agent-workflow v2

## Tujuan

`agent-workflow` v2 memusatkan workflow ke OpenCode. Proxy memberi lapisan session, cache, prompt template, config JSON, dan output cleanup di atas `opencode run`.

## Command Mapping

| Command   | Role           |
| --------- | -------------- |
| `explore` | `exploration`  |
| `plan`    | `reasoning`    |
| `analyze` | `reasoning`    |
| `execute` | `execution`    |
| `verify`  | `verification` |

Mapping ada di `config/opencode.json`.

## Alur Request

```text
CLI -> main.run()
  -> load/create proxy session
  -> cache check
  -> route command via config JSON
  -> build prompt
  -> opencode run
  -> parse opencode session id
  -> clean logs
  -> normalize output
  -> cache success response
  -> record run history
```

## Session Behavior

Jika belum ada `opencode_session_id`:

```text
opencode run <prompt> --print-logs
```

Proxy parse:

```text
session.id=ses_...
```

Jika session sudah ada:

```text
opencode run <prompt> -s <session_id>
```

## Model Behavior

Default model mengikuti OpenCode active default jika route model `null` dan CLI `--model` tidak diisi.

Custom model:

```text
opencode run <prompt> -m <provider/model_key>
```

## Output Cleanup

Proxy membuang log lines OpenCode/Nest-like:

```text
INFO  2026-05-09T12:10:24 ...
```

Proxy juga membuang banner:

```text
> build · gpt-5.3-codex
```

Konten assistant tetap masuk `content`.

## Constraint

- Python stdlib only
- OpenCode wajib tersedia di PATH
- Config user JSON
- Legacy Kimi/Claude adapter dihapus
