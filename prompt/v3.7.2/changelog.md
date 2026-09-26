# Changelog — v3.7.2

Satu tema: rilis dokumentasi dan proses riset. Tidak ada perubahan perilaku runtime,
installer, maupun bundle prompt — `dist/` hanya berubah pada banner versi hasil
`stamp_version` dan `dist/manifest.json` hasil `gen_manifest`.

## Status rilis

Gerbang `RELEASE.md` dijalankan 2026-09-26 pada tree final rilis ini:

| Gerbang | Hasil |
|---|---|
| `python tools/maintain/stamp_version.py --check` | lolos |
| `python tools/maintain/gen_manifest.py --check` | lolos — manifest in sync dengan `dist/` |
| `python tests/run.py` | lolos — `scenario` PASS; `e2e-smoke` di-skip (opt-in `WORKFLOW_E2E_SMOKE=1`) |
| `python tools/e2e/e2e.py` | lolos — 137 passed, 0 failed, 1 skipped (`delegated commands`, opt-in `--full`); dijalankan sebelum koreksi wording terakhir, yang hanya menyentuh Markdown |
| `/.verify` (delegated) | kontrak valid: nol blocking, nol escalation, nol note; verdict runtime `incomplete` karena second agent read-only tidak menjalankan test — ditutup oleh baris di atas |

Skipped bukan pass: Chromium sungguhan (`e2e-smoke`) dan command delegated live (`--full`)
belum dijalankan. Rilis ini tidak mengubah runtime, jadi `--full` sengaja tidak dijalankan.

Temuan di luar scope, untuk rilis runtime berikutnya: parser verify
(`core/evidence/contract.py` `_NONE_ITEM`) hanya menerima penanda kosong `none`/`(none)`,
sedangkan skill verify main agent menulis `(tidak ada: …)`. Verdict `DONE` yang sah dengan
penanda itu dibaca sebagai `fail` (exit 2); terjadi dua kali saat memverifikasi rilis ini.

## Dokumentasi baru

- **`docs/research/`** — catatan riset publik: `README.md` (model riset, sumber evidence,
  provenance, status epistemik), `methodology.md`, dan `CONTRACT.md` (kontrak pengembangan dan
  riset untuk perubahan signifikan). Tujuh jenis catatan, masing-masing dengan
  `_TEMPLATE.md`: `literature`, `synthesis`, `hypotheses`, `experiments`, `real-cases`,
  `decisions`, `design-archaeology`. Rekonstruksi keputusan historis ditunda.
- **`CLAUDE.md` di root repo** — kontrak pengembangan untuk repo ini saja, tidak ikut
  terpasang ke project user (bundle global tetap `dist/config/claude/CLAUDE.md`). Catatan riset
  dilarang ditulis ke `docs/project-knowledge/`, karena runtime menyuntikkan direktori itu ke
  prompt delegated.
- **`docs/architecture/README.md`** — diagram alur yang diverifikasi terhadap source: jalur
  delegated (job → worker → Executor → Router → context → PromptBuilder → adapter → redaksi →
  contract check → digest → persist), jalur samping, dan tabel storage.
- **`docs/team-guide/`** — panduan adopsi tim untuk developer: `getting-started`,
  `when-to-use` (kapan workflow, kapan agent langsung; benefit vs cost termasuk latency),
  `task-framing` (permintaan prosedural vs berbasis tujuan; yang berubah adalah siapa yang
  melakukan dekomposisi dan penjelajahan repo), `examples` (satu task, dua mode),
  `troubleshooting` (termasuk mengukur latency lewat `main.py --command report`).
- **`docs/README.md`** — index dan kontrak tiga lapis: team guide = how/when, research = why,
  reference = perilaku teknis persis dan otoritatif. Ringkasannya juga di root `CLAUDE.md`.
- **Catatan riset pertama** — `CASE-001` (tim tetap memecah task secara prosedural),
  `CASE-002` (keluhan latency), `H-001` (kompleksitas task memprediksi apakah delegasi
  sepadan dengan latency-nya; `proposed`, belum ada perubahan runtime). Disanitasi, bukti
  anekdotal, outcome `inconclusive`.
- README: estimasi latency diubah dari "tens of seconds to a few minutes" menjadi "from under
  a minute to several minutes for broad analysis or planning"; enam call delegated pada sesi
  maintainer 2026-09-26 memakan 238–488 detik.
- **Research log 2026-09-26 dipecah menjadi record**: `CASE-003` (refactor besar; primary
  agent mengoreksi klaim secondary agent yang berlebihan; eksternalisasi memindahkan, bukan
  menghapus, komputasi), `SYN-001` (procedural prompting sebagai pengelolaan
  ketidakpastian, adoption loop), `H-002` (delegasi tingkat tujuan mengurangi beban
  orkestrasi manusia), `H-003` (explore-first memitigasi drift iterasi lokal), `EXP-001`
  (desain 2×2 workflow × strategi prompt, `planned`), `docs/research/questions.md` (RQ-01–09
  dengan status apa yang sudah ada), dan ringkasan `docs/research/logs/2026-09-26.md`. Data
  pribadi tidak dicatat; identitas dan angka repo privat diganti skala.
- **`SYN-002`** — autonomy dialokasikan per tanggung jawab (explore, decompose, decide,
  execute, verify, remember, learn); tabel alokasi saat ini diturunkan dari kode. Koreksi:
  verifikasi delegated dijalankan secondary agent yang dikonfigurasi (sama dengan yang
  melakukan explore kecuali provider diganti), runtime hanya memeriksa bentuk verdict — tidak
  independen. README tabel command diperbarui. RQ baru:
  RQ-10 (alokasi tanggung jawab), RQ-11 (verifier independen); RQ-05/06/07a/08 dipertajam
  (latency sebagai kerja paralel, knowledge yang dioperasionalkan, trajectory data dengan
  batas privasi, memory vs experience). `evaluation/README.md`: target biaya manusia total
  minimum untuk hasil yang dapat diterima.
- `methodology.md`: tingkat evidence (creator, team, controlled, independent) dan delapan
  prinsip. `CONTRACT.md` §12: empat pertanyaan wajib sebelum menambah mekanisme baru.
  `evaluation/README.md`: fungsi net value (konseptual, belum terukur).
- Koreksi saat pencatatan: job asinkron, runner di background, dan knowledge persisten sudah
  ada di runtime — dicatat sebagai *partial*, bukan kapabilitas masa depan. Team guide: tiga
  mode (direct, assisted, autonomous — yang terakhir belum tersedia).
- **Gambar arsitektur README baru: `docs/assets/architecture.png`**, digambar dari diagram
  ASCII yang diverifikasi terhadap source (developer, primary agent, runtime, secondary agent
  dengan evidence boundary, `.workflow/data/`). Diagram ASCII itu menjadi overview teks di
  `docs/architecture/README.md`. `docs/assets/flow.png` dihapus: memuat "low-cost", "never
  writes", dan "promoted after 3 validations" (nilai sebenarnya 5 sesi). Catatan: gambar baru
  menulis primary agent "makes final decision"; caption README meluruskan bahwa developer
  yang menjawab open questions dan menyetujui implementasi.
- **`prompt/README.md`** — menjelaskan bahwa `prompt/` adalah arsip catatan rilis dan snapshot
  prompt, dan path modul di dalamnya milik versi masing-masing.

## Dipindah

- Section "Batasan yang diketahui" di `docs/reference.md` → `docs/limitations.md`.
- Section "Benchmark" di `docs/reference.md` → `docs/evaluation/benchmark.md`.
- Telemetry "Measured" di `README.md` → `docs/evaluation/observed-usage.md`.

Heading lama di `docs/reference.md` dipertahankan sebagai penunjuk, jadi anchor
`#batasan-yang-diketahui` tetap berfungsi.

## README ditulis ulang

Struktur baru: hero, gambar arsitektur, what-is, pembagian peran, kapan berguna, install dan
quick start, security dan requirements, riset dan evaluasi, dokumentasi, batasan, lisensi.
Klaim yang diperketat:

- "cheaper agent" tidak lagi menjadi definisi; benchmark hanya mengukur konteks/token.
- "strictly read-only" dan "writing code is never delegated" menjadi peran dan desain; tingkat
  penegakannya dinyatakan per provider.
- "under ~50 files" dihapus; heuristik dinyatakan sebagai heuristik.
- `promote-*` diganti tiga nama command sebenarnya; `/.execute` disebut sebagai skill main
  agent, bukan command runtime.
- Angka telemetry keluar dari README. Di `observed-usage.md` unitnya dipisah: 107 delegated
  call versus 112 usage row; mix per command berjumlah 112.

## Koreksi dokumentasi usang

- `docs/reference.md`: klaim bahwa `stamp_version.py` menstempel `bench/BENCHMARK-PLAN.md`
  salah — tool itu sengaja mengecualikan `bench/`. Diperbaiki di `docs/evaluation/benchmark.md`.
- `docs/reference.md`: tautan `main.py` di Referensi diperbaiki (`../main.py`).
- `docs/runtime-contracts.md`: intro menyebut `core/workflow_runtime.py` yang sudah dihapus;
  kini menunjuk `core/runtime/config_defaults.py`.
- `bench/BENCHMARK-PLAN.md`, `bench/STATE.md`: catatan bahwa path source di dalamnya merujuk
  tag SUT `v3.4.5`. Isi tidak diubah.
- `CHANGELOG.md`: baris 3.7.1 yang hilang ditambahkan.
- `tools/maintain/stamp_version.py`: entri `EXEMPT` untuk kalimat sejarah "Sejak v3.7.1 posisi
  ini" di `docs/reference.md`. Selama `TOOL_VERSION` masih 3.7.1 baris itu lolos karena sama
  dengan versi berjalan; bump berikutnya mana pun membuat `--check` gagal. Hanya data, logika
  tool tak berubah.

## Tidak termasuk (ditunda)

- Komentar kode yang merujuk path lama (`core/workflow_runtime.py`, `core/fact_store.py`,
  `core/graph_index.py`) belum dibersihkan.
- Catatan literature, rekonstruksi keputusan historis.
