# Changelog

Full release notes live one directory per version under `prompt/`. This file is the index
and the place to look first; it does not duplicate the notes.

Release procedure: `RELEASE.md`.

| Version | Notes | Theme |
|---------|-------|-------|
| 3.5.1 | [prompt/v3.5.1/changelog.md](prompt/v3.5.1/changelog.md) | Task cap diturunkan dari transport provider (argv vs stdin) alih-alih satu konstanta; kegagalan tulis stdin codex tak lagi ditelan; perbaikan parsing digest CRLF |
| 3.5.0 | [prompt/v3.5.0/changelog.md](prompt/v3.5.0/changelog.md) | Knowledge ter-Git dan `/.promote` yang menulisnya; generator skrip runner dengan deteksi drift; benchmark dijalankan sungguhan |
| 3.4.5 | [prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) | Release stability (CI, test runner, release procedure) and the measurement layer: workflow contracts, telemetry, governance, verified graphify; agy provider behind an explicit opt-in |
| 3.4.4 | [prompt/v3.4.4/changelog.md](prompt/v3.4.4/changelog.md) | Second provider: codex adapter, provider registry, read-boundary findings |
| 3.4.2 | [prompt/v3.4.2/changelog.md](prompt/v3.4.2/changelog.md) | — |
| 3.4.1 | [prompt/v3.4.1/changelog.md](prompt/v3.4.1/changelog.md) | — |
| 3.4.0 | [prompt/v3.4.0/changelog.md](prompt/v3.4.0/changelog.md) | — |
| 3.3.1 | [prompt/v3.3.1/changelog.md](prompt/v3.3.1/changelog.md) | — |
| 3.3.0 | [prompt/v3.3.0/changelog.md](prompt/v3.3.0/changelog.md) | — |
| 3.2.1 | [prompt/v3.2.1/changelog.md](prompt/v3.2.1/changelog.md) | — |
| 3.2.0 | [prompt/v3.2.0/](prompt/v3.2.0/) | — |
| 3.1.2 | [prompt/v3.1.2.md](prompt/v3.1.2.md) | — |
| 3.1.1 | [prompt/v3.1.1.md](prompt/v3.1.1.md) | — |
| 3.1.0 | [prompt/v3.1.0.md](prompt/v3.1.0.md) | — |
| 3.0.1 | [prompt/v3.0.1.md](prompt/v3.0.1.md) | — |
| 3.0.0 | [prompt/v3.0.0.md](prompt/v3.0.0.md) | — |
| 2.0.0 | [prompt/v2.0.0.md](prompt/v2.0.0.md) | — |
| 0.0.0 | [prompt/v0.0.0.md](prompt/v0.0.0.md) | — |

v3.4.3 was built but never released; its changes are described inside the v3.4.4 notes.

## Termasuk di tag v3.4.5, di luar catatan rilisnya

Tag `v3.4.5` menunjuk `6ef1be0`, bukan commit tempat
[prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) ditulis. Butir di bawah ada di
dalam tag dan tidak ada di catatan rilis itu. Nomor versinya sengaja tidak dinaikkan:
`bench/BENCHMARK-PLAN.md:5` sudah mengunci v3.4.5 sebagai versi system under test, dan
menggeser nomornya sekarang berarti benchmark mengukur versi yang namanya berbeda dari
rencananya. Yang dibayar untuk itu adalah baris ini — tanpanya, tag dan catatannya
berselisih diam-diam.

Two of the gaps the v3.4.5 notes list under "Yang belum ditutup" are closed on `dev`
(2026-08-18), plus one the notes never listed — the installer had no tests of its own,
which nothing had recorded as a gap because the integration tests passing made it look
covered:

- **`correlation_id` now aggregates a task chain.** A plan records its derived id as the
  session's active chain (`state.json` key `chain`); the execute and verify that follow
  adopt that id instead of deriving their own, so one piece of work lands in `usage.jsonl`
  as one subject. Without a chain the old derivation still applies. Proven by
  `_correlation_chain` in `tests/checks/contracts.py`.
- **The installer has dedicated unit tests.** `tests/checks/installer.py` adds four checks
  — lenient decode, intent stanzas and managed-block splice, hook refresh with user-hook
  preservation and the POSIX rewrite, receipted rollback and settings drift — registered
  in both entry points.
- **`python tools/e2e.py --full` has been run against a live provider**: 98 passed,
  0 failed, 0 skipped, including the paid [DELEGATED] block (explore + sweep). Run on the
  `dev` working tree carrying the two fixes above, not on the 3.4.5 tag itself.

The measurement layer that sat here — workflow contracts, telemetry, governance, verified
graphify, the duplicated-warning fix, the stdlib-only test, and the benchmark harness —
was folded into [prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) rather than held
for a later number. It had been built after the version bump, so the release it belonged to
described none of it.
