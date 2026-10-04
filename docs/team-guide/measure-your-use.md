# Measure your use

The workflow records every delegated call in each project, and Claude Code keeps your session
transcripts. Two offline tools in the clone read both and summarize how you work with the
workflow: how many prompts a session takes, how often you push back, how long delegated calls
run, and how much of your coding work reaches `main`. The figures are yours; nothing is sent
anywhere.

## When it is worth running

- After a few weeks on a release, to see whether a change of habit (command-only mode, a
  different secondary agent, verify with chosen tests) changed anything.
- Before and after an upgrade, on the same projects, to compare two releases.
- When you want to share how the workflow works for you without sharing your projects.

## What you need

- The projects you worked on, under one folder, each with its `.workflow/` data.
- Your Claude Code transcripts, where Claude Code keeps them by default.
- For the outcome tool only: a small labels file that says, per session, what kind of work it
  was (feature, bug, refactor, and so on). A first prompt does not say reliably what a
  session became, so you label it once.

## Reading the result

Every figure is cited as `<id>@<version>` and its exact method is in the
[metrics registry](../evaluation/metrics.md). A figure only compares with another at the same
version. Project names become letters unless you ask for names.

## Sharing it

Ask for an export file. It holds the same aggregates and a stamp saying which tool version,
parameters and inputs produced them (a hash, not the files). The tool refuses to write it if
it would contain a path or one of your project names, and it never contains prompt text.
Nobody, including the maintainer, can recount your figures; the stamp is what makes them
comparable.

How to run both tools: [metrics, "Running the tools"](../evaluation/metrics.md#running-the-tools).
