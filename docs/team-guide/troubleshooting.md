# Troubleshooting

## Setup and runtime errors

Start with `doctor` (`.workflow/run.sh doctor`, on Windows `.workflow\run.ps1 doctor`) and
read its `recommended_fixes`. The common cases are in the [README
troubleshooting table](../../README.md#quick-start); commands and their options are in the
[reference](../reference.md#command).

| Symptom | What to do |
| --- | --- |
| A warning that `.workflow` was built by an older tool version | Run `upgrade` for that project |
| The provider CLI is not found | Install it and make sure it is on `PATH` |
| Commands fail after you moved the tool checkout | Run `upgrade` from the new location |

## "PROXY GAGAL" / proxy failed

Claude Code stops and asks before doing anything else when the secondary agent returns no
usable evidence (an error, empty output, or a refusal instead of findings). This is
deliberate: it will not quietly fall back to reading the code itself. Answer `yes` to
continue locally without the secondary agent, or `no` to stop and fix the provider first.

## It is slow

It is slow because the secondary agent reads the code. That is the work you are delegating,
not overhead around it. What you can do:

**Measure first.** Your own numbers are better than anyone's estimate. The `report` command
summarizes how long delegated calls took in your project (mean, median, and how many calls
were not measured); per-call detail is kept in the workspace too. How to run it and where
the per-call files live: [reference, command table](../reference.md#command) and
[workspace layout](../reference.md#layout-workspace).

**Then reduce it:**

- **Skip the workflow for small tasks.** See [when-to-use.md](when-to-use.md). This is the
  biggest saving.
- **Do something else while it runs.** A delegated call runs as a background job, and
  Claude Code hands control back to you while it works. You cannot continue *that* line of
  reasoning until the result arrives, but you can review, write, or start another task.
- **Use the local commands.** `sweep` and `doctor` never call the secondary agent.
- **Scope the request.** "How does checkout validate addresses" finishes sooner than "explain
  checkout".
- **Let reuse work.** Repeated or similar questions can reuse earlier evidence and facts.
  Forcing a fresh session turns that off; do it only when you know the earlier evidence is
  stale. The option is in the [CLI reference](../reference.md#cli-langsung).
- **Use the quick verify mode when parsing is all you need.** A project can switch
  `/.verify` to parse checks only, without the secondary agent. It is much faster, but it
  proves only that files parse — **not** that the behavior is correct. The setting and what
  each mode checks: [verify modes in the reference](../reference.md#mode-verify).

If a task is worth delegating but still too slow to be practical, say so: that feedback is
recorded as research input ([research questions](../research/questions.md), RQ-04 and
RQ-05) rather than dismissed.

## "It feels the same as the normal agent"

Usually a sign that the request was procedural: you had already decided the steps, so there
was nothing left for the workflow to discover. Try the same task with an objective-oriented
request — [task-framing.md](task-framing.md), and the side-by-side in
[examples.md](examples.md). If it still adds nothing for that kind of task, that is a valid
result: use the agent directly for it.
