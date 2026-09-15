# Skill: verify-browser
description: Verifikasi lewat browser Playwright sungguhan. Wawancara → draft spec (second_agent) → konfirmasi user → run + review. Tanpa config.

## Trigger
/.verify-browser [<fitur/alur yang mau diuji>]
Beda dari /.verify: /.verify = review kode (delegated|syntax). /.verify-browser = klik dan assert di app yang jalan.
LOCAL command: tak ada pre-flight gate. Tapi DILARANG menjalankan browser sebelum user konfirmasi rencana (STEP 4).

## Prasyarat
- Playwright: `python install.py --apply --with-e2e` di checkout agent-workflow. /.doctor → `e2e_readiness`.
- App target SUDAH jalan di URL yang user sebut. Runtime tak menyalakan server.
- Run script lama (belum kenal `verify-browser`) balas job tanpa hasil / `unsupported command` → jalankan /.upgrade dulu.

## STEP 1 — Wawancara (AskUserQuestion, main thread)
Isi dari konteks dulu (diff, pesan user); tanya HANYA yang belum pasti. Max 4 pertanyaan per call, header ≤12 char.
1. Alur/fitur yang diuji + hasil yang dianggap benar (claims). Terbuka → opsi dari konteks bila ada.
2. base_url: `http://localhost:8000` | `http://127.0.0.1:8000` | virtual host (mis. `http://app.test`) → user isi lewat "Other".
   Host bukan loopback (bukan localhost/127.0.0.1/::1/*.localhost) → WAJIB settings `allow_remote: true` + `allowed_origins: ["<scheme://host[:port]> persis"]`. Sebut di rencana; user konfirmasi.
3. Perlu login? tidak | ya, akun sudah ada.
   Ya → tanya NAMA key (mis. `E2E_USER`, `E2E_PASS`) dan NAMA profil akun (mis. `qa`, `admin`), BUKAN nilainya. Scenario pakai `${E2E_USER}`.
   Nilai diisi user sendiri di `.workflow/e2e/secrets.json` (gitignored, di bawah `.workflow/`):
   `{"default": "qa", "profiles": {"qa": {"E2E_USER": "...", "E2E_PASS": "..."}, "admin": {...}}}`.
   Beberapa akun = beberapa profil; satu run memakai satu profil lewat settings `secrets_profile` (kosong = `default`). Coba akun lain = run lain dengan nama profil lain.
   File belum ada → draft (STEP 3) membuatnya dengan slot kosong per nama key; string kosong = belum diisi. File yang sudah ada tak pernah ditulis ulang.
   User menempel password di chat → JANGAN dipakai/diulang/ditulis ke file; minta taruh di secrets.json.
4. Tampilan: headless | headed + slow_mo_ms 700 (bisa ditonton) | headed tanpa jeda.
5. Side effect data (buat/ubah/hapus data test)? tidak (default) | boleh — hanya di environment test, tiap step wajib `cleanup`.
   `tidak` DITEGAKKAN runtime: player abort semua request selain GET/HEAD/OPTIONS (origin mana pun, termasuk API beda port). Login lewat form POST ikut keblok → tanya path login-nya, isi `allowed_mutation_paths` (`"/login"` relatif base_url, atau URL penuh `http(s)://host:port/path` untuk origin lain; path exact, tanpa wildcard/query). POST yang cuma BACA (search, filter, GraphQL query) bukan `allowed_mutation_paths`: second_agent mengusulkannya di draft (`read_only_requests`, wajib bukti handler file:line), user konfirmasi di STEP 4, lalu masuk `allowed_read_only_requests`. Body GraphQL `mutation`/`subscription`/persisted query tetap diblok runtime. Batas: GET yang mengubah data, WebSocket, service worker tidak terlihat guard.
6. Existing test project (opsional): argv command tanpa shell (`{files}`, `{base_url}`) + allowlist glob. Tak ada → lewati.
Hybrid review SELALU jalan (codex menilai bukti browser) — bukan pertanyaan. Sebut: tiap run = 2 panggilan second_agent (draft + review).

## STEP 2 — Tulis request draft
File: `<project>/.workflow/sessions/<MAIN_SESSION_ID>/e2e/request.json` (Write tool, buat folder bila perlu).
```json
{"version": 1, "phase": "draft",
 "settings": {"base_url": "http://app.test", "allow_remote": true, "allowed_origins": ["http://app.test"],
              "headless": false, "slow_mo_ms": 700, "allow_side_effects": false}}
```
Key settings sah: base_url browser headless slow_mo_ms nav_timeout_ms step_timeout_ms idle_timeout_s total_timeout_s probe_max_elements allow_remote allowed_origins allow_side_effects allowed_mutation_paths allowed_read_only_requests secrets_profile fail_on_console_error artifact_max_mb existing_test_command existing_test_allowlist existing_test_timeout_s. Key lain/tipe salah → `request_invalid`.
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

## STEP 4 — Rencana + konfirmasi (WAJIB sebelum browser)
[BROWSER TEST PLAN]
target: <base_url> (loopback | remote allow-listed: <origin>)
tampilan: headless | headed slow_mo <n> ms
auth: <nama key> — secrets.json profil <nama>: set | BELUM (missing_env; `secrets_file` di draft menyebut path + apakah template baru dibuat)
claims: - <id> | <severity> | <description> | <source_refs>
steps: <nomor. aksi → target → claim_id> (ringkas, bahasa manusia)
existing_tests: <path covers id> | tidak ada
side_effects: tidak ada (write diblokir runtime; allowed_mutation_paths: <list> | kosong) | <step + cleanup>
read_only_requests: <usulan draft: METHOD endpoint | source_refs | reason> | tidak ada
risiko: <data berubah, akun nyata, URL remote, missing_env, spec_uncertainties>
biaya: 1 panggilan review second_agent
Lalu AskUserQuestion: Jalankan | Ubah rencana | Batal.
- missing_env tak kosong → minta user isi secrets.json (profil yang dipakai) dulu; jangan run sampai user bilang sudah.
- read_only_requests tak kosong → AskUserQuestion multiSelect satu entri per opsi (description = source_refs + reason). HANYA yang dicentang ditulis ke `allowed_read_only_requests` saat STEP 5 sebagai `"POST <endpoint>"`. Tak dicentang = tetap diblok. DILARANG menambah entri yang tak diusulkan draft atau tak dikonfirmasi user.
- Ubah → edit scenario (tetap tervalidasi di run) → tampilkan ulang → konfirmasi lagi.

## STEP 5 — Run (background)
Tulis ulang request.json: `"phase": "run"`, settings sama (atau yang diubah user), `"scenario"`, `"existing_tests"`, `"spec_notes"` disalin dari draft.json (scenario hasil edit bila ada). Panggil run script yang sama dengan <task> yang sama.
Headed → browser muncul di layar user; beri tahu sebelum dispatch.

## Output (RELAY)
Hasil run = [VERIFICATION] canonical (meta.command=verify, meta.invocation=verify-browser). Relay apa adanya:
verdict (pass | fail | incomplete) | browser_verdict | reason | blocking_findings | not_verified | artifacts (`meta.e2e.artifacts`: report.json, events.jsonl, evidence.md, verification.md; saat gagal stepNN.html/png, trace.zip).
- `pass` HANYA dari runtime meta.verdict. Exit nonzero = bukan pass. `ok:true` saja bukan bukti lolos.
- incomplete reason: request_missing | request_invalid | secrets_invalid | env_missing | spec_invalid | playwright_missing | browser_missing | base_url_unreachable | harness_error | unknown_origin | stuck | timeout | launch_failed | output_truncated. Sebut artinya + langkah perbaikan.
- Screenshot + trace tak diambil bila scenario pakai `${ENV}` (tak bisa di-scrub). Jangan minta buka screenshot bila bukti terstruktur cukup.
- fail origin=app → bug app, tawarkan /.analyze atau fix. origin harness/unknown → masalah scenario/lingkungan, bukan bukti bug.
- Failure `mutation_blocked` (harness) → scenario mengirim write saat allow_side_effects=false. Bukan bug app. Tawarkan: tambah path ke `allowed_mutation_paths` (mis. login) | POST yang cuma baca → ulang draft agar diusulkan di `read_only_requests` dengan bukti handler | aktifkan side effect di env test | ubah alur jadi read-only. Detail menyebut `allowed_read_only_requests covers the endpoint, but the body is ...` → body GraphQL write; bukan kandidat read-only. Observation `mutation_blocked` (beacon/async) = warning saja.

## Batas
- Nilai credential tak pernah lewat chat, request, prompt, atau artifact. secrets.json bisa dibaca second_agent codex (read boundary NOT_ENFORCEABLE) — sebut ke user bila provider codex.
- Satu session = satu request. Session lain di project yang sama tak berbagi setting.
- Metrik: tiap run = satu baris `kind: e2e_run` di `.workflow/quality.jsonl`; draft tidak dicatat.
