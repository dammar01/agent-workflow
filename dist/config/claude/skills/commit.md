# Skill: commit
description: Generate commit message (Conventional Commits). Local.

## Trigger
/.commit

## Execution
1. `git status` + `git diff --staged`. Tak ada staged → "[COMMIT] Stage dulu: git add <files>", STOP.
2. Tentukan type (feat|fix|refactor|chore|docs|test|perf|style|build|ci), scope, subject (≤50, imperative, no period), body (hanya jika why non-obvious).
3. Output [COMMIT MESSAGE] block. "Jalankan? (yes/no)" → yes: `git commit -m`. JANGAN commit tanpa konfirmasi.
