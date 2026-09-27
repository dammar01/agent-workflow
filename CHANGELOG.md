# Changelog

From 3.7.3 on, release notes are written in this file: the index table below, then one
section per version under "Release notes". Notes for 3.2.1 through 3.7.2 stay in the
historical archive, one directory per version under `prompt/`.

Release procedure: `RELEASE.md`.

| Version | Notes | Theme |
|---------|-------|-------|
| 3.7.3 | [below](#v373) | **breaking:** key selector `testid` diganti `e2e` (`data-e2e` satu-satunya atribut test); draft `verify-browser` yang rusak mendapat satu perbaikan di thread yang sama; rem `repeat_failure` lepas saat project berubah dan tercatat sebagai knowledge; streak app dikunci signature kegagalan dan dilepas saat runtime berubah; polling player menunggu di dalam Playwright (`page.wait_for_timeout`); git fingerprint tanpa jendela konsol (suite `hidden-spawn`); `/.verify-browser` jadi DELEGATED di semua lapisan (registry dan gate `CLAUDE.md`, intent map, skill dengan alur draft-dulu, reference, runtime contracts); team guide berhenti menyalin flag dan key config; `docs/research/` hanya berisi catatan yang terjadi dan punya hasil (DEC-001, DEC-004..DEC-008, CASE-004, CASE-005), hipotesis/eksperimen/sintesis ditarik ke `docs/research-drafts/` yang di-ignore git, angka CASE-004 dihitung ulang dari telemetry usage, catatan ter-track dibekukan (`CONTRACT.md` §15) |
| 3.7.2 | [prompt/v3.7.2/changelog.md](prompt/v3.7.2/changelog.md) | Rilis dokumentasi, tanpa perubahan runtime: `docs/research/` (metodologi, kontrak pengembangan dan riset, template tujuh jenis catatan), `CLAUDE.md` root untuk pengembangan repo ini, `docs/team-guide/` (panduan adopsi tim: kapan dipakai, task framing, contoh, troubleshooting), index `docs/README.md` dengan kontrak tiga lapis team guide/research/reference, catatan riset (`CASE-001..003`, `SYN-001..002`, `H-001..003`, `EXP-001`, daftar RQ, log periode 2026-09-26), gambar arsitektur baru `architecture.png` menggantikan `flow.png` (versi teks di `docs/architecture/`), `docs/architecture/` terverifikasi, batasan dan benchmark dipindah ke `docs/limitations.md` dan `docs/evaluation/`, telemetry keluar dari README dengan unit call/row dipisah, README ditulis ulang dengan klaim security per provider, koreksi dokumentasi usang |
| 3.7.1 | [prompt/v3.7.1/changelog.md](prompt/v3.7.1/changelog.md) | Menutup temuan audit codebase 2026-09-24 pada batas kepercayaan: key policy `/.verify-browser` jadi config-only (**breaking**), environment OpenCode dipangkas ke allowlist + `.env` project (**breaking**), paritas pola secret `grep`/`read` OpenCode, jalur baca browser menolak alamat metadata, lock store bertoken owner, rollback migrasi dan receipt installer yang melapor jujur, recurrence fact = 5 dari satu sumber |
| 3.7.0 | [prompt/v3.7.0/changelog.md](prompt/v3.7.0/changelog.md) | `/.verify-browser` dan evidence diperketat: **breaking:** body write tak terbaca ditolak (`uninspectable_body`), DELETE dan request pembawa `_method` butuh persetujuan per endpoint, confidence dibatasi rasio anchor tersertifikasi (`anchor_cap`), rem pengulangan menghitung kegagalan preflight; `blocked_requests` fail-closed, izin write lokal, route template; rem per bucket dengan `source`/`mixed` dan `ignore_repeat_brake`; anchor sadar drive Windows; task tanpa argv tak dibatasi, diganti `scope_width` |
| 3.6.0 | [prompt/v3.6.0/changelog.md](prompt/v3.6.0/changelog.md) | `/.verify-browser`: verifikasi lewat browser Playwright, fail-closed, dengan section `e2e` per project + override per run, default headed, retry khusus lingkungan, guard origin dan guard write berbasis alamat (redirect dan DNS rebinding ikut dinilai), credential hanya sebagai nama key dan profil `secrets.json` (list), POST read-only yang dikonfirmasi dengan bukti handler, knowledge browser, usulan tag `data-e2e`, dan Playwright sebagai extra opsional `--with-e2e`; **layout workspace `data/`** — `.workflow/` hanya berisi file yang diedit user, internal pindah ke `.workflow/data/`, workspace v3.5.x dimigrasi otomatis oleh `upgrade` dengan backup dan rollback, `config.json` jadi override saja, `.workflow/current/` baru; **breaking:** `allowed_mutation_paths` dihapus, tak ada fallback ke `config/second_agent.json` level mesin, opencode tanpa model ditolak `model_unset`, seed jadi `config/second_agent.seed.json`; prompt opencode lewat file lampiran (`-f`) alih-alih argv; installer menolak dist yang tak cocok manifest dan membersihkan file rilis lama; thread second_agent disimpan per provider; `~` statusline tak lagi dinyalakan oleh panggilan gagal; transcript Claude Code terbaca untuk mengukur sisi premium; `stamp_version` tak lagi membaca alamat IP sebagai versi; isi yang sempat dicatat sebagai 3.6.1 dan 3.7.0 dirilis di sini; tag pertama sejak v3.5.1 — 3.5.2 dan 3.5.3 tak pernah di-tag dan isinya dirilis bersama tag ini |
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

## Release notes

## v3.7.3

Tiga tema: `/.verify-browser` diklasifikasikan ulang sebagai command DELEGATED di semua
lapisan; tiga perbaikan runtime `/.verify-browser` (draft yang gagal di percobaan pertama,
rem `repeat_failure` yang memaksa sesi baru, dan saran `data-testid` padahal atributnya
`data-e2e`); dan `docs/research/` dibersihkan sampai hanya berisi catatan yang benar-benar
terjadi dan punya hasil. **Breaking:** key selector `testid` dihapus, diganti `e2e`.

### Status rilis

Gerbang `RELEASE.md` dijalankan 2026-09-27 pada working tree rilis ini, setelah perbaikan
runtime (belum di-commit):

| Gerbang | Hasil |
|---|---|
| `python tools/maintain/stamp_version.py --check` | lolos — semua target v3.7.3 |
| `python tools/maintain/gen_manifest.py --check` | lolos — manifest in sync dengan `dist/` |
| `python tools/maintain/sync_intent_map.py --check` | lolos — 5 command delegated |
| `python tools/maintain/sync_skills.py --check` | lolos — 20 skill dan 20 wrapper |
| `python tests/run.py` | lolos — `scenario` PASS (mencakup semua suite check, termasuk `e2e-spec`, `e2e-routing`, `e2e-hardening`, `e2e-tagging`, `e2e-browser`, `e2e-knowledge`, `continuation`); `e2e-smoke` di-skip (opt-in `WORKFLOW_E2E_SMOKE=1`) |
| `WORKFLOW_E2E_SMOKE=1 python tests/run.py --only e2e-smoke` | lolos 2026-09-28 — Chromium sungguhan, Playwright 1.60.0, Windows; termasuk case baru guarded write → `expect_url` (DEC-007). Pertama kali dijalankan sejak 3.7.0: sebelumnya gagal di HEAD `2d6b20e` juga, karena test belum ikut dua kontrak write 3.7.x (lihat di bawah) |
| `python tools/e2e/e2e.py` | lolos — 137 passed, 0 failed, 1 skipped (`delegated commands`, opt-in `--full`) |
| `python tools/e2e/e2e.py --full` | tidak dijalankan: butuh provider sungguhan berbayar |
| `/.verify` (delegated) | nol blocking, nol escalation; verdict runtime `incomplete` karena second agent read-only tak bisa menjalankan suite — ditutup oleh baris di atas. Note yang tersisa (escape karakter kontrol di `e2e_css`, field `source`/`fingerprint` pada knowledge dari jalur preflight, guard eksplisit `repeat_failure` di `promotable_claims`) bernilai low dan ditunda |

Skipped bukan pass: command delegated live (`--full`) belum dijalankan. CI tidak pernah
menyalakan `WORKFLOW_E2E_SMOKE`, jadi `e2e-smoke` tak punya run hijau tercatat sebelum
2026-09-28. Saat dijalankan, suite gagal di dua ekspektasi usang, bukan di runtime: case
penolakan write mengira `allow_side_effects: false` memblok POST ke loopback, padahal
`allow_local_side_effects` (default `true`) meloloskannya dengan sengaja — case itu dan case
POST read-only kini mematikan `allow_local_side_effects`; case CRUD belum menyetujui
DELETE-nya di `allowed_destructive_requests` (wajib sejak 3.7.0). Guard sendiri mencegat
POST navigasi dengan benar (`core/evidence/e2e/browser.py` `Session.guard`). Pemakaian nyata pertama sesudah perubahan: satu draft di project aplikasi
memicu perbaikan stage 1, tetapi tetap `invalid` (claim tanpa assertion) — lihat DEC-006.
Pelepasan rem karena perubahan project belum pernah terjadi di pemakaian nyata.

### Perbaikan `/.verify-browser`

- **Draft tidak lagi gagal di percobaan pertama karena bentuk.** Section `[E2E SPEC]` yang
  datang tapi gagal validasi (scenario di luar fence ```` ```json ````, JSON rusak, nilai di
  luar enum seperti `side_effect`, key selector usang, penolakan policy/grounding) mendapat
  SATU perbaikan di thread provider yang sama, mengutip pesan validator, dan balasannya
  divalidasi ulang dari nol. Sebelumnya hanya section yang hilang yang diminta ulang; section
  rusak langsung `spec_invalid`. Prompt draft kini menyebut nilai `side_effect` yang sah dan
  aturan fence. `meta.e2e.repair` = `{attempted, errors, recovered}`; tanpa
  `provider_session_id` tertulis `reason: no_provider_session`. Bukti: dari 39 draft di
  16 sesi (2026-09-21..27) tidak satu pun `ready` — 34 `invalid`, 5 `blocked`; error
  pertama terbanyak JSON rusak (10), claim tanpa `source_refs` (9), bentuk selector (6)
  (`docs/research/real-cases/CASE-005-…`, keputusan DEC-006; `core/evidence/e2e/spec.py`,
  `runner.py`, `core/prompt/prompt_builder.py`).
- **`repeat_failure` tanpa sesi baru.** Tiap kegagalan yang dihitung menyimpan fingerprint
  project (HEAD + diff + file untracked, di luar `.workflow/`; `core/evidence/e2e/fingerprint.py`).
  Streak di batas yang fingerprint-nya beda dari project sekarang dilepas dan run berjalan
  (`meta.e2e.repeat_released.by = change_detected`). Edit scenario tidak dihitung sebagai
  perubahan. Saat rem menolak, kejadiannya dicatat sebagai knowledge `repeat_failure` yang
  ditawarkan ke draft berikutnya lintas sesi dan pensiun setelah satu run `pass`. Perbaikan
  tanpa perubahan file tetap lewat `ignore_repeat_brake` sekali. Skill `verify-browser`
  berhenti menyarankan sesi baru.
- **Rem `repeat_failure` membedakan kegagalan app.** Streak bucket `app` kini hanya
  bertambah bila signature kegagalannya sama: halaman (`url_after` ternormalisasi, query dan
  id dilipat), field (selector step dari scenario placeholder), dan kondisi (jenis error,
  ekspektasi step, readiness yang tak tercapai) — `classify.failure_signature`, `v1:<digest>`.
  Tiga kegagalan login yang berbeda (form belum render, redirect ditahan runtime, redirect
  itu lagi) dulu dihitung satu streak dan menolak run ke-4. `timeout`/`harness` tetap kasar.
  Tiap outcome mencatat `runtime` (`TOOL_VERSION` + digest `core/evidence/e2e/*.py`); streak
  dari runtime lain dilepas (`repeat_released.by = runtime_changed`). Record `app` lama tanpa
  signature dibaca sebagai tanpa streak. Knowledge `repeat_failure` satu entri per signature.
  Keputusan DEC-008 (merevisi sebagian DEC-005).
- **Polling player tidak lagi menahan event browser.** Loop tunggu (resolusi selector,
  readiness, `expect_url`/`expect_title`) kini menunggu lewat `page.wait_for_timeout`, bukan
  `time.sleep`. Playwright sync hanya mengirim event (route handler guard write,
  `framenavigated`) selama thread berada di dalam panggilan Playwright; `time.sleep` dan
  `page.url` (atribut cache) bukan panggilan itu, jadi POST login yang lewat guard tertahan
  sampai `expect_url` gagal — durasinya selalu ≈ `step_timeout_ms` + 0,8 s, berapa pun
  timeout-nya. Keputusan DEC-007; `core/evidence/e2e/browser.py` `Session.pause`; regresi
  dikunci `tests/checks/e2e_browser.py` `_check_polling_keeps_events_flowing`.
- **Nol jendela konsol dari git fingerprint (Windows).** `fingerprint.py` memanggil `git`
  tanpa `hidden_run_kwargs()`, sehingga worker tanpa konsol memunculkan jendela cmd yang
  berkedip tiap panggilan. Suite baru `hidden-spawn` (`tests/checks/hidden_spawn.py`)
  menolak `subprocess.*` di `core/`, `adapters/`, `utils/` yang tidak menyembunyikan konsol.
- **`data-e2e` satu-satunya atribut test (breaking).** Key selector `testid` diganti `e2e`
  (`[data-e2e="…"]`), sama dengan atribut yang diusulkan tagging. `testid`, `data-testid`,
  dan `data-e2e` sebagai key ditolak dengan pesan yang menyebut `e2e` (dan ikut diperbaiki
  lewat jalur perbaikan draft di atas). Probe halaman membaca `data-e2e`. Entry knowledge
  lama ber-`testid` tidak ditawarkan lagi. Project yang mengandalkan `data-testid` perlu
  menambah `data-e2e` pada elemennya — usulan tag dari run berikutnya melakukannya.

### `/.verify-browser` jadi DELEGATED

Sebelumnya tiga lapisan tidak sepakat: `CLAUDE.md` bundle menaruhnya di registry LOCAL dan
skill-nya menyebut "no pre-flight gate", reference menyebutnya delegated, dan intent map
tidak mengenalnya sehingga prefix `/.verify-browser` terbaca sebagai `verify`
(keputusan: `docs/research/decisions/DEC-001-verify-browser-delegated.md`).

- `dist/config/claude/CLAUDE.md`: masuk registry DELEGATED dan pre-flight gate; tiga stage
  dijelaskan — second_agent menulis draft spec (stage 1) dan mereview bukti (stage 3),
  runtime player (proses anak runtime) menjalankan Playwright (stage 2).
- `dist/config/claude/hooks/intent-map.json`: `verify-browser` di daftar delegated, pola
  prefix diperbaiki agar tidak tertelan `verify`, frasa pemicu ditambah.
- `dist/config/claude/skills/verify-browser.md`: alur dibalik — tulis request minimal,
  jalankan draft, wawancara hanya bila draft melaporkan target belum dikonfigurasi atau tak
  terjangkau. `config.json` dibaca runtime, bukan main_agent sebelum run.
- `tools/maintain/sync_intent_map.py`: nama command boleh mengandung `-`.
- `README.md`, `docs/reference.md`, `docs/runtime-contracts.md`: klasifikasi dan tahapan
  diseragamkan.

Trade-off: main_agent tidak lagi membaca `config.json` sebelum draft pertama, jadi project
yang belum dikonfigurasi membayar satu panggilan draft sebelum wawancara.

### Team guide

`getting-started.md`, `when-to-use.md`, `examples.md`, `troubleshooting.md`: flag, key
config, dan path tidak lagi disalin — diganti link ke anchor reference. `verify-browser`
masuk tabel contoh.

### Research: hanya yang terjadi dan punya hasil

Audit maintainer terhadap setiap catatan: apa buktinya dan bagaimana dibuat. Tidak ada
hipotesis, eksperimen, atau sintesis yang pernah dijalankan atau punya hasil.

- **Draft ditarik dari repo.** H-001..H-007, EXP-001..EXP-003, SYN-001..SYN-002, DEC-002,
  DEC-003 dipindah ke `docs/research-drafts/`, yang di-ignore git (area kerja lokal
  maintainer). H-001..H-003, EXP-001, SYN-001..SYN-002 yang ter-track di v3.7.2 terhapus
  dari tree. Catatan ter-track menyebut ID draft sebagai teks, bukan link.
- **CASE-001..003** juga ditarik ke draft: kejadiannya nyata, tetapi tidak ada artefak yang
  disimpan, jadi tidak bisa dicek ulang. Sebelum dipindah, penjelasan kausal dan angka
  tanpa sumber sudah dihapus dari isinya. Ketiganya ter-track di v3.7.2 dan terhapus dari
  tree.
- **CASE-004** angka usage dihitung ulang maintainer 2026-09-27 langsung dari 255 baris
  pertama `.workflow/data/usage.jsonl`; hasilnya cocok dengan angka draft. Angka dari
  sumber lain (evidence, facts, jobs, recovery) tidak dihitung ulang dan dihapus. Hanya
  bisa direproduksi di mesin yang memegang `.workflow/`.
- **DEC-001** tetap: keputusannya terimplementasi di kode; validasi tetap `not_validated`.
- **`validated_by` / `validated_on`** di DEC-001 dan CASE-004 diisi `maintainer` /
  `2026-09-27`: siapa dan kapan status `observed`/`not_validated` ditetapkan, bukan tanda
  tervalidasi.
- **Aturan baru** `docs/research/CONTRACT.md` §15, juga di `CLAUDE.md` root dan
  `docs/README.md`: catatan ter-track dibekukan per 3.7.3 — isinya hanya berubah untuk
  menambah hasil baru atau mengoreksi fakta terhadap sumbernya.
- Template dan `methodology.md`: blok `disposition` dan tier evidence.
- **`methodology.md` diselaraskan dengan `CONTRACT.md` §14.** Methodology menyatakan
  observasi creator tak pernah `validated`, tanpa menyebut pengecualian DEC di §14 (DEC
  `validated` lewat run direct-use maintainer yang bersih). Pengecualian itu kini disebut,
  dibatasi ke DEC saja.
- **Validasi research bukan gerbang tag.** `RELEASE.md` langkah 5 kini menggerbangi tag
  dengan test, `e2e-smoke`, `tools/e2e/e2e.py`, dan pemakaian nyata yang aman. DEC-001,
  DEC-004..DEC-008 dirilis dengan disposition apa adanya; validasinya lewat jalur §14
  dijadwalkan untuk 3.7.4. CASE-004 dan CASE-005 tetap `observed`: `validated` untuk CASE
  butuh evaluasi terdesain (`EXP-XXX`) yang belum ada.

### Proses rilis

- **Catatan rilis pindah ke `CHANGELOG.md`.** Mulai 3.7.3 tidak ada lagi
  `prompt/v<versi>/changelog.md`; `prompt/` menjadi arsip historis sampai v3.7.2.

### Batasan yang diketahui

- Alur `verify-browser` yang dibalik belum dijalankan end to end.
- `docs/research-drafts/` hanya ada di satu mesin; tidak ada backup otomatis.
- Temuan v3.7.2 soal parser verify (`_NONE_ITEM` hanya menerima `none`/`(none)`) belum
  ditangani.
