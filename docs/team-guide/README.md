# Team guide

Practical guidance for developers using `agent-workflow` with Claude Code. It explains how
and when to use it, not how it works inside; for exact behavior, follow the links into the
[reference](../reference.md).

Read in this order:

1. [getting-started.md](getting-started.md) — set it up and make your first request.
2. [when-to-use.md](when-to-use.md) — the workflow or the agent directly? Benefits and costs.
3. [task-framing.md](task-framing.md) — how to phrase a request so the workflow can help.
   **The most important page.**
4. [examples.md](examples.md) — the same task done two ways.
5. [troubleshooting.md](troubleshooting.md) — errors, slowness, and what to do about them.

The one idea to take away: agent-workflow changes **who does the decomposition and
repository discovery**. If you still break every task into small instructions yourself, it
will mostly feel like the agent you already had, only slower.
