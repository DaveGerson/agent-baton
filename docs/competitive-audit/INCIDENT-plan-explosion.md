# Incident: Plan Explosion During Audit Remediation

**Date:** 2026-04-17
**Severity:** Process failure (no code impact)

## What Happened

During conversion of the audit remediation spec into baton execution plans, the operator (Claude) created 27 individual baton plans — one per work item. This was guided by the brainstorming skill's "transition to implementation" step, which framed each spec item as a separate planning unit.

## Why This Is Wrong

1. **Schema migration v10 spans items A2, A4, and A6** — these MUST be a single plan or the migration gets applied three times with conflicts
2. **Phase 0 items share test infrastructure** — testing daemon gate parity (0.1) and gate retry (0.5) against the same test fixtures makes no sense as separate executions
3. **27 plans create 27 branches, 27 trace records, 27 retrospectives** — the observability overhead exceeds the work itself
4. **Baton plans are designed for coherent work units** — a "phase" in the spec IS the right granularity for a baton plan, not individual line items

## Root Cause

The operator followed the spec item-by-item mechanically instead of recognizing that the spec's phase groupings (0, A, B, C, D, E) already represent the correct baton plan granularity. The first plan (the umbrella Phase 0 plan) was actually correct — it should have been the template for the rest.

## Correct Approach

Create 6 baton plans (one per phase), not 27. Each plan bundles the related items as steps within a multi-phase execution. The spec's dependency chain already defines the inter-plan dependencies.

## Action

Consolidate to 6 phase-level plans. Archive the 27 individual plans.
