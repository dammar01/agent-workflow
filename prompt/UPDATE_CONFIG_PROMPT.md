Lakukan migrasi OpenCode global workflow dari v3.0.1 ke v3.1.0 FINAL.
Ikuti setiap step berurutan. Jangan skip. Jangan tanya kecuali ada risiko destructive
yang tidak bisa dibuat backup. Jangan interpretasi ulang spec.

Source of truth:

- Old spec: `prompt/v3.0.1.md`
- New spec: `prompt/v3.1.0.md`

Target global config:

- `~/.config/opencode/AGENTS.md`
- `~/.config/opencode/install_guide.md`
- `~/.config/opencode/skills/`
- `~/.config/opencode/commands/`
- `~/.config/opencode/reference/`
- `~/.config/opencode/memory/`

Jika path source prompt tidak ditemukan di current working directory, STOP dan output:

```text
[MIGRATION BLOCKED]
Reason: prompt/v3.1.0.md atau prompt/v3.0.1.md tidak ditemukan.
Action: jalankan prompt ini dari root repo agent-workflow.
```

---

## PRE-CONDITION OUTPUT

Sebelum mulai, output:

```text
[OPENCODE MIGRATION v3.0.1 -> v3.1.0 FINAL]
Mode: update existing global config
Target: ~/.config/opencode/

Source:
  old: prompt/v3.0.1.md
  new: prompt/v3.1.0.md

Rules:
  - Backup before overwrite.
  - Preserve user memory files.
  - Use v3.1.0.md FILE blocks as exact output.
  - Setup verify is structural only.
  - Runtime smoke test is separate and may WARN/SKIP.
```

---

## MIGRATION DELTA

Apply these semantic changes from v3.0.1 to v3.1.0:

1. Command ownership wording
   - `/.execute -y` is local. No `AGENT_PATH`.
   - `/.verify` is local full verification. No `AGENT_PATH`.
   - `/.verify-quick` is local lightweight verification. No `AGENT_PATH`.
   - `/.audit` requires `AGENT_PATH`. No fallback.

2. Risk classifier
   - Remove v3.0.1 pre-execute contract sanity check as a blocker.
   - Add lightweight risk classifier only after diff exists.
   - Purpose: choose `/.verify` vs `/.verify-quick`.
   - This is not full contract awareness.

3. Setup verify vs runtime smoke
   - Structural setup verify checks files/content/backups only.
   - Runtime unavailable conditions are WARN/SKIP, not setup failure.
   - Smoke test runs after setup and is reported separately.

4. Audit behavior
   - Cross-model audit is preferred.
   - Same-model audit is acceptable if cross-model unavailable.
   - `AGENT_PATH` is still mandatory for `/.audit`.

5. Structure
   - v3.1.0 adds/keeps `install_guide.md`.
   - v3.1.0 adds `reference/`.
   - Per-command skills remain separate.
   - Memory files are preserved if already present.
   - Relative references inside generated global files must resolve to `~/.config/opencode/`, especially `~/.config/opencode/reference/` and `~/.config/opencode/skills/`.

6. Workflow agent evidence gathering
   - Workflow agent must perform exhaustive evidence gathering before outputting uncertainties.
   - Output format must include `assumptions:` based on evidence found.
   - `uncertainties:` should only contain items that cannot be answered after search.

---

## HARD RULES

- Do not run `graphify init`.
- Do not run `graphify build`.
- Do not run `graphify watch`.
- Do not run `graphify update` automatically.
- Do not modify env vars.
- Do not delete user files.
- Do not overwrite `memory/PERSONAL_MEMORY.md` or `memory/DOMAIN_MAP.md` if they exist.
- Do not claim migration success before structural verify completes.
- Do not fail structural setup only because runtime condition is missing.
- Do not use v3.0.1 content as final output if v3.1.0 has a matching FILE block.

---

## STEP 1 - Inspect Current State

Check:

```bash
test -f prompt/v3.0.1.md
test -f prompt/v3.1.0.md
test -d ~/.config/opencode
```

Then list current global config:

```bash
find ~/.config/opencode -maxdepth 3 -type f | sort
```

If `~/.config/opencode` does not exist, create it and continue as fresh v3.1.0 setup.

---

## STEP 2 - Backup Existing Config

Create one timestamped backup root:

```bash
BACKUP_DIR="$HOME/.config/opencode.backup.v3.0.1-to-v3.1.0.$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
```

Before overwriting any existing file, copy it into the same relative path under
`$BACKUP_DIR`.

Required backup behavior:

- Existing root files: backup before overwrite.
- Existing `skills/*.md`: backup before overwrite.
- Existing `commands/*.md`: backup before overwrite.
- Existing `reference/*.md`: backup before overwrite.
- Existing `memory/PERSONAL_MEMORY.md`: backup and preserve original in target.
- Existing `memory/DOMAIN_MAP.md`: backup and preserve original in target.
- Existing `memory/MEMORY.md`: backup, then merge missing index entries only.

If backup fails, STOP:

```text
[MIGRATION BLOCKED]
Reason: backup failed for <path>
Action: fix filesystem permission / disk space, then rerun.
```

---

## STEP 3 - Extract v3.1.0 FILE Blocks

Use `prompt/v3.1.0.md` as exact source for generated files.

For every block:

```text
===== FILE: <relative_path> =====
<content>
===== END FILE =====
```

write `<content>` to:

```text
~/.config/opencode/<relative_path>
```

Create parent directories as needed.

Important:

- Use exact content inside the block.
- Do not include the `===== FILE` or `===== END FILE` markers in target files.
- Do not invent files that are not listed in v3.1.0 FILE blocks.
- Do not keep obsolete v3.0.1 generated content when v3.1.0 has a replacement block.
- Exception: `memory/*.md` is handled by STEP 4 to preserve user data.

---

## STEP 4 - Memory Preservation

Memory is user data. Handle specially.

For `memory/PERSONAL_MEMORY.md`:

- If target exists: keep existing target content unchanged.
- If target missing: create from v3.1.0 FILE block.

For `memory/DOMAIN_MAP.md`:

- If target exists: keep existing target content unchanged.
- If target missing: create from v3.1.0 FILE block.

For `memory/MEMORY.md`:

- If target missing: create from v3.1.0 FILE block.
- If target exists: append missing v3.1.0 index lines only.
- Do not remove existing entries.

---

## STEP 5 - Structural Verify

This step determines migration success.

Verify:

1. Root files exist:
   - `AGENTS.md`
   - `install_guide.md`

2. Skill files exist:
   - `skills/caveman.md`
   - `skills/workflow.md`
   - `skills/agent-workflow.md`
   - `skills/graphify.md`
   - `skills/safety.md`
   - `skills/context7.md`
   - `skills/memory.md`
   - `skills/explore.md`
   - `skills/plan.md`
   - `skills/execute.md`
   - `skills/verify.md`
   - `skills/verify-quick.md`
   - `skills/refactor.md`
   - `skills/analyze.md`
   - `skills/audit.md`
   - `skills/help.md`

3. Command files exist:
   - `commands/explore.md`
   - `commands/plan.md`
   - `commands/execute.md`
   - `commands/verify.md`
   - `commands/verify-quick.md`
   - `commands/refactor.md`
   - `commands/analyze.md`
   - `commands/audit.md`
   - `commands/memory.md`
   - `commands/help.md`
   - `commands/commit.md`
   - `commands/review.md`
   - `commands/compress.md`

4. Reference files exist:
   - `reference/invocation-examples.md`
   - `reference/json-contract.md`
   - `reference/token-budget.md`
   - `reference/graphify-missing-protocol.md`
   - `reference/errors.md`

5. Memory files exist:
   - `memory/PERSONAL_MEMORY.md`
   - `memory/DOMAIN_MAP.md`
   - `memory/MEMORY.md`

6. Required v3.1.0 wording exists:
    - `AGENTS.md` contains `v3.1.0 FINAL`.
    - `AGENTS.md` contains `Untuk input yang memiliki command workflow`.
    - `AGENTS.md` contains `Evidence commands bersifat workflow-agent primary`.
    - `AGENTS.md` contains `[WORKFLOW_AGENT] Evidence Gathering Protocol`.
    - `AGENTS.md` output format includes `assumptions:` field.
    - `AGENTS.md` says `/.execute -y`, `/.verify`, and `/.verify-quick` are local / no `AGENT_PATH`.
    - `AGENTS.md` says `/.audit` requires `AGENT_PATH`.
    - `AGENTS.md` contains `Lightweight Risk Classifier`.
   - `skills/execute.md` says it is always local and must not require `AGENT_PATH`.
   - `skills/verify.md` contains `Full local verification`.
   - `skills/verify-quick.md` contains `Lightweight local verify`.
   - `skills/audit.md` contains `AGENT_PATH wajib`.
   - `reference/invocation-examples.md` says examples are only for `explore`, `plan`, `analyze`, `audit`.

7. Removed/changed v3.0.1 wording is gone from active target files:
    - `AGENTS.md` must not say `Untuk input yang diawali command workflow`.
    - `skills/execute.md` must not require pre-execute contract sanity check.
    - `skills/verify.md` must not invoke `python $env:AGENT_PATH -c verify`.
    - `skills/verify-quick.md` must not invoke `python $env:AGENT_PATH -c verify_quick`.
   - `AGENTS.md` must not say action commands require `AGENT_PATH`.

Runtime availability checks are not structural failures:

- `AGENT_PATH` missing -> WARN/SKIP runtime smoke.
- Graphify CLI missing -> WARN/SKIP runtime smoke.
- No sample project -> WARN/SKIP runtime smoke.
- Project toolchain missing -> WARN/SKIP runtime smoke.

---

## STEP 6 - Runtime Smoke Test (Separate)

Run only after structural verify passes.

If runtime preconditions are missing, mark related checks as WARN/SKIP and continue.

Smoke checks:

1. `/.execute -y` expectation: local implementation, no `AGENT_PATH` check.
2. `/.verify` expectation: local full verification, no `AGENT_PATH` check.
3. `/.verify-quick` expectation: local lightweight verification, no `AGENT_PATH` check.
4. Low-risk diff expectation: risk classifier selects `/.verify-quick`.
5. Elevated-risk diff expectation: risk classifier selects `/.verify`.
6. `/.audit` expectation: `AGENT_PATH` required, no fallback.
7. `/plan` expectation: `[INVALID COMMAND]`.

Do not restore backup only because runtime smoke is skipped due to missing runtime condition.

---

## FINAL REPORT

Output exactly this shape:

```text
[MIGRATION RESULT - OPENCODE v3.0.1 -> v3.1.0 FINAL]
status: PASS / FAIL / PASS_WITH_WARNINGS

backup:
  path: <BACKUP_DIR>
  files: <count>

structural_verify:
  root_files: PASS / FAIL
  skills: PASS / FAIL
  commands: PASS / FAIL
  reference: PASS / FAIL
  memory: PASS / FAIL
  v3.1.0_wording: PASS / FAIL
  stale_v3.0.1_wording: PASS / FAIL

runtime_smoke:
  status: RUN / SKIPPED / WARN
  notes: <short notes>

key_changes:
  - execute/verify are local, no AGENT_PATH
  - audit requires AGENT_PATH, no fallback
  - lightweight risk classifier selects verify depth
  - setup verify separated from runtime smoke
  - workflow agent evidence gathering protocol added
  - output format includes assumptions field

warnings:
  - <none or list>

next:
  - Restart OpenCode session to load new global config.
```

If FAIL:

```text
[MIGRATION FAILED]
failed_step: <step>
reason: <reason>
backup: <BACKUP_DIR>
restore_hint: copy files from backup path back to ~/.config/opencode/
```
