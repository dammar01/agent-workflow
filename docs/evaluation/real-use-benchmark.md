# Real-use benchmark

What happened to real coding work done with `agent-workflow`: did it reach the main branch,
did it need fixing, and what did it cost the developer and the main agent. It reads the
question Senior SWE-bench asks of agents — senior-level, underspecified work, graded on
whether it is really done — from sessions that already happened, without replaying them.

It is an observation, not a controlled study: one developer, nine private projects, and no
arm without the workflow. Read it as acceptance and cost in practice, not as proof that the
workflow raises them. Method, sources, and limitations:
[CASE-012](../research/real-cases/CASE-012-real-use-task-outcomes-on-nine-projects.yaml) (task
outcomes) and [CASE-011](../research/real-cases/CASE-011-real-use-baseline-of-3-7-3.yaml)
(session cost).

## Scope

| | |
| --- | --- |
| Sessions | 167 Claude Code sessions, 2026-09-15 .. 2026-10-03 |
| Releases | 3.6.0 (98 sessions) and 3.7.3 (69), the two most used stable releases |
| Projects | 9 private projects (below); agent-workflow itself excluded |
| Coding tasks graded | 97 — 68 features, 26 bugs, 1 performance, 1 migration, 1 refactor |
| Not graded | 70 sessions of investigation (24), working-tree review (13), operations (33) |

## How a task is graded

A task is one session whose purpose was a code change. The lines its successful edits left
behind (8+ characters; lines it removed again itself do not count) are looked up on the
main branch:

| Grade | Rule |
| --- | --- |
| **Solved, clean** | 80% or more of the lines entered `main`, and the same author did not change them within 24 hours |
| **Solved, fixed within 24 h** | Entered `main`, then changed within 24 hours by an author of the commits that brought them in |
| Partial | 30–80% of the lines entered `main` |
| Not in main | Less than 30% |
| No change | The session left no code change |

Entering `main` means the developer accepted the work. It does not mean the work is correct;
a quality review of a sample is planned as EXP-008.

## Results

| Metric | All | 3.6.0 | 3.7.3 |
| --- | --- | --- | --- |
| Coding tasks | 97 | 61 | 36 |
| **Solved (entered main)** | **68 (70%)** | 39 (64%) | 29 (81%) |
| **Solved, clean** | **47 (48%)** | 20 (33%) | 27 (75%) |
| Solved, fixed within 24 h | 21 | 19 | 2 |
| Partial / not in main / no change | 13 / 9 / 7 | 11 / 5 / 6 | 2 / 4 / 1 |
| Prompts per task (median) | 9 | 11 | 8 |
| Active time per task (median) | 53 min | 61 min | 41 min |
| Delegated calls per task (median) | 4 | 5 | 3 |
| Main-agent final context (median) | 210k | 246k | 170k |
| Context saved: tokens kept from entering the main agent (median) | 574k | 636k | 531k |

The 3.6.0 period was dominated by large features built over many consecutive sessions, so
the difference between the two columns is not attributed to the release.

### By category

| Category | Tasks | Solved | Clean | Fixed 24 h | Prompts | Active |
| --- | --- | --- | --- | --- | --- | --- |
| Feature | 68 | 46 (68%) | 31 (46%) | 15 | 10 | 64 min |
| Bug | 26 | 19 (73%) | 14 (54%) | 5 | 9 | 37 min |
| Performance, migration, refactor | 3 | 3 | 2 | 1 | 8–13 | 43–95 min |

### By delegated calls

82 of 97 tasks delegated to the second agent. A call is one prompt a provider answered;
verify-browser's spec and review stages each count as one.

| Calls | Tasks | Solved | Clean | Fixed 24 h | Prompts | Active | Final context | Saved |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 15 | 8 (53%) | 8 | 0 | 5 | 19 min | 108k | 0 |
| 1–2 | 20 | 14 (70%) | 12 | 2 | 6 | 33 min | 152k | 170k |
| 3–5 | 28 | 23 (82%) | 15 | 8 | 9 | 53 min | 201k | 671k |
| 6+ | 34 | 23 (68%) | 12 | 11 | 16.5 | 101 min | 354k | 1.4M |

Larger tasks drew more calls, so this table describes where delegation was used, not what
it caused.

### What the developer's prompts were

Follow-up prompts per task (mean; the first prompt is the request itself):

| Kind | All | 3.6.0 | 3.7.3 |
| --- | --- | --- | --- |
| Plan | 0.8 | 1.0 | 0.6 |
| Execute / approve | 1.6 | 1.7 | 1.5 |
| **Fix / correction** | **1.5** | 1.8 | 1.0 |
| Question | 2.1 | 2.2 | 1.9 |
| Verify | 1.7 | 1.9 | 1.4 |
| Further exploration | 0.3 | 0.3 | 0.3 |
| New change request | 0.5 | 0.5 | 0.6 |
| Other (reports, summaries, information) | 2.4 | 2.9 | 1.8 |

Prompts are classified from the slash command or the shipped intent map; 85% agreed with a
hand label on 80 samples. Misses were mostly problem reports without a keyword, so the fix
count is a lower bound.

### Main-agent context

| | All | 3.6.0 | 3.7.3 |
| --- | --- | --- | --- |
| Final context, median / p90 | 210k / 491k | 246k / 519k | 170k / 339k |
| Largest context in any task | 694k | 660k | 694k |
| Sessions compacted | 3 | 2 | 1 |
| **Context saved**, median / p90 | **574k** / 1.8M | 636k / 1.8M | 531k / 1.6M |
| Context saved, total | 78.7M | 52.4M | 26.3M |
| Context saved ÷ final context (median) | 2.4× | 2.1× | 2.8× |
| Read fresh by the second agent / returned to the main agent (median) | 583k / 9k | 652k / 13k | 539k / 8k |
| Status line `Saved` (adds reasoning), median / total | 609k / 81.3M | 661k / 54.2M | 547k / 27.2M |
| Digest saving (lower bound), median / total | 8.0k / 929k | 8.5k / 643k | 6.7k / 285k |

**Context saved** is the number of tokens delegation kept from entering the main agent:
what the second agent read fresh (input minus cache read), less the answers that came back
to the main agent. Per task it was a median 2.4 times the main agent's whole final window,
by category 638k for features and 291k for bugs, and 1.4M for tasks with 6 or more calls.
It assumes the main agent would have read what the second agent read, and it includes the
second agent's own fixed overhead per call (its system prompt and instructions, which the
runtime does not separate), so it overstates the reduction somewhat. The status line's
`Saved` badge differs in two ways: it adds the second agent's reasoning, which is output
rather than tokens entering the main agent, and it does not subtract the answers that came
back. Together they make the badge about 3% higher.

The **digest saving** is the lower bound: the part of the second agent's answer the digest
stood in for (`premium_context_avoided_tokens`, answer minus digest). The runtime
under-records it — since 2026-09-15 verify-browser's spec and review stages skip the digest
step of the main delegated path, so about 14% is missing from the local streams. The figures
above recover those rows from the archived replies (as recorded: 6.7k median, 779k total).
The badge is unaffected, because those rows carry provider token counts. The same review
rows are logged with command `verify`, so verify counts and verdicts include verify-browser
reviews.

## Projects

| Project | Kind | Stack | Size (source / test files) | Tasks | Solved | Clean | Saved (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | Frontend of a multi-tenant work-management SaaS | Next.js 16, React 19 | ~570 / 29 | 29 | 19 (66%) | 14 | 763k |
| B | API backend of the same SaaS | Laravel 12 | ~920 / 138 | 23 | 20 (87%) | 14 | 855k |
| C | Finance and operations back-office, in use since 2022 | Laravel 13 | ~3,100 / ~360 | 13 | 8 (62%) | 8 | 213k |
| D | Code-audit automation service run from CI | Python | ~75 / 19 | 10 | 10 (100%) | 4 | 352k |
| E | Internal analytics dashboard with a scheduled ETL job | Next.js 16, Bun | ~100 / 9 | 10 | 5 (50%) | 2 | 395k |
| F | Company operations app (scheduling, permissions), since 2020 | Laravel 13, Vue 2 | ~460 / 16 | 4 | 2 | 1 | 137k |
| G | A second working copy of C, for branch reviews and CI | Laravel 13 | as C | 4 | 3 | 3 | 0 |
| H | HR performance-review frontend | Next.js 16 | ~1,260 / 156 | 3 | 0 | 0 | 984k |
| I | HR attendance app, since 2020 | Laravel 13, Vue 2 | ~520 / 45 | 1 | 1 | 1 | 540k |

H works on a `production` branch, so the main-only rule counts its tasks as not in main. 35
of the 68 solved tasks touched a test file — 20 of them in B, none in A.

## Session cost: the 3.7.3 baseline

Per session rather than per task, all 69 sessions of 3.7.3 (coding and not), from
`tools/maintain/measure_real_use.py`:

| Metric (per session unless stated) | Median | p90 |
| --- | --- | --- |
| Prompts the developer sent | 7 | 17 |
| Prompts before the first edit, sessions that changed code | 4 | 7 |
| Active time (gaps over 15 min dropped) | 32.6 min | 98.5 min |
| Files changed, sessions that changed code (49 of 69) | 5 | 19 |
| Lines added / removed, same sessions | 215 / 25 | 1,275 / 170 |
| Delegated calls | 2 | 8 |
| Delegated call duration | 110 s | 499 s |

Corrections: 15% of follow-ups in a hand-labelled sample of 80 (95% interval 8.8–24%).
Delegated ok rate 1.000 over 309 calls; no verify verdict was `pass` (as in CASE-009).

## What these figures support

They support: in daily use on these projects, 70% of coding work reached `main` and 48%
without a fix within a day, at a median of 9 prompts and 53 active minutes per task, while
delegation kept a median 574k tokens per task from entering the main agent.

They do not support: that the workflow raises any of these rates (there is no run without
it), that delegation improves outcomes (delegated tasks are larger), a comparison with
Senior SWE-bench scores (the developer was in the loop and accepted the work), or a
generalisation beyond one developer.

## Recounting

Both tools are read-only and print JSON; project names become letters unless
`--show-names`.

```text
python tools/maintain/measure_task_outcomes.py --projects-root <projects> \
    --labels <labels.json> --version 3.6.0 --version 3.7.3
python tools/maintain/measure_real_use.py --projects-root <projects> --version 3.7.3
```

The task labels (one category per session transcript) were assigned by the main agent and
have not been reviewed by the maintainer yet. They live in the maintainer's git-ignored
drafts area, beside the transcripts and usage streams they describe, so none of these
figures can be recounted from a clone of this repository — only on the machine that holds
the sessions.
