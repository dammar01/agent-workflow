# agent-workflow v3.5.1

Runtime orkestrasi mandiri untuk alur kerja dua-agent. Tanpa dependency pihak ketiga.

## Ringkas

Dua peran dipisah tegas:

- **main_agent** — agent yang kamu pakai (Claude Code, Codex, Cursor, dll). Orchestrator, antarmuka user, dan **satu-satunya** yang boleh menulis file.
- **second_agent** — OpenCode, pengumpul bukti **read-only**. Bukan jawaban akhir.

Runtime ini duduk di antara keduanya: menerima command, merakit prompt terstruktur, menjalankan `opencode run`, memvalidasi bentuk output, menyimpan state per-sesi, lalu mengembalikan JSON contract yang stabil:

```json
{ "ok": true, "content": "...", "meta": {} }
```

`digest` ditambahkan hanya bila output second_agent membawa blok `[DIGEST]` yang dapat diparse; ia bukan field wajib.

Evidence, config, dan state per-sesi yang project-local hidup di `.workflow/` pada project target. Binding sesi OpenCode, antrean job, dan cache lintas project milik tool tetap berada di `storage/` repo ini.

---

## Alur prompt sampai response

```text
Prompt user
→ main_agent: auto-detect intent atau pakai override /.
→ .workflow/run.{ps1|sh}
→ main.py await → JobManager → detached worker
→ Executor → Router
→ Graphify leads + fact store → PromptBuilder
→ OpenCodeAdapter → second_agent
→ sub-agent fan-out bila aktif
→ validasi contract + digest + penyimpanan evidence
→ await mengembalikan {ok, content, meta, digest?}
→ main_agent relay/synthesis
→ response user
```

Auto-intent, output `[OPTIONS]` pada `/.plan`, dan keputusan menjalankan `/.verify` setelah `/.execute` berada di lapisan prompt **main_agent**. Runtime Python tidak menerima pesan user mentah dan tidak melihat response final yang ditulis main_agent. Runtime hanya mengelola delegasi, evidence, job, dan policy metadata.

Diagram ini menggambarkan jalur delegated penuh. `sweep` dipotong lebih awal oleh `main.run()` dan selesai lokal tanpa `Executor`; `verify` dengan `verify_mode: syntax` berhenti di `quick_verify` sebelum Router, Graphify, PromptBuilder, OpenCode, dan second_agent.

---

## Prasyarat

| Kebutuhan | Catatan |
|---|---|
| Python 3.10+ | sintaks `X \| None` dipakai di seluruh kode |
| `opencode` di `PATH` | hanya untuk command terdelegasi |
| `git` di `PATH` | opsional; dipakai `sweep` dan mode verify `syntax` |
| Dependency runtime | **nol**; seluruh runtime memakai standard library |
| Graphify | opsional |

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

## Install (v3.5.1)

### Anggota tim baru — urutan lengkap dari nol

Empat langkah, dijalankan sekali per mesin (kecuali langkah 4, sekali per project):

```bash
git clone <repo> && cd agent-workflow      # 1. ambil tool-nya
python install.py --apply                  # 2. pasang config agent global
export AGENT_PATH="$PWD/main.py"           # 3. beri tahu runtime letak main.py
                                           #    (permanenkan — lihat "Set AGENT_PATH")
python main.py --command init --work-dir /path/to/project-anda   # 4. per project
```

Verifikasi sebelum memakainya:

```bash
python install.py --check                  # bundle + instalasi global
cd /path/to/project-anda && .workflow/run.sh doctor    # .workflow\run.ps1 di Windows
```

`doctor` harus melaporkan `READY` dengan nol issue. Selain itu, baca `recommended_fixes`
sebelum melanjutkan — status `NOT_READY` berarti ada pintu masuk yang benar-benar rusak,
bukan sekadar catatan gaya.

Skrip entry di `.workflow/` memanggang path absolut mesin tempat `init` dijalankan, jadi
langkah 4 milik masing-masing orang. Mengcommit `.workflow/` ke repo bersama tidak akan
menolong siapa pun; `init` sudah menambahkannya ke `.gitignore`.

### Detail installer

Clone → jalankan satu script → terkonfigurasi.

```bash
git clone <repo> && cd agent-workflow
python install.py            # DRY RUN — tampilkan semua perubahan, tulis nol
python install.py --apply    # baru menulis
python install.py --apply --only-command      # matikan auto-intent
python install.py --check                     # cek bundle + instalasi (termasuk project bila cwd punya .workflow/)
python install.py --rollback                  # dry-run rollback terakhir
python install.py --rollback --apply          # rollback setelah preflight hash
```

**Dry run adalah default, disengaja.** Script ini menulis ke config agent global — dibaca setiap project di mesin itu. Kesalahan di sini tidak terkurung dalam satu repo.

Yang dilakukan `--apply`:

| Target | Strategi |
|---|---|
| `~/.claude/CLAUDE.md` | ganti isi **di antara marker** `WORKFLOW-MAIN-AGENT` — tulisan tangan di luar marker selamat |
| `~/.claude/skills/*.md` | replace (template) |
| `~/.claude/hooks/*` | replace |
| `~/.claude/settings.json` | tambah key dan refresh hook milik workflow; hook user dipertahankan |
| `~/.config/opencode/AGENTS.md` | ganti isi di antara marker `WORKFLOW-SECOND-AGENT` |
| `~/.config/opencode/opencode.{json,jsonc}` | merge additive untuk config umum; permission `agent.plan` milik workflow ditegakkan, key user lain dipertahankan |
| `~/.config/opencode/agents/*.md` | replace — satu roster subagent global, dipakai semua project yang dikelola workflow |
| `<project_root>/opencode.json` | **bukan** tugas installer — dipasang dan di-refresh oleh `init`/`upgrade` (deny-rule file rahasia ditegakkan tiap kali) |

Semua yang akan tertimpa di-backup dulu ke `~/.claude/backups/install_<timestamp>/`.
Receipt schema v2 mencatat hash sebelum dan sesudah untuk setiap file yang dibuat atau
diubah, termasuk `settings.json` dan file mode intent. Rollback memvalidasi seluruh
destination dan backup sebelum menulis apa pun; satu perubahan setelah instalasi membuat
rollback berhenti dengan konflik, bukan menimpa edit user atau melakukan rollback parsial.
Receipt mencakup target instalasi global dan bundle. Init/upgrade stateful pada `.workflow/`
serta penambahan `.workflow/` ke `.gitignore` tidak masuk rollback installer karena dapat
memuat session dan state project yang tidak aman dihapus otomatis.

Bila installer dijalankan dari dalam project yang sudah memiliki `.workflow/`, ia menjalankan **upgrade in-place**: scripts diregenerasi, key config baru di-backfill secara additive, `<project_root>/opencode.json` di-refresh, dan `sessions/` dipertahankan. Workspace baru tidak di-scaffold oleh installer — pakai `python main.py --command init --work-dir DIR` (skill `/.init`). Upgrade workspace ditolak bila masih ada job aktif; install global tetap selesai dan warning harus diperiksa.

### Mode intent

Default installer adalah **auto-intent**: bahasa natural dapat dipetakan ke command dan
hook `UserPromptSubmit` mengaktifkan pre-flight gate. `--only-command` mempertahankan hanya
kontrak prefix `/.` dan menghapus hook `intent-gate-set` milik workflow yang sudah
terpasang; hook lain milik user tidak dihapus. Jalankan `--auto-intent` untuk memulihkannya.
Tanpa kedua flag, upgrade mempertahankan mode instalasi sebelumnya.

`--check` menentukan scope dari cwd: dijalankan di dalam project yang punya `.workflow/`, ia
ikut memeriksa boundary project (`<project_root>/opencode.json`). Di luar workspace, scope
itu dilaporkan `SKIPPED` — bukan didiamkan lalu dilaporkan READY.

### Extractor (sisi maintainer)

`dist/` dihasilkan dari config live maintainer:

```bash
python tools/maintain/extract_config.py --dry-run
python tools/maintain/extract_config.py
```

Postur keamanannya, berurutan menurut kepentingan:

1. **Allowlist, bukan blocklist.** Home agent berisi `.credentials.json`, `history.jsonl` ratusan KB, transkrip sesi, dan path project. Blocklist mengirimkan apa pun yang lupa disebut; allowlist hanya mengirim yang disebut.
2. **Gagal-tutup pada rahasia.** Setiap byte dipindai pola kredensial. Satu temuan **membatalkan seluruh run dan menulis nol** — peringatan akan dibaca, diabaikan, lalu ter-commit. Rahasia di git history praktis tak bisa dicabut.
3. **Read-only.** Extractor tak pernah menulis di luar `dist/`.

Path absolut diredaksi jadi `{{HOME}}` / `{{PROJECT_ROOT}}`; installer yang mengembalikannya.

### E2E

```bash
python tools/e2e/e2e.py          # lokal + installer: tanpa delegated run/kuota
python tools/e2e/e2e.py --full   # plus command delegated: menit + kuota nyata
```

`--full` opt-in. Command delegated memakai anggaran rate-limit yang sama dengan yang dibutuhkan workflow itu sendiri; suite yang membakarnya diam-diam lebih buruk daripada tak ada suite.

Installer diuji terhadap **HOME sementara**, bukan mesin yang menghasilkan `dist/` — meng-install ke mesin asal hanya membuktikan idempotensi, tak pernah menjalankan jalur *create* yang dihadapi user baru.

`SKIPPED` bukan `PASS`, dan dilaporkan terpisah.

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

`init` bersifat idempoten: membuat scaffolding yang belum ada dan meregenerasi enam runner script. Ia tidak mem-backfill key pada config lama; gunakan `upgrade` untuk itu.

- `.workflow/config.json` — path absolut `main.py`/`check.py`, sehingga runner dapat menemukan tool tanpa mengandalkan `AGENT_PATH`
- `.workflow/second_agent.json` — salinan project-local, boleh kamu ubah
- skrip runner untuk platform yang sedang berjalan: `run` `inspect` `check` — `.ps1` di Windows, `.sh` di POSIX
- `.workflow/sessions/` kosong — state per-sesi dibuat lazy saat panggilan terdelegasi pertama
- entri `.workflow/` ditambahkan ke `.gitignore` root project

Skrip `.sh` diberi bit executable saat dibuat. Kalau repo dipindah lewat media yang membuang mode bit:

```bash
chmod +x .workflow/*.sh
```

**Batasan portabilitas.** Skrip memanggang **path absolut dari mesin tempat `init` dijalankan** — path ke `main.py`, `--work-dir`, dan interpreter Python yang terdeteksi. Tak ada satu pun yang portabel lintas mesin.

Karena itu hanya flavour OS yang sedang berjalan yang ditulis, dan `init`/`upgrade` **menghapus** skrip flavour lain yang tertinggal dari build lama. Sebuah `run.sh` yang dihasilkan di Windows berisi path bergaya `C:\...`, tidak akan jalan di Linux/macOS, dan tidak dipelihara generator mana pun di mesin itu — menyimpannya hanya membuatnya tampak dapat dipakai. Setiap anggota tim menjalankan `init` (atau `upgrade`) sendiri di environment masing-masing; itu juga berlaku saat berpindah antara WSL dan Windows asli.

`doctor` membandingkan isi skrip di disk dengan hasil generator dan melaporkannya di `run_script_drift` — `missing`, `content_differs`, atau `foreign_os_leftover`. Semuanya dihitung sebagai issue: skrip inilah satu-satunya pintu masuk, dan yang sudah melenceng akan merutekan command yang sudah tidak diterima CLI.

## Upgrade workspace

Jalur yang direkomendasikan setelah menarik versi agent-workflow baru:

```bash
cd /path/to/target-app && python /path/to/agent-workflow/install.py --apply
```

Command itu memperbarui config global dan meng-upgrade workspace di cwd. Project yang belum
punya `.workflow/` di-scaffold lewat `init`, bukan installer. Untuk workspace saja:

```bash
python3 "$AGENT_PATH" --command upgrade --work-dir /path/to/target-app --pretty
```

```powershell
python $env:AGENT_PATH --command upgrade --work-dir "C:/path/to/target-app" --pretty
```

`upgrade`:

- menolak berjalan ketika ada delegated job aktif;
- meregenerasi runner scripts dan me-repoint path tool;
- memigrasi kunci v3.4.2 (`opencode_*` → `provider_*`, `.workflow/opencode.json` → `.workflow/second_agent.json`) sekali, memindahkan nilainya;
- mem-backfill key `.workflow/config.json` dan `.workflow/second_agent.json` secara additive;
- mempertahankan nilai user dan seluruh `sessions/`;
- tidak mengirim prompt, tidak memanggil second_agent, dan tidak menjalankan verify.

Command biasa tidak menjalankan **full workspace upgrade**. Delegated call dapat mem-backfill `.workflow/config.json` saat memuat state, tetapi tidak meregenerasi scripts atau mem-backfill `.workflow/second_agent.json`. Karena backfill itu juga memperbarui marker versi, warning runner dan status `doctor: NEEDS_UPGRADE` dapat hilang sesudah delegated call pertama walaupun dua bagian tadi masih stale. Karena itu jalankan `upgrade` secara eksplisit sebelum memakai workspace versi lama.

---

## Pemakaian harian

main_agent memanggil **satu** skrip runner — tidak merakit command Python sendiri.

```powershell
& "C:/path/to/target-app/.workflow/run.ps1" explore "cari entry point auth" "<MAIN_SESSION_ID>"
```

```bash
/path/to/target-app/.workflow/run.sh explore "cari entry point auth" "<MAIN_SESSION_ID>"
```

Argumen ketiga adalah session id. **Wajib** diteruskan: tanpa itu runner memakai fallback `"default"` yang di-resolve melalui cache global tool, sehingga beberapa main_agent dapat berbagi ID efektif, saling menimpa state, dan saling memblokir job. Skrip mencetak peringatan ke stderr bila ini terjadi.

Skrip bersifat blocking dan mengembalikan JSON yang sama seperti CLI.

### Auto-intent

Prefix `/.` tetap didukung, tetapi tidak wajib. Main_agent memetakan bahasa natural ke command dan menampilkan satu baris transparansi sebelum menjalankannya:

```text
[INTENT] analyze — user meminta audit logic
```

Contoh mapping: pertanyaan lokasi/alur → `explore`, sebab/penilaian → `analyze`, fitur baru → `plan`, implementasi → `execute -y`, hasil yang sudah dibuat → `verify`, dan blast radius diff → `sweep`.

Batasnya:

- command eksplisit seperti `/.plan` selalu menang atas tebakan;
- percakapan biasa tidak dipaksa menjadi command;
- delegated intent yang ambigu dapat memicu satu pertanyaan klarifikasi;
- aksi destruktif atau irreversible tidak boleh auto-fire tanpa konfirmasi.

Auto-intent adalah kontrak prompt main_agent, bukan fitur parser Python dan tidak memiliki key `auto_intent`.

### Opsi implementasi pada `/.plan`

Setelah `[PLAN]`, main_agent menambahkan `[OPTIONS]` berisi maksimal tiga pendekatan yang tetap berada dalam scope. Setiap opsi memuat kelebihan, kekurangan, effort, risiko, dan atribusi evidence; tepat satu opsi direkomendasikan. Bila hanya satu pendekatan yang feasible, alternatif tidak boleh dikarang.

Second_agent hanya memasok evidence dan reasoning. Runtime tidak membuat atau memvalidasi blok `[OPTIONS]`.

---

## Command

| Command | Jenis | Butuh prompt | Keterangan |
|---|---|---|---|
| `init` | lokal | — | scaffold `.workflow/`; regenerate runner script |
| `upgrade` | lokal | — | refresh workspace, backfill config, preserve sessions |
| `doctor` | lokal | — | cek kesiapan, tulis `reports/doctor.json` |
| `clean` | lokal | — | prune job, fakta usang/duplikat, sesi lama |
| `inspect` | lokal | — | daftar job untuk sesi berjalan |
| `provider` | lokal | — | baca/ubah provider second_agent dan reasoning effort |
| `report` | lokal | — | ringkasan stream kualitas workspace (`quality.jsonl`) |
| `audit` | lokal | — | baca `audit.jsonl` sesi |
| `graph-meta` | lokal | — | status snapshot graphify: segar, usang, atau tak ada |
| `explore` | terdelegasi | ya | peta codebase, entry point, pemilik |
| `plan` | terdelegasi | ya | evidence + jejak dependency terbalik |
| `analyze` | terdelegasi | ya | analisis mendalam, nol perubahan kode |
| `verify` | terdelegasi¹ | ya | verifikasi; kedalaman diatur `verify_mode` |
| `sweep` | lokal | — | pindai staged, unstaged, dan untracked diff tanpa OpenCode |
| `promote-validate` | lokal | ya² | validasi dokumen knowledge; nol tulisan |
| `promote-verify` | lokal | ya² | freshness tiap klaim + rekonsiliasi lawan dokumen existing |
| `promote-write` | lokal | ya² | satu-satunya jalur tulis ke direktori knowledge |
| `submit` | job | ya | jalankan asinkron, kembalikan `job_id` |
| `await` | job | ya | submit lalu tunggu selesai |
| `status` | job | — | butuh `--job-id` |
| `result` | job | — | butuh `--job-id` |
| `worker` | internal | — | dipakai proses worker, jangan dipanggil manual |

¹ `verify` melewati OpenCode sepenuhnya ketika `verify_mode` bernilai `syntax`.

² Ketiga tahap `promote-*` menerima **path ke file JSON** lewat `--prompt`, bukan dokumennya sendiri: satu dokumen knowledge melewati batas argv 8191 karakter Windows dengan mudah. Tahapnya dipisah karena persetujuan user terjadi di antaranya — CLI tak bisa bertanya apa pun, jadi verifikasi berhenti pada vonis, main_agent yang menjalankan review, dan penulisan adalah panggilan terpisah yang hanya bisa terjadi sesudahnya. `promote-write` menolak di luar `policies.production_branch`.

Tidak ada command Python `execute`. `/.execute -y` tetap tersedia sebagai command user-facing di main_agent; menulis kode sengaja tidak didelegasikan ke runtime atau second_agent.

---

## CLI langsung

Berguna untuk debugging; alur normal cukup lewat skrip runner. Untuk perilaku blocking yang sama dengan runner, gunakan `await`:

```bash
python3 main.py -c await --job-command explore -p "cari entry point auth" -s "main_app_20260723_090000" -w /path/to/target-app --pretty
```

```powershell
python main.py -c await --job-command explore -p "cari entry point auth" -s "main_app_20260723_090000" -w "C:/path/to/target-app" --pretty
```

Memanggil `-c explore`, `plan`, `analyze`, atau `verify` secara langsung hanya melakukan
`submit` dan segera mengembalikan payload job. Ambil hasilnya lewat `result`/`check`, atau
gunakan `await` seperti contoh di atas. `sweep` dan command lokal lain selesai langsung.

| Argumen | Alias | Arti |
|---|---|---|
| `--command` | `-c` | lihat tabel command |
| `--prompt` | `-p` | task |
| `--prompt-file` | | baca task dari file (alternatif `--prompt`) |
| `--session` | `-s` | id sesi main_agent |
| `--fresh-session` | | paksa sesi baru, abaikan cache ID sesi dan evidence reuse |
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

Dibuat saat `init`. Jalankan `upgrade` untuk mem-backfill key versi baru; nilai yang sudah kamu isi tetap menang.

Delapan key berikut benar-benar mengubah perilaku runtime Python:

| Key | Default | Arti |
|---|---|---|
| `commands.verify_mode` | `"delegated"` | `delegated` = verifikasi penuh second_agent. `syntax` = check parse lokal saja. Nilai tak dikenal jatuh ke `delegated` |
| `policies.fact_relevant_limit` | `3` | maksimum fakta yang diinjeksi ke tiap prompt |
| `policies.fact_recurrence_threshold` | `5` | jumlah sesi **lain** yang harus melaporkan klaim sebelum dipromosikan |
| `policies.graph_leads_enabled` | `true` | injeksi shortlist dari `graphify-out/graph.json` |
| `policies.subagent_fanout_enabled` | `true` | minta fan-out untuk role exploration/reasoning |
| `policies.production_branch` | `"main"` | satu-satunya branch tempat `promote-write` mau menulis. Promoted knowledge menggambarkan yang hidup; hipotesis dari feature branch tak boleh masuk repo bersama membawa otoritas itu |
| `policies.knowledge_dir` | `"docs/project-knowledge"` | lokasi dokumen knowledge, relatif project root. Satu-satunya artefak workflow yang **ter-Git** — seluruh nilainya ada pada bisa dibagi |
| `policies.knowledge_relevant_limit` | `3` | maksimum dokumen knowledge yang ikut satu prompt terdelegasi. `0` mematikan sidecar knowledge |

`commands.auto_verify_after_execute` dibaca dan dikembalikan di `meta.policy`, tetapi hanya main_agent yang bisa menjalankannya karena Python tidak memiliki jalur `execute`.

Key instruksi main_agent lainnya: `commands.allow_analyze_to_plan`, `commands.allow_explore_to_plan`, `commands.auto_sweep_after_execute`, `policies.workflow_prefix`, `policies.chat_mode_for_plain_text`, `policies.fallback_requires_confirmation`, dan `policies.max_active_job_per_session`.

Tidak ada key `auto_verify_method`. Dua key yang valid dan ortogonal adalah:

- `commands.auto_verify_after_execute` — **kapan** verify dipanggil; default `false`. Saat false, `/.execute` harus melaporkan `verification: not_run`, berstatus `implemented`, lalu menawarkan `/.verify`.
- `commands.verify_mode` — **seberapa dalam** verify berjalan ketika dipanggil.

Key pensiun `commands.autoverify` dimigrasikan saat upgrade. Salah ketik key lain tidak menimbulkan error dan akan jatuh ke default.

### `.workflow/second_agent.json`

File adapter project-local ini boleh diubah per project. Namanya mengikuti PERAN, bukan vendor — sejak v3.4.3 provider second_agent dipilih lewat config, jadi file ini tidak lagi mengasumsikan OpenCode. Workspace v3.4.2 yang masih memakai `.workflow/opencode.json` dimigrasi sekali saat `upgrade` (nilainya dipindah, kunci lama dihapus). Jangan dikelirukan dengan `<project_root>/opencode.json` dan `~/.config/opencode/opencode.json`, yang memang milik OpenCode sendiri dan tetap bernama begitu.

Saat `init`, source-nya adalah `config/second_agent.json` bila file lokal itu ada, atau `config/second_agent.example.json` pada clone bersih. Loader melengkapi key yang belum tersimpan dengan default source secara in-memory; `upgrade` menuliskannya ke file secara additive. Bentuk efektifnya:

```json
{
  "provider": "opencode",
  "provider_command": "opencode",
  "provider_agent": "plan",
  "default_model": null,
  "timeout_seconds": 1800,
  "bootstrap_timeout_seconds": 180,
  "stall_threshold_seconds": 360,
  "idle_stall_seconds": 240,
  "probe_timeout_seconds": 45,
  "probe_recheck_seconds": 120,
  "job_poll_interval_seconds": 2.0,
  "routes": {
    "explore": { "model": null },
    "plan":    { "model": null },
    "analyze": { "model": null },
    "verify":  { "model": null }
  }
}
```

`model: null` berarti pakai model default OpenCode.

#### Memilih provider — dan konsekuensi keamanannya

`provider` menerima `opencode` atau `codex`. Keduanya bukan pilihan setara:

| | `opencode` | `codex` |
| --- | --- | --- |
| Boundary baca file rahasia | **ditegakkan** lewat `<project_root>/opencode.json` | **tidak ada** |
| Sandbox tulis | ya | ya (`--sandbox read-only`) |
| Config boundary project-root | file, di-refresh tiap `init`/`upgrade` | tak ada layer-nya |

Codex mengirim daftar deny yang sama sebagai flag `-c permissions.workflow.filesystem` di tiap panggilan, tetapi flag itu tidak menghentikan apa pun. Diuji terhadap codex-cli 0.147.0 mode `exec`: men-deny `**` dan `**/*` untuk `:workspace_roots` lalu meminta sebuah file di root itu tetap mengembalikan isinya, exit 0. Codex membaca dengan menjalankan shell, dan `--sandbox read-only` membatasi **tulis**, bukan baca.

Artinya second_agent codex bisa membaca tiap file di project yang kamu tunjuk, `.env` termasuk. `init` melaporkan ini sebagai `status: not_enforceable` dengan `permissions_enforced: 0`, dan `dist/config/codex/AGENTS.md` menyatakan ke agent-nya bahwa menghindari file rahasia adalah kewajibannya sendiri — instruksi, bukan penegakan.

Pakai `codex` bila project-nya memang tak menyimpan rahasia, atau bila kamu menerima risikonya. Untuk project yang rahasianya harus tetap tak terbaca second_agent, pakai `opencode`.

Kunci reliability (v3.5.1):

| Kunci | Default | Arti |
| --- | --- | --- |
| `timeout_seconds` | `1800` | Batas satu panggilan agent. `0` = tanpa batas (tidak lagi default). `null` = warisi default, **bukan** tanpa batas. |
| `bootstrap_timeout_seconds` | `180` | Anggaran terpisah untuk `init_session`. Bootstrap hanya membalas "READY", jadi tak boleh mewarisi anggaran task panjang. |
| `idle_stall_seconds` | `240` | Tidak ada byte baru di stdout/stderr selama ini → `alive-stalled`. |
| `stall_threshold_seconds` | `360` | Tidak ada heartbeat selama ini padahal PID hidup → `alive-stalled`. |
| `probe_timeout_seconds` | `45` | Batas probe PING itu sendiri. Tanpa ini watchdog ikut menggantung seperti pasiennya. |
| `probe_recheck_seconds` | `120` | Cadence probe ulang selama job tetap stalled; minimum jalur `await` adalah 10 detik. |
| `job_poll_interval_seconds` | `2.0` | Interval heartbeat/poll adapter. |

Per-route juga bisa: `"plan": { "model": "...", "timeout_seconds": 3600 }`.

**Role tidak dibaca dari file ini.** Pemetaan command → role ditentukan di kode (`config/routing.py`), jadi tak bisa ditumpuk lewat config.

**Tiga key pensiun.** `job_max_runtime_seconds`, `job_poll_timeout_seconds`, dan
`agent_workflow_path` dulu ditulis ke file ini dan tidak pernah dibaca siapa pun. Ketiganya
sudah berhenti ditulis; nilainya diambil dari `AI_PROXY_JOB_MAX_RUNTIME_SECONDS`, CLI
`--poll-timeout`, dan `.workflow/config.json → runtime.agent_workflow_path` (atau `AGENT_PATH`).
Workspace lama tetap membawanya karena upgrade bersifat additive — `doctor` menandainya
`retired — never read` dan aman dihapus manual.

### Sub-agent fan-out (default ON)

`policies.subagent_fanout_enabled` berlaku untuk `explore`, `plan`, dan `analyze`. `verify` tetap memakai satu reviewer agar seluruh diff dilihat sebagai satu perubahan.

Upgrade mempertahankan nilai existing. Workspace lama yang sudah menyimpan `false` tetap nonaktif sampai kamu mengubahnya sendiri.

Ketika aktif, prompt membawa anchor `[EVIDENCE_SIDECARS]`; cluster fan-out berada di
`.workflow/sessions/<id>/runtime/leads.json` agar tidak menghabiskan batas argv Windows:

- graph memiliki ≥2 community → satu slice per community;
- graph tidak ada atau hanya menghasilkan 0–1 community → tetap fan-out ke empat sudut investigasi: entry point, core flow, reverse dependencies, serta config/tests.

Second_agent wajib memakai custom agent `wf-slice` melalui tool `task` bila tersedia,
menjalankan slice secara paralel, lalu menggabungkan klaim dengan tag `[cN]`. Jika tool
spawn tidak tersedia, ia harus menyebutkan daftar tool yang benar-benar tersedia sebelum
membaca slice secara berurutan.

**Pemakaian dilaporkan apa adanya.** Runtime mengecek dua sinyal yang harus sepakat: baris `subagents:` yang dideklarasikan, dan tag `[cN]` pada klaim hasil merge.

| Kondisi | `meta` |
|---|---|
| deklarasi + tag cocok | `subagent_used: true`, `subagent_fanout_clusters: [...]` |
| deklarasi tanpa tag | `subagent_used: false` + `subagent_warning` |
| `subagents: none (...)` jujur | `subagent_used: false`, `covered_clusters` tetap dapat berisi tag yang dibaca |

Deklarasi tanpa klaim bertag adalah pengakuan kerja, bukan bukti kerja — dan tidak dihitung sukses.

---

## Graphify

Dengan `policies.graph_leads_enabled: true`, runtime membaca `graphify-out/graph.json` **langsung** tanpa memanggil CLI atau MCP Graphify. Ia meranking maksimal 12 candidate file berdasarkan keyword dan weighted dependency degree, membawa maksimal empat community, lalu menyuntikkannya sebagai **starting points, bukan evidence**.

- `explore`/`plan`/`analyze` menerima leads lengkap dan community untuk fan-out;
- `verify` menerima shortlist ringkas maksimal enam file tanpa community;
- graph yang lebih tua daripada source `.py` tetap dipakai, tetapi prompt membawa warning stale;
- graph tidak ada atau rusak → delegated flow tetap berjalan tanpa leads.

Runtime Python tidak pernah menjalankan `graphify init`, `build`, `watch`, atau `update`. Bundle Claude/PowerShell saat ini juga menyediakan Stop hook terpisah yang dapat menjalankan `graphify update` setelah `[EXECUTION RESULT]` atau `[REFACTOR RESULT]`, hanya bila graph sudah ada, stale, dan binary tersedia. Hook bersifat fail-open dan tidak menolak response bila gagal, tetapi berjalan sinkron sehingga dapat menambah latency sampai 45 detik (outer timeout 60 detik). Hook tidak pernah membuat graph baru atau menjalankan `init`/`build`/`watch`.

Cache verdict graph memakai fingerprint path, mtime nanosecond, dan ukuran seluruh source
`.py`. Perubahan isi, penambahan, atau penghapusan source menginvalidasi cache walaupun
`graph.json` tidak berubah.

---

## Mode verify

Kontrak konfigurasi yang dimaksud memisahkan dua pengaturan:

- `auto_verify_after_execute` menentukan apakah main_agent otomatis memanggil `/.verify` setelah implementasi; default `false`;
- `verify_mode` menentukan kedalaman pemeriksaan bila verify benar-benar dipanggil.

Jadi `auto_verify_after_execute: false` tidak mematikan `/.verify`; ia hanya mencegah pemanggilan otomatis.

`verify_mode` mengatur **sedalam apa** `/.verify` bekerja:

- **`delegated`** (default) — second_agent memverifikasi dan mengembalikan kontrak berlabel. Tiap temuan wajib membawa tiga tag: `severity` (critical/high/medium/low), `origin` (introduced/regression/pre_existing/unknown), `scope_relation` (in_scope/out_of_scope). Blocking ditentukan kombinasi ketiganya, bukan severity saja — cacat pre-existing tidak menyandera verdict perubahan berjalan, dan `origin: unknown` gagal-tertutup.
- **`syntax`** — dijawab lokal, tanpa memanggil OpenCode sama sekali. Memeriksa staged, unstaged, dan untracked files, termasuk repository tanpa commit: `.py` via `compile()` in-process, `.json` via `json.loads`, `.js`/`.mjs`/`.cjs` via `node --check`, `.php` via `php -l`. File Python juga memerlukan name check `pyflakes`; bila tool tidak tersedia, hasilnya `incomplete`. Kegagalan discovery Git juga menghasilkan `incomplete`, bukan `skipped`.

Semua yang tak bisa diperiksa dilaporkan apa adanya, tidak pernah dihitung lulus:

| Keluaran | Arti |
|---|---|
| `not_checked` | tak ada checker untuk ekstensi itu, atau file > 2 MB |
| `skipped` | toolchain bahasa tak ada di `PATH` |
| `name_check: unavailable` | `pyflakes` tak terpasang; file Python masuk `skipped` dan verdict `incomplete` |

Python diperiksa in-process, bukan lewat `py_compile`, supaya tak ada `.pyc` yang tertinggal di pohon kerjamu. Direktori `__pycache__`, `node_modules`, `.git`, `vendor`, `.venv`, `venv` dilewati.

`verdict: pass` berarti semua checker yang berlaku selesai tanpa finding dan tidak ada gap.
**Bukan** berarti fiturnya bekerja atau test perilaku telah dijalankan.

---

## Fact store

`.workflow/facts.jsonl` menyimpan pengetahuan yang bertahan lintas sesi. Sebuah klaim masuk lewat salah satu dari dua jalur:

1. ditandai eksplisit `[config]`/`[pattern]`/`[invariant]` oleh second_agent, atau
2. dilaporkan secara mandiri oleh ≥ `fact_recurrence_threshold` sesi **lain**.

Sesi yang sedang berjalan dikecualikan dari hitungan recurrence. Tanpa itu, sebuah fakta yang diinjeksi ke prompt lalu sekadar digemakan kembali bisa menaikkan hitungannya sendiri sampai ambang promosi — pengulangan menyamar jadi bukti.

Tiap fakta ditambatkan ke hash isi baris `file:line`. Ketika baris itu berubah, fakta dianggap usang saat dibaca dan tak pernah disajikan sebagai segar.

Dua klaim dilebur hanya bila **semua** pagar setuju: `file` sama, `category` sama, `anchor_hash` identik, polaritas negasi sama, kemiripan Jaccard ≥ 0.5, dan kedua klaim ≥ 6 kata. Satu pagar menolak → dua-duanya disimpan. Duplikat itu murah; fakta yang hilang tak bisa dikembalikan.

## Evidence reuse dan boundary

Evidence lintas sesi diindeks di `.workflow/evidence.jsonl` dengan lock lintas proses dan
atomic rewrite. Entry hanya boleh menunjuk artifact immutable
`sessions/<id>/logs/<prompt_id>/output.raw.md`; hash artifact dan seluruh anchor `file:line`
harus tetap cocok. Anchor yang tidak resolve atau melebihi batas membuat evidence tidak
eligible untuk reuse. Entry lama yang menunjuk `response.last.md` dibuang.
Query cache bersifat case-sensitive dan juga mengikat effective route/model, versi config,
mode fan-out, graph leads, serta facts yang dipakai. `--fresh-session` menonaktifkan lookup
reuse untuk invocation tersebut.

Sebelum delegasi, path relatif di-resolve terhadap project root; traversal, path absolut
di luar root, home-relative path, dan bare sensitive file seperti `credentials.json`
ditolak. Primary OpenCode juga memakai `external_directory: deny`; file discovery harus
melalui Read/Grep/Glob built-in. Bash hanya mengizinkan command Git read-only yang terdaftar.

Semua content dan metadata adapter, termasuk error, timeout, bootstrap, probe, dan
`call.meta.json`, melewati redaksi recursive. Argumen proses mentah tidak disimpan;
telemetry hanya membawa jumlah, panjang, dan hash argv.

Bersihkan yang usang dan duplikat:

```bash
python3 main.py --command clean --work-dir /path/to/target-app --pretty
```

---

## Layout workspace

```text
<target-app>/.workflow/
├─ config.json              # statis, dibuat saat init
├─ second_agent.json        # salinan project-local, config provider
├─ facts.jsonl              # fact store lintas sesi
├─ evidence.jsonl           # index artifact immutable lintas sesi
├─ provider-sessions/       # ID sesi provider, terisolasi per project
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
   │  ├─ output.raw.md
   │  └─ call.meta.json
   └─ reports/sweep.last.md
```

State yang berubah-ubah (`state`/`scope`/`cache`/`runtime`/`logs`) hidup di bawah `sessions/<id>/`, sehingga dua main_agent pada project yang sama tak pernah saling menimpa. Config dan reports tetap bersama di root `.workflow/`.

Job asinkron tetap disimpan di repo tool pada `storage/jobs/`. Cache ID sesi default berada
di `storage/main-sessions/` dan dipisah dengan hash project root; pemetaan ke ID sesi
OpenCode berada project-local di `.workflow/provider-sessions/` sehingga ID yang sama pada
dua project tidak dapat me-resume provider session satu sama lain.

---

## Job asinkron & pemulihan

Command terdelegasi otomatis berjalan lewat worker terpisah. Pada Claude Code, runner
harus diluncurkan sebagai background tool task sehingga tool call foreground segera
mengembalikan task ID; Claude kemudian mengambil hasil task itu. Runtime tetap
agent-agnostic—caller lain boleh memakai runner blocking biasa.

Kalau background task hilang atau pemanggilan terputus, panggil runner lagi dengan
session, command, dan task yang identik. Runtime akan attach bila worker masih hidup.
Jika worker sudah mati, job yang sama dipulihkan satu kali melalui OpenCode session lama
dengan prompt continuation terstruktur. Kematian kedua menghasilkan
`recovery_exhausted`, melepas lock, dan tidak memicu loop otomatis.

```powershell
& ".workflow/inspect.ps1"
& ".workflow/check.ps1" <job_id> --wait --result
```

```bash
.workflow/inspect.sh
.workflow/check.sh <job_id> --wait --result
```

Exit code status `check`: `0` selesai · `1` gagal · `2` masih jalan/antre · `3` tak
ditemukan. Dengan `--result` untuk job verify, `0` hanya berarti verdict `pass`; verdict
`fail` atau `incomplete` mengembalikan `2`.

Kalau tak ada job yang cocok, hasil terakhir masih ada di `.workflow/sessions/<id>/runtime/response.last.md`.

Recovery bersifat best-effort, bukan process survival: jika session OpenCode lama tidak
pernah tercatat, runtime gagal sebagai `session_capture_failed` dan clean run diperlukan.
Request berbeda pada session yang masih terkunci tetap ditolak sebagai
`job_already_running`.

### Liveness worker (v3.5.1)

PID yang hidup **tidak** berarti sedang bekerja. Worker karena itu melaporkan heartbeat sekaligus usia output stream, lalu job diklasifikasi tiga keadaan:

| Keadaan | Arti | Tindakan |
| --- | --- | --- |
| `alive-progressing` | PID hidup, heartbeat segar, stream belum melewati batas idle | tunggu |
| `alive-stalled` | stream idle > `idle_stall_seconds` atau heartbeat basi > `stall_threshold_seconds` | probe fresh session |
| `dead` | PID hilang | reap → `worker_died` |

Saat `alive-stalled`, runtime mengirim PING ke **sesi OpenCode baru**, bukan sesi yang dicurigai menggantung. Probe pertama berjalan segera setelah status stalled terlihat, lalu diulang sesuai `probe_recheck_seconds` selama job masih stalled. Default source saat ini `120` detik, bukan satu menit.

Jalur normal `await` membaca `stall_threshold_seconds`, `idle_stall_seconds`, dan `probe_recheck_seconds` dari config project. Jalur attach `check.py --wait` saat ini memakai default tool untuk ketiganya dan cadence probe tetap 120 detik; tuning project belum diteruskan ke proses attach.

- probe menjawab → simpan `stalled_no_progress`, lanjut menunggu;
- provider menolak karena quota/rate limit → terminate process tree, reap sebagai `rate_limited`;
- stream probe terputus → terminate/reap sebagai `streaming_failed`;
- probe gagal karena alasan lain → terminate/reap sebagai `second_agent_unavailable`;
- PID hilang kapan pun → reap sebagai `worker_died`.

Plafon keras JobManager tetap ada sebagai jaring pengaman: default 5400 detik dan dapat diubah lewat env `AI_PROXY_JOB_MAX_RUNTIME_SECONDS`. Job yang melewatinya gagal sebagai `job_expired` meski PID tampak hidup. Key project-local bernama sama belum mengubah nilai ini.

Setiap panggilan yang mencapai `OpenCodeAdapter` menuliskan `call.meta.json` yang sudah
diredaksi (exit code, durasi, timeout, cara kill, ekor stderr aman, dan agregat argv) ke
`.workflow/sessions/<id>/logs/<prompt_id>/`. `verify_mode: syntax` tidak membuat file ini.

---

## Sesi

`--session` adalah otoritas tunggal untuk binding sesi. Panggilan pertama bootstrap sesi OpenCode:

```text
opencode run <prompt> --print-logs --log-level INFO
```

Runtime mengurai `session.id=ses_...` dari log dan menyimpannya. Panggilan berikutnya memakai ulang sesi itu:

```text
opencode run <prompt> -s <provider_session_id>
```

Baris log OpenCode dan banner model dibuang dari `content`; isi jawaban asisten dipertahankan utuh.

---

## Test

```bash
python3 tests/run.py
```

```powershell
python tests/run.py
```

Suite default tidak memanggil OpenCode sungguhan; alur agent memakai adapter palsu,
sedangkan jalur subprocess, heartbeat, timeout, kill-tree, installer, rollback, dan
kontrak persistence diuji secara lokal.

Gunakan `python tools/e2e/e2e.py --full` bila ingin menambahkan smoke test OpenCode nyata; mode itu opt-in karena memakai quota.

Pemeriksaan tambahan:

```bash
python3 main.py --help
python3 main.py --command doctor --work-dir . --pretty
```

---

## CI

Dua workflow GitHub Actions menjalankan gerbang yang tanpanya seseorang harus ingat
menjalankannya sendiri.

| File | Pemicu |
|---|---|
| `.github/workflows/ci.yml` | push ke `main`/`dev`, pull request, manual |
| `.github/workflows/e2e-full.yml` | manual saja |

Gerbang yang dijalankan jalur gratis, berurutan:

```
python tools/maintain/stamp_version.py --check
python tools/maintain/gen_manifest.py --check
python tests/run.py --only deps
python tests/run.py --keep-going --record .
python tools/e2e/e2e.py
```

`--only deps` berdiri sebagai step tersendiri, bukan hanya di dalam suite: itu gerbang yang
memutuskan apakah ketiadaan lockfile masih benar, dan pembaca yang menyapu daftar step harus
bisa melihatnya berlaku. `--keep-going` supaya satu kegagalan tak menyembunyikan sisanya;
`--record .` menulis hasilnya ke stream kualitas workspace, jadi `--command report` bisa
menunjukkan pass rate lintas waktu alih-alih hanya run terakhir. `e2e.py` tanpa `--full`:
nol provider CLI dipanggil, nol kuota dibakar — itu sebabnya ia boleh jalan di tiap push.

Jalur terdelegasi manual dan tak pernah otomatis: ia memanggil second_agent sungguhan,
memakan kuota nyata, dan butuh provider CLI plus kredensial yang sengaja tidak dipegang
pipeline. Ia workflow `workflow_dispatch` terpisah, dengan session id dan runner sebagai
input dispatch — default `e2e-dispatch` dan `self-hosted`.

### Toolchain dan matrix

Python dipaku ke `3.13` lewat `actions/setup-python`, jadi workflow MENYEDIAKAN interpreter
alih-alih berharap runner membawanya. Matrix `runs-on` mencakup `ubuntu-latest` dan
`windows-latest` dengan `fail-fast: false`: runtime mengirim runner PowerShell dan
menghasilkan yang POSIX, dan penanganan path paling berbeda persis di tempat yang penting
(lock, direktori sesi) — run satu-OS akan lolos sementara separuh produk tak teruji, dan satu
platform yang merah tak boleh menyembunyikan hasil platform lain.

`on: push` (`main`/`dev`) + `pull_request` + `workflow_dispatch`, dengan
`concurrency: cancel-in-progress` supaya pipeline yang tersalip di ref yang sama dibatalkan
alih-alih dibiarkan selesai melawan commit basi. Permission job dikunci ke `contents: read`.

Yang **tidak** dilakukan CI: bump, tag, publish. Langkah yang tetap milik
manusia ada di `RELEASE.md`.

---

## Batasan yang diketahui

Disebut terbuka karena diam soal ini akan membuat runtime terlihat lebih menjamin daripada kenyataannya:

- **Mutasi main_agent tak terlihat oleh runtime ini.** `/.execute` tak punya jalur Python sama sekali. Audit scope, penjaga operasi destruktif, dan atribusi perubahan file karena itu belum ada — perlu lapisan hook di sisi main_agent.
- **Kontrak masih sebagian berbasis prompt.** Runtime memvalidasi struktur dan routing finding verify, tetapi kebenaran semantik klaim serta output kontrak milik main_agent tetap tidak dapat dibuktikan hanya dari penanda.
- **Telemetry masih parsial.** Durasi, exit code, hasil kill, ukuran prompt/output, dan estimasi token dicatat per panggilan di `call.meta.json`; jumlah pemanggilan tool dan token provider aktual belum selalu tersedia.
- **OpenCode nyata hanya diuji opt-in.** Suite default mensimulasikan provider; jalur `Popen`, persistence, installer, dan process lifecycle tetap dijalankan lokal. Gunakan `tools/e2e/e2e.py --full` untuk smoke test berkuota.
- **Probe PING memakai kuota.** Job yang terus stalled dapat diprobe berulang sesuai cadence; default `await` adalah 120 detik.
- **Tuning liveness belum seragam di jalur attach.** `check.py --wait` memakai default tool untuk ambang stalled dan probe ulang, bukan nilai project-local.
- **Deteksi upgrade dapat tertutupi backfill parsial.** Delegated load memperbarui marker versi `config.json` tanpa meregenerasi scripts atau `second_agent.json`, sehingga warning berikutnya dan `doctor` dapat menganggap workspace current.
- **Deteksi staleness graph hanya mengikuti source `.py`.** Perubahan bahasa lain tidak masuk fingerprint runtime; Stop hook Graphify merupakan layer Claude terpisah dan tidak tersedia di semua main agent.
- **Path graph lintas-OS belum dinormalisasi penuh.** Snapshot yang dibuat di Windows lalu dibaca dari POSIX/WSL dapat tetap menghasilkan candidate path ber-backslash; refresh graph secara manual dari environment aktif bila ini terjadi.
- **Skrip runner tidak portabel lintas-OS.** Path absolut dipanggang saat init/upgrade; pindah repo, path, atau OS berarti jalankan upgrade dari environment baru.

---

## Benchmark

`bench/` berisi harness benchmark 3-arm yang mengukur ekonomi quality-adjusted tool ini
terhadap dirinya sendiri. Rencananya di [`bench/BENCHMARK-PLAN.md`](../bench/BENCHMARK-PLAN.md),
progres eksekusi di [`bench/STATE.md`](../bench/STATE.md).

Tiga arm: **A** = Claude langsung, **B** = native sub-agent, **C** = agent-workflow (repo
ini). Arm A dan B dijalankan operator secara manual dan harness memanen biayanya per
`sessionId`; arm C jalan lewat `python main.py --command ...` dan itu satu-satunya arm yang
bisa dibuat deterministik.

System under test dibekukan di tag `v3.4.5`. Versi itu tidak ikut naik saat `TOOL_VERSION`
naik: SUT yang bergeser di tengah pengukuran membuat hasilnya tidak bisa diatribusikan ke
versi mana pun. `tools/maintain/stamp_version.py` menstempel baris `**Versi SUT:**` di
`bench/BENCHMARK-PLAN.md` — kalau SUT dan versi berjalan sudah berpisah, baris itu harus
diperbarui sadar, bukan dibiarkan ikut stempel.

`bench/` sengaja berada di luar scope task apa pun. Agen yang sedang diuji tidak boleh
menyentuh instrumen yang menilainya.

Verdict per unit ditentukan `bench/oracle.py`, yang dibekukan sebelum unit pertama dipanen;
setiap perubahan sesudah klaim beku itu wajib tercatat di log pembekuan di kepala file.
Empat verdict: `accepted`, `rejected`, `security_violation`, `incomplete`. `not_checked` dan
`skipped` bukan pass — stage yang tidak dijalankan menghasilkan `incomplete`, dan
`incomplete` bukan diterima.

Pemetaan verdict dikunci [`bench/test_oracle.py`](../bench/test_oracle.py), dijalankan
terpisah dari `tests/run.py`:

```
python bench/test_oracle.py
```

Terpisah karena oracle menjalankan `tests/run.py` sebagai stage-nya sendiri; test bench di
dalam suite itu membuat oracle menilai instrumennya sendiri.

Satu unit dijalankan [`bench/driver.py`](../bench/driver.py) dalam enam fase, dan tiga di
antaranya bukan milik mesin:

```
python bench/driver.py prepare  --task T01 --arm C --repeat 1
#   jalankan sesi agen di dalam worktree yang dicetak
python bench/driver.py delegate --unit T01_C_1 --command explore   # opsional, arm C
python bench/driver.py judge    --unit T01_C_1
python bench/driver.py finish   --unit T01_C_1 --rework-cycles 0
python bench/driver.py teardown --unit T01_C_1
```

`prepare`, `judge`, dan `teardown` berulang identik; sesi agennya tidak. `finish` menstempel
dua angka yang cuma operator lihat — `rework_cycles` dan `main_agent_rewrote` — dan itu
disengaja: apakah main agent menulis ulang kerja delegatnya adalah fakta tentang sesi, bukan
bentuk yang bisa dibaca dari patch akhir.

[`bench/collect.py`](../bench/collect.py) mengubah unit selesai jadi `ledger.jsonl`. Jalankan
**sebelum** `teardown` — angka sisi worker arm C ada di dalam `.workflow` milik worktree.
Baris tanpa biaya premium ditolak kecuali diminta eksplisit: `aggregate.py` memaksa biaya
yang hilang jadi `$0`, jadi arm yang ekspornya tak pernah datang akan terbaca sebagai arm
termurah.

Batas run terkumpul di [`bench/policy.py`](../bench/policy.py) — `python bench/policy.py`
mencetaknya. Waktu per unit, cap `rework_cycles`, dan cap panggilan terdelegasi ditegakkan
saat jalan; budget per unit dan per run **tidak** — biaya datang dari tokenburn sesudah run
selesai, jadi `collect.py` melaporkan pelampauan alih-alih mencegahnya.

Daftar karantina flaky di file yang sama, dan kosong. Empat run hijau berturut bukan bukti
suite ini stabil, cuma ketiadaan bukti sebaliknya; nol suite dikarantina atas dasar curiga.
Mengisi daftar itu mengeluarkan suite tersebut dari gerbang penerimaan **setiap** unit dalam
studi, jadi baris yang terkena dicap `quarantined_suites` di ledger dan tidak sebanding
dengan baris bergerbang penuh.

---

## Referensi

- Catatan rilis: [`prompt/v3.5.1/changelog.md`](../prompt/v3.5.1/changelog.md)
- Kontrak canonical main_agent: [`dist/config/claude/CLAUDE.md`](../dist/config/claude/CLAUDE.md)
- Kontrak canonical second_agent: [`dist/config/opencode/AGENTS.md`](../dist/config/opencode/AGENTS.md)
- Runtime entry point: [`main.py`](main.py)
- Rencana benchmark: [`bench/BENCHMARK-PLAN.md`](../bench/BENCHMARK-PLAN.md)
