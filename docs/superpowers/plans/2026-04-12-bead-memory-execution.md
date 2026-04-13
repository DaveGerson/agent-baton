# Bead Memory — Execution Instructions for Opus

**Spec:** `docs/superpowers/specs/2026-04-12-bead-memory-design.md`
**Date:** 2026-04-12

---

## Overview

This document provides execution instructions for implementing the Beads-inspired
agent memory system described in the design spec. The work is organized into 4 tiers
of features. Each tier builds on the prior one. Use the baton engine for orchestration
when tackling a full tier; work directly for individual features.

---

## Pre-Execution Checklist

Before starting any tier:

1. Read the full spec: `docs/superpowers/specs/2026-04-12-bead-memory-design.md`
2. Read the schema linkage rules in `CLAUDE.md` — every schema change touches 5 files
3. Read the invariants in `docs/invariants.md` — nothing in this spec should change them
4. Create a feature branch: `git checkout -b feature/bead-memory`
5. Run existing tests to confirm green baseline: `pytest`

---

## Tier 1: Foundation (F1, F2, F5)

### Execution Order

F1 must complete first. F2 and F5 can be done in parallel after F1.

### F1. Bead Data Model and Store

**What to build:**

1. **`agent_baton/models/bead.py`** — Create `Bead` and `BeadLink` dataclasses exactly
   as specified in the Data Model section. Include `_generate_bead_id()` with progressive
   scaling (4→5→6 hex chars at 500/1500 thresholds). Include full `to_dict()`/`from_dict()`
   following the pattern in `models/knowledge.py`.

2. **`agent_baton/core/engine/bead_store.py`** — Create `BeadStore` class with methods:
   `write()`, `read()`, `query()`, `ready()`, `close()`, `link()`, `decay()`. Use the
   same `ConnectionManager` pattern as `SqliteStorage`. All SQL uses parameterized queries.
   `write()` must write both to `beads` table and `bead_tags` table in a single transaction.

3. **`agent_baton/core/storage/schema.py`** — Bump `SCHEMA_VERSION` to 4. Add `beads`
   and `bead_tags` tables to `PROJECT_SCHEMA_DDL`. Add mirror tables with `project_id`
   to `CENTRAL_SCHEMA_DDL`. Add `MIGRATIONS[4]` with `CREATE TABLE IF NOT EXISTS`.

4. **`agent_baton/core/storage/sync.py`** — Add two `SyncTableSpec` entries to
   `SYNCABLE_TABLES` after `shared_context`: `beads` (PK: `bead_id`) and
   `bead_tags` (PK: `bead_id, tag`). Order matters: beads before bead_tags (FK).

5. **`agent_baton/models/__init__.py`** — Re-export `Bead`, `BeadLink`.

**Tests to write:**
- Unit tests for `_generate_bead_id()` progressive scaling
- Unit tests for `Bead.to_dict()` / `Bead.from_dict()` round-trip
- Unit tests for `BeadStore` CRUD: write, read, query with filters, ready logic
- Integration test: schema migration from v3 → v4 on an existing DB
- Integration test: sync beads to central.db via SyncEngine

**Commit when:** All BeadStore operations work against a real SQLite DB. Tests pass.

### F2. Agent Signal Protocol

**What to build:**

1. **`agent_baton/core/engine/bead_signal.py`** — Create `parse_bead_signals()` function
   following the exact pattern of `parse_knowledge_gap()` in `core/engine/knowledge_gap.py`.
   Three signal types: `BEAD_DISCOVERY`, `BEAD_DECISION` (with `CHOSE:`/`BECAUSE:` subfields),
   `BEAD_WARNING`. Returns `list[Bead]` (one outcome can contain multiple signals).

2. **`agent_baton/core/engine/executor.py`** — In `record_step_result()`, after the
   knowledge gap protocol block (~line 739), add a bead signal block:
   ```python
   # ── Bead signal protocol ──────────────────────────────────────────
   if status in ("complete", "interrupted") and outcome and self._bead_store:
       beads = parse_bead_signals(outcome, step_id=step_id,
                                   agent_name=agent_name, task_id=state.task_id)
       for bead in beads:
           self._bead_store.write(bead)
   ```
   Instantiate `_bead_store` in `__init__` using the same `db_path` as `_storage`.

3. **`agent_baton/core/engine/dispatcher.py`** — After `_KNOWLEDGE_GAPS_LINE` (~line 264),
   add signal instructions. Create `_BEAD_SIGNALS_LINE` constant and append it to the
   delegation prompt after the knowledge gaps line.

4. **`agent_baton/core/events/events.py`** — Add `bead_created()` factory function
   with topic `"bead.created"`.

**Tests to write:**
- Unit tests for `parse_bead_signals()` with valid signals, partial signals, malformed signals
- Unit test confirming malformed signals return empty list (never raise)
- Integration test: `record_step_result()` with outcome containing BEAD_DISCOVERY produces a bead in the store
- Unit test: delegation prompt contains bead signal instructions

**Commit when:** Signals parse correctly. record_step_result writes beads. Prompt includes instructions.

### F5. `baton beads` CLI Commands

**What to build:**

1. **`agent_baton/cli/commands/bead_cmd.py`** — Module with `register(subparsers)` and
   `handler(args)`. Subcommands:
   - `baton beads list [--type TYPE] [--status STATUS] [--task TASK_ID] [--tag TAG]`
   - `baton beads show <bead-id>`
   - `baton beads ready`
   - `baton beads close <bead-id> [--summary TEXT]`
   - `baton beads link <source-id> --relates-to|--contradicts|--extends <target-id>`

2. **`agent_baton/cli/main.py`** — Register the bead command module.

**Tests to write:**
- CLI integration tests for each subcommand
- Test `baton beads list` output format
- Test `baton beads ready` returns only unblocked beads

**Commit when:** All 5 subcommands work end-to-end from the CLI.

---

## Tier 2: Value Multipliers (F3, F4, F6)

**Important:** Let Tier 1 run for 2-4 weeks of real usage before starting Tier 2.

All three features are independent — can be built in parallel.

### F3. Forward Relay

**What to build:**

1. **`agent_baton/core/engine/bead_selector.py`** — `BeadSelector` class with
   `select(bead_store, current_step, plan, token_budget=4096) -> list[Bead]`.
   Ranking: dependency-chain beads > same-phase > cross-phase. Within tiers:
   warnings > discoveries > decisions. Budget-trim to fit within token_budget.

2. **`agent_baton/core/engine/executor.py`** — In `_dispatch_action()` (~line 2124),
   call `BeadSelector.select()` and pass results to `build_delegation_prompt()`.

3. **`agent_baton/core/engine/dispatcher.py`** — `build_delegation_prompt()` gains
   `prior_beads: list[Bead] | None = None` parameter. Renders `## Prior Discoveries`
   section between Knowledge Context and Your Task sections (~line 224).

**Key constraint:** Bead injection has its own 4k token budget, separate from
knowledge's 32k. Hard cap of 5 beads per dispatch.

### F4. Planning Decision Capture

**What to build:**

Modify `core/engine/planner.py` — `create_plan()` writes decision beads at each
of its 14 numbered steps: classification, pattern lookup, agent selection, risk
assessment, phase template, gate assignment, etc. Each bead has
`bead_type="planning"`, `source="planning-capture"`, `agent_name="planner"`.

`explain_plan()` reads from BeadStore instead of `_last_*` variables. Keep
`_last_*` as fallback when no BeadStore is available.

### F6. Memory Decay

**What to build:**

1. **`agent_baton/core/engine/bead_decay.py`** — `decay_beads()` function that
   queries closed beads older than TTL, generates one-line summaries, transitions
   to `archived` status.

2. Hook into `executor.complete()` (~line 890) to trigger decay for the finished
   task's beads.

3. Add `baton beads cleanup [--ttl HOURS] [--task TASK_ID] [--dry-run]` subcommand.

---

## Tier 3: Intelligence Layer (F7, F8, F9)

All three are independent.

### F7. BeadAnalyzer

Create `core/learn/bead_analyzer.py`. Three analysis passes:
1. Warning frequency → `add_review_phase` hint
2. Discovery file clustering → `add_context_file` hint
3. Decision reversal detection → `add_approval_gate` hint

Add `PlanStructureHint` dataclass to `models/pattern.py`. Wire into
`planner.create_plan()` after pattern lookup step.

### F8. Knowledge Gap Auto-Resolution

Modify `core/engine/knowledge_gap.py` — `determine_escalation()` gains optional
`bead_store` parameter. Search for matching discoveries before applying the existing
escalation matrix. Auto-resolve by packaging bead content as `ResolvedDecision`.

### F9. Bead-to-Knowledge Promotion

Add `promote` subcommand to `bead_cmd.py`:
`baton beads promote <bead-id> --pack <pack-name>`. Converts bead to
`KnowledgeDocument` in `.claude/knowledge/<pack>/`.

---

## Tier 4: Scale and Safety (F10, F11, F12)

### F10. Central Store Sync
Verify beads sync end-to-end to central.db (infrastructure from F1).
Add analytics view `v_cross_project_discoveries` to central DDL.
Update `baton query` help text.

### F11. Conflict Detection
Add conflict detection to `bead_store.link()` for `contradicts`/`supersedes`.
Check for unresolved conflicts in `executor._determine_action()`.
Add `baton beads graph <task-id>` subcommand.

### F12. Quality Scoring
Add `quality_score`, `retrieval_count` to `Bead`. Parse `BEAD_FEEDBACK` signals.
Feed quality into `BeadSelector` ranking. Surface in `PerformanceScorer`.

---

## General Rules

1. **Tests are mandatory.** Every feature must have unit and integration tests.
   Tests run during GATE steps only, not during agent dispatch.

2. **Follow schema linkage rules.** Any schema change MUST update all 5 sync points
   documented in `CLAUDE.md`.

3. **Graceful degradation.** If `BeadStore` is unavailable (no baton.db, older schema),
   all bead features silently degrade. The engine must never fail because beads are
   missing.

4. **No new dependencies.** Everything is pure Python + SQLite. No Go, no Dolt,
   no external services.

5. **Backward compatibility.** `Bead.from_dict()` must use `.get()` with defaults
   for every field. Old databases without bead tables must not cause errors.

6. **Commit per feature.** Each F-number gets its own commit with a clear message
   referencing the feature ID.

7. **Update documentation.** After completing each tier, update:
   - `CLAUDE.md` — schema linkage rules, test count, repo structure
   - `docs/architecture.md` — if new subsystems added
   - `docs/design-decisions.md` — ADR for the Beads architecture decision
   - `README.md` — if new CLI commands added
