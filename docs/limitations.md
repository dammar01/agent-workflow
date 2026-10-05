# Known limitations

Moved from `docs/reference.md` ("Batasan yang diketahui"), where the old anchor still
points here. The list below is kept in its original language (Bahasa Indonesia).

Provider-specific security boundaries (write prevention, secret-file reads, shell
restriction) are summarized in the [security page](security.md) and
detailed in [`reference.md`](reference.md).

## Batasan yang diketahui

Disebut terbuka karena diam soal ini akan membuat runtime terlihat lebih menjamin daripada kenyataannya:

- **Mutasi main_agent tak terlihat oleh runtime ini.** `/.execute` tak punya jalur Python sama sekali. Audit scope, penjaga operasi destruktif, dan atribusi perubahan file karena itu belum ada — perlu lapisan hook di sisi main_agent.
- **Tulis ke project lain tidak ditahan, dan edit lewat Bash tidak tercatat.** Hook `intent-gate-check` hanya menolak tulis ke script runner; Edit/Write ke path di luar project root sesi — termasuk repo tetangga — lolos. Edit yang dilakukan lewat Bash atau skrip patch tidak melewati hook Edit/Write, jadi `task-events` tidak mencatatnya (DEC-021 sengaja tak memasang hook pada Bash), dan edit ke project lain dibuang karena di luar root. Larangan menulis ke project lain tanpa persetujuan eksplisit user ada di kontrak prompt (`CLAUDE.md`, DEC-046), bukan di hook. Teramati di pemakaian nyata (CASE-018).
- **Kontrak masih sebagian berbasis prompt.** Runtime memvalidasi struktur dan routing finding verify, tetapi kebenaran semantik klaim serta output kontrak milik main_agent tetap tidak dapat dibuktikan hanya dari penanda.
- **Telemetry masih parsial.** Durasi, exit code, hasil kill, ukuran prompt/output, dan estimasi token dicatat per panggilan di `call.meta.json`; jumlah pemanggilan tool dan token provider aktual belum selalu tersedia.
- **OpenCode nyata hanya diuji opt-in.** Suite default mensimulasikan provider; jalur `Popen`, persistence, installer, dan process lifecycle tetap dijalankan lokal. Gunakan `tools/e2e/e2e.py --full` untuk smoke test berkuota.
- **Probe PING memakai kuota.** Job yang terus stalled dapat diprobe berulang sesuai cadence; default `await` adalah 120 detik.
- **Tuning liveness belum seragam di jalur attach.** `check.py --wait` memakai default tool untuk ambang stalled dan probe ulang, bukan nilai project-local.
- **Deteksi upgrade dapat tertutupi stamp parsial.** Delegated load memperbarui marker versi `config.json` tanpa meregenerasi scripts atau `second_agent.json`, sehingga warning berikutnya dapat menganggap workspace current. Layout lama tetap terdeteksi dari isinya, bukan dari stamp.
- **Deteksi staleness graph hanya mengikuti source `.py`.** Perubahan bahasa lain tidak masuk fingerprint runtime; Stop hook Graphify merupakan layer Claude terpisah dan tidak tersedia di semua main agent.
- **Path graph lintas-OS belum dinormalisasi penuh.** Snapshot yang dibuat di Windows lalu dibaca dari POSIX/WSL dapat tetap menghasilkan candidate path ber-backslash; refresh graph secara manual dari environment aktif bila ini terjadi.
- **Skrip runner tidak portabel lintas-OS.** Path absolut dipanggang saat init/upgrade; pindah repo, path, atau OS berarti jalankan upgrade dari environment baru.
