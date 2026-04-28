# Spec: Velocity & Quality Roadmap (The "Engine First" Strategy)

**Version:** 2.0 — April 2026
**Status:** DRAFT
**Owner:** Orchestrator
**Context:** This roadmap pivots Agent Baton away from operational oversight and toward maximizing software development velocity and output quality. It focuses on the technical accelerators that turn the system into a compounding intelligence asset.

---

## Wave 1: High-Throughput Parallelism
**Goal:** Enable multiple agents to work concurrently without friction or collisions.

### 1.1 Automated Git Worktree Isolation
*   **Problem:** Sequential execution is the primary bottleneck. Parallel execution in a single tree causes index locks and file collisions.
*   **Requirement:** Implement `WorktreeManager` to spin up isolated git worktrees for every parallel branch in a `MachinePlan`.
*   **Velocity Impact:** Enables 3-10× throughput by allowing true concurrent development across backend, frontend, and tests.

### 1.2 Declarative `baton.yaml` Workflow
*   **Problem:** Manual CLI configuration is overhead.
*   **Requirement:** A project-level config to define auto-routing, default quality gates, and agent preferences.
*   **Velocity Impact:** Reduces task setup time from minutes to seconds.

---

## Wave 2: Compounding Institutional Memory
**Goal:** Formalize the processes for Knowledge Graph and Context Profile creation so the system gets smarter with every commit.

### 2.1 Formalized Knowledge Graph Synthesis
*   **Problem:** Beads are currently flat records. Relationships must be inferred manually.
*   **Process Requirement:** Implement a `BeadSynthesizer` that runs post-phase. It must:
    1.  **Infer Edges:** Use semantic similarity and file-overlap to automatically link beads (e.g., "Feature A" extends "Architecture B").
    2.  **Cluster Discovery:** Group related discoveries into "Knowledge Nodes."
    3.  **Conflict Detection:** Automatically flag contradictory beads (e.g., two agents making different assumptions about an API).
*   **Quality Impact:** Prevents regression and ensures all agents work from a unified, evolving understanding of the codebase.

### 2.2 Formalized Agent Context Profile Harvesting
*   **Problem:** Agents start every task as "strangers" to the project.
*   **Process Requirement:** Implement a `ContextHarvester` that executes after every successful `task_completed` event.
    1.  **Expertise Extraction:** Summarize the specific patterns and files the agent mastered during the task.
    2.  **Strategy Recording:** Note which implementation strategies worked (passed gates) vs. which failed.
    3.  **Persistent Storage:** Store in an `agent_context` table, indexed by `(agent_name, domain)`.
*   **Velocity Impact:** Eliminates "cold start" token waste. Agents arrive at the next task already knowing the project's conventions and pitfalls.

---

## Wave 3: Precision & Verification
**Goal:** Tighten the loop between planning, execution, and quality.

### 3.1 Expected Outcome (Demo Statements)
*   **Problem:** Passing tests != accomplishing the goal.
*   **Requirement:** Require an `expected_outcome` for every plan step. This becomes the primary prompt for the `code-reviewer` and `test-engineer` agents.
*   **Quality Impact:** Shifts the focus from "no errors" to "behavioral correctness."

### 3.2 Automated Handoff Synthesis
*   **Problem:** Context is lost during handoffs between agents.
*   **Requirement:** Auto-generate a "Handoff Bead" that summarizes the `git diff`, active discoveries, and remaining blockers from the previous agent.
*   **Velocity Impact:** Reduces the "re-orientation" phase for the next agent in the sequence.

---

## Wave 4: Integration & Unblocking
**Goal:** Connect to external systems to automate the "last mile" of delivery.

### 4.1 CI-Driven Quality Gates
*   **Problem:** Local gates can't always replicate production environments.
*   **Requirement:** Direct integration with GitHub Actions/GitLab CI. The orchestrator dispatches the agent and then waits for the CI check to pass.
*   **Quality Impact:** Guarantees that agent-produced code meets the exact same standards as human PRs.

---

## Removed/Deferred (Governance & Overhead)
The following items from the previous roadmap have been removed to focus on velocity:
- **PMO Dashboard:** Deferred (secondary to engine performance).
- **Cost Accounting:** Removed (operational overhead).
- **Incident Response Tooling:** Removed (replaced by higher-quality verification).
- **Chargeback Systems:** Removed.
