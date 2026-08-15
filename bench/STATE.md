# STATE — Benchmark 3-Arm

Rencana: `bench/BENCHMARK-PLAN.md`. Perbarui file ini setiap fase selesai.

## Progres

| Fase | Status | Tanggal | Keluaran |
|------|--------|---------|----------|
| P0.1 Tambal harga `claude-opus-5` | BELUM | — | — |
| P0.2 Verifikasi unit → sessionId | BELUM | — | — |
| P0.3 Uji `tokenburn proxy` (opsional) | BELUM | — | — |
| 1. Bangun corpus 15 task | BELUM | — | `bench/corpus.json` |
| 2. Driver per unit | BELUM | — | `bench/driver.py` |
| 3. Oracle dibekukan | BELUM | — | `bench/oracle.py` |
| 4. Panen data | BELUM | — | `bench/ledger.jsonl` |
| 5. Agregasi dan analisis | BELUM | — | `bench/aggregate.py`, laporan |

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
