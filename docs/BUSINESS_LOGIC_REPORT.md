# Business Logic Report — ai-proxy

**Session:** ai-proxy-20260506_222532
**Date:** 2026-05-06
**Source:** Kimi via proxy (confidence: medium)

---

## Tujuan

`ai-proxy` adalah proxy CLI yang merutekan perintah pengguna ke model AI yang tepat:

- **Kimi** — eksplorasi dan analisis kode (baca konteks, jawab pertanyaan tentang codebase)
- **Claude** — penalaran dan eksekusi (buat plan, jalankan perubahan)

Proxy menambahkan tiga lapisan di atas model: **caching**, **sesi persisten**, dan **standardisasi output**.

---

## Pemetaan Perintah ke Model

| Perintah | Target | Peran |
|----------|--------|-------|
| `explore` | Kimi | Eksplorasi codebase |
| `plan` | Kimi | Kumpulkan evidence untuk plan |
| `analyze` | Kimi | Analisis topik spesifik |
| `execute` | Claude | Jalankan task/reasoning |
| `verify` | Claude | Verifikasi hasil |

Peta ini didefinisikan di `config/routing.py` sebagai `COMMAND_ROUTES`.

---

## Alur Request End-to-End

```
CLI: python main.py -c <cmd> -p <task> -s <session> -w <work_dir>
       │
       ▼
1. SessionManager.load_or_create(session_id)
   └─ Buat/muat file JSON di storage/sessions/<session_id>.json

       │
       ▼
2. Cache.check(key)
   ├─ Key = "<command>:<dir_hash>:<task_hash>" (SHA-256)
   ├─ HIT  → return cached output + tandai cache_hit di meta
   └─ MISS → lanjut eksekusi

       │
       ▼
3. Router.route(command)
   └─ Lookup COMMAND_ROUTES → return target model tuple

       │
       ▼
4. Executor.dispatch
   ├─ Kimi path:
   │   ├─ build_prompt(role=exploration, task, session_id)
   │   ├─ KimiAdapter.run(prompt, session, work_dir)
   │   │   └─ subprocess: kimi --quiet -w <work_dir> -p <prompt> [--session <kimi_id>]
   │   └─ _maybe_link_kimi_session() → simpan kimi_session_id ke session JSON
   │
   └─ Claude path:
       ├─ build_prompt(role=reasoning, task, session_id)
       └─ ClaudeAdapter.run(prompt, session)
           ├─ CLAUDE_COMMAND kosong → return placeholder response
           └─ subprocess: claude -p <prompt>

       │
       ▼
5. contract.normalize_output()
   └─ Standarisasi: {status, model, role, session_id, content, meta}

       │
       ▼
6. Cache.set(key, output)  ← hanya jika status == "success"
7. SessionManager.record_run(session, command, cache_hit)
8. Return output dict (JSON ke stdout)
```

---

## Temuan & Gap

| # | Temuan | Dampak |
|---|--------|--------|
| 1 | `plan` di-route ke Kimi saja, tapi workflow di CLAUDE.md mengharapkan 2-step (Kimi → Claude) | Feature gap — plan evidence tidak otomatis dikirim ke Claude |
| 2 | `ClaudeAdapter` default **placeholder mode** jika `CLAUDE_COMMAND` kosong | Command `execute`/`verify` tidak benar-benar memanggil Claude tanpa env var |
| 3 | Tidak ada fallback antar model | Kimi gagal → error langsung, tidak retry ke Claude |
| 4 | Cache key include `work_dir` hash | Prompt sama di direktori berbeda = cache entry terpisah (by design) |
| 5 | `test_scenario.py` menulis ke `storage/` | Test tidak isolated — jalankan di env terpisah |

---

## Constraint Teknis

- Tidak ada dependency third-party (stdlib only)
- Python 3.10+ (union type `X | Y` di function signature)
- Kimi CLI harus tersedia di PATH
- Claude CLI opsional — tanpanya semua routing ke Claude pakai placeholder
- Timeout default: 300 detik (dapat di-override via `AI_PROXY_TIMEOUT_SECONDS`)
