# Baton PMO UI — Implementation Plan

## Context

Agent-Baton needs a portfolio management UI ("PMO") that provides: (1) AI Kanban for tracking orchestration plan lifecycles, (2) The Forge consultative plan builder using baton's own IntelligentPlanner, (3) Program Health Bar for aggregate progress, (4) Signals bar for bug triage. The PMO extends the existing FastAPI server — no separate Node.js server, no direct Anthropic API calls. UI is React/Vite pre-built to static files for deployment on locked-down PCs.

## User Decisions

| Decision | Choice |
|----------|--------|
| Backend | FastAPI (Python) — extend existing `agent_baton/api/` |
| UI location | In-repo `pmo-ui/` — isolated code/CI/testing |
| State | Extend existing engine state — no separate `~/.baton-pmo/data.json` |
| ADO | Read-only, tabled for now — documented for later |
| Forge backend | Agent-Baton's own agent routing — no direct API calls |

## Architecture

```
Browser (localhost:8741/pmo/)
    │
    ├── GET /api/v1/pmo/board       → PmoScanner reads execution-state.json per project
    ├── GET /api/v1/pmo/health      → Aggregate program metrics
    ├── POST /api/v1/pmo/forge/plan → IntelligentPlanner.create_plan()
    ├── GET /api/v1/pmo/events      → SSE stream (EventBus subscription)
    └── Static files from pmo-ui/dist/
    │
FastAPI Server (existing, extended)
    │
    ├── PmoStore → ~/.baton/pmo-config.json (projects, programs, signals)
    ├── PmoScanner → reads .claude/team-context/execution-state.json per project
    ├── ForgeSession → wraps IntelligentPlanner (no Anthropic API calls)
    └── EventBus → existing event infrastructure for live updates
```

## Phase 1: Data Models

**New file: `agent_baton/models/pmo.py`**

```python
@dataclass
class PmoProject:
    project_id: str          # slug, e.g. "nds"
    name: str
    path: str                # absolute filesystem path
    program: str             # "NDS", "ATL", etc.
    color: str = ""
    description: str = ""
    registered_at: str = ""

@dataclass
class PmoCard:
    card_id: str             # task_id from MachinePlan
    project_id: str
    program: str
    title: str               # task_summary
    column: str              # queued|planning|executing|awaiting_human|validating|deployed
    risk_level: str = "LOW"
    priority: int = 0
    agents: list[str]
    steps_completed: int = 0
    steps_total: int = 0
    created_at: str = ""
    updated_at: str = ""

@dataclass
class PmoSignal:
    signal_id: str
    signal_type: str         # bug|escalation|blocker
    title: str
    description: str = ""
    source_project_id: str = ""
    severity: str = "medium"
    status: str = "open"
    created_at: str = ""

@dataclass
class ProgramHealth:
    program: str
    total_plans: int = 0
    active: int = 0
    completed: int = 0
    blocked: int = 0
    completion_pct: float = 0.0

@dataclass
class PmoConfig:
    projects: list[PmoProject]
    programs: list[str]
    signals: list[PmoSignal]
    version: str = "1"
```

All classes follow existing pattern: `to_dict()` / `from_dict()` / `field(default_factory=list)`.

## Phase 2: Core Services

**New: `agent_baton/core/pmo/__init__.py`** (empty)

**New: `agent_baton/core/pmo/store.py`** — `PmoStore`
- Reads/writes `~/.baton/pmo-config.json` (atomic write via tmp+rename, same as `StatePersistence`)
- Append-only archive: `~/.baton/pmo-archive.jsonl` (same pattern as `UsageLogger`)
- Methods: `load_config()`, `save_config()`, `register_project()`, `unregister_project()`, `add_signal()`, `resolve_signal()`, `archive_card()`, `read_archive()`

**New: `agent_baton/core/pmo/scanner.py`** — `PmoScanner`
- Iterates registered projects, loads each `execution-state.json` via existing `StatePersistence`
- Maps `ExecutionState.status` → `PmoCard.column`:
  - no state → "queued"
  - "running" → "executing"
  - "gate_pending" → "validating"
  - "approval_pending" → "awaiting_human"
  - "complete" → "deployed"
  - "failed" → "executing" (with error flag)
- `scan_all()` → `list[PmoCard]`
- `program_health()` → `dict[str, ProgramHealth]`

**New: `agent_baton/core/pmo/forge.py`** — `ForgeSession`
- Wraps `IntelligentPlanner.create_plan()` — NO direct Anthropic API calls
- `create_plan(description, program, project_id, task_type, priority)` → `MachinePlan`
- `save_plan(plan, project)` → saves via `ContextManager.write_plan()` + `StatePersistence.save()`

## Phase 3: API Routes

**New: `agent_baton/api/routes/pmo.py`**

| Method | Path | Handler | Returns |
|--------|------|---------|---------|
| GET | `/pmo/board` | `get_board()` | `list[PmoCard]` + health |
| GET | `/pmo/board/{program}` | `get_board_by_program()` | Filtered cards |
| GET | `/pmo/projects` | `list_projects()` | `list[PmoProject]` |
| POST | `/pmo/projects` | `register_project()` | `PmoProject` |
| DELETE | `/pmo/projects/{id}` | `unregister_project()` | `{removed: true}` |
| GET | `/pmo/health` | `get_health()` | `dict[str, ProgramHealth]` |
| POST | `/pmo/forge/plan` | `create_forge_plan()` | `PlanResponse` |
| POST | `/pmo/forge/approve` | `approve_forge_plan()` | `{saved: true, path}` |
| GET | `/pmo/signals` | `list_signals()` | `list[PmoSignal]` |
| POST | `/pmo/signals` | `create_signal()` | `PmoSignal` |
| POST | `/pmo/signals/{id}/resolve` | `resolve_signal()` | `PmoSignal` |
| POST | `/pmo/signals/{id}/forge` | `signal_to_forge()` | `PlanResponse` |
| GET | `/pmo/events` | `stream_events()` | SSE stream |

**Modify: `agent_baton/api/models/requests.py`** — Add `RegisterProjectRequest`, `CreateForgeRequest`, `ApproveForgeRequest`, `CreateSignalRequest`

**Modify: `agent_baton/api/models/responses.py`** — Add `PmoCardResponse`, `PmoProjectResponse`, `PmoSignalResponse`, `ProgramHealthResponse`, `PmoBoardResponse`

**Modify: `agent_baton/api/deps.py`** — Add `_pmo_store`, `_pmo_scanner`, `_forge` singletons + providers

**Modify: `agent_baton/api/server.py`** — Add one line to `_ROUTE_MODULES` + static file mount for `pmo-ui/dist/`

## Phase 4: CLI Integration

**New: `agent_baton/cli/commands/pmo_cmd.py`**
- `baton pmo serve` — Start API server (delegates to existing `baton serve`)
- `baton pmo status` — Print board summary to terminal
- `baton pmo add` — Register a project interactively
- `baton pmo health` — Print program health bar
- Auto-discovered by existing `cli/main.py` mechanism

## Phase 5: React UI

**New directory: `pmo-ui/`** (isolated from core code)

```
pmo-ui/
  package.json          # react, react-dom, vite, @vitejs/plugin-react
  vite.config.ts        # base: "/pmo/", outDir: "dist"
  index.html
  src/
    App.tsx             # Router: Dashboard | Forge
    api/client.ts       # fetch wrapper → /api/v1/pmo/*
    api/types.ts        # TypeScript interfaces matching response models
    components/
      KanbanBoard.tsx   # Column layout with cards
      KanbanCard.tsx    # Individual card (adapted from baton_pmo_final.jsx)
      HealthBar.tsx     # Program progress bars
      SignalsBar.tsx    # Bug triage with "Send to Forge" buttons
      ForgePanel.tsx    # Task description → plan preview → approve
      PlanPreview.tsx   # Read-only plan visualization
    hooks/
      useSSE.ts         # EventSource hook for /pmo/events
      usePmoBoard.ts    # Board fetching + live SSE updates
  dist/                 # Pre-built (committed for deployment)
```

Adapted from `reference_files/UIelements/baton_pmo_final.jsx` — replace `window.storage` with fetch calls, remove direct Claude API calls, add project selector.

**Deployment**: `dist/` committed to repo. No Node.js required at runtime. `baton serve` serves it at `/pmo/`.

## Phase 6: Tests

| Test File | Covers |
|-----------|--------|
| `tests/test_pmo_models.py` | Model serialization round-trips, column mapping |
| `tests/test_pmo_store.py` | Config save/load, project register/unregister, signal CRUD, archive JSONL |
| `tests/test_pmo_scanner.py` | Status→column mapping, program health aggregation, missing state handling |
| `tests/test_pmo_forge.py` | Plan creation delegates to IntelligentPlanner, save writes correct paths |
| `tests/test_api_pmo.py` | All API endpoints via TestClient, SSE connection |

## Phase 7: Documentation

- Update `CLAUDE.md` — add `pmo-ui/` to structure, PMO CLI commands
- Update `docs/architecture.md` — PMO subsystem description
- Update `README.md` — PMO feature + setup instructions
- Document ADO integration design for later implementation

## Files Summary

### New (15 files + UI directory)
- `agent_baton/models/pmo.py`
- `agent_baton/core/pmo/__init__.py`
- `agent_baton/core/pmo/store.py`
- `agent_baton/core/pmo/scanner.py`
- `agent_baton/core/pmo/forge.py`
- `agent_baton/api/routes/pmo.py`
- `agent_baton/cli/commands/pmo_cmd.py`
- `tests/test_pmo_models.py`
- `tests/test_pmo_store.py`
- `tests/test_pmo_scanner.py`
- `tests/test_pmo_forge.py`
- `tests/test_api_pmo.py`
- `pmo-ui/` (React scaffold + components)

### Modified (5 files)
- `agent_baton/api/server.py` — route registration + static mount
- `agent_baton/api/deps.py` — PMO singletons
- `agent_baton/api/models/requests.py` — PMO request models
- `agent_baton/api/models/responses.py` — PMO response models
- `agent_baton/core/events/events.py` — PMO domain events (optional)

## Key Reuse Points

| Reuse | Source | For |
|-------|--------|-----|
| `StatePersistence` | `core/engine/persistence.py` | Read execution-state.json per project |
| `IntelligentPlanner.create_plan()` | `core/engine/planner.py` | Forge plan generation |
| `EventBus` + SSE pattern | `core/events/bus.py` + `api/routes/events.py` | Live updates |
| `UsageLogger` JSONL pattern | `core/observe/usage.py` | PMO archive |
| `ContextManager` | `core/orchestration/context.py` | Save Forge plans to projects |
| Lazy `_ROUTE_MODULES` | `api/server.py` | Route registration |
| `deps.py` singletons | `api/deps.py` | DI pattern |
| `TestClient` fixtures | `tests/test_api_plans.py` | API test scaffolding |

## Build Order (dependency-sequenced)

1. Models (`pmo.py`) → no deps
2. Store (`store.py`) → depends on models
3. Tests for 1-2
4. Scanner (`scanner.py`) → depends on models, store, StatePersistence
5. Forge (`forge.py`) → depends on models, store, IntelligentPlanner
6. Tests for 4-5
7. API request/response models → depends on models
8. DI setup (`deps.py`) → depends on store, scanner, forge
9. API routes (`pmo.py`) → depends on 7-8
10. Server registration + static mount → depends on 9
11. API tests
12. CLI commands → depends on store, scanner
13. React UI → depends on API contract (7, 9)
14. Pre-build UI + commit dist/
15. Documentation

## Verification

1. `pytest tests/test_pmo_*.py` — all PMO tests pass
2. `pytest` — full suite passes (no regressions)
3. `baton serve` — server starts, `/api/v1/pmo/board` returns empty board
4. `baton pmo add` — register a project, verify in config
5. `baton pmo status` — shows board summary
6. Open `http://localhost:8741/pmo/` — UI loads, shows empty Kanban
7. Create a plan via Forge UI → plan appears in Kanban
8. Run `baton execute start` in a registered project → card moves to "executing" on refresh

## ADO Integration (Deferred — Design Notes)

Reserved fields for future implementation:
- `PmoCard.external_id: str = ""` — ADO work item ID
- `PmoProject.ado_project: str = ""` — ADO project name
- Future module: `agent_baton/core/pmo/ado.py` — read-only ADO sync via REST API
- The `/pmo/board` endpoint returns cards regardless of source, so ADO cards merge into the same board
