# Audit: CLI Surface

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/cli/` -- all files including `main.py`, `colors.py`, `formatting.py`, `errors.py`, `_context.py`, `_override_helper.py`, and all command groups under `commands/`

## Executive Summary

The CLI is a large, well-organized surface with strong conventions in its core execution path (`execute.py`, `plan_cmd.py`) and good plugin architecture for command discovery. The biggest risk is the sheer breadth: approximately 70+ subcommands across 8 command groups, with `execute.py` alone at 2300+ lines acting as the monolithic protocol surface. Code quality in the core path is high, but the periphery has duplicated context-root resolution logic (at least 4 separate implementations), inconsistent error handling patterns across command groups, and several experimental/stub commands.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | B | Strong patterns in core execution; peripheral commands drift from conventions |
| 2 | Acceleration & Maintainability | B | Plugin architecture is excellent; `execute.py` monolith is the maintenance bottleneck |
| 3 | Token/Quality Tradeoffs | A | `--terse` mode, sidecar prompt files, and JSON output all actively reduce token burn |
| 4 | Implementation Completeness | B | Core workflow is solid; `run.py` is a stale duplicate of `execute.py run`; some stubs exist |
| 5 | Silent Failure Risk | B | Most paths print to stderr; auto-viz and auto-sync swallow exceptions silently |
| 6 | Code Smells | C | execute.py is a 2300-line god module; `_resolve_context_root` duplicated 4 times; `run.py` vs `execute.py run` overlap |
| 7 | User Discoverability | B | Grouped help epilog is good; 70+ commands overwhelm new users; no progressive disclosure |
| 8 | Extensibility | A | Auto-discovery plugin architecture; `register()`/`handler()` contract is clean and works |

## Critical Issues (Fix Now)

- **`run.py` is likely broken or stale.** It uses `MachinePlan.parse_obj(data)` (Pydantic v1 API), constructs `ExecutionEngine()` with no arguments (contradicting every other construction site), and delegates to `BatonRunner`. This command should either be removed, marked deprecated, or brought in sync with `execute.py`'s `_handle_run`.

## Important Issues (Fix Soon)

- **execute.py should be decomposed.** The 2300-line monolith. Extract handler functions for each subcommand group.
- **Unify `_resolve_context_root` into one implementation.** Four copies are a ticking time bomb.
- **Standardize error handling across all commands.** Commands that use `print("Error: ...")` + `return` (exit code 0) should use `user_error()` from `errors.py`.
- **Dual registration of `handoff`.** Resolve whether `baton handoff` and `baton execute handoff` should both exist.

## Silent Failure Inventory

| Location | Pattern | Risk Level |
|----------|---------|------------|
| `execute.py:969-975, 1122-1128, 1209-1216, 1339-1346` | `except Exception: pass` on auto-viz | Low |
| `daemon.py:302-313` | `print("Error: ..."); return` (exit 0 on failures) | **Medium** |
| `run.py:83-89, 99-104` | `print(color_error(...)); return` (exit 0 on failures) | **Medium** |
| `bead_cmd.py:946-971` | `_query_bead_edges_for` returns `[]` on any DB exception | Low |
