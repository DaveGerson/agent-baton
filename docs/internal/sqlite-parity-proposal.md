# SQLite Parity Proposal

**Status:** Draft (design only — no production-code conversions in this branch)
**Top-line recommendation in one sentence:** Treat this as a **multi-phase migration** — land a single small structural piece first (Phase A: scalar columns on the `executions` row + roundtrip parity for the cheap fields) so the SQLite backend immediately starts approaching parity, then peel collection/nested fields out into normalized tables phase-by-phase, with the optimistic-concurrency-control work landing **after** the Pydantic Phase 1 leaf-type slice has shipped.

---

## 1. Field-by-field archeology

Source location for every missing field: `agent_baton/models/execution.py` (`ExecutionState` is the legacy `@dataclass` declaration; the Pydantic migration is pre-Phase-1 and these are still dataclass fields).

| # | Field | Line | Type | Default | `to_dict` line | Why probably missing from SQLite |
|---|-------|------|------|---------|----------------|----------------------------------|
| 1 | `delivered_knowledge` | 1408 | `dict[str, str]` (doc-key → step_id) | `{}` | 1529 | Session-level dedup added with knowledge resolver. Mutated by `dispatcher.build_delegation_prompt` (`dispatcher.py:267,289,313,465`) and persisted via `executor.py:6072,6081`. Plumbed only through file backend. |
| 2 | `consolidation_result` | 1412 | `ConsolidationResult \| None` (15 sub-fields, 2 are list-of-objects — `execution.py:1306-1319`) | `None` | 1530 | Added with `CommitConsolidator` (Wave 1.x). Only ever assigned at end of execution by `executor.py:3328`. JSON-only persistence; never normalized. |
| 3 | `force_override` | 1418 | `bool` | `False` | 1535 | F0.3 / bd-f606 (auditor VETO override). Trivial scalar, simply not added to `executions` row. |
| 4 | `override_justification` | 1419 | `str` | `""` | 1536 | Same wave as `force_override`; same omission. |
| 5 | `step_worktrees` | 1424 | `dict[step_id, dict]` (serialised `WorktreeHandle`) | `{}` | 1538 | Wave 1.3 (bd-86bf). Read by `worktree_manager.py:1006` from JSON. SQLite never received the table. |
| 6 | `takeover_records` | 1441 | `list[dict]` (active record marked by empty `resumed_at`) | `[]` | 1543 | Wave 5 / bd-e208 (Human-Agent Loop, takeover). Heavy mutation in `executor.py:3047,3049,3104,3125,3231,3238,3321`. JSON only. |
| 7 | `selfheal_attempts` | 1443 | `list[dict]` | `[]` | 1544 | Wave 5 / bd-1483. JSON only. |
| 8 | `speculations` | 1445 | `dict[spec_id, dict]` (also doubles as a misc `_phase_retries` store via `phase_manager.py:310-314`) | `{}` | 1545 | Wave 5 / bd-9839. Mirror copy held in `Speculator._speculations` (`speculator.py:190`); state field is the canonical persisted form. JSON only. |
| 9 | `run_cumulative_spend_usd` | 1452 | `float` | `0.0` | 1549 | End-user readiness #7. Read/written by `BudgetGovernor` (`govern/budget.py:217,695-733`). Trivial scalar; never plumbed. |
| 10 | `pending_scope_expansions` | 1456 | `list[dict]` | `[]` | 1550 | Scope-expansion subsystem; processed at phase boundaries. Heterogeneous-shape list; never plumbed. |
| 11 | `scope_expansions_applied` | 1457 | `int` | `0` | 1551 | Counter incremented by `executor.py:3932`. Trivial scalar. |

Two additional fields are also lossy via the SQLite path even though they were not in the user's enumerated list:

- `steps_ran_in_place: dict[step_id, str]` (line 1428)
- `working_branch: str`, `working_branch_head: str` (lines 1431, 1434, bd-def9)
- `pending_approval_request: PendingApprovalRequest | None` (line 1470, Hole 1 fix)

The existing `tests/models/test_execution_sqlite_roundtrip.py:7-10` explicitly says the SQLite backend "is intentionally lossy for some fields" — that comment should be removed once parity lands.

**Pattern.** Every missing field shares one history: it was added inside a behaviour wave that updated `to_dict`/`from_dict` on the dataclass and the file persistence path, but did not earn a `schema.py` entry, a migration, or a `sqlite_backend.py` upsert/load block. That is consistent with a stalled refactor, not a deliberate exclusion: `schema.py` shipped 35 migrations, none of which touch `executions` for these fields.

---

## 2. Schema design

### 2.1 Per-field shape decisions

Two viable shapes:
- **(A) JSON blob columns** on `executions` for opaque or fast-evolving payloads.
- **(B) Normalised child tables** for collections with stable shapes that benefit from indexed access.

Pick (B) when the field is queried, large enough to make selective fetch worthwhile, or mutated with high frequency. Otherwise (A).

| # | Field | Shape | Rationale |
|---|-------|-------|-----------|
| 1 | `delivered_knowledge` | (B) `delivered_knowledge(task_id, doc_key, first_step_id, delivered_at)` PK `(task_id, doc_key)` | Pure dedup map; small, append-only with idempotent writes. May be queried by PMO and retro tooling. |
| 2 | `consolidation_result` | (A) `executions.consolidation_result_json TEXT NULL` | Singleton; written once at run end; nested `attributions: list[FileAttribution]` not queried by sub-field. JSON column avoids 4 child tables for 1-shot data. |
| 3 | `force_override` | Scalar `executions.force_override INTEGER NOT NULL DEFAULT 0` | Boolean. |
| 4 | `override_justification` | Scalar `executions.override_justification TEXT NOT NULL DEFAULT ''` | Free-text scalar. |
| 5 | `step_worktrees` | (B) `step_worktrees(task_id, step_id, worktree_path, branch, base_branch, created_at, ran_in_place_reason TEXT NULL)` PK `(task_id, step_id)` | Stable shape (`WorktreeHandle`). Frequent lookup by `step_id`. Folds in `steps_ran_in_place` as a nullable column. |
| 6 | `takeover_records` | (B) `takeover_records(task_id, takeover_id, started_at, started_by, resumed_at, resumed_by, scope, reason)` PK `(task_id, takeover_id)` | "Active record" predicate (`resumed_at IS NULL`) is queried — `executor.py:3047-3125` and `resolver.py:235-237` walk the list. Indexing on `(task_id) WHERE resumed_at IS NULL` makes that O(1). |
| 7 | `selfheal_attempts` | (B) `selfheal_attempts(task_id, attempt_id, step_id, started_at, status, escalated_to, cost_usd, ...)` PK `(task_id, attempt_id)` | Stable shape per Wave 5. |
| 8 | `speculations` | (A+B split) `speculations(task_id, spec_id, started_at, target_step_id, status, payload_json)` + `executions.phase_retries_json TEXT NOT NULL DEFAULT '{}'` for the misc `_phase_retries` map | The dict is bimodal: real speculation records (stable) and a `_phase_retries` slot used as scratchpad by `phase_manager.py:310-314`. Pull retries out into a dedicated column. |
| 9 | `run_cumulative_spend_usd` | Scalar `executions.run_cumulative_spend_usd REAL NOT NULL DEFAULT 0.0` | Read on resume, hit on every BudgetGovernor turn. |
| 10 | `pending_scope_expansions` | (A) `executions.pending_scope_expansions_json TEXT NOT NULL DEFAULT '[]'` | Free-form `list[dict]`; not worth normalising until shape locks. |
| 11 | `scope_expansions_applied` | Scalar `executions.scope_expansions_applied INTEGER NOT NULL DEFAULT 0` | Counter. |
| extra | `working_branch`, `working_branch_head` | Scalar columns | Strings. |
| extra | `pending_approval_request` | (A) `executions.pending_approval_request_json TEXT NULL` | Singleton typed object; nullable. JSON keeps the migration small. |

### 2.2 Concrete DDL — split into v36-v40 (one logical group per migration)

```sql
-- v36: scalar columns on executions
ALTER TABLE executions ADD COLUMN force_override            INTEGER NOT NULL DEFAULT 0;
ALTER TABLE executions ADD COLUMN override_justification    TEXT    NOT NULL DEFAULT '';
ALTER TABLE executions ADD COLUMN run_cumulative_spend_usd  REAL    NOT NULL DEFAULT 0.0;
ALTER TABLE executions ADD COLUMN scope_expansions_applied  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE executions ADD COLUMN working_branch            TEXT    NOT NULL DEFAULT '';
ALTER TABLE executions ADD COLUMN working_branch_head       TEXT    NOT NULL DEFAULT '';

-- v37: JSON-blob columns on executions for singletons / queue
ALTER TABLE executions ADD COLUMN consolidation_result_json    TEXT;
ALTER TABLE executions ADD COLUMN pending_scope_expansions_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE executions ADD COLUMN pending_approval_request_json TEXT;
ALTER TABLE executions ADD COLUMN phase_retries_json            TEXT NOT NULL DEFAULT '{}';

-- v38: delivered_knowledge child table
CREATE TABLE IF NOT EXISTS delivered_knowledge (
    task_id        TEXT NOT NULL,
    doc_key        TEXT NOT NULL,
    first_step_id  TEXT NOT NULL,
    delivered_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (task_id, doc_key),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_dk_step ON delivered_knowledge(task_id, first_step_id);

-- v39: step_worktrees + folded steps_ran_in_place reason
CREATE TABLE IF NOT EXISTS step_worktrees (
    task_id              TEXT NOT NULL,
    step_id              TEXT NOT NULL,
    worktree_path        TEXT NOT NULL DEFAULT '',
    branch               TEXT NOT NULL DEFAULT '',
    base_branch          TEXT NOT NULL DEFAULT '',
    head_sha             TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL DEFAULT '',
    ran_in_place_reason  TEXT,                        -- non-NULL ⇒ degraded execution
    payload_json         TEXT NOT NULL DEFAULT '{}',  -- forward-compat for new WorktreeHandle keys
    PRIMARY KEY (task_id, step_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- v40: human-agent loop tables (takeover, selfheal, speculations)
CREATE TABLE IF NOT EXISTS takeover_records (
    task_id      TEXT NOT NULL,
    takeover_id  TEXT NOT NULL,
    started_at   TEXT NOT NULL DEFAULT '',
    started_by   TEXT NOT NULL DEFAULT '',
    resumed_at   TEXT,                                 -- NULL ⇒ active
    resumed_by   TEXT,
    scope        TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (task_id, takeover_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_takeover_active ON takeover_records(task_id) WHERE resumed_at IS NULL;

CREATE TABLE IF NOT EXISTS selfheal_attempts (
    task_id     TEXT NOT NULL,
    attempt_id  TEXT NOT NULL,
    step_id     TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    cost_usd    REAL NOT NULL DEFAULT 0.0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (task_id, attempt_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS speculations (
    task_id         TEXT NOT NULL,
    spec_id         TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT '',
    target_step_id  TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (task_id, spec_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
```

### 2.3 Migration of existing data

- **`baton.db`:** new columns/tables don't exist anywhere yet (no migrations write them). All `ALTER TABLE ... DEFAULT` migrations are forward-safe; new tables start empty. No back-fill.
- **`.claude/team-context/*.json`:** the migrator at `core/storage/migrate.py:208-230` (`_migrate_executions`) currently drops every one of these fields silently. Once the schema lands, `_insert_execution` must be extended to copy the new fields/tables; one-line addition per field.

### 2.4 `sqlite_backend.py` impact

- `save_execution` (line 88+): extend `INSERT ... ON CONFLICT` on `executions` with new scalar/JSON columns; add `DELETE+INSERT` blocks for the new child tables.
- `load_execution` (line 347+): join the new tables, JSON-decode the new columns; pass to `ExecutionState(...)`.
- `save_step_result`, `delete_execution`: unaffected (cascades handle deletion).

Estimated diff: ~250 LOC added to `sqlite_backend.py`, ~60 LOC of DDL across migrations v36-v40, ~120 LOC of new tests under `tests/storage/`.

---

## 3. Concurrency design

### 3.1 What exists today

From `core/storage/connection.py:80-92`:
- `journal_mode=WAL` — concurrent readers don't block a writer.
- `foreign_keys=ON`, `busy_timeout=5000` (5 s).
- One connection per thread (via `threading.local`).
- All public writes wrapped in `with conn:` (`sqlite_backend.py:117`), giving a single implicit transaction per logical save.

This handles **multi-thread, single-process** writers correctly. **It does not protect multi-process writers from logically overwriting each other's state.** Two processes running `baton execute run` on the same `task_id`:
1. Read the same `executions` row.
2. Mutate disjoint in-memory `ExecutionState` graphs.
3. Each call `save_execution`, `INSERT … ON CONFLICT DO UPDATE`-ing the row plus `DELETE+INSERT` on `step_results`, etc.
4. Last-write-wins. Earlier writer's appended `step_result` is preserved (keyed by `(task_id, step_id)`); a `current_step_index` mid-flight bump is silently overwritten. `takeover_records` (DELETE+INSERT pattern) would be more dangerous because the rebuild is wholesale.

The user's stated concurrency goal — multiple plans against the same codebase simultaneously — is already partially safe **across different `task_id`s**: WAL serialises writes per-DB, every table is keyed by `task_id`. The collision case is "two processes, one task_id" — the resume / takeover / orchestrator-restart scenario.

### 3.2 What needs to change for safe multi-process: optimistic concurrency control

```sql
ALTER TABLE executions ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
```

Change writes from blind upsert to CAS-style:

```sql
UPDATE executions
   SET status=?, current_phase=?, current_step_index=?, ...,
       version = version + 1,
       updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
 WHERE task_id = ?
   AND version = ?            -- the version the caller observed at load
```

If `cur.rowcount == 0`, raise a typed `ConcurrentModificationError` (new entry in `core/engine/errors.py`). Orchestrator-side handler: re-load → re-apply delta → retry the save (bounded retries; on persistent conflict, fail loudly).

This requires `ExecutionState` to carry the loaded version. Cleanest fit: a transient `_loaded_version: int` field that does **not** appear in `to_dict` (Pydantic: `Field(exclude=True, default=0)`). **The field belongs on `ExecutionState` once Pydantic Phase 1 lands** — adding it here would create double migration work, so this lands in a follow-up phase.

### 3.3 Cross-machine

Out of scope. SQLite-over-NFS is a known footgun; `flock()` doesn't work reliably on most network filesystems. If two machines share state, the answer is "promote to a server-side store" (Postgres, Litestream replication). Not engineered here.

### 3.4 PMO UI subscription

PMO reads via `pmo_sqlite.py` and `api/routes/pmo.py`. WAL readers see a consistent snapshot at `BEGIN` and never block writers. There is **no push notification path** today — UI polls. Adding `version` to `executions` lets the API return `version` to clients so they can detect "underlying state moved" without polling diffs. Real subscriptions could ride on the existing `events` table + an SSE endpoint — not part of this proposal.

### 3.5 Worktree isolation interplay

`BATON_WORKTREE_ENABLED=1` mandate: worktrees today share the same `.claude/team-context/baton.db` (worktree only isolates the working tree, not the metadata directory). That is the **correct** sharing model — what's missing is the OCC layer above it.

---

## 4. File backend deprecation path

Today `executor.py:_save_execution` performs **dual-write** (SQLite primary + file dual-write, lines 626-633). Once SQLite is at parity:

**Recommended path (3 stages):**

1. **Stage 1 — parity reached.** SQLite is lossless. Keep file backend as the dual-write fallback; emit `DeprecationWarning` from `FileStorage.__init__` when used as primary. `detect_backend()` already prefers SQLite for new projects.
2. **Stage 2 — migrator promotion.** `baton storage migrate` becomes the recommended command. After running, the file backend drops to "snapshot/export only" — no more dual-write.
3. **Stage 3 — read-only fallback.** Remove `FileStorage` from the `StorageBackend` factory; keep a thin `dump_state_to_json(task_id)` helper for export. New CLI verb: `baton execute export <task_id> --to plan.json`.

**In-flight projects with `.claude/team-context/*.json`:** existing `StorageMigrator` (`migrate.py`) handles import; just needs extension to write the 11 new fields/tables once schema lands.

**`baton execute resume` semantics:** with OCC `version` added, `_load_execution` returns the version token and `resume` operates on it. No user-visible change.

**Recommendation: do not delete the file backend until at least one full release cycle after Stage 1.** Keep available for `BATON_DB_PATH=` users who want JSON.

---

## 5. Interaction with the in-flight Pydantic migration

Phase 0 just landed. Phase 1 (leaf-type slice) not started.

**Recommendation: SQLite parity work runs in parallel but commits AFTER Phase 1 for the model-touching parts.**

- **Phases A and B (storage-only)** can ship today, before Phase 1, because they are pure additions to `core/storage/` and `schema.py`. They do not require any model change.
- **Phases C and D (which add `_loaded_version: int` to `ExecutionState` and remove `getattr(state, "...", default)` dances)** should land **after** Pydantic Phase 1. Reasons:
  1. Adding a new field to `@dataclass` then immediately migrating to Pydantic doubles the work.
  2. Phase 1's `validate_assignment` decisions interact directly with how OCC retries reassign fields.
  3. The `getattr(state, "field", default)` legacy guards (16+ sites) are a Pydantic-cleanup task anyway; do them once.
- **Phase 1 leaf-type slice does not need to be paused for storage parity work.** They are decoupled.

**Coordinated dependency to flag:** when Phase 1 introduces `model_dump()`/`model_validate()` everywhere, the SQLite parity code must use those instead of `to_dict()`/`from_dict()`. Mechanical (swap method names), but if storage parity ships in the gap, an engineer must come back and migrate.

---

## 6. Concrete recommendation: multi-phase, signed off per phase

| Phase | Summary | Touches | Risk | Sign-off |
|-------|---------|---------|------|----------|
| **A** | Migration v36 (scalars on `executions`) + read/write plumbing in `sqlite_backend.py`. Lift `force_override`, `override_justification`, `run_cumulative_spend_usd`, `scope_expansions_applied`, `working_branch`, `working_branch_head` into the existing executions row. Add roundtrip assertions. | `schema.py`, `sqlite_backend.py`, tests | Low | Yes |
| **B** | Migrations v37-v40 (JSON columns + child tables for the 4 collection-shaped fields and singletons). Extend `sqlite_backend.py`. | Same files + new test fixtures | Medium (DELETE+INSERT semantics on takeover_records under save_execution) | Yes |
| **C** | post-Phase 1 Pydantic: add `_loaded_version` to `ExecutionState`. Add OCC `version` column + migration. CAS update path in `save_execution`. New `ConcurrentModificationError` typed exception. | `models/execution.py`, `errors.py`, storage, tests | Higher (changes write semantics) | Yes |
| **D** | File-backend deprecation: warning, then snapshot-only, then removal-from-factory. `baton execute export`. Doc updates. | CLI, `file_backend.py`, docs | Low | Yes |

**Smallest first step that makes meaningful progress = Phase A.** It:
- Closes 6 of the 11 missing fields (cheap scalars).
- Validates migration discipline (a v36 against schema at v35).
- Forces Phase B's writer to not bundle scalars and children together.
- Lays foundation for Phase C without needing the Pydantic migration.
- Has zero risk to running executions (column adds with defaults are forward-compatible).

**It does NOT** address the user's stated concurrency motivation. Phase C is what does that. Honest framing: "Phases A-B reach lossless parity; Phase C is where 'multiple baton plans against the same codebase simultaneously' becomes safe."

---

## 7. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | A migration that adds child tables runs against a live `baton.db` while a `baton execute` is in flight | Low | Medium | Migrations run inside a single transaction at first connection open. WAL ensures the writer sees a consistent view. Existing `migration_backup.py` policy applies. |
| 2 | DELETE+INSERT on new collection tables silently drops a record added by another process between read and write | Medium under multi-process | High (loses takeover audit) | Exact case Phase C (OCC) addresses. Until C lands, document concurrent processes per task_id are not yet safe. |
| 3 | Pydantic migration changes `to_dict` semantics; storage parity code references the old method | Medium | Low (mechanical) | Storage code centralises every `to_dict()` call inside `sqlite_backend.py` — single grep + replace when Phase 1 lands. |
| 4 | `_phase_retries` extraction from `speculations` breaks anything walking the dict expecting both keys | Medium | Medium | Walk all readers (`phase_manager.py:310`, `executor.py:3371`, `resolver.py:441`) before splitting; gate behind a feature flag in Phase B. |
| 5 | `consolidation_result_json` blob grows unbounded if many `attributions` accumulate | Low | Low | Currently the only writer is at execution end (`executor.py:3328`); per-execution it is small. Document a 1 MB warning threshold. |
| 6 | `BATON_DB_PATH` users on shared filesystems hit cross-machine locking issues | Low | Medium | Document explicitly: "BATON_DB_PATH must point to a local filesystem." Out of scope. |
| 7 | Multiple worktrees pointing at the same `baton.db` don't pin OCC version per-worker, leading to false-positive conflicts | Low | Medium | `version` is per `(task_id)`, not per `(task_id, worker)`. False positives manifest as transient retries — acceptable. |

---

## 8. Implementation guidance (enough to build Phase A)

1. **Append to `schema.py` `MIGRATIONS` dict, key `36`,** the v36 DDL block from §2.2.
2. **Bump `SCHEMA_VERSION` to 36** at line 43.
3. **Update `PROJECT_SCHEMA_DDL`** (the `CREATE TABLE executions` block at line 1185) to include the same six new columns inline so fresh DBs match migrated DBs.
4. **In `sqlite_backend.py`:**
   - Extend `INSERT … ON CONFLICT DO UPDATE` at lines 122-150 with the six new columns.
   - Extend `ExecutionState(...)` reconstruction at lines 546-567 with the six new keyword arguments.
5. **In `migrate.py` `_insert_execution`** (lines 671-700): add the same six columns to the `executions` INSERT.
6. **In `tests/models/test_execution_sqlite_roundtrip.py`:**
   - Set non-default values for all six fields on the `_minimal_execution_state` fixture.
   - Add `test_execution_sqlite_phase_a_scalar_fields_roundtrip` asserting each survives.
   - Re-scope the "intentionally lossy" comment at lines 7-10 to the still-lossy fields until Phase B.
7. **Run `pytest tests/storage tests/models/test_execution_sqlite_roundtrip.py`** — the only test sweep this phase needs.

The same recipe scales to Phases B-D.

### 8.1 Optional prototype script

A self-contained "prototype" exercising the simplest design (single state_blob column with OCC) — not wired into the engine, used for design validation only — would live at `agent_baton/core/storage/state_blob_prototype.py`. The multi-table proposal is the recommendation; the prototype gives a comparison point of "we could just do this and call it done" for design review.

---

## 9. Blocking questions the user must answer

1. **Are normalised collection tables (Phase B) preferred over a single `state_blob` JSON column?** The latter ships in 1 day; the former takes ~1 week and is the right long-term answer. The proposal recommends normalised.
2. **Is a hard dependency on Pydantic Phase 1 acceptable for the OCC work (Phase C)?** Proposal says yes; if user wants concurrency safety **before** Phase 1, `_loaded_version` has to be added to the dataclass and re-migrated in Phase 1, which is wasted effort but feasible.
3. **Is there appetite for `baton storage doctor`** to detect inconsistent SQLite/file dual-write (the warning at `executor.py:611-622`) and offer a one-shot reconciliation? Out of scope but Phase D depends on the answer.
4. **Cross-machine mode:** confirm out of scope. If yes, drop the cross-machine risk; if no, this proposal needs a different concurrency primitive.

---

## Files referenced

- `agent_baton/models/execution.py` — `ExecutionState` definition; missing fields at lines 1408, 1412, 1418, 1419, 1424, 1441, 1443, 1445, 1452, 1456, 1457; serialisation at 1513-1604.
- `agent_baton/core/storage/sqlite_backend.py` — `save_execution` at line 88, `load_execution` reconstruction at line 546.
- `agent_baton/core/storage/schema.py` — `SCHEMA_VERSION = 35` at line 43, `executions` DDL at line 1185, `MIGRATIONS` dict around line 47.
- `agent_baton/core/storage/connection.py` — WAL/PRAGMA setup at lines 80-92.
- `agent_baton/core/storage/file_backend.py` — `FileStorage`.
- `agent_baton/core/storage/migrate.py` — JSON-to-SQLite migrator; `_insert_execution` at line 671.
- `agent_baton/core/engine/executor.py` — dual-write at `_save_execution` (line 596).
- `agent_baton/core/engine/persistence.py` — file-side `StatePersistence` (Phase D deprecation target).
- `tests/models/test_execution_sqlite_roundtrip.py` — the existing parity test.
- `docs/internal/pydantic-migration-mutation-audit.md` — Phase-0 artifact.
