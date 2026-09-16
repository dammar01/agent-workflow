# Rencana Revisi: `verify_mode=e2e` Berbasis Playwright

Status: **Fase 1–4 diimplementasikan (1 dan 4 terverifikasi `/.verify`; 2 dan 3 lolos suite lokal + smoke Chromium, belum `/.verify`); tooling Fase 5 ada; evaluasi Fase 0/5 menunggu dataset project nyata** — detail di §1.2 dan §1.3  
Tanggal: 2026-09-14 (status terakhir diperbarui 2026-09-14, setelah batch implementasi 1–8)  
Project: `dammar01/agent-workflow`

> **Catatan historis (2026-09-15).** Dokumen ini merekam rancangan awal berbasis `verify_mode=e2e` dan blok `commands.e2e`. Implementasi akhirnya berbeda: pembuktian browser adalah command eksplisit `/.verify-browser` yang **tidak membaca `config.json`** — semua setting per-run ada di request session, dan `e2e` bukan nilai `verify_mode`. Kontrak yang berlaku ada di `docs/runtime-contracts.md` §/.verify-browser, termasuk write guard `allow_side_effects` + `allowed_mutation_paths` yang tidak ada di rencana ini. Sebutan `verify_mode=e2e`, `commands.e2e.*`, dan `meta.verify_mode = "e2e"` di bawah dibiarkan sebagai jejak keputusan, bukan instruksi.

## 1. Ringkasan keputusan

Tambahkan `verify_mode=e2e` sebagai lapisan pembuktian perilaku runtime untuk aplikasi web. Mode ini tidak menggunakan model sebagai operator browser berbasis screenshot. Model menganalisis codebase dan menyusun kontrak pengujian, sedangkan Playwright menjalankan interaksi serta assertion secara lokal dan deterministik.

Prinsip utama:

> **Code-first, browser-second, visual-last.**

Urutan kerjanya:

1. Tentukan scope perubahan dari Git dan task pengguna.
2. Eksplorasi bagian codebase yang terdampak.
3. Turunkan perilaku yang harus dibuktikan menjadi `verification claims`.
4. Gunakan existing E2E test milik project jika tersedia dan relevan.
5. Jika terdapat coverage gap, hasilkan scenario JSON deklaratif.
6. Jalankan scenario melalui Playwright player lokal.
7. Evaluasi DOM, URL, browser error, dan network evidence.
8. Kirim report ringkas ke hybrid reviewer.
9. Buka screenshot atau trace hanya jika bukti terstruktur tidak cukup.

Implementasi dilakukan sebagai MVP terlebih dahulu. Self-healing kompleks, boot aplikasi, visual reasoning otomatis, dan dukungan lintas-browser ditunda sampai MVP terbukti memberikan nilai.

### 1.1 Keputusan arsitektur (2026-09-14)

Tiga keputusan berikut diambil setelah rencana ini dianalisis terhadap codebase verify saat ini. Keputusan ini mengikat seluruh bagian di bawah.

| # | Keputusan | Alasan |
|---|---|---|
| D1 | **Orkestrasi three-stage di runtime Python.** Stage 1: delegated call ke second_agent menghasilkan claims + scenario JSON (`[E2E SPEC]`). Stage 2: player Playwright lokal. Stage 3: hybrid review delegated membaca `[E2E EVIDENCE]` compact, lalu normalizer menggabungkan report browser dan hasil review menjadi `[VERIFICATION]` canonical. Stage 1 dan 3 **tidak** memanggil ulang `Executor.execute("verify")` dengan config apa adanya — lihat aturan dispatch §5.1. | Branch lokal `Executor.execute()` untuk mode `syntax` early-return sebelum router/provider — tidak ada tempat bagi model membentuk claims. Menempatkan claim builder di runtime membuat seluruh lifecycle dapat diuji dengan fake player, bukan bergantung disiplin prompt main agent. |
| D2 | **Hasil E2E dinormalisasi ke kontrak `[VERIFICATION]` yang sudah ada.** Tidak ada jalur bypass verdict baru seperti mode `quick`. | `_finalize_verify_result`, predicate continuation, dan invalid-evidence guard semuanya memvalidasi output verify sebagai delegated contract; satu-satunya pengecualian adalah metadata quick (`meta.mode == "quick"` atau `meta.quick_verify`). Raw JSONL/report E2E tanpa normalisasi akan dinilai `INCOMPLETE` atau memicu continuation. Satu parser verdict berarti nol drift dan exit code otomatis benar. |
| D3 | **Playwright sebagai optional extra dengan lazy import.** Runtime tetap stdlib-only; `core/evidence/e2e/` mengimpor Playwright saat dipakai; absen → `incomplete` dengan reason `playwright_missing`. Test invariant dependency hanya mengizinkan import ber-guard di paket e2e. Pemasangan: `requirements-e2e.txt` terpin + `python install.py --apply --with-e2e` (pip, lalu `playwright install chromium`). | `tests/checks/deps.py` menegakkan runtime stdlib-only, dan repo tidak punya `requirements.txt` sama sekali. Menambahkan Playwright ke dependency wajib memberatkan semua instalasi yang tidak memakai E2E; flag opt-in memisahkannya tanpa mengubah instalasi default. |

Perkiraan risiko rencana: sebelum keputusan ini `high` (tabrakan dengan contract delegated + tidak ada rumah untuk claim builder); setelah ketiganya masuk, `medium` (sisa risiko: player supervisor baru, dependency browser, kalibrasi timeout).

Keputusan tambahan saat implementasi (Q1): stage 1 dan stage 3 memanggil `Executor._run_delegated()` / `_build_delegated_prompt()` yang diekstrak dari `execute()`, bukan `execute()` ulang. Lihat §5.1.

Keputusan tambahan batch implementasi: packaging lewat `requirements-e2e.txt` + `--with-e2e` yang juga mengunduh Chromium; existing test project hanya dijalankan lewat `commands.e2e.existing_test_command` yang ditulis user (§8, §18); dataset evaluasi Fase 0/5 diambil dari project nyata.

### 1.2 Status implementasi

Posisi per 2026-09-14. **Terverifikasi** = lolos `/.verify` delegated dan suite lokal. **Diimplementasikan** = kode dan test ada, `python tests/run.py` PASS, `python -m tools.sim.sim_flows` 34/34, smoke Chromium nyata PASS di Windows (`WORKFLOW_E2E_SMOKE=1`), tetapi belum melewati `/.verify`. Seluruh perubahan belum di-commit.

| Fase | Status | Bukti | Catatan |
|---|---|---|---|
| 0 — Baseline dan validasi kebutuhan | **belum (menunggu data)** | tooling: label dataset `WORKFLOW_E2E_LABEL`, baseline delegated verify di `report` | Butuh 3–5 perubahan web nyata, `base_url` non-production, akun test via `${ENV}`. Tidak bisa dikerjakan tanpa project target. |
| 1 — Kontrak tanpa browser | **selesai, terverifikasi** | `e2e_spec.py`, `e2e_normalize.py`, `e2e_supervisor.py`, `e2e_routing.py`; sim S22–S22c; re-verify batch 2 DONE | — |
| 2 — Playwright MVP | **diimplementasikan** | batch 1–5: policy `spec.py`, `browser.py`, kebijakan artifact, `e2e_browser.py`, `e2e_redact.py`, `e2e_doctor.py`, `installer-e2e`, `e2e_smoke.py` (10 kasus Chromium nyata) | Exit criteria (pass, app failure, harness failure terklasifikasi benar) terpenuhi di smoke. Linux, Firefox, dan WebKit belum diuji. |
| 3 — Stage 1: claim builder | **diimplementasikan** | batch 6: grounding `source_refs`, replay `last_spec.json`, existing test via config, uji source tidak diteruskan; `e2e_existing_tests.py`, sim S22d | Existing test baru diuji dengan runner Python pengganti, belum `npx playwright test` nyata. |
| 4 — Stage 3: hybrid review | **selesai, terverifikasi** | `[E2E EVIDENCE]`; normalizer fail-closed; exhaustive agreement normalizer dan validator | Evidence block kini juga memuat probe, existing test, dan daftar artifact (batch 4 dan 6). |
| 5 — Evaluasi MVP | **tooling diimplementasikan; evaluasi belum** | batch 7: baris `kind: e2e_run` di `quality.jsonl`, `telemetry.e2e_metrics` di `main.py --command report`; `e2e_metrics.py` | Angka evaluasi bergantung dataset Fase 0. Baseline loop browser visual tidak diukur dan tidak diestimasi. |

### 1.3 Celah

Celah G1–G14 dari revisi sebelumnya dan batch yang menutupnya:

| # | Celah | Status | Ditutup oleh |
|---|---|---|---|
| G1 | Player browser nyata, observer, `page_stable` | tertutup | batch 3 (`browser.py`), batch 5 (smoke) |
| G2 | DOM probe bounded | sebagian | batch 3 dan 4: action `probe`, probe otomatis saat selector miss, ringkasan di evidence. Cache probe (§11) belum ada. |
| G3 | Kebijakan screenshot/HTML/trace | tertutup | batch 4 |
| G4 | Existing test dijalankan | tertutup (opt-in) | batch 6, lewat `existing_test_command` |
| G5 | Validasi origin navigasi | tertutup | batch 1 (validator), batch 3 (route guard runtime) |
| G6 | Metadata action destruktif | tertutup | batch 1 (`side_effect` + `allow_side_effects`) |
| G7 | Selector candidates | tertutup | batch 1 (schema), batch 3 (eksekusi) |
| G8 | Grounding `source_refs` | tertutup | batch 1 (format), batch 6 (file dan baris ada) |
| G9 | Config belum dikonsumsi | tertutup | batch 3 dan 4 |
| G10 | Replay spec stage 1 | **terbuka** — tidak ada di kode (`runner.py` mencatat `replay_used: false`, tanpa `last_spec.json`/`commands.e2e.replay`). Pengganti parsial sejak 3.6.1: knowledge browser (`core/evidence/e2e/knowledge.py`) menawarkan alur yang terbukti ke draft berikutnya, bukan replay spec | batch 6 (klaim awal, dikoreksi) |
| G11 | Retensi artifact berbasis ukuran | tertutup | batch 4 (`artifact_max_mb`) |
| G12 | Metrik evaluasi e2e | tooling tertutup, data belum | batch 7 |
| G13 | Token dibanding loop browser visual | sebagian | batch 7: token per run dan baseline delegated verify; baseline loop visual belum diukur |
| G14 | Test §22 yang belum ada | tertutup | batch 4 (retensi), batch 5 (smoke), batch 6 (existing test reuse, source tidak diteruskan) |

Masih terbuka:

- Dataset Fase 0 dan evaluasi Fase 5 pada project nyata (butuh input user).
- Baseline loop browser visual untuk acceptance #13.
- Cache probe (§11); bounded outer HTML target pada step sukses (§12).
- Smoke di Linux; Firefox dan WebKit; job CI yang memasang extra dan menyetel `WORKFLOW_E2E_SMOKE=1`.
- Existing test command dengan runner nyata (mis. `npx playwright test`).
- Batch 1–7 belum melewati `/.verify`; seluruh perubahan belum di-commit; skill terpasang di `~/.claude/` belum di-upgrade dari `dist/` (`python install.py --apply`).
- Self-healing (§15.2) ditunda sesuai rencana, bukan utang.

## 2. Masalah yang hendak diselesaikan

Mode verifikasi yang membaca kode dapat menilai apakah implementasi terlihat benar, tetapi belum membuktikan bahwa perilaku tersebut benar-benar terjadi ketika aplikasi dijalankan.

Contoh masalah yang dapat lolos dari syntax check dan code review:

- route atau redirect salah;
- tombol tidak dapat diklik karena state atau overlay;
- JavaScript runtime exception;
- request API menghasilkan HTTP 5xx;
- state UI tidak diperbarui setelah respons berhasil;
- form tidak mengirim nilai yang diharapkan;
- integrasi frontend dan backend tidak tersambung;
- build berjalan, tetapi workflow pengguna gagal.

Tujuan mode E2E bukan menggantikan syntax test, unit test, atau delegated review. Mode ini menambahkan bukti runtime ketika perubahan memang menyentuh perilaku aplikasi web.

## 3. Sasaran dan non-sasaran

### 3.1 Sasaran MVP

- Membuktikan satu atau beberapa perilaku penting melalui browser.
- Menghasilkan hasil `pass`, `fail`, atau `incomplete` yang dapat dipertanggungjawabkan.
- Menjaga penggunaan token rendah dengan memindahkan interaksi browser ke proses lokal.
- Menghasilkan evidence yang terstruktur, bounded, dan dapat dijalankan ulang.
- Membedakan kegagalan aplikasi, kegagalan test harness, dan kondisi ambigu.
- Menggunakan ulang test milik project target jika sudah tersedia.
- Menjaga secret dan data sensitif agar tidak masuk prompt maupun report.

### 3.2 Non-sasaran MVP

- Menjadi browser agent visual serbaguna.
- Menjalankan eksplorasi UI lewat screenshot berulang.
- Menulis arbitrary Playwright/Python/JavaScript dari model.
- Menyalakan atau mematikan aplikasi target.
- Menjalankan test terhadap production secara default.
- Mendukung semua action Playwright.
- Memperbaiki aplikasi secara otomatis berdasarkan hasil E2E.
- Melakukan self-healing bebas terhadap expected behavior.
- Menggantikan seluruh test suite milik project target.

## 4. Kapan mode E2E digunakan

Mode E2E hanya relevan jika semua kondisi berikut terpenuhi:

1. Target memiliki aplikasi web yang dapat diakses dari environment verifikasi.
2. Perubahan memengaruhi route, form, interaksi, state UI, atau integrasi frontend–backend.
3. Terdapat perilaku pengguna yang dapat dinyatakan sebagai assertion.
4. Environment dan test data cukup stabil untuk pengujian ulang.

Mode E2E tidak wajib untuk perubahan seperti:

- dokumentasi saja;
- refactor internal tanpa perubahan perilaku;
- library non-web;
- perubahan yang sudah dibuktikan secara cukup oleh unit/integration test;
- aplikasi yang tidak memiliki environment aman untuk browser test.

Resolver awal yang disarankan:

| Kondisi | Mode utama |
|---|---|
| Perubahan parse/config sederhana | `syntax` |
| Perubahan logika tanpa surface web | `delegated` + existing tests |
| Perubahan workflow aplikasi web | `e2e` |
| Project memiliki E2E test relevan | jalankan existing test terlebih dahulu |
| Browser/environment tidak tersedia | `incomplete`, bukan fallback `pass` |

Catatan runtime: reader `verify_mode()` saat ini adalah allowlist murni — nilai yang tidak dikenal hanya diberi warning lalu **fallback diam ke `delegated`** (`core/runtime/config_defaults.py`, fungsi `verify_mode` dan validasi `commands.verify_mode`). Jika `e2e` hanya ditambahkan ke allowlist tanpa preflight, konfigurasi `e2e` pada environment tanpa Playwright akan diam-diam berjalan sebagai delegated review, bukan `incomplete`. Karena itu:

- reader tetap allowlist murni (`delegated | syntax | e2e`), tidak melakukan pengecekan dependency;
- preflight dependency/browser/base URL dilakukan di branch `e2e` executor, sebelum stage apa pun berjalan;
- preflight gagal → hasil `incomplete` dengan reason eksplisit (`playwright_missing`, `browser_missing`, `base_url_unreachable`), tidak pernah turun ke `delegated` tanpa instruksi pengguna.

## 5. Arsitektur konseptual

Sesuai D1, mode `e2e` adalah runner three-stage di dalam `Executor.execute()`. Branch `e2e` masuk di sebelah branch `syntax` (early local branch), tetapi berbeda dengan `syntax`, branch ini memanggil provider dua kali (stage 1 dan stage 3) melalui jalur delegated yang sudah ada.

```text
Task + Git scope
      │
      ▼
┌──────────────────────────────────────────────────────────────────┐
│ STAGE 1 — delegated call (second_agent, read-only)               │
│   scope resolver ─▶ code explorer ─▶ claim builder ─▶ coverage   │
│   output: [E2E SPEC] = claims + scenario JSON + existing tests   │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
                     spec validator (lokal, deterministik)
                               │
             ┌─────────────────┴─────────────────┐
             ▼                                   ▼
      Existing E2E test                    Scenario JSON
             │                                   │
             └─────────────────┬─────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ STAGE 2 — player Playwright (child process lokal)                │
│   stdout JSONL: progress, heartbeat, result                      │
│   evidence classifier: app / harness / unknown                   │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ STAGE 3 — normalizer + hybrid review                             │
│   normalizer: hasil player ─▶ [E2E EVIDENCE] compact             │
│   delegated call: reviewer ─▶ [VERIFICATION] canonical           │
│   combine fail-closed ─▶ verdict efektif ─▶ validator existing   │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
              validate_verification_contract (tidak berubah)
                               │
                               ▼
                  _finalize_verify_result / exit code
```

Visual fallback (screenshot/trace) tetap ada di stage 3 dan hanya dibuka bila bukti terstruktur tidak cukup (lihat §16).

Komponen utama:

| Komponen | Stage | Tanggung jawab | Status di codebase |
|---|---|---|---|
| Scope resolver | 1 | Menentukan perubahan yang sedang diverifikasi | reuse — prompt verify sudah meminta changed files + consumers (`core/prompt/prompt_builder.py`, section verify) |
| Code explorer | 1 | Menemukan route, komponen, selector, API, dan expected behavior | reuse — kemampuan explore second_agent |
| Claim builder | 1 | Mengubah hasil eksplorasi menjadi perilaku yang dapat dibuktikan | baru — prompt + kontrak output `[E2E SPEC]` |
| Coverage resolver | 1 | Menentukan apakah existing test sudah cukup | baru |
| Spec validator | lokal | Memvalidasi scenario dan membatasi action yang diizinkan | baru — `core/evidence/e2e/spec.py` |
| Player supervisor | 2 | Menjalankan child process, JSONL parse, heartbeat, idle/total timeout, tree kill | baru; reuse `utils.osutil.terminate_tree()` dan pola bounded capture dari adapter provider |
| Playwright player | 2 | Menjalankan browser dan menghasilkan event terstruktur | baru — `core/evidence/e2e/player.py`, lazy import Playwright |
| Evidence classifier | 2 | Mengklasifikasikan `app`, `harness`, atau `unknown` | baru |
| Normalizer | 3 | Memetakan hasil player ke `[E2E EVIDENCE]` + finding canonical | baru — `core/evidence/e2e/normalize.py` |
| Hybrid reviewer | 3 | Mencocokkan evidence runtime dengan kode dan claim, output `[VERIFICATION]` | reuse — jalur delegated verify existing + section prompt baru |
| Contract validator | akhir | Menentukan verdict runtime + exit code | reuse tanpa perubahan — `core/evidence/contract.py`, `core/evidence/result_shaping.py` |

### 5.1 Aturan dispatch stage 1 dan stage 3 (anti-rekursi)

`Executor.execute()` hanya memiliki satu entry point publik, dan mode verify dibaca dari config **sebelum** routing (`mode = verify_mode(project_root) if normalized_command == "verify"`). Bila stage 1 atau stage 3 memanggil `Executor.execute("verify", ...)` secara naif ketika config masih `verify_mode=e2e`, panggilan itu masuk lagi ke branch E2E — rekursi, dan provider tidak pernah tercapai. Selain itu, invalid-evidence guard menolak output command `verify` yang tidak memuat `[VERIFICATION]`, sehingga stage 1 (output `[E2E SPEC]`) tidak boleh berjalan sebagai command `verify` sama sekali.

Keputusan implementasi (Q1 plan Fase 1, 2026-09-14): inti provider-call `execute()` — handoff, bind adapter, `_adapter_run`, satu bounded continuation, archive call meta, snapshot, persist session, audit, `_record_failed_call`, guard `[VERIFICATION]` dan guard evidence — diekstrak menjadi `Executor._run_delegated(route, command, task, session, session_id, project_root, work_dir, on_progress, session_manager, *, prompt, prompt_meta, lock_claim)`; sizing prompt menjadi `Executor._build_delegated_prompt(route, command, task, session_id, project_root, *, ..., e2e_evidence=None)`. Jalur publik `execute()` memakai keduanya lalu tetap melakukan bookkeeping role (facts, index, fan-out, cache). Stage E2E memanggil kedua helper itu **langsung** — nol re-entry `execute()`, nol lock ulang, nol reset `_call_metas`, nol side-effect role. Orkestrasi stage ada di modul `core/evidence/e2e/runner.py`, dipanggil dari branch `e2e` di `execute()` setelah lock dimiliki.

Aturan:

| Stage | Command | Route | Kontrak output | Cara dispatch |
|---|---|---|---|---|
| 1 | `e2e_spec` (internal, baru) | entri baru di `COMMAND_ROUTES` (`config/routing.py`), role `ROLE_EXPLORATION` — read-only, tanpa validator `[VERIFICATION]`; terdaftar di `INTERNAL_COMMANDS` | format evidence exploration standar (`[EVIDENCE]` … `[DIGEST]`) dengan section tambahan `[E2E SPEC]` di dalamnya | `executor._run_delegated(route_e2e_spec, "e2e_spec", ...)` dari `runner.py`; bukan command `verify`, jadi tidak menyentuh `verify_mode` |

Catatan guard stage 1: executor memiliki generic evidence guard untuk semua role `exploration|reasoning` yang menolak output tanpa marker evidence (`_EVIDENCE_MARKERS` di `core/provider/continuation.py`: `[evidence]`, `[digest]`, `entry_points`, `grounded:`, dll.) sebagai `invalid_evidence`, terpisah dari guard `[VERIFICATION]`. Karena itu output stage 1 **wajib** berbentuk evidence exploration standar — `[EVIDENCE]` (grounded/assumptions/scope) + `[DIGEST]` — dan `[E2E SPEC]` hanyalah section tambahan di dalamnya, bukan format pengganti. Konsekuensinya: guard, continuation exploration, dan digest-first existing berlaku apa adanya untuk stage 1; `_EVIDENCE_MARKERS` tidak perlu diubah. Parser `[E2E SPEC]` bekerja di atas `content` yang sudah lolos guard; section hilang atau JSON rusak → `incomplete` reason `spec_invalid` (satu continuation terstruktur bila `[EVIDENCE]` ada tetapi `[E2E SPEC]` tidak).
| 2 | — | lokal | JSONL player | supervisor, tanpa provider |
| 3 | `verify` | route `verify` existing (sudah di-resolve `execute()`), role `ROLE_VERIFICATION` | `[VERIFICATION]` canonical | `executor._run_delegated(route_verify, "verify", ..., prompt=<prompt verify + [E2E EVIDENCE]>)` dari `runner.py`; tidak lewat `execute()`, jadi `verify_mode` tidak dibaca ulang — tidak ada override, tidak ada rekursi |

Batas internal:

- `e2e_spec` tidak tersedia sebagai command publik: tidak ada di `choices` argparse `main.py`, dan `main.run()` menolaknya lewat `config.routing.INTERNAL_COMMANDS` (`routing_error`). `COMMAND_ROUTES` memuatnya karena router hanya menentukan role. `provider_select.SELECTABLE_ROUTES` tidak memuatnya.
- Stage tidak pernah memanggil `execute()`; tidak ada parameter override dan tidak ada recursion guard karena tidak ada jalur untuk rekursi.
- Test Fase 1 (`tests/checks/e2e_routing.py`): fake adapter mencatat setiap panggilan provider; stage 1 tercatat sebagai `e2e_spec`, stage 3 sebagai `verify`, tidak ada panggilan ketiga; stage 3 dilewati saat browser `incomplete`.

Metadata hasil akhir: `meta.verify_mode = "e2e"`, `meta.verdict` (efektif, diset sebelum `_finalize_verify_result`), `meta.e2e = {browser_verdict, reason, stages: [{stage, command, prompt_id|returncode, ...}], artifacts, preflight, spec_source, fake}` agar `await`/`result` dapat menampilkan jejak ketiga stage.

Kontrak output stage 1 (usulan; bentuk final ditetapkan pada Fase 1 mengikuti pola parser section `[VERIFICATION]`). Bagian `[EVIDENCE]`/`[DIGEST]` mengikuti format exploration yang sudah ada; `[E2E SPEC]` adalah section tambahan:

```text
[EVIDENCE]
confidence: high
entry_points:
- src/pages/Login.tsx:12-80
grounded:
- Form login submit ke POST /api/login, sukses redirect /dashboard [src/routes/auth.ts:40-58]
assumptions:
- none
scope_covered:
- src/pages, src/routes, e2e/
scope_not_covered:
- backend session store
uncertainties:
- selector heuristic pada ...

[E2E SPEC]
claims:
- id: login-valid-user | severity: blocking | source_refs: src/pages/Login.tsx, src/routes/auth.ts
existing_tests:
- path: e2e/login.spec.ts | covers: login-valid-user | confidence: high
scenario_json: <blok JSON sesuai §9>
coverage_gap:
- claim_id yang belum dicakup existing test

[DIGEST]
summary: 1 claim blocking, existing test mencakup login valid; gap: redirect setelah error.
key_findings:
- ...
risk_level: medium
confidence: high
```

## 6. Scope eksplorasi codebase

Eksplorasi tidak boleh memindai seluruh repository tanpa batas. Urutan scope:

1. Task atau nama fitur yang diberikan pengguna.
2. Working-tree diff jika ada perubahan belum di-commit.
3. Commit range terhadap merge-base untuk feature branch.
4. Changed symbols dan dependency langsungnya.
5. Route, component, template, handler, API, dan test terkait.
6. APP_MAP, feature registry, atau dokumentasi internal jika tersedia.

Data yang dicari:

- entry URL;
- prerequisite dan authentication;
- user-visible role, label, heading, atau `data-testid`;
- action utama;
- request API yang relevan;
- expected redirect atau state akhir;
- error state;
- existing test dan fixture;
- environment variable yang dibutuhkan, hanya namanya;
- risiko tindakan destruktif.

Output eksplorasi harus berupa digest ringkas, bukan salinan file penuh:

```text
Feature: login
Scope: perubahan form login dan redirect
Entry route: /login
Primary component: src/pages/Login.tsx
Success route: /dashboard
Stable selector: role=button, name=Masuk
Existing coverage: unit validation only
Runtime gap: redirect dan session cookie belum dibuktikan
```

## 7. Verification claims

Sebelum membuat langkah browser, sistem harus menentukan perilaku yang hendak dibuktikan.

Contoh:

```json
{
  "claims": [
    {
      "id": "login-valid-user",
      "description": "Pengguna dengan kredensial valid masuk ke dashboard",
      "severity": "blocking",
      "source_refs": [
        "src/pages/Login.tsx",
        "src/routes/auth.ts"
      ]
    }
  ]
}
```

Aturan claim:

- Setiap scenario minimal memiliki satu claim.
- Setiap assertion harus menunjuk satu claim.
- Claim harus berasal dari task, requirement, code contract, atau existing test.
- Step interaksi tanpa assertion tidak cukup untuk menghasilkan `pass`.
- Jumlah step bukan ukuran coverage; claim kritis yang terbukti adalah ukuran utamanya.

## 8. Strategi coverage

Urutan pemilihan test:

1. Cari existing Playwright/Cypress/Webdriver test yang sesuai.
2. Periksa apakah test tersebut mencakup claim yang sedang diverifikasi.
3. Jalankan test relevan saja, bukan seluruh suite tanpa alasan.
4. Jika coverage sebagian, jalankan existing test lalu buat scenario hanya untuk gap.
5. Jika tidak ada coverage, buat scenario baru melalui format deklaratif.

Existing test tidak otomatis dipercaya. Exit code, report, dan relevansinya terhadap claim tetap harus diperiksa.

Implementasi (batch 6): runtime tidak pernah menebak cara menjalankan test project. Test hanya dijalankan lewat `commands.e2e.existing_test_command` yang ditulis user (argv tanpa shell; `{files}` dan `{base_url}` diekspansi), untuk file berpath polos yang ada dan cocok `existing_test_allowlist`, dengan timeout dan kill process tree. Test yang lulus membuktikan claim yang di-cover-nya (claim itu tidak wajib di-assert di scenario); test yang gagal membuat claim `unknown`. Tanpa command, test yang di-list hanya dicatat dan claim-nya wajib di-assert.

## 9. Scenario deklaratif

Model menghasilkan JSON scenario. Runtime tetap berisi implementasi Playwright yang sudah ditentukan dan diuji.

```json
{
  "version": 1,
  "feature": "login",
  "heal_iteration": 0,
  "claims": [
    {
      "id": "login-valid-user",
      "description": "Login valid membuka dashboard",
      "severity": "blocking"
    }
  ],
  "steps": [
    {
      "action": "goto",
      "url": "/login"
    },
    {
      "action": "fill",
      "selector": {
        "role": "textbox",
        "name": "Email"
      },
      "selector_provenance": {
        "type": "source",
        "ref": "src/pages/Login.tsx",
        "confidence": "high"
      },
      "value": "${E2E_USER}"
    },
    {
      "action": "fill",
      "selector": {
        "testid": "password"
      },
      "value": "${E2E_PASS}"
    },
    {
      "action": "click",
      "selector": {
        "role": "button",
        "name": "Masuk"
      }
    },
    {
      "action": "expect_url",
      "contains": "/dashboard",
      "claim_id": "login-valid-user"
    }
  ]
}
```

Action MVP:

- `goto`
- `click`
- `fill`
- `select`
- `press`
- `wait_dom`
- `expect_dom`
- `expect_url`
- `expect_title`
- `probe`

Tidak tersedia pada MVP:

- arbitrary `evaluate`;
- raw JavaScript/Python;
- command shell;
- upload/download file;
- browser extension;
- cross-origin navigation tanpa allowlist;
- action destruktif tanpa deklarasi eksplisit.

## 10. Selector dan provenance

Urutan selector yang dianjurkan:

1. `role + accessible name`
2. `label`
3. `data-testid`
4. text yang stabil
5. CSS sebagai fallback terakhir

Setiap selector sebaiknya memiliki provenance:

| Provenance | Makna |
|---|---|
| `source` | Ditemukan langsung dari codebase |
| `existing_test` | Diambil dari test yang sudah ada |
| `runtime_probe` | Ditemukan dari DOM aktual |
| `heuristic` | Diperkirakan model, confidence rendah |

Provenance ikut menentukan klasifikasi error. Stable selector dari source yang hilang memiliki bobot berbeda dibanding selector heuristic yang gagal.

## 11. DOM probe

Probe adalah fallback discovery, bukan langkah wajib setiap run.

Probe dijalankan jika:

- selector dari kode tidak ditemukan;
- aplikasi membangun elemen secara dinamis;
- accessible name tidak dapat disimpulkan dari source;
- existing test menggunakan selector yang sudah tidak valid.

Output probe dibatasi, misalnya:

```json
{
  "url": "http://localhost:8000/login",
  "title": "Login",
  "headings": ["Masuk"],
  "buttons": [
    {"role": "button", "name": "Masuk", "testid": null}
  ],
  "inputs": [
    {"role": "textbox", "label": "Email", "type": "email"}
  ]
}
```

Ketentuan:

- jumlah elemen dibatasi;
- text dipotong;
- hidden element diabaikan kecuali diminta;
- value input tidak direkam;
- secret direduksi sebelum ditulis;
- full DOM tidak dimasukkan ke prompt.

Probe dapat di-cache berdasarkan kombinasi commit/tree hash, route, viewport, dan auth profile. Cache tidak digunakan jika source atau route terkait berubah.

## 12. Evidence runtime

Evidence per step dibuat dalam bentuk JSONL agar runner dapat menerima heartbeat dan progress tanpa menunggu proses selesai.

```json
{
  "step": 4,
  "action": "expect_url",
  "claim_id": "login-valid-user",
  "status": "failed",
  "expected": {"contains": "/dashboard"},
  "actual": {"url": "/login?error=1"},
  "url_after": "/login?error=1",
  "duration_ms": 420,
  "origin": "app"
}
```

Evidence yang dikumpulkan:

- status dan durasi step;
- selector yang berhasil digunakan;
- URL dan title;
- bounded outer HTML target;
- assertion expected vs actual;
- same-origin HTTP 5xx yang relevan;
- uncaught page error;
- console error terfilter;
- artifact reference.

Evidence yang tidak otomatis dikirim ke model:

- full page HTML;
- seluruh stdout/stderr;
- screenshot;
- trace;
- nilai credential;
- request/response body sensitif.

## 13. Klasifikasi kegagalan

Gunakan tiga origin, bukan dua:

| Origin | Definisi | Verdict umum | Boleh di-heal? |
|---|---|---|---|
| `app` | Evidence cukup bahwa aplikasi melanggar claim | `fail` | tidak |
| `harness` | Evidence cukup bahwa scenario/runtime test bermasalah | `incomplete` | terbatas |
| `unknown` | Evidence belum cukup menentukan penyebab | `incomplete` | tidak otomatis |

Contoh klasifikasi:

| Kondisi | Origin | Hasil |
|---|---|---|
| Assertion grounded tidak terpenuhi | `app` | `fail` |
| HTTP 5xx pada navigasi/request utama | `app` | `fail` |
| Uncaught same-origin page error | `app` | `fail` |
| Browser tidak terpasang | `harness` | `incomplete` |
| Base URL tidak dapat dijangkau | `harness` | `incomplete` |
| Selector heuristic tidak ditemukan | `harness` | `incomplete` |
| Stable selector hilang tetapi halaman juga belum stabil | `unknown` | `incomplete` |
| Background analytics menghasilkan 5xx | observasi | warning, bukan otomatis `fail` |
| Console error pihak ketiga | observasi | warning atau filter |

Nilai `unknown` tidak boleh otomatis diubah menjadi `harness`, karena hal tersebut dapat menyembunyikan regresi aplikasi.

## 14. Verdict

### 14.1 Verdict browser

- `pass`: seluruh blocking claim terbukti, tidak ada gap, dan tidak ada app error.
- `fail`: minimal satu blocking claim gagal dengan evidence app-origin.
- `incomplete`: verifikasi tidak dapat diselesaikan atau origin masih ambigu.

Reason untuk `incomplete` antara lain:

- `playwright_missing`
- `browser_missing`
- `base_url_unreachable`
- `spec_invalid`
- `harness_error`
- `unknown_origin`
- `stuck`
- `timeout`
- `launch_failed`
- `output_truncated`

### 14.2 Kombinasi hybrid

| Browser | Delegated review | Final |
|---|---|---|
| `fail` | apa pun | `fail` |
| `pass` | bersih | `pass` |
| `pass` | menemukan blocking issue | `fail` |
| `pass` | tidak lengkap | `incomplete` |
| `incomplete` | apa pun | `incomplete` |

Browser pass bukan bukti bahwa coverage sudah cukup. Hybrid reviewer bertugas menilai apakah claim dan scenario memang mewakili risiko perubahan.

### 14.3 Normalisasi ke kontrak canonical (D2)

Runtime verify saat ini memiliki tiga titik yang memperlakukan output verify sebagai delegated contract dan hanya mengecualikan mode quick (`meta.mode == "quick"` atau `meta.quick_verify`):

1. `_finalize_verify_result` di `core/evidence/result_shaping.py` — memanggil `validate_verification_contract` dan menulis `meta.verdict`; dipanggil dua kali (worker di `main.run` dan `await_job` di `core/jobs/job_lifecycle.py`).
2. Predicate continuation di `core/provider/continuation.py` — meminta `[VERIFICATION]` ulang bila shape tidak memenuhi contract.
3. Invalid-evidence guard di `core/provider/executor.py` — menolak output verify tanpa `[VERIFICATION]`.

Mode `e2e` **tidak** menambah pengecualian baru pada ketiga titik tersebut. Sebagai gantinya, normalizer stage 3 menjamin bahwa `result.content` yang keluar dari branch `e2e` sudah berbentuk `[VERIFICATION]` canonical lengkap (`verdict`, seluruh section finding, `checks_run`, `not_verified`, `confidence`). Raw JSONL player, report player, dan `[E2E EVIDENCE]` tidak pernah menjadi `result.content` akhir; mereka tersimpan sebagai artifact dan ikut ke prompt reviewer saja.

Aturan pemetaan hasil player ke kontrak canonical:

| Hasil player | Ke kontrak canonical |
|---|---|
| Claim blocking gagal, origin `app` | finding pada section blocking, `severity` sesuai claim, tag `origin` **temporal** (`introduced`/`regression`/`pre_existing`/`unknown`, ditentukan reviewer dari kode + git scope), plus field `evidence_source: e2e_runtime` |
| Claim non-blocking gagal, origin `app` | finding non-blocking dengan field yang sama |
| Step/claim gagal, origin `harness` | entri `not_verified` dengan reason (`selector_missing`, `harness_error`, ...) — runtime otomatis menahan verdict di `incomplete` |
| Step/claim gagal, origin `unknown` | entri `not_verified` dengan reason `unknown_origin` — tidak boleh dipromosikan ke `harness` |
| Preflight gagal (`playwright_missing`, `browser_missing`, `base_url_unreachable`, `spec_invalid`) | seluruh claim masuk `not_verified` dengan reason tersebut; `checks_run` kosong dengan alasan |
| Step sukses / claim terbukti | baris pada `checks_run` (`e2e:<claim_id>: pass`) |
| Observasi (5xx background, console error pihak ketiga) | warning non-blocking, bukan finding |

Field `origin` pada finding tetap berarti temporal seperti sekarang; klasifikasi `app/harness/unknown` dari player **tidak pernah** ditulis ke `origin`. Klasifikasi itu hidup di `[E2E EVIDENCE]` dan di `evidence_source`/reason `not_verified`.

Combine matrix §14.2 diterapkan oleh normalizer setelah reviewer menjawab: bila browser `fail` tetapi reviewer menulis `verdict: pass`, normalizer menurunkan verdict efektif ke `fail` dan mencatat warning `reviewer_verdict_overridden`. Ini harus terjadi **sebelum** `validate_verification_contract` dan `_verify_exit_code` dijalankan, karena kontrak saat ini mengizinkan `DONE` dengan hanya warning verification-gap tetap exit `0` (`core/evidence/contract.py`, bagian exit status). Verdict efektif yang sudah `fail`/`incomplete` menjamin exit code non-nol tanpa mengubah pemetaan exit yang ada.

## 15. Self-healing

### 15.1 Keputusan MVP

MVP tidak memiliki healing berbasis reasoning berulang. Runtime hanya mencoba candidate selector deterministik yang sudah ada di spec.

Alasannya:

- mengurangi kompleksitas;
- mencegah expected behavior berubah diam-diam;
- memudahkan pengukuran false failure yang sebenarnya;
- menjaga penggunaan token tetap rendah.

### 15.2 Fase lanjutan

Jika data MVP menunjukkan selector failure signifikan, tambahkan healing terbatas.

Yang boleh berubah:

- selector;
- urutan candidate;
- wait strategy;
- timeout dalam batas konfigurasi;
- bootstrap route yang semantik hasilnya sama.

Yang tidak boleh berubah:

- claim;
- expected URL;
- expected text/state;
- severity;
- assertion type;
- expected business outcome.

Setiap healing harus memiliki before/after, alasan, evidence source, dan iteration counter. Default maksimum ditentukan setelah data smoke test tersedia, bukan sebagai angka asumsi permanen.

## 16. Screenshot, HTML, dan trace

Perlu dibedakan antara menyimpan artefak dan meminta model menganalisis artefak.

| Kondisi | Screenshot disimpan | Dibaca model otomatis |
|---|---:|---:|
| Pass | tidak | tidak |
| App failure jelas | opsional satu | tidak |
| Harness selector failure | opsional | tidak; gunakan probe dahulu |
| Unknown/stuck | ya | hanya jika DOM/report tidak cukup |
| Navigasi timeout | ya | jika dibutuhkan untuk klasifikasi |

Full HTML hanya disimpan pada failure, stuck, atau unknown. Pada step sukses cukup menyimpan digest dan bounded outer HTML target.

Trace dapat dimulai sejak awal tetapi hanya dipertahankan ketika run gagal dan bukti lain belum cukup. Retention artifact harus dibatasi berdasarkan jumlah run atau ukuran.

Lokasi dan indexing (keputusan MVP):

- artifact E2E disimpan run-local di `sessions/<session>/logs/<run>/e2e/` (spec final ter-redact, events JSONL, report, screenshot/HTML/trace sesuai policy), memakai archive dan redaction audit yang sudah ada di `core/evidence/runtime_io.py`;
- artifact E2E **tidak** dimasukkan ke `evidence.jsonl`. Index evidence saat ini hanya menerima artifact immutable berformat `sessions/<session>/logs/<run>/output.raw.md` dengan hash dan anchor freshness (`core/evidence/evidence_store.py`), dan executor hanya melakukan `record()`/reuse untuk role `exploration|reasoning` — verify hari ini pun tidak di-index;
- `output.raw.md` untuk run `e2e` berisi `[VERIFICATION]` canonical hasil stage 3, sama seperti run delegated;
- pruning mengikuti retention archive yang ada; direktori `e2e/` ikut terhapus bersama run-nya;
- implementasi (batch 4): HTML ber-batas dan satu screenshot per step gagal, `trace.zip` hanya untuk run gagal; nilai input dikosongkan sebelum capture; tidak ada screenshot maupun trace bila scenario me-resolve `${ENV}`; `commands.e2e.artifact_max_mb` memangkas trace, lalu screenshot, lalu HTML.

Reuse/indexing artifact E2E dievaluasi ulang setelah Fase 5 bila ada kebutuhan replay lintas run.

## 17. Hybrid review hemat token

Hybrid reviewer menerima:

- task dan scope perubahan;
- daftar verification claims;
- changed symbols atau source references;
- ringkasan step;
- assertion result;
- error classification;
- path artifact;
- potongan kode relevan yang bounded.

Hybrid reviewer tidak menerima full DOM, raw log, screenshot, atau trace secara otomatis.

Mekanisme (D1 + D2): reviewer adalah delegated call verify biasa. Prompt builder verify (`core/prompt/prompt_builder.py`, branch penyisipan section verify) mendapat satu section tambahan `[E2E EVIDENCE]` yang berisi ringkasan di atas dalam bentuk compact. Format output yang diminta dari reviewer **tidak berubah**: `[VERIFICATION]` canonical dengan finding tags yang sama. Reviewer tidak diminta mengulang klasifikasi `app/harness/unknown`; ia menerima klasifikasi itu sebagai input dan menambah dimensi temporal (`origin`) serta penilaian coverage.

Bentuk compact `[E2E EVIDENCE]` (usulan):

```text
[E2E EVIDENCE]
browser_verdict: fail
claims:
- login-valid-user | blocking | status: failed | origin: app
steps: 5 total, 4 passed, 1 failed (step 5 expect_url)
assertion_failed:
- claim: login-valid-user | expected: url contains /dashboard | actual: /login?error=1
app_errors:
- none
harness_issues:
- none
not_verified:
- none
artifacts: sessions/<session>/logs/<run>/e2e/
```

Tanggung jawab reviewer:

1. Memeriksa apakah claim sesuai dengan perubahan.
2. Memeriksa apakah blocking behavior sudah tercakup.
3. Mencocokkan browser failure dengan kode terkait.
4. Mendeteksi false pass akibat assertion terlalu lemah.
5. Menghasilkan finding menggunakan contract verify yang sudah ada.

Jika field `origin` pada finding sudah memiliki arti temporal seperti `introduced`, `regression`, `pre_existing`, atau `unknown`, jangan gunakan `origin=behavior`. Tambahkan field evidence terpisah, misalnya:

```json
{
  "origin": "regression",
  "evidence_source": "e2e_runtime"
}
```

## 18. Keamanan dan isolasi

Default keamanan:

- hanya izinkan loopback/local development;
- remote host membutuhkan opt-in dan allowlist;
- production ditolak secara default;
- navigasi lintas origin ditolak kecuali diizinkan;
- credential hanya melalui `${ENV_NAME}` atau auth state lokal;
- environment value tidak masuk spec final, report, log, atau prompt;
- input sensitif dibersihkan sebelum screenshot;
- storage state disimpan di area runtime yang diabaikan Git;
- request/response body tidak direkam secara default;
- action destruktif membutuhkan metadata dan konfigurasi eksplisit;
- tidak mengeksekusi start command atau shell command dari project target, kecuali existing test command yang ditulis user sendiri di `commands.e2e.existing_test_command` (argv tanpa shell, file allowlist, timeout, output di-scrub).

Untuk action yang dapat mengubah data, scenario perlu mendeklarasikan:

```json
{
  "side_effect": "creates_test_data",
  "test_environment_required": true,
  "cleanup": "external_fixture"
}
```

## 19. Konfigurasi awal

Nama dan struktur final harus mengikuti pola konfigurasi project saat implementasi. Bentuk konseptual:

```json
{
  "commands": {
    "verify_mode": "e2e",
    "e2e": {
      "base_url": "http://localhost:8000",
      "browser": "chromium",
      "headless": true,
      "spec_path": ".workflow/e2e/spec.json",
      "nav_timeout_ms": 15000,
      "step_timeout_ms": 8000,
      "idle_timeout_s": 30,
      "total_timeout_s": 300,
      "probe_max_elements": 200,
      "hybrid_review": true,
      "allow_remote": false,
      "allowed_origins": [],
      "allow_side_effects": false,
      "fail_on_console_error": false,
      "artifact_max_mb": 25,
      "replay": false,
      "existing_test_command": [],
      "existing_test_allowlist": [],
      "existing_test_timeout_s": 300
    }
  }
}
```

Nilai timeout dan batas probe adalah nilai awal untuk smoke test, bukan keputusan permanen. Kalibrasi berdasarkan data beberapa project nyata.

Reader konfigurasi: `verify_mode` di `core/runtime/config_defaults.py` diperluas menjadi `delegated | syntax | e2e` (allowlist murni, lihat catatan §4). Blok `commands.e2e` membutuhkan reader bertipe baru dengan default per field dan validasi nested (saat ini tidak ada reader untuk struktur bersarang di bawah `commands`); nilai tidak valid diberi warning dan jatuh ke default field, bukan mematikan mode.

### 19.1 Kebijakan dependency (D3)

- Playwright adalah **optional extra**, dipin di `requirements-e2e.txt` (`playwright==1.60.0`) dan hanya dipasang oleh `python install.py --apply --with-e2e`, yang juga menjalankan `python -m playwright install chromium`. Instalasi default tidak berubah.
- Runtime inti tetap stdlib-only. Hanya modul di `core/evidence/e2e/` yang mengimpor `playwright`, dan hanya secara lazy di dalam fungsi yang menjalankan player — bukan pada import modul.
- Test invariant dependency (`tests/checks/deps.py`) mengecualikan `core/evidence/e2e/` dari pemindaian stdlib-only, atau mengizinkan `playwright` sebagai import opsional yang dibungkus `try/except ImportError`.
- Preflight branch `e2e`: (1) import Playwright, (2) cek browser Chromium terpasang, (3) cek `base_url` dapat dijangkau. Gagal pada salah satunya → `incomplete` dengan reason terkait; tidak ada instalasi otomatis.
- Perintah instalasi (`--with-e2e`) didokumentasikan di skill `verify.md` dan `docs/runtime-contracts.md`; `/.doctor` melaporkan `e2e_readiness` (interpreter, versi terpasang dibanding versi pin, package, browser, URL). Versi 1.60.0 dipakai di smoke Chromium Windows.
- Manifest/bundle tidak membawa binary browser.

## 20. Model proses

Player dijalankan sebagai child process:

```text
runner
  └─ python -m core.evidence.e2e.player
       ├─ stdout JSONL: progress, heartbeat, result
       ├─ stderr bounded
       ├─ browser process tree
       └─ artifact directory
```

Runner bertanggung jawab atas:

- validasi path dan config;
- environment redaction;
- stdout/stderr bounded capture;
- heartbeat;
- idle timeout;
- total timeout;
- penghentian process tree;
- parsing report;
- klasifikasi output rusak atau terpotong.

Player bertanggung jawab atas:

- membuka browser;
- menjalankan action;
- mengevaluasi assertion;
- mengumpulkan browser events;
- membuat probe;
- membuat artifact sesuai policy;
- menulis event JSONL.

### 20.1 Reuse vs baru

Yang sudah ada dan dipakai ulang:

| Kebutuhan | Sumber existing |
|---|---|
| Process-tree termination lintas platform | `utils.osutil.terminate_tree()`, `hidden_run_kwargs()` |
| Pola bounded stdout/stderr capture, polling timeout, heartbeat callback | adapter provider (`adapters/providers/opencode_adapter.py`, bagian run subprocess) — pola ditiru, bukan dipanggil langsung |
| Heartbeat outer, idle stall detection, recovery worker | `core/jobs/job_manager.py`, `core/jobs/job_lifecycle.py` — membungkus seluruh job verify, termasuk stage 2 |
| Archive artifact, pruning, redaction audit, call metadata | `core/evidence/runtime_io.py` |
| Delegated call untuk stage 1 dan stage 3 | jalur provider existing di `Executor.execute()` |

Yang baru (`core/evidence/e2e/`):

| Modul | Isi |
|---|---|
| `spec.py` | schema scenario + claims, action allowlist, selector provenance, `${ENV_NAME}` substitution, validasi |
| `preflight.py` | lazy import Playwright, cek browser, cek base URL, safe-URL/allowed-origin policy |
| `supervisor.py` | spawn player, parse JSONL, heartbeat child, idle timeout, total timeout, panggil `terminate_tree`, klasifikasi output rusak/terpotong |
| `player.py` | implementasi Playwright: action, assertion, observer console/pageerror/response, probe, artifact policy |
| `classify.py` | taksonomi `app/harness/unknown` |
| `normalize.py` | hasil player → `[E2E EVIDENCE]` + finding/`not_verified`/`checks_run` canonical; combine fail-closed |
| `redact.py` | redaction rekursif spec/event/report (dapat memakai helper redaction runtime_io bila cocok) |

Heartbeat player dilaporkan ke JobManager melalui mekanisme heartbeat job yang sudah ada, sehingga stall detection outer tetap berlaku selama stage 2 tanpa penambahan jalur baru.

## 21. Rencana implementasi bertahap

Posisi tiap fase ada di §1.2; batch implementasi 1–8 tercatat di §28.

### Fase 0 — Baseline dan validasi kebutuhan

1. Pilih 3–5 perubahan web nyata yang sebelumnya hanya diverifikasi melalui kode.
2. Catat expected behavior dan bug runtime yang pernah lolos.
3. Tetapkan baseline token, waktu, dan kualitas verdict saat ini.
4. Pastikan terdapat environment non-production yang dapat diuji.

Output: dataset kecil untuk menilai apakah mode E2E memberikan nilai.

### Fase 1 — Kontrak tanpa browser

1. Tambahkan `e2e` ke allowlist `verify_mode` dan buat reader bertipe `commands.e2e` dengan default + validasi nested (`core/runtime/config_defaults.py`).
2. Tambahkan branch `e2e` di `Executor.execute()` di sebelah branch `syntax`, dengan preflight yang menghasilkan `incomplete` ber-reason; uji bahwa mode `e2e` tanpa dependency **tidak** jatuh ke `delegated`.
2a. Ekstrak `Executor._run_delegated()` dan `Executor._build_delegated_prompt()` dari `execute()` tanpa perubahan perilaku (suite + sim hijau sebelum lanjut); tambahkan route internal `e2e_spec` (`config/routing.py`, role exploration) dan `INTERNAL_COMMANDS`; `e2e_spec` tidak masuk `choices` argparse `main.py` dan `main.run()` menolaknya; uji urutan dispatch §5.1 dengan fake adapter (`e2e_spec` → `verify`, tidak ada panggilan ketiga).
3. Buat schema scenario dan verification claims (`spec.py`), termasuk selector provenance.
4. Buat kontrak output stage 1: prompt `e2e_spec` meminta format evidence exploration standar + section `[E2E SPEC]`; parser section + fixture; uji bahwa fixture lolos generic evidence guard (`_EVIDENCE_MARKERS` tidak diubah), bahwa `[EVIDENCE]` tanpa `[E2E SPEC]` memicu satu continuation terstruktur lalu `incomplete:spec_invalid`, dan bahwa parser tidak bertabrakan dengan parser `[VERIFICATION]` existing.
5. Buat taksonomi `app/harness/unknown` (`classify.py`).
6. Buat normalizer (`normalize.py`): pemetaan §14.3, `[E2E EVIDENCE]` compact, combine matrix fail-closed, penetapan verdict efektif sebelum validator.
7. Buat redaction contract (`redact.py`).
8. Buat fake player yang mengirim JSONL deterministik (pass, app fail, harness fail, unknown, malformed, stall, crash).
9. Buat supervisor (`supervisor.py`) dan uji dengan fake player: heartbeat, idle timeout, total timeout, process-tree termination, stdout/stderr besar, report terpotong.
10. Uji jalur akhir: `result.content` dari branch `e2e` selalu lolos `validate_verification_contract` tanpa warning shape; raw JSONL/report tidak pernah menjadi `content`; predicate continuation mengembalikan `None`; invalid-evidence guard tidak menolak; exit code sesuai verdict efektif untuk seluruh kombinasi §14.2.
11. Kecualikan `core/evidence/e2e/` dari test invariant stdlib-only dan pastikan `python -c "import core"` tetap tidak menyentuh Playwright.

Exit criteria: seluruh lifecycle dapat diuji tanpa dependency browser, dan tidak ada perubahan pada `core/evidence/contract.py`, `core/evidence/result_shaping.py` (selain meta), maupun `core/provider/continuation.py`.

### Fase 2 — Playwright MVP

1. Implementasikan action MVP.
2. Implementasikan assertion MVP.
3. Tambahkan console, page error, dan network observation terfilter.
4. Implementasikan DOM probe bounded.
5. Terapkan screenshot/HTML/trace policy.
6. Terapkan same-origin dan secret policy.
7. Jalankan smoke test pada aplikasi lokal nyata.

Exit criteria: minimal satu alur pass, satu app failure, dan satu harness failure terklasifikasi benar.

### Fase 3 — Stage 1: claim builder via delegated call

Sesuai D1, claim builder adalah delegated call second_agent, bukan modul lokal terpisah.

1. Tambahkan section prompt stage 1 di `core/prompt/prompt_builder.py`: scope dari task + Git, instruksi menemukan route, component, API, selector, existing tests, dan menghasilkan `[E2E SPEC]`.
2. Reuse mekanisme scope existing pada prompt verify (changed files + consumers).
3. Bangun verification claims dan coverage resolver di dalam kontrak `[E2E SPEC]`.
4. Hasilkan scenario hanya untuk coverage gap.
5. Validasi `[E2E SPEC]` secara lokal (`spec.py`); spec tidak valid → `incomplete` reason `spec_invalid`, dengan satu continuation terstruktur bila shape hampir benar (pola continuation existing).
6. Uji bahwa full source tidak diteruskan ke player atau reviewer; hanya digest + source references.

Exit criteria: scenario dapat dijelaskan asal-usulnya melalui claim dan source references; stage 1 dapat disimulasikan dengan fake adapter yang mengembalikan `[E2E SPEC]` fixture.

### Fase 4 — Stage 3: hybrid review

1. Tambahkan section `[E2E EVIDENCE]` compact ke prompt verify (§17).
2. Reviewer diminta output `[VERIFICATION]` canonical; tidak ada format output baru.
3. Normalizer menerapkan combine matrix fail-closed dan menetapkan verdict efektif sebelum `_finalize_verify_result`.
4. Uji browser `fail` + reviewer `pass` → final `fail` dengan warning `reviewer_verdict_overridden`.
5. Uji browser `pass` + reviewer tidak lengkap → `incomplete`; browser `incomplete` + apa pun → `incomplete`.
6. Uji bahwa `_finalize_verify_result` dipanggil dua kali (worker + `await`) tanpa menggandakan warning atau mengubah verdict.

Exit criteria: verdict akhir konsisten untuk seluruh kombinasi browser/reviewer, dengan validator existing tidak dimodifikasi.

### Fase 5 — Evaluasi MVP

Ukur pada dataset Fase 0:

- tambahan bug runtime yang ditemukan;
- false failure akibat harness;
- jumlah `incomplete`;
- jumlah probe;
- jumlah screenshot yang benar-benar dibaca model;
- token per verifikasi;
- durasi per verifikasi;
- kemampuan menjalankan ulang scenario;
- stabilitas pada Windows/Linux yang didukung.

Keputusan setelah evaluasi:

- lanjutkan jika E2E menemukan gap bermakna dengan tingkat false failure yang dapat diterima;
- perbaiki classifier/probe jika `incomplete` terlalu tinggi;
- hentikan atau pertahankan sebagai opt-in jika manfaatnya kecil;
- baru pertimbangkan self-healing setelah terdapat data penyebab harness failure.

## 22. Strategi test

### Unit tests

- spec valid dan invalid;
- action allowlist;
- claim wajib;
- assertion wajib memiliki `claim_id`;
- selector provenance;
- `${ENV_NAME}` substitution;
- redaction recursive;
- error classification;
- verdict matrix;
- hybrid combination;
- safe URL dan allowed origin;
- immutable assertion saat healing fase lanjutan;
- normalizer: setiap kombinasi origin × severity × preflight reason menghasilkan finding/`not_verified`/`checks_run` yang benar;
- normalizer: output selalu lolos `validate_verification_contract` tanpa warning shape;
- `[E2E SPEC]` parser: valid, section hilang, JSON rusak;
- `verify_mode=e2e` + preflight gagal → `incomplete`, bukan `delegated`;
- pengecualian `core/evidence/e2e/` pada invariant stdlib-only, dan import `core` tanpa Playwright terpasang.

### Process tests dengan fake player

- progress JSONL;
- heartbeat;
- stdout/stderr besar;
- malformed event;
- idle timeout;
- total timeout;
- process tree termination;
- missing dependency;
- browser launch failure.

### Real browser smoke tests

- halaman sederhana berhasil;
- assertion DOM gagal;
- redirect salah;
- selector heuristic gagal;
- page error;
- main request HTTP 5xx;
- third-party error tidak menyebabkan false fail;
- secret tidak muncul di artifact;
- screenshot hanya dibuat sesuai policy.

### Integration tests

- E2E standalone pass/fail/incomplete;
- E2E + hybrid reviewer;
- invalid evidence guard;
- exit code;
- artifact retention;
- existing target test reuse;
- coverage gap menghasilkan scenario tambahan.

## 23. Acceptance criteria MVP

MVP dinyatakan berhasil jika semua kriteria di bawah terpenuhi. Kolom status = posisi 2026-09-14 setelah batch 1–8; "smoke" berarti terbukti dengan Chromium nyata terhadap `tests/fixtures/e2e_app/` di Windows.

| # | Kriteria | Status | Dasar |
|---|---|---|---|
| 1 | Test pass dapat selesai tanpa screenshot analysis. | terpenuhi (smoke) | Run pass tidak menyimpan screenshot atau trace; tidak ada stage yang mengirim screenshot ke model (`screenshots_sent_to_model` selalu 0). |
| 2 | Setiap scenario mempunyai minimal satu grounded claim. | terpenuhi | `source_refs` wajib dan harus menunjuk file serta baris yang ada, atau `req:<id>` (`ground_claims`). |
| 3 | Setiap assertion terhubung ke claim. | terpenuhi | `validate_scenario`. |
| 4 | Existing E2E test digunakan lebih dahulu jika relevan. | terpenuhi (opt-in) | Test yang dapat dijalankan menggantikan assertion atas claim yang di-cover-nya dan dijalankan sebelum player, hanya bila user mengisi `existing_test_command`. |
| 5 | Selector heuristic yang gagal tidak langsung menjadi app failure. | terpenuhi (smoke) | Diklasifikasi `harness`, dengan probe sebagai pengganti screenshot. |
| 6 | Grounded assertion yang gagal tidak dapat di-heal menjadi pass. | terpenuhi | Tidak ada healing; app failure fail-closed. |
| 7 | Browser/dependency/environment failure menghasilkan `incomplete`. | terpenuhi (smoke) | Preflight, launch, navigasi keluar origin, halaman macet (`stuck`). |
| 8 | Full DOM dan screenshot tidak otomatis masuk prompt. | terpenuhi | Prompt stage 3 hanya berisi `[E2E EVIDENCE]` terstruktur; uji marker source tidak muncul di prompt maupun artifact. |
| 9 | Secret tidak muncul dalam report atau artifact yang diperiksa. | terpenuhi, dengan batas | Scrub raw, URL-encoded, HTML- dan JSON-escaped; HTML artifact di-scrub; tanpa screenshot/trace bila `${ENV}` dipakai; smoke memindai byte seluruh artifact. Batas: nilai di bawah 4 karakter dan encoding lain (mis. base64). |
| 10 | Browser `fail` tidak dapat diubah menjadi `pass` oleh reviewer. | terpenuhi | Combine fail-closed. |
| 11 | Scenario dapat dijalankan ulang pada state aplikasi yang sama. | sebagian | request `phase: run` dengan scenario yang sama bisa dijalankan ulang; replay (`last_spec.json`) tidak diimplementasikan; reproducibility diukur per `scenario_hash`. |
| 12 | Process browser dihentikan dengan benar ketika timeout. | terpenuhi (Windows) | Smoke: halaman macet, idle timeout, nol proses Chromium tersisa. Linux belum diuji. |
| 13 | Penggunaan token lebih rendah dibanding loop browser visual untuk alur yang sama. | belum | Token per run dan rasio terhadap delegated verify tersedia di `report`; baseline loop visual belum diukur. |

## 24. Metrik evaluasi

Tidak menetapkan angka keberhasilan tanpa baseline. Kumpulkan terlebih dahulu:

| Metrik | Tujuan |
|---|---|
| Runtime bugs found | Mengukur nilai tambahan dibanding code review |
| False failure rate | Mengukur kualitas selector dan classifier |
| Incomplete rate | Mengukur kesiapan environment dan harness |
| Tokens per verification | Membandingkan dengan browser visual |
| Browser runs per claim | Mengukur efisiensi probe/rerun |
| Screenshot-analysis rate | Harus menjadi pengecualian |
| Median execution time | Mengukur dampak terhadap workflow |
| Reproducibility rate | Mengukur determinisme scenario |

Metrik harus dipisahkan berdasarkan penyebab: app, harness, environment, dan unknown.

Implementasi (batch 7): tiap run e2e menulis satu baris `kind: e2e_run` ke `.workflow/quality.jsonl` berisi hitungan mentah; semua rate diturunkan saat dibaca (`core/audit/telemetry.py`, bagian `e2e` pada `main.py --command report`). Run dipisah per `run_kind` (`project`, `smoke`, `fake`) sebelum diagregasi, agar run test suite tidak terbaca sebagai bukti nilai. Pemetaan:

- false failure rate → kandidat berupa `harness_failure_rate` dan `unknown_origin_rate`; penilaian false failure tetap butuh manusia;
- incomplete rate → `incomplete_rate` dan `incomplete_by_reason`;
- tokens per verification → `tokens_per_run` (join prompt id stage ke `usage.jsonl`) dan `project_vs_baseline_token_ratio` terhadap delegated verify di workspace yang sama;
- browser runs per claim → `browser_runs_per_claim`;
- screenshot-analysis rate → `screenshot_analysis_rate`;
- median execution time → `duration_seconds.median`;
- reproducibility rate → run berulang per `scenario_hash` dengan browser verdict sama;
- runtime bugs found → butuh label manusia per perubahan dataset (`WORKFLOW_E2E_LABEL`).

## 25. Risiko utama dan mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Selector rapuh | false failure | provenance, role/testid, probe bounded |
| Assertion terlalu lemah | false pass | verification claim + hybrid coverage review |
| Healing mengubah ekspektasi | bug tersembunyi | MVP tanpa reasoning heal; immutable claims |
| Error pihak ketiga | false fail | same-origin filtering dan warning policy |
| Secret masuk artifact | kebocoran data | env reference, redaction, bounded evidence |
| Test menyentuh production | kerusakan data | local-only default, allowlist, destructive guard |
| Hybrid boros token | biaya meningkat | compact evidence, pointer-first |
| App/environment tidak stabil | banyak incomplete | environment preflight dan reason terpisah |
| Browser process macet | job hang | heartbeat, idle/total timeout, terminate tree |
| DSL terlalu terbatas | coverage rendah | perluas action berdasarkan data, bukan asumsi |

Risiko residual per 2026-09-14 (setelah batch 1–8):

| Risiko | Dampak | Status / mitigasi |
|---|---|---|
| Nilai `${ENV}` di bawah 4 karakter, atau echo dalam encoding selain raw, URL, HTML, JSON (mis. base64), tidak di-scrub | nilai bisa sampai ke events, HTML, atau prompt stage 3 | Diterima: scrub substring pendek merusak URL dan selector. Screenshot dan trace tidak dibuat bila `${ENV}` dipakai. |
| Replay memakai spec lama setelah kode di file yang sama berubah | selector atau alur usang, hasil `harness` atau `unknown` | Opt-in (`replay: false` default); `code_changed_since_saved` dilaporkan. |
| `existing_test_command` menjalankan kode project target | kode target berjalan di mesin user | Hanya dari config user; argv tanpa shell, path polos + allowlist, timeout + kill tree, output di-scrub. Belum diuji dengan runner nyata. |
| Klik atau aksi yang mengubah data tanpa deklarasi `side_effect` | data test berubah tanpa izin | Tidak dapat dideteksi otomatis; mitigasi: default loopback, instruksi prompt stage 1, `allow_side_effects: false`. |
| Smoke hanya di Windows + Chromium | perilaku Linux, Firefox, WebKit belum terbukti | Uji saat ada job CI atau mesin Linux. |
| `_guarded_imports` di `tests/checks/deps.py` menganggap import dalam fungsi di blok `try` sebagai guarded | false-negative untuk deferred import non-stdlib | Belum diperbaiki (sejak Fase 1). |
| Parser kontrak verify menghitung `- none — <penjelasan>` sebagai finding | verdict `fail` palsu pada review | Pre-existing, di luar rencana ini. |
| Usage row run e2e dicatat saat finalisasi dengan `prompt_id`, `command`, dan `role` stage terakhir; row stage 1 terlabel `verify` (ditemukan batch 7) | `calls_by_command` dan metrik per role salah label untuk run e2e; total token per run tetap benar | Join token metrik e2e memakai semua prompt id stage sehingga tiap row terhitung sekali. Perbaikan atribusi per invocation ada di `core/provider/executor.py` (kode Fase 1), belum dikerjakan. |

Diperbaiki selama batch implementasi: nilai resolved yang di-echo halaman bocor ke prompt stage 3 (verify batch 2 Fase 1); normalizer tidak me-route finding reviewer by tag; usage stage gagal tercatat dua kali; nilai typed yang kembali dalam bentuk URL-encoded lolos scrub (ditemukan smoke batch 3, diperbaiki batch 4); page error script pihak ketiga diatribusikan ke origin halaman sehingga menjadi false fail (ditemukan smoke batch 5).

## 26. File terdampak

Kondisi working tree setelah batch 1–8 (belum di-commit). **baru** = file baru, **berubah** = dimodifikasi, **tidak berubah** = sengaja tidak disentuh.

| File | Status | Perubahan |
|---|---|---|
| `core/runtime/config_defaults.py` | berubah | `VERIFY_MODES` + `e2e`; `default_e2e()` termasuk `allow_side_effects`, `artifact_max_mb`, `replay`, `existing_test_*`; validasi nested; `e2e_config()` |
| `core/provider/executor.py` | berubah | ekstrak `_run_delegated()` + `_build_delegated_prompt()`; branch `e2e` ke `runner.run()` |
| `config/routing.py` | berubah | route internal `e2e_spec` + `INTERNAL_COMMANDS` |
| `main.py` | berubah | `main.run()` menolak command internal |
| `core/prompt/prompt_builder.py` | berubah | prompt `e2e_spec` (source_refs, selector candidates, navigasi, side effect, existing test); `[E2E EVIDENCE]` untuk verify |
| `core/audit/diagnostics.py` | berubah | `e2e_readiness` di doctor |
| `core/audit/telemetry.py` | berubah | `e2e_metrics()` di `report()` |
| `core/evidence/e2e/runner.py` | baru | preflight, sumber spec (`spec_path`, replay, stage 1), validasi + grounding, existing test, player, scrub + budget artifact, review, normalisasi, baris `e2e_run` |
| `core/evidence/e2e/spec.py` | baru | parser `[E2E SPEC]`; `validate_scenario` (origin, side effect, candidates, source_refs, `covered`); `ground_claims`; `step_selectors`; `navigation_error` |
| `core/evidence/e2e/browser.py` | baru | player Playwright: `Session` (aksi, candidates, polling, route guard, observer, capture) dan `run_scenario` (launch, trace, close) |
| `core/evidence/e2e/player.py` | baru | child process; fake modes; non-fake ke `browser.run_scenario` |
| `core/evidence/e2e/existing_tests.py` | baru | `select`, `build_argv`, `run` (tanpa shell, timeout, scrub) |
| `core/evidence/e2e/classify.py` | baru | `classify_step`, `build_report` (artifact, probe, existing test, console enforced) |
| `core/evidence/e2e/normalize.py` | baru | `evidence_block` (probe, existing test, artifact), `to_verification` |
| `core/evidence/e2e/redact.py` | baru | `scrub_resolved` (varian encoded), `scrub_text_files`, writer artifact |
| `core/evidence/e2e/supervisor.py` | baru | child process JSONL, timeout, `terminate_tree` |
| `core/evidence/e2e/preflight.py` | baru | kebijakan `base_url`, dependency, reachability, `readiness()` untuk doctor |
| `install.py`, `installer/settings.py` | berubah | `--with-e2e`: pip `requirements-e2e.txt`, lalu `playwright install chromium` |
| `requirements-e2e.txt` | baru | `playwright==1.60.0` |
| `tests/checks/deps.py` | berubah | izin import `playwright` ber-guard hanya di paket e2e |
| `tests/checks/installer.py` | berubah | check `installer-e2e` |
| `tests/checks/e2e_spec.py`, `e2e_normalize.py`, `e2e_supervisor.py`, `e2e_routing.py` | baru | kontrak, klasifikasi, supervisor, orkestrasi (termasuk grounding, replay, existing test, baris metrik) |
| `tests/checks/e2e_browser.py`, `e2e_redact.py`, `e2e_doctor.py`, `e2e_existing_tests.py`, `e2e_metrics.py`, `e2e_smoke.py` | baru | player dengan page pengganti, scrub, doctor, existing test, metrik, smoke Chromium opt-in |
| `tests/fixtures/e2e_app/*.html` | baru | aplikasi fixture untuk smoke |
| `tests/run.py`, `tests/scenario.py` | berubah | registrasi check baru |
| `tools/sim/sim_flows.py` | berubah | S22, S22b, S22c, S22d (replay) |
| `docs/runtime-contracts.md` | berubah | section `verify_mode=e2e` |
| `dist/config/claude/skills/verify.md`, `dist/config/claude/CLAUDE.md` | berubah | dokumentasi mode `e2e` |
| `dist/manifest.json` | berubah | regen |
| `core/evidence/contract.py`, `core/evidence/result_shaping.py`, `core/provider/continuation.py`, `core/evidence/runtime_io.py`, `utils/osutil.py` | tidak berubah | dipakai apa adanya |

## 27. Keputusan final

Mode E2E layak dibangun sebagai eksperimen terukur karena menutup celah antara “kode terlihat benar” dan “perilaku runtime terbukti”. Implementasi tidak boleh langsung mencakup semua kemampuan browser automation.

Scope awal yang disetujui untuk MVP:

- satu browser: Chromium;
- app sudah berjalan;
- scenario JSON deklaratif;
- claims dan selector provenance;
- action/assertion dasar;
- existing-test-first;
- DOM-first evidence;
- `app/harness/unknown` classification;
- `pass/fail/incomplete`;
- compact hybrid review;
- screenshot/trace sebagai fallback;
- tanpa reasoning-based self-healing;
- D1: three-stage di runtime (delegated `[E2E SPEC]` → player lokal → delegated review → normalizer);
- D2: hasil dinormalisasi ke `[VERIFICATION]` canonical, validator/continuation/guard existing tidak diubah;
- D3: Playwright optional extra + lazy import; absen → `incomplete:playwright_missing`;
- artifact E2E run-local, tidak masuk `evidence.jsonl`.

Setelah MVP diuji pada beberapa perubahan nyata, data hasil pengukuran menjadi dasar apakah fitur dilanjutkan, diperluas, atau tetap opt-in.

## 28. Riwayat revisi

| Tanggal | Perubahan |
|---|---|
| 2026-09-14 | Draft awal |
| 2026-09-14 | Revisi setelah analisis terhadap codebase verify: tambah §1.1 keputusan D1–D3, catatan fallback `verify_mode` (§4), arsitektur three-stage + kontrak `[E2E SPEC]` (§5), normalisasi canonical + exit code (§14.3), lokasi artifact (§16), mekanisme reviewer + `[E2E EVIDENCE]` (§17), kebijakan dependency (§19.1), reuse vs baru (§20.1), Fase 1/3/4 dikonkretkan (§21), test tambahan (§22), daftar file konkret (§26) |
| 2026-09-14 | Setelah verifikasi: tambah §5.1 aturan dispatch anti-rekursi (command internal `e2e_spec` untuk stage 1, `_verify_mode_override="delegated"` untuk stage 3, recursion guard + test), D2 menyebut kedua metadata quick, §21 Fase 1 item 2a, §26 tambah `config/routing.py`, `main.py`, path docs konkret |
| 2026-09-14 | Setelah verifikasi ke-2: output stage 1 wajib format evidence exploration standar (`[EVIDENCE]` … `[DIGEST]`) dengan `[E2E SPEC]` sebagai section tambahan agar lolos generic evidence guard exploration/reasoning tanpa mengubah `_EVIDENCE_MARKERS` (§5.1, contoh kontrak, Fase 1 item 4); guard `main.run()` untuk command internal (§5.1, item 2a) |
| 2026-09-14 | Fase 1 diimplementasikan. Keputusan Q1: stage memakai `Executor._run_delegated()`/`_build_delegated_prompt()` yang diekstrak dari `execute()` — `_verify_mode_override` dan recursion guard dibatalkan karena tidak ada re-entry (§5.1, §21 item 2a, §26). Modul `core/evidence/e2e/{spec,classify,normalize,redact,supervisor,preflight,player,runner}.py`; test `tests/checks/e2e_{spec,normalize,supervisor,routing}.py`; sim S22/S22b/S22c; `tests/checks/deps.py` mengizinkan hanya import `playwright` ber-guard di paket e2e. Player browser nyata = Fase 2; non-fake run berakhir `incomplete: player_unavailable`. |
| 2026-09-14 | Verifikasi batch 2 menemukan 3 blocking: (1) nilai `${ENV}` hasil resolve bisa masuk prompt stage 3 lewat event player, (2) normalizer menyalin section reviewer tanpa routing by tag sehingga verdict berbeda dari validator, (3) usage stage gagal tercatat dua kali. Diperbaiki: `redact.scrub_resolved` + redact evidence block sebelum prompt/artifact; `_reviewer_sections` me-route by tag + adopsi verdict validator bila lebih ketat; `_run_delegated(record_failure=False)` untuk stage e2e. Test regresi + uji mutasi ditambahkan. Re-verify: DONE. |
| 2026-09-14 | Pembaruan status dokumen: header, §1.2 status per fase, §1.3 celah terbuka G1–G14 + housekeeping, §23 status per acceptance criteria, §25 risiko residual, §26 status per file. Isi desain §2–§22 tidak diubah. |
| 2026-09-14 | Batch implementasi 1–8: policy spec (origin, side effect, selector candidates, source_refs); packaging `requirements-e2e.txt` + `--with-e2e` + doctor; player Playwright nyata; artifact dan privasi (scrub varian encoded, tanpa screenshot/trace bila `${ENV}`, `artifact_max_mb`); smoke Chromium dan perbaikan atribusi page error pihak ketiga; grounding, replay, existing test via config; metrik `e2e_run` di `report`; dokumentasi. D1 dikoreksi (review sebelum normalizer), D3 diperbarui (packaging). Batch 1–7 belum `/.verify`. |
