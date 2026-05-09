# Architecture — ai-proxy

## Overview

`ai-proxy` adalah CLI proxy berlapis tiga:

```
┌─────────────────────────────────┐
│  ENTRY LAYER                    │
│  main.py — orkestrasi utama     │
└──────────────┬──────────────────┘
               │
┌──────────────▼──────────────────┐
│  INFRASTRUCTURE LAYER           │
│  SessionManager  SimpleCache    │
└──────────────┬──────────────────┘
               │
┌──────────────▼──────────────────┐
│  ROUTING & EXECUTION LAYER      │
│  Router → Executor              │
└──────────────┬──────────────────┘
               │
┌──────────────▼──────────────────┐
│  ADAPTER LAYER                  │
│  KimiAdapter  ClaudeAdapter     │
└──────────────┬──────────────────┘
               │
┌──────────────▼──────────────────┐
│  EXTERNAL PROCESSES             │
│  kimi CLI     claude CLI        │
└─────────────────────────────────┘
```

---

## Layer Detail

### Entry Layer — `main.py`

Titik masuk tunggal. Orkestrasi urutan: session → cache → executor → cache write → session record.

```
run(command, task, session_id, work_dir)
  │
  ├─ SessionManager.load_or_create()
  ├─ Cache.get()  ──► HIT: return + mark_cache_hit()
  ├─ Executor.execute()
  ├─ Cache.set()  (jika success)
  └─ SessionManager.record_run()
```

### Infrastructure Layer

**SessionManager** (`core/session_manager.py`)
- Persistensi sesi ke `storage/sessions/<id>.json`
- Menyimpan `kimi_session_id` untuk resume sesi Kimi
- Mencatat history runs (command, timestamp, cache_hit)

**SimpleCache** (`utils/cache.py`)
- Cache file tunggal: `storage/cache.json`
- Key = `"<command>:<dir_hash12>:<task_sha256>"`
- Optional TTL (default: None = tidak expire)

### Routing & Execution Layer

**Router** (`core/router.py`)
- Lookup `COMMAND_ROUTES` dict
- Return tuple target model: `(MODEL_KIMI,)` atau `(MODEL_CLAUDE,)`
- Raise `ValueError` untuk command tidak dikenal

**Executor** (`core/executor.py`)
- Dispatch ke adapter berdasarkan route
- Build prompt via `prompt_builder.build_prompt()`
- Normalize output via `contract.normalize_output()`
- Link Kimi session ID setelah run pertama berhasil

**PromptBuilder** (`core/prompt_builder.py`)
- Wrap task dalam template `[WORKFLOW_CONTEXT]` + `[TASK]`
- Template berbeda per role: exploration vs reasoning
- Optional `[EVIDENCE]` block untuk plan multi-step

**Contract** (`core/contract.py`)
- Satu-satunya tempat shape output didefinisikan
- Semua output harus lewat `normalize_output()`
- Fields: `status`, `model`, `role`, `session_id`, `content`, `meta.confidence`, `meta.notes`

### Adapter Layer

**KimiAdapter** (`adapters/kimi_adapter.py`)
- Subprocess: `kimi --quiet -w <work_dir> -p <prompt> [--session <id>]`
- Baca `~/.kimi/kimi.json` untuk link session ID
- Error handling: FileNotFoundError, TimeoutExpired, OSError

**ClaudeAdapter** (`adapters/claude_adapter.py`)
- Subprocess: `claude -p <prompt>`
- Jika `CLAUDE_COMMAND` kosong → return placeholder (tidak error)
- Error handling sama dengan KimiAdapter

---

## Dependency Graph

```
main.py
├── core/executor.py
│   ├── core/router.py
│   │   └── config/routing.py
│   ├── core/prompt_builder.py
│   │   └── config/roles.py
│   ├── core/contract.py
│   │   └── config/roles.py
│   ├── adapters/kimi_adapter.py
│   │   ├── config/settings.py
│   │   └── utils/parser.py
│   └── adapters/claude_adapter.py
│       ├── config/settings.py
│       └── utils/parser.py
├── core/session_manager.py
│   └── config/settings.py
└── utils/cache.py
    └── config/settings.py
```

---

## Storage Layout

```
storage/
├── sessions/
│   └── <session_id>.json     ← satu file per sesi
└── cache.json                ← semua cache entries dalam satu file
```

### Session JSON Schema

```json
{
  "session_id": "finance-auth",
  "kimi_session_id": "a2d0c19d-...",
  "history": {
    "created_at": "2026-05-06T14:00:00+00:00",
    "updated_at": "2026-05-06T14:05:00+00:00",
    "runs": [
      { "command": "explore", "cache_hit": false, "timestamp": "..." }
    ]
  }
}
```

### Output Contract Schema

```json
{
  "status": "success | error",
  "model": "kimi | claude",
  "role": "exploration | reasoning",
  "session_id": "...",
  "content": "...",
  "meta": {
    "confidence": "low | medium | high",
    "notes": "..."
  }
}
```
