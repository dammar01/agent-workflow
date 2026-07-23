# Changelog — v3.3.1

Rilis konsolidasi. Nol fitur agent baru; yang berubah adalah **kontrak yang ditegakkan runtime** dan **higiene fact store**.

## Fase 0 — perbaikan data (P0)

### Fact store tak lagi memperkuat dirinya sendiri
`_recurrence_counts()` sebelumnya memindai SEMUA direktori sesi, termasuk sesi yang sedang berjalan. Karena `write_response_snapshot()` menulis `output.raw.md` **sebelum** `fact_store.ingest()` dipanggil, sebuah fakta yang diinjeksi ke prompt sebagai `[KNOWN_FACTS]` lalu di-echo ulang oleh second_agent bisa menaikkan hitungan recurrence-nya sendiri — mencapai ambang promosi tanpa satu pun bukti independen.

Sekarang `_recurrence_counts(project_root, exclude_session_id=...)` membuang sesi berjalan. Promosi hanya dari sesi LAIN.

### Dedup semantik
Kunci dedup lama adalah string ternormalisasi persis, jadi dua kalimat berbeda untuk fakta yang sama (anchor file identik) tersimpan dua kali dan diinjeksi dua kali. Sekarang dua klaim dengan `file` sama dilebur bila irisan kata (Jaccard) ≥ `DUPLICATE_SIMILARITY` (0.5). Berlaku di `ingest()`, `_save_facts()`, `load_relevant()`, dan `prune()`.

Kemiripan kata saja bukan identitas — ia dengan senang hati melebur sebuah klaim dengan negasinya sendiri. Collapse hanya terjadi bila **semua** pagar setuju:

| Pagar | Alasan |
|---|---|
| `file` sama | dasar |
| `category` sama | nilai `[config]` dan `[invariant]` bukan hal yang sama |
| `anchor_hash` identik | lihat catatan di bawah |
| polaritas negasi sama | "X di-cache" vs "X **tidak** di-cache" beririsan hampir sempurna, artinya berlawanan |
| Jaccard ≥ 0.5 | |
| kedua klaim ≥ 6 kata | "validates token" vs "handles token" beririsan 0.5 secara kebetulan |

Satu pagar menolak → **simpan dua-duanya**. Duplikat murah; fakta hilang tak bisa dikembalikan.

Kedekatan baris (`LINE_PROXIMITY`) adalah sinyal identitas yang lemah, jadi dipakai **hanya** untuk dedup saat baca (`load_relevant`) yang sekadar memangkas apa yang diinjeksi. Collapse persisten — yang menghapus record dari `facts.jsonl` — selalu menuntut `anchor_hash` identik.

Pagar `status: contradicted` ditunda ke Fase 2 bersama field `status`; record fact sekarang belum punya field itu.

`prune()` kini mengembalikan `duplicates_collapsed` selain `kept`/`removed`.

## Fase 1 — konfigurasi

### `commands.verify_mode` + `commands.auto_verify_after_execute`
Satu boolean dulu mencampur dua keputusan berbeda. Sekarang dipisah:

```json
"commands": {
  "auto_verify_after_execute": false,
  "verify_mode": "delegated"
}
```

- `verify_mode` — **sedalam apa** `/.verify` bekerja ketika ia jalan. `delegated` = verifikasi penuh second_agent. `syntax` = dijawab lokal oleh `core/quick_verify.py`: check parse atas file berubah (`git diff --name-only HEAD` + untracked), plus name check opsional bila `pyflakes` tersedia. Nol test suite, nol panggilan opencode. Nilai tak dikenal jatuh ke `delegated` — setting yang tak jelas tak boleh diam-diam menurunkan verifikasi.
- `auto_verify_after_execute` — **apakah** `/.execute` memanggil `/.verify` sendiri. Default `false`, supaya tak ada test berat berjalan tanpa diminta. Ketika `false`, `/.execute` wajib melapor `verification: not_run` dengan status `implemented`, dan **dilarang** memakai kata "done" — tanpa verifikasi, "bekerja" itu belum diketahui.

`auto_verify_after_execute` **prompt-only**: `/.execute` tidak punya jalur Python sama sekali (ditolak argparse, ditolak Router), jadi runtime ini tak bisa menegakkannya. Hanya disiplin main_agent yang bisa.

Batas yang sengaja dilaporkan eksplisit, bukan disembunyikan:
- bahasa tanpa checker → `not_checked`
- toolchain tak ada di PATH → `skipped`
- file > `MAX_FILE_BYTES` (2 MB) → `not_checked`
- `pyflakes` absen → `name_check: unavailable`

Python diperiksa in-process via `compile()`, bukan `py_compile`: yang terakhir menjatuhkan `.pyc` ke pohon kerja user. Command verifikasi tak boleh meninggalkan artefak. `__pycache__`, `node_modules`, `.git`, `vendor`, `.venv`, `venv` dilewati.

`verdict: pass` berarti file parse. Bukan berarti fitur bekerja.

### `/.verify` punya kontrak output — severity-tiered
Sebelumnya role `verification` tidak menerima `[OUTPUT_FORMAT]` sama sekali; prompt-nya hanya "Return the requested result only". Bentuk laporan verify sepenuhnya bergantung pada bagaimana main_agent menyusun teks task — jadi tidak pernah benar-benar sebuah kontrak.

Sekarang `build_prompt()` mengirim `[VERIFICATION]` + `[DIGEST]` untuk command `verify`. Setiap temuan wajib membawa **tiga** tag, bukan satu:

```
severity:       critical | high | medium | low
origin:         introduced | regression | pre_existing | unknown
scope_relation: in_scope | out_of_scope
```

Severity sendirian tidak memutuskan blocking — sebuah lubang security yang sudah ada sebelum perubahan ini tidak boleh menyandera verdict perubahan ini, dan sebuah defect medium yang muncul di luar scope adalah sinyal berbeda dari defect medium yang di dalam scope. Rutenya:

| origin | scope_relation | critical/high | medium/low |
|---|---|---|---|
| introduced / regression | in_scope | **blocking** | note |
| introduced / regression | out_of_scope | **blocking** (+ pelanggaran scope) | **escalation** |
| unknown | apa pun | **blocking** (fail closed) | note |
| pre_existing | apa pun | **escalation** | note |

`verdict: NEEDS FIX` hanya bila `blocking_findings` tidak kosong.

Section **`escalations`** baru: masalah critical/high yang tidak memblokir verdict tapi tetap harus dilihat user. Tanpa ini, lubang security pre-existing tenggelam di antara catatan penamaan variabel.

`origin: unknown` sengaja fail-closed. "Belum tahu ini saya buat atau bukan" bukan jalan lolos — untuk turun dari `unknown` wajib menyebut bukti (diff, git history, versi sebelumnya); tidak bisa, tetap memblokir.

Pagar anti-manipulasi sever: temuan tanpa `file:line` dan skenario gagal konkret tidak boleh critical/high. Pagar itu soal mutu evidence, bukan alat meredam masalah sistemik — defect yang tersebar di banyak lokasi tetap critical/high, dengan `file:line` perwakilan dan pernyataan seberapa luas. Defect yang sudah ada sebelum perubahan ditandai `[pre-existing]` dan tidak memblokir verdict perubahan ini. `checks_run` dan `not_verified` wajib — cek yang tidak dijalankan bukan pass.

Format ini dipasang per-**command** (`verify`), bukan per-role: `ROLE_VERIFICATION` juga dipakai `init`/`doctor`/`sweep`/`submit`/`status`/`result`, yang tetap memakai prompt fallback ringkas.

Penegakannya masih di sisi prompt. Validasi runtime atas sever dan verdict masuk Fase 2 bersama enum confidence.

### Migrasi config additive
`ensure_valid_json_or_create()` hanya menulis config bila BELUM ada, sehingga project yang sudah di-init tak pernah menerima key baru dari versi berikutnya. `merge_config_defaults()` sekarang mem-backfill key yang hilang di `commands` dan `policies` saat `load_workspace_state()`, lalu menulis ulang secara atomik. **Nilai yang sudah diisi user tidak pernah ditimpa.**

Migrasi juga menulis ulang key yang dipensiunkan: `commands.autoverify` dipetakan ke `verify_mode` (`true→delegated`, `false→syntax`) lalu dibuang. Backfill aditif saja tidak cukup di sini — ia akan meninggalkan key lama berdampingan dengan penggantinya, dan user tak bisa tahu mana yang dipatuhi runtime.

### Knob fact store dapat di-tune per project
| Key | Default | Sebelumnya |
|---|---|---|
| `policies.fact_relevant_limit` | 3 | 8 (konstanta) |
| `policies.fact_recurrence_threshold` | 5 | 5 (konstanta) |

Lebih sedikit fakta diinjeksi ke tiap prompt. Ambang recurrence **tetap 5** — sempat diturunkan ke 3, lalu dikembalikan: menurunkan ambang promosi tanpa data pendukung berarti menaruh lebih banyak kepercayaan pada mekanisme yang baru saja terbukti bisa dibohongi.

### Key mana yang benar-benar dibaca runtime
Hanya 3 dari 11 key config punya konsumen Python: `commands.verify_mode`, `policies.fact_relevant_limit`, `policies.fact_recurrence_threshold` — didaftar di `RUNTIME_CONSUMED_KEYS`. Delapan sisanya instruksi untuk main_agent saja. `default_commands()`/`default_policies()` sekarang menandainya, supaya "sudah dikonfigurasi" tidak disalahartikan sebagai "sudah ditegakkan".

Salah ketik nama key tetap tidak terdeteksi: `.get()` jatuh diam-diam ke default. Belum ada test yang mengunci nama key.

## File

| File | Perubahan |
|---|---|
| `core/fact_store.py` | exclude-session, dedup semantik, knob dari config |
| `core/workflow_runtime.py` | `CONFIG_VERSION`, `VERIFY_MODES`, `RUNTIME_CONSUMED_KEYS`, `default_commands()`, `default_policies()`, `merge_config_defaults()`, `_rewrite_superseded_keys()`, `verify_mode()` |
| `core/prompt_builder.py` | kontrak `[VERIFICATION]` untuk command `verify` (`_verification_format()`) |
| `core/quick_verify.py` | baru |
| `core/executor.py` | cabang `verify` + `verify_mode=syntax` |
| `prompt/v3.3.1/*` | skill verify (mode), design notes, managed block |

## Belum dikerjakan (tetap terbuka)

Ditunda dengan sadar, bukan terlewat:

- **Fase 2** — enum confidence, `critical_claims`, `blocking_uncertainties`, state machine fact (`candidate/active/stale/contradicted/superseded`), pemisahan `fact`/`decision`, persist `unresolved`/`deviations`/`pending_user_decisions`.
- **Fase 3** — smoke test real CLI (`WORKFLOW_E2E=1`), telemetry per-run.
- **v3.4 (layer hook)** — scope audit, destructive-operation guard, mutation attribution, exact-user-case verification. Semua ini butuh `PreToolUse` hook Claude Code; runtime Python tidak pernah melihat mutation main_agent, jadi tak bisa ditegakkan dari sini.

Sampai layer hook ada, klaim "tidak ada mutation di luar scope" dan "operasi destruktif tidak bisa jalan diam-diam" **belum berlaku**.
