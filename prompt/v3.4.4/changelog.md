# Changelog — v3.4.4

Rilis provider kedua. v3.4.3 membangun seam-nya tetapi tidak pernah terbit, jadi catatan
ini memuat keduanya: adapter codex beserta perkakas yang membuatnya bisa dipilih, dan
perbaikan atas tiga hal di dalamnya yang baru terlihat ketika CLI-nya benar-benar
dijalankan, bukan sekadar dibaca.

## Provider codex

- `adapters/codex_adapter.py` menjalankan `codex exec` sebagai second_agent. Bentuknya
  berbeda dari opencode, bukan sekadar penggantian nama: prompt masuk lewat stdin (`-`)
  sehingga batas 8191 karakter milik Windows tak pernah tercapai, session id datang di
  event pertama aliran JSONL sehingga tak ada panggilan bootstrap yang harus dibayar, dan
  melanjutkan pekerjaan adalah subcommand `codex exec resume <id>`, bukan sebuah flag.
- Sandbox `read-only` dipasang di setiap invocation lewat `--sandbox` dan `-c
  sandbox_mode=…`, bukan ditulis sekali ke sebuah file config. Nilai di file bisa diedit
  belakangan tanpa ada yang tahu; argumen argv tidak bisa.
- `config/providers.py` menyatakan sekali apa yang dikirim tiap provider. Sebelumnya
  jawaban itu dieja empat kali — `tools/gen_manifest.py`, `tools/extract_config.py`,
  `install.py`, dan manifest itu sendiri — masing-masing dengan string `"opencode"`
  tertanam di dalamnya.
- `adapters/registry.py` memetakan nama ke adapter. Sebelum ini `core/executor.py`
  menyebut `OpenCodeAdapter` di konstruktornya sendiri, sehingga kunci `second_agent`
  ditulis tetapi tidak pernah dibaca.

## Codex `exec resume` menolak `--color`

Argv resume mengirim `--color never`. Subcommand `resume` menerima himpunan opsi yang
lebih sempit daripada `exec`, dan `--color` bukan salah satunya:

```
error: unexpected argument '--color' found
```

Penolakan itu terjadi di parser argumen, sebelum model tersentuh, sehingga panggilan mati
dengan exit 2 dan pesan yang tak satu pun pola di `_RATE_LIMIT_SIGNS` atau
`_STREAM_FAIL_SIGNS` bisa kenali — hasilnya `error_type: unknown` tanpa petunjuk penyebab.
Efeknya bukan panggilan yang melemah melainkan continuation yang mati total: setiap call
kedua dan seterusnya dalam satu sesi codex gagal.

Test yang ada saat itu hanya memastikan `-C` dan `--sandbox` **tidak** dikirim. Tak ada
satu pun yang memastikan flag yang dikirim memang diterima. `checks/provider.py` kini
menyebut setiap flag yang ditolak `resume` satu per satu.

## Session capture codex

Terverifikasi terhadap codex-cli 0.147.0: `codex exec --json` menerbitkan
`{"type":"thread.started","thread_id":"…"}` sebagai baris pertama stdout dan membiarkan
stderr kosong, jadi jalur normalnya memang bekerja. Yang tidak ada adalah jaring di
bawahnya.

- `run()` kini memindai ulang stdout dan stderr yang tersimpan ketika penangkapan hidup di
  `_drain` tidak menghasilkan apa pun. Build yang menyangga alirannya menyerahkan
  semuanya sekaligus saat keluar, dan pemindaian baris-demi-baris yang sudah selesai tak
  akan pernah melihatnya.
- `_drain` memindai kedua aliran, bukan stdout saja.
- `_THREAD_ID_PATTERNS` menerima banner teks `session id: <uuid>` yang dicetak `codex exec`
  **tanpa** `--json`. Bukan bentuk yang bisa dihasilkan adapter ini — setiap panggilan
  memakai `--json` — tetapi itulah yang dilihat pembaca saat menjalankan codex dengan
  tangan, dan pola ini menutup jarak antara "capture rusak" dengan "kamu sedang melihat
  mode yang lain".
- Panggilan yang menjawab tetapi tak pernah menyebut thread id kini gagal dengan
  `session_capture_failed`, sejajar dengan opencode. Teks jawabannya dibawa di
  `orphan_content` supaya menggagalkan panggilan tidak sekaligus memusnahkan hasilnya.
  Cek ini diletakkan paling akhir, sesudah timeout dan returncode: keduanya kegagalan lain
  dengan next_action sendiri, dan melabeli panggilan yang kena rate limit sebagai gagal
  capture akan mengirim pembaca memperbaiki hal yang salah.
- Pesan timeout tidak lagi menjanjikan "the session was captured, so the retry resumes"
  tanpa syarat. Panggilan yang dibunuh sebelum event pertamanya tidak punya sesi, dan
  kalimat itu paling salah justru pada saat ia paling dibutuhkan benar.

## Satu file yang memilih provider

v3.4.3 membuat `runtime.second_agent` di `.workflow/config.json` ikut memilih provider.
v3.4.4 menariknya kembali. Persoalannya ada pada pasangan kunci, bukan pada kuncinya:
`provider_command` hanya pernah dibaca dari `second_agent.json`, sehingga project yang
menyebut codex di config.json membangun adapter codex lalu menyerahkannya binary milik
opencode — kombinasi yang ditolak `_command_guard` pada setiap panggilan.

- `adapters/registry.py:selected_provider()` menjadi satu-satunya jawaban. Sebelumnya
  doctor, probe, pemindai MCP, dan executor masing-masing menyelesaikannya dengan caranya
  sendiri, sehingga sebuah project bisa didiagnosis sebagai opencode, dipindai sebagai
  codex, dan dieksekusi sebagai apa pun yang kebetulan dibaca terakhir.
- Hint yang diabaikan dilaporkan lewat `provider_hint_ignored` di meta tiap panggilan.
  Kunci yang tidak dihormati tanpa dilaporkan akan kembali menjadi kunci yang diam-diam
  tidak berfungsi — persis keadaan yang hendak diperbaiki v3.4.3.
- `main.py:probe_second_agent` menyebut provider secara eksplisit. Config yang sudah
  ter-merge selalu membawa kunci `provider`, terisi default ketika filenya tidak pernah
  memilih, sehingga watchdog menguji CLI yang berbeda dari yang menggantung.

## Yang belum ditutup

- Codex tidak mengirim file batas di root project. Sandbox-nya menghalangi tulis, tetapi
  membaca file rahasia tidak dilarang sebagaimana `opencode.json` melarangnya.
  `adapters/codex_install.py` melaporkannya sebagai `not_applicable` dengan alasannya,
  bukan melewatkannya diam-diam.
- `core/bundle_integrity.py` memeriksa `merge == "merge"` sebelum memeriksa blok managed,
  sehingga deteksi edit lokal pada `AGENTS.md` terpasang tidak pernah berjalan untuk
  provider mana pun. Diketahui, sengaja belum diubah di rilis ini: memperbaikinya akan
  memunculkan drift pada setiap pemakai yang pernah menyunting file itu.
- `tools/sim_flows.py` dan `checks/support.py` masih terikat ke `OpenCodeAdapter`. Tidak
  ada simulasi alur untuk codex; cakupan codex seluruhnya di tingkat argv dan parser.
