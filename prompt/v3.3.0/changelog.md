# Changelog — Agent Workflow v3.3.0

**Tanggal:** 2026-07-08
**Tema:** Runtime reliability + kurangi beban main_agent + cross-OS.
**Breaking:** Ya — state pindah, `opencode.json` disederhanakan, contract error berubah. Storage lama dibersihkan (nol migrasi).

---

## Mesin (perbaikan 8 isu)

### Structured errors (FASE 1)
- **`core/contract.py`** dirombak: `make_ok` / `make_error` + taksonomi `error_type`. **`next_action` wajib** di tiap error (raise kalau kosong).
- **`adapters/opencode_adapter.py`**: `permission_denied` (+`blocked_paths`), `empty_output` (+`raw_tail`) — tak pernah balik kosong-diam.
- **`utils/parser.py`**: pola fallback session-id + retry 1× di `init_session` → `session_capture_failed` bila gagal (akar riil "retry baru jadi").
- **`utils/path_guard.py`** (baru): preflight denylist (`.env`/`.ssh`/`secret`/luar-root) → `path_out_of_scope`.

### Job reliability (FASE 2)
- **`request_hash`** — request identik yang masih aktif → reuse job, bukan buat baru.
- **`job_already_running`** terstruktur (+`active_job_id` +`next_action`).
- **Reaper liveness** — worker mati (pid tak hidup) → job auto-`failed`. **Anti-deadlock tanpa timeout** (poll-timeout tetap 0).
- **`prune_jobs`** + command `clean` (ttl 7 hari, keep 50).
- Lock tunggal (session O_EXCL); parallel-read dibuang.

### Prompt fidelity (FASE 3)
- Prompt multiline di-flatten ke 1 line (`\n` → ` \n `) saat kirim ke opencode — opencode `run` **truncate** multiline arg di newline pertama (cuma baris 1 sampai). Wire = 1 line; arsip tetap multiline.
- Rolling archive `.workflow/logs/<prompt_id>/` (prompt.md multiline + sha256 + output) — simpan 20 terakhir. Global `runtime/prompt.txt` tetap sbg handoff aktif.
- CATATAN: v3.3.0 awal sempat buang flatten (asumsi salah subprocess preserve newline) → prompt terpotong → direvert.

## Interface (kurangi beban AI)

### `.workflow` standalone (FASE 4)
- `init` **copy** `opencode.json` ke `.workflow/` (project-local, overridable).
- `config.json` simpan **path absolut** `main_py`/`check_py` + `tool_version` → **`$AGENT_PATH` tak wajib** (fallback).
- Generate **`run.ps1`+`run.sh`** & **`inspect.ps1`+`inspect.sh`** — main_agent panggil 1 script, tak karang command.
- Command **`inspect`** — snapshot human-readable (session, job aktif, last response).

### 1-call + session + digest (FASE 5)
- Alur AI = `await` (1 panggilan blocking, submit+poll+final).
- Session-link main↔second (opencode_session_id) tersimpan di session record.
- **`[DIGEST]`** diproduksi second_agent, diekstrak `contract.extract_digest`. Absen/invalid → fallback `contract_detail`. Main_agent relay digest, lalu bebas pakai skill.
- second_agent prompt: forbid `open_questions` (domain main_agent).

## Housekeeping (FASE 7)
- **`opencode.json`**: buang `role` (role = code-authoritative via `config/routing.py`), buang route `execute` (domain main_agent). Fix bug role `execution` invalid.
- Buang flag mati `audit_enabled`.
- Versi `3.2.0` → `3.3.0`.

## Cross-OS
- **`utils/osutil.py`** (baru) — semua platform-specific terisolasi: `process_alive` (POSIX `os.kill` / Win `OpenProcess`), `detached_popen_kwargs` (POSIX `start_new_session` / Win flags), `python_exe`, `resolve_exe`, `script_ext`, `make_executable`.
- Diverifikasi di Windows + WSL (POSIX): primitif OK. Generate script `.ps1`+`.sh` sekaligus.

## FASE 6 — CLAUDE.md rewrite (PENDING)
- Rombak managed block global `~/.claude/CLAUDE.md` ke alur 1-call + dedup section duplikat + longgarkan aturan 11-field jadi relay-digest.
- **Di luar repo, sensitif (semua project) → butuh approval terpisah, dikerjakan terakhir.**

---

## Migrasi v3.2.1 → v3.3.0
1. `git pull` / update tool.
2. Re-run `init` per project → regenerate `.workflow/` (scripts, opencode.json copy, config abs-path).
3. `opencode.json` project: hapus `role` + route `execute` bila masih ada (atau biarkan — router abaikan).
4. Storage central lama (`storage/`) pensiun — aman dihapus.
5. FASE 6 (CLAUDE.md) manual setelah approval.
