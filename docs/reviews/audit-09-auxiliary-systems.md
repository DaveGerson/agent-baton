# Audit: Auxiliary Systems

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/core/swarm/` (4 files), `agent_baton/core/specs/` (1 file), `agent_baton/core/intel/` (5 files), `agent_baton/core/config/` (1 file), `agent_baton/core/runtime/` (12 files), `agent_baton/core/release/` (5 files), `agent_baton/core/orchestration/` (5 files), `agent_baton/testing/` (1 file), `agent_baton/utils/` (1 file), `agent_baton/visualize/` (6 files)

## Executive Summary

The auxiliary systems domain is the largest audit surface (41 files across 10 subdirectories) and houses a wide maturity spectrum: from production-hardened runtime infrastructure (`runtime/`, `orchestration/`) to solidly-built supporting subsystems (`intel/`, `release/`, `visualize/`) to properly feature-gated experimental work (`swarm/`). The biggest strength is consistent best-effort error handling throughout. The biggest risk is the `orchestration/` vs `core/engine/` boundary overlap, which creates cognitive load for contributors.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | B | Consistently idiomatic Python; minor duplication in utility helpers |
| 2 | Acceleration & Maintainability | B | Most subsystems navigable quickly; orchestration/engine boundary is confusing |
| 3 | Token/Quality Tradeoffs | A | Intel subsystem is zero-LLM by design; debate falls back to stub; knowledge ranker is pure arithmetic |
| 4 | Implementation Completeness | B | Runtime stack is production-complete; swarm is properly gated; coalescer and reconciler are not yet integrated |
| 5 | Silent Failure Risk | B | Best-effort patterns are pervasive and appropriate; a few swallowed exceptions deserve audit beads |
| 6 | Code Smells | B | Some duplication (`_utcnow`, `_format_duration`); `router.py` `detect_stack` is a 300-line method |
| 7 | User Discoverability | C | Release tools, debate, and knowledge ranking are powerful but poorly surfaced in user-facing docs |
| 8 | Extensibility | B | Registry pattern is solid; config is declarative; swarm is locked to Python-only via libcst |

## Critical Issues (Fix Now)

- **Type violation in `InteractiveDecisionManager.get()`** (`runner.py:78-80`): Returns `DecisionResolution` instead of `DecisionRequest | None`, violating the parent class contract. Works by coincidence because both types have a `status` field.

- **Engine state mutation bypass** (`worker.py:486-489`): When gate retries are exhausted without a `DecisionManager`, the worker directly mutates `_state.status = "failed"`, bypassing the engine's state machine validation. Fix: add a `fail_execution()` method to the `ExecutionDriver` protocol.

## Important Issues (Fix Soon)

- **Coalescer and reconciler are disconnected from the dispatch path**: `SwarmDispatcher._execute_swarm()` never calls them. If swarm is ever enabled, conflicts will not be detected.
- **Sequential dispatch in "parallel" swarm**: Implement steps dispatched in a `for` loop despite `execution_mode="parallel"`.
- **`detect_stack()` god method** (`router.py:114-382`): 268 lines. Decompose into private helpers.
- **`_utcnow()` duplication**: Defined identically in 7 files.
- **`persist_debate()` skips WAL mode** (`debate.py:405`): Opens raw `sqlite3.connect()`.

## Silent Failure Inventory

| Location | Risk | Description |
|----------|------|-------------|
| `runner.py:78-80` | HIGH | Wrong return type works by coincidence |
| `worker.py:486-489` | HIGH | Direct state mutation bypasses engine state machine |
| `worker.py:354` | MEDIUM | `shell=True` subprocess for automation commands |
| `reconciler.py:286` | MEDIUM | Accesses private attributes across objects |
| `debate.py:405-423` | LOW | No WAL mode; concurrent writes could corrupt |
| `context_harvester.py:212` | LOW | All harvest failures swallowed at debug level |
| `supervisor.py:466-476` | LOW | PID file descriptor not cleaned up on GC |
