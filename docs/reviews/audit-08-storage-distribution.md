# Audit: Storage & Distribution

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/core/storage/` (all files), `agent_baton/core/distribute/` (all files), `scripts/` (install.sh, install.ps1, build_dist.sh, sync_bundled_agents.sh), `templates/` (CLAUDE.md, settings.json, skills/, playbooks/)

## Executive Summary

The storage layer is the strongest subsystem reviewed in this codebase. The SQLite backend is well-architected with proper transactional semantics, WAL mode, thread-safe connection management, and a clean protocol-based abstraction. The migration system works but carries a **critical silent data loss bug**: a duplicate dictionary key (`16`) in `schema.py` silently drops the deployment profiles migration DDL. The distribution layer is solid and handles edge cases well.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | A | Excellent separation of concerns, consistent patterns, thorough docstrings |
| 2 | Acceleration & Maintainability | A | Adding new entity types follows a clear, repeatable pattern across 8+ stores |
| 3 | Token/Quality Tradeoffs | B | Storage I/O is efficient; minor bloat from defensive column-exists checks in load paths |
| 4 | Implementation Completeness | B | All 4 adapters are complete; file_backend remains for backward compat (by design) |
| 5 | Silent Failure Risk | C | Duplicate MIGRATIONS key silently drops DDL; broad except clauses in several stores |
| 6 | Code Smells | B | Duplicate dict key is the main smell; some long methods in sqlite_backend.py |
| 7 | User Discoverability | B | Install scripts are interactive and well-documented; migration/backup docs are thin |
| 8 | Extensibility | A | Protocol-based storage, self-registering adapters, clean extension points throughout |

## Critical Issues (Fix Now)

- **Duplicate MIGRATIONS key 16 in `schema.py`** (lines 416 and 488): The first `16:` entry (deployment_profiles + releases) is silently overwritten by the second `16:` entry (tenancy columns). While later migrations (v21, v26) recover the lost tables, this is a structural bug that proves the migration system lacks a uniqueness check. **Fix**: Add a test that validates MIGRATIONS keys are unique.

## Important Issues (Fix Soon)

- **Broad `except Exception` in specialized stores**: `conflict_store.py`, `handoff_store.py`, `user_store.py` catch all exceptions and return empty values. Catch `sqlite3.Error` and `OSError` specifically.
- **INSERT OR REPLACE on retrospectives and traces**: Uses DELETE+INSERT approach unlike safe upsert for executions. Future FK CASCADE child would be silently destroyed.
- **Dead code in `deployment_profile_store.py`**: Lines 81-85 execute a query whose result is never used.
- **`save_execution` and `load_execution` are too long**: Both exceed 200 lines.

## Silent Failure Inventory

| Location | Risk | Description |
|----------|------|-------------|
| `schema.py` lines 416/488: duplicate MIGRATIONS key 16 | HIGH (mitigated by v26) | Deployment profiles DDL silently dropped |
| `connection.py` line 189: `"no such table"` skip | MEDIUM | Missing prerequisite table causes migration step to be silently skipped |
| `conflict_store.py` / `handoff_store.py` / `user_store.py` | MEDIUM | Disk-full/permission errors converted to "no data" |
| `sqlite_backend.py` line 1164: INSERT OR REPLACE on retrospectives | LOW | Future FK CASCADE child would be silently destroyed |
| `file_backend.py` lines 109-113: read-modify-write race | LOW | Concurrent step completions could lose data (legacy backend only) |
