# Changelog — v3.5.2

Satu tema: berhenti menyebut tebakan sebagai pengukuran. Metrik usage runtime tak pernah
menyentuh angka token yang dilaporkan provider — ia membagi jumlah karakter dengan empat
dan menuliskannya ke `usage.jsonl`. Rilis ini membuat angka yang benar-benar diukur bisa
masuk, dan membuat baris yang tetap tebakan mengatakannya dengan jujur.

## Status rilis

**Belum di-tag.** Nomor sudah di-stamp dan manifest sudah regenerasi; `stamp_version --check`
dan `gen_manifest --check` lolos.

**`tools/e2e/e2e.py --full` belum dijalankan.** Tapi berbeda dari v3.5.1, rilis ini
**tidak** berdiri di belakang nol panggilan provider: satu delegated `explore` sungguhan
dijalankan terhadap codex selama pengembangan, dan barisnya di `usage.jsonl` adalah bukti
yang dikutip di bawah.

## Yang salah

`chars//4` bukan sekadar estimasi kasar dari besaran yang benar. Ia besaran yang **berbeda**.

Estimasi lama mengukur teks yang tiba: prompt yang dikirim, dan jawaban akhir yang dibaca.
Yang dihitung provider adalah seluruh yang dikirim dan dihasilkan model — termasuk reasoning
yang tak pernah tampil, dan konteks ter-cache yang tak pernah diketik siapa pun.

Selisihnya, dari satu panggilan `explore` nyata terhadap codex:

| | estimasi lama | terukur | selisih |
|---|---|---|---|
| input | 554 | 791.937 | 1.430× |
| output | 243 | 1.695 | 7× |
| reasoning | — | 866 | tak pernah terlihat |
| cached input | — | 637.696 | tak pernah terlihat |

Angka input melonjak karena 80% dari padanya adalah cache read yang `chars//4` tak punya
cara melihat. Angka output tujuh kali lipat karena model menghasilkan reasoning yang tak
pernah masuk ke `content`.

## Aturan yang menentukan bentuk seluruh perubahan

**Reasoning ada DI DALAM output. Cached ada DI DALAM input.** Kedua API provider besar
melaporkannya begitu: sebuah rincian `*_details` yang menempel pada total di sebelahnya,
bukan pos yang berdiri sendiri untuk dijumlahkan.

Lapis mana pun yang dengan niat baik menjumlahkan semua field yang ia temukan akan
menghasilkan tagihan yang **membesar setiap kali model berpikir lebih keras untuk jawaban
yang sama**. Kegagalan itu senyap — angkanya tetap terlihat seperti angka. Karena itu
aturannya ditegaskan di setiap lapis yang bisa melanggarnya, bukan sekali di puncak, dan
test-nya memeriksa `reasoning < output` justru karena reasoning yang melebihi output adalah
tanda tangan penjumlahan yang salah.

## Yang berubah

### Pembaca usage bersama (`adapters/shared/usage.py`, baru)

`normalize_usage` menerima beberapa ejaan (`input_tokens`/`prompt_tokens`,
`output_tokens`/`completion_tokens`, reasoning di top level maupun di dalam
`*_details`) dan mereduksinya jadi empat angka. Alias-driven, bukan dipatok ke satu schema,
karena repo ini tak bisa membuktikan ejaan mana yang dipancarkan build tertentu — fixture
yang dimilikinya hanya menunjukkan `output_tokens`.

Kunci yang tak dikenali menghasilkan **pengukuran yang hilang**, sesuatu yang sudah bisa
dikatakan telemetry. Kunci yang ditebak akan menghasilkan **angka yang salah**, yang tidak
bisa.

`merge_usage` menjumlahkan lintas turn — satu `adapter.run` bisa mencakup beberapa turn, dan
menyimpan yang terakhir saja melaporkan sebagian tagihan sebagai keseluruhannya. Penjumlahan
benar **di sana**, di mana kedua sisi adalah field yang sama dari turn berbeda, dan salah
**lintas field**.

### `UsageRecord` (`core/evidence/contracts.py`)

Lima field: `actual_input_tokens`, `actual_output_tokens`, `actual_reasoning_tokens`,
`actual_cached_input_tokens`, `provider_call_index`. `CONTRACT_VERSION` naik ke 2.

Estimasi lama **tetap ditulis berdampingan**. Tanpanya tak ada pembanding yang menunjukkan
seberapa meleset `chars//4` — tabel di atas tak akan bisa disusun.

`billable_input`/`billable_output` memilih angka terukur bila ada, estimasi bila tidak.
Keduanya menolak menjumlahkan rincian.

### `token_source` punya tiga nilai

`estimated`, `provider`, dan `mixed`. Yang ketiga bukan teori: fixture codex yang ada di repo
ini hanya membawa `output_tokens`, dan baris seperti itu tak jujur disebut `provider` maupun
`estimated` — separuhnya salah dideskripsikan oleh keduanya.

### Satu baris per panggilan provider

Continuation menjalankan adapter dua kali. Sekarang keduanya tercatat, dibedakan oleh
`provider_call_index`.

Penulisannya **tetap dari satu tempat**. Memindahkan `_record_usage` ke dalam provider flow
akan kehilangan reuse hit — satu-satunya baris di stream yang membuktikan sebuah delegated
call bisa berbiaya nol, dan justru baris yang paling murah yang akan hilang, sehingga biaya
per task melebih-lebihkan dirinya sendiri.

`adapter.last_call_meta` adalah satu atribut yang di-rebind utuh tiap run, jadi panggilan
kedua menghancurkan angka panggilan pertama. `_snapshot_invocation` menyalinnya keluar di
antara keduanya. Adapter membawa peringatan yang sama tentang metadata-nya sendiri
(`codex_adapter.py:772`) karena alasan yang persis sama — ini bahaya itu satu lapis di atas.

### Konsumen

`telemetry._cost` dan `governance.budget_state` membebankan tiap baris menurut apa yang
baris itu benar-benar tahu. `reasoning_tokens_within_output` dilaporkan **di samping** total,
tak pernah di dalamnya. History yang tak punya reasoning terukur melaporkan `None`, bukan
nol: tak ada provider yang memberitahu bukan hal yang sama dengan tak ada yang dihabiskan.

`report()["calls"]` sekarang berarti panggilan provider, bukan command. `commands`
ditambahkan supaya makna lamanya tak perlu disimpulkan dari angka yang definisinya bergeser
di bawahnya.

## Dua bug yang ditemukan saat mengerjakannya

**Ekspansi memakan keluarannya sendiri.** Menyimpan baris hasil ekspansi ke atribut yang
menampung snapshot mentah membuat pass kedua mengekspansi hasil pass pertama, mengosongkan
setiap hitungan di dalamnya.

**Penghematan konteks jatuh ke nol pada continuation.** `premium_context_avoided_tokens`
diukur pada baris terakhir, yang membawa `response_chars` milik retry saja — 100 karakter —
sementara digest dibandingkan terhadapnya. Padahal yang diukur adalah jawaban **hasil merge**
yang main_agent tak perlu baca. Yang kedua lebih berbahaya: ia menghapus angka pembenaran
kontrak digest-first tanpa satu pun error. `final_response_chars` menutupnya, dan test
menguncinya.

## Cakupan

**codex**: terukur penuh, terverifikasi terhadap biner sungguhan.

**opencode**: tetap `estimated`. Pemeriksaan langsung menemukan bahwa opencode **memang**
melaporkan usage, tetapi hanya di `--format json` yang tak pernah dikirim adapter, dan
bentuknya berbeda dari yang dibaca normalizer sekarang (`part.tokens` di event
`step_finish`, dengan kunci `input`/`output`/`reasoning` polos dan `cache.read` bersarang).

Lebih penting: pada satu run yang diperiksa, `input + output + cache.read` berjumlah persis
ke `total` — yang berarti `input` opencode adalah input segar saja dan cache berdiri
terpisah, **kebalikan** dari konvensi codex. Apakah `reasoning` juga addend terpisah di sana
belum terjawab; run yang diperiksa melaporkan `reasoning: 0` dan satu angka nol tak bisa
membedakan kedua kemungkinan. Menerapkan aturan codex ke opencode tanpa menjawab itu akan
menghasilkan angka yang salah dengan percaya diri, jadi tidak dilakukan.

Beralih ke `--format json` juga berarti menulis ulang `clean_output` opencode: jawabannya
pindah dari teks polos ke event `type:"text"`. Itu seam yang bila salah bukan cuma metrik
yang hilang, melainkan jawabannya.

**agy**: tak dikerjakan, keputusan scope.

## Diketahui, tak diubah

- `provider` pada baris usage masih `None`. `call_meta` tak pernah menetapkan identitas
  provider (`core/evidence/contracts.py:330`) — bug lama, kini lebih terasa karena barisnya
  punya angka nyata tapi tak menyebut siapa yang melaporkannya.
- `bench/collect.py:205` membaca `call.meta.json` lewat jalur terpisah dan tak ikut berubah,
  jadi bench dan `--command report` bisa melaporkan angka berbeda.
- Cache read dijumlahkan penuh ke input oleh `_cost`, tanpa membedakan harganya. Konsisten
  dengan desain yang sengaja tak punya price table; datanya kini ada di baris bila
  pemisahan harga dibutuhkan nanti.
- Bila `session_token_budget` di-set, angkanya baru saja berubah sekitar seribu kali lipat.
  Ceiling yang masuk akal untuk `chars//4` akan menggigit di panggilan pertama. Spend-nya
  tidak bertambah; penutup matanya yang dilepas.
