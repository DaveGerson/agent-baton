# Audit Report: CLI Wiring & Cross-Cutting Integration

**Scope:** `cli/` — main entrypoint and all 49 command modules
**Date:** 2026-04-13

---

## Findings

### FRAGILE: Error Handling Gaps

**1. `cleanup` — no error handling around archiver calls**
`observe/cleanup.py:40-61` — calls `DataArchiver(root)` and `.cleanup()` with no try/except. Permissions issues or archiver errors produce raw tracebacks. No test coverage.

**2. `source sync` — silent exception swallowing**
`source_cmd.py:419-420` and `:428-429` — bare `except Exception: pass` on both per-row persist errors and `last_synced` timestamp update. Failed rows produce zero diagnostic output.

**3. `bead promote` — writes files without error guard**
`bead_cmd.py:472,488` — `doc_path.write_text(...)` has no try/except. A permission error gives a raw traceback. The bead is then closed (line 488) even if the file write failed, creating inconsistent state.

**4. `main.py` — no top-level error catch**
`main.py:157` — only `execute.py` and a few others use `user_error()`/`validation_error()`. Most commands let exceptions propagate as raw Python tracebacks.

### UNWIRED: Cross-Cutting Gaps

**5. No CLI commands emit telemetry about their own invocation.**
Telemetry is only recorded by agents during execution (via `AgentTelemetry`). CLI invocations of `baton improve --run`, `baton cleanup`, `baton source sync`, etc. are invisible to the observability layer. Only `execute.py` and `daemon.py` create an `EventBus`.

**6. `source` commands are storage-only — not wired into execution.**
External source adapters write to `external_items` and `external_mappings` tables in `central.db`, but the execution engine never queries these tables. The planner does not consult external work items when generating plans.

**7. Governance commands don't enforce policies on other CLI commands.**
`classify`, `compliance`, `policy`, `validate` are standalone inspection tools. No CLI command invokes them as a pre-check before destructive operations.

### ORPHAN: Commands Not in Any Documented Workflow

**8. `async`** — delegates to `core/distribute/experimental/async_dispatch.py`. Not referenced in orchestration loop. Overlaps with `daemon` functionality.

**9. `incident`** — delegates to `core/distribute/experimental/incident.py`. Generates templated incident documents. No integration with execution engine.

**10. `transfer`** — delegates to `core/distribute/experimental/transfer.py`. All three experimental commands lack documented workflows.

**11. `detect`** — stack detection utility. The planner calls `AgentRouter.detect_stack()` directly, never through the CLI.

### Positive Findings

- **Beads are genuinely wired into the execution engine.** The CLI commands are a management interface for an actively used system.
- **`query` command is comprehensive.** 16 predefined subcommands plus ad-hoc SQL with `--central` flag.
- **`migrate-storage` is well-designed.** Supports `--dry-run`, `--verify`, `--keep-files`/`--remove-files`, uses `INSERT OR IGNORE`.
- **`execute.py` uses standardized error API** (`user_error`, `validation_error`) consistently — the other 48 commands should follow this pattern.
- **No stub commands found.** All 49 commands have substantive implementations.

---

## Summary Table

| # | Category | Command | File:Line | Issue |
|---|----------|---------|-----------|-------|
| 1 | FRAGILE | `cleanup` | `observe/cleanup.py:40` | No try/except, no tests |
| 2 | FRAGILE | `source sync` | `source_cmd.py:419,428` | Silent `except: pass` |
| 3 | FRAGILE | `beads promote` | `bead_cmd.py:472,488` | Write-then-close without atomicity |
| 4 | FRAGILE | `main` | `main.py:157` | No top-level error catch |
| 5 | UNWIRED | all non-execute | across CLI | No self-telemetry |
| 6 | UNWIRED | `source *` | `source_cmd.py` | Storage island, no engine consumer |
| 7 | UNWIRED | `govern *` | `govern/` | Advisory-only, no enforcement wiring |
| 8 | ORPHAN | `async` | `execution/async_cmd.py` | Experimental, undocumented |
| 9 | ORPHAN | `incident` | `agents/incident.py` | Experimental, no engine integration |
| 10 | ORPHAN | `transfer` | `distribute/transfer.py` | Experimental, no documented workflow |
| 11 | ORPHAN | `detect` | `govern/detect.py` | Standalone, engine calls router directly |

## Orchestration Loop Coverage

Only **7 of 49** CLI commands are used in the documented `baton plan / execute start / execute next / execute record / execute gate / execute approve / execute complete` loop. The remaining 42 are standalone tools or support commands — most of which are never invoked during automated orchestration.
