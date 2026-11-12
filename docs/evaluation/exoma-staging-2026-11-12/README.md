# Exoma 38fb1df staging, 2026-11-12

Technical staging passed on Windows for Exoma
`38fb1df4f7d053d7cad1abe1a38e0f0cb9707b41` (package version `1.0.2`),
using Codex `gpt-6.1-sol` as the real provider. The manifest, lock and installed
Git metadata all identify that commit. The dependency was not patched.

| Check | Observed result | Boundary |
| --- | --- | --- |
| Candidate on Exoma main | `git ls-remote` resolved main to the candidate | Observation on 2026-11-12 |
| Exoma CI | [Run 43178245808](https://github.com/dammar01/Exoma/actions/runs/43178245808) completed successfully, including every dist/wheel job | GitHub API run and job metadata checked for this exact SHA |
| Default suites | `python -I tests/run.py --keep-going`: exit 0, seven suites passed | Harnesses, offline E2E, research, research closure, fact replay, real use, blind sample |
| Research integrity | `python -I tools/maintain/check_research.py --git`: exit 0, 137 records, two warnings | Existing DEC-086/DEC-087 `uncommitted` markers remain warnings |
| Local research drafts | `python -I tools/maintain/check_research.py --drafts`: exit 0, 137 records, eight warnings | Local drafts are not published by this receipt |
| Candidate bundle integrity | `python -I -m exoma install --check`: bundle versus manifest OK | The overall command exits 1 because installed host files differ; it is not an overall install pass |
| Real-provider E2E | [codex-e2e.json](codex-e2e.json): exit 0, 11 checks passed, zero failed/skipped | Fresh delegated explore and local sweep; one provider/model |
| Existing project/host readiness | Doctor: exit 1, NOT_READY; install check: DRIFTED | Three project entry scripts differ; installed bundle files differ, and Codex auto-review context is missing |

Commands above used this repository's `.venv/Scripts/python.exe`. The provider run was:

```text
python -I tools/e2e/e2e.py --full --provider codex --model gpt-6.1-sol --report-json <receipt>
```

The public receipt preserves the original timestamp, stages, identity metadata and
coverage limits; its three absolute installation paths are replaced with
`<project>` paths. It contains no raw provider output or inherited environment.

The initial default-suite attempt passed six suites and failed the offline E2E
runner-binding assertion: init inherited the existing host's `python.path`, which
selected the global interpreter despite the isolated project CLI. The staging
context now sets both `EXOMA_HOME` and `AGENT_HOME` to its disposable directory
and restores them afterward. An added regression checks that an existing host
interpreter pin is hidden during staging and restored afterward. All seven
suites passed after this harness correction. No installed Exoma source or host
configuration was modified. The initial attempt also emitted ResourceWarnings
for bounded-process pipe handles; the successful suite summary does not expose
subprocess warnings and does not establish that those warnings are fixed.

The provider's work directory is a new temporary Git repository containing only
the synthetic `app.py` fixture (`def main(): return 'ready'`) plus generated
workflow files. Automatic approval initially rejected the launch over possible
repository-content transmission. Inspection confirmed the synthetic payload and
temporary work directory; the subsequent reviewed launch was accepted and ran.

This satisfies the one-provider CLI staging gate in [RELEASE.md](../../../RELEASE.md).
It does not establish current deployed-host readiness: installed host drift and
missing auto-review context remain unresolved, host loading/trust and the live
plan/approval/execute/verify chain were not exercised, and other providers were
not tested. Source regression results from earlier commits are not inherited.
Research validation and causal effectiveness claims remain unchanged. The moved
pin and staging evidence remain uncommitted.
