# Audit: Learning & Observability Pipeline

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/core/learn/` (10 files), `agent_baton/core/improve/` (13 files), `agent_baton/core/observe/` (13 files), `agent_baton/core/events/` (4 files), `agent_baton/core/observability/` (4 files)

## Executive Summary

The Learning & Observability domain is the most ambitious and architecturally sophisticated subsystem in Agent Baton. It implements a genuine closed-loop learning pipeline (detect -> analyze -> propose -> apply -> rollback) with thoughtful safety mechanisms (circuit breakers, guardrails, audit trails). The biggest risk is structural: five packages with overlapping concerns create redundancy (two Prometheus modules, two metric systems) and the event bus -- despite being well-designed -- is not wired as the primary data backbone. The biggest strength is the defense-in-depth guardrail system.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | B | Clean, well-documented code throughout; some redundancy between packages degrades the overall pattern |
| 2 | Acceleration & Maintainability | B | Good module separation and docstrings; the 5-package split creates cognitive load |
| 3 | Token/Quality Tradeoffs | B | Most analysis is read-side and lightweight; JSONL full-file reads could become expensive at scale |
| 4 | Implementation Completeness | B | The closed loop is genuinely closed; prompt evolution was cleanly retired; some features are detection-only stubs |
| 5 | Silent Failure Risk | C | Pervasive try/except swallowing throughout the learning pipeline means degradation could go unnoticed for weeks |
| 6 | Code Smells | C | observe/ vs observability/ duplication; two prometheus.py files; duplicated retro-scanning logic |
| 7 | User Discoverability | B | CLI integration exists for most features; learning engine results are queryable |
| 8 | Extensibility | A | Plugin-friendly architecture with clear interfaces; all major components accept dependency injection |

## Critical Issues (Fix Now)

- **IncidentStore is memory-only** (`observe/incidents.py` line 49): Runtime incidents stored in a Python list. Process restart loses all history. Persist to SQLite.
- **LearnedOverrides.remove_override() is a stub** (`learn/overrides.py` lines 155-174): Always returns `False`. Rollback of applied overrides cannot actually be reversed programmatically.

## Important Issues (Fix Soon)

- **Pervasive silent failure in learning engine**: Entire detection pipeline uses `except Exception: _log.debug()`. Promote to `_log.warning()` and add detection success counter.
- **observe/ vs observability/ namespace collision**: Two `prometheus.py` files with different purposes.
- **Duplicated retro scanning in PerformanceScorer** (`scoring.py` lines 258-315).
- **LookbackAnalyzer.analyze_range() double-classification** (`lookback.py` lines 140-225): First classification pass entirely discarded.
- **UsageLogger.read_all() called repeatedly per cycle**: Every consumer independently reads and parses the entire JSONL file.

## Silent Failure Inventory

| Location | Risk Level | Description |
|----------|------------|-------------|
| `learn/engine.py` lines 97-285 | **HIGH** | All five detection blocks catch `Exception` with `_log.debug()` |
| `improve/loop.py` lines 181-185 | **HIGH** | Learning engine analysis failure caught at DEBUG level |
| `observe/incidents.py` line 49 | **HIGH** | In-memory only; process crash loses all incident records |
| `improve/scoring.py` lines 323-333 | **MEDIUM** | Bead store query failures produce `avg_bead_quality=0.0` |
| `improve/loop.py` lines 195-210 | **MEDIUM** | Pattern learner refresh failures caught |
| `triggers.py` lines 173-203 | **MEDIUM** | Supplementary trigger signals silently fail |
| `observe/archiver.py` lines 186-196 | **LOW** | VACUUM failure silently swallowed |
