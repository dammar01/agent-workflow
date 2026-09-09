# Benchmark 3-Arm: Ekonomi Quality-Adjusted agent-workflow

**Status:** rencana disetujui, belum dieksekusi
**Dibuat:** 2026-08-15
**Versi SUT:** agent-workflow v3.4.5 (`config/settings.py:20`)
**Dokumen ini self-contained.** Sesi baru cukup membaca file ini — tidak perlu riwayat percakapan sebelumnya.

---

## 1. Pertanyaan yang dijawab

Apakah "Claude main + worker murah" lebih murah **per task yang diterima**, bukan per panggilan, setelah kualitas diperhitungkan?

Tiga topologi kerja dibandingkan:

| Arm | Topologi |
|-----|----------|
| **A** | Claude langsung. Tanpa subagent, tanpa agent-workflow. |
| **B** | Claude + native subagent (Task tool). |
| **C** | Claude main + worker murah via agent-workflow (opencode `deepseek-v4-flash-free`). |

Metrik target:

1. Biaya per task yang diterima
2. Premium-context token yang dihindari
3. First-pass correctness
4. Waktu sampai PR diterima
5. Jumlah rework
6. Test/security pass rate
7. Persentase task yang akhirnya harus diulang Claude utama

---

## 2. Keputusan yang sudah dikunci

Jangan buka ulang tanpa alasan baru.

| Keputusan | Nilai |
|-----------|-------|
| Worker arm C | opencode `deepseek-v4-flash-free` (gratis) |
| Kriteria "PR diterima" | Oracle otomatis saja, tanpa review manusia |
| Sumber corpus task | Revert commit historis repo ini |
| SUT (subjek uji) | Repo agent-workflow ini sendiri |
| Arsitektur harness | Black-box; nol perubahan source runtime yang diukur |
| Tulang punggung biaya | `tokenburn` 0.2.0 (setelah prasyarat P0 di bawah) |

---

## 3. Evidence dasar (sudah terverifikasi)

### 3.1 Runtime agent-workflow

| Fakta | Anchor |
|-------|--------|
| Telemetri per call ditulis ke `logs/<prompt_id>/call.meta.json` | `core/executor.py:801-827`, `core/runtime_io.py:216-240` |
| Token cuma estimasi `chars//4`, `token_source="estimated"` | `core/executor.py:786-813` |
| Artifact per run: `prompt.md`, `prompt.sha256`, `output.raw.md`, `call.meta.json` | `core/runtime_io.py:37-44,169-180` |
| Job record `storage/jobs/job_<id>.json`: `created_at`/`started_at`/`completed_at` | `core/job_manager.py:191,404,419` |
| Wall-clock `await` TIDAK dipersist | `core/job_lifecycle.py:389` |
| Evidence reuse `find_fresh()`, flag `evidence_ref.reused` | `core/evidence_store.py:325-349`, `core/executor.py:469-476` |
| 16 command CLI; `BACKGROUND_COMMANDS = {explore,plan,analyze,verify}` auto-submit | `main.py:388-405`, `main.py:51` |
| Adapter seragam `run(prompt, session, model=None, work_dir=None) -> dict` | `adapters/opencode_adapter.py:488`, `codex_adapter.py:244`, `agy_adapter.py:302` |
| Lock 1 worker per `session_id`, TTL 300s | `core/workspace_paths.py:36` |
| Cap worker global default 6 | `config/settings.py:155` (`AI_PROXY_MAX_GLOBAL_WORKERS`) |
| `auto_verify_after_execute=False`, prompt-only, nol penegakan Python | `core/workflow_runtime.py:114-129` |
| Nol data harga di repo | grep price/pricing/cost_per/usd = nol match; studi memakai token saja |
| Nol modul agregasi metrik lintas run | dikonfirmasi eksplisit |
| Harness test tunggal, pass/fail biner, tanpa report file | `tests/scenario.py:1016` |
| `.workflow/run.sh` digenerate dinamis, OS-gated POSIX, tidak di-ship | — |

### 3.2 tokenburn

CLI: `tokenburn` 0.2.0, DB `C:\Users\damma\.tokenburn\tokenburn.db`, mode `subscription`, plan $100/bulan.

Subcommand relevan:

```
tokenburn import --since <7d>        # impor log lokal Claude Code
tokenburn report --last 1d --by session --json
tokenburn tree --session <id> --json # pohon biaya agent (parent/child)
tokenburn scan --last 7d --json      # 30 rule pemborosan
tokenburn db export                  # CSV semua record
tokenburn db path
```

Kolom CSV `db export`:

```
id,timestamp,provider,model,source,inputTokens,outputTokens,
cacheReadTokens,cacheWriteTokens,costUSD,durationMs,promptHash,toolUse,stopReason
```

**Temuan lama — `claude-opus-5` berharga NOL.** Dicatat di sini sebagai riwayat, bukan
sebagai gerbang: sejak studi ini beralih ke metrik token murni, kolom `costUSD` tidak
dibaca sama sekali dan harga yang salah tidak lagi memblokir apa pun. Kolom token
(`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`) yang dipakai, dan
kolom itu benar meski harganya nol.

| model | rows | cost |
|-------|------|------|
| claude-opus-4-6 | 6.742 | $4.677,03 |
| claude-opus-4-7 | 3.593 | $2.383,39 |
| claude-opus-4-8 | 2.097 | $795,85 |
| claude-sonnet-4-6 | 10.605 | $554,24 |
| claude-haiku-4-5 | 20.282 | $154,31 |
| claude-opus-4-5 | 201 | $51,63 |
| claude-sonnet-4-5 | 363 | $18,08 |
| **claude-opus-5** | **16.972** | **$0,00** |

Token opus-5 yang tak berharga: input 60.887, output 18.858.192, cacheRead 2.925.645.409, cacheWrite 87.824.151.

Model tarif tervalidasi: menghitung ulang opus-4-6 dengan $15 / $75 / $1,50 / $18,75 per juta token (in / out / cacheRead / cacheWrite) menghasilkan **$4677,03**, identik dengan nilai DB. Tarif yang sama diterapkan ke opus-5 memberi **$7.450,45** yang hilang. Total DB $8.634,52 seharusnya ≈ **$16.084,97** — kurang-lapor 46%.

Komposisi biaya opus-5: cacheRead 59%, cacheWrite 22%, output 19%, input segar 0,01%.

**Konsekuensi desain:** "premium-context token yang dihindari" diukur pada **cacheRead + cacheWrite**, bukan input segar. Input segar adalah suku yang nyaris nol.

---

## 4. Prasyarat P0 — WAJIB sebelum run apa pun

### P0.1 Tambal harga `claude-opus-5`

Tanpa ini arm A dan B terbaca $0 dan benchmark tidak berarti apa-apa.

```bash
tokenburn config show          # cari lokasi config
cat "$(dirname "$(tokenburn db path)")/config.yaml"
```

Cari tabel harga di paket tokenburn (`/c/nvm4w/nodejs/tokenburn` → resolve ke direktori paket npm-nya). Tambahkan entri `claude-opus-5` dengan tarif Opus: input $15/M, output $75/M, cacheRead $1,50/M, cacheWrite $18,75/M.

**Verifikasi wajib** — hitung ulang satu sesi opus-5 yang diketahui dan bandingkan dengan hitungan manual:

```bash
tokenburn db export | awk -F, 'NR>1 && $4=="claude-opus-5"{i+=$6;o+=$7;cr+=$8;cw+=$9;c+=$10} \
  END{printf "reported=%.2f expected=%.2f\n", c, i*15/1e6+o*75/1e6+cr*1.5/1e6+cw*18.75/1e6}'
```

Lolos bila kedua angka cocok. Kalau tarif opus-5 resmi berbeda dari asumsi Opus 4.x, catat tarif sebenarnya dan ulangi.

### P0.2 Verifikasi pemetaan unit → sessionId

Satu unit eksperimen harus jadi satu `sessionId` terpisah di log Claude Code, kalau tidak biaya antar unit bercampur.

Uji: jalankan dua sesi Claude pendek terpisah, lalu

```bash
tokenburn import --since 1d --json
tokenburn report --last 1d --by session --json
```

Lolos bila dua `sessionId` berbeda muncul dengan biaya terpisah.

### P0.3 Uji `tokenburn proxy` untuk worker (opsional)

Kalau `tokenburn proxy` bisa menangkap traffic opencode, biaya worker naik kelas dari estimasi ke terukur. Kalau tidak, tetap pakai `call.meta.json`. Bukan blocker — worker gratis, error estimasinya hampir tak menggeser kesimpulan.

---

## 5. Struktur harness

Ditaruh di `bench/`, di luar path yang diukur:

```
bench/
  BENCHMARK-PLAN.md   # dokumen ini
  STATE.md            # progres eksekusi, diperbarui tiap fase selesai
  policy.py           # batas run: waktu, retry, budget, karantina (§7b)
  corpus.py           # generator kandidat task dari git log
  corpus.json         # daftar task, prompt dan oracle_tests diisi tangan
  driver.py           # satu unit: prepare/delegate/judge/finish/teardown
  oracle.py           # verdict mesin, dibekukan sebelum unit pertama
  test_oracle.py      # pengunci pemetaan verdict + policy, di luar tests/run.py
  collect.py          # unit records + tokenburn + call.meta.json -> ledger.jsonl
  aggregate.py        # ledger -> tabel per arm
  ledger.jsonl        # satu baris per unit
  units/              # satu record per unit, sumber ledger
  raw/                # ekspor tokenburn mentah per batch
  worktrees/          # git worktree per unit, dibuang setelah selesai
  .gitignore          # units/, raw/, worktrees/ tidak dilacak
```

`units/`, `raw/`, dan `worktrees/` adalah scratch dan tidak dilacak git. `corpus.json` dan
`ledger.jsonl` dilacak: yang pertama definisi studi, yang kedua datanya.

`bench/` wajib di luar scope task apa pun, supaya agen yang diuji tidak menyentuh instrumennya sendiri.

---

## 6. Fase eksekusi

### Fase 1 — Bangun corpus (target 15 task)

Kriteria pilih commit: menyentuh 1-3 file, ada test yang menutupinya, pesan commit jelas, bukan merge, bukan format-only.

```bash
git log --oneline --no-merges -n 200 --pretty=format:'%h|%s|%ad' --date=short
git show --stat <sha>
```

Per task simpan ke `corpus.json`:

```json
{
  "task_id": "T01",
  "base_sha": "<sha>^",
  "answer_sha": "<sha>",
  "prompt": "<deskripsi masalah, TANPA solusi>",
  "files_expected": ["core/foo.py"],
  "difficulty": "easy|medium|hard",
  "oracle_tests": ["tests/run.py --only jobs"]
}
```

Distribusi: 5 easy (satu file, satu fungsi), 5 medium (lintas modul), 5 hard (menyentuh `core/executor.py`, `core/job_manager.py`, atau `adapters/`). Label kesulitan dari ukuran diff asli dan jumlah file, **ditetapkan sebelum run**.

**Jebakan kebocoran jawaban.** Worktree wajib dipotong tepat di `<sha>^`. Verifikasi tiap worktree tidak memuat sha jawaban:

```bash
git -C bench/worktrees/<unit> log --oneline | grep -q <answer_sha> && echo "BOCOR"
```

Diff asli disimpan hanya untuk kalibrasi manusia, **tidak** dipakai oracle.

### Fase 2 — Driver per unit

Semua arm menerima prompt identik, timeout identik, worktree bersih identik. Urutan task diacak dan di-counterbalance antar arm.

```bash
git worktree add bench/worktrees/<task>_<arm>_<rep> <base_sha>
```

- **Arm A** — sesi Claude, subagent dimatikan, `.workflow` tidak dipasang di worktree.
- **Arm B** — sesi Claude, subagent native diizinkan, `.workflow` tidak dipasang.
- **Arm C** — sesi Claude + `.workflow` terpasang, worker opencode. `bench/driver.py prepare`
  memasang `.workflow` di worktree; tanpa itu panggilan terdelegasi pertama mati di
  `.workflow/config.json` dalam dua detik dan unitnya bukan arm C sama sekali. Panggil
  `main.py` langsung, lewati `run.ps1` (runner POSIX digenerate dinamis dan tidak di-ship;
  panggilan langsung menyeragamkan lintas OS):

```bash
python main.py --command await --job-command explore --prompt "<task>"   --session <unit_session_id> --work-dir <worktree> --poll-timeout <sisa detik unit>
```

`await`, bukan `--command explore`. explore/plan/analyze/verify ada di `BACKGROUND_COMMANDS`
(`main.py:51`): memanggilnya langsung mengirim job lalu kembali seketika dengan
`{ok, status, job_id}` — nol `evidence_ref`, nol isi, dan `ok` yang menggambarkan
pengiriman alih-alih pekerjaan. `driver.py delegate` membungkus ini.

Isolasi: `session_id` unik per unit. Paralel maksimum 6; naikkan `AI_PROXY_MAX_GLOBAL_WORKERS` bila mau lebih.

Stempel waktu dicatat harness, bukan diambil dari DB (`durationMs` banyak bernilai 0).

### Fase 3 — Oracle (dibekukan sebelum run pertama)

Urutan, berhenti di kegagalan pertama:

1. **Sintaks** — `core/quick_verify.py`. Gagal = hard fail.
2. **Test suite** — `python tests/run.py`, pass/fail biner.
3. **Test spesifik task** — test yang menutupi commit itu.
4. **Checks** — modul di `tests/checks/` sebagai gerbang kontrak/keamanan.

`verdict = accepted` hanya bila keempat lolos. `not_checked` dan `skipped` **bukan** pass — hitung sebagai `incomplete`, artinya tidak diterima.

### Fase 4 — Panen data

```bash
tokenburn import --since 1d --json
tokenburn report --last 1d --by session --json > bench/raw/report_<batch>.json
tokenburn tree --session <unit_session_id> --json > bench/raw/tree_<unit>.json
tokenburn scan --last 1d --json > bench/raw/scan_<batch>.json
tokenburn db export --since <batch_start_ms> > bench/raw/export_<batch>.csv
```

Sisi worker arm C dari `.workflow/sessions/<sid>/logs/<prompt_id>/call.meta.json` saja.
`storage/jobs/job_<id>.json` **tidak** dipanen: isinya catatan siklus hidup — `worker_pid`,
`worker_identity`, `status`, `error` — dan nol di antaranya ada di skema §7. Yang dulu
diharapkan darinya (alasan kegagalan) kini datang langsung di balasan `await` yang
`driver.py delegate` tunggu.

Nama direktori sesi bukan `session_id` apa adanya: runtime melewatkannya ke
`safe_path_component` (`core/workspace_paths.py:99`), jadi `bench_T01_C_1` menjadi
`bench_T01_C_1--<hash12>`. `collect.py` menerapkan fungsi yang sama alih-alih menebak.

### Fase 5 — Agregasi dan analisis

Metrik utama: **biaya per task diterima** = total biaya seluruh percobaan ÷ jumlah task diterima. Percobaan gagal tetap masuk pembilang. Itulah yang membuat angka quality-adjusted, bukan biaya per panggilan.

Uji berpasangan per task, bukan antar grup. Laporkan selisih per pasang dengan selang kepercayaan bootstrap. **Jangan** laporkan satu p-value untuk 15 task — sampelnya terlalu kecil; ukuran efek dan selang lebih jujur.

---

## 7. Skema ledger

Satu baris JSONL per unit:

```
task_id, arm, provider, test_tier, repeat, base_sha, session_id, worktree,
t_start, t_first_submit, t_accepted, t_end,
premium_cache_read_tokens, premium_cache_write_tokens,
premium_output_tokens, premium_input_tokens, premium_total_tokens,
worker_input_tokens, worker_output_tokens, worker_token_source,
delegated_calls, evidence_reused_hits,
first_pass_accepted, rework_cycles,
oracle_stage_failed, verdict, stages_passed, stages_run,
main_agent_rewrote, files_touched,
scan_findings,
unit_seconds, timed_out, quarantined_suites, over_unit_budget,
message_turns, turns_to_first_edit,
context_input_tokens, context_peak_tokens, context_sidechain_tokens,
transcript_source
```

`unit_seconds`, `timed_out`, `quarantined_suites`, dan `over_unit_budget` ditambahkan
2026-08-20 bersama `bench/policy.py`. Masing-masing menjawab pertanyaan yang tak bisa
dijawab dari daftar asli: berapa lama unit berjalan sama sekali, apakah ia lewat cap waktu,
apakah ia dinilai dengan gerbang yang dikurangi, dan apakah pemakaian token premium-nya
lewat plafon per unit.

`provider` dan `test_tier` adalah dua dimensi desain, dibawa per baris, bukan diurai ulang
dari `unit_id`. Kunci pengelompokan yang harus dipulihkan dengan memotong string cuma
berjarak satu rename dari diam-diam menggabungkan semuanya jadi satu grup.

`stages_passed`/`stages_run` adalah test pass rate berikut penyebutnya. `stages_run`
menghitung stage yang benar-benar jalan, jadi unit yang berhenti di stage 1 terbaca 0/1,
bukan 25% dari gerbang empat-stage yang tak pernah ia capai.

Enam field terakhir berasal dari transcript Claude unit itu. Tokenburn melaporkan apa yang
dihabiskan sesi; hanya transcript yang tahu berapa kali seorang manusia harus bertanya, dan
berapa besar context yang dibawa tiap giliran.

**Nol kolom mata uang.** Studi ini mengukur token. Paket langganan tidak menagih per token,
jadi kolom USD hanya bisa jadi angka setara-API yang berpakaian biaya, dan setiap
perbandingan yang bersandar padanya mewarisi fiksi itu. `costUSD` di export tokenburn
sengaja dilewati, bukan disimpan di kolom yang tak boleh dikutip siapa pun.

Catatan turunan:

- `premium_*` dari tokenburn per `sessionId`. `premium_total_tokens` = input + output +
  cache read + cache write; cache tidak dikurangkan, sebab giliran yang membaca 200k token
  cache tetap membawa 200k token context.
- `worker_*` dari `call.meta.json`, `token_source` dari runtime (`provider` atau
  `estimated`/`chars//4`). Token premium dan token worker adalah dua sumber daya berbeda
  dan **tidak pernah** dijumlahkan jadi satu angka.
- `message_turns` = record `type=user` yang benar-benar prompt manusia. Tool result,
  `<task-notification>`, dan injeksi `isMeta` juga datang di peran user; menghitungnya
  mengubah "berapa kali manusia bertanya" jadi "berapa banyak tool yang dipanggil model".
- `turns_to_first_edit` = giliran manusia sebelum model pertama kali menyentuh file.
  `null` bila tak pernah — bukan `0`, yang justru terbaca "langsung mengedit".
- `context_sidechain_tokens` dipisah dari `context_input_tokens`: subagent native adalah
  arm B, jadi melebur keduanya menyembunyikan biaya arm B.
- `evidence_reused_hits` dari `evidence_ref.reused` — proxy langsung token premium yang dihindari.
- `main_agent_rewrote` distempel harness per fase, **tidak** ditebak dari isi diff. Ini yang menjawab "persen task yang akhirnya diulang Claude utama".
- `rework_cycles` punya dua sumber: stempel harness dan `promptHash` berulang di CSV tokenburn. Silang-cek keduanya.
- `quarantined_suites` kosong berarti unit dinilai dengan gerbang penuh. Tak kosong berarti
  stage 2 melewati suite yang disebut, dan baris itu tidak sebanding dengan baris bergerbang
  penuh — laporkan per baris, jangan diringkas jadi catatan kaki.

## 7c. Provider sebagai dimensi

Provider **bukan** arm keempat dan kelima. Arm adalah topologi — siapa yang mengerjakan —
sedangkan provider adalah siapa second agent itu. Meratakannya jadi `A, B, C-opencode,
C-codex` merusak perbandingan berpasangan A-lawan-C, karena tak ada lagi satu C tunggal
untuk dipasangkan dengan A.

| Arm | Provider yang sah | Alasan |
|-----|-------------------|--------|
| A | `claude` | nol `.workflow`, nol second agent |
| B | `claude` | subagent native, tetap nol second agent |
| C | `opencode`, `codex` | `.workflow` terpasang, provider dipin per unit |

`agy` terdaftar di runtime tapi **tidak** masuk matrix: adapter-nya tak memancarkan
`provider_usage`, jadi token worker-nya cuma estimasi `chars//4` dan barisnya tak sebanding
dengan baris yang terukur.

Identitas unit sekarang `(task, arm, provider, repeat)` — tercermin di `unit_id`, nama
worktree, dan `session_id`. `driver.py prepare --provider` menolak pasangan yang tak sah,
lalu menulis provider itu ke `second_agent.json` milik worktree setelah `init`. Ditulis
sesudah `init`, bukan diteruskan ke dalamnya: `init` menyemai file itu dari environment
operator, dan unit yang mewarisinya akan berlabel satu provider tapi berjalan di provider
lain tanpa ada yang menyadarinya.

Pin **membangun ulang seluruh kunci milik provider**, bukan cuma dua field yang menyebut
namanya: `provider`, `provider_command`, `provider_agent`, `default_model`, `effort`, dan
`routes`. Alasannya di `core/prompt/router.py` — `routes[<command>].model` menang atas
`default_model`, jadi worktree yang disemai untuk opencode lalu dipin ke codex akan tetap
mengirim nama model opencode di tiap panggilan terdelegasi, berlabel codex di ledger.
Nilai netral diambil dari `config.settings.default_provider_config`, yang menulis
`default_model: None` sehingga default milik provider itu sendiri yang berlaku — bukan
tebakan nama model di sini. Kunci provider-netral (timeout, poll interval, jumlah probe)
dibiarkan seperti tulisan `init`.

## 7d. Tier test

Tiap entri corpus menyatakan tepat satu `test_tier`: `unit`, `integration`, atau `e2e`.
Dikosongkan generator dengan alasan yang sama seperti `prompt` — apakah sebuah task dinilai
di level unit, integration, atau end-to-end adalah keputusan tentang task itu APA, dan
menebaknya dari file yang disentuh commit akan membuat corpus diam-diam menilai semua task
di level yang paling murah dideteksi.

Oracle menolak entri tanpa tier. Tier yang tak dinyatakan tidak di-default ke yang termurah:
task yang dinilai di level yang tak seorang pun pilih adalah task yang verdict-nya tak bisa
dibandingkan dengan task mana pun.

Tier `e2e` menjalankan `tools/e2e/e2e.py` sebagai tambahan stage 2 — **tanpa** `--full`.
Flag itu membuat panggilan terdelegasi sungguhan, yang menghabiskan kuota provider yang
justru sedang diukur studi ini dan menjadikan oracle peserta di arm yang ia nilai.

## 7b. Batas run

Terkumpul di `bench/policy.py`, satu tempat supaya mengubahnya jadi edit yang terlihat.

| Batas | Nilai | Ditegakkan |
|-------|-------|-----------|
| Waktu per unit | 1800 s | live, `driver.py` |
| Timeout per stage oracle | 900 s | live, `oracle.py` |
| `rework_cycles` maksimum | 3 | live, `driver.py finish` menolak di atasnya |
| Panggilan terdelegasi per unit | 12 | live, `driver.py delegate` |
| Budget token premium per unit | 5.000.000 | **pasca-fakta**, dilaporkan `collect.py` |
| Budget token premium per run | 400.000.000 | **pasca-fakta**, dilaporkan `collect.py` |
| Suite dikarantina | kosong | dibaca `oracle.py` saat stage 2 |

Budget dihitung dalam token premium saja. Token worker sengaja di luar gerbang: itu sumber
daya murah yang justru dibelanjakan desain ini untuk menghemat yang mahal, jadi
menghitungnya di sini akan menghukum arm C karena melakukan hal yang sedang diuji.

Budget tidak bisa ditegakkan live: hitungan token premium datang dari tokenburn setelah run
selesai, jadi tak ada yang bisa memotong di tengah jalan. Menyebutnya penegakan akan menjadi
klaim palsu tentang apa yang harness bisa lihat.

Nilai budget dan cap waktu adalah keputusan operator, bukan turunan dari pengukuran — nol
unit pernah dipanen waktu angka-angka itu ditulis. Tinjau ulang setelah batch pertama, dan
sebutkan di laporan bila direvisi.

---

## 8. Peta metrik → sumber

| Metrik | Rumus | Sumber |
|--------|-------|--------|
| Token premium per task diterima | `premium_total_tokens` ÷ jumlah diterima | tokenburn |
| Token worker per task diterima | `worker_input+output` ÷ jumlah diterima | `call.meta.json` |
| Giliran pesan sampai eksekusi | `message_turns`, `turns_to_first_edit` | transcript Claude |
| Context per task | `context_input_tokens` (jumlah), `context_peak_tokens` (puncak) | transcript Claude |
| Premium-context token dihindari | `cacheRead+cacheWrite` arm A − arm C, per task sama | tokenburn `db export` |
| First-pass correctness | proporsi `first_pass_accepted` | oracle |
| Waktu sampai diterima | `t_accepted − t_start` | jam harness |
| Jumlah rework | rerata `rework_cycles` | harness + `promptHash` |
| Test/security pass rate | proporsi lolos tahap 2 dan 4 | oracle |
| % diulang Claude utama | proporsi `main_agent_rewrote` (arm C) | harness |

Metrik kualitas sekunder gratis dari `tokenburn scan`: `duplicate-requests`, `context-explosion`, `large-file-reread`, `low-cache-hit`, `deep-agent-tree`, `read-heavy`, `retry-storm`.

---

## 9. Matriks run

15 task × 3 arm × 3 ulangan = **135 run**. Perkiraan 5-20 menit per run. Paralel 6 worker: sekitar 4-8 jam per arm.

Naikkan jumlah ulangan bila variansi antar-ulangan melebihi selisih antar-arm.

---

## 10. Ancaman validitas — sebutkan di laporan akhir

1. **Tarif opus-5 diasumsikan** setara Opus 4.x. Kalau meleset, semua angka premium bergeser seragam; peringkat antar-arm tidak berubah karena ketiga arm memakai model main yang sama.
2. **Token worker estimasi** `chars//4` (`core/executor.py:786-813`), error sekitar ±20-30%. Bias jatuh di sisi murah.
3. ~~**Bug continuation**~~ — **sudah diperbaiki 2026-08-15** sebelum benchmark dimulai (`_merge_continuation()` di `core/executor.py`). Sebelumnya 3 dari 4 panggilan delegated kehilangan body evidence dan itu akan menaikkan rework arm C secara artifisial. Run apa pun yang dikumpulkan sebelum tanggal ini tidak sah untuk arm C.
4. **`auto_verify_after_execute=False` prompt-only** (`core/workflow_runtime.py:114-129`). Harness menjalankan verify sendiri; jangan percaya klaim agen bahwa pekerjaan selesai.
5. **Kontaminasi SUT** — instrumen dan subjek berada di repo yang sama. Worktree dipotong di `<sha>^` dan `bench/` di luar scope task.
6. **Efek belajar** — task yang sama dilihat berkali-kali. Counterbalance dan worktree bersih mengurangi, tidak menghapus.
7. **Nol klaim biaya** — studi ini melaporkan token, bukan uang. Boleh dikatakan "arm C memakai 80% lebih sedikit token premium"; **tidak** boleh dikatakan "arm C lebih murah" tanpa tabel harga, dan tak ada tabel harga di repo ini.

---

## 11. Protokol untuk sesi baru

Sesi Claude baru yang mengambil alih pekerjaan ini:

1. Baca `bench/BENCHMARK-PLAN.md` (file ini) dan `bench/STATE.md`.
2. Cek gerbang P0 sudah lolos: jalankan perintah verifikasi di §4.1. Kalau `reported` masih 0 untuk opus-5, **berhenti** dan kerjakan P0 lebih dulu.
3. Lanjutkan dari fase pertama yang belum bertanda selesai di `STATE.md`.
4. Setelah tiap fase selesai, perbarui `STATE.md`: fase, tanggal, apa yang dihasilkan, apa yang diketahui rusak.
5. Keputusan di §2 sudah dikunci. Jangan buka ulang tanpa alasan baru; kalau dibuka, catat alasannya di `STATE.md`.

Perintah pemeriksaan cepat:

```bash
tokenburn db export | awk -F, 'NR>1 && $4=="claude-opus-5"{t+=$6+$7} END{print "opus5_tokens="t}'   # harus > 0
ls bench/corpus.json bench/ledger.jsonl 2>/dev/null                                            # progres fase 1 dan 4
git worktree list                                                                              # worktree tersisa
```
