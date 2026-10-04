# Research Charter — Agent Decomposition & Resource Allocation

Adopted by the maintainer on 2026-10-05 as the research boundary of `agent-workflow`. It sets
the direction; [`CONTRACT.md`](CONTRACT.md) still governs how evidence is recorded. The charter's
questions are tracked as RQ-16 and RQ-17 (new) and through the existing RQ-01, RQ-02 and RQ-13
(see [questions.md](questions.md)); its core hypothesis is a draft until an experiment tests it.

## 1. Research Objective

`agent-workflow` is developed to study **how much agentic computation should be allocated to a task**, rather than to maximize the number, breadth, or autonomy of agents.

The central problem is:

> **Given a task and a bounded resource budget, how should work be decomposed across agents so that additional agentic computation produces sufficient marginal task value?**

The project therefore treats agent delegation as a **resource allocation problem**, not merely as a multi-agent architecture problem.

---

## 2. Primary Research Question

### RQ1 — Optimal Agent Decomposition

> **What degree of agent decomposition is optimal for a task under constraints of token usage, latency, human intervention, and coordination overhead?**

The objective is not to prove that more agents are better or worse.

The objective is to determine:

> **when additional agentic computation is still worth its cost, and when additional decomposition becomes inefficient.**

Formally:

$$
R^* =
\arg\min_R Cost(R)
\quad
\text{subject to}
\quad
Quality(R) \ge Q_{target}
$$

where:

$$
R = (N, T, L, H, K)
$$

and:

* \(N\) = number of delegated agent executions
* \(T\) = token/computation cost
* \(L\) = latency
* \(H\) = human intervention
* \(K\) = coordination overhead

---

## 3. Core Hypothesis

### H1 — Diminishing Marginal Return

> **Increasing agentic decomposition does not produce linear improvement in task outcome. Beyond a task-dependent point, additional agents produce diminishing or negative marginal returns because coordination, token, latency, and intervention costs increase.**

Expected relationship:

$$
\Delta Quality_i
=
Quality(R+i)-Quality(R)
$$

while:

$$
\Delta Cost_i
=
\Delta T_i+
\Delta L_i+
\Delta H_i+
\Delta K_i
$$

An additional agent is justified only when:

$$
\boxed{
\Delta Quality_i > \Delta Cost_i
}
$$

The practical meaning of the inequality depends on the chosen normalization of each cost component. The equation is therefore a research objective, not a claim that these quantities are already directly comparable.

---

## 4. Secondary Research Questions

### RQ2 — Agent Scaling

> **How does task outcome change as the number of delegated agent executions increases?**

Study:

$$
N \rightarrow Quality
$$

The experiment should identify whether the relationship is:

* increasing,
* saturating,
* or degrading after a certain point.

The goal is to identify a task-dependent **useful decomposition range**, not a universal optimal number of agents.

---

### RQ3 — Token Efficiency

> **How much additional task value is obtained per unit of additional token/computation expenditure?**

Measure:

$$
Efficiency_T
=
\frac{\Delta Quality}{\Delta Tokens}
$$

The relevant question is not whether delegation saves tokens in absolute terms.

The relevant question is:

> **whether the additional tokens spent on delegated computation produce enough additional task value.**

---

### RQ4 — Coordination Efficiency

> **How does coordination overhead change as agent decomposition increases, and at what point does coordination offset the benefit of additional agents?**

Coordination includes, where observable:

* task decomposition,
* context preparation,
* handoff,
* result interpretation,
* continuation,
* verification,
* synthesis.

This separates:

$$
Agent\ Computation
$$

from:

$$
Coordination\ Computation
$$

because increasing the number of agents may increase both simultaneously.

---

### RQ5 — Context Allocation

> **Does externalizing repository exploration and analysis to secondary agents improve the allocation of primary-agent context, rather than merely reducing token usage?**

The project must distinguish:

$$
Context\ Saved
$$

from:

$$
Useful\ Context\ Allocation
$$

A reduction in primary-agent context is not automatically a positive outcome if relevant information is lost or additional coordination is required.

---

### RQ6 — Human Intervention

> **How does agent decomposition affect the amount of human intervention required to reach a successful task outcome?**

Measure:

$$
Efficiency_H
=
\frac{\Delta Quality}{\Delta HumanIntervention}
$$

The desired system is not necessarily the one with zero human intervention.

The research asks whether additional agents:

* reduce intervention,
* merely move intervention to another stage,
* or create additional intervention through coordination and correction.

---

## 5. Central Research Model

The project investigates the following relationship:

$$
\boxed{
(N,\ Allocation,\ Context)
\rightarrow
(Quality,\ Tokens,\ Latency,\ Human,\ Coordination)
}
$$

The core optimization target is:

$$
\boxed{
\text{maximize task value subject to resource constraints}
}
$$

not:

$$
\text{maximize agent count}
$$

and not:

$$
\text{maximize autonomy}
$$

---

## 6. Specialist and Generalist Boundary

`agent-workflow` does **not** aim to build a general-purpose multi-agent system merely because such systems have broader capability.

Specialized agents may be used as experimental components, but specialization is not itself the research objective.

The project specifically studies:

> **whether and when composing additional agentic components produces sufficient marginal value to justify its resource and coordination cost.**

Therefore:

* adding more specialized agents is valid only when it enables a measurable experiment;
* implementing broader agent capabilities is not sufficient justification;
* feature parity with another general agent harness is not a project objective.

A system having a broader abstraction or more features does **not** invalidate this research.

---

## 7. Explicit Non-Goals

The following are **not** primary goals of the project:

1. Building the most general agent harness.
2. Maximizing the number of available specialized agents.
3. Reproducing another agent platform's feature set.
4. Proving that multi-agent systems are universally superior to single-agent systems.
5. Eliminating human involvement entirely.
6. Maximizing raw benchmark score without considering resource expenditure.
7. Adding infrastructure solely because another agent framework already has it.

A new subsystem must therefore be justified by its relationship to the research questions.

---

## 8. Research Decision Rule for New Features

Before adding a subsystem, answer:

### Problem

What research problem does this feature enable us to investigate?

### Variable

Which measurable research variable does it change?

Examples:

* number of agents,
* token allocation,
* context allocation,
* coordination overhead,
* latency,
* human intervention.

### Experiment

What comparison becomes possible because of this feature?

### Evidence

What observable data will determine whether the feature contributes useful knowledge?

### Complexity

What implementation and operational complexity does the feature introduce?

### Existing Mechanism

Can the current architecture already support the experiment without adding the feature?

A feature that cannot answer these questions should be considered **out of research scope**, even when it improves product capability.

---

## 9. Required Evidence Discipline

Research conclusions must distinguish:

### Implemented

The mechanism exists in code.

### Observed

The mechanism has been exercised in real usage or controlled execution.

### Validated

A controlled experiment provides sufficient evidence for the stated claim.

These states must never be conflated.

In particular:

> **A higher success rate after adding agents is not sufficient evidence that the added agents caused the improvement.**

Possible confounders include:

* task difficulty,
* model variance,
* prompt differences,
* context differences,
* human intervention,
* selection bias,
* task type,
* provider behavior,
* execution retries.

---

## 10. Primary Experimental Direction

The minimum controlled experiment should compare different decomposition levels while keeping other conditions as constant as practical.

Example:

$$
N = 0,1,2,3,\ldots
$$

For each \(N\), measure:

$$
Quality(N)
$$

$$
Tokens(N)
$$

$$
Latency(N)
$$

$$
Human(N)
$$

$$
Coordination(N)
$$

The primary object of analysis is the **marginal return curve**:

$$
\Delta Q(N)
=
Q(N)-Q(N-1)
$$

against:

$$
\Delta C(N)
=
C(N)-C(N-1)
$$

The important transition is:

$$
\boxed{
\Delta Q(N) \leq \Delta C(N)
}
$$

which indicates that additional decomposition may no longer be justified under the selected cost model.

This threshold is expected to be **task-dependent**, not universal.

---

## 11. Expected Research Contribution

The intended contribution of `agent-workflow` is not a new general agent architecture.

The intended contribution is a framework and empirical analysis for answering:

> **How much agentic computation should be spent on a task, and how should that computation be allocated, before additional decomposition becomes inefficient?**

The project aims to establish that agentic system design should consider:

$$
Capability
+
Resource\ Cost
+
Coordination\ Cost
$$

rather than evaluating agent architectures only by final task quality.

---

## 12. Instructions for Code Agents

When modifying `agent-workflow`, treat this document as the research boundary.

### Prefer changes that:

* create or improve measurable experimental variables;
* improve telemetry needed to answer an RQ;
* improve controlled comparison;
* reduce measurement ambiguity;
* improve reproducibility;
* improve evidence provenance;
* test a stated hypothesis.

### Be skeptical of changes that:

* only add general-purpose capabilities;
* only increase the number of agents;
* only imitate features from another agent harness;
* add memory, DAG, specialization, or autonomy without a measurable research purpose;
* increase architectural complexity without improving experimental validity.

### Before proposing a major architectural addition, explicitly state:

```text
Research Question:
Hypothesis:
Variable Changed:
Experiment Enabled:
Expected Measurement:
Why Existing Mechanisms Are Insufficient:
Complexity Introduced:
```

If these cannot be answered, the change should not be treated as research-driven development.

---

## 13. Current Research Position

The project currently treats the following as the working thesis:

> **Agent decomposition is not inherently beneficial. Its value depends on the marginal task improvement obtained relative to the additional token, latency, human, and coordination costs introduced by the decomposition.**

This thesis remains **a hypothesis until controlled experiments validate it**.

The project's goal is therefore not to prove that fewer agents are better.

The goal is to discover:

$$
\boxed{
\text{the task-dependent point at which additional agentic computation stops being worth its cost}
}
$$
