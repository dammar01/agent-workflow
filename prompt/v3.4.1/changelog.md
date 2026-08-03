# Changelog — v3.4.1

Rilis stabilisasi untuk menutup false-positive verification, reuse evidence yang tidak
immutable, kebocoran credential pada failure path, race persistence, dan rollback
installer yang tidak dapat membuktikan destination masih aman disentuh.

## Runtime dan verification

- `sweep` sekarang lokal: staged, unstaged, dan untracked diff dipindai tanpa OpenCode atau
  job worker, termasuk repository yang belum memiliki commit.
- `upgrade` tersedia sebagai command lokal untuk regenerate runner dan backfill config sambil
  mempertahankan nilai user serta `sessions/`.
- Quick verify tidak menganggap file skipped, checker yang hilang, kegagalan discovery Git,
  atau name finding sebagai pass. Staged file pada repository tanpa commit ikut diperiksa.
  Exit code `verify`, `await`, dan `result` mengikuti verdict efektif.
- Kontrak delegated verify diparse menjadi verdict runtime; hasil incomplete/fail tidak lagi
  bergantung pada pembaca JSON untuk menghasilkan exit nonzero. Finding yang ditempatkan di
  section yang salah tetap dirutekan menurut severity/origin/scope dan gagal-tertutup.
- Fan-out menerima subset cluster bertag dari cluster yang benar-benar dideklarasikan sehingga
  slice tanpa finding tidak menghasilkan false negative.

## Evidence dan concurrency

- Evidence reuse hanya menerima artifact immutable
  `.workflow/sessions/<id>/logs/<prompt_id>/output.raw.md` dengan SHA-256 content.
- Entry `response.last.md`, artifact berubah, anchor unresolved, atau anchor tidak lengkap
  ditolak dan dibersihkan dari index.
- Evidence index memakai lock lintas proses dan atomic rewrite. Fact recurrence cache sekarang
  menginvalidasi perubahan existence, mtime, dan ukuran output sesi.
- Admission worker dan upgrade workspace diserialkan lintas proses. Reservation pending tidak
  lagi dapat men-spawn dua worker, dan limit worker global berlaku pada submit maupun recovery.
- Ownership job/session/runtime lock memakai token generasi sehingga worker lama atau
  penyelesaian terlambat tidak dapat menghapus lock attempt yang lebih baru.
- Runtime lock diakuisisi sebelum sidecar/state mutation, memakai native transition guard,
  dan tetap aktif selama PID owner hidup. Reservation yang mati sebelum spawn dapat
  direklamasi oleh `await` maupun `check --wait` setelah grace period.
- Provider session dan cache ID default diisolasi per project. Session path yang perlu
  sanitasi membawa hash sehingga traversal dan collision tidak berbagi state.
- Evidence query mengikat task case-sensitive, route/model/config, fan-out, graph leads,
  dan facts; `--fresh-session` menonaktifkan reuse.

## Security boundary

- Redaksi recursive berlaku pada success, error, timeout, bootstrap, probe, snapshot, dan
  `call.meta.json`. Raw argv diganti jumlah, panjang, dan hash.
- Path relatif di-resolve terhadap project root; traversal, path luar project, home-relative,
  dan bare sensitive filename ditolak sebelum delegasi.
- Primary OpenCode memakai `external_directory: deny`. File discovery harus melalui built-in
  Read/Grep/Glob; Bash file readers dihapus dan hanya command Git read-only yang diizinkan.
- Catch-all Bash deny ditempatkan sebelum allow karena aturan terakhir yang cocok menang;
  variasi Git read-only yang dapat menulis lewat `--output` ditolak setelah allow umum.
- Graph stale cache memakai source fingerprint sehingga touch, add, dan delete source
  menginvalidasi verdict disk maupun process-local.

## Installer dan bundle

- Receipt schema v2 mencatat hash sebelum/sesudah untuk semua create, replace, merge,
  `settings.json`, OpenCode config, dan perubahan mode intent.
- Rollback memvalidasi seluruh destination dan backup sebelum menulis. Destination yang diedit
  setelah install membuat rollback fail-closed tanpa perubahan parsial.
- `--only-command` menghapus hook `intent-gate-set` milik workflow yang sudah terpasang tanpa
  menghapus hook user; `--auto-intent` memulihkannya.
- `--check` menerapkan transform instalasi yang sama untuk settings dan OpenCode, sehingga hook
  wajib atau plan permission yang drift tidak lagi dilaporkan READY.
- Urutan Bash permission dicanonicalize karena last-match menentukan hasil. JSON settings atau
  OpenCode dengan root non-object ditolak sebagai drift tanpa traceback.
- Manifest sekarang mencakup `intent-map.json`; extractor menghasilkan schema manifest aktif
  lengkap. Agent global diperiksa tanpa project; `--init-project DIR` memilih agent project-local.
- Bundle main_agent, second_agent, help, init, doctor, sweep, dan upgrade diselaraskan ke
  v3.4.1. Link prompt canonical sekarang menunjuk source aktif di `dist/config/`.

## Migrasi

Jalankan:

```bash
python install.py --apply --init-project /path/to/project
python install.py --check --init-project /path/to/project
```

Receipt dari versi sebelum schema v2 tetap dapat dipulihkan manual dari backup, tetapi
rollback otomatis menolaknya karena tidak memiliki hash yang cukup untuk membuktikan safety.
