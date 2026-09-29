# Skill: refactor
description: Structural improvement — zero behavior change.

## Trigger
/.refactor <scope>

## Rules
Struktural ONLY, behavior TIDAK BERUBAH. Jangan expand scope.
[REFACTOR SCOPE] scope | allowed | forbidden | goal
Post: output [REFACTOR RESULT] → auto-trigger /.verify. Graph di-refresh Stop hook, JANGAN jalankan `graphify update` sendiri.
