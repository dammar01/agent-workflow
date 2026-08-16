# Changelog — v3.4.5

Rilis stabilitas, ditambah lapisan pengukuran. Bagian pertama isinya perkakas yang membuat
rilis berikutnya bisa dipercaya — CI, suite test yang bisa dijalankan sepotong-sepotong,
prosedur rilis tertulis — ditambah provider ketiga dan satu gerbang yang membuat provider
itu tidak bisa dipilih tanpa sengaja. Bagian kedua menambahkan empat subsistem yang
sebelumnya tidak ada sama sekali: kontrak workflow bertipe, telemetry, governance, dan
provenance graph. Beberapa item di bawah dibangun sebelum v3.4.4 terbit tetapi tidak
pernah punya nomor rilis sendiri; catatan ini yang memberi mereka satu.

Empat subsistem baru itu **aditif**: `make_ok`/`make_error` tetap mengembalikan dict polos
yang sama, jadi tidak ada adapter, call site, atau fixture yang berubah bentuk. Yang
berubah perilakunya cuma satu, dan itu ada di bagian "Perbaikan jalur gagal": command
gagal kini keluar nonzero.

Batas dari yang diukur lapisan baru ini ditulis di bagian "Yang belum ditutup", bukan
tersirat. Satu di antaranya cukup berat untuk dibaca sebelum siapa pun memakai angkanya.

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

## Kontrak workflow

`core/contracts.py` menamai lima struktur yang selama ini mengalir sebagai dict anonim:
`TaskSpec`, `RouteDecision`, `EvidenceBundle`, `VerificationReport`, `UsageRecord` —
dataclass stdlib dengan `to_dict()`/`from_dict()`.

Aditif dengan sengaja. `make_ok`/`make_error` tetap mengembalikan bentuk yang sama, jadi
tidak ada satu pun call site yang perlu berubah. Yang didapat bukan validasi runtime
melainkan tempat: `RouteDecision` mencerminkan `Router.route()` kunci per kunci, dan kunci
yang ditambahkan di satu ujung sebelumnya tak punya cara mengumumkan dirinya di ujung yang
lain — dict itu dikonsumsi lapangan demi lapangan sepanjang ~180 baris `executor.execute()`.

`from_dict` membuang kunci yang tak dikenal alih-alih meledak, supaya baris lama di stream
tetap terbaca setelah sebuah field ditambahkan.

## Telemetry

`core/telemetry.py` dan `python main.py --command report`. Tiap panggilan delegated
menambahkan satu `UsageRecord` ke `.workflow/usage.jsonl`.

Metrik **diturunkan saat baca**, bukan dihitung saat tulis. Konsekuensinya disengaja:
definisi yang lebih baik membaca ulang sejarah alih-alih membatalkannya. Yang dilaporkan —
biaya per task diterima, premium context yang dihindari, waktu penyelesaian, kebenaran
percobaan-pertama, rework, tingkat lolos test dan security. Tiap rasio membawa penyebutnya
sendiri, dan panggilan yang tak terukur disebut namanya alih-alih dirata-ratakan hilang.

Pencatatan duduk di jalur balik `Executor`, bukan di sebelah `write_call_meta`. Jalur reuse
pulang tanpa pernah membangun call meta, dan reuse hit justru baris paling menarik di
stream: panggilan delegated yang biayanya nol. Merekam hanya yang sampai ke provider akan
menaikkan biaya-per-task secara sistematis dengan membuang persis yang murah.

## Governance

`core/governance.py`. Tiga kontrol, dan tiap satunya menyatakan di mana giginya sebenarnya
— kontrol governance yang terdengar lebih kuat dari kenyataannya lebih buruk daripada yang
absen, karena ia dipercaya.

- **Provider allowlist — penegakan nyata.** Route yang menyebut provider tak terdaftar
  ditolak di `Router.route()`, sebelum apa pun di-spawn.
- **Plafon token per sesi — penegakan nyata, atas riwayat tercatat.** `session_token_budget`,
  mati secara default, menolak dengan error type baru `budget_exceeded`. Terbatas pada yang
  sudah TERCATAT: panggilan yang sedang terbang belum masuk stream, jadi kerja konkuren bisa
  melewati garis sebanyak yang sedang berjalan.
- **Kebijakan tool per command — deklarasi, bukan sandbox.** Batas yang menegakkan tetap
  config permission milik provider sendiri. `FORBIDDEN_TOOLS` tak bisa dilebarkan config.

Jejak audit di `.workflow/audit.jsonl`, terpisah dari stream usage meski lapangannya
beririsan: usage itu pengukuran dan boleh dihitung ulang dengan definisi baru, audit itu
catatan apa yang dikerjakan. Cakupannya sempit dan jujur — hanya panggilan delegated.

## Verified Graphify

`core/graph_meta.py` dan `python main.py --command graph-meta`. Graphify adalah CLI
eksternal, jadi provenance menumpang di sidecar berkunci node id, bukan di dalam
`graph.json` yang akan ditimpanya.

Yang dicatat: SHA commit dan anchor hash per node. Verifikasinya membedakan baris yang
**pindah** dari baris yang **berubah** — pembedaan yang staleness berbasis mtime
seluruh-graph tak akan pernah bisa lakukan. `graph_index.subgraph()` mengembalikan
tetangga n-hop terurut confidence alih-alih seluruh graph, dan `leads()` kini menampakkan
campuran confidence edge yang selama ini memang menggerakkan peringkatnya.

## Peringatan kontrak tak lagi terhitung dua kali

`_finalize_verify_result` berjalan dua kali pada payload yang sama **secara desain** —
worker memfinalisasi yang ia produksi, dan `await` memfinalisasi output tersimpan lagi di
jalan pulang. Keduanya sah; `await` tak boleh berasumsi record yang ia baca pernah
difinalisasi. Tapi `extend` lama membuat tiap peringatan muncul sekali per pass, sehingga
verify dengan satu celah nyata melaporkannya dua kali — terbaca sebagai dua masalah.

Peringatan kini digabung berdasarkan identitas `(kind, detail, sample)`. Pass kedua
menambah nol, sementara entri dari produsen lain — evidence contract miss, task truncation
— selamat, yang tidak akan terjadi kalau solusinya mengganti wholesale.

## Reproducibility

`tests/checks/deps.py` menggagalkan build kalau kode yang di-ship mengimpor apa pun di luar
stdlib. Itu titik di mana lock file baru jadi perlu; berkas constraints yang tak mendaftar
apa-apa akan mendokumentasikan klaimnya tanpa mengujinya.

## Harness benchmark

`bench/oracle.py`, `bench/aggregate.py`, `bench/corpus.py` — bagian dari
`bench/BENCHMARK-PLAN.md` yang bisa dibangun repo ini sendiri. Arm A dan B belum bisa
dijalankan; lihat "Yang belum ditutup".

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

- **`correlation_id` tidak menggabungkan panggilan seperti yang dijanjikannya.** Baca ini
  sebelum memakai angka `--command report`. Id diturunkan dari
  `sha256(project_root, session_id, teks_task)`, dan teks task tiap command berbeda — jadi
  plan, execute yang mengikutinya, dan verify yang menilainya menghasilkan tiga id berbeda,
  bukan satu. Pada `usage.jsonl` repo ini sendiri: 3 baris, 3 id, tiap grup berukuran satu.
  Akibatnya `first_pass_correctness` dan `rework` — yang seluruh gunanya membandingkan
  percobaan pertama dengan percobaan ke-N — tak pernah punya percobaan kedua untuk
  dibandingkan, dan `cost_per_accepted_task` menghitung per panggilan alih-alih per task.
  Angkanya hijau karena penyebutnya kosong, bukan karena sehat. Memperbaikinya menuntut
  keputusan yang belum diambil: mengunci id ke sesuatu yang stabil lintas command, atau
  mengalirkan id eksplisit lewat job record dan argv — yang justru ditolak sadar saat
  merancangnya, karena tiap hop adalah tempat id bisa hilang.
- **Edit main_agent tidak terukur sama sekali.** `/.execute` tak punya jalur Python, jadi
  perubahan berkas yang dikerjakan main_agent tak pernah melewati proses ini dan tak masuk
  usage maupun audit. `accepted` hanya lahir dari verify. Jejak audit karena itu mencatat
  panggilan delegated saja — dinyatakan begitu di `runtime_io.py`, bukan disiratkan.
- **Jejak audit belum punya pembaca.** `.workflow/audit.jsonl` ditulis tiap panggilan
  delegated, tapi `--command report` hanya membaca stream usage dan quality. Barisnya ada
  dan benar; belum ada perintah yang menyajikannya.
- **Allowlist provider lolos saat nama provider kosong.** `check_provider` meloloskan
  provider yang falsy dengan sengaja — provider di-resolve belakangan dari bundel, dan
  menolak di sana akan mematikan tiap workspace yang confignya mendahului kunci itu. Pada
  baris usage repo ini lapangan `provider` memang `null`, jadi gerbangnya belum pernah
  menolak apa pun dalam praktik.
- **Arm A dan B benchmark belum ada.** `bench/oracle.py` dan `bench/aggregate.py` selesai,
  tapi `bench/driver.py` belum dibangun dan korpus 15 task belum diisi tangan. Arm A (Claude
  langsung) dan arm B (native sub-agent) menuntut harness yang bisa menjalankan sesi Claude
  Code. Ditambah satu penghalang eksternal: selama `claude-opus-5` berharga $0 di tokenburn,
  biaya kedua arm terbaca nol dan perbandingannya tak bermakna. Arm C bisa dipanen dari
  `usage.jsonl` hari ini — tanpa pembanding.
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
