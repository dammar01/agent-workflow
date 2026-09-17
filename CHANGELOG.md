# Changelog

Full release notes live one directory per version under `prompt/`. This file is the index
and the place to look first; it does not duplicate the notes.

Release procedure: `RELEASE.md`.

| Version | Notes | Theme |
|---------|-------|-------|
| Unreleased | — (notes land in `prompt/<version>/changelog.md` at release) | **Layout 3.7.0 (breaking untuk workspace lama, dimigrasi otomatis).** `.workflow/` kini hanya berisi file yang diedit user (`config.json`, `second_agent.json`, `e2e/secrets.json`, script, `current/`); semua internal (sesi, log, stream usage/audit/quality/redactions, evidence, facts, knowledge browser, cache, backup) pindah ke `.workflow/data/`, dengan `workspace_paths.workflow_paths` sebagai satu-satunya sumber path. Workspace 3.6 tetap jalan apa adanya sampai `upgrade` memigrasinya: backup ke `data/backups/<stamp>/`, lock store dipegang, item dipindah lewat `data.migrating/` lalu rename sekali, path evidence ditulis ulang, sisa lama dihapus, `secrets.json` format objek dikonversi; gagal → dikembalikan (+ `migration-backup-*`); job hidup → ditolak; hook dan run script memakai aturan layout yang sama saat runtime. `config.json` jadi **override saja** — default tak lagi ditulis (key hilang = default bawaan build), upgrade membuang nilai yang sama dengan default dan key pensiun. `.workflow/current/` baru: mirror dispatch terakhir (`session.json`, `progress.jsonl`, event browser live, report/screenshot terakhir). `doctor` melaporkan `workspace_layout` dan file tak dikenal di root. **Stabilisasi 3.6.1.** Keamanan: prompt opencode dikirim sebagai file lampiran (`-f`), bukan argv — di Windows shim `opencode.cmd` diurai cmd.exe, sehingga contoh JSON ber-kutip di prompt jadi redirect (tiap draft verify-browser opencode gagal "The system cannot find the file specified") dan kutip ganjil di task = command injection; argumen yang masih berisi metakarakter cmd ditolak `unsafe_command_line`. Guard write e2e kini mengikuti redirect sendiri (`route.fetch(max_redirects=0)`, tiap hop 307/308 dinilai) karena redirect tak pernah lewat route handler — POST 307 sebelumnya mengirim ulang body ke tujuan mana pun; redirect navigasi keluar origin dideteksi dan menggagalkan step; nama yang resolve ke loopback kini dipatok (DNS rebinding); write https ke host terpatok ditolak; nilai expected di event dalam bentuk placeholder; JWT/token campuran diredaksi dari ledger. `.test` diperlakukan persis localhost (write tanpa lookup/patokan). Retry yang baru lolos di percobaan berikutnya → `incomplete`, bukan pass; headed tanpa display di Linux → headless + warning; job verify-browser yang worker-nya mati tak di-replay (`not_recoverable`). **Breaking:** tak ada lagi fallback ke `config/second_agent.json` level mesin — project tanpa/rusak `.workflow/second_agent.json` ditolak (`provider_config_missing`/`invalid`); opencode tanpa model ditolak `model_unset` (tanpa `-m` opencode memakai model terakhir, sumber mimo); seed kini `config/second_agent.seed.json` dibangun ulang tiap `--apply`, file lama dipensiunkan; route `e2e_spec` bisa diberi model. Knowledge browser setara fact (`.workflow/e2e-knowledge.jsonl`): run mencatat alur login/logout, route, readiness, selector yang terbukti per origin; draft berikutnya membacanya lewat sidecar; 2 gagal beruntun → pensiun; ber-anchor ≥3 run → claim `/.promote`. `install.py --apply` menolak dist yang tak cocok manifest, menghapus file terpasang rilis lama yang tak lagi dikirim (hanya yang tercatat & tak diedit; backup + rollback), membuang hook settings untuk script pensiun, me-refresh `statusLine` workflow; upgrade mengarah ke build yang sedang jalan. Sebelumnya di rilis ini: `/.verify-browser` tanpa wawancara dan tanpa gate: section `e2e` di `.workflow/config.json` jadi default terpasang per-project (request cuma menulis selisihnya), knob salah di config jadi warning + fallback (`meta.e2e.config_warnings`) bukan error seperti di request; default berubah jadi **headed** (`headless: false`), headless dipasang manual di config; draft `ready` langsung lanjut ke run — AskUserQuestion "Jalankan | Batal" dihapus, yang tersisa cuma stop teknis (draft blocked/invalid, `missing_env`) dan izin `read_only_requests`; retry otomatis `settings.max_retries` (default 2, ceiling 5) untuk `incomplete` berlatar lingkungan saja — `fail` origin=app dan `allow_side_effects: true` tak pernah diulang, dua percobaan identik per step berhenti sebagai `stable`, artifact retry di `e2e/retry<n>/`. Origin dan write jadi dua pertanyaan terpisah: nama `.test` lolos tanpa `allow_remote` (RFC 6761), domain sungguhan tetap butuh `allow_remote` + `allowed_origins` persis, sedangkan write dinilai dari ALAMAT — tiap write host di-resolve, ditolak bila publik/link-local/tak ter-resolve, lalu dipatok ke browser via `--host-resolver-rules` (browser non-chromium ditolak untuk write non-loopback). Tag elemen: run mengusulkan `data-e2e` untuk step yang lolos dengan selector ber-`ref` `path:line`, divalidasi ke repo (di dalam project, file template, bukan git-ignored, baris masih opening tag), ditulis ke `tag-proposals.json`; runtime nol tulis ke template — user konfirmasi satu batch, lalu `/.promote` yang mengangkatnya jadi knowledge ter-Git. Sebelumnya di rilis ini: CRUD dan kontrak draft: **breaking** — `secrets.json` jadi list `{name, credentials}` dengan nama key dari registry kode (`E2E_USER`, `E2E_PASS`), format objek lama ditolak tanpa konversi, generator template atomik (hard link, aman dari draft bersamaan); `allowed_mutation_paths` dihapus (error migrasi), write hanya ke host loopback dan `allow_side_effects` dengan base_url remote ditolak preflight, POST read-only tetap; step wajib `id` stabil, `within`, readiness deklaratif (`ready`, bukan `networkidle`), `request` yang diharapkan, `test_data.marker`, cleanup berupa step executable (`scenario.cleanup`, `cleans`) yang dilaporkan terpisah dan membuat verdict `INCOMPLETE` bila gagal/tak jalan; ledger request non-GET (endpoint tersanitasi, status, durasi, atribusi step atau `uncertain`, planned); laporan `trail` step → selector (jumlah match, fallback) → readiness → request → hasil; aksi write tak pernah dikirim ulang saat timeout |
| 3.6.0 | [prompt/v3.6.0/changelog.md](prompt/v3.6.0/changelog.md) | `/.verify-browser`: verifikasi lewat browser Playwright dari request per sesi, fail-closed, dengan guard navigasi dan guard tulis (`allowed_mutation_paths`), credential hanya sebagai nama key dan profil `secrets.json` (dibuat draft bila belum ada), POST read-only yang diusulkan dengan bukti handler lalu dikonfirmasi user, dan Playwright sebagai extra opsional `--with-e2e`; thread second_agent disimpan per provider sehingga ganti provider di tengah sesi tak lagi me-resume thread asing; `~` statusline tak lagi dinyalakan oleh panggilan gagal; transcript Claude Code terbaca untuk mengukur sisi premium; `stamp_version` tak lagi membaca alamat IP sebagai versi; tag pertama sejak v3.5.1 — 3.5.2 dan 3.5.3 tak pernah di-tag dan isinya dirilis bersama tag ini |
| 3.5.3 | [prompt/v3.5.3/changelog.md](prompt/v3.5.3/changelog.md) | opencode ikut terukur: adapter meminta `--format json`, membaca `step_finish.part.tokens`, dan melipat `cache.read` ke input serta `reasoning` ke output karena opencode melaporkan keduanya sebagai addend terpisah; stdin ditutup karena mode JSON menggantung tanpa itu; `clean_output` menyusun jawaban dari event `text` |
| 3.5.2 | [prompt/v3.5.2/changelog.md](prompt/v3.5.2/changelog.md) | Token nyata dari provider masuk ke usage.jsonl: reasoning dan cached ikut terekam sebagai rincian yang tak pernah dijumlahkan; satu baris per panggilan provider; codex terukur, opencode tetap estimasi dengan alasan yang dicatat; statusline global merender angka itu di setiap prompt, dikirim sebagai `workflow-statusline.{ps1,sh}` |
| 3.5.1 | [prompt/v3.5.1/changelog.md](prompt/v3.5.1/changelog.md) | Task cap diturunkan dari transport provider (argv vs stdin) alih-alih satu konstanta; kegagalan tulis stdin codex tak lagi ditelan; perbaikan parsing digest CRLF |
| 3.5.0 | [prompt/v3.5.0/changelog.md](prompt/v3.5.0/changelog.md) | Knowledge ter-Git dan `/.promote` yang menulisnya; generator skrip runner dengan deteksi drift; benchmark dijalankan sungguhan |
| 3.4.5 | [prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) | Release stability (CI, test runner, release procedure) and the measurement layer: workflow contracts, telemetry, governance, verified graphify; agy provider behind an explicit opt-in |
| 3.4.4 | [prompt/v3.4.4/changelog.md](prompt/v3.4.4/changelog.md) | Second provider: codex adapter, provider registry, read-boundary findings |
| 3.4.2 | [prompt/v3.4.2/changelog.md](prompt/v3.4.2/changelog.md) | — |
| 3.4.1 | [prompt/v3.4.1/changelog.md](prompt/v3.4.1/changelog.md) | — |
| 3.4.0 | [prompt/v3.4.0/changelog.md](prompt/v3.4.0/changelog.md) | — |
| 3.3.1 | [prompt/v3.3.1/changelog.md](prompt/v3.3.1/changelog.md) | — |
| 3.3.0 | [prompt/v3.3.0/changelog.md](prompt/v3.3.0/changelog.md) | — |
| 3.2.1 | [prompt/v3.2.1/changelog.md](prompt/v3.2.1/changelog.md) | — |
| 3.2.0 | [prompt/v3.2.0/](prompt/v3.2.0/) | — |
| 3.1.2 | [prompt/v3.1.2.md](prompt/v3.1.2.md) | — |
| 3.1.1 | [prompt/v3.1.1.md](prompt/v3.1.1.md) | — |
| 3.1.0 | [prompt/v3.1.0.md](prompt/v3.1.0.md) | — |
| 3.0.1 | [prompt/v3.0.1.md](prompt/v3.0.1.md) | — |
| 3.0.0 | [prompt/v3.0.0.md](prompt/v3.0.0.md) | — |
| 2.0.0 | [prompt/v2.0.0.md](prompt/v2.0.0.md) | — |
| 0.0.0 | [prompt/v0.0.0.md](prompt/v0.0.0.md) | — |

v3.4.3 was built but never released; its changes are described inside the v3.4.4 notes.

## Termasuk di tag v3.4.5, di luar catatan rilisnya

Tag `v3.4.5` menunjuk `6ef1be0`, bukan commit tempat
[prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) ditulis. Butir di bawah ada di
dalam tag dan tidak ada di catatan rilis itu. Nomor versinya sengaja tidak dinaikkan:
`bench/BENCHMARK-PLAN.md:5` sudah mengunci v3.4.5 sebagai versi system under test, dan
menggeser nomornya sekarang berarti benchmark mengukur versi yang namanya berbeda dari
rencananya. Yang dibayar untuk itu adalah baris ini — tanpanya, tag dan catatannya
berselisih diam-diam.

Two of the gaps the v3.4.5 notes list under "Yang belum ditutup" are closed on `dev`
(2026-08-18), plus one the notes never listed — the installer had no tests of its own,
which nothing had recorded as a gap because the integration tests passing made it look
covered:

- **`correlation_id` now aggregates a task chain.** A plan records its derived id as the
  session's active chain (`state.json` key `chain`); the execute and verify that follow
  adopt that id instead of deriving their own, so one piece of work lands in `usage.jsonl`
  as one subject. Without a chain the old derivation still applies. Proven by
  `_correlation_chain` in `tests/checks/contracts.py`.
- **The installer has dedicated unit tests.** `tests/checks/installer.py` adds four checks
  — lenient decode, intent stanzas and managed-block splice, hook refresh with user-hook
  preservation and the POSIX rewrite, receipted rollback and settings drift — registered
  in both entry points.
- **`python tools/e2e.py --full` has been run against a live provider**: 98 passed,
  0 failed, 0 skipped, including the paid [DELEGATED] block (explore + sweep). Run on the
  `dev` working tree carrying the two fixes above, not on the 3.4.5 tag itself.

The measurement layer that sat here — workflow contracts, telemetry, governance, verified
graphify, the duplicated-warning fix, the stdlib-only test, and the benchmark harness —
was folded into [prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) rather than held
for a later number. It had been built after the version bump, so the release it belonged to
described none of it.
