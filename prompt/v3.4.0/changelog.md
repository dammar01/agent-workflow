# Changelog — v3.4.0

Rilis dua tahap. `3.4.0-a` memperbaiki keandalan runtime; `3.4.0-b` mengubah kontrak prompt.
Latar belakangnya satu keluhan konkret: job ke-reap padahal kerjanya masih sah, dan second_agent menggantung tanpa progres saat kena limit.

---

## 3.4.0-a — Reliability & integritas evidence

### PID hidup ≠ sedang bekerja

Sebelumnya `process_alive(pid)` adalah satu-satunya sinyal kesehatan worker. Masalahnya: proses tampak persis sama saat agent sibuk bekerja dan saat ia menggantung menunggu rate limit. Reaper tak punya cara membedakan keduanya, jadi ia menebak — dan kadang membunuh kerja yang sah.

Worker kini mengirim **heartbeat** dari poll loop-nya, dan job diklasifikasi tiga keadaan:

| Keadaan | Arti | Tindakan |
|---|---|---|
| `alive-progressing` | PID hidup, heartbeat segar | tunggu |
| `alive-stalled` | PID hidup, heartbeat basi > `stall_threshold_seconds` | **jangan reap** — probe dulu |
| `dead` | PID hilang | reap → `worker_died` |

Saat `alive-stalled`, runtime mengirim probe `PING` **sekali** ke sesi opencode **baru** — bukan ke sesi yang dicurigai menggantung, karena sesi itulah yang sedang diperiksa. Probe punya timeout sendiri; tanpa itu watchdog ikut menggantung seperti pasiennya.

- probe menjawab → `stalled_no_progress`: opencode sehat, sesi ini yang macet
- probe gagal/timeout → `stalled_on_limit`: kemungkinan kena limit, kerja masih bisa menyusul

Keduanya **dilaporkan, bukan di-reap**. Menghukum kecurigaan adalah persis bug yang dikeluhkan.

Probe dibatasi satu per job: probe sendiri memakai kuota, dan watchdog yang boros memperparah keadaan yang ia awasi.

### Heartbeat mustahil sebelum model eksekusi berubah

Worker sepenuhnya terkunci di satu `subprocess.run()`. Tak ada loop, tak ada callback — nol tempat untuk mengirim heartbeat. Rencana awal mengira ini "tambah file heartbeat"; falsifikasi menunjukkan itu keliru.

`_run_args()` kini `Popen` + polling loop dengan dua thread penguras pipe. Satu perubahan ini membuka **tiga** kapabilitas sekaligus: heartbeat, timeout yang benar-benar bekerja, dan kill-tree.

### Timeout punya tiga lubang, bukan satu

1. `init_session()` meng-hardcode `communicate(timeout=None)` — bootstrap tak bisa dibatasi apa pun isi config
2. Default `timeout_seconds=0` → tanpa batas
3. Route dict mengambil `null` dari `opencode.json` dan **menimpa** default router

Ketiganya ditutup. Default kini 1800 dtk; bootstrap dapat anggaran sendiri (180 dtk) karena ia hanya membalas "READY" dan tak boleh mewarisi anggaran task panjang. `null` kini berarti **warisi default**, bukan tanpa batas. `0` tetap berarti tanpa batas, tapi bukan lagi default.

### Orphan Node.js

`opencode` di Windows adalah shim `.cmd` yang men-spawn node. `subprocess.run(timeout=)` membunuh shim-nya saja; node terus hidup. Setiap timeout meninggalkan satu proses memakan RAM — memperparah kondisi kehabisan memori yang jadi salah satu keluhan awal.

`osutil.terminate_tree()` membunuh seluruh pohon: `taskkill /F /T` di Windows, `killpg` di POSIX. Diverifikasi empiris terhadap proses cucu sungguhan, bukan mock.

> Cabang POSIX (`killpg`) belum diuji — hanya jalur Windows yang dijalankan. Ini lubang uji yang diketahui.

### Plafon runtime job

Saat RAM habis, worker bisa mati dengan cara yang luput dari pengecekan PID. `job_max_runtime_seconds` (5400 dtk) menggagalkan job yang melewatinya sebagai `job_expired` — berbeda dari `worker_died` supaya penyebabnya tak tersamar.

### Heartbeat tak boleh menimpa state job

Menyimpan heartbeat di dalam record job berarti read-modify-write tanpa lock, dari dua proses, setiap dua detik. Terbukti (bukan disimpulkan): beat yang telat mengembalikan job yang sudah di-reap menjadi `running` dan **menghapus** `error` serta `completed_at`.

Heartbeat dan verdict probe kini tinggal di file sisi terpisah — satu penulis per file. Race-nya dihilangkan, bukan dipersempit.

### Fact store berhenti belajar secara senyap

`_parse_block()` mengumpulkan bullet sampai baris pertama yang bukan bullet. Satu baris kosong tepat setelah `grounded:` mengakhiri section itu — mengembalikan nol klaim, tanpa error. Digabung `try/except pass` di executor, fact store bisa berhenti menerima fakta berhari-hari tanpa satu pun gejala.

Terukur pada output nyata: parser lama **0 klaim**, parser baru **20**, dari file yang sama.

Parser kini toleran pada baris kosong, bullet bersarang, `*`, blok kode, dan baris sambungan. Batas section: `key:` di kolom 0 — termasuk yang membawa teks setelah titik dua, karena `external: none (...)` mengakhiri blok sebelumnya dan bukan sambungan dari bullet terakhir.

Kegagalan ingest kini muncul di `meta.fact_ingest_error`. Tetap best-effort — tak pernah menggagalkan panggilan delegated — tapi tak lagi tak terlihat.

### Graphify jadi bisa ditanya

`graphify-out/graph.json` sebelumnya hanya dibaca sebagai file oleh second_agent. `core/graph_index.py` mem-parse-nya langsung — tanpa proses tambahan, tanpa ketergantungan PATH — dan menyuntikkan daftar file terurut ke prompt evidence.

Dibingkai sebagai **leads, bukan temuan**. Sebuah edge graph menyatakan dua hal berhubungan, bukan bahwa sebuah klaim benar; disajikan sebagai temuan, ia akan kembali sebagai `grounded` tanpa ada yang pernah membuka file-nya.

Confidence edge dihormati: `EXTRACTED` bobot penuh, `INFERRED`/`AMBIGUOUS` diturunkan. Path dinormalisasi ke POSIX relatif-repo agar bermakna sama di mesin lain. Graph yang basi terdeteksi dan ditandai — runtime tak bisa memperbaikinya sendiri, tapi bisa berhenti berpura-pura ia mutakhir.

### Instrumentasi

Tiap panggilan delegated menulis `call.meta.json`: exit code, durasi, timeout, cara kill, ekor stderr, dan apakah output sempat terkuras penuh. Tanpa ini, "opencode berperilaku aneh saat kena limit" tetap jadi cerita rakyat.

Ambang 360 / 1800 / 5400 dtk **belum dikalibrasi** — ditetapkan dari durasi run yang teramati, bukan dari data kegagalan nyata. Instrumentasi ini yang nantinya mengoreksinya.

---

## 3.4.0-b — Kontrak prompt

### Prefix `/.` tidak lagi wajib

Validasi STRICT diganti **auto-detect intent**. Bahasa natural dipetakan ke command lalu langsung dijalankan, didahului satu baris `[INTENT] <command> — <alasan>`.

Baris itu transparansi, bukan pertanyaan: ia tidak menunggu jawaban. User bisa Esc kalau tebakannya meleset. Gate konfirmasi ditolak dengan sengaja — memaksa konfirmasi tiap perintah mengubah auto-detect jadi ketikan tambahan, bukan penghematan.

Batas yang tetap ada:

- Prefix `/.` **tetap didukung** sebagai override eksplisit, dan selalu menang atas penebakan. Itu jalan keluar saat auto-detect salah.
- Aksi destruktif/ireversibel (commit, hapus, tulis di luar project) **tak pernah** auto-fire dari intent tebakan.
- Ragu antara dua command → pilih yang lebih murah. Command delegated memakan menit dan kuota; menebak salah itu mahal.
- Pertanyaan biasa dan obrolan bukan command. Tidak semua kalimat harus dipaksa jadi perintah.

`[INVALID COMMAND]` dihapus. Input tanpa prefix bukan error.

### `/.plan` wajib menyajikan alternatif

Setiap plan kini ditutup blok `[OPTIONS]`: maksimal 3 opsi, masing-masing dengan plus, minus, effort, dan risiko utama — ditutup **satu** rekomendasi.

Bounded dengan sengaja, karena format ini gampang berubah jadi mesin scope creep:

- Wajib beda **pendekatan**, bukan beda kosmetik atau urutan
- Wajib **dalam scope task**. Usul rewrite atau ganti stack saat task-nya bukan itu adalah pengalihan, bukan pilihan
- `minus` wajib jujur **termasuk untuk opsi yang direkomendasikan**. Opsi tanpa minus berarti belum dipikirkan
- Opsi yang dibantah evidence ditandai ❌ beserta evidence yang membantahnya — tidak disajikan setara
- Rekomendasi wajib **satu**. Tak bisa memilih berarti itu open_question, bukan opsi
- Hanya satu opsi masuk akal → katakan begitu. Jangan karang opsi kedua demi memenuhi format

User memilih opsi non-rekomendasi → jalankan pilihannya. Minus-nya sudah disebut; keputusannya milik user.

### Sub-agent fan-out (default OFF)

Rencana awal menyatakan second_agent tak punya kemampuan spawn — disimpulkan dari tidak adanya konfigurasi MCP/tools di `opencode.json`. Observasi config-nya benar; kesimpulannya salah. Tool `task` bawaan agent opencode, bukan sesuatu yang dinyalakan lewat config.

Spike membuktikannya lewat trace runtime, bukan lewat pengakuan model: dua sub-agent dimulai bersamaan lalu selesai bersamaan. Bukti eksekusi mengalahkan laporan-diri — sebuah agent bisa saja mengaku fan-out padahal membaca berurutan.

Saat dinyalakan dan graphify punya ≥2 cluster, prompt membawa `[SUBAGENT_PLAN]`: satu sub-agent per cluster, paralel, tiap sub-agent dibatasi ke file cluster-nya sendiri.

Default **OFF**, dan itu disengaja:

- fan-out mengalikan kuota per panggilan — memperbesar peluang kena limit, keadaan yang justru diperbaiki `3.4.0-a`
- blok instruksinya menambah ~1,1 KB, sementara prompt dikirim sebagai argv dengan plafon ~8191 char
- output terstruktur besar terbukti mati di tengah stream 3 dari 3 kali hari itu; fan-out memperbesar output

Satu cluster tidak memicu fan-out: satu sub-agent memakan round trip tanpa keuntungan atas membaca langsung.

**Klaim fan-out diverifikasi, bukan dipercaya.** Dua sinyal harus sepakat — baris `subagents:` yang dideklarasikan, dan tag `[cN]` pada klaim hasil merge. Deklarasi tanpa tag menghasilkan `subagent_used: false` plus peringatan eksplisit. Menerimanya diam-diam adalah cara sebuah langkah yang tak dikerjakan mulai terlihat selesai.

Jawaban jujur `subagents: none (no spawn tool)` diperlakukan sebagai hasil sah, bukan kegagalan.

---

## Migrasi

`CONFIG_VERSION` 3.3.1 → 3.4.0. Backfill aditif saat `load_workspace_state`; nilai user menang. `runtime.tool_version` kini ikut disegarkan — sebelumnya ia terpaku ke versi yang pertama kali menulis workspace, membuat `doctor` melaporkan versi tool yang sudah tak dijalankan.

Key baru di `opencode.json`: `bootstrap_timeout_seconds`, `stall_threshold_seconds`, `probe_timeout_seconds`, `job_max_runtime_seconds`. Semua punya default; config lama tetap jalan.

`policies.graph_leads_enabled` (default `true`) mematikan injeksi leads bila tak diinginkan.
`policies.subagent_fanout_enabled` (default `false`) menyalakan fan-out sub-agent.

## Belum tuntas

- Cabang kill-tree POSIX nol diuji
- Perilaku opencode nyata saat rate limit belum terekam; ambang masih tebakan
- `probe()` belum pernah menghadapi limit asli
- Prompt dikirim sebagai argv → plafon keras ~8191 char lewat `cmd.exe` di Windows. Blok fan-out memakan ~1,1 KB dari anggaran itu
- `config/opencode.json` cocok dengan pola `.gitignore`, jadi perubahan di sana tak ikut ter-commit
- Fan-out sub-agent belum pernah dijalankan dari ujung ke ujung: kapabilitas spawn terbukti, tapi jalur merge + deteksi baru diuji lawan output sintetis, belum lawan hasil fan-out sungguhan
- `3.4.0-b` murni prompt-layer di sisi auto-detect intent — nol tercakup `test_scenario.py`, karena runtime tak punya jalur intent sama sekali
