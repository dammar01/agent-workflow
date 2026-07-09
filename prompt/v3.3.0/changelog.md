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

## Iterasi pre-release — trust + concurrency + session delivery

### Session identity + delivery (F0)
- `utils/parser.py generate_main_session_id`: detik → **ms + pid** (`main_<ts_ms>_<pid>`). Fallback tak lagi collision-prone antar-proses.
- **Explicit session threading**: skill delegated (explore/plan/analyze/sweep) + managed block WAJIB teruskan `MAIN_SESSION_ID` (arg ke-3 run script). Akar bug: hook taruh id di context, run script baca dari arg — tanpa diteruskan jatuh ke sesi `default` bersama → 2 main agent concurrent collapse.

### Trust contract (F1 — plan.md + managed block)
- Atribusi tiap klaim: `[proxy:file:line]|[main_agent-inference]|[user-provided]|[PLACEHOLDER]`. Larang tebakan (angka/dependency/regresi) sebagai fakta tak berlabel.
- Pisah `open_questions` (keputusan-user, BLOCKING) vs `resolvable_uncertainties` (main_agent tutup dulu).
- `dependencies` bukti/asumsi. Decision gate MEKANIS: solution_path<high ∨ open_q ∨ inference-berat → clarify (dilarang proceed).
- Inject lintas-sistem sebelum delegate (proxy scope-bounded).

### Evidence-grade kontrak proxy (F2 — prompt_builder.py + second_agent.md)
- `[EVIDENCE]`: `findings/reasoning` → **`grounded` (WAJIB file:line) + `assumptions`** + `dependencies` + `external` ([EXTERNAL:context7] dll, pisah dari codebase) + `scope_covered/scope_not_covered`.
- `[DIGEST]`: tambah `evidence_basis: grounded|mixed|mostly-assumption` (relay-tag anti-lossy).
- MCP context7: read-only, diizinkan tapi WAJIB tag `external` (provenance visible).

### Concurrency partition (F3 — workflow_runtime.py)
- State mutable per-session: `.workflow/sessions/<session_id>/{state,scope,command-cache}.json` + `runtime/` (lock per-session). Static (config/opencode.json/logs/reports) tetap shared.
- `bind_session` **buang reset-on-mismatch** — file per-session, nol tabrakan. Per-session lazy-create (init = static scaffolding saja).
- Lock-block error kini bawa `error_type=runtime_lock` + `next_action`.
- 2 main agent concurrent project sama → state terisolasi, nol clobber (diverifikasi).

### Batch lanjutan (hardening + memory)
- **POSIX concurrency (session-bind.sh)**: hook `.sh` paritas `.ps1` (STEP 5b.1) + registrasi settings per-OS (5b.2). Tanpa ini MAIN_SESSION_ID tak sampai di mac/linux → concurrent rusak.
- **Timeout recovery (#3)**: managed block — panggilan terputus → auto `/.inspect` → attach `check.<ps1|sh> <job_id> --wait --result` atau baca `response.last.md`; re-run cuma bila nihil. `_generate_run_scripts` kini emit `check.ps1`/`check.sh`.
- **Logs per-session (#4)**: `_archive_prompt`/`response` → `.workflow/sessions/<sid>/logs/`.
- **Blast-radius (#5)**: kontrak `dependents` (reverse-dep) + plan/analyze WAJIB masukkan fitur terdampak ke `risks`.
- **Fact-store (#6)** — `core/fact_store.py`: `.workflow/facts.jsonl` project-shared. Ingest HANYA `durable_facts` [config|pattern|invariant] atau grounded recurring ≥5 sesi. Anchor hash file:line → ingest-verify (skip stale-at-birth) + read-verify (drop stale, nol serve-as-fresh). Inject `[KNOWN_FACTS — verify]` sebelum delegate. Prune via `/.clean`.
- Hang opencode: cap wall-clock worker sengaja OFF (keputusan user) — reaper-only.

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
