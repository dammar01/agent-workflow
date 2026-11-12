# Exoma 38fb1df staging, 2026-11-12

Technical staging passed on Windows for Exoma
`38fb1df4f7d053d7cad1abe1a38e0f0cb9707b41` (package version `1.0.2`),
using Codex `gpt-6.1-sol` as the real provider. The manifest, lock and installed
Git metadata all identify that commit. The dependency was not patched.

## Initial exact-pin staging

The following observations describe the initial staging checkpoint. The later
installed-host recheck below records the updated installation state separately.

| Check | Observed result | Boundary |
| --- | --- | --- |
| Candidate on Exoma main | `git ls-remote` resolved main to the candidate | Observation on 2026-11-12 |
| Exoma CI | [Run 43178245808](https://github.com/dammar01/Exoma/actions/runs/43178245808) completed successfully, including every dist/wheel job | GitHub API run and job metadata checked for this exact SHA |
| Default suites | `python -I tests/run.py --keep-going`: exit 0, seven suites passed | Harnesses, offline E2E, research, research closure, fact replay, real use, blind sample |
| Research integrity | `python -I tools/maintain/check_research.py --git`: exit 0, 137 records, two warnings | At this checkpoint DEC-086/DEC-087 still contained `uncommitted` markers; these are corrected in the subsequent documentation update |
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
At this checkpoint, installed host drift and missing auto-review context were
unresolved. Host loading/trust and the live plan/approval/execute/verify chain
were not exercised, and other providers were not tested. Source regression
results from earlier commits are not inherited. The moved pin and staging
evidence were committed in `066dbfdeefdcab3303f904e183275c7f21f6dc7b`.

## Installed-host recheck (2026-11-12)

A later local recheck, with doctor timestamp `2026-11-12T11:14:06Z`, observed:

| Check | Observed result | Boundary |
| --- | --- | --- |
| Installed CLI | `exoma --version`: `exoma 1.0.2`, exit 0 | The global installation has no Git-source commit metadata; its version alone does not prove exact-pin identity |
| Project dependency | Package metadata: `1.0.2`, Git commit `38fb1df4f7d053d7cad1abe1a38e0f0cb9707b41` | Separate project interpreter; matches the research manifest and lock |
| Installed bundle | `exoma install --check`: exit 0, READY; bundle versus manifest OK, installed host configuration versus bundle READY | Hosts checked: Claude, Agy, Codex and OpenCode; the OpenCode project boundary was skipped because this project uses Codex |
| Codex auto-review context | Installed | Installation state only; the effective policy can be overridden |
| Project doctor | Workspace runner `doctor`: exit 0, READY, zero issues and five recommendations | Readiness checks; no live approval/execution or provider E2E run |
| Research integrity after documentation correction | `python -I tools/maintain/check_research.py --git --strict`: exit 0, 137 records, zero warnings | DEC-086/DEC-087 now name their existing implementation commits; the generated inventory matches the records |

The earlier installed-file drift and missing-context observations are superseded
by this recheck. That doctor report recommended reviewing an unrecognised `.workflow/tmp`
directory, a session lock older than 300 seconds that can block delegated calls,
leftover user-owned Claude configuration text, installed Playwright `1.65.1`
versus pinned `1.60.0`, and the trusted Codex provider read boundary. Codex does
not enforce a project-file read boundary and receives the process environment;
doctor found no project files matching its secret-name patterns. These were
recommendations rather than blocking issues in the reported READY result.

This installation recheck did not modify the installation, clear the lock,
rerun the default suites or call a real provider. The later live-session
observations below add hook and delegated-verification evidence. Research
validation and causal effectiveness claims are unchanged.

## Live-session observations (2026-11-12)

The same Codex main-agent session subsequently exercised a live preflight hook
and the delegated verification path. These observations refine the earlier
unverified-hook description:

| Surface | Observed result | Boundary |
| --- | --- | --- |
| Live host hook | At `2026-11-12T11:23:06Z`, the execution gate rejected a read of `.workflow/config.json`; the local gate receipt records `codex-host`, rule `preflight`, hybrid mode and exit 2 | Confirms that this preflight hook ran in the live host; does not establish all hooks or their trust configuration |
| Delegated documentation review | Job `job_20261112_112617_780778`, submitted at `2026-11-12T11:26:17Z`: reviewer DONE, no blocking findings, escalations or notes; nine changed files and 234 local links/anchors checked | Covers the documentation diff submitted to that job, before this follow-up entry and its summary updates |
| Runtime-run research check | `uv run python tools/maintain/check_research.py --git --strict`: passed, exit 0, 3.4 seconds, zero reused results | Executed by the runtime during verification; separate from the reviewer's read-only inspection |
| Runtime verification verdict | `incomplete`, despite the successful review and checker | Two declared evidence gaps: live hook loading/trust and full approval/execution acceptance; independent revalidation of historical private logs and remote CI |
| Formal workflow lifecycle | A complete plan-record, genuine approval, execute-begin, execute-end and verify sequence was not exercised | Hook activity and a successful standalone verify establish only part of that lifecycle |

The rejected config read is an allowed exception in the verification protocol.
It demonstrates live gate activity and a protocol/refusal mismatch, not proof
that this particular refusal was correct or that approval enforcement passed.
The review's hook-loading gap is narrowed by this separate observed event;
all-hook trust and full lifecycle acceptance remain unverified.

An initial verification attempt failed because its saved Codex provider thread
could not be resumed (`no rollout found`). The project-local provider-session
record was backed up and its unavailable thread references reset while keeping
the same main session. The subsequent job above started a new provider thread
and completed the review and requested check. No runtime source was changed.

The recorded review and runtime verdict are retained as returned. This follow-up
entry and its summary updates were added afterward and were not part of that
reviewed tree. No scientific disposition is upgraded by these observations.
