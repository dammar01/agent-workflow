# Changelog — v3.6.0

Dua tema besar dan satu kecil. Yang besar pertama: verifikasi lewat browser sungguhan, sebagai
command terpisah `/.verify-browser`, bukan mode baru `/.verify`. Yang besar kedua: layout
workspace baru — `.workflow/` hanya berisi file yang diedit user, semua internal pindah ke
`.workflow/data/`, dan workspace lama dimigrasi otomatis oleh `upgrade`. Yang kecil: sisi premium
akhirnya bisa diukur dari transcript Claude Code, bukan hanya sisi second_agent.

Semua pekerjaan yang sempat dicatat sebagai "Stabilisasi 3.6.1" dan "Layout 3.7.0" di baris
`Unreleased` `CHANGELOG.md` dirilis di sini, di bawah satu nomor. Nomor 3.6.1 dan 3.7.0 tidak
pernah di-stamp, tidak pernah di-tag, dan tidak akan dipakai untuk isi ini. Catatan di bawah
menggambarkan keadaan akhir yang dikirim tag, bukan urutan pengerjaannya: rancangan yang sempat
ada di build dev lalu diganti (config-less, `allowed_mutation_paths`, gate konfirmasi run)
disebut hanya di bagian **Breaking** dan **Migrasi**, sebagai hal yang tidak berlaku.

## Status rilis

Nomor sudah di-stamp dan manifest sudah regenerasi; `stamp_version --check` dan
`gen_manifest --check` lolos pada tree final. Gerbang `RELEASE.md` langkah 5 pada tree final:

| Gerbang | Hasil |
|---|---|
| `python tests/run.py` | `scenario` PASS (56 s, Windows; juga PASS di Ubuntu); smoke dilewati tanpa flag, sesuai desain |
| `python tools/e2e/e2e.py` | 137 passed, 0 failed, 1 skipped (delegated, opt-in `--full`) — Windows dan Ubuntu |
| `WORKFLOW_E2E_SMOKE=1 python tests/run.py --only e2e-smoke` | **belum dijalankan ulang pada tree final.** Terakhir PASS (49,6 s, Chromium sungguhan, Playwright 1.60.0) pada build sebelum layout `data/` dan stabilisasi guard write |
| `python tools/e2e/e2e.py --full` | **belum dijalankan ulang pada tree final.** Terakhir 143 passed, 0 failed, 0 skipped (101 s, opencode `openrouter/deepseek/deepseek-v4-flash-0731`) pada build yang sama |
| ganti provider di satu sesi, provider sungguhan | codex lalu opencode di sesi `a2-switch-…`: opencode membuka thread sendiri (`ses_…`), thread codex tetap tersimpan terpisah, nol `Session not found` (build sebelum layout `data/`) |

Dua baris berbayar/berat di atas sengaja ditulis sebagai belum diulang, bukan diwariskan
sebagai lulus: perubahan sesudahnya menyentuh jalur yang keduanya uji (lokasi sesi dan
evidence, transport prompt opencode, guard write). Jalankan keduanya sebelum membuat tag, lalu
ganti baris ini dengan hasilnya.

Batas yang tetap berlaku: `--full` membuktikan `explore` dan `sweep` terhadap provider nyata,
bukan tahap `draft` dan review `/.verify-browser`. Kedua tahap itu diuji dengan provider palsu
dan player palsu; smoke membuktikan player dan guard di Chromium nyata, tanpa second_agent.

**Tag.** v3.6.0 adalah tag pertama sejak `v3.5.1`. v3.5.2 dan v3.5.3 ditulis, di-stamp dan
dicatat, tetapi **tidak pernah di-tag dan tidak akan di-tag**: isinya dirilis bersama tag ini.
Siapa pun yang memasang dari tag melompat langsung dari 3.5.1 ke 3.6.0, jadi catatan
`prompt/v3.5.2/` dan `prompt/v3.5.3/` tetap bagian dari yang diterima. Untuk menelusuri tree
yang dimaksud nomor lama, commit terakhir yang masih membawa nomor itu adalah `e7a2ded` (3.5.2;
bump ada di `5617dbd`, tetapi statusline yang dicatat changelog 3.5.2 baru masuk sesudahnya) dan
`7771baa` (3.5.3) — referensi, bukan rilis yang bisa dipasang.

## Breaking

- **Layout workspace.** Isi internal `.workflow/` pindah ke `.workflow/data/`. Workspace layout
  v3.5.x tetap jalan apa adanya sampai `upgrade` memigrasinya (lihat **Layout `data/`**).
- **`config.json` hanya override.** Default tak lagi ditulis; key yang hilang = default bawaan
  build. Upgrade membuang nilai yang sama dengan default dan key pensiun.
- **`secrets.json` berbentuk list** `{name, credentials}` dengan nama key dari registry kode
  (`E2E_USER`, `E2E_PASS`). Runtime menolak bentuk objek lama; `upgrade` mengonversinya sekali
  dengan nilai dipertahankan.
- **`allowed_mutation_paths` dihapus.** Request yang masih memakainya ditolak dengan pesan
  migrasi: write hanya lewat `allow_side_effects: true`, POST yang cuma membaca masuk
  `allowed_read_only_requests`.
- **Tak ada fallback ke `config/second_agent.json` level mesin.** Project tanpa atau dengan
  `.workflow/second_agent.json` rusak ditolak (`provider_config_missing` / `invalid`).
- **opencode tanpa model ditolak** `model_unset`: tanpa `-m` opencode memakai model terakhir
  yang dipakainya, dan dari situlah pin `mimo` yang tak pernah dipilih sampai ke project.
- **Seed provider** kini `config/second_agent.seed.json`, dibangun ulang dari
  `config/second_agent.example.json` tiap `install.py --apply`; file lama dipensiunkan
  (di-rename `.retired`, pilihannya dicetak, tak dibawa).

## Layout `data/`

`.workflow/` kini hanya berisi yang dibuka orang: `config.json`, `second_agent.json`,
`e2e/secrets.json`, script `run`/`check`/`inspect`, dan `current/`. Sesi, log, stream
usage/audit/quality/redactions, evidence, facts, knowledge browser, cache, dan backup ada di
`.workflow/data/`. `workspace_paths.workflow_paths` satu-satunya sumber path; hook dan run
script memakai aturan layout yang sama (keberadaan `data/`), jadi semua pembaca sepakat pada
satu layout di setiap saat.

Migrasi oleh `upgrade` (`core/runtime/migrations.py`, layout 1 → 2): backup seluruh
`.workflow/` ke `data/backups/<stamp>/`, pegang lock store, pindahkan item lewat
`data.migrating/` lalu rename sekali, tulis ulang path evidence, hapus sisa lama, strip
`config.json`, konversi `secrets.json`. Gagal sebelum rename → semua dikembalikan (backup tetap
disimpan); langkah sesudah rename dicatat di `data/.migration-pending.json` dan dilanjutkan
`upgrade` berikutnya. Job delegated yang masih hidup → migrasi ditolak.

- `.workflow/current/`: mirror dispatch terakhir (`session.json`, `progress.jsonl`, event
  browser live, report/screenshot terakhir).
- `doctor` melaporkan `workspace_layout`, migrasi yang belum selesai, dan file tak dikenal di
  root `.workflow/`.

## `/.verify-browser`

Alur per run: section `e2e` di `.workflow/config.json` (bila ada) → draft spec oleh
second_agent (`e2e_spec`, route internal yang bisa diberi model) → run langsung → player
Playwright lokal → review hasil oleh second_agent → `[VERIFICATION]` kanonik. Vonis fail-closed:
browser `fail` tetap `fail` apa pun kata reviewer; browser `pass` dengan reviewer tak tersedia
menjadi `incomplete`, bukan `pass`.

- **Config per project, override per run.** Section `e2e` di `config.json` jadi default terpasang;
  request sesi (`.workflow/data/sessions/<session>/e2e/request.json`) cuma menulis selisihnya.
  Knob salah di config → warning + fallback ke default (`meta.e2e.config_warnings`); knob salah
  di request → `request_invalid`.
- **Tanpa wawancara, tanpa gate.** Draft `ready` langsung lanjut ke run. Yang masih menghentikan
  cuma draft blocked/invalid, `missing_env`, dan izin `read_only_requests` yang belum diberikan.
- **Default headed** (`headless: false`). Linux tanpa display (DISPLAY/WAYLAND_DISPLAY kosong)
  → dijalankan headless dengan warning, bukan gagal launch.
- **Retry lingkungan.** `max_retries` (default 2, ceiling 5) hanya untuk `incomplete` berlatar
  lingkungan; `fail` origin=app dan `allow_side_effects: true` tak pernah diulang. Dua percobaan
  identik per step berhenti sebagai `stable`; artifact retry di `e2e/retry<n>/`. Yang baru lolos
  di percobaan berikutnya → `incomplete`, bukan `pass`.
- **Credential hanya sebagai nama.** Skenario memakai `${NAMA}`; nilainya dari profil
  `.workflow/e2e/secrets.json` atau environment, tak pernah lewat chat, prompt, atau artifact.
  Draft membuat template file itu bila belum ada (atomik, aman dari draft bersamaan). Nilai yang
  ter-resolve di-scrub dari event dan artifact teks (raw, URL-encoded, HTML- dan JSON-escaped);
  nilai expected di event disimpan sebagai placeholder; JWT/token campuran diredaksi dari
  ledger. Run yang memakai credential tidak mengambil screenshot maupun trace.
- **Origin dan write dua pertanyaan terpisah.** Navigasi top-level keluar dari origin
  `base_url`/`allowed_origins` diblokir (`navigation_blocked`), termasuk redirect navigasi.
  Nama `.test` diperlakukan seperti localhost (RFC 6761); domain sungguhan butuh
  `allow_remote` + `allowed_origins` persis. Write dinilai dari ALAMAT: tiap host write
  di-resolve, ditolak bila publik/link-local/tak ter-resolve, lalu dipatok ke browser lewat
  `--host-resolver-rules` (nama yang resolve ke loopback juga dipatok — DNS rebinding; browser
  non-chromium ditolak untuk write non-loopback; write https ke host terpatok ditolak).
- **Redirect di-guard sendiri.** Redirect tak pernah lewat route handler Playwright, jadi guard
  write mengikutinya sendiri (`route.fetch(max_redirects=0)`, tiap hop 307/308 dinilai).
  Sebelumnya POST 307 mengirim ulang body ke tujuan mana pun.
- **POST yang hanya membaca.** Draft boleh mengusulkannya di `read_only_requests` dengan bukti
  handler `path:line` yang benar-benar ada; hanya yang dikonfirmasi user di
  `allowed_read_only_requests` lolos. GraphQL `mutation`/`subscription`, persisted query, atau
  body tak terbaca tetap diblok.
- **Kontrak draft.** Step wajib `id` stabil; `within`, readiness deklaratif (`ready`, bukan
  `networkidle`), `request` yang diharapkan, `test_data.marker`; cleanup berupa step executable
  (`scenario.cleanup`, `cleans`) yang dilaporkan terpisah dan membuat verdict `incomplete` bila
  gagal/tak jalan. Aksi write tak pernah dikirim ulang saat timeout.
- **Laporan.** Ledger request non-GET (endpoint tersanitasi, status, durasi, atribusi step atau
  `uncertain`); `trail` per step: selector (jumlah match, fallback) → readiness → request → hasil.
  Klasifikasi kegagalan `app | harness | unknown`; `unknown` tak pernah dipromosikan ke `harness`.
- **Knowledge browser** (`.workflow/data/e2e-knowledge.jsonl`), setara fact: run mencatat alur
  login/logout, route, readiness, dan selector yang terbukti per origin; draft berikutnya
  membacanya; 2 gagal beruntun → pensiun; ber-anchor ≥3 run → claim untuk `/.promote`.
- **Tag elemen.** Run mengusulkan `data-e2e` untuk step yang lolos dengan selector ber-`ref`
  `path:line`, divalidasi ke repo (di dalam project, file template, bukan git-ignored, baris
  masih opening tag) dan ditulis ke `tag-proposals.json`. Runtime nol tulis ke template; user
  konfirmasi satu batch, lalu `/.promote` mengangkatnya jadi knowledge ter-Git. Penulisan
  menolak file yang namanya sudah menunjuk file lain sejak dibaca — identitas memakai
  device, inode, ukuran, dan mtime, karena ext4 memakai ulang nomor inode yang baru dibebaskan.
- **Test yang sudah ada** bisa ikut membuktikan klaim lewat `existing_test_command` (argv tanpa
  shell, allowlist file).
- **Job yang worker-nya mati tak di-replay** (`not_recoverable`).
- **Metrik.** Setiap run menambah satu baris `kind: e2e_run` di `quality.jsonl`;
  `--command report` menurunkan bagian `e2e` darinya.

### Dependensi opsional

Playwright dipin di `requirements-e2e.txt` dan hanya dipasang oleh
`python install.py --apply --with-e2e` (pip, lalu `playwright install chromium`). Runtime tetap
stdlib-only; `/.doctor` melaporkan `e2e_readiness` dari metadata paket saja, tak pernah
blocking. Browser dan URL diperiksa di preflight tiap run.

## Keamanan transport opencode

Prompt opencode dikirim sebagai file lampiran (`-f`), bukan argv. Di Windows shim
`opencode.cmd` diurai cmd.exe: contoh JSON ber-kutip di prompt jadi redirect (tiap draft
`/.verify-browser` lewat opencode gagal "The system cannot find the file specified"), dan kutip
ganjil di task = command injection. Argumen yang masih berisi metakarakter cmd ditolak
`unsafe_command_line`.

## Installer

- `install.py --apply` menolak `dist/` yang tak cocok manifest.
- File terpasang dari rilis lama yang tak lagi dikirim dihapus — hanya yang tercatat di receipt
  dan tak diedit user, dengan backup dan rollback.
- Hook settings untuk script pensiun dibuang; `statusLine` workflow di-refresh.
- Upgrade project mengarah ke build yang sedang jalan.
- Re-install idempoten dari checkout bersih: `second_agent.example.json` kini sudah berbentuk
  kanonik, sehingga seed yang disalin verbatim di run pertama tak dibangun ulang jadi `replace`
  di run kedua.

## Transcript sisi premium

`core/audit/transcript.py` membaca transcript Claude Code di `~/.claude/projects/` menjadi
jumlah turn manusia, turn sampai edit pertama, dan total token konteks per sesi. Hanya membaca,
tak pernah menulis. Tiga aturan parsing: schema milik vendor jadi setiap akses defensif; usage
dijumlahkan per pesan, tak pernah diambil yang terakhir; turn user hanya yang benar-benar
diketik manusia (tool result, notifikasi, dan teks sistem yang disuntik harness tidak dihitung).
Record sidechain subagent dihitung terpisah, tidak dibuang.

- Hook `session-bind` kini merekam `transcript_path` yang diberikan Claude Code, sehingga
  transcript sesi bisa ditemukan tanpa menebak nama direktori.
- `project_slug` men-slug path yang sudah absolut apa adanya. Sebelumnya path Windows yang dibaca
  di Linux di-resolve sebagai relatif dan mendapat prefix direktori kerja, menghasilkan slug yang
  tak menunjuk direktori mana pun.
- Konsumen: `bench/observe.py` (metrik *observasi* sesi nyata — tidak diacak, tidak dipasangkan,
  dan dilaporkan sebagai observasi, bukan kausalitas), `bench/driver.py`, `bench/aggregate.py`.

## Thread second_agent per provider

Record sesi dulu menyimpan satu id thread, siapa pun penerbitnya. Setelah `/.provider`
mengganti codex ke opencode di tengah sesi, panggilan berikutnya menyerahkan id thread codex
ke opencode untuk dilanjutkan, dan gagal dalam dua detik (`Session not found`) — terekam di
`usage.jsonl` sebelum perbaikan ini ada. Kini `provider_sessions` menyimpan thread per
provider; executor mengikat record ke provider terpilih tepat sebelum setiap panggilan,
jadi provider baru membuka thread sendiri dan kembali ke provider lama melanjutkan thread
lamanya. `provider_session_id` tetap ada sebagai entri provider aktif untuk semua pembaca
yang sudah memakainya. Record lama tanpa peta diadopsi sekali berdasarkan bentuk id (opencode
`ses_…`); yang tak cocok dibuang dan thread baru dibuat. `/.doctor` kini membaca store sesi
project-local yang sebenarnya dipakai runtime, bukan fallback global.

## Arti `~` di statusline

`~` di statusline dimaksudkan sebagai "angka ini estimasi". Nyatanya ia menyala begitu ada
satu baris yang tidak dihitung provider — termasuk panggilan yang gagal sebelum provider
menghitung apa pun (rate limit, resume yang ditolak). Satu kegagalan membuat sesi yang
seluruh panggilan suksesnya terukur tampak "tidak terukur", dan terbaca sebagai "provider ini
tidak bisa diukur". Panggilan gagal tanpa hitungan provider kini tampil sebagai `N failed` di
samping jumlah calls dan tidak masuk total maupun Saved; `~` tetap menyala untuk panggilan
sukses yang hanya punya estimasi. Kedua flavor (`.ps1`, `.sh`) diubah bersama dan diuji
(`tests/checks/statusline.py`).

## Perbaikan alat rilis dan CI

- `tools/maintain/stamp_version.py --check` gagal di CI karena pola semver `\d+\.\d+\.\d+`
  membaca `127.0.0.1` di skill `verify-browser` sebagai versi "127.0.0". Pola kini dibatasi di
  kedua sisi (`(?<![\d.])\d+\.\d+\.\d+(?!\.?\d)`). Lookahead untuk `.digit` saja tidak cukup:
  digit terakhir bisa mundur, sehingga `10.10.10.10` masih terbaca "10.10.1". Test regresi
  `tests/checks/stamp_version.py` (suite `stamp-version`) mengunci kedua arah.
- Suite `scenario` gagal di runner Ubuntu karena test konfigurasi e2e membaca `headless` dari
  meta, yang dipaksa `true` di mesin tanpa display. Test kini mematok `display_available`;
  fallback tanpa display punya test sendiri di `e2e_hardening`.
- Test race tag elemen memalsukan `os.close` untuk seluruh proses, dan di POSIX `subprocess`
  ikut menutup pipe lewat fungsi itu, sehingga file ditukar sebelum dibaca. Swap kini hanya
  dipicu descriptor file target.

## Migrasi

- Workspace layout v3.5.x: jalankan `upgrade` (atau biarkan auto-upgrade `init`). Backup ada di
  `.workflow/data/backups/<stamp>/`.
- Rancangan awal `/.verify-browser` di build dev memakai `commands.verify_mode = "e2e"` dan blok
  `commands.e2e`, lalu sempat memakai setting request saja tanpa section config. Keduanya
  **tidak pernah dirilis**: `verify_mode` hanya menerima `delegated` dan `syntax` (nilai lain
  memberi warning lalu jatuh ke `delegated`), dan `commands.e2e` dilaporkan sebagai key tak
  dikenal. Pakai section `e2e` di `config.json`.
- Request yang memakai `allowed_mutation_paths` ditolak dengan pesan migrasi (lihat **Breaking**).
- Pilihan provider dari `config/second_agent.json` lama tidak dibawa; pilih ulang dengan
  `install.py --apply --provider … --model …`. Project yang sudah punya
  `.workflow/second_agent.json` tidak tersentuh.

## Yang tidak diverifikasi

- Guard tulis bersifat method-only; `GET` yang mengubah data, pesan WebSocket, dan request
  service worker tidak terlihat.
- Write asinkron bisa tercatat pada step sesudah step yang memicunya.
- Cek identitas sebelum `os.replace` mempersempit race tag elemen, tidak menutupnya: tak ada
  rename ber-cek-identitas di stdlib.
- Adopsi id thread lama hanya bisa membedakan opencode dari yang lain: id codex dan agy sama-sama
  tanpa prefix, jadi record lama milik salah satunya bisa ditawarkan sekali ke yang lain.
- Deteksi GraphQL membaca bentuk body yang umum (JSON, batch, form, raw). Operasi yang
  disamarkan di luar bentuk itu tidak terbaca; pengamannya tetap konfirmasi user atas bukti
  handler.
- **Perbandingan token codex vs opencode belum dilakukan.** Task identik dijalankan di kedua
  provider pada sesi baru masing-masing, tetapi codex menjawab `rate_limited` di kedua
  percobaan, sehingga tidak ada baris codex terukur untuk dibandingkan. Sisi opencode terukur
  (`token_source: provider`): input 35.452 termasuk cache 16.128, output 989 termasuk reasoning
  170 — semantik yang sama dengan codex (cache di dalam input, reasoning di dalam output)
  menurut adapter dan fixture, belum menurut pengukuran berdampingan. Ulangi saat kuota codex
  tersedia.
