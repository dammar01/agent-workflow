# Changelog — v3.4.5

Rilis stabilitas. Isinya perkakas yang membuat rilis berikutnya bisa dipercaya — CI,
suite test yang bisa dijalankan sepotong-sepotong, prosedur rilis tertulis — ditambah
provider ketiga dan satu gerbang yang membuat provider itu tidak bisa dipilih tanpa
sengaja. Beberapa item di bawah dibangun sebelum v3.4.4 terbit tetapi tidak pernah punya
nomor rilis sendiri; catatan ini yang memberi mereka satu.

Satu hal yang perlu dibaca sebagai peringatan, bukan sebagai fitur: **memilih provider agy
sekarang gagal sampai sebuah variabel lingkungan dipasang.** Rinciannya di bagian pertama.

## Provider agy, dan gerbang opt-in yang menyertainya

`adapters/agy_adapter.py`, bundelnya di `config/providers.py`, dan `core/agy_guard.py`
menambahkan provider ketiga. Berbeda dari dua yang lain, agy **tidak menegakkan boundary
read-only apa pun**. Baik `--sandbox` maupun `--mode plan` sudah diprobe terhadap binary
yang terpasang dan keduanya meninggalkan 56 tool aktif dengan `permission_mode:
always-proceed`, `write_to_file` dan `run_command` di antaranya. Menghapus
`--dangerously-skip-permissions` menghasilkan `request-review`, yang menolak setiap tool
termasuk baca — menyisakan second_agent yang tak bisa mengumpulkan evidence sama sekali.

Jadi adapter mengambil sisi permisif dan memasangkannya dengan `core/agy_guard.py`, yang
mendiff working tree di sekitar tiap panggilan. Itu **mendeteksi** tulisan, bukan
mencegahnya.

Provider dengan sifat seperti itu tetap boleh dipilih, tapi tidak secara diam-diam:

- `config/providers.py` kini menyatakan `requires_opt_in` pada bundel yang tak punya
  boundary. Kunci itu menandai pengecualian — bundel yang memegang kontraknya tidak
  menyebutnya sama sekali.
- `/.provider agy|<model>` **ditolak** kecuali `AI_PROXY_AGY_OPT_IN` terpasang di
  lingkungan. Penolakan menulis nol byte: `second_agent.json` yang menunjuk ke
  second_agent tanpa batas, sementara perintahnya melaporkan telah menolak, adalah satu
  keadaan yang tak boleh ditinggalkan.
- Katalog `/.provider` membawa `requires_opt_in` dan `opt_in_granted` per provider, supaya
  picker tidak menawarkan agy seolah setara lalu gagal sedetik kemudian.

**Batas yang disengaja:** gerbang ini menjaga pintu masuk, bukan ruangannya.
`second_agent.json` yang **sudah** berisi `agy` tetap berjalan tanpa pernah bertemu
gerbang — `adapters/registry.py:selected_provider` hanya membaca berkas itu kembali, dan
menolak di sana akan mematikan `doctor` justru pada saat tugasnya adalah melaporkan
provider mana yang terpasang. Workspace agy yang sudah ada perlu ditinjau tangan.

`AI_PROXY_PROVIDER=agy` di shell juga melewati gerbang ini. Itu memang pilihan sadar yang
diketik seseorang, bukan kecelakaan konfigurasi.

## Kontrak `/.execute` diperketat

`commands.auto_verify_after_execute` selalu prompt-only: `/.execute` dikerjakan main_agent
yang mengedit berkas langsung, jadi tak ada proses Python yang hidup untuk menegakkan
apa pun. Itu tidak berubah di rilis ini, dan tidak bisa diubah tanpa memberi `/.execute`
sebuah entry point yang ia tak punya. Yang berubah: kontraknya berhenti bergantung pada
pembacaan yang longgar.

`dist/config/claude/skills/execute.md` dan `dist/config/claude/CLAUDE.md` kini menyebut
kuncinya harus dibaca ulang dari `.workflow/config.json` tiap kali (bukan dari ingatan,
bukan dari sesi lain), menyatakan bahwa chain ke `/.verify` saat kuncinya `true` adalah
bagian dari `/.execute` alih-alih langkah opsional sesudahnya, dan mendaftar kata yang
dilarang saat `verification: not_run` — "done", "selesai", "berhasil", "sudah jalan",
"test lolos". Pembedaannya bukan gaya bahasa: user memutuskan langkah berikutnya dari
kata itu.

Ini penguatan kontrak, **bukan** penegakan. Tak ada exit code, marker, atau gerbang baru
di belakangnya, karena tak ada tempat untuk memasangnya.

## Pemilihan provider interaktif

`/.provider` dan `core/provider_select.py`. Satu string masuk, setiap keputusan tentang
apakah string itu boleh ditulis terjadi di kode yang bisa dijalankan
`tests/checks/provider.py` — bukan di model yang mengedit `second_agent.json` dengan
tangan. Satu apply menulis lima kunci sekaligus beserta `routes`, karena `provider`
sendirian meninggalkan binary dan persona provider lama di disk.

## Continuation tak lagi membuang balasan yang dilanjutkannya

second_agent yang berhenti sebelum `[DIGEST]` diminta blok yang hilang, lalu balasan
lanjutan itu **menggantikan balasan pertama seutuhnya** — badan evidence dan seluruh
anchor terbuang, sementara run tetap melaporkan `ok:true` dan
`continuation_recovered:true`. Terlihat pada 3 dari 4 panggilan delegated.

`_merge_continuation()` di `core/executor.py` menggabungkan keduanya, memotong di penanda
`[DIGEST]` **pertama** milik balasan pertama. Percobaan awal memotong di penanda terakhir
dan gagal di lapangan: balasan yang mengutip template membawa dua penanda, yang terkutip
tetap berada di depan, dan gabungannya ditolak diam-diam. Kedua kasus kini punya test.

## Perbaikan jalur gagal

- **`await` tak bisa menggantung selamanya.** `poll_timeout=0` (default) meninggalkan loop
  tunggu tanpa jalan keluar ketika job berhenti maju tanpa mencapai status terminal.
  Sekarang jatuh ke plafon runtime job itu sendiri.
- **Command gagal keluar nonzero.** `_verify_exit_code` mengembalikan 0 untuk tiap command
  non-verify berapa pun nilai `ok`, sehingga `explore` yang gagal tak bisa dibedakan dari
  yang bersih oleh caller mana pun yang membaca exit status.
- **Worker yang mati mengembalikan error terstruktur.** `run_worker` sebelumnya
  mengembalikan dict polos tanpa `error_type` dan tanpa `next_action`.
- **`dist/config/agy/AGENTS.md`.** Bundel agy mendeklarasikan berkas instruksi yang tak
  pernah ditulis, jadi memasang agy tidak mengirim kontrak second_agent sama sekali —
  untuk satu-satunya provider yang boundary bacanya cuma prosa.

## Anchor relocation

Fact dan evidence bertahan ketika baris yang mereka tunjuk bergeser, alih-alih dianggap
basi karena nomor barisnya berubah.

## Test dan CI

- **Test pindah ke `tests/`.** `test_scenario.py` → `tests/scenario.py`, `checks/` →
  `tests/checks/`, plus `tests/run.py` dengan registry suite: `--list`, `--only <name>`,
  `--keep-going`. Suite scenario tetap satu urutan karena assertion-nya memang berbagi
  state; empat belas check mandiri masing-masing membangun workspace sendiri dan kini bisa
  dijalankan sendirian. `tests/` bukan `test/` karena paket `test` di tingkat atas
  membayangi milik CPython.
- **`tests/checks/registry.py`** memastikan tiap check terjangkau dari kedua entry point.
  Check yang ditulis tetapi tak pernah didaftarkan adalah test yang hijau tanpa pernah
  dijalankan.
- **CI.** `.github/workflows/ci.yml` menjalankan gerbang stamp versi, manifest, test, dan
  e2e di Linux dan Windows pada tiap push dan pull request. Dua di antaranya merah di
  branch default saat workflow ini pertama ditambahkan — sebuah target stamp yang tak
  pernah dibuat dan manifest yang basi — dan itulah seluruh argumen keberadaannya.
  `.github/workflows/e2e-full.yml` menjalankan jalur delegated berbayar pada dispatch
  manual saja, terhadap self-hosted runner yang punya CLI provider terpasang.

## Prosedur rilis

`RELEASE.md` menuliskan urutannya: bump satu sumber, stamp, manifest, catatan, test, tag,
lalu verifikasi tag itu bereproduksi dari checkout bersih. Tiap langkah adalah perintah
yang sudah ada di repo; berkas ini hanya memperbaiki urutannya dan membuat langkah yang
dilewati jadi terlihat.

## Yang belum ditutup

- **Gerbang agy menjaga pintu masuk, bukan ruangan.** Lihat bagian pertama.
  `second_agent.json` yang sudah menunjuk agy tidak akan pernah bertemu gerbang.
- **`auto_verify_after_execute` tetap tak bisa ditegakkan.** Kontraknya diperketat; tak ada
  yang menegakkannya selain agen yang membacanya.
- **Reproducible build berhenti di hash manifest.** Repo tak punya lock file karena tak
  punya dependensi pihak ketiga. Reproducible di sini berarti "byte `dist/` yang sama untuk
  tag yang sama", diverifikasi di langkah 7 `RELEASE.md` — bukan toolchain yang dipin.
  Versi Python dipin hanya di CI.
- **Token second_agent masih estimasi** `chars//4` di `call.meta.json`, dengan
  `token_source="estimated"`. Cukup untuk perbandingan kasar, tidak untuk akuntansi.
- **`python tools/e2e.py --full` TIDAK dijalankan untuk rilis ini.** `tests/run.py` dan
  `tools/e2e.py` lolos (92 passed, 0 failed, 1 skipped), tetapi satu-satunya langkah yang
  melatih panggilan delegated sungguhan dari ujung ke ujung dilewati karena menghabiskan
  kuota berbayar. Rilis ini belum pernah dijalankan terhadap provider hidup. Dicatat di
  sini alih-alih dibiarkan tersirat, sesuai langkah 5 `RELEASE.md`.
