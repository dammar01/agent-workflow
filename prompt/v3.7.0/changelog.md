# Changelog — v3.7.0

Satu tema: `/.verify-browser` dan kontrak evidence diperketat setelah 3.6.0. Guard write kini
menilai izin per endpoint (DELETE dan request pembawa `_method` butuh persetujuan eksplisit,
`blocked_requests` fail-closed), rem pengulangan lintas invocation tahu gate mana yang
menulis streak-nya, body write yang tak terbaca ditolak alih-alih dikirim, dan confidence
digest ikut turun saat anchor yang dikutip tak bisa dibuka di project ini. Di sisi prompt,
batas panjang task untuk transport tanpa argv dilepas, diganti ukuran lebar perubahan
(`scope_width`).

Isi rilis ini adalah commit sesudah tag `v3.6.0` (`cf4cfa1`) sampai tag ini.

## Status rilis

Nomor sudah di-stamp (`stamp_version --check` lolos) dan manifest sudah regenerasi. Gerbang
`RELEASE.md` pada tree final:

| Gerbang | Hasil |
|---|---|
| `python tests/run.py` | `scenario` PASS (56,3 s, Windows); smoke dilewati tanpa flag, sesuai desain |
| `python tools/e2e/e2e.py` | 137 passed, 0 failed, 1 skipped (delegated, opt-in `--full`) — Windows |
| `python tools/e2e/e2e.py --full` | 143 passed, 0 failed, 0 skipped (opencode `opencode/mimo-v2.6-flash-free`) — Windows |

Dua run `--full` sebelumnya gagal di `explore returns ok`: model `opencode/mimo-v2.5-free` sudah
dihapus opencode (`ProviderModelNotFoundError`). Sesudah katalog model diganti (lihat **Katalog
model**), `--full` lolos penuh. Salah satu run gagal itu juga mencatat `upgrade is exposed by the
CLI` rc=1 sekali; tidak muncul lagi di 4 reproduksi lokal maupun di run final — penyebabnya
belum diketahui.

## Breaking

- **Body write yang tak terbaca ditolak.** Write yang body-nya tak bisa dibaca `post_data`
  maupun `post_data_buffer` kini ditolak dengan reason `uninspectable_body`, termasuk di bawah
  `allow_side_effects`. Di 3.6.0 write itu dikirim dan hanya dilaporkan sebagai observation
  `write_uninspected`. Observation itu kini berarti laporan penolakan. Tak ada setting yang
  meloloskannya; upload yang wajib jalan dipindah ke `settings.existing_test_command`.
- **DELETE dan `_method` butuh persetujuan per endpoint.** DELETE, dan request apa pun yang
  membawa field `_method` (body, query, atau header `X-HTTP-Method-Override` /
  `X-Method-Override`), hanya dikirim bila endpoint-nya ada di
  `allowed_destructive_requests`. `allow_side_effects` dan izin write lokal tak menjangkaunya.
  Refusal: `destructive_unapproved` / `override_unapproved`.
- **Confidence dibatasi rasio anchor.** `digest.anchors = {certified, total}` kini ikut
  menentukan confidence (`contract.anchor_cap`): < 50% tersertifikasi → maksimal `low`;
  ≥ 50% → maksimal `medium`; nol tersertifikasi → maksimal `medium` (analisa lintas project
  wajar tak punya anchor lokal); semua tersertifikasi → tanpa batas. Alasannya masuk
  `digest.confidence_capped_by`. Di 3.6.0 rasio parsial hanya dilaporkan.
- **Rem pengulangan menghitung kegagalan preflight.** `playwright_missing`, `browser_missing`,
  dan `base_url_unreachable` dari preflight kini ikut streak. Run preflight yang menulis
  streak ketiga langsung ditolak `repeat_failure`; untuk hasil browser, run berikutnya yang
  ditolak.

## `/.verify-browser`: izin dan guard

- Write non-destruktif (POST/PUT/PATCH) ke host pengembangan lokal punya izin sendiri, terpisah
  dari `allow_side_effects`.
- Persetujuan endpoint dan izin tersimpan per project; `blocked_requests` milik project,
  fail-closed, tak bisa ditambah atau dikurangi dari settings request. Entri yang rusak
  menolak run (`request_invalid`) sambil menyebut entrinya.
- Endpoint dicocokkan sebagai route template (`:id` untuk satu segmen).
- Deteksi `_method` membaca body, query, dan header pada tiap bentuk decoding: percent
  (termasuk berlapis), escape JSON, envelope multipart, dan body UTF-16.
- Redirect ikut dinilai guard; navigasi yang diblok dilaporkan.
- DELETE yang diblok memunculkan `destructive_pending` untuk ditanyakan ke user, bukan hanya
  ditolak.

## Rem pengulangan lintas invocation

- Streak dikunci per origin dan per bucket (`timeout`, `harness`, `app`), bukan per reason
  persis, sehingga satu masalah dengan nama berganti tetap satu streak.
- Pengecekan rem pindah ke sesudah preflight, sehingga preflight yang lolos bisa
  membuktikan masalah lingkungan sudah hilang.
- Record menyimpan `source` (`preflight` / `browser`). Preflight yang lolos hanya menghapus
  streak yang ditulis preflight. Streak yang ditulis kedua gate menjadi `mixed` dan hanya
  hilang oleh run yang pass.
- Record yang sudah di batas tidak ditimpa oleh kegagalan dari bucket lain.
- `settings.ignore_repeat_brake` (default `false`) melewati rem untuk satu run tanpa
  menghabiskan streak-nya; nilainya tercatat di `meta.e2e.config`.

## Evidence

- Parsing anchor `file:line` sadar drive Windows dan dipakai bersama `fact_store` dan
  `contract`.
- Anchor yang tak bisa disertifikasi disimpan (bukan dibuang) tetapi tidak pernah membuat
  evidence layak dipakai ulang.

## Prompt dan lebar perubahan

- Batas panjang task mengikuti transport provider. Transport tanpa argv (file opencode, stdin
  codex) tak dibatasi secara default; `AI_PROXY_MAX_TASK_CHARS_NO_ARGV` memasang batas bagi
  yang mau (`task_cap_source: policy`).
- `meta.scope_width` melaporkan jumlah file dan baris yang berubah di working tree, file di luar
  scope plan, dan `wide` bila melewati 30 file atau 2000 baris.
- Warning panjang task di script runner hasil generate dihapus.

## Katalog model

- Pilihan opencode `opencode/mimo-v2.5-free` diganti `opencode/mimo-v2.6-flash-free`: model lama
  sudah dihapus opencode dan setiap call ke sana gagal `ProviderModelNotFoundError`. Sama
  seperti pendahulunya, model ini bernalar tanpa knob effort (`efforts: ()`).

## Alat rilis

- `stamp_version` mengecualikan catatan sejarah upgrade layout `data/` di `docs/reference.md`.

## Migrasi

- Record rem pengulangan dari build dev sebelum field `source` dibaca sebagai `mixed`, jadi
  preflight yang lolos tak menghapusnya. Build 3.6.0 tidak pernah menulis record ini. Bila
  sesi dev lama tertahan: buka sesi baru, atau set `ignore_repeat_brake` untuk satu run.
- Seed atau project yang sudah memilih `opencode/mimo-v2.5-free` tidak diubah otomatis. Pilih
  ulang: `install.py --apply --provider opencode --model opencode/mimo-v2.6-flash-free`, lalu
  sunting `.workflow/second_agent.json` di project yang sudah ada.
- Scenario yang meng-upload lewat browser dengan body yang tak terbaca: pindahkan ke
  `settings.existing_test_command`.
- Scenario yang memakai DELETE atau `_method`: daftarkan endpoint-nya di
  `allowed_destructive_requests`.

## Yang tidak diverifikasi

- Tak ada pengecekan otomatis bahwa isi `dist/config/claude/*` sesuai perilaku runtime.
  `gen_manifest` hanya meng-hash byte `dist/`.
- `--full` membuktikan `explore` dan `sweep` terhadap provider nyata, bukan tahap `draft` dan
  review `/.verify-browser`.
