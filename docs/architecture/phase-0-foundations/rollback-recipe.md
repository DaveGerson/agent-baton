# Phase 0 v16 Migration — Rollback Recipe

This document covers how to safely roll back the v16 schema migration
(F0.1–F0.4 foundation primitives) if it needs to be reverted after being
applied.

---

## 1. How `_run_migrations` decides to apply v16

`ConnectionManager._run_migrations` is called from `_ensure_schema` every
time a connection is opened to a database that has already been initialised
(i.e. its `_schema_version` table exists).  The decision flow is:

1. Read `SELECT version FROM _schema_version` — call this `current`.
2. If `current < SCHEMA_VERSION` (currently 15, will be 16 after Phase 3):
   iterate from `current + 1` through `SCHEMA_VERSION` inclusive.
3. For each version in `schema.MIGRATIONS`, execute the DDL with individual
   statement splitting.  Statements that would add a column that already
   exists (`OperationalError: duplicate column name`) are silently skipped —
   this makes re-applying idempotent.
4. After all scripts succeed, `UPDATE _schema_version SET version = 16`.
5. Commit.

The migration fires on the **first** `ConnectionManager.get_connection()` call
after `configure_schema()` is called with `version=16`.  The two triggering
paths are:

* `baton plan` → `SqliteStorage.__init__` → `ConnectionManager.configure_schema`
* `baton execute start` (and any other command that instantiates `SqliteStorage`
  or `CentralStore`).

---

## 2. `MIGRATIONS_DOWN` block for v16

SQLite does not support `DROP COLUMN` before version 3.35 or `ALTER TABLE
DROP COLUMN` in older installs, so column removals require the
create-new-table-copy-rename idiom.  Add this dict to `schema.py` when
Phase 3 ships and populate it for v16:

```python
MIGRATIONS_DOWN: dict[int, str] = {
    16: """
-- v16 DOWN: remove F0.1 spec tables, F0.2 tenancy columns,
--           F0.3 audit_log table + AuditorVerdict column,
--           F0.4 knowledge_lifecycle_events table.

-- F0.1 — drop spec link table before parent (FK ordering)
DROP TABLE IF EXISTS spec_plan_links;
DROP TABLE IF EXISTS specs;

-- F0.3 — drop hash-chain audit log
DROP TABLE IF EXISTS audit_log;

-- F0.4 — drop knowledge lifecycle telemetry
DROP TABLE IF EXISTS knowledge_lifecycle_events;

-- F0.2 — drop tenancy columns from plans using the rebuild idiom.
-- Step 1: preserve data
CREATE TABLE plans_v15_restore AS SELECT * FROM plans;

-- Step 2: drop current table (has the new tenancy columns)
DROP TABLE plans;

-- Step 3: recreate at v15 shape (copy from schema.PROJECT_SCHEMA_DDL
--          plans block as of v15; omit tenant_id / workspace_id columns)
CREATE TABLE plans (
    task_id                    TEXT PRIMARY KEY,
    task_summary               TEXT NOT NULL,
    risk_level                 TEXT NOT NULL DEFAULT 'LOW',
    budget_tier                TEXT NOT NULL DEFAULT 'standard',
    execution_mode             TEXT NOT NULL DEFAULT 'phased',
    git_strategy               TEXT NOT NULL DEFAULT 'commit-per-agent',
    shared_context             TEXT NOT NULL DEFAULT '',
    pattern_source             TEXT,
    explicit_knowledge_packs   TEXT NOT NULL DEFAULT '[]',
    explicit_knowledge_docs    TEXT NOT NULL DEFAULT '[]',
    intervention_level         TEXT NOT NULL DEFAULT 'low',
    task_type                  TEXT,
    classification_signals     TEXT,
    classification_confidence  REAL
    -- (add any other columns present at v15 from PROJECT_SCHEMA_DDL)
);

-- Step 4: restore data (columns not present in v15 are dropped automatically)
INSERT INTO plans
SELECT
    task_id, task_summary, risk_level, budget_tier, execution_mode,
    git_strategy, shared_context, pattern_source,
    explicit_knowledge_packs, explicit_knowledge_docs,
    intervention_level, task_type,
    classification_signals, classification_confidence
FROM plans_v15_restore;

-- Step 5: drop staging table
DROP TABLE plans_v15_restore;

-- Step 6: restore indexes
CREATE INDEX IF NOT EXISTS idx_plans_risk  ON plans(risk_level);
CREATE INDEX IF NOT EXISTS idx_plans_type  ON plans(task_type);
""",
}
```

> **Note:** The exact column list for the `plans` rebuild must be kept in
> sync with `PROJECT_SCHEMA_DDL` at the time of the v16 migration.  Finalise
> this block in Phase 3 once the exact DDL additions are committed.

---

## 3. Shell commands to invoke backup before migration

Use `baton storage preflight` to back up both databases automatically:

```bash
# From the project root
baton storage preflight --context .claude/team-context
```

Or invoke the backup helper directly from Python:

```python
from pathlib import Path
from agent_baton.core.storage.migration_backup import backup_db

baton_bak   = backup_db(Path(".claude/team-context/baton.db"))
central_bak = backup_db(Path.home() / ".baton" / "central.db")

print(f"baton.db  backup: {baton_bak}")
print(f"central.db backup: {central_bak}")
```

Or as a one-liner shell backup (no Python required):

```bash
# Manual fallback — copy before any baton command that triggers migration
TS=$(date -u +%Y%m%dT%H%M%SZ)
cp .claude/team-context/baton.db ".claude/team-context/baton.db.bak-manual-${TS}"
cp ~/.baton/central.db ~/.baton/"central.db.bak-manual-${TS}"
```

---

## 4. Restore procedure (rolling back to v15)

```bash
# 1. Stop any running baton daemon
baton daemon stop 2>/dev/null || true

# 2. Identify the correct backup (list_backups returns sorted oldest-first)
python3 - <<'EOF'
from pathlib import Path
from agent_baton.core.storage.migration_backup import list_backups
for p in list_backups(Path(".claude/team-context/baton.db")):
    print(p)
EOF

# 3. Restore (replace live db with the chosen backup)
python3 - <<'EOF'
from pathlib import Path
from agent_baton.core.storage.migration_backup import restore_db

# Replace <timestamp> with the actual backup suffix shown above
bak = Path(".claude/team-context/baton.db.bak-15-<timestamp>")
restore_db(bak, Path(".claude/team-context/baton.db"))

central_bak = Path.home() / ".baton" / "central.db.bak-15-<timestamp>"
restore_db(central_bak, Path.home() / ".baton" / "central.db")
print("Restore complete.")
EOF

# 4. Pin SCHEMA_VERSION back to 15 in schema.py (revert the Phase 3 commit)
#    Then reinstall:
pip install -e ".[dev]"
```

---

## 5. Verification queries (confirm rollback succeeded)

Run these queries against both `baton.db` and `~/.baton/central.db`:

```sql
-- Confirm version is back to 15
SELECT version FROM _schema_version;
-- Expected: 15

-- Confirm v16 tables are gone
SELECT name FROM sqlite_master
WHERE type = 'table'
  AND name IN ('specs', 'spec_plan_links', 'audit_log', 'knowledge_lifecycle_events');
-- Expected: 0 rows

-- Confirm tenancy columns are gone from plans
PRAGMA table_info(plans);
-- Expected: no tenant_id or workspace_id columns

-- Confirm existing data is intact
SELECT COUNT(*) FROM plans;
SELECT COUNT(*) FROM executions;
SELECT COUNT(*) FROM beads;
```

If any of these checks fail, inspect the WAL sidecar (`baton.db-wal`) — a
stale WAL may contain v16 frames.  Flush it before restoring:

```sql
PRAGMA wal_checkpoint(TRUNCATE);
```

---

## 6. Upgrade procedure: rechain a pre-Phase-0 compliance log (bd-c0e0)

`compliance-audit.jsonl` files written before the F0.3 hash-chain landed
contain plain-text rows with no `prev_hash` / `entry_hash` fields.
`baton compliance verify` will return non-zero on these rows because the
chain is incomplete.  This is expected on the **first run after upgrading**
to a Phase-0 build — it does not indicate tampering.

### Symptoms

```bash
$ baton compliance verify
ERROR  compliance: row 0001 missing prev_hash
ERROR  compliance: row 0002 missing entry_hash
EXIT   non-zero
```

### Recommended sequence

1. **Back up first.**  Rechain mutates the audit log in place; if it
   aborts midway you want a known-good copy.  Use the same preflight
   helper that protects schema migrations:

   ```bash
   baton storage preflight --context .claude/team-context
   # also copy the JSONL out-of-band:
   cp .claude/team-context/compliance-audit.jsonl{,.bak-pre-rechain}
   ```

2. **Rechain.**  This walks each row in order, computes `prev_hash` from
   the prior row's `entry_hash`, and emits a fresh JSONL with the chain
   columns populated:

   ```bash
   baton compliance rechain
   ```

   Pre-existing rows keep their original `payload`; only the chain
   columns are added.  New rows written after this point append to a
   valid chain.

3. **Verify.**  Re-run the verifier — it should now exit zero:

   ```bash
   baton compliance verify
   # expected: OK <N> rows, chain intact
   ```

4. **Sanity-check row counts.**  Number of rows in the rechained file
   must equal the number in the backup:

   ```bash
   wc -l .claude/team-context/compliance-audit.jsonl \
         .claude/team-context/compliance-audit.jsonl.bak-pre-rechain
   ```

### Risk notes

- Rechain is **destructive** in the sense that it rewrites every row.
  Do not skip the preflight backup.
- Once rechained, any future tamper of an old row will be detected by
  `baton compliance verify` — the upgrade boundary is one-way.
- If you maintain offsite copies of `compliance-audit.jsonl` for
  regulatory retention, keep both the pre-rechain and post-rechain
  versions and document the upgrade event in the audit log itself.
