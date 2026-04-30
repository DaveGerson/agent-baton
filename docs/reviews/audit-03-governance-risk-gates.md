# Audit: Governance, Risk & Gates

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/core/govern/` (classifier.py, policy.py, budget.py, compliance.py, escalation.py, validator.py, spec_validator.py, aibom.py, override_log.py, _redaction.py), `agent_baton/core/gates/` (ci_gate.py), `agent_baton/core/immune/` (daemon.py, cache.py, scheduler.py, sweeper.py, triage.py), `agent_baton/core/audit/` (dispatch_verifier.py)

## Executive Summary

The governance layer is architecturally mature -- hash-chained compliance logs, tamper-resistant actor identification, multi-tier budget enforcement, and redaction-before-hashing are all real and well-implemented. The biggest strength is the depth of the compliance chain infrastructure. The biggest risk is that the policy engine's enforcement is advisory at the execution layer: `require_agent` and `require_gate` rules always emit warnings rather than blocking, which means the "Regulated Data" preset's safety guarantees are aspirational unless the planner independently wires the required agents.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | A | Clean dataclass-driven architecture, excellent docstrings, consistent patterns across all 16 files |
| 2 | Acceleration & Maintainability | B | Well-structured and navigable, but BudgetEnforcer has grown organically into a 768-line file covering 5 subsystems |
| 3 | Token/Quality Tradeoffs | A | No unnecessary LLM calls in governance; classifier is keyword-based; immune sweeps use Haiku with prompt caching |
| 4 | Implementation Completeness | B | Core capabilities work end-to-end, but `require_agent`/`require_gate` enforcement has a structural hole |
| 5 | Silent Failure Risk | C | Policy engine structural requirements are downgraded to warnings; budget bead callbacks fail silently; redaction failure logs but does not halt |
| 6 | Code Smells | B | BudgetEnforcer is a borderline god class; two hash-chain implementations exist with slightly different schemas |
| 7 | User Discoverability | B | Custom policy presets are documented but the `.claude/policies/` extension point is not surfaced in user docs or CLI help |
| 8 | Extensibility | A | Clean plugin points for custom policies, sweep kinds, gate types, and redaction patterns |

## Critical Issues (Fix Now)

- **`require_agent` / `require_gate` policy rules are not enforced at the plan or execution level.** The "Regulated Data" preset claims to require `subject-matter-expert` and `auditor` agents, but `PolicyEngine.evaluate()` only emits informational warnings for these rule types (policy.py lines 563-577). A regulated-data task can proceed without an auditor, which violates the system's stated safety guarantees. **Fix**: Add a `validate_plan(policy, plan)` method to `PolicyEngine` and wire it into the executor's `start()` method.

- **Redaction failure writes un-redacted data to the compliance chain.** In `compliance.py` lines 288-296, when `redact_payload()` returns a non-dict, the code logs a warning and writes the un-redacted entry. **Fix**: Either raise an exception to halt the write, or skip the append entirely.

## Important Issues (Fix Soon)

- **BudgetEnforcer has no dedicated test file.** The 768-line class with 5 subsystems and critical safety logic has no direct test coverage.
- **Budget bead-warning callback failures are logged at DEBUG level** (budget.py lines 757-763). Budget cap signals can vanish silently.
- **Duplicate hash-chain implementations.** `ComplianceChainWriter` and `LockedJSONLChainWriter` share locking and recovery logic but have different schemas.
- **`BudgetEnforcer._ensure_predict_state()` uses `hasattr` for lazy init** -- fragile and error-prone.

## Silent Failure Inventory

| Location | Risk | Description |
|----------|------|-------------|
| `policy.py:563-577` | CRITICAL | `require_agent`/`require_gate` rules emit warnings, never block |
| `compliance.py:288-296` | HIGH | Redaction failure writes un-redacted sensitive data to the hash chain |
| `budget.py:757-763` | HIGH | Budget bead-warning callback failure swallowed at DEBUG level |
| `daemon.py:324-328` | MEDIUM | Run-ceiling bead filing uses `getattr` + bare except |
| `policy.py:462-497` | MEDIUM | Hand-rolled `**` glob matching may fail to match valid patterns |
| `escalation.py:178-180` | LOW | `add()` has a read-modify-write race |
| `classifier.py:184-222` | LOW | Substring matching on keywords can produce false negatives |
| `budget.py:529-535` | LOW | `_ensure_predict_state()` uses `hasattr` for lazy init |
