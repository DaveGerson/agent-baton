# Executable Specification: The Complete Agent Baton Roadmap

**Version:** 3.1 — April 2026 (The Comprehensive Vision with Escalation)
**Implementation Status:** READY
**Target Branch:** `feature/comprehensive-roadmap`

## Introduction: Scaling to the 20x Frontier with Responsibility

To achieve a true 20x increase in engineering productivity, Agent Baton must evolve into a **continuous, predictive compute engine** that fundamentally changes how software is built. However, this extreme scale must be balanced with the reality of token costs, operational visibility, and enterprise governance. 

This specification combines all strategic tracks into a unified, six-wave roadmap. It builds the foundation for widespread adoption, establishes rigorous operational controls, accelerates the core engine for maximum velocity, and pushes the boundary of autonomous engineering—all while strictly managing API consumption through stepped escalation.

---

## Wave 1: Foundation & Onboarding (The Setup)
**Goal:** Reduce adoption friction and harden the execution environment for true parallel work.

### 1.1 PyPI Packaging (`pip install agent-baton`)
*   **Requirement:** Package `agent-baton` for PyPI distribution, bundling all default agent definitions and reference procedures.
*   **Impact:** A solo developer can go from zero to running parallel agents in under 5 minutes.

### 1.2 Declarative `baton.yaml` Configuration
*   **Requirement:** Implement a central, version-controlled `baton.yaml` in the project root to define default agents, risk thresholds, and gate configurations.
*   **Impact:** Replaces fragmented CLI flags and env vars with a governable, reviewable config.

### 1.3 Automated Git Worktree Isolation
*   **Requirement:** Implement `WorktreeManager` to spin up isolated git worktrees (`.claude/worktrees/{task_id}/{step_id}`) for every parallel branch in a `MachinePlan`.
*   **Impact:** Enables true concurrent development without index locks or file collisions, turning the system from sequential to massively parallel.

---

## Wave 2: Operations, Visibility, & Cost Control
**Goal:** Provide the necessary oversight and financial accountability to scale agent usage across an enterprise.

### 2.1 Real-time PMO Dashboard (SSE)
*   **Requirement:** Wire existing SSE API endpoints into the React frontend for real-time status updates and token counters.
*   **Impact:** Transforms the UI from a static report into a live control plane for engineering managers.

### 2.2 Cost Accounting & Chargeback (Token Cost Mitigation)
*   **Requirement:** Attribute every token-consuming action to a cost center (project/team) with hierarchical budget allocation and anomaly detection.
*   **Impact:** Ensures the "20x productivity" doesn't result in runaway API bills. Hard limits automatically pause agents that exceed their budgets.

### 2.3 CI-Driven Quality Gates
*   **Requirement:** Direct integration with GitHub Actions/GitLab CI. The orchestrator dispatches the agent and waits for the remote CI check to pass.
*   **Impact:** Guarantees that agent-produced code meets the exact same production standards as human-produced code.

---

## Wave 3: Precision Verification & Incident Response
**Goal:** Tighten the loop between planning, execution, quality, and post-mortem analysis.

### 3.1 Expected Outcomes (Demo Statements)
*   **Requirement:** Add `expected_outcome` to `PlanStep`. This becomes the primary prompt for `code-reviewer` and `test-engineer` agents.
*   **Impact:** Shifts the focus of gates from "no errors" to "behavioral correctness."

### 3.2 Automated Handoff Synthesis
*   **Requirement:** Auto-generate a "Handoff Bead" that summarizes the `git diff` and active discoveries from the previous agent.
*   **Impact:** Reduces the token cost of context transfer between sequential agents by eliminating the need for the new agent to re-read everything.

### 3.3 Incident Response & Decision Reconstruction
*   **Requirement:** Tooling to reconstruct the exact prompt, context, and decision chain (beads + trace) for any past commit.
*   **Impact:** Crucial for governance. When an agent breaks production, the team can instantly audit *why* the decision was made.

---

## Wave 4: Compounding Institutional Memory
**Goal:** Formalize the processes that make the framework smarter over time, reducing the token cost of "cold starts."

### 4.1 Formalized Knowledge Graph Synthesis
*   **Requirement:** Implement a `BeadSynthesizer` that runs post-phase to infer semantic edges (extends, contradicts) and cluster discoveries into Knowledge Nodes.
*   **Impact:** Evolves beads from a flat list into a queryable graph, preventing regressions and duplicate research.

### 4.2 Formalized Agent Context Profiles
*   **Requirement:** Implement a `ContextHarvester` to extract an agent's mastered patterns and successful strategies after every task, storing them in an `agent_context` table.
*   **Impact:** Eliminates "cold start" token waste. Agents arrive at the next task already knowing the project's conventions.

---

## Wave 5: Unblocking the Human-Agent Loop
**Goal:** Optimize for recovery and seamless human intervention when agents fail or get stuck.

### 5.1 Seamless Developer Takeover (The Escape Hatch)
*   **Requirement:** `baton execute takeover <step_id>` pauses the engine and drops the developer into the agent's isolated worktree to manually fix a failing test, recording the fix before resuming.
*   **Impact:** Turns a frustrating 30-minute state-reconstruction task into a 2-minute surgical intervention.

### 5.2 Targeted Self-Healing Micro-Agents with Stepped Escalation (Token Cost Mitigation)
*   **Requirement:** When a gate fails, dispatch a fast, cheap model (e.g., Haiku) with *only* the diff and the `stderr` output, instructed to generate a patch fix. 
*   **Stepped Escalation:** If the Haiku micro-agent fails to resolve the issue after 2 attempts, the engine automatically escalates the failing diff and error context to a more capable model (e.g., Sonnet). If Sonnet fails, it escalates back to the original heavy model (e.g., Opus) with the full codebase context, as the bug is likely structural rather than a simple typo.
*   **Impact:** Dramatically slashes the token cost of iterative bug-fixing for simple errors, while retaining the intelligence required to fix deep logical flaws.

### 5.3 Budget-Aware Speculative Pipelining with Handoffs
*   **Requirement:** Dispatch next-phase agents speculatively into background worktrees while waiting for human approval or CI.
*   **Stepped Escalation & Handoff:** Speculative execution is initiated by cheap models (Haiku) drafting the initial scaffolding and boilerplate. If the human approves the direction, the engine seamlessly hands off the scaffolded worktree to a larger model (Sonnet/Opus) to complete the complex implementation logic.
*   **Impact:** Hides CI and human-approval latency without incurring unacceptable API costs. Heavy lifting is only performed *after* human validation of the initial direction.

---

## Wave 6: The 20x Frontier (Extreme Scale & Evolution)
**Goal:** Push the orchestration model to its absolute limits, evolving from a reactive assistant to a continuous compute engine.

### 6.1 Next-Generation Bead Architecture (The Gastown Evolution)
*   **Git-Native Bead Persistence (Branch-Aware Memory):** Move the primary bead store to Git (e.g., `git notes`). An agent's memory becomes perfectly synchronized with the codebase's version history, merging and branching alongside the code.
*   **Persistent Agent "Souls":** Agents gain persistent, cryptographic identities (e.g., `agent_auth_specialist_f7x`). The engine routes by expertise, sending tasks to the specific identity that authored the relevant code and holds the most context.
*   **Executable Beads (Procedural Memory):** Allow Beads to store verified bash scripts, AST-manipulation commands, or specialized test harnesses. Agents execute these to automatically verify state, building their own custom tooling over time.

### 6.2 Extreme Scale Engineering Flow
*   **Massive Swarm Refactoring (AST-Aware Execution):** For massive migrations, the orchestrator parses the AST, partitions call sites into independent chunks, and dispatches a swarm of 100 micro-agents across a massive Worktree array. A 6-month migration completes in 20 minutes.
*   **The "Immune System" (Continuous Background Autonomy):** A subset of specialized agents run continuously in daemon mode at low priority, sweeping for untested edge cases or deprecated APIs. To manage token costs, this runs on a strict daily budget and utilizes heavily cached context.
*   **Zero-Latency Predictive Computation:** A background observer agent watches filesystem events as a developer types. It predicts the intent and pre-computes speculative implementations in background worktrees using cheap models. The developer simply hits "Tab" to accept a massive, multi-file feature generated in the negative space of their typing latency.
