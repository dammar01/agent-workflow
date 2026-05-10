# Update OpenCode Global Config — Agent-Workflow Integration

Update konfigurasi global OpenCode di `~/.config/opencode/` untuk menambahkan Skill Command Enforcement diatas ## OpenCode Subprocess Invocation Protocol dan dibawah ## Workflow

## Precondition

- `AGENT_PATH` env variable sudah di-set dan menunjuk ke `agent-workflow/main.py`
- Python 3.10+ tersedia di PATH
- OpenCode global config sudah ada di `~/.config/opencode/`

## Changes Summary

## Skill Command Enforcement

**WAJIB invoke agent-workflow untuk setiap skill command (`/.explore`, `/.plan`, `/.analyze`, `/.execute -y`, `/.verify`, `/.refactor`).**
Detection flow:

1. Detect apakah user prompt adalah skill command → cek prefix `/.` + match command registry.
2. Jika match skill command:
   - **TIDAK BOLEH** langsung jalankan logic lokal (search/read/edit).
   - **WAJIB** invoke agent-workflow via `AGENT_PATH` dengan command mapping.
   - Jalankan multi-layer check (L1-L5) sebelum invoke.
   - Parse response JSON, extract `content`, tampilkan ke user.
3. Jika bukan skill command (prompt natural tanpa `/.`):
   - Boleh pilih antara invoke agent-workflow atau langsung lokal sesuai efisiensi.
     Command mapping:
     | User Command | Agent `-c` arg |
     | --------------- | -------------- |
     | `/.explore` | `explore` |
     | `/.plan` | `plan` |
     | `/.analyze` | `analyze` |
     | `/.execute -y` | `execute` |
     | `/.verify` | `verify` |
     | `/.refactor` | (map to `plan` + `execute` sequence) |
     Error bila user pakai skill command tapi agent-workflow unavailable (L1-L5 gagal) → inform user, STOP.
     Natural prompt tanpa `/.` → optional invoke agent-workflow (agent judgment).

Status: READY
