# Architecture — ai-proxy v2

## Overview

`ai-proxy` v2 adalah OpenCode-only CLI proxy.

```text
main.py
  -> SessionManager
  -> SimpleCache
  -> Router (config/opencode.json)
  -> Executor
  -> OpenCodeAdapter
  -> opencode run
```

## Layer Detail

### Entry Layer

`main.py` mengatur urutan request:

```text
run(command, task, session_id, work_dir, model)
  -> SessionManager.load_or_create()
  -> Cache.get()
  -> Executor.execute()
  -> Cache.set() jika success
  -> SessionManager.record_run()
```

### Config Layer

`config/opencode.json` menentukan command, timeout, default model, dan route command ke role/model.

`model: null` berarti OpenCode memakai default model aktif.

### Routing Layer

`core/router.py` membaca config JSON dan mengembalikan route:

```json
{
  "command": "explore",
  "role": "exploration",
  "model": null,
  "opencode_command": "opencode",
  "timeout_seconds": 300
}
```

CLI `--model` override route model.

### Execution Layer

`core/executor.py` membangun prompt berdasarkan role dan menjalankan `OpenCodeAdapter`.

Run pertama memakai `--print-logs` agar session id bisa diparse. Run berikutnya memakai `-s <opencode_session_id>`.

### Adapter Layer

`adapters/opencode_adapter.py` menjalankan subprocess arg-list:

```text
opencode run <prompt> [ -m <provider/model_key> ] [ --print-logs | -s <session_id> ]
```

Adapter membersihkan log OpenCode dan mengembalikan `{ok, content, meta}`.

### Contract Layer

`core/contract.py` menormalisasi output:

```json
{
  "status": "success",
  "adapter": "opencode",
  "model": null,
  "role": "exploration",
  "session_id": "proxy-session",
  "opencode_session_id": "ses_...",
  "content": "...",
  "meta": {
    "confidence": "medium",
    "notes": ""
  }
}
```

### Storage

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

Cache lives in `storage/cache.json`.
