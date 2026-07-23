# Skill: compress
description: Compress prose file ke caveman-speak. Preserve substansi teknis. Local.

## Trigger
/.compress <file>

## Execution
Baca file. Compress prose: drop artikel/filler/pleasantries/hedging. Fragments OK.
PRESERVE exact: code, paths, commands, URLs, angka, heading, technical terms.
Backup original → <file>.original.md sebelum overwrite.

## Output
[COMPRESS <file>] before: <bytes> | after: <bytes> | saved: <pct> | backup: <file>.original.md
"Confirm overwrite? (yes/no)"
