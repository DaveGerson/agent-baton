# Pipeline Gap Closure — Make the Execution Engine Work End-to-End

**Date:** 2026-03-24
**Status:** Approved

## Problem

Unit tests (2828) pass, but the execution pipeline has 8 bugs that prevent real end-to-end operation. The system is functionally scaffolding — plan creation, execution loop, SQLite storage, and query API all work in isolation but fail when composed.

## Bugs to Fix

### Bug 1: Silent exception swallowing in `_save_execution` (CRITICAL)
**File:** `agent_baton/core/engine/executor.py:152-160`
**Problem:** `except Exception: pass` — if SQLite save fails, data is silently lost. The engine continues as if the save succeeded but the next `_load_execution` returns stale state.
**Fix:** Log the exception and re-raise (or fall back to file persistence with a warning). Never silently discard execution state.

### Bug 2: Silent exception swallowing in `_load_execution` (CRITICAL)
**File:** `agent_baton/core/engine/executor.py:162-176`
**Problem:** `except Exception: return None` — engine thinks "no state" instead of "error". Causes record methods to raise RuntimeError("no active execution state") even though state exists.
**Fix:** Log and re-raise. If SQLite fails, fall back to file persistence with warning.

### Bug 3: File persistence stale when SQLite is primary (HIGH)
**File:** `agent_baton/core/engine/executor.py:152-160`
**Problem:** When `storage` is SQLite, `_save_execution` only writes to SQLite. The file-based `execution-state.json` stays at its initial state. Components that read files get stale data.
**Fix:** When SQLite is primary, also write to file persistence as a best-effort sync. This keeps file readers working during transition.

### Bug 4: PMO scanner reads only file persistence (HIGH)
**File:** `agent_baton/core/pmo/scanner.py`
**Problem:** `PmoScanner` uses `StatePersistence` to read execution states. It never checks `baton.db`. SQLite-backed executions are invisible on the Kanban board.
**Fix:** Scanner should check for `baton.db` first via `get_project_storage()`, fall back to file scan.

### Bug 5: `execute list/switch` reads only StatePersistence (HIGH)
**File:** `agent_baton/cli/commands/execution/execute.py`
**Problem:** `list` and `switch` subcommands call `StatePersistence.list_executions()` directly. SQLite-backed executions don't appear.
**Fix:** Check StorageBackend first, fall back to file list.

### Bug 6: Retrospective captures `gate_results: 0` (MEDIUM)
**File:** `agent_baton/core/engine/executor.py` — `_build_retrospective_data()`
**Problem:** Gate counting logic doesn't properly extract from `state.gate_results`. The retrospective always reports 0 gates.
**Fix:** Count directly from `state.gate_results` list.

### Bug 7: Trace sometimes not saved to SQLite (MEDIUM)
**File:** `agent_baton/core/engine/executor.py` — `complete()`
**Problem:** Trace save may hit the same silent-exception pattern. The `_save_retro` and trace paths need the same error-handling fix.
**Fix:** Apply the same logging/fallback pattern from Bug 1-2 fixes to all storage helper methods.

### Bug 8: No end-to-end integration test (HIGH)
**Problem:** All tests are unit-level. The full pipeline (plan → start → dispatch → record → gate → complete → retrospective → query) has never been tested as a single flow.
**Fix:** Add `tests/test_pipeline_e2e.py` — exercises the complete lifecycle with both file and SQLite backends, verifying all data persists correctly.

## Architecture Principle

**Dual-write during transition:** When SQLite is the primary backend, also write to file persistence as best-effort. This means:
- SQLite is authoritative (read from it first)
- File persistence is a compatibility layer (readers that haven't been updated yet still work)
- No data loss if SQLite write fails — file write still happens, error is logged
- Eventually, file writes can be removed once all readers are updated

## Implementation Order

1. Fix executor error handling (bugs 1-2, 7) — all storage helpers
2. Add dual-write to executor (bug 3)
3. Update PMO scanner to check SQLite (bug 4)
4. Update execute list/switch to check SQLite (bug 5)
5. Fix retrospective gate counting (bug 6)
6. Add end-to-end integration test (bug 8)

## Success Criteria

- Full execution loop works with SQLite backend: plan → dispatch → record → gate → complete
- Retrospective captures actual gate pass/fail counts
- Trace saved to SQLite after completion
- PMO scanner sees SQLite-backed executions
- `execute list` shows SQLite-backed executions
- End-to-end test passes with both backends
- All 2828+ existing tests still pass
