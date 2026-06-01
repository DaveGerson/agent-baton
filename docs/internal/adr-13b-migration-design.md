# ADR-13b Phases 2–3 — Implementation Design

*Status: design (architect, 2026-06-01). Source of truth for the `bd`-backend
cutover. See `docs/design-decisions.md` ADR-13b.*

`bd` becomes the bead system of record; the SQLite bead store is removed.

## Crux

`BeadSynthesizer.synthesize(conn)` and `HandoffSynthesizer` are fed a **raw
sqlite3 connection** via `self._bead_store._conn()` (executor
`_synthesize_beads_post_phase` ~line 1003; handoff ~6748; consumed in
`dispatcher.build_delegation_prompt` ~508). `BdBeadStore` exposes no `_conn()`.
Derived data (edges/clusters/handoffs) is baton-private analytics with no `bd`
equivalent → it moves to a dedicated, rebuildable projection DB.

## 1. Direct-SQLite bead sites & fixes (inventory)

- `cli/commands/bead_cmd.py`
  - `_get_bead_store` / `_get_or_create_bead_store` → use `make_bead_store(...)`, drop `gastown_dual_write`. Keep `_resolve_db_path()`.
  - `_get_active_task_id` → **keep** (`active_task` is not a bead table).
  - `_query_bead_edges_for` + `_handle_graph` synth-edge portion → `DerivedBeadStore.edges_for(...)`.
  - `_handle_synthesize` → `synthesize_beads(store, derived)` (no `store._conn()`).
  - `_handle_clusters` → `DerivedBeadStore.clusters()`.
  - `_handle_handoffs` → `DerivedBeadStore.handoffs(task_id)`.
  - `_handle_create_exec` → script body via bd metadata (§3); relocate `compute_script_sha`.
- `core/intel/bead_synthesizer.py` → read beads via `store.query(status="open", limit=N)`; write edges/clusters to derived conn. New sig `synthesize(beads, derived_conn)`.
- `core/intel/handoff_synthesizer.py` → read via store; persist handoff rows to derived DB.
- `core/learn/bead_analyzer.py` → **no change** (already uses `store.query()`).
- `core/engine/executor.py:~623` → `make_bead_store(...)`; hold a `DerivedBeadStore`. Lines 1003 & 6748 → pass derived store, not raw conn.
- `core/engine/dispatcher.py:~508` → handoff param becomes `DerivedBeadStore`/`None`.
- `core/runtime/claude_launcher.py:~752` → `make_bead_store(...)`.
- `api/routes/pmo_h3.py` `list_arch_beads` (308), `review_arch_bead` (385), `list_beads` (502), `_collect_scorecard` → `make_bead_store(...).query(...)`.
- `api/routes/metrics.py:_collect_open_beads` → store `query(status="open")` + Python group-by.
- `api/routes/noc.py` (central.db cross-project) → §5.
- `core/storage/sync.py:136-137` → §5.
- `core/storage/schema.py` bead tables: `beads`, `bead_tags`, `bead_anchors` + central mirror + analytics view → drop in Phase 3. `bead_edges`, `bead_clusters`, `handoff_beads` → move to derived DB schema.
- `core/exec/runner.py` → script load/store via bd metadata (§3). `auditor_gate.py` → no change.
- `core/engine/notes_adapter.py`, `notes_replication.py`, `bead_anchors.py` (ADR-13a) → remove in Phase 3; relocate `compute_script_sha`/`script_ref_for`.

## 2. Derived data — decision: small SQLite projection DB

New `agent_baton/core/storage/derived_bead_store.py` → `DerivedBeadStore` over
`.claude/team-context/baton-derived.db` holding `bead_edges`, `bead_clusters`,
`handoff_beads` (DDL moved verbatim; bead IDs become plain text, no FK).
API: `connection()`, `edges_for(bead_ids)`, `clusters()`, `handoffs(task_id)`.
Rebuildable/disposable cache — created on init, no migration-backup ceremony.

Flow: `bd (.beads/)` → `store.query(open)` → `synthesize(beads, derived_conn)`
→ `baton-derived.db` → read by `baton beads graph/clusters/handoffs` + executor
post-phase refresh.

Rejected: drop features (breaks CLI contracts); recompute-on-demand (O(n²) +
loses append-only handoff history).

## 3. Executable beads

- Subtype reconstruction in `bd_mapping.bd_issue_to_bead`: when
  `metadata.baton.bead_type == "executable"`, build `ExecutableBead.from_dict(blob)`.
- Script bodies move from git notes → bd metadata under `baton.exec_script_body`
  (add `script_body` field to `ExecutableBead`). `runner.store()` sets it before
  `store.write`; `runner._load_script()` returns `bead.script_body`. Keep
  `script_sha`/`script_ref` as identity/info. Bodies are small; no CAS.
- Trust-boundary: bodies now ride in `.beads/issues.jsonl`; existing
  `_emit_trust_boundary_warning_if_external` already covers it — note in docs.

## 4. PMO UI — keep wire shape stable

UI is near-zero change: `/api/v1/pmo/beads` response model stays. Backend swaps
`BeadStore(...)` → `make_bead_store(...)`; `list_arch_beads` filters tags in
Python. Single frontend change: add `executable` to `BeadType` union +
`BEAD_TYPE_COLOR`/`BEAD_TYPE_LABEL` in `pmo-ui/src/api/beads.ts`; refresh stale
header comment. No new endpoint required (synth edges aren't surfaced to UI).

## 5. Sync — drop replication; export-based central projection

Remove `beads`/`bead_tags` `SyncTableSpec`s from `sync.py`. For NOC cross-project
counts, add `export_beads_to_central()` that upserts a minimal projection
(`project_id, bead_id, bead_type, status, agent_name, created_at`) from each
project's `bd list --json` / `.beads/issues.jsonl` (reuse `adapters/beads.py`).
`noc.py` query unchanged. Reject bd-federation (large, future).

## 6. Cutover order (green between commits; default stays sqlite until F)

- **A.** Derived DB + synthesizer/handoff refactor + executor/dispatcher rewire + CLI read sites. (green)
- **B.** Replace all direct `BeadStore(...)` with `make_bead_store(...)`. (green)
- **C.** Executable-bead body migration + subtype reconstruction + relocate `compute_script_sha`. (green)
- **D.** PMO/metrics rerouting + UI `executable` type. (green)
- **E.** Sync rerouting + `export_beads_to_central()`. (green)
- **F.** Flip `bead_backend._DEFAULT_BACKEND = "auto"` + installer/env defaults. (behavioral cutover)
- **G.** Remove SQLite bead tables (+ migration/backup), ADR-13a notes mirror, `BeadStore`, `gastown_dual_write`, `BATON_GASTOWN_ENABLED`.
- **H.** Test sweep → target `make_bead_store` with `BATON_BD_BACKEND=bd`; prune SQLite-only cases.

## 7. Dispatch packages (file-boundary ownership; worktree isolation)

- **WP-1 backend (engine+derived+exec):** `derived_bead_store.py` (new),
  `bead_synthesizer.py`, `handoff_synthesizer.py`, `executor.py` (~623/1003/6748),
  `dispatcher.py` (~508), `claude_launcher.py` (~752), `bd_mapping.py` (subtype),
  `exec/runner.py`, `exec/script_hash.py` (new), `models/bead.py` (script_body),
  `bead_backend.py` (flip gated to step F).
- **WP-2 backend (CLI+API+sync):** `bead_cmd.py`, `api/routes/pmo_h3.py`,
  `api/routes/metrics.py`, `api/routes/noc.py`, `core/storage/sync.py` +
  `export_beads_to_central()`. *Depends on WP-1's `DerivedBeadStore` interface —
  land that interface first (thin commit) or stub it.*
- **WP-3 frontend (pmo-ui):** `src/api/beads.ts` (+ Bead* views only if rendering
  the new type). Fully parallel.
- **WP-G backend (teardown, after F):** `schema.py`, `migrate.py`, notes/anchors
  removal, `bead_store.py` retire, install scripts, docs.
- **WP-H test-engineer (after WP-G interface removals):** all bead test files +
  new `tests/storage/test_derived_bead_store.py`, `tests/intel/` synth tests; add
  a static lint banning `BeadStore(`/`._conn()` outside `bead_store.py`.

## Rollback runbook (migration safety, step 2.1)

The destructive work is Phase 3 (step G): dropping `beads`/`bead_tags`/
`bead_anchors` and the central mirror. Safety net:

1. **Automatic pre-migration backup.** `core/storage/migrate.py` runs
   `migration_backup.backup_db()` before applying, writing a self-contained
   snapshot `<db>.bak-<schema_version>-<timestamp>` (WAL checkpointed first).
   The step-G DROP migration MUST go through the normal migration path so this
   fires — never DROP out-of-band.
2. **Reversible migration.** The step-G migration is paired with a documented
   down-path: restore the pre-migration backup (the SQLite bead tables are not
   recreated by a forward migration once removed, so restore-from-backup is the
   supported rollback rather than an inverse DDL migration).
3. **Restore command.** `scripts/restore_baton_db.sh [--list] [--file BACKUP]`
   lists snapshots and restores the newest (or a named one), snapshotting the
   current DB first so the restore is itself reversible, and clearing stale
   `-wal`/`-shm` sidecars.
4. **`bd` data is independent.** `.beads/` (the new source of record) is not
   touched by a `baton.db` rollback; `bd backup` covers the bead data itself.
5. **Derived DB is disposable.** `baton-derived.db` can be deleted and rebuilt
   via `baton beads synthesize` — it is never a rollback concern.

Verification before Phase 3: dry-run a DROP migration on a copy, confirm the
`.bak-*` is written and `restore_baton_db.sh --list` sees it, then restore and
confirm the tables return.

## Risks

- Hidden `_conn()`/`BeadStore(` callers → add static lint (WP-H).
- `archived`/`quarantine` have no bd status → mapped via labels; `query` filters
  in Python on reconstructed `b.status`. Add a test.
- metrics/NOC under bd = `bd list` + Python group-by on scrape; cap with `limit`,
  degrade-to-empty on error.
- `baton-derived.db` staleness → rebuildable via `baton beads synthesize`;
  document as disposable.
