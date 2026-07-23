# Skill: help
description: Command reference v3.3.0

## Trigger
/.help

## Output
[COMMAND GUIDE — v3.3.0]

LOCAL (main_agent langsung):
  /.execute -y      implement code (wajib -y)
  /.init            buat/regenerate .workflow/ (scripts, opencode.json, config abs-path)
  /.refactor <s>    structural, zero behavior change
  /.commit          commit message (Conventional Commits)
  /.review <f>      one-line per issue review
  /.compress <f>    compress prose ke caveman
  /.memory <note>   simpan insight
  /.caveman [lite|full|ultra]  toggle compression
  /.local [on|off|status]      toggle no-proxy
  /.help            panduan ini

DELEGATED (1-call .workflow/run script → second_agent):
  /.explore <hint>  evidence gathering
  /.plan <task>     evidence + rencana terstruktur
  /.analyze <topic> deep analysis | --local: Claude only
  /.verify          3-step verification (auto setelah /.execute)
  /.sweep           git diff impact
  /.doctor          .workflow readiness

[WORKFLOW] /.explore → /.plan → /.execute -y → /.verify
[SESSION CACHE] LAST_EXPLORE_RESULT → /.plan,/.analyze | LAST_PLAN_RESULT → /.execute | LAST_EXECUTE_DIFF → /.verify,/.sweep
Prefix "/." wajib. Tanpa prefix → INVALID.
