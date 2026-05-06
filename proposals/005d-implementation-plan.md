# Proposal 005d: Advanced Agent Routing - Implementation Plan

**Status:** Proposed / Design Phase
**Epic:** Epic 3 (Stabilization & Usability)
**Target:** `agent_baton/core/orchestration/router.py` and `planner.py`

## 1. Executive Summary

This document outlines the phased implementation strategy for **Proposal 005d**. The goal is to replace the fragile file-presence routing heuristics (which caused routing misfires in polyglot monorepos like BUG-004) with a robust, weighted scoring system. Additionally, it enables multi-agent composite assignment within a single execution plan, allowing specialized agents (e.g., Node frontend and Python backend engineers) to collaborate on the same task.

---

## 2. Phase 1: Weighted Stack Detection & LLM Fallback (Weeks 1-2)

**Goal:** Build a robust, depth-aware workspace scanner that leverages cost-effective LLMs when heuristics are ambiguous.

1.  **Create Detector:** Create `agent_baton/core/orchestration/detector.py`.
2.  **Implement Weighted Scoring:** Instead of halting at the first manifest (e.g., `pyproject.toml`), scan the workspace to a configurable depth.
    *   `pyproject.toml` (root): +10 Python
    *   `package.json` (depth > 1, e.g., `ui/`): +3 Node
    *   `.go` files presence: +5 Go
3.  **LLM-Assisted Disambiguation:** When heuristics yield close scores (e.g., Python: 10, Node: 10) or when no clear primary stack emerges, invoke a cost-effective LLM (like Claude 3 Haiku or Sonnet) with a scoped view of the directory structure to determine the stack intent. This avoids the fragility of pure heuristics while remaining cost-neutral.
4.  **Define `CompositeStackProfile`:** Instead of returning a single string, the detector returns an object containing all identified stacks and their confidence scores, with the highest score designated as the primary.

---

## 3. Phase 2: Model Enhancements (Week 3)

**Goal:** Allow granular agent targeting at the phase and step level.

1.  **Update `Phase` Model:** Modify `agent_baton/models/execution.py` to allow agent overrides on a per-phase basis.
    ```python
    class Phase(BaseModel):
        id: str
        name: str
        required_agent: Optional[str] = None # Overrides project default
    ```
2.  **Update `PlanStep` Model:** Ensure steps can individually declare an agent assignment differing from the global execution run.

---

## 4. Phase 3: Planner Integration (Weeks 4-5)

**Goal:** Empower the `IntelligentPlanner` to design polyglot plans.

1.  **Integrate with CapabilityAnalyzer:** Update the `CapabilityAnalyzer` (introduced in Proposal 005b) to utilize the `CompositeStackProfile`.
2.  **Subtask Roster Creation:** If the detector identifies both Python (primary, 10) and Node (secondary, 3), allow the planner to assign `backend-engineer--python` to API tasks and `frontend-engineer--node` to UI tasks within the *same* `MachinePlan`.

---

## 5. Phase 4: Routing Resolution & Fast Inference Fallback (Week 6)

**Goal:** Ensure the router correctly translates base agent names into flavored instances, leveraging LLMs for edge cases.

1.  **Update `AgentRouter`:** Modify `agent_baton/core/orchestration/router.py` to accept a `CompositeStackProfile`.
2.  **Flavor Matching Heuristics:** When routing an agent (e.g., `backend-engineer`), the router should check the requested step's context against the composite profile to determine if it should route to the primary flavor (Python) or a secondary flavor (Node/Go) based on the specific path the step operates in.
3.  **Fast LLM Fallback:** If a step's path is ambiguous regarding the requested flavor (e.g., a shared `scripts/` directory in a polyglot monorepo), fallback to a fast, cost-neutral Haiku/Sonnet inference call. Provide the LLM with the step description and a scoped directory listing to choose the optimal agent flavor dynamically.
