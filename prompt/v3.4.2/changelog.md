# Changelog — v3.4.2

Rilis stabilisasi menjelang pemakaian tim. Fokusnya permukaan user: entry script yang
menolak command-nya sendiri, script basi yang tidak pernah terdeteksi, versi yang berbeda
antara kode dan dokumen, serta fan-out yang menyala di konfigurasi tetapi tidak pernah
berjalan.

## Entry script

- `run.ps1` menyusun argumen sebagai array dan hanya menambahkan `--prompt` ketika ada
  task. Sebelumnya `--prompt $Task` selalu dikirim; PowerShell membuang argumen string
  kosong sebelum sampai ke proses, sehingga argparse menerima `--prompt` tanpa nilai dan
  seluruh command lokal (`doctor`, `sweep`, `clean`, `inspect`, `init`, `upgrade`) gagal
  dengan `expected one argument`. `run.sh` mengikuti bentuk yang sama demi paritas.
- `init` dan `upgrade` menghapus entry script milik platform lain yang masih tertinggal di
  `.workflow/`. Script itu tidak dipelihara generator mana pun di mesin ini, tetapi tetap
  terlihat dapat dipakai — dan ikut terbawa saat project disalin ke OS lain.

## Doctor

- `doctor` membandingkan isi `run`/`inspect`/`check` di disk dengan hasil generator dan
  melaporkan `run_script_drift` (`missing`, `content_differs`, `foreign_os_leftover`).
  Pemeriksaan sebelumnya hanya menanyakan keberadaan file, sehingga `run.sh` yang masih
  merutekan `sweep` melalui `--job-command` bertahan satu siklus rilis penuh setelah
  generator berhenti melakukannya.
- Drift dihitung sebagai issue, bukan catatan: workspace yang pintu depannya menolak
  command sendiri tidak layak disebut READY.

## Versi

- `tools/stamp_version.py` menjadikan `config.settings.TOOL_VERSION` satu-satunya sumber
  versi. Baris ber-anchor di README, banner `CLAUDE.md`/`AGENTS.md`, dan skill
  `help`/`init`/`doctor` di-stamp ulang dari sana; `--check` mengembalikan exit 1 bila ada
  yang tertinggal. Semver di luar baris ber-anchor tidak disentuh, sehingga catatan
  historis seperti `utils/path_guard.py` tetap menyebut versi aslinya.
- v3.4.1 sempat rilis dengan tiga belas lokasi yang masih menampilkan versi sebelumnya,
  termasuk keluaran `/.help` yang pertama kali dibaca anggota tim baru.

## Fan-out second_agent

- `permission.task` dideklarasikan eksplisit pada agent `plan`: deny-by-default dengan
  allowlist `wf-slice`, `wf-map`, `wf-trace`, `wf-docs`, `wf-db`. Sebelumnya tidak ada
  aturan `task` di konfigurasi mana pun, sehingga larangan men-spawn `general` yang
  bisa menulis hanya hidup sebagai kalimat di `AGENTS.md`.
- `meta.declared_clusters` muncul ketika second_agent menyatakan telah men-dispatch
  sub-agent tetapi tidak menandai klaimnya dengan `[cN]`. Sebelumnya run seperti itu
  melaporkan daftar cluster kosong di samping `subagent_used: false`, yang terbaca seolah
  fan-out tidak pernah dicoba.
- Peringatan `claimed_unconfirmed` menyebutkan penyebabnya dan menunjuk
  `meta.declared_clusters`, bukan sekadar menyatakan hasilnya tidak terkonfirmasi.
- `AGENTS.md` mempersempit pintu `declined`. Alasan seperti "task-nya analitis" atau
  "lebih cepat kalau dibaca sendiri" tidak lagi sah; `declined` hanya berlaku saat
  `communities[]` kosong atau seluruh community jatuh pada file yang sama.
- Probe empiris menegaskan bahwa `@nama` di dalam teks prompt tidak men-spawn apa pun pada
  `opencode run` non-interaktif. Tool `task` adalah satu-satunya jalur fan-out.

## Dihapus

- Argumen `install.py --init-project` dihapus pada v3.4.2. Project root dideteksi otomatis
  dari `.workflow/config.json`, dan scaffolding project menjadi tanggung jawab `init`.
  Perintah lama pada catatan rilis v3.4.1 tidak lagi berlaku:

  ```
  python install.py --apply --init-project /path/to/project   # dihapus
  python install.py --check --init-project /path/to/project   # dihapus
  ```

  Penggantinya:

  ```
  python install.py --apply
  python main.py --command init --work-dir /path/to/project
  ```
