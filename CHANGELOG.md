# Changelog

Full release notes live one directory per version under `prompt/`. This file is the index
and the place to look first; it does not duplicate the notes.

Release procedure: `RELEASE.md`.

| Version | Notes | Theme |
|---------|-------|-------|
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

## Unreleased — sesudah tag v3.4.5

Perubahan di bawah ada **setelah** `6ef1be0`, jadi tidak ada di dalam tag. Sebagian besar
di `bench/`, yang tidak ikut dikirim (`dist/`) dan tidak mengubah runtime; kelompok
terakhir menyentuh gerbang sinkronisasi bundel dan satu default yang dihapus.

- **`bench/oracle.py` punya verdict keempat: `security_violation`.** Unit yang gagal di
  stage `checks` tidak lagi terbaca sebagai `rejected` biasa. Alasannya bukan kerapian:
  angka yang paling tidak boleh kabur dalam benchmark agen terdelegasi adalah seberapa
  sering delegatnya melewati batas, dan menggabungkannya dengan assertion gagal
  menghapus angka itu. Perubahan sah karena nol unit sudah dipanen — edit yang sama
  setelah panen pertama tidak sah, dan log pembekuan di kepala file mencatat tanggalnya.
- **`bench/aggregate.py` melaporkan `security_violations` per arm.** Filter `accepted`
  sudah eksklusif, jadi verdict baru gugur dari pembilang dengan sendirinya — tapi gugur
  diam-diam adalah cara sebuah temuan berhenti dilaporkan sama sekali.
- **`bench/test_oracle.py` mengunci pemetaan verdict.** Sengaja tidak didaftarkan di
  `tests/run.py`: oracle menjalankan suite itu sebagai stage 2-nya sendiri, jadi test
  bench di sana membuat oracle menilai dirinya sendiri.
- **`bench/driver.py` menjalankan satu unit dalam enam fase.** Worktree di `base_sha`, cek
  kebocoran terhadap `answer_sha`, session id per unit, stempel jam, oracle, teardown. Tiga
  fase lain milik operator dan ditandai begitu: sesi agennya sendiri, dan `finish` yang
  menstempel `rework_cycles` serta `main_agent_rewrote`. Otomasi berhenti di sana dengan
  sengaja — `BENCHMARK-PLAN.md:223` mendefinisikan arm C sebagai sesi Claude plus
  `.workflow`, jadi mengotomatiskan `main.py` saja akan memberi label arm C kepada sisi
  worker yang berdiri sendiri.
- **`bench/collect.py` menulis `ledger.jsonl`.** Satu-satunya penulis skema §7;
  `aggregate.py` satu-satunya pembaca. Menolak baris tanpa biaya premium kecuali
  `--allow-missing-cost` diminta, karena `aggregate._spend` memaksa nilai hilang jadi `0.0`
  dan arm yang ekspornya hilang akan terbaca sebagai yang termurah. Bentuk ekspor tokenburn
  tidak ditebak: kalau kunci sesi tak ditemukan, yang dicetak adalah kunci yang benar-benar
  ada, bukan sederet null.
- **`bench/policy.py` mengumpulkan batas run.** Waktu per unit, timeout stage oracle, cap
  `rework_cycles`, cap panggilan terdelegasi, budget per unit dan per run, daftar karantina
  flaky. Tiga yang pertama ditegakkan live oleh `driver.py`; budget hanya dilaporkan
  `collect.py` sesudahnya, karena biaya datang dari tokenburn setelah run selesai dan
  menyebutnya penegakan akan jadi klaim palsu tentang apa yang harness bisa lihat.
- **Stage 2 oracle membaca daftar karantina.** Dengan daftar kosong perintahnya tak berubah.
  Dengan isi, nama suite diminta ke `tests/run.py --list` saat runtime alih-alih disalin ke
  `policy.py` — salinan akan melenceng begitu ada suite baru, dan melencengnya diam-diam
  mengecilkan gerbang penerimaan. Karantina penuh melaporkan `ran: False`, bukan jatuh ke
  suite default yang justru baru dikecualikan.
- **Ledger dapat empat kolom.** `unit_seconds`, `timed_out`, `quarantined_suites`,
  `over_unit_budget`. Baris yang dinilai dengan gerbang dikurangi tidak sebanding dengan
  baris bergerbang penuh, dan sekarang perbedaan itu terbaca dari datanya sendiri.
- **Dua defect ditemukan saat verifikasi, bukan saat menulis.** `collect.py` gugur
  seluruhnya ketika satu unit record rusak — cabang "lewati dan catat" yang ada di kode tak
  pernah terjangkau karena pembacanya melempar lebih dulu, jadi satu file rusak dari 135
  akan membunuh seluruh harvest. Record rusak sekarang dilewati dan disebut namanya;
  ekspor tokenburn rusak tetap gugur keras, karena itu bukan satu baris melainkan sumber
  biayanya. `driver.py prepare --repeat -1` juga diterima dan mencetak unit bernama
  `T01_A_-1`; `repeat` di bawah 1 kini ditolak. Keduanya muncul dari menjalankan jalur
  gagalnya, sesudah penelusuran kode menyatakan area itu bersih.
- **Putaran verifikasi kedua menemukan yang pertama lewatkan.** Unit record ber-`status:
  finished` tetapi kekurangan key tidak gugur — `build_row` membaca semuanya lewat `.get()`,
  jadi ia terpanen sebagai baris 28-dari-33 null. Itu kegagalan yang lebih buruk daripada
  crash: baris dengan `arm` terisi dan `verdict` kosong tetap masuk penyebut `per_arm` dan
  menyeret `first_pass_correctness` serta `cost_per_accepted_task_usd` untuk unit yang tak
  pernah dijalankan. `collect.py` kini memvalidasi kunci wajib dan melewati record tak
  lengkap sambil menyebut kunci yang hilang; daftar kunci itu diimpor dari `driver.py`
  alih-alih disalin, karena salinan akan melenceng. `driver.load_unit` memvalidasi hal yang
  sama sehingga record separuh berhenti sebagai kalimat, bukan `KeyError` tiga frame dalam.
  Tiga rangkai peringatan di `collect.py` dipaksa `str` — satu `task_id` bernilai `None`
  sebelumnya menggugurkan peringatannya sendiri dengan `TypeError`.

  Ketiganya, seperti dua sebelumnya, muncul dari menjalankan jalur gagal sesudah
  penelusuran kode menyatakan area itu bersih.
- **Putaran verifikasi ketiga menemukan path traversal lewat `task_id`.** `unit_id()`
  menempelkan `task_id` mentah ke nama direktori, nama file record, dan `session_id`; entri
  corpus bertanda `task_id: "../ESCAPE"` menaruh worktree di `bench/ESCAPE_A_1` dan
  recordnya di `bench/ESCAPE_A_1.json`, keduanya di luar direktori yang dimaksud, dan
  `teardown` kemudian menjalankan `git worktree remove --force` di sana. Jalur ini normal,
  bukan adversarial: generator corpus sengaja meninggalkan field untuk diisi tangan, dan
  tangan yang sama menulis `task_id`. `check_task_id()` kini menolak apa pun di luar
  `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, dan `_contained()` me-resolve tiap path lalu
  membuktikannya tetap di bawah induknya. Validasi, bukan sanitasi —
  `utils.path_guard.safe_path_component` menulis ulang `T01` jadi `T01--<hash>` karena
  memperlakukan huruf besar sebagai non-portabel, dan mengganti nama task operator diam-diam
  lebih buruk daripada menolaknya.
- **Jendela worktree yatim di `prepare` ditutup.** Proses yang mati antara `git worktree add`
  dan penyimpanan record meninggalkan direktori yang tak ditunjuk record mana pun: tak
  terlihat `list`, tak terjangkau `teardown`. Record kini ditulis lebih dulu dengan status
  `preparing`, sehingga interupsi terburuk meninggalkan record yang menyebutkan worktree-nya
  sendiri. `judge`, `finish`, dan `collect.py` menolak record berstatus `preparing`.
- **`BENCHMARK-PLAN.md` §5 menyusul isi `bench/` yang sebenarnya.** Daftar strukturnya
  menyebut 7 item sementara direktorinya sudah berisi 10+; kini lengkap, dengan catatan mana
  yang dilacak git dan mana yang scratch.
- **Putaran verifikasi keempat: dua cacat yang membuat angka worker arm C selalu nol.**
  Keduanya kelas yang sama — nilai yang hilang berubah jadi angka yang meyakinkan — dan
  keduanya baru ketahuan saat `call.meta.json` **asli** diadu dengan kode, bukan saat
  kodenya dibaca. Pertama, `collect._worker_totals` mencari `input_tokens` /
  `prompt_tokens` / `input_chars_est`; runtime menulis `estimated_input_tokens` dan
  `estimated_output_tokens` di level atas tanpa pembungkus `usage`
  (`core/executor.py:1001-1017`), jadi `int(None or 0)` mengubah "tak ketemu" jadi nol
  sementara barisnya tetap berlabel `token_source: estimated` — terbaca terukur padahal
  tidak. Kedua, path sesi disusun dari `session_id` mentah, sedangkan runtime melewatkannya
  ke `safe_path_component` (`core/workspace_paths.py:99`), yang mengubah `bench_T01_C_1`
  jadi `bench_T01_C_1--24cc82773233` karena huruf besar dianggap non-portabel; path yang
  dicari tak akan pernah ada, dan hasilnya terbaca `no_logs_found`, seolah unitnya memang
  tak punya log. `collect.py` kini mengimpor `safe_path_component` dari `utils.path_guard`
  — mengikuti runtime alih-alih menebaknya — membaca nama field yang benar, dan mengambil
  `worker_token_source` dari field `token_source` milik runtime alih-alih mengklaimnya
  sendiri. Dibuktikan dengan memanen `call.meta.json` asli: 739 token input, 2371 output,
  yang sebelumnya terbaca 0/0.
- **Stage 3 oracle memakai `shlex`, dan menolak backslash.** `str.split()` memecah
  `--only "tests/test foo.py"` jadi dua argumen rusak, menggagalkan stage karena alasan
  milik harness, bukan milik unit yang diuji. `posix=True` mengutip benar tetapi memakan
  backslash — `tools\e2e.py` diam-diam jadi `toolse2e.py` — jadi alih-alih memilih mangling
  yang lebih ringan, backslash ditolak dengan pesan. Forward slash bekerja di Windows untuk
  tiap path yang Python buka.
- **Stage 3 dan 4 oracle akhirnya dijalankan.** Tiga putaran verifikasi sebelumnya selalu
  berhenti di `incomplete` karena entri corpus ujinya tidak punya `oracle_tests`. Dengan
  entri berisi, harness menghasilkan verdict `accepted` pertamanya, dan pipeline penuh
  sampai `aggregate` menghitung `cost_per_accepted_task_usd` yang benar: total belanja
  dibagi jumlah task diterima, percobaan gagal tetap di pembilang.
- **Putaran verifikasi kelima: fase `delegate` selama ini mengukur pengiriman, bukan
  hasil.** `driver.delegate` memanggil `main.py --command explore`, padahal keempat command
  yang boleh dipakainya ada di `BACKGROUND_COMMANDS` (`main.py:51`). Dispatch jatuh ke
  `submit()`, yang kembali seketika begitu worker di-spawn dengan `{ok, status, job_id}` —
  nol `evidence_ref`, nol isi. Akibatnya `evidence_reused_hits` selalu 0 dan `ok` di log
  delegasi menggambarkan pengiriman, bukan pekerjaan: unit akan mencatat panggilan
  terdelegasi sukses tanpa peduli apa yang worker lakukan. Kelas yang sama dengan dua cacat
  putaran keempat — nilai kosong menyamar jadi angka yang meyakinkan. `delegate` kini
  memanggil `--command await --job-command <cmd>`, yang memblokir sampai hasil nyata, dengan
  `--poll-timeout` diturunkan dari sisa waktu unit.
- **`BENCHMARK-PLAN.md` Fase 4 dikoreksi, bukan kodenya.** Rencananya menyebut sisi worker
  dipanen dari `call.meta.json` **dan** `storage/jobs/job_<id>.json`; `collect.py` cuma
  membaca yang pertama. Record job asli dibaca untuk memutuskan mana yang salah: 19 key,
  semuanya catatan siklus hidup — `worker_pid`, `worker_identity`, `status`, `error` — dan
  nol di antaranya ada di skema §7. Yang dulu diharapkan darinya, yaitu alasan kegagalan,
  kini datang langsung di balasan `await`. Fase 4 sekalian mencatat bahwa nama direktori
  sesi melewati `safe_path_component`, supaya kekeliruan putaran keempat tidak terulang
  lewat dokumen.
- **Putaran verifikasi keenam: arm C tidak pernah bisa dijalankan sama sekali.** Worktree
  segar tidak punya `.workflow`, dan panggilan terdelegasi pertama mati di
  `[Errno 2] No such file or directory: ...\.workflow\config.json` dalam dua detik —
  padahal `prepare` sudah menyetel `workflow_installed: true`. Klaim, bukan tindakan, untuk
  arm yang justru merupakan subjek seluruh studi. `prepare` kini menjalankan
  `main.py --command init` untuk arm C, memeriksa bahwa `.workflow/config.json` benar-benar
  ada, dan membatalkan unit bila tidak; flagnya merekam apa yang terjadi.

  Putaran kelima menandai ini `low` atas dasar penalaran bahwa runtime melakukan auto-init.
  Penalarannya salah, dan yang membuktikannya adalah menjalankannya — bukan membacanya lagi.
- **Path absolut berhenti masuk `ledger.jsonl`.** `build_row` menulis `worktree` apa adanya,
  dan ledger dilacak git: layout direktori operator terbit ke siapa pun yang clone repo, dan
  dua run di dua mesin terbaca sebagai data berbeda padahal bukan. Kini dinormalkan relatif
  terhadap root repo dengan forward slash; path di luar repo disusutkan jadi nama daunnya.
  Unit record tetap memegang path absolut, karena harness memang membutuhkannya dan
  `units/` tidak dilacak.
- **`BENCHMARK-PLAN.md` Fase 2 menyusul kodenya.** Contoh arm C-nya masih menampilkan
  `main.py --command explore`, bentuk yang perbaikan `await` sudah tinggalkan satu putaran
  sebelumnya. Diganti ke bentuk `await` beserta alasannya, plus catatan bahwa `prepare`
  memasang `.workflow` lebih dulu.
- **Arm C dijalankan sungguhan untuk pertama kalinya.** Satu unit penuh: `prepare` memasang
  `.workflow`, `delegate` mengembalikan hasil nyata dalam 68 detik (`ok: true`) alih-alih
  dua detik status-pengiriman, runtime membuat direktori sesi bernama
  `bench_T01_C_1--24cc82773233` persis seperti yang diprediksi perbaikan
  `safe_path_component`, dan `collect.py` memanen 615 token input serta 339 output dari
  `call.meta.json` yang dihasilkan run itu. Dua perbaikan yang sebelumnya hanya diverifikasi
  secara struktural kini terbukti terhadap kenyataan.
- **README punya section Benchmark.** Sebelumnya `bench/` tidak disebut sama sekali di
  dokumentasi utama.

### Gerbang sinkronisasi bundel

Ketiganya berangkat dari satu pemeriksaan: apa yang bisa melenceng antara `dist/`, skill,
dan config tanpa ada yang menyadarinya. Jawabannya tiga hal, dan dua di antaranya sudah
punya tool yang menganggur.

- **`tools/sync_skills.py` dan `tools/sync_intent_map.py` akhirnya punya pemanggil.**
  Docstring keduanya menulis "for CI / doctor" sejak ditulis, dan tak satu pun pernah
  dipanggil dari mana-mana: `ci.yml` menggerbangi `stamp_version` dan `gen_manifest` saja.
  Artinya skill baru tanpa entri registry, atau pattern `intent-map.json` tanpa trigger
  NL-map yang cocok, lolos merge dengan hijau — gerbang runtime dan prompt jadi berbeda
  pendapat soal apa yang DELEGATED. `tests/checks/bundle_sync.py` memanggil keduanya lewat
  entry point aslinya, terdaftar di `tests/run.py` dan `tests/scenario.py`, jadi ia ikut
  `python tests/run.py` di CI maupun di mesin lokal. `tools/e2e_installer.py` sebelumnya
  memeriksa skill dan `intent-map.json` *terpasang*, bukan *sepakat* — bentuk yang terbaca
  seperti cakupan tanpa menjadi cakupan.
- **`stamp_version.py --check` tidak lagi buta di luar daftarnya.** `TARGETS` tetap
  menjawab "baris mana yang saya tulis ulang", dan `SCAN_PATHS` + `EXEMPT` yang baru
  menjawab "penyebutan versi mana yang tak seorang pun daftarkan" — kelas melenceng yang
  allowlist tak bisa lihat secara struktural, dan persis cara `BENCHMARK-PLAN.md` dulu
  hanyut ke v3.4.4. Penulisan tetap `TARGETS`-saja: scan yang juga menulis ulang akan,
  pada pengecualian pertama yang terlupa, mengubah kalimat tentang masa lalu jadi kalimat
  tentang sekarang — dan yang paling terpapar justru catatan migrasi dan changelog.
- **`bench/BENCHMARK-PLAN.md` keluar dari `TARGETS`.** Menstempelnya salah sejak awal:
  README dan `bench/STATE.md` sama-sama mencatat SUT dibekukan di sebuah tag, jadi baris
  `**Versi SUT:**` justru **tidak boleh** mengikuti `TOOL_VERSION`. Bump berikutnya akan
  memutus identitas SUT benchmark tanpa suara.
- **Tiga key pensiun dari `second_agent.json`.** `job_max_runtime_seconds`,
  `job_poll_timeout_seconds`, dan `agent_workflow_path` ditulis `default_provider_config()`
  dan tidak pernah dibaca siapa pun — nilainya selalu datang dari env, CLI, atau
  `config.json`. Sebuah knob yang tidak melakukan apa-apa tetapi terbaca sebagai
  konfigurasi lebih buruk daripada ketiadaannya. Ketiganya berhenti ditulis;
  `RETIRED_PROVIDER_KEYS` menahan namanya supaya `validate_provider_config` bisa
  membedakan key pensiun dari salah ketik — upgrade bersifat additive dan tidak pernah
  menghapus, jadi tiap workspace lama masih membawanya dan menyebutnya salah ketik berarti
  menyalahkan user atas default yang tool ini sendiri kirim.
