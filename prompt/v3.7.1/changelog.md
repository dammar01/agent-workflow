# Changelog — v3.7.1

Satu tema: menutup temuan audit codebase 2026-09-24
(`storage/audit_codebase-agent-workflow-20260924_014936-76a74294/`) pada batas kepercayaan.
`request.json` ditulis agent, jadi policy yang mengizinkan origin remote atau command yang
dieksekusi tak lagi boleh tinggal di request itu sendiri; salinan policy secret OpenCode untuk
`grep` disamakan dengan `read`; dan risiko provider tanpa batas baca kini selalu terlihat.
Tambahan hardening sebelum tag: environment OpenCode dipangkas ke scope project, jalur baca
browser menolak alamat metadata, lock store bertoken owner, dan jalur gagal
(rollback migrasi, receipt installer, lock sesi job, telemetry executor) melapor jujur.

Isi rilis ini adalah commit sesudah tag `v3.7.0` sampai tag ini.

## Status rilis

Nomor sudah di-stamp (`stamp_version --check` lolos). Gerbang `RELEASE.md` dijalankan
2026-09-25 pada working tree sesudah `/.verify` (verdict DONE, belum di-commit):

| Gerbang | Hasil |
|---|---|
| `python tests/run.py` | lolos — `scenario` PASS (semua check standalone termasuk `hardening`); `e2e-smoke` di-skip (opt-in `WORKFLOW_E2E_SMOKE=1`) |
| `python tools/e2e/e2e.py` | lolos — 137 passed, 0 failed, 1 skipped (`delegated commands`, opt-in `--full`) |
| `python tools/maintain/gen_manifest.py --check` | lolos — manifest in sync dengan `dist/` |

Skipped bukan pass: Chromium sungguhan (`e2e-smoke`) dan command delegated live (`--full`)
belum dijalankan.

## Breaking

- **Key config-only di `/.verify-browser` (audit F1, F2).** `allow_remote`, `allowed_origins`,
  `existing_test_command`, `existing_test_allowlist`, `existing_test_timeout_s`
  (`core/evidence/e2e/request.py` `CONFIG_ONLY_SETTINGS`) hanya dibaca dari section `e2e`
  `.workflow/config.json`. Request yang menyebut salah satunya → `request_invalid` dengan pesan
  `settings.<key>: config-only, a request cannot set it; move it to the e2e section of
  .workflow/config.json`. Sebelumnya request bisa menjalankan command apa pun dengan seluruh
  environment user dan mengizinkan origin remote untuk dirinya sendiri.
  Migrasi: pindahkan key itu dari request ke `config.json` `e2e`. `base_url` tetap boleh per-run,
  dan dinilai dengan policy origin dari config.
- **Environment OpenCode = allowlist + `.env` project aktif.** Bootstrap, probe, dan run
  OpenCode tidak lagi mewarisi `os.environ`. Child mendapat variabel OS/jaringan (`PATH`, temp,
  locale, proxy/CA, `HOME`/`USERPROFILE`/`APPDATA`/`XDG_*`, `OPENCODE_*`) ditambah key dari
  `.env` di project root (`adapters/shared/child_env.py`). Auth `opencode auth login` /
  `/connect` tetap jalan (`auth.json` di data dir home). Migrasi: key provider yang selama ini
  hanya ada di environment shell global dan dirujuk `{env:NAME}` → pindahkan ke `.env`
  project, atau pakai `opencode auth login`. Codex/agy tidak berubah.

## Keamanan

- **Paritas `grep` OpenCode (audit F5, F34).** `dist/config/opencode/opencode.project.json`
  blok `grep` kehilangan delapan pola yang ada di `read` (`*.p12`, `*.pfx`, `*.jks`,
  `*.keystore`, `*.ppk`, `*.kdbx`, `*credentials.yml`, `*credentials.yaml`) — file yang ditolak
  `read` bisa dicetak `grep`. Kini sama; `tests/checks/provider.py` memeriksa tiap blok pola path
  terhadap `SECRET_READ_PATTERNS`, bukan cuma `read`.
- **Codex/agy = trusted provider (audit F8, diterima sebagai risiko).** Keduanya tanpa batas
  baca dan menerima seluruh environment. Tidak dipangkas dengan allowlist (bisa memutus auth CLI,
  dan tak menjaga apa pun dari provider yang sudah bisa membaca `.env`). `/.doctor` menambah cek
  `second_agent_read_boundary` dan satu `WARNING` di `recommended_fixes` (bukan issue); skill
  `/.provider` menyebutnya saat memilih.
- **`external_directory: deny` di root policy project OpenCode (audit access).** Sebelumnya
  hanya di `agent.plan` template; override `AI_PROXY_OPENCODE_AGENT` ke agent lain lolos.
  `tests/scenario.py` memeriksa keduanya.
- **Jalur baca browser menolak alamat metadata (audit injection/SSRF).** Preflight menambah
  cek `read_policy`: host remote yang di-allow-list tetapi resolve ke link-local
  (169.254.169.254, fe80::), `fd00:ec2::254`, `100.100.100.200`, unspecified, atau multicast →
  `spec_invalid`. Alamat private tetap boleh (staging internal). Probe reachability tidak lagi
  mengikuti redirect — 3xx sudah bukti server menjawab. Alamat IPv6 yang membawa IPv4 dibongkar
  dulu sebelum dinilai: IPv4-mapped (`::ffff:`), IPv4-compatible (`::/96`), 6to4 (`2002::/16`),
  NAT64 well-known (`64:ff9b::/96`), dan NAT64 local-use (`64:ff9b:1::/48`, semua layout
  RFC 6052 yang mungkin di dalamnya).
- **agy lewat shim `.cmd`/`.bat` ditolak bila argv berisi metakarakter cmd.exe** (guard yang
  sama dengan OpenCode) → `unsafe_command_line` sebelum spawn.
- **Redaksi:** credential ber-key yang di-quote kini tertangkap mulai 6 karakter (tanpa quote:
  12, atau 8 bila mengandung digit); URL skema apa pun dengan `user:password@` diredaksi.
- **Marketplace plugin Claude di-pin** ke tag (`caveman` `v2.7.0`, `ui-ux-pro-max-skill`
  `v2.15.0`) di `settings.template.json`. Source marketplace hanya mendukung `ref`, bukan `sha`,
  jadi tag tetap bisa digeser pemilik repo. Settings user yang sudah ada tidak ditimpa merge
  aditif installer.

## Reliability

- **Lock store bertoken owner (audit F9/F24).** `utils/owned_lock.py` menggantikan tiga salinan
  lock (`fact_store`, `knowledge/store`, `e2e/knowledge`). Lock hanya diambil alih bila pid
  pemiliknya sudah mati (atau file tanpa owner lebih tua dari TTL); pemilik hidup ditunggu lalu
  `TimeoutError` (ingest melaporkannya sebagai `fact_ingest_error`). Release hanya menghapus lock
  bertoken miliknya sendiri, dengan retry bila Windows menolak unlink saat file sedang dibaca.
  Pengambilalihan lock owner mati diserialkan lewat OS advisory lock (`<lock>.reclaim`,
  `msvcrt.locking`/`flock`) dan owner dinilai ulang di dalamnya, sehingga dua writer yang
  sama-sama melihat pid mati tak bisa saling menghapus lock baru; guard tak pernah direbut dari
  reclaimer hidup. Di Windows, create di atas file delete-pending (`PermissionError`) di-retry.
- **Rollback migrasi jujur (audit F10).** Setiap langkah rollback dicoba dan kegagalannya
  dikumpulkan; bila ada yang gagal → `MigrationRollbackIncomplete` (error asli sebagai cause),
  staging dipertahankan, dan upgrade melapor `rollback is INCOMPLETE` beserta lokasinya.
- **Receipt installer bertahap (audit F11).** Receipt ditulis ulang atomik (temp unik per
  proses) mulai sebelum mutasi pertama (sesudah semua cek abort) dan setelah tiap langkah
  tercatat, `complete: false` sampai tulisan terakhir; apply tanpa perubahan tidak meninggalkan
  receipt. `--rollback` menerima receipt parsial dan memberi catatan install terputus.
- **Lock sesi job dilepas walau save gagal.** `complete_job`/`fail_job` melepas lock di
  `finally` (release tetap bertoken); jalur exception worker tidak lagi bisa melempar keluar
  saat mencatat kegagalan (`meta.job_record_error`).
- **Telemetry executor tak lagi menimpa hasil (audit F13).** Seluruh blok call-meta di `finally`
  di-guard; kegagalannya muncul sebagai `meta.call_meta_error`.
- **Recurrence fact = 5, dari satu sumber.** Fallback `RECURRENCE_THRESHOLD` membaca
  `default_policies()`. Karena `config.json` hanya berisi override, fallback lama `3` adalah nilai
  efektif untuk setiap project yang tidak menyetel key-nya — klaim dipromosikan lebih cepat dari
  yang didokumentasikan.
- **quick_verify:** path dari git diberi prefix `./` sebelum masuk argv checker, sehingga nama
  file berawalan `-` tidak terbaca sebagai opsi.

## Lain-lain

- Pesan runtime yang menyebut `settings.allow_remote`, `settings.allowed_origins`,
  `settings.existing_test_command`, `settings.existing_test_allowlist` kini menyebut
  `config.json e2e.<key>` (`preflight.py`, `spec.py`, `browser.py`, `existing_tests.py`).
- Test baru: penolakan tiap key config-only, request penyelundup policy berhenti sebelum
  panggilan provider, `base_url` dinilai origin dari config, policy di config bertahan lintas
  request (`e2e_routing`); `doctor-read-boundary`; suite `hardening` (lock owner, rollback
  gagal, receipt parsial, lock job saat save gagal, telemetry gagal, env OpenCode, alamat
  metadata + redirect, argv/redaksi).
- Kontrak diperbarui: `docs/runtime-contracts.md`, `docs/reference.md`, skill
  `verify-browser`, skill `provider`, `dist/config/claude/CLAUDE.md`.

## Tidak termasuk (ditunda)

- `allow_side_effects`, `allow_local_side_effects`, `ignore_repeat_brake`,
  `allowed_destructive_requests`, `allowed_read_only_requests` masih bisa diset dari request.
- Throughput (F25–F27): admission yang mem-parse semua job, usage stream dibaca penuh tiap call,
  quick_verify tanpa batas jumlah file / deadline total. Butuh pengukuran dulu.
- Env codex/agy dan `existing_tests.py` tetap mewarisi environment penuh (trusted/diterima).
- Rebinding DNS pada jalur baca: read_policy menilai alamat saat preflight; browser tetap
  me-resolve sendiri (pin hanya untuk write).
- NAT64 network-specific prefix (prefix pilihan operator di luar `64:ff9b::/96` dan
  `64:ff9b:1::/48`): tak bisa dikenali dari sisi klien, jadi alamat metadata yang disematkan di
  prefix seperti itu lolos `read_policy`. Teredo (`2001::/32`) sengaja tak dibongkar — IPv4 di
  dalamnya endpoint klien, bukan tujuan.
- Temuan low lain (drift, cleanup resource, validasi tipe) di audit yang sama.
- Receipt installer: bila file tujuan sudah berubah lalu penulisan receipt gagal (disk penuh),
  entry terakhir hilang dari receipt; backup file itu tetap ada untuk dipulihkan manual.
- PID reuse: lock milik pid yang sudah dipakai proses lain dianggap hidup → `TimeoutError`
  (availability, bukan concurrent write).
