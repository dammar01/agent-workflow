# Changelog — v3.5.3

v3.5.2 menutup pengukuran token untuk codex dan meninggalkan opencode di `estimated`,
dengan alasan yang ditulis terbuka: opencode memang melaporkan usage, tetapi hanya di
`--format json`, bentuknya berbeda, dan satu pertanyaan aritmetika belum terjawab. Rilis ini
menjawab pertanyaan itu dengan pengukuran, lalu mengirim angkanya sampai ke badge.

## Status rilis

**Belum di-tag.** Nomor sudah di-stamp dan manifest sudah regenerasi; `stamp_version --check`
dan `gen_manifest --check` lolos.

**`tools/e2e/e2e.py --full` belum dijalankan.** Yang menggantikannya bukan nol: dua delegated
`explore` sungguhan dijalankan terhadap opencode di project sandbox terpisah selama
pengembangan — satu dengan model non-reasoning, satu dengan model reasoning — dan baris
`usage.jsonl` dari keduanya adalah bukti yang dikutip di bawah. Ditambah lima run
`opencode run --format json` langsung terhadap biner untuk membekukan bentuk stream.

## Pertanyaan yang v3.5.2 tinggalkan, dan jawabannya

v3.5.2 menemukan `input + output + cache.read` berjumlah persis ke `total`, lalu berhenti:
run yang diperiksa melaporkan `reasoning: 0`, dan satu angka nol tak bisa membedakan
"reasoning ada di dalam output" dari "reasoning addend terpisah". Menebak di situ berarti
menulis angka salah dengan percaya diri.

Run terhadap model reasoning menjawabnya:

```
{"total":16418,"input":16353,"output":27,"reasoning":38,"cache":{"write":0,"read":0}}
```

`16353 + 27 + 0 = 16380`, bukan `16418`. Selisihnya persis `38`. Jadi di opencode,
**`reasoning` adalah addend terpisah, bukan rincian dari `output`** — kebalikan dari aturan
yang dipegang `adapters/shared/usage.py` dan dipatuhi codex.

Konsekuensinya bukan "tulis apa adanya". Setiap lapisan di atas — `billable_input`/
`billable_output`, telemetry, statusline — membaca `reasoning` sebagai rincian dan sengaja
tidak menjumlahkannya lagi. Maka pelipatan dilakukan di adapter, tempat aritmetika provider
ini diketahui:

```
input_tokens         = input + cache.read
cached_input_tokens  = cache.read
output_tokens        = output + reasoning
reasoning_tokens     = reasoning
```

`adapters/shared/usage.py` tidak disentuh. Aturannya tetap benar untuk provider yang memang
mematuhinya; yang salah adalah menganggap semua provider mematuhinya.

## Delta, bukan cumulative

Satu run dua langkah melaporkan `output: 79` lalu `output: 6`. Penghitung kumulatif tak bisa
turun. Tiap `step_finish` adalah satu request yang ditagih terpisah, jadi biaya run adalah
**jumlah** semua step — pembacaan yang sama yang sudah dipakai codex untuk `turn.completed`.

## Jebakan yang hampir membunuh fitur ini

`--format json` **menggantung** bila stdin proses dibiarkan terbuka dan menganggur. Bukan
lambat — berhenti: log terakhir yang tercetak adalah `init`, lalu nol byte stdout sampai
timeout membunuhnya. Argv yang sama persis dengan stdin di `/dev/null` selesai di bawah dua
detik.

Mode default tak pernah menunjukkan ini, jadi seluruh ongkos kelalaian itu jatuh tepat di
mode yang sekarang dipakai adapter. `stdin=subprocess.DEVNULL` di `_popen_capture` adalah
satu baris yang, bila hilang, membuat setiap panggilan opencode menggantung sampai timeout.

## Jawaban pindah tempat

Beralih ke `--format json` memindahkan jawaban dari teks polos ke event `type:"text"` —
seam yang v3.5.2 sudah tandai: bila salah, bukan metrik yang hilang, melainkan jawabannya.

`clean_output` sekarang menyusun jawaban dari `part.text`, dikunci per id part dengan nilai
terakhir yang menang, sehingga build yang menstream satu part bertahap menghasilkan teks
jadi, bukan setiap awalannya. Jalur teks polos tetap ada — bootstrap masih memakainya, dan
setiap tail error melewatinya.

Satu keputusan di sini layak disebut karena versi pertamanya salah. Fallback sempat membuang
**setiap** baris yang parse sebagai JSON ber-`type`. Itu memperbaiki stream event dan merusak
hal lain: body `[EVIDENCE]` yang kebetulan mengutip sebuah objek JSON kehilangan baris itu —
diam-diam, dengan run tetap dilaporkan sukses. Keputusannya sekarang diambil per-stream lewat
daftar tipe event yang disebut eksplisit (`_EVENT_TYPES`), bukan per-baris lewat "kelihatan
seperti JSON". Test regresinya ada di `tests/checks/usage_tokens.py`.

## Buktinya

Satu delegated `explore` nyata terhadap opencode, dibandingkan dengan estimasi yang akan
ditulis baris yang sama sebelum rilis ini:

| | estimasi lama | terukur | selisih |
|---|---|---|---|
| input | 667 | 54.393 | 81× |
| output | 479 | 1.211 | 2,5× |
| cached input | — | 49.792 | tak terlihat sama sekali |

Baris yang ditulis:

```json
{"session_id":"main_sandbox_e2e_1","provider":"opencode","token_source":"provider",
 "estimated_input_tokens":667,"estimated_output_tokens":479,
 "actual_input_tokens":54393,"actual_output_tokens":1211,
 "actual_reasoning_tokens":0,"actual_cached_input_tokens":49792}
```

Run kedua dengan model reasoning menutup jalur yang tersisa:

```json
{"session_id":"main_sandbox_reason_1","token_source":"provider",
 "actual_input_tokens":112892,"actual_output_tokens":1296,
 "actual_reasoning_tokens":648,"actual_cached_input_tokens":56160}
```

Dan badge yang membacanya, dua flavor, angka identik:

```
sandbox-proj | Second Agent 55.6k tok (49.8k cached) / 1 calls | Saved 4.6k tok
sandbox-proj | Second Agent 114.2k tok (56.2k cached) / 1 calls | Saved 57.4k tok
```

Nol penanda `~` di keduanya — itu yang membedakannya dari setiap baris opencode sebelum
rilis ini. Baris yang tetap tak terukur masih merender `~`, dan itu diuji.

## Cakupan

**opencode**: terukur penuh, terverifikasi terhadap biner sungguhan.

**codex**: tak disentuh. Jalurnya tetap `turn.completed.usage` lewat normalizer bersama.

**agy**: tetap tak dikerjakan, keputusan scope yang sama seperti v3.5.2.

**bootstrap opencode**: sengaja tetap di luar hitungan. `last_call_meta` ditimpa oleh capture
berikutnya, jadi parser yang terbatas pada agent-call tidak mengukur bootstrap. Menghitungnya
berarti melebarkan scope dari parser menjadi accounting lintas-invocation; itu bukan rilis ini.

## Yang tidak diverifikasi

Build `opencode` yang tak mengenal `--format json`. Tak ada fallback otomatis: argv yang
ditolak jatuh ke jalur returncode bukan-nol, bukan ke mode teks. Tak dapat diuji tanpa biner
versi lama.
