# Skill: verify
description: 3-step verification — logic, falsification, reality.

## Trigger
/.verify (auto setelah /.execute -y atau /.refactor)

## Protocol
1. Logic: solve problem? assumptions valid? konsisten pola codebase? → PASS/FAIL + reason
2. Falsification: kondisi gagal? edge case? malformed input? → list
3. Reality: test suite → run → simulate → "not executable". Actual vs expected.

## Output
[VERIFICATION] logic: PASS|FAIL — <reason> | failure: <list> | reality: <actual>|not executable | verdict: DONE|NEEDS FIX
NEEDS FIX → fix → re-run /.verify. JANGAN output final sebelum done.
