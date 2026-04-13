# Audit Report: Governance Subsystem

**Scope:** `core/govern/` — policy, compliance, classification, escalation, validation
**Date:** 2026-04-13

---

## Findings

### 1. Policy Violations Never Block Execution — PAPER-TIGER

At `planner.py:800`, the comment is explicit:

> "Violations are recorded as warnings; they never hard-block plan creation."

Policy violations — including `severity="block"` rules like `require_auditor`, `no_bash_on_data`, and `append_only_historical` in the `regulated` preset — are collected into `_last_policy_violations` and rendered as "Policy Notes" in the plan markdown (`planner.py:1042-1051`). The plan is created regardless. Neither the executor nor the dispatcher imports `PolicyEngine`. The "block" severity in `PolicyRule` (`policy.py:86`) is semantically meaningless during execution.

### 2. Compliance Reports Never Generated During Execution — BOLT-ON

`ComplianceReportGenerator` and `ComplianceEntry` (`core/govern/compliance.py`) are imported by zero files in `core/engine/` or `core/runtime/`. The compliance module's own docstring (lines 13-18) describes a lifecycle where "the executor creates ComplianceEntry objects for each agent dispatch" — but the executor never does this. The compliance system is a data model with no writer.

### 3. EscalationManager Disconnected from Execution Loop — BOLT-ON

The executor (`executor.py:56`) imports `determine_escalation` from `knowledge_gap.py` — a separate, engine-local escalation matrix. This is NOT the same as `EscalationManager` from `core/govern/escalation.py`, which manages a markdown file at `.claude/team-context/escalations.md`. There are **two parallel escalation systems** that do not communicate: the engine's knowledge-gap matrix (inline, automatic) and the governance module's file-based escalation queue (manual CLI only).

### 4. spec_check and validate Are Standalone — CLI-ONLY

`baton validate` validates agent `.md` file structure. `baton spec-check` validates agent output against a schema. Neither is called by the planner or executor. `SpecValidator` is used inside `GateRunner.evaluate_output()` (`gates.py:193-202`), but only for `gate_type="spec"` gates, and the default gate set does not include a spec gate. A spec gate must be explicitly added to a plan.

### 5. DataClassifier Output Does Not Constrain Execution — PAPER-TIGER

The `DataClassifier` is wired into the planner (`plan_cmd.py:116`) and influences risk level and guardrail preset selection (`planner.py:683-700`). But the classification only affects: (a) plan markdown annotations, (b) which `PolicySet` is loaded for advisory-only validation, and (c) whether approval gates are added. The classified risk level does NOT cause the executor to enforce data handling policies at runtime. Once agents are dispatched, they operate without runtime policy enforcement.

---

## Summary Table

| Finding | Category | Key File:Line |
|---------|----------|---------------|
| Policy violations never block plan creation or execution | PAPER-TIGER | `planner.py:800` |
| ComplianceReport/Entry never instantiated during execution | BOLT-ON | `compliance.py:13-18` |
| EscalationManager disconnected from engine; two parallel systems | BOLT-ON / CLI-ONLY | `executor.py:56` vs `escalation.py:102` |
| spec-check and validate not in automated pipeline | CLI-ONLY | `spec_check.py`, `validate.py` |
| DataClassifier influences plan text, not runtime enforcement | PAPER-TIGER | `planner.py:683-700` |

## Verdict

Governance is a **bolt-on** system. The `DataClassifier` and `PolicyEngine` are wired into the planner at plan-creation time, but their output is advisory text appended to plan markdown. Nothing in the executor enforces policies at runtime. The compliance module has no writer. The escalation module has no reader in the engine. The governance subsystem has the architecture of an enforcement layer but the runtime behavior of a reporting layer.
