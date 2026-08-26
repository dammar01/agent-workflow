# STATE — Benchmark 3-Arm

Rencana: `bench/BENCHMARK-PLAN.md`. Perbarui file ini setiap fase selesai.

## Progres

| Fase | Status | Tanggal | Keluaran |
|------|--------|---------|----------|
| P0.1 Tambal harga `claude-opus-5` | BELUM (blocker eksternal) | — | — |
| P0.2 Verifikasi unit → sessionId | BELUM | — | — |
| P0.3 Uji `tokenburn proxy` (opsional) | BELUM | — | — |
| 1. Bangun corpus 15 task | SEBAGIAN | 2026-08-15 | `bench/corpus.py` (generator jalan; `corpus.json` belum ditulis) |
| 2. Driver per unit | SEBAGIAN | 2026-08-20 | `bench/driver.py`, `bench/collect.py`. Satu unit arm C dijalankan penuh (delegate nyata 68 s, worker token terpanen); sesi agen tetap kerja operator |
| 3. Oracle dibekukan | SELESAI (dibuka sekali 2026-08-20, verdict `security_violation`) | 2026-08-15 | `bench/oracle.py`, `bench/test_oracle.py` |
| 4. Panen data | SEBAGIAN | 2026-08-20 | `bench/collect.py` jadi; `bench/ledger.jsonl` belum ada data (P0.1) |
| 5. Agregasi dan analisis | SELESAI | 2026-08-15 | `bench/aggregate.py` |

Catatan status di atas sengaja tidak menulis SELESAI untuk Fase 1: generatornya ada dan
terbukti jalan, tetapi `prompt` dan `oracle_tests` sengaja dibiarkan kosong. Prompt yang
digenerate dari pesan commit ditulis oleh sesuatu yang sudah melihat jawabannya — itu satu
hal yang corpus tidak boleh mengandungnya. Fase 1 selesai ketika 15 entri sudah diisi
tangan dan dikunci.

Fase 2 dipecah setelah keputusan 2026-08-20, dan pembagiannya lebih tajam dari yang
dikira waktu keputusan diambil. `BENCHMARK-PLAN.md:223` mendefinisikan arm C sebagai sesi
Claude **plus** `.workflow`; mengotomatiskan `main.py` saja adalah sisi worker tanpa main
agent di atasnya, bukan arm C. Jadi `bench/driver.py` memiliki bagian yang benar-benar bisa
diulang mesin — worktree di base_sha, cek kebocoran, session id per unit, stempel jam,
oracle, teardown — dan berhenti di situ:

| Fase unit | Pemilik | Isi |
|-----------|---------|-----|
| `prepare` | mesin | worktree, cek kebocoran, session id, `t_start` |
| sesi agen | operator | sesi Claude di dalam worktree (ketiga arm) |
| `delegate` | mesin | opsional, arm C: satu panggilan `main.py` terdelegasi |
| `judge` | mesin | verdict oracle, `t_accepted`, `files_touched` |
| `finish` | operator | `rework_cycles`, `main_agent_rewrote`, `t_end` |
| `teardown` | mesin | worktree dibuang |

Ini bukan driver setengah jadi yang menyamar siap: batas mesin/operator ditulis di tabel itu
supaya jelas kolom mana yang terukur dan kolom mana yang distempel tangan. Variansi operator
menyentuh ketiga arm, bukan cuma A dan B — konsekuensinya masuk §10 Ancaman validitas.

`bench/collect.py` menolak menulis baris tanpa biaya premium kecuali diminta
(`--allow-missing-cost`). `aggregate._spend` memaksa nilai hilang jadi `0.0`, dan arm yang
ekspor tokenburn-nya tak pernah datang akan terbaca sebagai arm termurah dalam studi.

Batas run ada di `bench/policy.py` (§7b rencana). Waktu, retry, dan jumlah panggilan
terdelegasi ditegakkan live oleh `driver.py`; budget cuma dilaporkan `collect.py` sesudahnya,
karena biaya datang dari tokenburn setelah run selesai. Karantina flaky kosong: empat run
hijau berturut pada 2026-08-20 bukan bukti stabil, cuma ketiadaan bukti tak-stabil, dan nol
suite dikarantina atas dasar curiga.

## Gerbang aktif

**P0.1 memblokir semua run.** Selama `claude-opus-5` berharga $0 di tokenburn, biaya arm A dan B terbaca nol dan hasil benchmark tidak berarti.

Cek cepat:

```bash
tokenburn db export | awk -F, 'NR>1 && $4=="claude-opus-5"{c+=$10} END{print "opus5_cost="c}'
```

Harus lebih besar dari 0.

## Yang diketahui rusak

- ~~**Bug continuation agent-workflow**~~ — **DIPERBAIKI 2026-08-15.** Reply second_agent berakhir sebelum `[DIGEST]`, lalu balasan continuation (yang isinya hanya digest) menimpa balasan pertama di `core/executor.py:772`. Body evidence hilang, `output.raw.md` tinggal digest, `anchors: 0`. Lolos gerbang kontrak karena `[digest]` sendiri terdaftar sebagai penanda evidence (`core/executor.py:178`), jadi kegagalan tampil sebagai `ok:true, continuation_recovered:true`. Teramati 3 dari 4 panggilan delegated (`continuation_first_reply_chars` 12635, 12498, 3402 — semuanya terbuang).

  Perbaikan: `_merge_continuation()` di `core/executor.py` menggabungkan body balasan pertama dengan blok kontrak balasan kedua, memotong di penanda `[DIGEST]` **pertama** milik balasan pertama, dan mundur ke balasan kedua saja bila hasil gabungan sendiri cacat. Meta baru `continuation_merged` menandai apakah penggabungan terjadi. Test pengunci di `tests/checks/continuation.py`.

  Catatan: percobaan pertama memotong di penanda terakhir (`rsplit`) dan gagal di lapangan — balasan yang mengutip template membawa dua penanda, penanda terkutip tetap berada di depan dan menutupi blok lengkap, sehingga gabungan ditolak diam-diam (`continuation_merged=false`) dan body tetap hilang. Test mock tidak menangkapnya; uji lapangan yang menangkap. Kedua kasus kini punya test.

  Verifikasi lapangan 2026-08-15: `continuation_merged=true`, body ber-anchor bertahan, digest lengkap tergabung, `python tests/run.py` lolos.

## Catatan keputusan

Keputusan terkunci ada di `BENCHMARK-PLAN.md` §2. Kalau ada yang dibuka ulang, catat di sini beserta alasannya.

- 2026-08-15 — rencana awal dibuat, empat keputusan dikunci (worker opencode gratis, oracle otomatis, corpus dari revert commit, SUT repo ini).
- 2026-08-15 — revisi setelah analisis `tokenburn`: dua item bangun dicoret (tabel harga, agregator), metrik token premium dipindah dari input segar ke cacheRead+cacheWrite.
- 2026-08-15 — runtime sekarang punya telemetri sendiri (`.workflow/usage.jsonl`,
  `python main.py --command report`). Ini TIDAK menggantikan benchmark 3-arm: ia mengukur
  arm C dari dalam, dengan token estimasi `chars//4`, dan tidak melihat arm A maupun B sama
  sekali. Yang berubah cuma sisi worker Fase 4 — angka arm C bisa dipanen dari satu stream
  alih-alih dijahit dari `call.meta.json` per run. Pertanyaan §1 tetap butuh tokenburn dan
  tetap terhalang P0.1.
- 2026-08-20 — empat keputusan dikunci sebelum Phase 0 lanjut. (1) Tag `v3.4.5` menunjuk
  `6ef1be0` apa adanya; nomor tidak dinaikkan karena `BENCHMARK-PLAN.md:5` sudah mengunci
  nama SUT, dan selisih antara tag dan catatan rilisnya dicatat di `CHANGELOG.md` alih-alih
  dibereskan dengan versi baru. (2) `security_violation` ditambahkan sebagai verdict
  keempat di `oracle.py` — sah karena nol unit dipanen; jendelanya tertutup begitu baris
  ledger pertama ditulis. (3) Arm A dan B dijalankan operator manual, harness cuma memanen
  tokenburn per `sessionId`; konsekuensinya P0.2 naik jadi gerbang keras dan variansi
  operator masuk §10. (4) Bentuk harness = opsi 1: `driver.py` mengotomatiskan arm C,
  `collect.py` memanen ketiga arm. Arm C diotomatiskan karena ia subjek yang diukur, dan
  satu-satunya yang bisa diulang mesin.
