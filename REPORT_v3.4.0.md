# Laporan Project — agent-workflow v3.4.0

> Runtime orkestrasi dua-agent. Zero dependency pihak ketiga (Python stdlib saja).
> Dokumen ini: apa yang tersedia, cara kerja, alur, ekspektasi, alasan desain, dan kekurangan.
> Semua klaim teknis di-ground ke `file:line`.

---

## 1. Ikhtisar

`agent-workflow` adalah runtime yang duduk **di antara dua agent** dan memisahkan peran secara tegas:

| Peran            | Siapa                                                  | Boleh apa                                                              |
| ---------------- | ------------------------------------------------------ | ---------------------------------------------------------------------- |
| **main_agent**   | Claude Code / Codex / Cursor (agent yang dipakai user) | Orchestrator, antarmuka user, **satu-satunya** yang boleh menulis file |
| **second_agent** | OpenCode CLI                                           | Pengumpul bukti **read-only**. Bukan jawaban akhir                     |

Runtime menerima command, merakit prompt terstruktur, menjalankan `opencode run`, memvalidasi bentuk output, menyimpan state per-sesi, lalu mengembalikan **JSON contract stabil**:

```json
{ "ok": true, "content": "...", "meta": {}, "digest": {} }
```

State project-local hidup di `.workflow/` pada project target — **bukan** di repo runtime ini.

- Versi: `TOOL_VERSION = "3.4.0"` [config/settings.py:13], `CONFIG_VERSION = "3.4.0"` [core/workflow_runtime.py:22]
- Prasyarat: Python 3.10+, `opencode` di PATH (hanya command terdelegasi), `git` opsional (sweep + verify syntax). Dependency: **nol**.

---

## 2. Apa yang tersedia

### 2.1 Struktur kode (repo runtime)

```
main.py              # CLI entrypoint: init/await/check/doctor/status/result
check.py             # attach path — poll job existing, reap kalau stall
install.py           # install global (~/.claude) + init/upgrade project (.workflow/)
core/
  workflow_runtime.py  # config, versi, upgrade, policies, locks, paths
  executor.py          # orkestrasi delegasi: route → leads → prompt → adapter
  job_manager.py       # lifecycle job, liveness tri-state, stall-detect, reap
  prompt_builder.py    # rakit prompt terstruktur (graph, subagent, verify)
  graph_index.py       # baca graphify-out/graph.json → ranked leads + stale flag
  router.py            # command → role mapping
  contract.py          # validasi bentuk output (EVIDENCE/DIGEST, subagent usage)
  quick_verify.py      # verify_mode=syntax (parse lokal, nol proxy)
  fact_store.py        # durable_facts persistence per-project
  session_manager.py   # isolasi state per MAIN_SESSION_ID
adapters/
  opencode_adapter.py  # jalankan opencode, capture sesi, probe, klasifikasi error
config/
  settings.py          # default timeout/stall/probe/runtime
  roles.py routing.py  # 3 role: exploration / reasoning / verification
prompt/v3.4.0/         # main_agent.md, second_agent.md, changelog.md
dist/                  # artefak ter-generate dari ~/.claude (source of truth)
```

### 2.2 Command (12 skill)

**LOCAL** (main_agent langsung, nol proxy):

| Command                        | Fungsi                                     |
| ------------------------------ | ------------------------------------------ |
| `/.execute -y`                 | Implementasi kode (wajib `-y`)             |
| `/.init`                       | Buat/regenerate `.workflow/`               |
| `/.refactor <s>`               | Perbaikan struktural, zero behavior change |
| `/.commit`                     | Commit message (Conventional Commits)      |
| `/.review <f>`                 | Review one-line-per-issue                  |
| `/.compress <f>`               | Kompres prose ke caveman                   |
| `/.memory <note>`              | Simpan insight                             |
| `/.caveman` `/.local` `/.help` | Toggle & bantuan                           |

**DELEGATED** (1-call `.workflow/run` → second_agent):

| Command             | Role         | Fungsi                             |
| ------------------- | ------------ | ---------------------------------- |
| `/.explore <hint>`  | exploration  | Kumpul bukti (lokasi/bentuk)       |
| `/.plan <task>`     | reasoning    | Bukti + rencana + blok `[OPTIONS]` |
| `/.analyze <topic>` | reasoning    | Analisa mendalam (sebab/penilaian) |
| `/.verify`          | verification | Verifikasi 3-langkah               |
| `/.sweep`           | verification | Git diff impact                    |
| `/.doctor`          | verification | Cek readiness `.workflow/`         |

Routing: `router.py` petakan command → role [config/routing.py:4-13]; 3 role di [config/roles.py:1-3].

---

## 3. Cara kerja — arsitektur dua-agent

Prinsip inti: **main_agent tak pernah menebak isi codebase, second_agent tak pernah menulis file.** Runtime menegakkan pemisahan itu secara mekanis (untuk yang bisa) dan via kontrak prompt (untuk yang tidak).

```
┌─────────────┐   command + task + session_id    ┌──────────────┐
│  main_agent │ ───────────────────────────────► │  run.ps1/sh  │
│ (Claude)    │                                   └──────┬───────┘
│  - route    │                                          │ python main.py --command await
│  - synthesize                                          ▼
│  - WRITE    │ ◄─── {ok,content,meta,digest} ─── ┌──────────────┐
└─────────────┘                                    │   Executor   │
                                                   │  route→leads │
                                                   │  →prompt     │
                                                   └──────┬───────┘
                                                          ▼
                                          ┌────────────────────────────┐
                                          │  OpenCodeAdapter            │
                                          │  opencode run <prompt>      │──► second_agent
                                          │  capture + classify         │◄── [EVIDENCE]/[DIGEST]
                                          └────────────────────────────┘
```

### Dua lapis streaming (penting untuk memahami failure)

1. **Pipe drain kita** — `_popen_capture` + daemon reader thread di adapter menyerap stdout/stderr opencode.
2. **SSE stream opencode** — stream provider LLM internal opencode. Error `"Streaming response failed"` lahir **di sini**, bukan di lapis kita.

Membedakan keduanya krusial untuk klasifikasi error yang benar (lihat §5 fitur 7).

---

## 4. Alur lengkap — prompt masuk hingga response

### 4.1 Delegated call (contoh `/.analyze`)

1. **Trigger** — user ketik command atau bahasa natural. main_agent deteksi intent, emit `[INTENT] <command>`, jalankan `.workflow/run.ps1 <command> "<task>" "<MAIN_SESSION_ID>"`.
2. **Await** — `run` panggil `python main.py --command await`. `MAIN_SESSION_ID` (arg ke-3) mengisolasi state per-sesi → `sessions/<id>/` (wajib untuk concurrent same-project).
3. **Job spawn** — `JobManager` buat worker detached (Popen), tulis job record + heartbeat side-file [core/job_manager.py].
4. **Route** — `Executor` → `router` petakan command ke role [core/executor.py].
5. **Leads** — kalau `graph_leads_enabled` (default `True` [workflow_runtime.py:189]), `graph_index.leads()` baca `graphify-out/graph.json`, hasilkan ranked candidate_files + community + **stale flag** [core/executor.py:102-103, graph_index.py:287-318].
6. **Prompt build** — `prompt_builder.build_prompt()` rakit prompt terstruktur: header role, `[GRAPH_LEADS]` block, `[CONSTRAINTS]` (read-only, no expand), `[TASK]`, `[OUTPUT_FORMAT]` (EVIDENCE/DIGEST) [core/prompt_builder.py:74-125].
7. **Execute** — `OpenCodeAdapter.run()` jalankan `opencode run <prompt> --agent <role> -m <model> -s <session>`. Prompt sebagai **satu argv** (batas cmd.exe 8191 char di Windows).
8. **Capture + classify** — adapter serap output, deteksi returncode/error. Klasifikasi: rate_limit → stream_failed → permission → cmd_line (dicek pada **tail** error, bukan seluruh transkrip — lihat §5 fitur 7).
9. **Validate** — `contract.py` cek bentuk output ada `[EVIDENCE]`/`[DIGEST]`; deteksi subagent usage kalau fanout aktif.
10. **Return** — JSON `{ok, content, meta, digest}` balik ke main_agent (blocking).
11. **Synthesize** — main_agent REASONING sendiri (plan/analyze) atau RELAY digest (explore/sweep/doctor), lalu output ke user dengan atribusi + confidence.

### 4.2 Recovery (call terputus)

Kalau tool timeout / no JSON: **jangan langsung re-run** (worker detached lanjut). Recovery otomatis: `/.inspect` → job running + cmd sama → attach `check.ps1 <job_id> --wait --result` (nol run baru); job baru selesai → baca `sessions/<id>/runtime/response.last.md`; nihil → baru re-run.

---

## 5. Fitur v3.4.0 & status stabilitas

Diringkas dari analisa grounded (10+ file). Verdict: **stabil / prompt-contract / rapuh**.

### (1) Sub-agent fanout di second_agent — IMPLEMENTED, rapuh, disabled di repo ini

- Config `subagent_fanout_enabled` default `True` [workflow_runtime.py:196]; executor gate pada `route["role"] in (exploration, reasoning)` + config [executor.py:114].
- `prompt_builder` emit `[SUBAGENT_PLAN]` hanya bila fanout + graph punya ≥2 cluster (fallback `_BLIND_SLICES`) [prompt_builder.py:37-71].
- Guardrail: `detect_subagent_usage()` validasi fan-out nyata (cek `subagents:` line + `[cN]` tag), mismatch di-flag [contract.py:67-98, executor.py:235-239].
- **Kelemahan:** bergantung opencode punya sub-agent/task tool — **nol verifikasi runtime**, kode cuma asumsi. Di workspace ini `subagent_fanout_enabled: false` (mati).

### (2) Auto-upgrade script — STABIL, manual by design

- `needs_upgrade()` bandingkan `workspace_versions()` vs TOOL/CONFIG_VERSION [workflow_runtime.py:287-299].
- `upgrade_workflow_workspace()` regen script + merge config default + backfill opencode key, **refuse saat ada job aktif** [workflow_runtime.py:302-371, active_jobs_for_workspace 374-403].
- Dipanggil `install.py --init-project` saat `.workflow/` sudah ada [install.py:390-409].
- **Kelemahan:** bukan auto-trigger — user wajib jalankan `install.py`/`upgrade` eksplisit. Tujuan "tak perlu prompt+verify manual" tercapai (regen tanpa delegasi), tapi bukan otomatis penuh.

### (3) `[OPTIONS]` block di `/.plan` — STABIL sebagai prompt-contract

- Terdaftar di `UNENFORCEABLE_PROMPT_CONTRACTS` [workflow_runtime.py:149-156] — Python runtime **tak pernah lihat** output ini.
- Definisi di prompt v3.4.0 main_agent.md:166-187 (STEP 2b: max 3 opsi, plus/minus/effort/risk, satu rekomendasi, bounded).
- **Kelemahan:** compliance murni disiplin prompt, tak terverifikasi runtime.

### (4) `auto_verify_after_execute` — STABIL sebagai prompt-contract

- Default `false` [workflow_runtime.py:168], disurface ke main_agent via `result.meta.policy` [executor.py:296-297].
- **Zero enforcement Python** (by design — `/.execute` nol jalur runtime). `false` → `/.execute` wajib `status: implemented`, `verification: not_run`, jangan bilang "done".
- **Kelemahan:** sama seperti (3), bergantung disiplin main_agent.

### (5) graphify leads + context-mode — CUKUP STABIL, degrade senyap saat graph basi

- `leads()` rank file + community + stale flag [graph_index.py:287-318]; `_compact_leads()` truncate untuk verify (Windows 8191 limit) [prompt_builder.py:110-125].
- context-mode: opt-in **global** (`install.py --enable-context-mode` register plugin di `~/.config/opencode/opencode.json`) [install.py:226-269], bukan per-project.
- **Kelemahan:** staleness cuma **WARNING**, nol auto-regen. Graph di repo ini sudah basi (warning muncul tiap call). Kualitas leads turun senyap kalau tak `graphify update`.

### (6) Auto intent detection tanpa `/.` — IMPLEMENTED sebagai prompt-contract

- Logika penuh ada di managed-block `CLAUDE.md` (dist + `~/.claude` global): "Intent detection AUTO-FIRE", NL map, tie-break rules.
- Runtime Python **tak** implement ini (benar — unenforceable); config cuma simpan `workflow_prefix: "/."` [workflow_runtime.py:180].
- **Catatan:** analisa proxy sempat bilang "zero implementation anywhere" — itu **keliru scope** (proxy baca `prompt/v3.4.0/main_agent.md`, bukan CLAUDE.md managed block). Fitur nyata jalan (bukti: `[INTENT]` di-emit tiap turn).

### (7) Fix second_agent stuck saat limit habis / reap 1-menit terlewat — STABIL (paling kokoh)

- **Dual-signal liveness:** `liveness()` cek PID alive → `_idle_seconds()` baca idle stream + drift → `ALIVE_STALLED` bila > `idle_stall_seconds` (240s default) [job_manager.py:286-346, settings.py:32].
- **Re-probe cadence:** `await_job()` re-probe setiap `probe_recheck_seconds` (120s), **bukan sekali** — inilah yang menutup isu "cek 1-menit terlewat" [main.py:430-438].
- **Fresh-session probe:** `check_stalled_job()` bedakan rate_limit vs stream_failed vs dead via PING session baru [main.py:322-403].
- **Atomic reap:** `reap_stalled()` klaim `reaped=True` **sebelum** kill, cegah resurrection [job_manager.py:396-442].
- **Diperkuat sesi ini (fix landed + verify DONE):**
  - _Race #4_ — `reaped` guard dirambah ke `get_result` DEAD + max-runtime path (`fail_job(..., reaped=True)`), cegah late `failed→completed` flip dari worker yang masih hidup [core/job_manager.py].
  - _Tail-scan classifier_ — `_error_tail()` (1600 char) batasi keyword rate-limit/stream ke **tail** error opencode saja, hilangkan false-positive dari transkrip agent yang echo komentar kode berbunyi "rate limit" [adapters/opencode_adapter.py].

**Ringkasan verdict:**

| Fitur             | Status                            | Enforcement        |
| ----------------- | --------------------------------- | ------------------ |
| 7 watchdog/reap   | Stabil penuh (diperkuat sesi ini) | Runtime            |
| 2 auto-upgrade    | Stabil, manual by design          | Runtime            |
| 3 OPTIONS block   | Stabil sebagai kontrak            | Prompt-only        |
| 4 auto_verify     | Stabil sebagai kontrak            | Prompt-only        |
| 6 auto intent     | Stabil sebagai kontrak            | Prompt-only        |
| 5 graph leads     | Degrade senyap saat basi          | Runtime + external |
| 1 subagent fanout | Rapuh + disabled di repo ini      | Runtime + external |

Risk keseluruhan: **medium** — nol blocking, tapi 2 titik degrade-senyap (graph staleness, subagent external-dep) tak terdeteksi runtime.

---

## 6. Apa yang diharapkan (kontrak & ekspektasi)

- **Output contract stabil:** setiap delegated call balik `{ok, content, meta, digest}`. `ok:false` = proxy gagal → main_agent WAJIB HARD GATE (STOP, tanya user, **jangan** auto-fallback).
- **Dua mode output main_agent:**
  - RELAY (explore/sweep/doctor) — teruskan `digest`.
  - SYNTHESIS (plan/analyze) — main_agent reasoning sendiri, isi `[PLAN]`/`[ANALYSIS RESULT]` penuh dengan confidence (3 sub) + atribusi tiap klaim.
- **Read-only invariant:** second_agent tak boleh tulis file. Ditegakkan via `[CONSTRAINTS]` prompt + role.
- **Session isolation:** `MAIN_SESSION_ID` per project root; concurrent same-project tak saling timpa.
- **"implemented" ≠ "done":** tanpa verify, main_agent tak boleh klaim sukses.

---

## 7. Mengapa desain ini dipilih

| Keputusan                                        | Alasan                                                                                                                                                                                                    |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Pisah main/second agent**                      | main_agent hemat context: tak perlu baca seluruh codebase, cukup terima digest ter-ground. second_agent read-only → nol risiko tulis liar.                                                                |
| **Zero dependency**                              | Portable, tak ada supply-chain surface, install cuma butuh Python + opencode.                                                                                                                             |
| **Job detached + side-file heartbeat**           | Call panjang (LLM lambat) tak blokir; heartbeat side-file hindari race dengan mutasi job record [job_manager.py:85-105].                                                                                  |
| **Dual-signal liveness + atomic reap**           | LLM bisa "hidup tapi diam" (stream idle). PID-alive saja menipu ("heartbeat semu"); idle-stream + reaped guard bedakan progressing vs stalled vs dead.                                                    |
| **Prompt-contract untuk sebagian fitur**         | `/.execute`, `[OPTIONS]`, intent-detection tak punya jalur Python — sengaja diserahkan ke disiplin main_agent (didokumentasikan eksplisit sebagai `UNENFORCEABLE_PROMPT_CONTRACTS`, bukan disembunyikan). |
| **graphify sebagai starting point, bukan bukti** | Graph edge mempercepat penelusuran, tapi prompt tegaskan "never a substitute for reading the file" — cegah halusinasi dari graph basi.                                                                    |
| **dist/ ter-generate dari ~/.claude**            | Single source of truth; `extract_config.py` (allowlist) + manifest LF-hash → reproducible, checkout-independent.                                                                                          |

---

## 8. Kekurangan & risiko

### Runtime

- **Feature 1 (subagent fanout):** nol verifikasi opencode punya sub-agent tool. Kalau tool absen, prompt asumsi bisa salah. Mitigasi ada (`detect_subagent_usage`) tapi reaktif, bukan preventif.
- **Feature 5 (graph staleness):** graph basi cuma WARNING, nol auto-regen. Leads bisa menyesatkan senyap. Repo ini sendiri graph-nya basi.
- **Kalibrasi timing:** `idle_stall=240s`, `probe_recheck=120s`, `job_max_runtime=5400s` — default, belum tervalidasi lintas provider (latency provider beda-beda). `[needs-calibration]`

### Kontrak prompt (unenforceable)

- Feature 3, 4, 6 bergantung penuh disiplin main_agent. Kalau prompt/CLAUDE.md tak ke-load atau agent lain dipakai tanpa managed-block, fitur hilang tanpa error runtime.

### Portabilitas

- `run.sh` menyimpan path Windows hardcoded → tak portable ke WSL/Linux murni (dicatat, belum difix).
- `job_id` inkonsistensi tipe str/int di beberapa titik [job_manager.py:38,45] — kosmetik, nol runtime impact.

### Operasional

- Auto-upgrade manual — user bisa lupa jalankan `install.py --apply` → drift antara `dist/` dan `~/.claude`.
- `.workflow/config.json` repo ini masih punya `subagent_fanout_enabled=false` + key `context_compression` usang (forbidden path, tak diubah).

---

## 8.5 Edge cases (per fitur — skenario konkret)

Tabel: **trigger** (kondisi pemicu) → **perilaku sekarang** → **risiko**.

### (1) Sub-agent fanout

| Trigger                                        | Perilaku sekarang                                                              | Risiko                                                                                         |
| ---------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| opencode versi tanpa sub-agent/task tool       | Prompt tetap emit `[SUBAGENT_PLAN]`, agent abaikan / jawab tanpa fanout        | `detect_subagent_usage()` flag mismatch — reaktif, tak cegah waktu kebuang [contract.py:67-98] |
| Graph punya <2 cluster                         | Fallback ke `_BLIND_SLICES` (slice buta tanpa graph) [prompt_builder.py:37-71] | Fanout kualitas rendah — tak ada community guidance                                            |
| `subagent_fanout_enabled=false` (repo ini)     | Fitur mati total, single-agent                                                 | Diam — tak ada sinyal ke user bahwa fanout off                                                 |
| Agent klaim `subagents:` tapi tanpa `[cN]` tag | Mismatch di-flag [executor.py:235-239]                                         | Output bisa parsial, main_agent harus deteksi                                                  |

### (2) Auto-upgrade

| Trigger                                      | Perilaku sekarang                                                                | Risiko                                                            |
| -------------------------------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Ada job pending/running saat upgrade         | `active_jobs_for_workspace()` deteksi → **refuse** [workflow_runtime.py:374-403] | Aman — cegah corrupt mid-job                                      |
| Worker DEAD tapi job record belum reaped     | Dikecualikan dari "active" (cek DEAD)                                            | Kalau deteksi DEAD meleset → upgrade bisa jalan atas job zombie   |
| User tak pernah jalankan `install.py`        | Workspace tetap versi lama, `needs_upgrade()=true` tapi nol auto-trigger         | Drift senyap — fitur baru tak aktif                               |
| Config user punya key custom di luar default | `upgrade` merge default, backfill opencode key                                   | Key usang (mis. `context_compression`) tak dibersihkan — menumpuk |

### (3) `[OPTIONS]` block

| Trigger                                       | Perilaku sekarang                                                        | Risiko                                    |
| --------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------- |
| main_agent lupa emit `[OPTIONS]`              | Nol enforcement Python — lolos tanpa error [workflow_runtime.py:149-156] | Kontrak dilanggar senyap                  |
| Agent lain (bukan Claude) tanpa managed-block | STEP 2b tak ke-load → tak ada OPTIONS                                    | Fitur hilang total tanpa sinyal           |
| Task sebenarnya cuma 1 pendekatan valid       | Prompt paksa max 3 opsi                                                  | Opsi ke-2/3 bisa dipaksakan / low-quality |

### (4) `auto_verify_after_execute`

| Trigger                                  | Perilaku sekarang                                          | Risiko                               |
| ---------------------------------------- | ---------------------------------------------------------- | ------------------------------------ |
| `false` + main_agent tetap bilang "done" | Nol enforcement — bergantung disiplin                      | Klaim sukses palsu tanpa verify      |
| `true` tapi verify berat/lama            | Auto-trigger `/.verify` tiap execute                       | Test berat tak diminta jalan — mahal |
| Config key absen (workspace lama)        | `default_commands()` isi `false` [workflow_runtime.py:168] | Aman — default konservatif           |

### (5) graph leads + context-mode

| Trigger                                              | Perilaku sekarang                                                        | Risiko                                                         |
| ---------------------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------- |
| `graph.json` basi vs source                          | `is_stale()` inject WARNING, leads tetap dipakai [graph_index.py:67-103] | **Degrade senyap** — file relevan bisa hilang / phantom muncul |
| `graph.json` tak ada sama sekali                     | Leads kosong, prompt jalan tanpa candidate_files                         | Second_agent mulai buta — lebih lambat, coverage turun         |
| Prompt + leads > 8191 char (Windows)                 | `_compact_leads()` truncate untuk verify [prompt_builder.py:110-125]     | Lead penting bisa kepotong di command panjang                  |
| context-mode di-enable global tapi project tak butuh | Plugin aktif untuk semua project [install.py:226-269]                    | Overhead / perilaku tak diinginkan lintas-project              |

### (6) Auto intent detection

| Trigger                                            | Perilaku sekarang                                | Risiko                                                              |
| -------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------- |
| Kalimat ambigu (dua command cocok)                 | Tie-break: pilih lebih murah (local > delegated) | Bisa salah tebak → command salah jalan                              |
| Pertanyaan biasa mengandung 1 kata trigger         | Aturan: jangan paksa ke command                  | Bergantung disiplin — bisa salah-fire delegated (bakar menit/kuota) |
| Managed-block CLAUDE.md tak ke-load                | Nol deteksi — cuma `workflow_prefix` di config   | Fitur hilang, user harus `/.` manual                                |
| Task destruktif (commit/hapus) dari intent tebakan | Aturan: **jangan** auto-fire, konfirmasi dulu    | Kalau aturan dilanggar → aksi ireversibel                           |

### (7) Watchdog / reap (paling banyak edge case ditangani)

| Trigger                                                    | Perilaku sekarang                                                                     | Risiko                                                       |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| opencode limit habis mid-stream                            | `check_stalled_job()` fresh-PING → klasifikasi `rate_limited`, reap [main.py:322-403] | Ditangani ✓                                                  |
| Stream idle >240s tapi PID hidup ("heartbeat semu")        | `_idle_seconds()` deteksi idle → `ALIVE_STALLED` [job_manager.py:325-346]             | Ditangani ✓ — idle dicek sebelum PID                         |
| Reap check 1-menit terlewat                                | Re-probe cadence 120s **berulang**, bukan sekali [main.py:430-438]                    | Ditangani ✓ — inti fix v3.4.0                                |
| max-runtime (5400s) fire tapi worker masih hidup           | `get_result` max-runtime → `fail_job(reaped=True)` block late flip (**race #4 fix**)  | Ditangani ✓ (sesi ini)                                       |
| Worker echo komentar kode berbunyi "rate limit"            | `_error_tail()` batasi scan ke tail 1600 char (**tail-scan fix**)                     | Ditangani ✓ (sesi ini) — no false-positive                   |
| Rate-limit signature ada di transkrip >1600 char dari tail | Diklasifikasi `unknown` (fail-closed)                                                 | Tradeoff diterima — jarang, aman                             |
| Transient stream drop (bukan limit nyata)                  | Klasifikasi `streaming_failed` (transient), bukan rate_limited                        | Ditangani ✓ — dibedakan dari limit                           |
| `probe` sendiri kena rate-limit saat PING                  | Timeout branch cek `_is_rate_limited(_error_tail(...))`                               | Ditangani ✓                                                  |
| 2 main_agent concurrent same-project tanpa session_id      | Fallback sesi "default" → job saling block/timpa                                      | **Belum ditangani penuh** — wajib teruskan `MAIN_SESSION_ID` |

---

## 9. Rekomendasi tindak lanjut

1. **`graphify update`** — tutup graph basi (Feature 5 degrade-senyap).
2. **Runtime detection sub-agent tool** — tambah probe kapabilitas opencode sebelum emit fanout prompt (Feature 1).
3. **Kalibrasi timing** — ukur latency provider nyata, sesuaikan `idle_stall`/`probe_recheck`.
4. **`install.py --apply`** — propagate `dist/` → `~/.claude` biar tak drift.
5. **Portabilitas `run.sh`** — resolusi path dinamis untuk WSL/Linux.

---

_Laporan digenerate 2026-07-24. Grounded via analisa second_agent (confidence: high) + fix terverifikasi sesi ini (race #4, tail-scan classifier — verify DONE)._
