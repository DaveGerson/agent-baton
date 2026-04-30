# Audit: API & PMO

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/api/` (server.py, deps.py, middleware/, models/, routes/, webhooks/), `agent_baton/core/pmo/` (forge.py, scanner.py, store.py), `pmo-ui/src/`

## Executive Summary

The API & PMO domain is surprisingly well-structured for its size. The FastAPI backend follows clean separation of concerns with proper dependency injection, typed request/response models, and consistent error handling across ~50 endpoints spanning 16 route modules. The biggest risk is the 2950-line `pmo.py` route file, which has grown into a god module. The biggest strength is the comprehensive Pydantic model layer that gives the API a clear, well-documented contract.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | B | Clean architecture with clear contracts, marred by pmo.py gigantism and pervasive `type: ignore` annotations |
| 2 | Acceleration & Maintainability | B | Dependency injection and model layer are exemplary; adding new route modules is easy |
| 3 | Token/Quality Tradeoffs | A | API responses are right-sized with deliberate omission of large fields; no unnecessary fetching |
| 4 | Implementation Completeness | B | Every advertised endpoint works; CRP wizard is a documented stub; mock specs in the frontend |
| 5 | Silent Failure Risk | C | Multiple `except Exception: pass` blocks in best-effort paths; arch-bead review swallows storage errors |
| 6 | Code Smells | C | pmo.py is a 2950-line god module; duplicated `_resolve_project_path` patterns; inline response models in pmo_h3.py |
| 7 | User Discoverability | B | PMO UI is self-explanatory with keyboard shortcuts, tab navigation, and clear labeling |
| 8 | Extensibility | A | Route module registry, dependency injection, and EventBus subscription patterns make extension trivial |

## Critical Issues (Fix Now)

- **Arch-bead review silently discards storage failures** (`pmo_h3.py` lines 404-408). Returns success with a synthetic bead ID even when `BeadStore.write()` throws.
- **Approval log write failures are silently swallowed** in `approve_gate` and `reject_gate` (pmo.py lines 1787-1789, 1892-1897). Losing approval decisions without warning is a material risk for audit compliance.

## Important Issues (Fix Soon)

- **Split `pmo.py`** (2950 lines) into focused modules: `pmo_board.py`, `pmo_forge.py`, `pmo_execution.py`, `pmo_gates.py`, `pmo_signals.py`, `pmo_external.py`, `pmo_changelist.py`.
- **Fix `pmo_h3.py:_project_db_path()`** to use dependency-injected `get_team_context_root()`.
- **Wire the missing spec endpoints** or remove the frontend mock fallbacks.
- **Add the `beads` tab to `NAV_TABS`** in App.tsx.
- **Remove `_MOCK_SPECS`** from `client.ts`.

## Silent Failure Inventory

| Location | Risk | Description |
|----------|------|-------------|
| `pmo_h3.py:review_arch_bead` lines 404-408 | **HIGH** | `BeadStore.write()` exception caught with `pass`; success response returned |
| `pmo.py:approve_gate` lines 1787-1789 | **HIGH** | Approval log write failure silently swallowed |
| `pmo.py:reject_gate` lines 1892-1897 | **HIGH** | Same as approve_gate |
| `executions.py:_count_pending` lines 480-484 | **MEDIUM** | Returns 0 on any exception |
| `pmo_h3.py:_safe_query` lines 57-73 | **MEDIUM** | All sqlite3.Error returns `[]` |
| `client.ts:listSpecs` line 372 | **MEDIUM** | Fetch failure silently falls back to mock data |
