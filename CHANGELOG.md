# Changelog — ai-proxy

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [Unreleased]

---

## [0.2.0] — 2026-05-07

### Added
- Multi-layer `$AI_PROXY` env check di semua skill invocations: CMD/Batch → PowerShell → Python → .NET — stop di layer pertama yang return path valid
- Proxy Invocation Protocol: semua invokasi proxy kini wajib berjalan background (`run_in_background: true`) disertai waiting message ke user, lalu wait-for-notification gate sebelum lanjut ke parse response
- `UPDATE_CONFIG_PROMPT.md` — prompt siap-pakai untuk update skill config yang sudah ada tanpa re-install penuh; menarget `explore.md`, `plan.md`, `analyze.md`, dan `CLAUDE.md` secara targeted
- `WORKFLOW_V0.md` — workflow setup alternatif: single-agent, no proxy, graphify-first; auto-detect agent (Claude Code / Codex / Cursor / Windsurf / Gemini CLI / GitHub Copilot) dan set path sesuai direktori agent yang terdeteksi

### Changed
- `CLAUDE_CODE_CONFIG_V1.1.md`: env check upgrade dari single `echo %AI_PROXY%` ke 4-layer check; tambah section Proxy Invocation Protocol di `CLAUDE.md` dan di tiap skill yang invoke proxy
- Default timeout naik dari 120s ke 300s — sesuai estimasi waktu response Kimi realistis

### Notes
- `UPDATE_CONFIG_PROMPT.md` dirancang untuk update incremental — tidak perlu jalankan setup penuh jika hanya ingin menerapkan perubahan protocol ke config yang sudah ada

---

## [0.1.0] — 2026-05-06

### Added
- Entry point `main.py` dengan CLI argparse (`-c`, `-p`, `-s`, `-w`, `--pretty`)
- `core/router.py` — routing command ke model via `COMMAND_ROUTES`
- `core/executor.py` — dispatch ke Kimi/Claude adapter, normalize output
- `core/prompt_builder.py` — template prompt `[WORKFLOW_CONTEXT]` + `[TASK]`
- `core/contract.py` — standardisasi schema output dengan validasi
- `core/session_manager.py` — persistensi sesi ke `storage/sessions/*.json`
- `adapters/kimi_adapter.py` — subprocess wrapper ke Kimi CLI
- `adapters/claude_adapter.py` — subprocess wrapper ke Claude CLI dengan placeholder mode
- `config/roles.py` — konstanta model dan role
- `config/routing.py` — peta `COMMAND_ROUTES`
- `config/settings.py` — konfigurasi dari env vars
- `utils/cache.py` — file-based cache dengan key SHA-256 dan optional TTL
- `utils/logger.py` — factory logger
- `utils/parser.py` — safe JSON/text parsing dengan 4-level fallback
- `test_scenario.py` — integration test end-to-end
- `.gitignore`

### Notes
- Claude adapter default ke placeholder mode jika `CLAUDE_COMMAND` tidak di-set
- Tidak ada third-party dependencies (stdlib only)
