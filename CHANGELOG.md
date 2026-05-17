# Changelog — agent-workflow

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [Unreleased]

### Added

- Project-local `.workflow/` workspace with `config.json`, `state.json`, `scope.json`, `command-cache.json`, runtime handoff files, and reports.
- New local commands: `init`, `doctor`, and `sweep`.
- Project-local prompt handoff via `.workflow/runtime/prompt.txt` and `.workflow/runtime/prompt.meta.json`.
- Runtime lock with TTL and last-response snapshot at `.workflow/runtime/response.last.md`.

### Changed

- Refactor V2 ke OpenCode-only backend.
- Hapus adapter Kimi/Claude dan route model hardcoded.
- Tambah config user JSON di `config/opencode.json`.
- Tambah session resume OpenCode via parsed `session.id=ses_...` dan `-s <session_id>`.
- Tambah cleanup output log OpenCode/Nest-like.
- Session binding now treats `--session` as source of truth and resets active project-local state when incoming session changes.
- `execute` now auto-runs bounded `sweep` after successful execution.
- Resolver now checks `.workflow/config.json.runtime.agent_workflow_path` before env `AGENT_PATH`.

### Deferred

- `audit` intentionally not implemented in v3.1.1.
- No `repair` command; repair flow is expected through `execute` with latest sweep context.

### Notes

- `.workflow` is snapshot/state only and is not the owner of the primary session.
- `.workflow/sessions/current.json` is intentionally not created.

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
