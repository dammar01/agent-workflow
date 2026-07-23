# agent-workflow v3.3.1

Runtime orkestrasi mandiri untuk alur kerja dua-agent. Tanpa dependency pihak ketiga.

## Ringkas

Dua peran dipisah tegas:

- **main_agent** — agent yang kamu pakai (Claude Code, Codex, Cursor, dll). Orchestrator, antarmuka user, dan **satu-satunya** yang boleh menulis file.
- **second_agent** — OpenCode, pengumpul bukti **read-only**. Bukan jawaban akhir.

Runtime ini duduk di antara keduanya: menerima command, merakit prompt terstruktur, menjalankan `opencode run`, memvalidasi bentuk output, menyimpan state per-sesi, lalu mengembalikan JSON contract yang stabil:

```json
{ "ok": true, "content": "...", "meta": {}, "digest": {} }
```

Semua state project-local hidup di `.workflow/` pada project target — bukan di repo ini.

---

## Prasyarat

| Kebutuhan | Catatan |
|---|---|
| Python 3.10+ | sintaks `X \| None` dipakai di seluruh kode |
| `opencode` di `PATH` | hanya untuk command terdelegasi |
| `git` di `PATH` | opsional; dipakai `sweep` dan mode verify `syntax` |
| Dependency | **nol**. `requirements.txt` sengaja kosong |

Cek cepat:

```bash
python3 --version   # atau: python --version
opencode --version
git --version
```

```powershell
python --version
opencode --version
git --version
```

---

## Bootstrap

`main.py` tinggal di repo ini, **bukan** di project target. Untuk init pertama, runtime perlu tahu letaknya. Sesudah init, `.workflow/config.json` menyimpan path absolutnya, jadi `AGENT_PATH` tak wajib lagi.

**Windows (persisten, jalankan sekali):**

```powershell
[Environment]::SetEnvironmentVariable("AGENT_PATH", "C:/path/to/agent-workflow/main.py", "User")
```

Tutup dan buka kembali terminal supaya variabel aktif.

**Windows (sesi berjalan saja):**

```powershell
$env:AGENT_PATH = "C:/path/to/agent-workflow/main.py"
```

**macOS / Linux (persisten):**

```bash
echo 'export AGENT_PATH="$HOME/path/to/agent-workflow/main.py"' >> ~/.bashrc   # atau ~/.zshrc
source ~/.bashrc
```

**macOS / Linux (sesi berjalan saja):**

```bash
export AGENT_PATH="$HOME/path/to/agent-workflow/main.py"
```

Verifikasi:

```bash
test -f "$AGENT_PATH" && echo OK
python3 "$AGENT_PATH" --help
```

```powershell
Test-Path $env:AGENT_PATH
python $env:AGENT_PATH --help
```

---

## Init di project target

```bash
python3 "$AGENT_PATH" --command init --work-dir /path/to/target-app --pretty
```

```powershell
python $env:AGENT_PATH --command init --work-dir "C:/path/to/target-app" --pretty
```

`init` bersifat idempoten dan hanya membuat scaffolding statis:

- `.workflow/config.json` — path absolut `main.py`/`check.py`, sehingga `.workflow/` **self-contained**
- `.workflow/opencode.json` — salinan project-local, boleh kamu ubah
- skrip runner untuk **kedua** platform: `run.ps1` `run.sh` `inspect.ps1` `inspect.sh` `check.ps1` `check.sh`
- `.workflow/sessions/` kosong — state per-sesi dibuat lazy saat panggilan terdelegasi pertama
- entri `.workflow/` ditambahkan ke `.gitignore` root project

Skrip `.sh` diberi bit executable saat dibuat. Kalau repo dipindah lewat media yang membuang mode bit:

```bash
chmod +x .workflow/*.sh
```

**Batasan portabilitas.** Kedua set skrip memang selalu ditulis, tapi keduanya memanggang **path absolut dari mesin tempat `init` dijalankan**. Yang generik hanya nama interpreter: skrip untuk OS saat init memakai path Python persis yang terdeteksi, skrip lintas-OS memakai `python`/`python3` yang di-resolve lewat `PATH`. Path ke `main.py` dan `--work-dir` tetap absolut dan spesifik-OS.

Konsekuensinya: `run.sh` yang dihasilkan di Windows berisi path bergaya `C:\...` dan **tidak akan jalan** di Linux/macOS, begitu pula sebaliknya. Ketika satu project dipakai dari OS berbeda — termasuk WSL versus Windows asli — jalankan ulang `init` di OS itu untuk menulis ulang skrip. `init` aman diulang: config, `opencode.json`, dan seluruh state per-sesi tidak disentuh.

---

## Pemakaian harian

main_agent memanggil **satu** skrip runner — tidak merakit command Python sendiri.

```powershell
& "C:/path/to/target-app/.workflow/run.ps1" explore "cari entry point auth" "<MAIN_SESSION_ID>"
```

```bash
/path/to/target-app/.workflow/run.sh explore "cari entry point auth" "<MAIN_SESSION_ID>"
```

Argumen ketiga adalah session id. **Wajib** diteruskan: tanpa itu semua pemanggil jatuh ke sesi `"default"` yang sama, dan dua main_agent di project yang sama akan saling menimpa state serta saling memblokir job. Skrip mencetak peringatan ke stderr bila ini terjadi.

Skrip bersifat blocking dan mengembalikan JSON yang sama seperti CLI.

---

## Command

| Command | Jenis | Butuh prompt | Keterangan |
|---|---|---|---|
| `init` | lokal | — | buat/regenerate `.workflow/` |
| `doctor` | lokal | — | cek kesiapan, tulis `reports/doctor.json` |
| `clean` | lokal | — | prune job, fakta usang/duplikat, sesi lama |
| `inspect` | lokal | — | daftar job untuk sesi berjalan |
| `explore` | terdelegasi | ya | peta codebase, entry point, pemilik |
| `plan` | terdelegasi | ya | evidence + jejak dependency terbalik |
| `analyze` | terdelegasi | ya | analisis mendalam, nol perubahan kode |
| `verify` | terdelegasi¹ | ya | verifikasi; kedalaman diatur `verify_mode` |
| `sweep` | terdelegasi | ya | pindai `git diff` → bukti dampak |
| `submit` | job | ya | jalankan asinkron, kembalikan `job_id` |
| `await` | job | ya | submit lalu tunggu selesai |
| `status` | job | — | butuh `--job-id` |
| `result` | job | — | butuh `--job-id` |
| `worker` | internal | — | dipakai proses worker, jangan dipanggil manual |

¹ `verify` melewati OpenCode sepenuhnya ketika `verify_mode` bernilai `syntax`.

Tidak ada command `execute`. Menulis kode adalah domain main_agent — runtime ini sengaja tak punya jalur untuk itu.

---

## CLI langsung

Berguna untuk debugging; alur normal cukup lewat skrip runner.

```bash
python3 main.py -c explore -p "cari entry point auth" -s "main_app_20260723_090000" -w /path/to/target-app --pretty
```

```powershell
python main.py -c explore -p "cari entry point auth" -s "main_app_20260723_090000" -w "C:/path/to/target-app" --pretty
```

| Argumen | Alias | Arti |
|---|---|---|
| `--command` | `-c` | lihat tabel command |
| `--prompt` | `-p` | task |
| `--prompt-file` | | baca task dari file (alternatif `--prompt`) |
| `--session` | `-s` | id sesi main_agent |
| `--fresh-session` | | paksa sesi baru, abaikan cache |
| `--work-dir` | `-w` | root project target (default: cwd) |
| `--model` | `-m` | override model, format `provider/model_key` |
| `--job-id` | | untuk `status`/`result`/`worker` |
| `--job-command` | | command yang dijalankan `submit`/`await` |
| `--poll-interval` | | detik antar polling saat `await` |
| `--poll-timeout` | | batas tunggu `await`; `0` = tanpa batas |
| `--pretty` | | JSON ber-indent |

`--prompt-file` menghindari masalah escaping shell pada task panjang atau multi-baris — persoalan yang bentuknya berbeda di PowerShell dan di POSIX shell.

---

## Konfigurasi

### `.workflow/config.json`

Dibuat saat `init`. Key baru dari versi berikutnya di-backfill otomatis saat pemanggilan berikutnya; **nilai yang sudah kamu isi tak pernah ditimpa**.

Hanya 3 dari 11 key yang benar-benar dibaca runtime Python. Delapan sisanya instruksi untuk main_agent — nyata bagi agent, tapi inert di sini. Pembedaan ini ditandai langsung di kode (`RUNTIME_CONSUMED_KEYS`) supaya "sudah dikonfigurasi" tak disalahartikan sebagai "sudah ditegakkan".

**Dibaca runtime:**

| Key | Default | Arti |
|---|---|---|
| `commands.verify_mode` | `"delegated"` | `delegated` = verifikasi penuh second_agent. `syntax` = check parse lokal saja. Nilai tak dikenal jatuh ke `delegated` |
| `policies.fact_relevant_limit` | `3` | maksimum fakta yang diinjeksi ke tiap prompt |
| `policies.fact_recurrence_threshold` | `5` | jumlah sesi **lain** yang harus melaporkan klaim sebelum dipromosikan |

**Prompt-only** (dipatuhi main_agent, bukan runtime): `commands.auto_verify_after_execute`, `commands.allow_analyze_to_plan`, `commands.allow_explore_to_plan`, `commands.auto_sweep_after_execute`, `policies.workflow_prefix`, `policies.chat_mode_for_plain_text`, `policies.fallback_requires_confirmation`, `policies.max_active_job_per_session`.

Salah ketik nama key tidak menimbulkan error — nilainya diam-diam jatuh ke default. Belum ada test yang mengunci nama key.

### `.workflow/opencode.json`

Salinan project-local dari `config/opencode.json`, boleh diubah per project:

```json
{
  "opencode_command": "opencode",
  "default_model": "opencode/deepseek-v4-flash-free",
  "timeout_seconds": null,
  "routes": {
    "explore": { "model": "opencode/deepseek-v4-flash-free" },
    "plan":    { "model": "opencode/deepseek-v4-flash-free" },
    "analyze": { "model": "opencode/deepseek-v4-flash-free" },
    "verify":  { "model": null },
    "sweep":   { "model": null }
  }
}
```

`model: null` berarti pakai model default OpenCode. `timeout_seconds: null` berarti menunggu tanpa batas.

**Role tidak dibaca dari file ini.** Pemetaan command → role ditentukan di kode (`config/routing.py`), jadi tak bisa ditumpuk lewat config.

---

## Mode verify

`verify_mode` mengatur **sedalam apa** `/.verify` bekerja:

- **`delegated`** (default) — second_agent memverifikasi dan mengembalikan kontrak berlabel. Tiap temuan wajib membawa tiga tag: `severity` (critical/high/medium/low), `origin` (introduced/regression/pre_existing/unknown), `scope_relation` (in_scope/out_of_scope). Blocking ditentukan kombinasi ketiganya, bukan severity saja — cacat pre-existing tidak menyandera verdict perubahan berjalan, dan `origin: unknown` gagal-tertutup.
- **`syntax`** — dijawab lokal, tanpa memanggil OpenCode sama sekali. Memeriksa parse file yang berubah (`git diff --name-only HEAD` + untracked): `.py` via `compile()` in-process, `.json` via `json.loads`, `.js`/`.mjs`/`.cjs` via `node --check`, `.php` via `php -l`. Name check opsional lewat `pyflakes` bila terpasang.

Semua yang tak bisa diperiksa dilaporkan apa adanya, tidak pernah dihitung lulus:

| Keluaran | Arti |
|---|---|
| `not_checked` | tak ada checker untuk ekstensi itu, atau file > 2 MB |
| `skipped` | toolchain bahasa tak ada di `PATH` |
| `name_check: unavailable` | `pyflakes` tak terpasang — hanya syntax yang diperiksa |

Python diperiksa in-process, bukan lewat `py_compile`, supaya tak ada `.pyc` yang tertinggal di pohon kerjamu. Direktori `__pycache__`, `node_modules`, `.git`, `vendor`, `.venv`, `venv` dilewati.

`verdict: pass` berarti berkas ter-parse. **Bukan** berarti fiturnya bekerja.

---

## Fact store

`.workflow/facts.jsonl` menyimpan pengetahuan yang bertahan lintas sesi. Sebuah klaim masuk lewat salah satu dari dua jalur:

1. ditandai eksplisit `[config]`/`[pattern]`/`[invariant]` oleh second_agent, atau
2. dilaporkan secara mandiri oleh ≥ `fact_recurrence_threshold` sesi **lain**.

Sesi yang sedang berjalan dikecualikan dari hitungan recurrence. Tanpa itu, sebuah fakta yang diinjeksi ke prompt lalu sekadar digemakan kembali bisa menaikkan hitungannya sendiri sampai ambang promosi — pengulangan menyamar jadi bukti.

Tiap fakta ditambatkan ke hash isi baris `file:line`. Ketika baris itu berubah, fakta dianggap usang saat dibaca dan tak pernah disajikan sebagai segar.

Dua klaim dilebur hanya bila **semua** pagar setuju: `file` sama, `category` sama, `anchor_hash` identik, polaritas negasi sama, kemiripan Jaccard ≥ 0.5, dan kedua klaim ≥ 6 kata. Satu pagar menolak → dua-duanya disimpan. Duplikat itu murah; fakta yang hilang tak bisa dikembalikan.

Bersihkan yang usang dan duplikat:

```bash
python3 main.py --command clean --work-dir /path/to/target-app --pretty
```

---

## Layout workspace

```text
<target-app>/.workflow/
├─ config.json              # statis, dibuat saat init
├─ opencode.json            # salinan project-local
├─ facts.jsonl              # fact store lintas sesi
├─ .gitignore
├─ run.ps1  run.sh          # entry point 1-panggilan
├─ inspect.ps1  inspect.sh  # daftar job
├─ check.ps1  check.sh      # status/hasil job
├─ reports/
│  └─ doctor.json
└─ sessions/<session_id>/   # dibuat lazy per sesi
   ├─ state.json
   ├─ scope.json
   ├─ command-cache.json
   ├─ runtime/
   │  ├─ prompt.txt
   │  ├─ prompt.meta.json
   │  ├─ response.last.md
   │  └─ lock
   ├─ logs/<prompt_id>/
   │  ├─ prompt.md
   │  ├─ prompt.sha256
   │  └─ output.raw.md
   └─ reports/sweep.last.md
```

State yang berubah-ubah (`state`/`scope`/`cache`/`runtime`/`logs`) hidup di bawah `sessions/<id>/`, sehingga dua main_agent pada project yang sama tak pernah saling menimpa. Config dan reports tetap bersama di root `.workflow/`.

Data runtime milik tool sendiri ada di repo ini: `storage/sessions/` (pemetaan sesi → sesi OpenCode), `storage/jobs/` (job asinkron), `storage/cache.json`.

---

## Job asinkron & pemulihan

Command terdelegasi otomatis berjalan lewat worker terpisah. `await` submit lalu memblokir sampai selesai — inilah yang dipakai skrip runner.

Kalau pemanggilan terputus (timeout tool, tak ada JSON), **jangan langsung ulangi**: worker sudah terlepas dan tetap berjalan, tapi job yang sudah selesai tidak terambil sendiri.

```powershell
& ".workflow/inspect.ps1"
& ".workflow/check.ps1" <job_id> --wait --result
```

```bash
.workflow/inspect.sh
.workflow/check.sh <job_id> --wait --result
```

Exit code `check`: `0` selesai · `1` gagal · `2` masih jalan/antre · `3` tak ditemukan.

Kalau tak ada job yang cocok, hasil terakhir masih ada di `.workflow/sessions/<id>/runtime/response.last.md`.

---

## Sesi

`--session` adalah otoritas tunggal untuk binding sesi. Panggilan pertama bootstrap sesi OpenCode:

```text
opencode run <prompt> --print-logs --log-level INFO
```

Runtime mengurai `session.id=ses_...` dari log dan menyimpannya. Panggilan berikutnya memakai ulang sesi itu:

```text
opencode run <prompt> -s <opencode_session_id>
```

Baris log OpenCode dan banner model dibuang dari `content`; isi jawaban asisten dipertahankan utuh.

---

## Test

```bash
python3 test_scenario.py
```

```powershell
python test_scenario.py
```

11 blok test dengan adapter palsu — tak ada satu pun yang memanggil `opencode` sungguhan. Cepat, tapi berarti perilaku subprocess nyata (timeout, retry, penangkapan sesi) belum tercakup. Smoke test CLI sungguhan masih di daftar tunggu.

Pemeriksaan tambahan:

```bash
python3 main.py --help
python3 main.py --command doctor --work-dir . --pretty
```

---

## Batasan yang diketahui

Disebut terbuka karena diam soal ini akan membuat runtime terlihat lebih menjamin daripada kenyataannya:

- **Mutasi main_agent tak terlihat oleh runtime ini.** `/.execute` tak punya jalur Python sama sekali. Audit scope, penjaga operasi destruktif, dan atribusi perubahan file karena itu belum ada — perlu lapisan hook di sisi main_agent.
- **Kontrak masih sebagian berbasis prompt.** Validasi bukti berupa pencocokan penanda; nilai `confidence` belum divalidasi runtime.
- **`facts.jsonl` dibaca-ubah-tulis tanpa lock.** Dua sesi yang ingest bersamaan bisa saling menimpa.
- **`config.json` yang hilang atau rusak** membuat command terdelegasi gagal dengan exception mentah, bukan error terstruktur.
- **Nol telemetry.** Token, durasi, dan jumlah pemanggilan tool tak dicatat, jadi klaim efisiensi apa pun belum bisa diukur.
- **Test 100% palsu.** Lihat bagian Test.
- **Skrip runner tidak portabel lintas-OS.** Path absolut dipanggang saat `init`; pindah OS berarti `init` ulang. Lihat bagian Init.

---

## Referensi

- Catatan rilis: `prompt/v3.3.1/changelog.md`
- Prompt setup main_agent: `prompt/v3.3.1/main_agent.md`
- Prompt setup second_agent: `prompt/v3.3.1/second_agent.md`
- Laporan teknis: `REPORT.md`
