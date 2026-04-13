# Audit Report: PMO Subsystem & UI

**Scope:** `core/pmo/`, `models/pmo.py`, `cli/commands/pmo_cmd.py`, `pmo-ui/`
**Date:** 2026-04-13

---

## Findings

### 1. PMO Has Zero Integration with the Execution Engine — BOLT-ON

No file in `core/engine/` imports anything from `core/pmo/`. The PMO scanner (`core/pmo/scanner.py`) reads execution state to build Kanban cards, but the relationship is strictly one-directional: PMO reads from the engine, the engine never reads from PMO. PMO health metrics (`ProgramHealth.completion_pct`, `blocked`, `failed`) do not influence the planner's agent selection, risk assessment, or resource allocation. The planner (`planner.py`) has zero references to PMO.

### 2. PMO UI SSE Integration — OK ✓

The PMO UI (`pmo-ui/src/hooks/usePmoBoard.ts:27,96`) connects to `/api/v1/pmo/events` via `EventSource` (SSE), backed by the API route at `api/routes/pmo.py:439-445` which subscribes to the shared `EventBus`. Falls back to polling at 5-second intervals when SSE is unavailable, slows to 15-second intervals when SSE is live. Well-implemented.

However, underlying data is only as fresh as what the scanner produces — the scanner re-reads execution state files on every poll, so latency depends on how quickly state files are written by the executor.

### 3. PMO Data Freshness Depends on Missing Step Events

Because the executor does not emit step-level domain events (see storage/sync/events audit), the PMO scanner must poll execution state files to detect step completions. With no step events flowing through the event bus, the PMO UI cannot show real-time step progress — it can only detect phase-level transitions.

---

## Summary Table

| Finding | Category | Key File:Line |
|---------|----------|---------------|
| PMO never feeds back into planner/executor | BOLT-ON | `core/engine/` has zero PMO imports |
| PMO UI SSE integration | OK | `usePmoBoard.ts:96`, `pmo.py:439` |
| PMO step-level freshness limited by missing step events | PARTIAL | executor.py:694 (no step event publish) |

## Verdict

PMO is a well-built **read-only dashboard** over execution state. The UI integration (SSE + polling fallback) is solid. The gap is that the relationship is one-directional — execution data flows to PMO for display, but PMO insights (blocked projects, health trends, resource bottlenecks) never flow back to inform planning or execution decisions.
