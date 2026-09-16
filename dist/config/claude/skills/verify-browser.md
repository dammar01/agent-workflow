# Skill: verify-browser
description: Verifikasi lewat browser Playwright sungguhan. Config project (bila ada) → draft spec (second_agent) → run + review. Wawancara hanya saat project belum dikonfigurasi.

## Trigger
/.verify-browser [<fitur/alur yang mau diuji>]
Beda dari /.verify: /.verify = review kode (delegated|syntax). /.verify-browser = klik dan assert di app yang jalan.
LOCAL command: tak ada pre-flight gate. Draft yang `ready` LANGSUNG dilanjut ke run — DILARANG bertanya "jalankan atau tidak". Yang tetap menghentikan: `blocked`/`invalid` dari draft, `missing_env`, dan usulan `read_only_requests` yang belum pernah diizinkan (itu keputusan izin, bukan gate jalan).

## Prasyarat
- Playwright: `python install.py --apply --with-e2e` di checkout agent-workflow. /.doctor → `e2e_readiness`.
- App target SUDAH jalan di URL yang user sebut. Runtime tak menyalakan server.
- Run script lama (belum kenal `verify-browser`) balas job tanpa hasil / `unsupported command` → jalankan /.upgrade dulu.

## STEP 0 — Baca config project (SEBELUM bertanya apa pun)
Section `e2e` di `<project>/.workflow/config.json` = default terpasang milik project. Isinya key yang sama dengan settings request.
- Section ada dan `base_url`-nya sesuai target → **LEWATI STEP 1 sepenuhnya**. Jangan wawancara. Tulis request minimal (STEP 2) dan lanjut.
- Section tidak ada / base_url beda dari yang user sebut → STEP 1, lalu tawarkan menuliskan hasilnya ke `config.json` supaya run berikutnya nol wawancara.
Nilai config yang salah tipe/tak dikenal TIDAK menggagalkan run: dibuang, dipakai default bawaan, dan namanya muncul di `meta.e2e.config_warnings`. Relay peringatan itu — knob yang diabaikan terbaca sama seperti knob yang rusak.
Credential TIDAK PERNAH masuk config.json — tetap di `.workflow/e2e/secrets.json`.

## STEP 1 — Wawancara (AskUserQuestion, main thread) — HANYA bila STEP 0 bilang perlu
Isi dari konteks dulu (diff, pesan user); tanya HANYA yang belum pasti. Max 4 pertanyaan per call, header ≤12 char.
1. Alur/fitur yang diuji + hasil yang dianggap benar (claims). Terbuka → opsi dari konteks bila ada.
2. base_url: `http://localhost:8000` | `http://127.0.0.1:8000` | virtual host (mis. `http://app.test`) → user isi lewat "Other".
   Tiga kelas, jangan dicampur:
   - loopback (localhost/127.0.0.1/::1/*.localhost) → lolos apa adanya.
   - nama `.test` (suffix cadangan RFC 6761 untuk development lokal) → lolos TANPA `allow_remote`. Ini pelonggaran yang sengaja: `.test` tak bisa ada di internet publik.
   - domain sungguhan (`staging.example.com`) → WAJIB `allow_remote: true` + `allowed_origins: ["<scheme://host[:port]> persis"]`. Itu opt-in yang harus diketik user, bukan default.
   Navigasi ke origin LAIN (termasuk `.test` lain) tetap butuh entri di `allowed_origins`, apa pun kelas base_url-nya.
3. Perlu login? tidak | ya, akun sudah ada.
   Nama key credential DITETAPKAN runtime: `E2E_USER`, `E2E_PASS` (registry `CREDENTIAL_KEYS`). Scenario hanya boleh `${E2E_USER}` / `${E2E_PASS}`, huruf persis; nama lain atau salah kapital → `spec_invalid`. JANGAN tanya/karang nama key lain.
   Ya → tanya NAMA profil akun (mis. `qa`, `admin`), BUKAN nilainya. Taruh di settings `secrets_template_profiles` (dipakai hanya bila file belum ada) dan pilih satu lewat `secrets_profile`.
   Nilai diisi user sendiri di `.workflow/e2e/secrets.json` (gitignored, di bawah `.workflow/`):
   `{"default": "qa", "profiles": [{"name": "qa", "credentials": {"E2E_USER": "...", "E2E_PASS": "..."}}, {"name": "admin", "credentials": {...}}]}`.
   Beberapa akun = beberapa profil; satu run memakai satu profil lewat settings `secrets_profile` (kosong = `default`). Coba akun lain = run lain dengan nama profil lain.
   File belum ada → draft (STEP 3) membuatnya (struktur + key dari kode, satu profil per nama di settings) dengan slot kosong; string kosong = belum diisi. File yang sudah ada tak pernah ditulis ulang, juga saat dua draft jalan bersamaan.
   Format lama (`"profiles": {"qa": {...}}`) DITOLAK (`secrets_invalid`), tak dikonversi → minta user tulis ulang ke format list di atas.
   User menempel password di chat → JANGAN dipakai/diulang/ditulis ke file; minta taruh di secrets.json.
4. Tampilan: **default headed** (`headless: false`) — tanya hanya bila user tampak ingin lain. headed + slow_mo_ms 700 (bisa ditonton) | headed tanpa jeda | headless (CI/tanpa display). Ini knob config, bukan pertanyaan wajib tiap run.
5. Side effect data (buat/ubah/hapus data test)? tidak (default) | boleh — hanya app LOKAL (base_url loopback), tiap perubahan wajib punya cleanup yang bisa dijalankan.
   `tidak` DITEGAKKAN runtime: player abort semua request selain GET/HEAD/OPTIONS (origin mana pun, termasuk API beda port) — login lewat form POST ikut keblok; flow yang butuh login POST berarti perlu `boleh` di app lokal. POST yang cuma BACA (search, filter, GraphQL query): second_agent mengusulkannya di draft (`read_only_requests`, wajib bukti handler file:line), user konfirmasi di STEP 4, lalu masuk `allowed_read_only_requests`. Body GraphQL `mutation`/`subscription`/persisted query tetap diblok runtime.
   `boleh` → settings `allow_side_effects: true`. Write dinilai dari ALAMAT, bukan nama: preflight me-resolve base_url dan tiap origin di `allowed_origins`, dan menolak run (`spec_invalid`) kalau salah satu punya alamat publik, link-local (`169.254.0.0/16` = metadata service cloud), atau tak bisa di-resolve. Semua alamat loopback/privat → host itu boleh menerima write, dan alamatnya DIPATOK ke browser (`--host-resolver-rules`) supaya nama itu tak bisa berpindah IP di tengah run. Patokan cuma didukung chromium → browser lain + write non-loopback DITOLAK. Tidak ada allow-list path lagi: `allowed_mutation_paths` DIHAPUS → `request_invalid` bila masih ditulis.
   Batas: GET yang mengubah data, WebSocket, service worker tidak terlihat guard maupun ledger request.
6. Existing test project (opsional): argv command tanpa shell (`{files}`, `{base_url}`) + allowlist glob. Tak ada → lewati.
Hybrid review SELALU jalan (codex menilai bukti browser) — bukan pertanyaan. Sebut: tiap run = 2 panggilan second_agent (draft + review).

## STEP 2 — Tulis request draft
File: `<project>/.workflow/sessions/<MAIN_SESSION_ID>/e2e/request.json` (Write tool, buat folder bila perlu).
```json
{"version": 1, "phase": "draft",
 "settings": {"base_url": "http://app.test", "allow_remote": true, "allowed_origins": ["http://app.test"],
              "headless": false, "slow_mo_ms": 700, "allow_side_effects": false}}
```
`settings` = SELISIH terhadap config project, bukan salinannya. Project sudah punya section `e2e` → tulis `{"version": 1, "phase": "draft"}` saja; apa pun yang ditulis di sini menang atas config, dan menyalin nilai yang sama cuma bikin dua tempat yang bisa berbeda diam-diam.
Key settings sah: base_url browser headless slow_mo_ms nav_timeout_ms step_timeout_ms idle_timeout_s total_timeout_s probe_max_elements allow_remote allowed_origins allow_side_effects allowed_read_only_requests secrets_profile secrets_template_profiles fail_on_console_error max_retries artifact_max_mb existing_test_command existing_test_allowlist existing_test_timeout_s. Key lain/tipe salah → `request_invalid` (`allowed_mutation_paths` → error migrasi). Key yang sama di config.json: salah tipe → diabaikan + warning, bukan error.
DILARANG menulis nilai credential ke request.

## STEP 3 — Draft (background)
Windows:   & "<work_dir>\.workflow\run.ps1" verify-browser "<task>" "<MAIN_SESSION_ID>"
mac/linux: "<work_dir>/.workflow/run.sh" verify-browser "<task>" "<MAIN_SESSION_ID>"
`run_in_background: true`, ambil hasil task ID yang sama. <task> ≤3000 char, isi: alur + claims yang diharapkan, base_url, nama key credential (`${E2E_USER}`...), boleh/tidak side effect, file yang berubah bila tahu. JANGAN tempel isi kode.
Hasil `meta.phase=draft`, `content` = [E2E DRAFT], detail di `meta.e2e.draft` + file `draft.json` di folder yang sama.
- ok:false (stage 1 gagal) → HARD GATE [PROXY GAGAL] seperti delegated. JANGAN karang scenario sendiri diam-diam.
- status `blocked` → preflight gagal (playwright_missing | browser_missing | base_url_unreachable | spec_invalid policy URL). Laporkan + fix, STOP.
- status `invalid` → tampilkan `errors`; tawarkan: perbaiki scenario bersama (main_agent edit JSON sesuai error) | ulang draft dengan task lebih jelas | batal.
- status `ready` → STEP 4.

## STEP 4 — Rencana (tampilkan, JANGAN minta izin jalan)
Cetak rencana supaya user tahu apa yang sedang berjalan, lalu LANGSUNG ke STEP 5. Tidak ada AskUserQuestion "Jalankan | Batal" — user yang memanggil /.verify-browser sudah menyatakan mau menjalankannya.
[BROWSER TEST PLAN]
target: <base_url> (loopback | remote allow-listed: <origin>)
tampilan: headless | headed slow_mo <n> ms
auth: <nama key> — secrets.json profil <nama>: set | BELUM (missing_env; `secrets_file` di draft menyebut path + apakah template baru dibuat)
claims: - <id> | <severity> | <description> | <source_refs>
steps: <step id. aksi → target (within <container>) → siap bila <ready> → request <METHOD path> → claim_id> (ringkas, bahasa manusia)
existing_tests: <path covers id> | tidak ada
side_effects: tidak ada (write diblokir runtime) | <step id + side_effect + test_data.marker + request yang diharapkan>
cleanup: <cleans step id: langkah → assertion hasil> | no_cleanup_reason <alasan> | tidak ada
read_only_requests: <usulan draft: METHOD endpoint | source_refs | reason> | tidak ada
risiko: <data berubah (hanya app lokal), akun nyata, URL remote, missing_env, spec_uncertainties, cleanup tanpa rencana>
biaya: 1 panggilan review second_agent
retry: <n> percobaan maksimum bila run berakhir incomplete karena lingkungan | mati (allow_side_effects true)
Dua hal — dan hanya dua — yang masih menghentikan langkah ini. Keduanya soal IZIN atau bahan yang belum ada, bukan soal "yakin mau jalan":
- missing_env tak kosong → minta user isi secrets.json (profil yang dipakai) dulu; jangan run sampai user bilang sudah. Tanpa credential, run pasti `env_missing`.
- read_only_requests tak kosong DAN belum tercakup `allowed_read_only_requests` dari config/request → AskUserQuestion multiSelect satu entri per opsi (description = source_refs + reason). HANYA yang dicentang ditulis ke `allowed_read_only_requests` saat STEP 5 sebagai `"POST <endpoint>"`. Tak dicentang = tetap diblok. DILARANG menambah entri yang tak diusulkan draft atau tak dikonfirmasi user. Sudah tercakup config → jangan tanya lagi.
Rencana salah → user bilang sendiri (Esc/instruksi baru); jangan pancing dengan pertanyaan.

## STEP 5 — Run (background, langsung setelah STEP 4)
Tulis ulang request.json: `"phase": "run"`, settings sama (atau yang diubah user), `"scenario"`, `"existing_tests"`, `"spec_notes"` disalin dari draft.json (scenario hasil edit bila ada). Panggil run script yang sama dengan <task> yang sama.
Headed (default) → browser muncul di layar user; beri tahu sebelum dispatch, sekali, tanpa menunggu jawaban.

## STEP 6 — Tag elemen (opsional, SATU konfirmasi batch)
Run mengusulkan `data-e2e` untuk elemen yang benar-benar terpakai. Usulan ada di `meta.e2e.tags` + file `tag-proposals.json`. Runtime TIDAK PERNAH menulis ke template — ini satu-satunya gate interaktif yang tersisa, karena ini satu-satunya langkah yang mengubah file milik user.
Batas usulan (runtime yang menegakkan, jangan ditawar): step `passed`, selector pemenang ber-provenance `source`, dan `selector_provenance.ref` menyebut `path:line` — file template di dalam project, bukan git-ignored, baris berisi PERSIS SATU opening tag dan nol markup dalam komentar (`<div><button>` ditolak, bukan ditebak sebagai div), belum punya `data-e2e`. Selector heuristic/runtime_probe nol alamat → nol usulan. DILARANG menambal dari DOM (`page.content()`): itu keluaran framework, bukan source project.
Alur:
1. Tampilkan entri `status: ready` sebagai diff (`old_line` → `new_line`) plus entri `skipped` beserta `reason` — yang skipped biasanya berarti `ref` draft sudah melenceng dari kode.
2. SATU AskUserQuestion untuk seluruh batch: Tulis semua | Pilih sebagian | Lewati.
3. Disetujui → edit tiap file memakai `old_line`/`new_line` PERSIS dari plan. Baris berubah sejak plan dibuat → lewati baris itu, jangan timpa. Path yang berubah jadi symlink keluar project juga ditolak — cek diulang saat tulis, bukan dipercaya dari plan.
4. Setelah tertulis → tawarkan /.promote dengan satu claim per tag (`e2e-tag-<tag>`, anchor `path:line` baru). `/.promote` yang memverifikasi anchor dan gate branch; jangan tulis ke knowledge dari sini.
Nol entri `ready` → lewati STEP 6 diam-diam, jangan tanya.

## Output (RELAY)
Hasil run = [VERIFICATION] canonical (meta.command=verify, meta.invocation=verify-browser). Relay apa adanya:
verdict (pass | fail | incomplete) | browser_verdict | reason | cleanup (`meta.e2e.cleanup.status`: passed | not_needed | not_planned | failed | not_run) | blocking_findings | not_verified | artifacts (`meta.e2e.artifacts`: report.json, events.jsonl, evidence.md, verification.md; saat gagal stepNN.html/png, trace.zip).
- `pass` HANYA dari runtime meta.verdict. Exit nonzero = bukan pass. `ok:true` saja bukan bukti lolos.
- Laporan per step (`report.json` → `trail`, dan `trail:` di evidence.md): step id → selector yang dipakai + jumlah match + fallback → readiness (kondisi, lama tunggu, yang tak tercapai) → request → hasil. Relay bagian yang relevan, jangan karang.
- Request (`report.json` → `requests`): tiap write (POST/PUT/PATCH/DELETE) dengan step, endpoint tersanitasi, status/kegagalan, durasi, `planned` (cocok `request` step) atau tambahan, `attribution: uncertain` bila terkirim di luar jendela step. HTTP 2xx saja BUKAN bukti CRUD berhasil — yang membuktikan assertion.
- Cleanup dilaporkan TERPISAH dari tes. `failed`/`not_run` → verdict `incomplete`, exit nonzero, walau semua claim terbukti; sebut data test mungkin tertinggal di app lokal. Jangan tampilkan sebagai sukses penuh.
- incomplete reason: request_missing | request_invalid | secrets_invalid | env_missing | spec_invalid | playwright_missing | browser_missing | base_url_unreachable | harness_error | unknown_origin | stuck | timeout | launch_failed | output_truncated. Sebut artinya + langkah perbaikan.
- Step gagal `not_ready` → indikator kesiapan app (loader, tombol aktif, konten AJAX) tak tercapai dalam step_timeout; origin unknown. Detail menyebut kondisi yang belum terpenuhi.
- Retry otomatis (`settings.max_retries`, default 2) hanya untuk `incomplete` berlatar lingkungan: harness_error, unknown_origin, stuck, timeout, output_truncated, launch_failed. `fail` origin=app TIDAK PERNAH diulang — mengulang bug nyata sampai ia sembunyi persis yang dicegah paket ini. Dua percobaan beruntun dengan hasil identik per step → berhenti, ditandai `stable`. Tiap percobaan ulang menulis artifact ke `retry<n>/`; `meta.e2e.attempts` merangkumnya. Sebut jumlah percobaan saat merelay.
- `allow_side_effects: true` → retry MATI total. Aksi write tak diulang saat timeout (request pertama mungkin sudah diproses server). Jangan sarankan "jalankan ulang otomatis"; periksa data dulu.
- Screenshot + trace tak diambil bila scenario pakai `${ENV}` (tak bisa di-scrub); HTML juga tak disimpan bila nilai credential < 4 karakter (`meta.e2e.secrets.unscrubbable`). Jangan minta buka screenshot bila bukti terstruktur cukup.
- fail origin=app → bug app, tawarkan /.analyze atau fix. origin harness/unknown → masalah scenario/lingkungan, bukan bukti bug.
- Failure `mutation_blocked` (harness) → write diblok guard. Bukan bug app. Detail `allow_side_effects is false` → tawarkan: POST yang cuma baca → ulang draft agar diusulkan di `read_only_requests` dengan bukti handler | aktifkan side effect (hanya app lokal) | ubah alur jadi read-only. Detail `writes go only to a loopback host` → write menuju host non-lokal; tak bisa diizinkan. Detail `allowed_read_only_requests covers the endpoint, but the body is ...` → body GraphQL write; bukan kandidat read-only. Observation `mutation_blocked` (beacon/async) = warning saja.

## Batas
- Config project hanya memindahkan DEFAULT; tak satu pun knob di `config.json` melonggarkan policy origin atau write. Domain sungguhan tetap butuh `allow_remote` + `allowed_origins` persis, dan write tetap dinilai dari alamat hasil resolve, di mana pun nilainya ditulis.
- Sisa risiko yang diketahui: patokan resolver menutup celah antara preflight dan run untuk chromium. Yang tak tertutup — host privat yang memang dikuasai pihak lain di jaringan yang sama, dan GET yang mengubah data (nol guard, seperti sebelumnya).
- Nilai credential tak pernah lewat chat, request, prompt, atau artifact. secrets.json bisa dibaca second_agent codex (read boundary NOT_ENFORCEABLE) — sebut ke user bila provider codex.
- Satu session = satu request. Session lain di project yang sama tak berbagi setting request — tapi berbagi section `e2e` di config.json.
- Metrik: tiap run = satu baris `kind: e2e_run` di `.workflow/quality.jsonl`; draft tidak dicatat.
