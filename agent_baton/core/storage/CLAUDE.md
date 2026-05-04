# agent_baton/core/storage/ — persistence and migrations

The only place that touches `baton.db` and the on-disk team-context layout. Inherits: [../../../CLAUDE.md](../../../CLAUDE.md), [../CLAUDE.md](../CLAUDE.md).

## Files

| File | Role |
|------|------|
| `connection.py` | SQLite connection management |
| `schema.py` | Schema definitions (tables, indices) |
| `migrate.py`, `migration_backup.py` | Forward migrations + automatic pre-migration backup |
| `sqlite_backend.py`, `file_backend.py` | Storage backends (DB and on-disk file) |
| `central.py` | Centralized read/write entry points used by `core/` |
| `pmo_sqlite.py` | PMO-specific projections backing the UI |
| `protocol.py` | `Storage` protocol — what the rest of `core/` is allowed to call |
| `queries.py` | Named query helpers (no inline SQL elsewhere) |
| `sync.py` | Cross-worktree sync of notes and beads |
| `*_store.py` (e.g. `release_store`, `slo_store`, `user_store`, `handoff_store`, `deployment_profile_store`, `conflict_store`) | One store per persisted aggregate |
| `adapters/` | External-system adapters (`ado.py`, `github.py`, `jira.py`, `linear.py`) |

## Mandatory rules

- **Schema changes require a migration.** Edit `schema.py` *and* add a forward migration in `migrate.py`. Never alter `schema.py` alone — older `baton.db` files must keep loading.
- **No raw SQL outside this directory.** Other code calls into `queries.py` or a `*_store.py`.
- **Migrations back up first.** `migration_backup.py` runs before each migration; never bypass it.
- **`BATON_DB_PATH`** can override the default discovery. Respect it everywhere; never hard-code paths.

## Adding a persisted type

1. Define the model in `agent_baton/models/`.
2. Add a table in `schema.py` and a migration in `migrate.py`.
3. Add a `<name>_store.py` exposing typed read/write methods.
4. Add the methods to the `Storage` protocol if `core/` consumers will use them.
5. Test under `tests/storage/`.

## Adapters

External-system adapters (`adapters/ado.py`, `github.py`, `jira.py`, `linear.py`) translate Baton concepts to/from external APIs. They:

- Are read-mostly. Writes to external systems are explicit and gated by a guardrail.
- Hold no state — credentials come from settings/env, not module-level globals.
- Don't import from `engine/` or `orchestration/`.

## Don'ts

- Don't import from `core/engine/` or `core/orchestration/`. Storage is a leaf.
- Don't catch and swallow `sqlite3.OperationalError` — let it propagate so the engine can surface it to the user.
- Don't add a new table without a migration test confirming the upgrade path.
