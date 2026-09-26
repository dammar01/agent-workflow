# Benchmark

Moved from `docs/reference.md` ("Benchmark"). Kept in its original language (Bahasa
Indonesia). Paths inside `bench/` refer to the frozen system under test (tag `v3.4.5`), not
to the current source tree.

`bench/` berisi harness benchmark 3-arm yang mengukur ekonomi quality-adjusted tool ini
terhadap dirinya sendiri. Rencananya di [`bench/BENCHMARK-PLAN.md`](../../bench/BENCHMARK-PLAN.md),
progres eksekusi di [`bench/STATE.md`](../../bench/STATE.md).

Tiga arm: **A** = Claude langsung, **B** = native sub-agent, **C** = agent-workflow (repo
ini). Arm A dan B dijalankan operator secara manual dan harness memanen biayanya per
`sessionId`; arm C jalan lewat `python main.py --command ...` dan itu satu-satunya arm yang
bisa dibuat deterministik.

System under test dibekukan di tag `v3.4.5`. Versi itu tidak ikut naik saat `TOOL_VERSION`
naik: SUT yang bergeser di tengah pengukuran membuat hasilnya tidak bisa diatribusikan ke
versi mana pun. `tools/maintain/stamp_version.py` sengaja **tidak** menstempel baris `**Versi SUT:**` di
`bench/BENCHMARK-PLAN.md`, dan `bench/` berada di luar `SCAN_PATHS`-nya: baris itu hanya
diperbarui sadar, saat SUT memang dipindah ke tag lain.

`bench/` sengaja berada di luar scope task apa pun. Agen yang sedang diuji tidak boleh
menyentuh instrumen yang menilainya.

Verdict per unit ditentukan `bench/oracle.py`, yang dibekukan sebelum unit pertama dipanen;
setiap perubahan sesudah klaim beku itu wajib tercatat di log pembekuan di kepala file.
Empat verdict: `accepted`, `rejected`, `security_violation`, `incomplete`. `not_checked` dan
`skipped` bukan pass — stage yang tidak dijalankan menghasilkan `incomplete`, dan
`incomplete` bukan diterima.

Pemetaan verdict dikunci [`bench/test_oracle.py`](../../bench/test_oracle.py), dijalankan
terpisah dari `tests/run.py`:

```
python bench/test_oracle.py
```

Terpisah karena oracle menjalankan `tests/run.py` sebagai stage-nya sendiri; test bench di
dalam suite itu membuat oracle menilai instrumennya sendiri.

Satu unit dijalankan [`bench/driver.py`](../../bench/driver.py) dalam enam fase, dan tiga di
antaranya bukan milik mesin:

```
python bench/driver.py prepare  --task T01 --arm C --repeat 1
#   jalankan sesi agen di dalam worktree yang dicetak
python bench/driver.py delegate --unit T01_C_1 --command explore   # opsional, arm C
python bench/driver.py judge    --unit T01_C_1
python bench/driver.py finish   --unit T01_C_1 --rework-cycles 0
python bench/driver.py teardown --unit T01_C_1
```

`prepare`, `judge`, dan `teardown` berulang identik; sesi agennya tidak. `finish` menstempel
dua angka yang cuma operator lihat — `rework_cycles` dan `main_agent_rewrote` — dan itu
disengaja: apakah main agent menulis ulang kerja delegatnya adalah fakta tentang sesi, bukan
bentuk yang bisa dibaca dari patch akhir.

[`bench/collect.py`](../../bench/collect.py) mengubah unit selesai jadi `ledger.jsonl`. Jalankan
**sebelum** `teardown` — angka sisi worker arm C ada di dalam `.workflow` milik worktree.
Baris tanpa biaya premium ditolak kecuali diminta eksplisit: `aggregate.py` memaksa biaya
yang hilang jadi `$0`, jadi arm yang ekspornya tak pernah datang akan terbaca sebagai arm
termurah.

Batas run terkumpul di [`bench/policy.py`](../../bench/policy.py) — `python bench/policy.py`
mencetaknya. Waktu per unit, cap `rework_cycles`, dan cap panggilan terdelegasi ditegakkan
saat jalan; budget per unit dan per run **tidak** — biaya datang dari tokenburn sesudah run
selesai, jadi `collect.py` melaporkan pelampauan alih-alih mencegahnya.

Daftar karantina flaky di file yang sama, dan kosong. Empat run hijau berturut bukan bukti
suite ini stabil, cuma ketiadaan bukti sebaliknya; nol suite dikarantina atas dasar curiga.
Mengisi daftar itu mengeluarkan suite tersebut dari gerbang penerimaan **setiap** unit dalam
studi, jadi baris yang terkena dicap `quarantined_suites` di ledger dan tidak sebanding
dengan baris bergerbang penuh.
