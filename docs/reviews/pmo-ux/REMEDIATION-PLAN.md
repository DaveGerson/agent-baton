# PMO UX Remediation Plan

## Overview

This plan addresses 26 findings from the PMO UX audit across 6 phases, ordered by dependency and priority. The total scope is approximately 30-40 hours of implementation work. Phases are designed to be executed sequentially with clear gates between them — each phase unlocks capabilities that later phases build on.

**Headline numbers:**
- **4 CRITICAL bugs/gaps** (Phases 1-2) — must-fix before any user testing
- **8 HIGH-severity gaps** (Phases 2-3) — needed for the UI to function as a standalone product
- **10 MEDIUM issues** (Phases 4-5) — efficiency and scale improvements
- **4 LOW issues** (Phase 6) — cleanup and polish

**Phase summary:**

| Phase | Goal | Est. Effort | Issues |
|-------|------|-------------|--------|
| 1 | Fix broken triage pipeline | ~1 hour | PMO-UX-001, PMO-UX-002 |
| 2 | Enable card actions and execution launch | ~6 hours | PMO-UX-003, PMO-UX-004, PMO-UX-006, PMO-UX-007 |
| 3 | Fix state management and interaction quality | ~6 hours | PMO-UX-005, PMO-UX-008, PMO-UX-009, PMO-UX-010, PMO-UX-011, PMO-UX-012, PMO-UX-013, PMO-UX-014 (agent edit only) |
| 4 | Add batch operations and session persistence | ~8 hours | PMO-UX-014, PMO-UX-015, PMO-UX-016, PMO-UX-019, PMO-UX-020, PMO-UX-021, PMO-UX-022 |
| 5 | Performance and real-time updates | ~8 hours | PMO-UX-017 |
| 6 | Wire ADO adapter and cleanup | ~5 hours | PMO-UX-018, PMO-UX-023, PMO-UX-024, PMO-UX-025, PMO-UX-026 |

---

## Phase 1: Fix Broken Triage Pipeline

**Goal:** Fix the 2 CRITICAL backend bugs that completely block the signal triage workflow. After this phase, signals can be forged and resolved without API errors or UI corruption.

**Agent:** backend-engineer--python

**Gate:** All signal API tests pass. `POST /pmo/signals/{id}/forge` accepts `{"project_id": "..."}` without 422. `POST /pmo/signals/{id}/resolve` returns a response that the frontend can safely consume. Add regression tests for both endpoints.

**Task description for baton plan:**
> "Fix two critical bugs in the PMO signal API endpoints. (1) POST /pmo/signals/{id}/forge rejects requests with Pydantic 422 because it reuses ApproveForgeRequest which requires a `plan` field — create a dedicated ForgeSignalRequest model with only project_id. (2) POST /pmo/signals/{id}/resolve returns a partial dict {resolved, signal_id} instead of a full PmoSignalResponse — either change the backend to return the full signal response, or document the minimal response shape. Add regression tests for both endpoints. Files: agent_baton/api/models/requests.py, agent_baton/api/routes/pmo.py, tests/."

**Steps:**
1. Create `ForgeSignalRequest(project_id: str)` model in `requests.py` (~10 min)
2. Update `POST /signals/{id}/forge` route to use `ForgeSignalRequest` (~5 min)
3. Fix `resolve_signal` return — either return full `PmoSignalResponse` or document the minimal shape (~10 min)
4. Add regression tests for both endpoints (~20 min)
5. Run full test suite to verify no regressions (~5 min)

**Dependencies:** None — this is the starting point.

---

## Phase 2: Enable Card Actions and Execution Launch

**Goal:** Transform the Kanban board from a read-only monitor into an actionable management surface. Add execution launch capability to both the Forge saved phase and queued cards. Refresh the board after Forge approval. Link signals to plans on approval.

**Agent:** frontend-engineer--react

**Gate:** From the Forge saved phase, clicking "Start Execution" calls `POST /executions` and the card transitions to "executing" on the board. Queued cards show a "Start Execution" button. Board refreshes immediately after Forge approval (no 5-second delay). Signals are marked triaged after Forge approval.

**Task description for baton plan:**
> "Enable execution launch and card actions in the PMO UI. (1) Add startExecution method to client.ts calling POST /api/v1/executions. (2) Add 'Start Execution' button to ForgePanel saved phase that calls startExecution and shows success/error. (3) Add context-sensitive action buttons to KanbanCard expanded view: 'Start Execution' for queued cards, 'Resolve Gate' for awaiting_human, 'Re-forge' for failed. Wire callbacks through KanbanBoard to App.tsx. (4) Pass usePmoBoard refresh callback to ForgePanel and call it after approval succeeds. (5) In ForgePanel.handleApprove, when initialSignal is set, call api.resolveSignal to mark the signal as triaged. Files: pmo-ui/src/api/client.ts, pmo-ui/src/components/ForgePanel.tsx, pmo-ui/src/components/KanbanCard.tsx, pmo-ui/src/components/KanbanBoard.tsx, pmo-ui/src/App.tsx."

**Steps:**
1. Add `startExecution(planPath)` to `client.ts` (~15 min)
2. Add "Start Execution" button to ForgePanel saved phase with loading/success state (~30 min)
3. Pass `refresh` from `usePmoBoard` through App.tsx to ForgePanel; call on approval success (~20 min)
4. Add `resolveSignal` call in `handleApprove` when `initialSignal` is set (~15 min)
5. Define `CardAction` type and `onAction` callback interface (~15 min)
6. Add action buttons to KanbanCard expanded view based on column (~1.5 hours)
7. Wire callbacks through KanbanBoard to App.tsx (~30 min)
8. Test all card actions end-to-end (~1 hour)

**Dependencies:** Phase 1 (resolveSignal must return correct data before the frontend calls it).

---

## Phase 3: Fix State Management and Interaction Quality

**Goal:** Fix Forge state loss, add board-to-Forge navigation, make signals self-refreshing, improve visual accessibility, add keyboard shortcuts, and enable agent editing in the PlanEditor. After this phase, the UI is usable for daily management without data loss.

**Agent:** frontend-engineer--react

**Gate:** Tab-switching between Kanban and Forge preserves all Forge state. Signals Bar updates every 15 seconds and shows signal count badge. All font sizes are at minimum 9px (scannable content at 11px+). Keyboard shortcuts (N, S, Esc) work from any view. Agent assignment is editable in PlanEditor. Regeneration errors are visible.

**Task description for baton plan:**
> "Fix PMO UI state management and interaction quality issues. (1) Keep ForgePanel mounted but hidden via CSS display:none when Kanban view is active, preserving all state across tab switches. (2) Add 15-second polling to SignalsBar and show open signal count badge on the toggle button. (3) Establish a font size floor: add FONT_SIZES constants to tokens.ts (xs=9px, sm=11px, md=12px, lg=14px), update KanbanCard titles to 12px, metadata to 11px, column headers to 11px, HealthBar stats to 9px minimum. (4) Create useHotkeys hook, add N=New Plan, S=Toggle Signals, Esc=close shortcuts in App.tsx. (5) Make PlanEditor agent chip a dropdown select when step is in edit mode. (6) Move generateError display outside the intake-phase guard so it's visible in preview phase; add Cancel button during generation. (7) Add Re-forge/Edit in Forge buttons to KanbanCard that call openForge with card context. Files: pmo-ui/src/App.tsx, pmo-ui/src/components/ForgePanel.tsx, pmo-ui/src/components/SignalsBar.tsx, pmo-ui/src/components/KanbanCard.tsx, pmo-ui/src/components/KanbanBoard.tsx, pmo-ui/src/components/PlanEditor.tsx, pmo-ui/src/components/InterviewPanel.tsx, pmo-ui/src/tokens.ts, pmo-ui/src/hooks/useHotkeys.ts (new)."

**Steps:**
1. Modify App.tsx to render both views with CSS display toggle instead of conditional mount (~30 min)
2. Add polling interval to SignalsBar (15s) + signal count badge on toggle button (~30 min)
3. Add `FONT_SIZES` to tokens.ts, update all components to use floor values (~1 hour)
4. Create `useHotkeys.ts` hook, wire N/S/Esc shortcuts in App.tsx (~45 min)
5. Make PlanEditor agent chip a dropdown select with agent roster list (~30 min)
6. Fix regeneration error visibility: move error display outside phase guard (~15 min)
7. Add Cancel button during generation/regeneration phases (~15 min)
8. Add board-to-Forge navigation: "Re-forge" and "Edit in Forge" buttons on cards (~1 hour)
9. Test all interactions end-to-end (~1 hour)

**Dependencies:** Phase 2 (card action infrastructure must exist before adding board-to-Forge buttons).

---

## Phase 4: Add Backend Improvements and UI Completeness

**Goal:** Fix the double-scan performance issue, align TypeScript types, add batch signal operations, wire forge session tracking, add card detail endpoint, make HealthBar clickable, and fix the interview zero-answer guard. These are the medium-severity improvements that round out the feature set.

**Agent:** backend-engineer--python (steps 1-5), then frontend-engineer--react (steps 6-10)

**Gate:** Board endpoint performs one scan per request (not two). TypeScript types match backend response shapes. Batch resolve endpoint works. Card detail endpoint returns full plan. HealthBar tiles filter the board on click. Interview allows zero-answer submission.

**Task description for baton plan:**
> "Backend and UI completeness improvements for PMO. Backend: (1) Refactor scanner.program_health to accept pre-computed card list, eliminating the double scan_all in get_board. (2) Add GET /pmo/cards/{card_id} endpoint returning card with full plan and execution state. (3) Wire forge session tracking: call pmo_sqlite_store.create_forge_session in ForgeSession.create_plan and complete_forge_session in save_plan. Add GET /pmo/forge/sessions endpoint. (4) Add POST /pmo/signals/batch/resolve endpoint accepting signal_ids array. Frontend: (5) Add missing fields to TypeScript types: source_project_id on PmoSignal, registered_at and ado_project on PmoProject, external_id on PmoCard. (6) Add checkbox selection to SignalsBar with 'Resolve Selected' bulk action. (7) Make HealthBar tiles clickable to filter the board. (8) Remove answeredCount===0 guard from InterviewPanel submit. (9) Add 'View Plan' button to KanbanCard using PlanPreview and the new card detail endpoint. Files: agent_baton/api/routes/pmo.py, agent_baton/core/pmo/scanner.py, agent_baton/core/pmo/forge.py, agent_baton/api/models/responses.py, pmo-ui/src/api/types.ts, pmo-ui/src/api/client.ts, pmo-ui/src/components/SignalsBar.tsx, pmo-ui/src/components/HealthBar.tsx, pmo-ui/src/components/KanbanBoard.tsx, pmo-ui/src/components/KanbanCard.tsx, pmo-ui/src/components/InterviewPanel.tsx."

**Steps:**
1. Refactor `program_health` to accept card list, fix double scan (~30 min)
2. Add `GET /pmo/cards/{card_id}` endpoint with plan data (~1 hour)
3. Wire forge session tracking into ForgeSession + add API endpoint (~1.5 hours)
4. Add `POST /pmo/signals/batch/resolve` endpoint (~45 min)
5. Fix TypeScript type alignment (3 interfaces) (~20 min)
6. Add checkbox selection + "Resolve Selected" to SignalsBar (~1 hour)
7. Make HealthBar tiles clickable → setFilter (~30 min)
8. Remove zero-answer guard from InterviewPanel (~10 min)
9. Add "View Plan" button to KanbanCard with PlanPreview modal (~1.5 hours)
10. Test all changes end-to-end (~1 hour)

**Dependencies:** Phase 3 (card action infrastructure should be in place; PlanPreview needs to be wired).

---

## Phase 5: Performance and Real-Time Updates

**Goal:** Replace 5-second polling with SSE-based real-time board updates. Add a board-level SSE endpoint that pushes card state changes via the EventBus. Frontend uses SSE for immediate updates with polling fallback.

**Agent:** backend-engineer--python (SSE endpoint), then frontend-engineer--react (SSE client)

**Gate:** Board updates within 1 second of execution state changes when SSE is connected. Graceful fallback to 15-second polling when SSE fails. No increase in server I/O compared to current polling.

**Task description for baton plan:**
> "Add real-time board updates via Server-Sent Events. Backend: (1) Create GET /api/v1/pmo/events SSE endpoint that subscribes to the EventBus for PMO-relevant events (step.completed, gate.required, execution.complete, plan.saved). On each event, push a lightweight JSON payload with card_id and new column. (2) Add event filtering to only emit board-relevant changes. Frontend: (3) In usePmoBoard, attempt SSE connection to /api/v1/pmo/events on mount. On receiving card_update events, trigger a targeted board refresh. (4) If SSE connection fails or drops, fall back to 15-second polling (increased from 5s since SSE handles the real-time case). (5) Add a subtle visual indicator showing SSE connection status (green dot = live, gray = polling). Files: agent_baton/api/routes/pmo.py, agent_baton/core/events/bus.py, pmo-ui/src/hooks/usePmoBoard.ts, pmo-ui/src/components/KanbanBoard.tsx."

**Steps:**
1. Design SSE event payload format for board updates (~30 min)
2. Implement `GET /api/v1/pmo/events` SSE endpoint with EventBus subscription (~2 hours)
3. Add event filtering (only PMO-relevant topics) (~30 min)
4. Create SSE client in `usePmoBoard` with reconnection logic (~2 hours)
5. Implement polling fallback when SSE is unavailable (~30 min)
6. Add connection status indicator to KanbanBoard toolbar (~30 min)
7. Performance testing: compare I/O before/after with 10+ projects (~1 hour)
8. Test SSE disconnection/reconnection scenarios (~1 hour)

**Dependencies:** Phase 4 (double-scan fix should be in place before adding real-time to avoid amplifying I/O issues).

---

## Phase 6: Wire ADO Adapter and Cleanup

**Goal:** Connect the ADO integration, fix remaining low-severity issues, and clean up dead code. After this phase, the system is polished and ready for production use.

**Agent:** backend-engineer--python (ADO wiring), then frontend-engineer--react (cleanup)

**Gate:** ADO search returns real work items when `ADO_PAT` is set and shows "not configured" when unset. Signal creation allows type selection. "Planning" column is either populated or removed. PlanPreview is either used or deleted. UI state persists across page reload.

**Task description for baton plan:**
> "Wire ADO adapter and clean up remaining PMO UI issues. (1) Wire GET /pmo/ado/search to use AdoAdapter when ADO_PAT is set, returning empty results with message when unset. Remove hardcoded mock data. Remove 'placeholder' from all user-visible strings. (2) Add signal_type selector (bug/escalation/blocker) to SignalsBar add-signal form. (3) Remove the 'planning' column from tokens.ts COLUMNS since no backend status maps to it. (4) Wire PlanPreview into KanbanCard detail view for read-only plan display (if PMO-UX-020 card detail endpoint exists), otherwise delete PlanPreview.tsx. (5) Add sessionStorage persistence for board filter, signals panel state, active view, and Forge intake draft. Create usePersistedState hook. Files: agent_baton/api/routes/pmo.py, agent_baton/api/deps.py, pmo-ui/src/components/SignalsBar.tsx, pmo-ui/src/components/ForgePanel.tsx, pmo-ui/src/components/AdoCombobox.tsx, pmo-ui/src/tokens.ts, pmo-ui/src/components/PlanPreview.tsx, pmo-ui/src/App.tsx, pmo-ui/src/components/KanbanBoard.tsx, pmo-ui/src/hooks/usePersistedState.ts (new)."

**Steps:**
1. Wire `GET /pmo/ado/search` to `AdoAdapter` with PAT detection and fallback (~1 hour)
2. Remove "placeholder" labels from ForgePanel and AdoCombobox (~15 min)
3. Add `signal_type` selector to SignalsBar add-signal form (~30 min)
4. Remove "planning" column from tokens.ts COLUMNS (~10 min)
5. Wire PlanPreview into card detail view or delete it (~30 min)
6. Create `usePersistedState` hook wrapping useState + sessionStorage (~30 min)
7. Apply `usePersistedState` to view, filter, showSignals, and Forge intake fields (~45 min)
8. Test ADO integration with and without PAT configured (~30 min)
9. Full regression test pass (~1 hour)

**Dependencies:** Phase 4 (card detail endpoint needed if wiring PlanPreview; forge session tracking for state recovery).

---

## Appendix: Issue-to-Phase Mapping

| Issue | Phase | Notes |
|-------|-------|-------|
| PMO-UX-001 | 1 | Backend bug fix |
| PMO-UX-002 | 1 | Backend bug fix |
| PMO-UX-003 | 2 | Execution launch |
| PMO-UX-004 | 2 | Card action buttons |
| PMO-UX-005 | 3 | Forge state preservation |
| PMO-UX-006 | 2 | Board refresh |
| PMO-UX-007 | 2 | Signal linkage |
| PMO-UX-008 | 3 | Board-to-Forge nav |
| PMO-UX-009 | 3 | Signal re-polling |
| PMO-UX-010 | 3 | Font sizes |
| PMO-UX-011 | 3 | Keyboard shortcuts |
| PMO-UX-012 | 3 | Agent editing |
| PMO-UX-013 | 3 | Error visibility |
| PMO-UX-014 | 4 | Batch operations |
| PMO-UX-015 | 4 | Forge sessions |
| PMO-UX-016 | 4 | Double scan fix |
| PMO-UX-017 | 5 | Real-time SSE |
| PMO-UX-018 | 6 | ADO adapter |
| PMO-UX-019 | 4 | TypeScript types |
| PMO-UX-020 | 4 | Card detail endpoint |
| PMO-UX-021 | 4 | HealthBar click |
| PMO-UX-022 | 4 | Interview guard |
| PMO-UX-023 | 6 | Signal type selector |
| PMO-UX-024 | 6 | Planning column |
| PMO-UX-025 | 6 | PlanPreview cleanup |
| PMO-UX-026 | 6 | State persistence |

## Appendix: Expected Score Impact

| Dimension | Current | After Phase 2 | After Phase 4 | After Phase 6 |
|-----------|:---:|:---:|:---:|:---:|
| Workflow Completeness | 4 | 6 | 8 | 9 |
| Triage Velocity | 3 | 5 | 7 | 8 |
| Forge Authoring Flow | 6 | 7 | 8 | 9 |
| Board ↔ Forge Integration | 2 | 5 | 7 | 8 |
| Interaction Efficiency | 5 | 5 | 7 | 8 |
| API-UX Alignment | 6 | 7 | 8 | 9 |
| **Overall** | **4.3** | **5.8** | **7.5** | **8.5** |
