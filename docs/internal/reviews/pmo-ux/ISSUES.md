# PMO UX — Issue Tracker

## Issue Index

| # | Title | Severity | Complexity | Workflow | Depends On |
|---|-------|----------|-----------|----------|------------|
| PMO-UX-001 | Signal-to-Forge endpoint rejects requests (Pydantic 422) | CRITICAL | S | Triage | — |
| PMO-UX-002 | resolveSignal return type mismatch corrupts signal display | CRITICAL | S | Triage | — |
| PMO-UX-003 | No "Launch Execution" path in entire UI | CRITICAL | M | Cross-cutting | — |
| PMO-UX-004 | Kanban cards have no action buttons | CRITICAL | L | Kanban-Oversight | PMO-UX-003 |
| PMO-UX-005 | Forge state lost on tab switch without warning | HIGH | S | Forge-Author | — |
| PMO-UX-006 | Board not refreshed after Forge approval | HIGH | S | Forge-Author | — |
| PMO-UX-007 | Signal not linked back to plan after Forge approval | HIGH | S | Triage | PMO-UX-002 |
| PMO-UX-008 | Board-to-Forge navigation is one-way only | HIGH | M | Kanban-Oversight | PMO-UX-004 |
| PMO-UX-009 | Signals Bar does not re-poll and goes stale | HIGH | S | Triage | — |
| PMO-UX-010 | Font sizes below 9px throughout the application | HIGH | M | Cross-cutting | — |
| PMO-UX-011 | No keyboard shortcuts anywhere | HIGH | M | Cross-cutting | — |
| PMO-UX-012 | Agent assignment not editable in PlanEditor | HIGH | S | Forge-Author | — |
| PMO-UX-013 | Regeneration error feedback invisible | MEDIUM | S | Forge-Author | — |
| PMO-UX-014 | No batch operations at any level | MEDIUM | L | Triage | PMO-UX-001 |
| PMO-UX-015 | Forge session state not tracked (dead code) | MEDIUM | M | Forge-Author | — |
| PMO-UX-016 | Double scan in board endpoint | MEDIUM | S | Kanban-Oversight | — |
| PMO-UX-017 | No real-time board updates (SSE) | MEDIUM | L | Kanban-Oversight | PMO-UX-016 |
| PMO-UX-018 | ADO search returns mock data only | MEDIUM | M | Forge-Author | — |
| PMO-UX-019 | TypeScript types missing fields from backend | MEDIUM | S | Cross-cutting | — |
| PMO-UX-020 | No card detail / plan view endpoint | MEDIUM | M | Kanban-Oversight | — |
| PMO-UX-021 | HealthBar tiles not clickable | MEDIUM | S | Kanban-Oversight | — |
| PMO-UX-022 | Interview submit blocked with zero answers | MEDIUM | S | Forge-Author | — |
| PMO-UX-023 | Signal creation hardcodes signal_type to "bug" | LOW | S | Triage | — |
| PMO-UX-024 | "Planning" column has no entry path | LOW | S | Kanban-Oversight | — |
| PMO-UX-025 | PlanPreview component is dead code | LOW | S | Forge-Author | — |
| PMO-UX-026 | No state persistence across page reload | LOW | M | Cross-cutting | PMO-UX-005 |

## Issues

---

### PMO-UX-001: Signal-to-Forge Endpoint Rejects Requests (Pydantic 422)

- **Severity:** CRITICAL
- **Complexity:** S (< 1 hour)
- **Workflow:** Triage
- **Depends on:** —
- **Affected files:**
  - `agent_baton/api/models/requests.py` (create new `ForgeSignalRequest` model)
  - `agent_baton/api/routes/pmo.py:397-442` (change route parameter type from `ApproveForgeRequest` to `ForgeSignalRequest`)
- **Reproduction:**
  1. Open the PMO UI, expand the Signals Bar.
  2. If `api.signalToForge()` is ever called (currently unused in UI, but testable via curl): `POST /api/v1/pmo/signals/{signal_id}/forge` with body `{"project_id": "some-project"}`.
  3. Observe HTTP 422 response: `plan` field is required but not provided.
- **Proposed fix:**
  1. In `requests.py`, create a new Pydantic model:
     ```python
     class ForgeSignalRequest(BaseModel):
         project_id: str = Field(..., description="Target project ID")
     ```
  2. In `pmo.py:397`, change the route signature from `body: ApproveForgeRequest` to `body: ForgeSignalRequest`.
  3. Add a regression test: POST to `/pmo/signals/{id}/forge` with only `project_id` and verify 200.
- **Acceptance criteria:**
  - `POST /pmo/signals/{id}/forge` with `{"project_id": "test"}` returns 200 (not 422).
  - Existing `forge/approve` endpoint still uses `ApproveForgeRequest` unchanged.
  - Regression test passes.

---

### PMO-UX-002: resolveSignal Return Type Mismatch Corrupts Signal Display

- **Severity:** CRITICAL
- **Complexity:** S (< 1 hour)
- **Workflow:** Triage
- **Depends on:** —
- **Affected files:**
  - `agent_baton/api/routes/pmo.py:379-394` (change return value to full `PmoSignalResponse` OR keep as-is)
  - `pmo-ui/src/components/SignalsBar.tsx:31-33` (change state update from replace to filter)
  - `pmo-ui/src/api/client.ts:68-70` (fix return type)
- **Reproduction:**
  1. Open the PMO UI, expand Signals Bar.
  2. Click "Resolve" on any signal.
  3. Observe the signal row shows undefined/blank title and severity after resolve.
  4. The signal entry in the list becomes corrupted (missing fields).
- **Proposed fix (simplest path):**
  1. In `SignalsBar.tsx:31-33`, change:
     ```typescript
     // Before:
     setSignals(prev => prev.map(s => s.signal_id === id ? updated : s))
     // After:
     setSignals(prev => prev.filter(s => s.signal_id !== id))
     ```
  2. In `client.ts:68`, change return type from `Promise<PmoSignal>` to `Promise<{resolved: boolean, signal_id: string}>`.
  3. Alternatively, fix the backend to return full `PmoSignalResponse` after resolution.
- **Acceptance criteria:**
  - After clicking "Resolve" on a signal, the signal disappears from the Signals Bar cleanly (no corrupted entry).
  - No console errors or render crashes.
  - Backend signal is correctly marked as resolved.

---

### PMO-UX-003: No "Launch Execution" Path in Entire UI

- **Severity:** CRITICAL
- **Complexity:** M (1-4 hours)
- **Workflow:** Cross-cutting
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/api/client.ts` (add `startExecution` method)
  - `pmo-ui/src/components/ForgePanel.tsx:305-328` (add "Start Execution" button to saved phase)
  - `pmo-ui/src/api/types.ts` (add execution request/response types if needed)
- **Reproduction:**
  1. Open the PMO UI, navigate to The Forge.
  2. Create a plan: fill intake, generate, approve.
  3. On the saved confirmation screen, observe only "New Plan" and "Back to Board" buttons.
  4. No way to start execution from the UI. Must run `baton execute start` in terminal.
- **Proposed fix:**
  1. Add to `client.ts`:
     ```typescript
     startExecution: async (planPath: string): Promise<{task_id: string}> => {
       const res = await fetch(`${BASE}/executions`, {
         method: 'POST', headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({ plan_path: planPath })
       });
       return res.json();
     }
     ```
  2. In `ForgePanel.tsx` saved phase, add a "Start Execution" button between "New Plan" and "Back to Board" that calls `api.startExecution(savePath)`.
  3. Show loading spinner during execution start, success/error feedback after.
  4. On success, call board `refresh` callback.
- **Acceptance criteria:**
  - After approving a plan in Forge, a "Start Execution" button is visible.
  - Clicking it starts execution and shows success feedback.
  - The card on the board transitions from "queued" to "executing" after refresh.

---

### PMO-UX-004: Kanban Cards Have No Action Buttons

- **Severity:** CRITICAL
- **Complexity:** L (4+ hours)
- **Workflow:** Kanban-Oversight
- **Depends on:** PMO-UX-003 (needs startExecution in client.ts)
- **Affected files:**
  - `pmo-ui/src/components/KanbanCard.tsx:59-208` (add action buttons to expanded view)
  - `pmo-ui/src/components/KanbanBoard.tsx:203-204` (pass action callbacks to KanbanCard)
  - `pmo-ui/src/App.tsx:104-116` (wire card action handlers)
  - `pmo-ui/src/api/client.ts` (add any missing API methods)
  - `agent_baton/api/routes/pmo.py` (may need new endpoints for card-level actions)
- **Reproduction:**
  1. Open the PMO UI, view the Kanban board.
  2. Click any card to expand it.
  3. Observe only metadata (program, gates, agents) — no action buttons.
  4. Cannot start execution, resolve gates, re-forge, or view plan from the card.
- **Proposed fix:**
  1. Define a `CardAction` type: `'start' | 'reforge' | 'resolve-gate' | 'view-plan'`.
  2. Add `onAction?: (card: PmoCard, action: CardAction) => void` prop to `KanbanCard`.
  3. In the expanded section, render context-sensitive buttons:
     - `queued` column: "Start Execution" button
     - `awaiting_human` column: "Resolve Gate" button
     - `executing` with error: "Re-forge" button
     - All columns: "View Plan" button
  4. Wire callbacks through `KanbanBoard` to `App.tsx`.
  5. For "Start Execution": call `api.startExecution(card.card_id)`.
  6. For "Re-forge": call `openForge()` with card context mapped to signal shape.
  7. For "View Plan": open a modal with `PlanPreview` (requires card detail endpoint, see PMO-UX-020).
- **Acceptance criteria:**
  - Queued cards show a "Start Execution" button that starts execution.
  - Awaiting_human cards show a "Resolve Gate" button.
  - Failed/errored cards show a "Re-forge" button that opens Forge with context.
  - All cards show a "View Plan" link.
  - Actions produce visible results (card status changes on board refresh).

---

### PMO-UX-005: Forge State Lost on Tab Switch Without Warning

- **Severity:** HIGH
- **Complexity:** S (< 1 hour)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/App.tsx:10-21,74-77` (keep ForgePanel mounted but hidden, or add guard)
  - `pmo-ui/src/components/ForgePanel.tsx` (optional: add sessionStorage persistence)
- **Reproduction:**
  1. Navigate to The Forge, fill in a description, generate a plan.
  2. While viewing the plan preview, click "AI Kanban" tab.
  3. Click "The Forge" tab again.
  4. Observe: all state is gone — description empty, plan lost, back to intake phase.
- **Proposed fix (Option A — lowest effort):**
  1. In `App.tsx`, render both views simultaneously but use CSS `display: none` to hide the inactive one:
     ```tsx
     <div style={{ display: view === 'kanban' ? 'block' : 'none' }}>
       <KanbanBoard ... />
     </div>
     <div style={{ display: view === 'forge' ? 'block' : 'none' }}>
       <ForgePanel ... />
     </div>
     ```
  2. Remove the conditional mount/unmount pattern.
  3. This preserves all ForgePanel state across tab switches without any state management changes.
- **Acceptance criteria:**
  - Navigate to Forge, generate a plan, switch to Kanban, switch back to Forge.
  - Plan is still visible. All state preserved.
  - No `window.confirm` needed for this fix (state is simply preserved).

---

### PMO-UX-006: Board Not Refreshed After Forge Approval

- **Severity:** HIGH
- **Complexity:** S (< 1 hour)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/App.tsx` (pass `refresh` callback from `usePmoBoard` to ForgePanel)
  - `pmo-ui/src/components/ForgePanel.tsx:129-139` (call refresh on approval success)
- **Reproduction:**
  1. Navigate to Forge, create and approve a plan.
  2. Click "Back to Board".
  3. Observe: the new card does not appear immediately. Wait 5 seconds for the poll cycle.
- **Proposed fix:**
  1. In `App.tsx`, extract `refresh` from `usePmoBoard` and pass it to `ForgePanel` as `onPlanSaved` prop.
  2. In `ForgePanel.handleApprove`, after the API succeeds (before `setPhase('saved')`), call `onPlanSaved?.()`.
  3. The board will immediately re-fetch and show the new card when the user navigates back.
- **Acceptance criteria:**
  - After approving a plan and clicking "Back to Board", the new card is immediately visible.
  - No 5-second delay.

---

### PMO-UX-007: Signal Not Linked Back to Plan After Forge Approval

- **Severity:** HIGH
- **Complexity:** S (< 1 hour)
- **Workflow:** Triage
- **Depends on:** PMO-UX-002 (resolveSignal must work correctly first)
- **Affected files:**
  - `pmo-ui/src/components/ForgePanel.tsx:129-139` (add resolveSignal call after approval)
  - `pmo-ui/src/api/client.ts` (resolveSignal already exists)
- **Reproduction:**
  1. Open Signals Bar, click "Forge" on a signal.
  2. Complete the Forge flow: generate, approve.
  3. Return to board and open Signals Bar.
  4. Observe: the signal is still listed as "open" despite a plan having been created from it.
- **Proposed fix:**
  1. In `ForgePanel.handleApprove`, after the approval API succeeds, check if `initialSignal` is set.
  2. If set, call `api.resolveSignal(initialSignal.signal_id)`.
  3. This marks the signal as triaged and clears it from the open signals list.
- **Acceptance criteria:**
  - After forging a plan from a signal and approving it, the signal disappears from the open signals list.
  - The signal's `forge_task_id` is populated on the backend (if the resolve endpoint supports it).

---

### PMO-UX-008: Board-to-Forge Navigation Is One-Way Only

- **Severity:** HIGH
- **Complexity:** M (1-4 hours)
- **Workflow:** Kanban-Oversight
- **Depends on:** PMO-UX-004 (card action buttons infrastructure)
- **Affected files:**
  - `pmo-ui/src/components/KanbanCard.tsx` (add "Re-forge" / "Edit in Forge" button)
  - `pmo-ui/src/components/KanbanBoard.tsx` (pass `onCardToForge` callback)
  - `pmo-ui/src/App.tsx` (handle card-to-Forge navigation with context)
- **Reproduction:**
  1. View a queued card on the board.
  2. Want to edit the plan or re-forge it.
  3. No button or link to open this card's plan in the Forge.
  4. Must manually navigate to Forge and re-enter the task description from scratch.
- **Proposed fix:**
  1. Add a "Re-forge" button to cards with `error` set, and an "Edit in Forge" button to `queued` cards.
  2. On click, construct a synthetic `PmoSignal`-compatible object from the card's title and description.
  3. Call `App.openForge(syntheticSignal)` to pre-populate the Forge intake with the card's context.
  4. For full plan editing, would need `GET /pmo/cards/{card_id}/plan` endpoint (see PMO-UX-020).
- **Acceptance criteria:**
  - A queued card has an "Edit in Forge" button that opens Forge pre-filled with the card's context.
  - A failed card has a "Re-forge" button that opens Forge with the error context.
  - The Forge intake is pre-populated — no manual data re-entry.

---

### PMO-UX-009: Signals Bar Does Not Re-Poll and Goes Stale

- **Severity:** HIGH
- **Complexity:** S (< 1 hour)
- **Workflow:** Triage
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/components/SignalsBar.tsx:23-28` (add polling interval)
  - `pmo-ui/src/components/KanbanBoard.tsx:66-80` (add signal count badge to toggle button)
  - `pmo-ui/src/hooks/usePmoBoard.ts` (optionally pre-fetch signal count)
- **Reproduction:**
  1. Open Signals Bar, note the signal list.
  2. Create a signal via CLI or another client: `baton pmo add-signal --title "test" --severity high`.
  3. Observe: the Signals Bar does not show the new signal until closed and reopened.
- **Proposed fix:**
  1. In `SignalsBar.tsx`, add a polling interval (every 15-30 seconds) similar to `usePmoBoard`:
     ```typescript
     useEffect(() => {
       const fetchSignals = () => api.getSignals().then(setSignals);
       fetchSignals();
       const interval = setInterval(fetchSignals, 15000);
       return () => clearInterval(interval);
     }, []);
     ```
  2. Add a "Refresh" button as immediate fallback.
  3. Show open signal count as a badge on the Signals toggle button (always visible, even when panel is closed).
- **Acceptance criteria:**
  - Signals update automatically every 15 seconds.
  - New signals appear without manual panel close/reopen.
  - Signal count badge is visible on the Signals button when panel is closed.

---

### PMO-UX-010: Font Sizes Below 9px Throughout Application

- **Severity:** HIGH
- **Complexity:** M (1-4 hours)
- **Workflow:** Cross-cutting
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/tokens.ts` (add named font size constants)
  - `pmo-ui/src/components/KanbanCard.tsx` (update inline styles)
  - `pmo-ui/src/components/KanbanBoard.tsx` (update column header styles)
  - `pmo-ui/src/components/HealthBar.tsx` (update stat line styles)
  - `pmo-ui/src/components/ForgePanel.tsx` (update form label styles)
  - `pmo-ui/src/App.tsx` (update nav tab styles)
- **Reproduction:**
  1. Open the PMO UI on a 1080p display.
  2. Observe card titles at 9px, metadata at 7-8px, column headers at 7-9px.
  3. Attempt to read card metadata at arm's length — difficult to impossible.
- **Proposed fix:**
  1. Add to `tokens.ts`:
     ```typescript
     export const FONT_SIZES = {
       xs: '9px',    // supplementary only: timestamps, IDs
       sm: '11px',   // minimum for scannable content
       md: '12px',   // card titles, column headers
       lg: '14px',   // section headers
     };
     ```
  2. Update all components to use these constants. Minimum rule: nothing below 9px, card titles at 12px, metadata at 11px, column headers at 11px.
- **Acceptance criteria:**
  - No text element in the UI is below 9px.
  - Card titles are at least 12px.
  - All scannable content (metadata, status, phase) is at least 11px.
  - Visual regression check: board still fits on a 1920x1080 screen without horizontal scroll.

---

### PMO-UX-011: No Keyboard Shortcuts Anywhere

- **Severity:** HIGH
- **Complexity:** M (1-4 hours)
- **Workflow:** Cross-cutting
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/App.tsx` (add global keydown listener)
  - `pmo-ui/src/hooks/useHotkeys.ts` (new file — lightweight hotkey hook)
- **Reproduction:**
  1. Open the PMO UI.
  2. Press `N` — nothing happens (expected: open New Plan / Forge).
  3. Press `S` — nothing happens (expected: toggle Signals).
  4. Press `Esc` — nothing happens (expected: close expanded card / cancel).
- **Proposed fix:**
  1. Create `useHotkeys.ts`:
     ```typescript
     export function useHotkeys(keymap: Record<string, () => void>) {
       useEffect(() => {
         const handler = (e: KeyboardEvent) => {
           if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
           const fn = keymap[e.key.toLowerCase()];
           if (fn) { e.preventDefault(); fn(); }
         };
         window.addEventListener('keydown', handler);
         return () => window.removeEventListener('keydown', handler);
       }, [keymap]);
     }
     ```
  2. In `App.tsx`, add: `useHotkeys({ n: openForge, s: toggleSignals, Escape: closeExpanded })`.
  3. Add a `?` shortcut that shows a keyboard shortcut overlay.
- **Acceptance criteria:**
  - `N` opens Forge from any view.
  - `S` toggles Signals panel from Kanban view.
  - `Esc` closes expanded card or cancels Forge generation.
  - Shortcuts do not fire when focus is in an input/textarea.

---

### PMO-UX-012: Agent Assignment Not Editable in PlanEditor

- **Severity:** HIGH
- **Complexity:** S (< 1 hour)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/components/PlanEditor.tsx:188-196` (make agent chip a dropdown)
- **Reproduction:**
  1. In Forge, generate a plan.
  2. In PlanEditor preview, observe agent name (e.g., "backend-engineer") as a static cyan chip.
  3. Try to click the agent chip — nothing happens. No way to change agent assignment.
- **Proposed fix:**
  1. Define a static agent roster list in `tokens.ts` or a new `agents.ts` constants file.
  2. When `editingStep === step.step_id`, render the agent chip as a `<select>` dropdown populated from the roster.
  3. On change, update `step.agent_name` via `onPlanChange`.
- **Acceptance criteria:**
  - Clicking a step's agent chip in edit mode shows a dropdown of available agents.
  - Selecting a different agent updates the plan preview immediately.
  - The change persists through approval (the approved plan has the new agent).

---

### PMO-UX-013: Regeneration Error Feedback Invisible

- **Severity:** MEDIUM
- **Complexity:** S (< 1 hour)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/components/ForgePanel.tsx:121-127,183-262,247-261` (move error display, add cancel button)
- **Reproduction:**
  1. In Forge, generate a plan, click "Regenerate".
  2. Kill the backend or simulate a network error.
  3. Observe: UI returns to preview phase with no error message visible.
  4. During generation/regeneration, there is no cancel button — must wait or navigate away (losing state).
- **Proposed fix:**
  1. Move the `generateError` display element outside the `phase === 'intake' || phase === 'generating'` conditional so it renders in preview phase too.
  2. Add a "Cancel" button during `generating`/`regenerating` phases:
     ```tsx
     {(phase === 'generating' || phase === 'regenerating') && (
       <button onClick={() => { abortRef.current?.abort(); setPhase(phase === 'regenerating' ? 'preview' : 'intake'); }}>
         Cancel
       </button>
     )}
     ```
  3. Map common API error codes to friendly messages.
- **Acceptance criteria:**
  - Regeneration failure shows an error message in the preview phase.
  - Cancel button is visible during generation/regeneration.
  - Clicking Cancel aborts the request and returns to the appropriate phase.

---

### PMO-UX-014: No Batch Operations at Any Level

- **Severity:** MEDIUM
- **Complexity:** L (4+ hours)
- **Workflow:** Triage
- **Depends on:** PMO-UX-001 (signal-to-forge must work first for batch forge)
- **Affected files:**
  - `agent_baton/api/routes/pmo.py` (add batch endpoints)
  - `agent_baton/api/models/requests.py` (add batch request models)
  - `pmo-ui/src/api/client.ts` (add batch API methods)
  - `pmo-ui/src/components/SignalsBar.tsx` (add checkbox selection + bulk actions)
  - `pmo-ui/src/components/KanbanBoard.tsx` (add card multi-select)
  - `pmo-ui/src/components/KanbanCard.tsx` (add checkbox for selection)
- **Reproduction:**
  1. Have 5 open signals in the Signals Bar.
  2. Want to resolve all 5 at once — must click "Resolve" 5 times individually.
  3. Want to forge plans for 3 signals — must do 3 separate Forge roundtrips.
- **Proposed fix:**
  1. Backend: Add `POST /pmo/signals/batch/resolve` accepting `{"signal_ids": [...]}`.
  2. Backend: Add `POST /pmo/signals/batch/forge` accepting `{"signal_ids": [...], "project_id": "..."}`.
  3. Frontend: Add checkbox to each signal row in `SignalsBar`. Add "Resolve Selected" and "Forge Selected" bulk action buttons.
  4. Frontend: Add hover-revealed checkbox to `KanbanCard`. Add floating action bar when items are selected.
- **Acceptance criteria:**
  - Can select multiple signals and resolve them in one click.
  - Can select multiple signals and batch-forge plans.
  - Batch operations show progress feedback and update the UI on completion.

---

### PMO-UX-015: Forge Session State Not Tracked (Dead Code)

- **Severity:** MEDIUM
- **Complexity:** M (1-4 hours)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `agent_baton/core/pmo/forge.py` (add calls to pmo_sqlite_store session methods)
  - `agent_baton/api/routes/pmo.py` (add `GET /pmo/forge/sessions`, `GET /pmo/forge/sessions/{id}` endpoints)
  - `agent_baton/api/deps.py` (ensure pmo_sqlite_store is injected into forge routes)
- **Reproduction:**
  1. Start a Forge session, generate a plan.
  2. Close the browser tab.
  3. Reopen PMO UI, navigate to Forge.
  4. No way to resume — all state is gone, no session history visible.
- **Proposed fix:**
  1. In `ForgeSession.create_plan()`, call `pmo_sqlite_store.create_forge_session(...)` to persist the session.
  2. In `ForgeSession.save_plan()`, call `pmo_sqlite_store.complete_forge_session(...)`.
  3. Add `GET /pmo/forge/sessions` endpoint listing active/recent sessions.
  4. Add `GET /pmo/forge/sessions/{id}` endpoint returning session details (including the last generated plan).
  5. In ForgePanel, on mount, check for active sessions and offer to resume.
- **Acceptance criteria:**
  - Forge sessions are tracked in the database.
  - `GET /pmo/forge/sessions` returns a list of sessions with status.
  - A user can see their recent forge sessions and resume an interrupted one.

---

### PMO-UX-016: Double Scan in Board Endpoint

- **Severity:** MEDIUM
- **Complexity:** S (< 1 hour)
- **Workflow:** Kanban-Oversight
- **Depends on:** —
- **Affected files:**
  - `agent_baton/api/routes/pmo.py:56-67` (refactor to single scan)
  - `agent_baton/core/pmo/scanner.py:183` (add `program_health_from_cards` method)
- **Reproduction:**
  1. Enable debug logging for `pmo.scanner`.
  2. Load the PMO board.
  3. Observe two full scan cycles in the logs per request (one from `scan_all()`, one from `program_health()`).
- **Proposed fix:**
  1. Add a `program_health_from_cards(cards: list[PmoCard]) -> list[ProgramHealth]` method to `PmoScanner` that computes health from a pre-existing card list instead of re-scanning.
  2. In `get_board` handler, call `scan_all()` once, then `program_health_from_cards(cards)`.
- **Acceptance criteria:**
  - Only one `scan_all()` call per board request.
  - Health data is identical to current output.
  - Board response time improves measurably (benchmark before/after with 10+ projects).

---

### PMO-UX-017: No Real-Time Board Updates (SSE)

- **Severity:** MEDIUM
- **Complexity:** L (4+ hours)
- **Workflow:** Kanban-Oversight
- **Depends on:** PMO-UX-016 (should fix double-scan before adding real-time)
- **Affected files:**
  - `agent_baton/api/routes/pmo.py` (add `GET /pmo/events` SSE endpoint)
  - `agent_baton/core/events/bus.py` (may need wildcard subscription)
  - `pmo-ui/src/hooks/usePmoBoard.ts` (add SSE client with polling fallback)
- **Reproduction:**
  1. Open PMO board in browser.
  2. Start an execution via CLI: `baton execute start`.
  3. Observe: board does not update until the next 5-second poll.
  4. With many projects, poll response becomes slow.
- **Proposed fix:**
  1. Add `GET /api/v1/pmo/events` SSE endpoint that subscribes to EventBus for PMO-relevant events (step.completed, gate.required, execution.complete).
  2. On event, push a lightweight payload: `{type: "card_update", card_id: "...", column: "..."}`.
  3. In `usePmoBoard`, attempt SSE connection first. On `card_update` events, call `fetchBoard()` to refresh. Fall back to 15-second polling if SSE fails.
- **Acceptance criteria:**
  - Board updates within 1 second of execution state changes (when SSE is connected).
  - Graceful fallback to polling when SSE is unavailable.
  - No increase in server I/O compared to current polling (SSE triggers targeted refresh, not full scan on every event).

---

### PMO-UX-018: ADO Search Returns Mock Data Only

- **Severity:** MEDIUM
- **Complexity:** M (1-4 hours)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `agent_baton/api/routes/pmo.py:314-329` (wire to AdoAdapter)
  - `agent_baton/api/deps.py` (add ADO adapter injection)
  - `pmo-ui/src/components/ForgePanel.tsx:190-193` (remove "placeholder" label)
  - `pmo-ui/src/components/AdoCombobox.tsx:51` (remove "placeholder" from input)
- **Reproduction:**
  1. In Forge intake, type a work item ID in the ADO search field.
  2. Observe: returns the same 5 hardcoded mock items regardless of query.
- **Proposed fix:**
  1. In `pmo.py:314-329`, check for `ADO_PAT` environment variable.
  2. If set, instantiate `AdoAdapter` and call `search_work_items(query)`.
  3. If not set, return empty results with a message: `"ADO integration not configured"`.
  4. Remove mock data.
  5. Update frontend labels: remove "(placeholder)" from all user-visible strings. When ADO is not configured, show "ADO not connected — set ADO_PAT to enable" in a muted style.
- **Acceptance criteria:**
  - With `ADO_PAT` set: ADO search returns real work items.
  - Without `ADO_PAT`: ADO field shows "not configured" message, no mock data.
  - No user-visible text contains the word "placeholder".

---

### PMO-UX-019: TypeScript Types Missing Fields From Backend

- **Severity:** MEDIUM
- **Complexity:** S (< 1 hour)
- **Workflow:** Cross-cutting
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/api/types.ts` (add missing fields to 3 interfaces)
- **Reproduction:**
  1. Inspect API response from `GET /pmo/signals` — `source_project_id` is present in JSON but not in TypeScript type.
  2. Similarly for `GET /pmo/projects` (`registered_at`) and card responses (`external_id`).
- **Proposed fix:**
  1. Add to `PmoSignal` interface: `source_project_id: string;`
  2. Add to `PmoProject` interface: `registered_at?: string;` and `ado_project?: string;`
  3. Add to `PmoCard` interface: `external_id?: string;`
  4. Update `_card_response` helper in `pmo.py:463-481` to include `external_id` in the response.
- **Acceptance criteria:**
  - TypeScript types match backend response shapes.
  - No type errors on build.
  - `source_project_id` is available for display in SignalsBar.

---

### PMO-UX-020: No Card Detail / Plan View Endpoint

- **Severity:** MEDIUM
- **Complexity:** M (1-4 hours)
- **Workflow:** Kanban-Oversight
- **Depends on:** —
- **Affected files:**
  - `agent_baton/api/routes/pmo.py` (add `GET /pmo/cards/{card_id}` endpoint)
  - `agent_baton/api/models/responses.py` (add `PmoCardDetailResponse` with plan data)
  - `pmo-ui/src/api/client.ts` (add `getCardDetail` method)
  - `pmo-ui/src/components/KanbanCard.tsx` (add "View Plan" button + modal)
- **Reproduction:**
  1. View any card on the board.
  2. Want to see the full plan, step details, execution history.
  3. No way to access this information from the UI.
- **Proposed fix:**
  1. Add `GET /pmo/cards/{card_id}` that reads the plan.json and execution-state.json for the card's task_id.
  2. Return a `PmoCardDetailResponse` with full plan phases/steps, execution state, and step outcomes.
  3. In `KanbanCard`, add "View Plan" button. On click, fetch card detail and show in a modal using `PlanPreview` (which is currently dead code — this gives it a purpose).
- **Acceptance criteria:**
  - `GET /pmo/cards/{card_id}` returns the card with full plan data.
  - "View Plan" button on cards opens a modal showing plan phases, steps, and agents.
  - `PlanPreview` component is used for the read-only display.

---

### PMO-UX-021: HealthBar Tiles Not Clickable

- **Severity:** MEDIUM
- **Complexity:** S (< 1 hour)
- **Workflow:** Kanban-Oversight
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/components/HealthBar.tsx:44-84` (add onClick and cursor styles)
  - `pmo-ui/src/components/KanbanBoard.tsx` (pass filter setter to HealthBar)
- **Reproduction:**
  1. View the HealthBar showing "Platform: 2 blocked".
  2. Click the Platform tile — nothing happens.
  3. Must manually find and click the "Platform" filter chip in the toolbar below.
- **Proposed fix:**
  1. Add `onProgramClick?: (program: string) => void` prop to `HealthBar`.
  2. In `KanbanBoard`, pass `setFilter` as `onProgramClick`.
  3. In `HealthBar`, add `onClick={() => onProgramClick?.(pg.program)}` to each tile div.
  4. Add `cursor: pointer` and a subtle hover background to tiles.
- **Acceptance criteria:**
  - Clicking a HealthBar tile filters the board to that program.
  - Tile shows hover state (cursor change + subtle background).
  - Clicking the same tile again returns to "all" filter.

---

### PMO-UX-022: Interview Submit Blocked With Zero Answers

- **Severity:** MEDIUM
- **Complexity:** S (< 1 hour)
- **Workflow:** Forge-Author
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/components/InterviewPanel.tsx:116-123` (remove zero-answer guard)
- **Reproduction:**
  1. In Forge, generate a plan, click "Regenerate".
  2. Read all interview questions and decide defaults are fine.
  3. Click "Re-generate with 0 answers" — button is disabled.
  4. Must type at least one answer to proceed.
- **Proposed fix:**
  1. Remove the `answeredCount === 0` condition from the disabled guard:
     ```typescript
     // Before:
     disabled={loading || answeredCount === 0}
     // After:
     disabled={loading}
     ```
  2. When `answeredCount === 0`, change button label to "Re-generate with defaults".
- **Acceptance criteria:**
  - Can click "Re-generate with defaults" without answering any questions.
  - Regeneration proceeds and produces a plan using the LLM's default interpretations.

---

### PMO-UX-023: Signal Creation Hardcodes signal_type to "bug"

- **Severity:** LOW
- **Complexity:** S (< 1 hour)
- **Workflow:** Triage
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/components/SignalsBar.tsx:43-49` (add signal_type selector)
- **Reproduction:**
  1. Open Signals Bar, click "+ Add Signal".
  2. Fill in title and severity.
  3. Submit — signal is created with `signal_type: 'bug'` regardless of intent.
  4. Cannot create escalation or blocker signals from the UI.
- **Proposed fix:**
  1. Add a `signalType` state variable initialized to `'bug'`.
  2. Render a `<select>` with options: Bug, Escalation, Blocker.
  3. Pass `signal_type: signalType` to the `createSignal` call.
- **Acceptance criteria:**
  - Add-signal form has a type selector with 3 options.
  - Created signals have the correct type in the backend.

---

### PMO-UX-024: "Planning" Column Has No Entry Path

- **Severity:** LOW
- **Complexity:** S (< 1 hour)
- **Workflow:** Kanban-Oversight
- **Depends on:** —
- **Affected files:**
  - `pmo-ui/src/tokens.ts:29` (remove "planning" from COLUMNS, OR)
  - `agent_baton/models/pmo.py:22-28` (add "planning" mapping to `_STATUS_TO_COLUMN`)
- **Reproduction:**
  1. Open the PMO board.
  2. Observe the "Planning" column is always empty.
  3. No execution status maps to this column.
- **Proposed fix (remove):**
  1. Remove the "planning" entry from `COLUMNS` in `tokens.ts` since no backend status uses it.
  2. Alternatively, if the concept is desired, map an early execution status (e.g., `planning`) to this column and update the executor to emit that status.
- **Acceptance criteria:**
  - Either: "Planning" column is removed and the board has one fewer empty column.
  - Or: "Planning" column is populated by cards in early execution states.

---

### PMO-UX-025: PlanPreview Component Is Dead Code

- **Severity:** LOW
- **Complexity:** S (< 1 hour)
- **Workflow:** Forge-Author
- **Depends on:** PMO-UX-020 (if choosing to wire PlanPreview into card detail view)
- **Affected files:**
  - `pmo-ui/src/components/PlanPreview.tsx` (delete OR wire into KanbanCard)
- **Reproduction:**
  1. Search for `PlanPreview` imports across the codebase — none found.
  2. The component is implemented but never rendered.
- **Proposed fix:**
  - If PMO-UX-020 (card detail endpoint) is implemented: use `PlanPreview` in the card detail modal for read-only plan display.
  - If not: delete `PlanPreview.tsx` to reduce code debt.
- **Acceptance criteria:**
  - Either: `PlanPreview` is imported and used in at least one component.
  - Or: `PlanPreview.tsx` is deleted and no references remain.

---

### PMO-UX-026: No State Persistence Across Page Reload

- **Severity:** LOW
- **Complexity:** M (1-4 hours)
- **Workflow:** Cross-cutting
- **Depends on:** PMO-UX-005 (Forge state preservation is a prerequisite — if state is already preserved via CSS hide, this issue covers the page-reload case)
- **Affected files:**
  - `pmo-ui/src/App.tsx` (persist `view` to sessionStorage)
  - `pmo-ui/src/components/ForgePanel.tsx` (persist draft to sessionStorage)
  - `pmo-ui/src/components/KanbanBoard.tsx` (persist filter, showSignals to sessionStorage)
- **Reproduction:**
  1. Set board filter to "Platform", open Signals panel.
  2. Refresh the page (F5).
  3. Observe: filter reset to "all", Signals panel closed, active view reset to Kanban.
- **Proposed fix:**
  1. Create a `usePersistedState` hook that wraps `useState` with `sessionStorage` read/write.
  2. Apply to: `view` in App.tsx, `filter` and `showSignals` in KanbanBoard.tsx.
  3. For Forge draft: debounce-persist `{description, projectId, taskType, priority}` to sessionStorage on every change. Restore on mount if present.
- **Acceptance criteria:**
  - Page reload preserves: active view, board filter, signals panel state.
  - Forge draft (intake fields) survives page reload.
  - Generated plan does not survive reload (too large for sessionStorage; acceptable trade-off).
