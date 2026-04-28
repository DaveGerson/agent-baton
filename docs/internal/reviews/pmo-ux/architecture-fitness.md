# PMO UX Architecture Fitness Assessment

**Date:** 2026-03-24
**Assessor:** Architecture Fitness Agent
**Scope:** Backend API surface, data models, and integration points supporting PMO UX workflows

---

## Executive Summary

The PMO backend provides solid coverage for the core Kanban board and Forge plan-generation workflows. The data model is well-aligned with the execution engine, and the scanner-based card generation is architecturally sound. However, the assessment reveals **2 critical bugs**, **4 high-severity gaps**, and **8 medium-severity issues** that collectively block or degrade the one-shot triage, batch operations, execution launch, and real-time update workflows.

The most urgent finding is a Pydantic validation error on the signal-to-forge endpoint that prevents the frontend's triage flow from working at all. The second critical finding is a return-type mismatch on `resolveSignal` that will cause silent frontend failures.

---

## Findings

### Finding F-AF-1: Signal-to-Forge Endpoint Rejects Frontend Requests (Pydantic 422)
- **Severity:** CRITICAL
- **Workflow:** Triage
- **Component(s):** `agent_baton/api/routes/pmo.py:397-442`, `agent_baton/api/models/requests.py:203-214`, `pmo-ui/src/api/client.ts:71-76`
- **Description:** The `POST /pmo/signals/{signal_id}/forge` endpoint declares `ApproveForgeRequest` as its request body. This Pydantic model requires `plan: dict` (no default -- uses `Field(...)`) and `project_id: str`. The frontend client (`signalToForge`) sends only `{ project_id: projectId }`, omitting the `plan` field entirely. The route docstring says "The `plan` field is ignored for this endpoint" but Pydantic validation will reject the request with HTTP 422 before the handler code ever runs.
- **Evidence:** `requests.py:206` declares `plan: dict = Field(...)` (required). `client.ts:73-74` sends `body: JSON.stringify({ project_id: projectId })`.
- **Impact:** The entire signal-to-forge triage flow is non-functional. Clicking "Forge" on a signal in the Signals Bar will produce a 422 error.
- **Recommendation:** Create a dedicated `ForgeSignalRequest` model with only `project_id: str` for this endpoint, or make `plan` optional with a default of `{}` in `ApproveForgeRequest`. The cleaner fix is a new request model since the semantics differ.

### Finding F-AF-2: resolveSignal Return Type Mismatch
- **Severity:** CRITICAL
- **Workflow:** Triage
- **Component(s):** `agent_baton/api/routes/pmo.py:379-394`, `pmo-ui/src/api/client.ts:68-70`, `pmo-ui/src/components/SignalsBar.tsx:31-33`
- **Description:** The backend `resolve_signal` endpoint returns `{"resolved": true, "signal_id": "..."}` (a plain dict). The frontend `api.resolveSignal()` declares its return type as `Promise<PmoSignal>` and `SignalsBar.tsx:32` uses the result to replace the signal in the local state array: `setSignals(prev => prev.map(s => s.signal_id === id ? updated : s))`. Since `updated` is not a `PmoSignal` but a `{resolved, signal_id}` object, the signal row in the UI will render with undefined fields after resolution.
- **Evidence:** `pmo.py:394` returns `{"resolved": True, "signal_id": signal_id}`. `client.ts:68` types return as `Promise<PmoSignal>`. `SignalsBar.tsx:32` uses the result object as a direct replacement in the signals array.
- **Impact:** After resolving a signal, the signals bar will show a corrupted entry (undefined title, severity, etc.) until the next data refresh. The signal is correctly marked resolved on the backend, so this is a UI rendering bug, not a data integrity issue.
- **Recommendation:** Either (a) change the backend to return the full updated `PmoSignalResponse` after resolution, or (b) change the frontend to remove the signal from the local array rather than replacing it (since resolved signals are filtered out of `get_open_signals` anyway).

### Finding F-AF-3: No One-Shot Plan Generation from Card ID
- **Severity:** HIGH
- **Workflow:** One-Shot
- **Component(s):** `agent_baton/api/routes/pmo.py` (entire file), `pmo-ui/src/components/KanbanCard.tsx`
- **Description:** The taxonomy document envisions a workflow where a user can click a card on the Kanban board and trigger plan generation directly (e.g., "re-forge this plan" or "generate a fix plan for this failed execution"). No API endpoint exists to accept a `card_id` and generate a plan from its existing context. The `KanbanCard` component is read-only -- clicking it toggles an expanded detail view but offers no actions (no "Re-forge", "Launch", or "View Plan" buttons).
- **Evidence:** The card expanded view (`KanbanCard.tsx:181-205`) displays metadata only. No action buttons exist. No `GET /pmo/cards/{card_id}/plan` or `POST /pmo/cards/{card_id}/reforge` endpoint exists.
- **Impact:** The one-shot triage workflow from the board is impossible. Users must manually navigate to the Forge, re-enter the task description, and generate a new plan from scratch. This breaks the "fast triage" UX goal.
- **Recommendation:** Add a `POST /pmo/forge/from-card` endpoint that accepts `card_id` and `project_id`, looks up the card's underlying plan/execution state, and pre-populates the Forge intake. On the frontend, add action buttons to `KanbanCard`'s expanded view: "View Plan", "Re-forge", and (for queued cards) "Launch Execution".

### Finding F-AF-4: No Execution Launch from Board/Forge
- **Severity:** HIGH
- **Workflow:** Kanban-Oversight, Forge-Author
- **Component(s):** `agent_baton/api/routes/pmo.py`, `agent_baton/api/routes/executions.py`, `pmo-ui/src/components/ForgePanel.tsx:305-328`
- **Description:** After a plan is approved in the Forge, the saved confirmation screen shows "Plan Saved & Queued" with options "New Plan" and "Back to Board". There is no "Launch Execution" button. The execution API exists (`POST /api/v1/executions`) and can accept an inline plan dict, but the PMO UI has no way to invoke it. The taxonomy document (section 3.2, step 8) says "Baton CLI picks up queued workflow" -- but this relies on a human running `baton execute start` in a terminal.
- **Evidence:** `ForgePanel.tsx:305-328` (saved phase) has no launch button. `executions.py:34-94` (`POST /executions`) exists and can accept a `plan` dict. The two are not connected.
- **Impact:** After plan approval, the user must switch to a terminal and run `baton execute start` manually. This breaks the seamless board-to-execution flow and adds friction to the primary workflow.
- **Recommendation:** Add a "Launch Execution" button to the Forge saved phase and to queued cards on the board. The button should `POST /api/v1/executions` with the plan dict. Consider adding a `POST /pmo/execute/{card_id}` convenience endpoint that resolves the plan from the card and starts execution in one call.

### Finding F-AF-5: No Batch Signal Triage
- **Severity:** HIGH
- **Workflow:** Triage
- **Component(s):** `agent_baton/api/routes/pmo.py` (signals section), `pmo-ui/src/components/SignalsBar.tsx`
- **Description:** The target user is a "busy engineering manager" who needs "batch operations for processing multiple items." Currently, signals can only be triaged one at a time. There is no `POST /pmo/signals/batch/forge` or `POST /pmo/signals/batch/resolve` endpoint. The Signals Bar renders individual "Forge" and "Resolve" buttons per signal with no multi-select capability.
- **Evidence:** No batch endpoints exist in `pmo.py`. `SignalsBar.tsx` renders per-signal action buttons with no checkbox or multi-select pattern.
- **Impact:** With 10+ open signals, triage becomes tedious. The manager must click Forge on each signal individually, wait for plan generation, approve, and return to the signal list. This does not meet the "fast triage" UX requirement.
- **Recommendation:** Add `POST /pmo/signals/batch/resolve` (accepts list of signal IDs) and `POST /pmo/signals/batch/forge` (accepts list of signal IDs + project_id, generates plans for each). On the frontend, add checkbox selection to SignalsBar with "Resolve Selected" and "Forge Selected" bulk action buttons.

### Finding F-AF-6: No Real-Time Board Updates (Polling Only)
- **Severity:** HIGH
- **Workflow:** Kanban-Oversight
- **Component(s):** `pmo-ui/src/hooks/usePmoBoard.ts`, `agent_baton/api/routes/events.py`
- **Description:** The board uses 5-second polling (`usePmoBoard.ts:14`, `POLL_INTERVAL_MS = 5000`). An SSE endpoint exists at `GET /api/v1/events/{task_id}` for per-task event streaming, but it requires a specific `task_id` -- there is no board-level SSE stream. Every poll calls `scanner.scan_all()` which iterates all registered projects, reads their filesystem/SQLite, and computes health metrics. With many projects, this becomes expensive.
- **Evidence:** `usePmoBoard.ts:48` sets up `setInterval(fetchBoard, POLL_INTERVAL_MS)`. The SSE endpoint at `events.py:43` is scoped to `{task_id}`, not to the board as a whole.
- **Impact:** At 50 projects with active executions, a 5s poll will cause perceptible UI lag and unnecessary I/O. The SSE infrastructure exists but is not wired to the board. Users see stale data for up to 5 seconds after state changes.
- **Recommendation:** Add a `GET /api/v1/pmo/events` SSE endpoint that subscribes to `*` topics on the EventBus and filters to PMO-relevant events (step.completed, gate.required, execution.complete). The frontend should use this for real-time updates and fall back to polling only when SSE is unavailable. Alternatively, increase the poll interval to 15s and add a manual "Refresh" button.

### Finding F-AF-7: TypeScript PmoSignal Missing `source_project_id`
- **Severity:** MEDIUM
- **Workflow:** Cross-cutting
- **Component(s):** `pmo-ui/src/api/types.ts:38-47`, `agent_baton/models/pmo.py:127-156`, `agent_baton/api/models/responses.py:586-598`
- **Description:** The Python `PmoSignal` model and `PmoSignalResponse` both include `source_project_id` (the project that generated the signal). The TypeScript `PmoSignal` interface omits this field entirely. This means the frontend cannot display which project a signal originated from, reducing triage context.
- **Evidence:** `types.ts:38-47` defines `PmoSignal` without `source_project_id`. `responses.py:593` includes `source_project_id` in `PmoSignalResponse`. The JSON payload will contain the field; TypeScript just does not type it.
- **Impact:** The data is available in the API response but TypeScript code cannot access it in a type-safe way. The Signals Bar cannot show the originating project, reducing the manager's ability to prioritize by project context.
- **Recommendation:** Add `source_project_id: string;` to the TypeScript `PmoSignal` interface. Display it in `SignalsBar.tsx` as a chip or label.

### Finding F-AF-8: TypeScript PmoProject Missing `registered_at` and `ado_project`
- **Severity:** MEDIUM
- **Workflow:** Cross-cutting
- **Component(s):** `pmo-ui/src/api/types.ts:19-26`, `agent_baton/api/models/responses.py:554-563`
- **Description:** The backend `PmoProjectResponse` includes `registered_at` (line 563). The Python `PmoProject` model includes `ado_project` (line 53). The TypeScript `PmoProject` interface has neither field.
- **Evidence:** `types.ts:19-26` defines 6 fields. `responses.py:554-563` defines 7 fields (includes `registered_at`). `pmo.py:53` defines `ado_project`.
- **Impact:** `registered_at` is cosmetic (display only). `ado_project` blocks future ADO integration UX -- the frontend cannot display or set the ADO project association.
- **Recommendation:** Add `registered_at?: string;` and `ado_project?: string;` to the TypeScript `PmoProject` interface.

### Finding F-AF-9: TypeScript PmoCard Missing `external_id`
- **Severity:** MEDIUM
- **Workflow:** Cross-cutting
- **Component(s):** `pmo-ui/src/api/types.ts:1-17`, `agent_baton/models/pmo.py:95`
- **Description:** The Python `PmoCard` model includes `external_id` (for ADO work item linking). The `PmoCardResponse` backend model does not expose it (not in `_card_response` helper at `pmo.py:463-481`). The TypeScript `PmoCard` interface also omits it.
- **Evidence:** `pmo.py:95` defines `external_id: str = ""`. `pmo.py:463-481` (`_card_response`) does not include `external_id` in the response construction. `types.ts:1-17` omits it.
- **Impact:** When ADO integration is wired up, cards with external work item links will not be able to display or link to the ADO item from the UI.
- **Recommendation:** Add `external_id` to `PmoCardResponse`, the `_card_response` helper, and the TypeScript `PmoCard` interface.

### Finding F-AF-10: Forge Sessions Not Exposed via API
- **Severity:** MEDIUM
- **Workflow:** Forge-Author
- **Component(s):** `agent_baton/core/storage/pmo_sqlite.py:233-272`, `agent_baton/api/routes/pmo.py`
- **Description:** `PmoSqliteStore` has full forge session management (`create_forge_session`, `complete_forge_session`, `list_forge_sessions`). The database schema includes a `forge_sessions` table. However, no API endpoint exposes forge sessions -- they are never created or queried. `ForgeSession.create_plan()` does not call `store.create_forge_session()`. The entire forge session persistence layer is dead code.
- **Evidence:** `pmo_sqlite.py:233-272` implements session CRUD. No route in `pmo.py` calls any of these methods. `ForgeSession` (`forge.py`) never references forge sessions.
- **Impact:** Forge sessions are not tracked, which means (a) the "resume interrupted forge" workflow is impossible, (b) there is no audit trail of forge activity, and (c) the manager cannot see in-progress forge work from other team members.
- **Recommendation:** Either wire forge session tracking into the Forge workflow (create on `forge/plan`, update on `forge/approve`, expose via `GET /pmo/forge/sessions`) or remove the dead code to reduce maintenance burden. The former is recommended for the "session recovery" requirement.

### Finding F-AF-11: ADO Search Returns Mock Data Only
- **Severity:** MEDIUM
- **Workflow:** Forge-Author
- **Component(s):** `agent_baton/api/routes/pmo.py:314-329`, `agent_baton/core/storage/adapters/ado.py`
- **Description:** The `GET /pmo/ado/search` endpoint returns hardcoded mock data (5 static work items). A fully implemented `AdoAdapter` exists in `core/storage/adapters/ado.py` with WIQL queries, batch fetching, and PAT authentication. These two are not connected -- the API route does not use the adapter.
- **Evidence:** `pmo.py:317-323` constructs `mock_items` inline. `ado.py:93-401` implements the full ADO adapter. The route does not import or reference the adapter.
- **Impact:** The ADO import feature in the Forge intake form is non-functional. Users see placeholder data that cannot be used for real work item import. The real adapter is production-ready but unwired.
- **Recommendation:** Wire `GET /pmo/ado/search` to use `AdoAdapter` when `ADO_PAT` is set in the environment, falling back to mock data otherwise. Add error handling for missing credentials (return empty results with a warning rather than crashing).

### Finding F-AF-12: Scanner Full-Scan on Every Board Refresh
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight
- **Component(s):** `agent_baton/core/pmo/scanner.py:156-171`, `agent_baton/api/routes/pmo.py:56-67`
- **Description:** `scanner.scan_all()` iterates every registered project, opens each project's storage backend (SQLite or file), loads all execution states, checks for orphaned plan.json files, and appends archived cards. This runs on every `GET /pmo/board` request, which is called every 5 seconds by the frontend. There is no caching layer.
- **Evidence:** `scanner.py:156-171` calls `scan_project()` for each project. `pmo.py:60-61` calls `scanner.scan_all()` and `scanner.program_health()` (which calls `scan_all()` again internally). Two full scans per board request.
- **Impact:** With 10 projects, this is fine. With 50 projects, each with multiple executions, the 5s poll will cause noticeable latency and I/O pressure. The double-scan (board + health) compounds the issue.
- **Recommendation:** (1) Refactor `get_board` to call `scan_all()` once and derive health from the same card list. (2) Add a TTL cache to `scan_all()` (e.g., 2-second memoization). (3) Long-term: use the EventBus to maintain an in-memory card index that is updated incrementally.

### Finding F-AF-13: Double Scan in Board Endpoint
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight
- **Component(s):** `agent_baton/api/routes/pmo.py:56-67`
- **Description:** The `get_board` handler calls `scanner.scan_all()` on line 60 and `scanner.program_health()` on line 61. Inside `program_health()` (`scanner.py:183`), `scan_all()` is called again. This means every board request performs two complete filesystem/database scans.
- **Evidence:** `pmo.py:60` calls `scan_all()`. `pmo.py:61` calls `program_health()`. `scanner.py:183-184` inside `program_health()` calls `self.scan_all()`.
- **Impact:** Every board poll does 2x the necessary I/O work. With N projects, the cost is O(2N) per request.
- **Recommendation:** Refactor `program_health()` to accept a pre-computed card list, or compute health inline in the `get_board` handler from the already-fetched cards.

### Finding F-AF-14: No Card Detail / Plan View Endpoint
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight
- **Component(s):** `agent_baton/api/routes/pmo.py`
- **Description:** There is no `GET /pmo/cards/{card_id}` or `GET /pmo/cards/{card_id}/plan` endpoint. The only way to see a card is through the board endpoint which returns all cards. The frontend's `KanbanCard` expanded view shows limited metadata but cannot display the full plan, execution history, or step details.
- **Evidence:** No single-card endpoint exists in `pmo.py`. The card response model (`PmoCardResponse`) has summary fields but no plan details.
- **Impact:** Users cannot drill into a card to see the full plan, review step outcomes, or view the execution trace. Deep linking to a specific card/plan is impossible.
- **Recommendation:** Add `GET /pmo/cards/{card_id}` returning the card with its full plan and execution state. This enables a card detail modal/page and supports deep linking.

---

## Summary Tables

### API Coverage Matrix

| Endpoint | Kanban-Oversight | Forge-Author | Triage | One-Shot | Batch |
|----------|:---:|:---:|:---:|:---:|:---:|
| `GET /pmo/board` | SERVES | - | - | - | - |
| `GET /pmo/board/{program}` | SERVES | - | - | - | - |
| `GET /pmo/projects` | PARTIAL | SERVES | - | - | - |
| `POST /pmo/projects` | SERVES | - | - | - | - |
| `DELETE /pmo/projects/{id}` | SERVES | - | - | - | - |
| `GET /pmo/health` | SERVES | - | - | - | - |
| `POST /pmo/forge/plan` | - | SERVES | - | - | - |
| `POST /pmo/forge/approve` | - | SERVES | - | - | - |
| `POST /pmo/forge/interview` | - | SERVES | - | - | - |
| `POST /pmo/forge/regenerate` | - | SERVES | - | - | - |
| `GET /pmo/ado/search` | - | PARTIAL (mock) | - | - | - |
| `GET /pmo/signals` | - | - | SERVES | - | - |
| `POST /pmo/signals` | - | - | SERVES | - | - |
| `POST /signals/{id}/resolve` | - | - | BROKEN (F-AF-2) | - | - |
| `POST /signals/{id}/forge` | - | - | BROKEN (F-AF-1) | - | - |
| `GET /pmo/cards/{id}` | MISSING | - | - | MISSING | - |
| `POST /pmo/forge/from-card` | - | - | - | MISSING | - |
| `POST /pmo/execute/{id}` | MISSING | MISSING | - | MISSING | - |
| `POST /signals/batch/resolve` | - | - | - | - | MISSING |
| `POST /signals/batch/forge` | - | - | - | - | MISSING |
| `GET /pmo/events` (SSE) | MISSING | - | - | - | - |
| `GET /pmo/forge/sessions` | - | MISSING | - | - | - |
| `POST /executions` (existing) | NOT WIRED | NOT WIRED | - | - | - |

**Legend:** SERVES = endpoint fully supports the workflow. PARTIAL = exists but incomplete. BROKEN = exists but non-functional. MISSING = endpoint does not exist. NOT WIRED = endpoint exists in another route module but is not connected to the PMO UI.

### Model Gap Analysis

| Concept | Model Exists | Completeness | Blocking? |
|---------|:---:|:---:|:---:|
| PmoCard | Yes | Complete for board display | No |
| PmoCard.external_id | Yes (Python) | Not in API response or TS types | Blocks ADO linking |
| PmoProject | Yes | Missing `ado_project` in TS | Blocks ADO integration |
| PmoSignal | Yes | Missing `source_project_id` in TS | Blocks project-scoped triage |
| ProgramHealth | Yes | Complete | No |
| MachinePlan | Yes | Complete | No |
| ForgeSession (DB) | Yes (SQLite schema + store methods) | Dead code -- never called | Blocks session recovery |
| InterviewQuestion/Answer | Yes | Complete | No |
| One-shot plan concept | No | N/A | Blocks one-shot workflow |
| Batch operation model | No | N/A | Blocks batch triage |
| Board SSE event model | No | N/A | Blocks real-time updates |

### Integration Readiness

| Integration Point | Status | Effort to Complete |
|-------------------|--------|-------------------|
| ADO Adapter (fetch) | **Production-ready** -- full WIQL/batch implementation in `ado.py` | Small: wire into `GET /pmo/ado/search`, add PAT detection |
| ADO Adapter (bidirectional sync) | **Not started** -- adapter is read-only | Large: requires write-back endpoints, webhook receiver |
| Scanner refresh model | **5s polling** -- no push mechanism | Medium: add SSE endpoint, wire EventBus |
| Forge-to-Execution | **Gap** -- Forge saves plan, execution requires CLI | Medium: add "Launch" button calling `POST /executions` |
| Cross-project queries | **Ready** -- `CentralStore.query()` supports arbitrary read-only SQL | Small: expose via a PMO analytics endpoint if needed |
| Forge session persistence | **Schema exists, code exists, not wired** | Small: add calls in `ForgeSession`, add API routes |
| Central.db sync (federation) | **Working** -- `SyncEngine.push_all()` syncs all projects | None needed for PMO |
| PMO metrics | **Schema + store methods exist, not exposed** | Small: add `GET /pmo/metrics/{name}` endpoint |

---

## Priority-Ordered Remediation Plan

### Tier 1: Fix Broken Functionality (must-fix before any user testing)

1. **F-AF-1** -- Create `ForgeSignalRequest(project_id: str)` model for `POST /signals/{id}/forge`. Change the route parameter type. (~15 min)
2. **F-AF-2** -- Change `resolve_signal` to return the full updated `PmoSignalResponse`, or change the frontend to filter resolved signals from the array. (~10 min)

### Tier 2: Enable Key Workflows (needed for UX review to be meaningful)

3. **F-AF-4** -- Add "Launch Execution" button in Forge saved phase and queued card actions. Wire to `POST /api/v1/executions`. (~2 hours)
4. **F-AF-3** -- Add `POST /pmo/forge/from-card` and card action buttons in `KanbanCard`. (~3 hours)
5. **F-AF-7/8/9** -- Fix TypeScript type alignment (add missing fields). (~30 min)
6. **F-AF-13** -- Eliminate double scan in `get_board`. (~15 min)

### Tier 3: Improve Efficiency and Scale (needed before real portfolio use)

7. **F-AF-5** -- Batch signal endpoints and multi-select UI. (~4 hours)
8. **F-AF-6** -- Board-level SSE stream. (~4 hours)
9. **F-AF-12** -- Scanner caching / incremental update. (~3 hours)
10. **F-AF-14** -- Card detail endpoint and detail modal. (~3 hours)

### Tier 4: Complete the Integration Story

11. **F-AF-11** -- Wire ADO adapter to search endpoint. (~2 hours)
12. **F-AF-10** -- Wire forge session tracking. (~2 hours)

---

## Appendix: File Reference

| File | Role | Key Lines |
|------|------|-----------|
| `agent_baton/api/routes/pmo.py` | All 15 PMO API routes | L1-509 |
| `agent_baton/api/models/requests.py` | Pydantic request bodies | L140-287 (PMO section) |
| `agent_baton/api/models/responses.py` | Pydantic response models | L554-668 (PMO section) |
| `agent_baton/api/deps.py` | Singleton DI wiring | L76-166 |
| `agent_baton/api/server.py` | FastAPI app factory | L52-62 (route registry) |
| `agent_baton/core/pmo/forge.py` | ForgeSession (plan gen, interview, signal-to-plan) | L1-248 |
| `agent_baton/core/pmo/scanner.py` | PmoScanner (board card generation) | L1-208 |
| `agent_baton/core/pmo/store.py` | PmoStore (file-based, legacy) | L1-144 |
| `agent_baton/core/storage/pmo_sqlite.py` | PmoSqliteStore (SQLite-backed) | L1-391 |
| `agent_baton/core/storage/central.py` | CentralStore (read-only replica) | L1-367 |
| `agent_baton/core/storage/sync.py` | SyncEngine (federation) | L1-550 |
| `agent_baton/core/storage/adapters/ado.py` | ADO adapter (production-ready) | L1-401 |
| `agent_baton/core/storage/schema.py` | SQLite DDL (PMO + Central) | L423-622 |
| `agent_baton/models/pmo.py` | All PMO data models | L1-271 |
| `agent_baton/api/routes/events.py` | SSE streaming (per-task) | L1-113 |
| `agent_baton/api/routes/executions.py` | Execution lifecycle endpoints | L1-287 |
| `pmo-ui/src/api/client.ts` | Frontend API client | L1-94 |
| `pmo-ui/src/api/types.ts` | Frontend TypeScript types | L1-186 |
| `pmo-ui/src/components/ForgePanel.tsx` | Forge wizard UI | L1-348 |
| `pmo-ui/src/components/KanbanBoard.tsx` | Board layout + columns | L1-265 |
| `pmo-ui/src/components/KanbanCard.tsx` | Individual card rendering | L1-230 |
| `pmo-ui/src/hooks/usePmoBoard.ts` | 5s polling hook | L1-57 |
