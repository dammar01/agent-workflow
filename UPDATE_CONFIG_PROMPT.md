# Update OpenCode Global Config — Agent-Workflow Integration

Update konfigurasi global OpenCode di `~/.config/opencode/` untuk mengintegrasikan agent-workflow Python script via `AGENT_PATH` env variable pada workflow commands: `/.explore`, `/.plan`, `/.analyze`, `/.execute -y`, dan `/.verify`.

## Precondition

- `AGENT_PATH` env variable sudah di-set dan menunjuk ke `agent-workflow/main.py`
- Python 3.10+ tersedia di PATH
- OpenCode global config sudah ada di `~/.config/opencode/`

## Changes Summary

Skills yang di-update untuk invoke agent-workflow:
- `~/.config/opencode/skills/explore.md` → invoke `-c explore`
- `~/.config/opencode/skills/plan.md` → invoke `-c plan`
- `~/.config/opencode/skills/analyze.md` → invoke `-c analyze`
- `~/.config/opencode/skills/execute.md` → invoke `-c execute`
- `~/.config/opencode/skills/verify.md` → invoke `-c verify`

Commands yang di-update description:
- `~/.config/opencode/commands/explore.md` → "Agent-workflow powered..."
- `~/.config/opencode/commands/plan.md` → "Agent-workflow powered..."
- `~/.config/opencode/commands/analyze.md` → "Agent-workflow powered..."
- `~/.config/opencode/commands/execute.md` → "Agent-workflow powered..."
- `~/.config/opencode/commands/verify.md` → "Agent-workflow powered..."

Skills yang TIDAK diubah (tidak ada route di agent-workflow):
- `refactor.md`
- `memory.md`
- `help.md`

## Execution

Update semua files sesuai content dari `E:\Work\project\agent-workflow\OPENCODE_GLOBAL_CONFIG_V2.md` bagian STEP 3 (skills) dan STEP 4 (commands).

Untuk setiap skill yang match dengan routes di `agent-workflow/config/opencode.json`:

1. Ganti execution logic dengan pattern:
   - STEP 1: Multi-layer check AGENT_PATH (5 layers)
   - STEP 2: Tentukan session ID
   - STEP 3: Invoke agent-workflow via `python $env:AGENT_PATH -c <command> -p "<prompt>" -s "<session>" -w "<workspace>" --pretty`
   - STEP 4: Parse JSON response
   - STEP 5: Output evidence/result

2. Update description di command file dari "Optional shortcut" menjadi "Agent-workflow powered"

Setelah selesai, verifikasi:
- Skills contain "Multi-layer check AGENT_PATH"
- Skills contain "python $env:AGENT_PATH -c"
- Commands description contain "Agent-workflow powered"

Output final report:

```text
[CONFIG UPDATE COMPLETE]

Skills updated (agent-workflow integration):
  explore.md  ✓
  plan.md     ✓
  analyze.md  ✓
  execute.md  ✓
  verify.md   ✓

Commands updated (description):
  explore.md  ✓
  plan.md     ✓
  analyze.md  ✓
  execute.md  ✓
  verify.md   ✓

Skills unchanged (no agent-workflow route):
  refactor.md ✓
  memory.md   ✓
  help.md     ✓

Status: READY
Agent-workflow integration active for: /.explore /.plan /.analyze /.execute /.verify
```
