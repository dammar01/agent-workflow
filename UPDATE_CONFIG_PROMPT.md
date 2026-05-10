# Update Config Prompt — Agent-Workflow → OpenCode Config Sync

Gunakan file ini untuk sinkronisasi perubahan di project `agent-workflow` ke konfigurasi OpenCode (`OPENCODE_GLOBAL_CONFIG_V2.md` dan skill files).

## Kapan Update Diperlukan

Update konfigurasi OpenCode setiap kali ada perubahan di:

- `main.py` — CLI args, session handling, output format
- `config/settings.py` — config structure, cache behavior, env vars
- `adapters/opencode_adapter.py` — OpenCode invocation protocol, session init
- `core/` — execution flow, router, prompt builder changes
- `utils/parser.py` — output parsing, session ID generation

## Session Handling Changes (Latest)

Perubahan di agent-workflow:

- `utils/parser.py` — `generate_main_session_id()` → format `main_YYYYMMDD_HHMMSS`
- `config/settings.py` — `get_cached_main_session_id()` / `set_cached_main_session_id()` menggunakan `storage/cache.json`
- `main.py` — auto-generate session ID kalau `--session default`, dengan cache reuse. Flag `--fresh-session` untuk force new session.
- `adapters/opencode_adapter.py` — `init_session()` menerima `workflow_session_id` untuk traceability.

Update yang diperlukan di `OPENCODE_GLOBAL_CONFIG_V2.md`:

1. **Startup Protocol** — tambahkan poin 5 tentang generate `MAIN_SESSION_ID` di awal sesi chat.
2. **Session Handling Rule (WAJIB)** — tambahkan section eksplisit: 1 sesi chat = 1 workflow session, WAJIB cek cache/context sebelum generate, jangan regenerate di tengah sesi.
3. **Semua skill files** (explore, plan, execute, verify, analyze) — ubah STEP 2 "Tentukan session" untuk mencakup:
   - Cek `MAIN_SESSION_ID` di context/memory sesi chat.
   - Reuse kalau sudah ada; generate baru kalau belum.
   - Semua invoke pakai `-s <MAIN_SESSION_ID>`.

## Checklist Update Config

- [ ] Baca `OPENCODE_GLOBAL_CONFIG_V2.md` versi saat ini
- [ ] Identifikasi section yang terdampak perubahan code
- [ ] Update Startup Protocol jika ada perubahan initialization/session
- [ ] Update Session Handling Rule jika ada perubahan session lifecycle
- [ ] Update semua skill "Tentukan session" sections (explore, plan, execute, verify, analyze)
- [ ] Update invocation commands kalau ada perubahan CLI args
- [ ] Update response format parsing kalau ada perubahan output JSON
- [ ] Verifikasi konsistensi antar section
- [ ] Pastikan tidak ada broken references

## Mapping: Code Change → Doc Section

| Code File | Doc Section | Notes |
|-----------|-------------|-------|
| `main.py` CLI args | Skill invocation commands | Update `-s`, `-c`, `-p`, `-w`, `-m`, `--pretty` |
| `main.py` session logic | Startup Protocol, Session Handling Rule, Skill STEP 2 | Session generation, caching, reuse |
| `adapters/opencode_adapter.py` | OpenCode Subprocess Invocation Protocol | `init_session`, `run_agent`, output parsing |
| `config/settings.py` | Config defaults, env vars | `AI_PROXY_TIMEOUT_SECONDS`, `OPENCODE_COMMAND` |
| `core/executor.py` | Response Format | JSON fields: `status`, `content`, `session_id`, `opencode_session_id` |
| `core/router.py` | Command Mapping | `-c` arg mapping ke workflow command |
| `utils/parser.py` | Output Cleanup, Session ID Extraction | Regex patterns, log filtering |
