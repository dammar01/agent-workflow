# Skill: review
description: One-line-per-issue code review. Local — no proxy.

## Trigger
/.review <file|diff>

## Execution
Baca target (file/diff). Per issue = satu baris. Tanpa praise, tanpa scope creep.
Format: path:line: <severity> — <problem>. <fix>.
severity: 🔴 critical | 🟠 major | 🟡 minor. Skip nit kecuali ubah makna.

## Output
[REVIEW <target>]
<path:line: severity — problem. fix.> (bersih → "no issues — <alasan>")
summary: <n> issues (<crit> critical, <major> major, <minor> minor)
