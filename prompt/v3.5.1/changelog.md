# Changelog — v3.5.1

Rilis kecil dengan satu tema: berhenti memakai satu angka untuk menjawab pertanyaan yang
punya jawaban berbeda per provider, dan berhenti menelan kegagalan yang justru dibutuhkan
untuk menjawabnya.

## Status rilis

**Di-tag `v3.5.1`.** Reproduksi dikonfirmasi dari checkout bersih tag: `stamp_version --check`,
`gen_manifest --check`, dan `tests/run.py` lolos dengan nol perubahan working tree. `v3.5.0`
ikut di-tag di `965b967` pada saat yang sama — utang tag yang menumpuk sejak rilis lalu.

**Belum di-push.** Kedua tag baru ada di repo lokal.

**`tools/e2e/e2e.py --full` belum dijalankan.** Nol panggilan provider sungguhan berdiri di
belakang rilis ini. Yang hijau: `python tests/run.py` (suite scenario penuh).

## Task cap diturunkan dari transport, bukan dari satu konstanta

`DEFAULT_MAX_TASK_CHARS = 3000` dipakai untuk semua provider dan semua command. Angka itu
dipilih untuk transport yang paling sempit, jadi ia salah di dua arah sekaligus:

- **codex** mengirim prompt lewat **stdin** dan tak pernah menyentuh argv, tapi tetap
  membayar pajak batas command line yang tak berlaku baginya.
- **opencode** menyerialkan prompt ke **argv** dan mengukur seluruh command line terhadap
  8191 — tapi sisa ruangnya bukan konstanta: prompt `verify` membawa tabel routing dan blok
  changed-files yang tak dibawa `explore`.

Sekarang cap dihitung dua-lintasan di `build_prompt()`: lintasan pertama merakit prompt
dengan task kosong untuk **mengukur** scaffolding-nya, lintasan kedua mengirim. Blok
changed-files dihitung sekali dan diteruskan, jadi pengukuran tak membayar `git` dua kali
dan tak bisa mendeskripsikan prompt yang berbeda dari yang dikirim.

Yang diukur adalah biaya **command line**, bukan panjang string Python. opencode menulis
ulang tiap newline jadi ` 
 ` sebelum menaruhnya di argv, jadi satu newline berharga empat
karakter di sana dan satu di sini. Versi pertama perbaikan ini mengukur `len()` mentah dan
lolos aritmetikanya sendiri sambil menyerahkan prompt yang ditolak adapter — 8444 karakter
terhadap ambang 7791, dan hanya untuk task padat-newline, yaitu justru instruksi
banyak-poin yang paling butuh ruang tambahan. Batas yang dipakai sekarang adalah ambang
adapter (`_CMD_LINE_LIMIT - _CMD_LINE_HEADROOM`), bukan batas OS di atasnya, dan potongan
task dicari lewat binary search terhadap biaya nyata prefiksnya.

Hasil pada repo ini (task pendek, tanpa newline):

| command | opencode | agy | codex |
|---------|---------:|----:|------:|
| explore | 4568 | 28706 | 3000 |
| plan    | 4394 | 28532 | 3000 |
| verify  | 1918 | 26146 | 3000 |
| sweep   | 4570 | 28708 | 3000 |

Task padat-newline dapat cap lebih kecil, sesuai biayanya di argv. Slack terburuk yang
terukur lintas empat command x empat bentuk task: 603 karakter (opencode), 605 (agy).

Penurunan dari transport hanya berlaku di **Windows**. Ceiling 8191 dan 32767 itu milik
`cmd.exe` dan `CreateProcess`; kedua adapter memang menolak menegakkannya di luar Windows
(`_too_long_for_cmd` mengembalikan `None` di POSIX). Menurunkan budget dari batas yang tak
ditegakkan di sana murni kerugian — ia memotong cap `verify` dari 3000 jadi 1918 di Linux
demi batas yang Linux tak punya. Di luar Windows, cap kembali ke `policy`.

Sisa argv di luar prompt **diukur dari route**, bukan ditebak dengan angka tetap. Nilainya
adalah `provider_command`, `provider_agent`, `model`, `effort`, dan session id yang sedang
dipegang route itu, dijumlah dengan akuntansi `len(v)+3` yang sama dengan `_too_long_for_cmd`
milik adapter. Untuk konfigurasi normal itu 216 karakter (opencode) dan 295 (agy) — jauh di
bawah tebakan tetap 512 yang dipakai versi pertama. Tapi tebakan itu gagal ke arah yang
salah: model id 1000 karakter plus path command absolut mendorong command line ke 8352,
lewat ambang 7791, dan sizing tak melihatnya karena gerbang rasio truncation membaca angka
keliru yang sama. Sekarang cap ikut mengecil dan panggilan tetap muat.

Prompt continuation juga dibatasi. Ia mengutip balasan yang gagal, jadi ukurannya ditentukan
provider bukan runtime — satu tag rusak bisa membawa satu paragraf. Empat di antaranya
menghasilkan command line 20410 karakter, dan yang ditolak adalah panggilan pemulihan itu
sendiri: stall yang sebetulnya bisa diselamatkan berubah jadi hilang. Detail `missing`
dipotong di 800 karakter, dengan jumlah sisa disebut agar potongannya tak senyap.

Saat scaffolding sendiri hampir memenuhi transport, `task_cap_source` berbunyi `floor`
alih-alih `transport` — angka 500 di situ bukan ruang yang tersedia, dan menyebutnya
`transport` akan menyembunyikan prompt yang memang tak muat di balik cap yang terlihat
masuk akal. Backstop adapter yang menolaknya.

Tabel transport ada di `config/providers.py` (`PROVIDER_TRANSPORT`), **bukan** di
`.workflow/second_agent.json`. Angka-angka itu properti OS dan biner provider, bukan
preferensi: user yang menyunting 8191 sedang menyunting fakta, dan salah isi berarti
invocation hilang sambil terbaca seperti kesalahan config.

Nol key config baru. Provider tak dikenal jatuh ke cap statis — perilaku yang persis sama
dengan sebelum tabel ini ada. Backstop `_too_long_for_cmd` tiap adapter tak disentuh: cap
ini optimasi, pemeriksa terakhir tetap yang tahu bentuk argv sebenarnya.

Nilai efektif dilaporkan di `meta.task_cap` dan `meta.task_cap_source`
(`transport` | `policy` | `floor`), plus `meta.prompt_overhead_chars` saat diturunkan
dari transport. Dilaporkan pada semua respons yang berhasil — termasuk `verify` dan
`sweep`, yang role-nya tak pernah masuk cabang evidence, dan termasuk panggilan bersih,
justru satu-satunya kasus di mana angka itu tak bisa disimpulkan dari hal lain.
Panggilan yang gagal kembali lebih awal dan tetap membawanya di `call.meta.json` saja.

## Kegagalan tulis stdin tak lagi bisu

`CodexAdapter._popen_capture()` membungkus penulisan prompt ke stdin dengan
`except Exception: pass`. Ini satu-satunya mode gagal yang tak bisa dimiliki provider argv:
prosesnya sudah jalan, lalu penulisan gagal ke pipe yang anaknya sudah tutup — biasanya
karena input ditolak — dan runtime lanjut menunggu proses yang tak pernah diberi
pertanyaannya. Yang tercatat cuma output kosong.

Sekarang penyebabnya direkam: `call_meta.prompt_chars` selalu, dan
`call_meta.stdin_write_failed` berisi tipe plus pesan exception saat gagal.

Keduanya ikut ke dict yang dirakit SETELAH proses selesai, bukan ditulis saat
diketahui: `last_call_meta` di-rebind utuh di akhir `_popen_capture()`, jadi versi
pertama perbaikan ini menulis ke objek yang sedetik kemudian dibuang. Instrumentasi
untuk kebisuan yang sendirinya bisu. Test `adapters-stdin` mengunci bentuk itu.

Ini prasyarat, bukan pelengkap. Plafon stdin **sengaja tidak dinaikkan** di rilis ini —
codex tetap 3000 — karena menaikkannya sekarang berarti menebak dengan alat ukur rusak.
Angka yang lebih baik menunggu data dari field ini.

## Perbaikan digest CRLF

`_DIGEST_HEADER` memakai kelas `[ \t]*` sebelum `$`. Di mode `MULTILINE`, `$` hanya cocok
tepat sebelum `\n`, jadi `\r` pada balasan CRLF menghalangi dan digest yang sah terbaca
sebagai **hilang** — memicu continuation yang tak perlu. `_SECTION_HEAD` di file yang sama
sudah toleran lewat `\s`; ini saudaranya yang tidak. Kelasnya kini `[ \t\r]*`.

## Konsekuensi jujur

Provider default saat ini (codex) **tidak** mendapat headroom dari rilis ini. Hadiahnya
jatuh ke opencode dan agy. Yang codex dapat adalah kemampuan melapor saat gagal — yang
justru syarat agar plafonnya bisa dinaikkan nanti dengan angka, bukan firasat.

Peringatan ukuran task di skrip `run.ps1`/`run.sh` yang di-generate kini berbunyi "may be
truncated (exact cap depends on the provider transport)". Skrip lama tetap jalan tanpa
regenerasi — peringatan itu advisory, penegakan ada di runtime. Salinan `CLAUDE.md` yang
sudah ter-install perlu `/.upgrade` untuk ikut berubah.
