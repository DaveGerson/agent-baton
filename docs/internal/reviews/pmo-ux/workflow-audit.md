# PMO UX Workflow Audit

**Auditor:** Workflow Auditor (automated analysis)
**Date:** 2026-03-24
**Scope:** React frontend (pmo-ui/) + FastAPI backend (agent_baton/api/routes/pmo.py + core/pmo/)
**Target user:** Engineering manager — triage bugs fast, author plans efficiently, monitor portfolio health without CLI fallback.

---

## Executive Summary

The PMO system has a solid structural foundation — the Forge flow controller, board polling, and signal triage path all function end-to-end for their happy paths. However, **nine significant workflow gaps** prevent the target user from operating without CLI fallback or context loss. The most severe issues are:

1. Approving a plan in the Forge does not cause the new card to appear on the board until the user manually navigates back — the board is not refreshed.
2. A card on the board is read-only: there is no "Launch Execution" button, no "Open in Forge" link, and no way to see the full plan details without going to the CLI.
3. The "Forge via signal" path (`POST /signals/{id}/forge`) bypasses the entire Forge interactive flow — it auto-generates and immediately saves, giving the user no preview or approval step.
4. All Forge state is ephemeral in component memory — a tab switch or navigation destroys it silently.
5. There is no batch operation capability at any level.

---

## Findings

---

### Finding F-WF-001: Board Does Not Refresh After Forge Approval

- **Severity:** HIGH
- **Workflow:** Forge-Author
- **Component(s):** `ForgePanel.tsx:129-139` (`handleApprove`), `App.tsx:18-21` (`backToBoard`), `usePmoBoard.ts:43-54`
- **Description:** After the user successfully approves a plan in the Forge, the flow transitions to the `saved` phase and shows a "Back to Board" button. Clicking that button calls `onBack()` in `App.tsx`, which calls `backToBoard()` — it sets `view = 'kanban'` and clears `forgeSignal`. However, `usePmoBoard` is mounted and polling on a 5-second timer in `KanbanBoard`. The hook is not told that a new plan was just saved; the board simply waits for the next poll cycle (up to 5 seconds) before the new card appears. More importantly, the scanner only surfaces a card when it finds an `execution-state.json` *or* a `plan.json` on disk. `ForgeSession.save_plan()` writes `plan.json` to `<project.path>/.claude/team-context/<task_id>/plan.json`. This should be picked up by `scan_project()` in the next poll. So the card *will* eventually appear — but there is no immediate visual confirmation and no manual refresh trigger exposed to the user.
- **Evidence:**
  - `ForgePanel.tsx:324-325`: `<button onClick={onBack}>Back to Board</button>` — calls `backToBoard()` with no refresh parameter.
  - `App.tsx:18-21`: `backToBoard()` does not call any board refresh.
  - `usePmoBoard.ts:56`: `refresh` is exported from the hook but never passed to `ForgePanel`.
- **Impact:** The user returns to the board, does not see the new card, and cannot tell whether the approval worked. They may click "Approve" again or check the CLI.
- **Recommendation:** Pass the `refresh` callback from `usePmoBoard` to `ForgePanel` (via `App.tsx`). Call it inside `handleApprove` immediately after the API succeeds, before transitioning to `saved` phase. Files: `App.tsx`, `ForgePanel.tsx`.

---

### Finding F-WF-002: Kanban Card Has No Actionable Controls

- **Severity:** CRITICAL
- **Workflow:** Kanban-Oversight
- **Component(s):** `KanbanCard.tsx:59-208`
- **Description:** The expanded card panel (`KanbanCard.tsx:181-205`) shows `program`, `gates_passed`, and a list of agent chips. There are zero action buttons. The user cannot:
  - Launch execution for a `queued` card
  - Retry or re-forge a `failed` card
  - Resolve a gate for a card in `awaiting_human`
  - View the full plan JSON or plan summary
  - Open the card's plan in the Forge for editing
  - Archive or dismiss a deployed card
  All of these are CLI-only operations (`baton execute start`, `baton execute gate`, etc.).
- **Evidence:** `KanbanCard.tsx:181-205` — the expanded section has two `<div>` elements showing metadata, no `<button>` elements.
- **Impact:** The board is a read-only status monitor. An engineering manager cannot act on anything they see. A card stuck in `awaiting_human` requires the CLI to unblock. A `queued` plan requires `baton execute start` before the board changes.
- **Recommendation:** Add context-sensitive action buttons based on `card.column`:
  - `queued`: "Start Execution" button → `POST /api/v1/executions` with the plan ID
  - `awaiting_human`: "Resolve Gate" button → needs a decision resolution API call
  - `executing` or `validating` with `card.error` set: "Re-forge" button → calls `openForge` with card context
  - `deployed`/any: "View Plan" link (read-only plan preview in a modal or slide-over)
  This requires `KanbanCard.tsx` to receive callback props and the board to wire them. It also requires exposing the execution-start endpoint from the API to the frontend client.

---

### Finding F-WF-003: Signal-to-Forge Path Bypasses Interactive Forge Flow

- **Severity:** HIGH
- **Workflow:** Triage
- **Component(s):** `SignalsBar.tsx:203` (`onForge` button), `App.tsx:13-16` (`openForge`), `ForgePanel.tsx:31-68` (intake pre-population)
- **Description:** There are two entirely different "signal to plan" paths, and the UI uses the wrong one:

  **Path A (UI — correct intent):** `SignalsBar` calls `onForge(sig)`, which propagates up to `App.tsx:openForge(signal)`, which opens `ForgePanel` with `initialSignal` set. `ForgePanel` pre-populates the description from the signal and the user then goes through intake → generate → preview → edit → approve. This is the correct interactive flow.

  **Path B (API — hidden path):** `api.signalToForge(id, projectId)` calls `POST /signals/{id}/forge`, which immediately generates AND saves the plan (calling `forge.save_plan()` inside the route handler at `pmo.py:437`). This path produces a plan on disk without any user review.

  The UI's "Forge" button in `SignalsBar` uses Path A. However, there is no UI that exercises Path B — meaning `api.signalToForge()` is defined in `client.ts:71-76` but never called from any component. If the API route is ever surfaced (e.g., via another integration), plans will be auto-saved with no user preview. Conversely, when using Path A through the UI, the original signal's `forge_task_id` is never linked back to the plan because `ForgeSession.signal_to_plan()` is never called — the signal remains in status `open` after a plan is created from it.
- **Evidence:**
  - `client.ts:71-76`: `signalToForge` defined but never called in any component.
  - `forge.py:209-247`: `signal_to_plan()` calls `save_plan()` directly — no approval gate.
  - `ForgePanel.tsx:36-39`: Pre-populates description from signal, but the signal's `signal_id` is never sent to the backend during approval.
  - `pmo.py:397-442`: The `/signals/{signal_id}/forge` route auto-saves without approval.
- **Impact:** (1) After a user Forges a plan from a signal via the UI, the signal stays `open` — it is never marked `triaged` and never linked to the created plan. The board card has no signal reference. (2) The API endpoint exists for direct auto-triage (e.g., webhooks), but there is no preview step in that path, which is high-risk for automated intake.
- **Recommendation:** In `ForgePanel.handleApprove()`, when `initialSignal` is set, call `api.resolveSignal(initialSignal.signal_id)` after approval succeeds (or add a dedicated "link signal" call that updates `forge_task_id` and sets status to `triaged`). Separately, document or gate the `/signals/{id}/forge` auto-save route with a confirmation step. Files: `ForgePanel.tsx`, `client.ts`, `pmo.py`.

---

### Finding F-WF-004: All Forge State Is Ephemeral — Lost on Tab Switch

- **Severity:** HIGH
- **Workflow:** Forge-Author, Cross-cutting
- **Component(s):** `App.tsx:10-11`, `ForgePanel.tsx:31-50`
- **Description:** `ForgePanel` holds all flow state in local React `useState` variables: `phase`, `description`, `projectId`, `taskType`, `priority`, `plan`, `interviewQuestions`, `generateError`, `saveError`, `savePath`. These are declared in the component body and have no persistence mechanism. `App.tsx` manages `view` and `forgeSignal` in `useState` — also ephemeral.

  When the user is mid-Forge (e.g., in the `preview` phase, reviewing a generated plan) and clicks the "AI Kanban" tab, `backToBoard()` is called:
  - `setView('kanban')` unmounts `ForgePanel`
  - `setForgeSignal(null)` clears the initial signal
  When the user clicks "The Forge" tab again, `openForge()` is called with no `signal` argument, which remounts `ForgePanel` with all state reset to defaults — the generated plan is gone.
- **Evidence:**
  - `App.tsx:74-77`: Kanban tab click calls `backToBoard()` which calls `setForgeSignal(null)`.
  - `App.tsx:75-76`: `openForge()` with no argument when Forge tab is clicked directly.
  - `ForgePanel.tsx:45-50`: `plan` state is `useState<ForgePlanResponse | null>(null)` — no restore path.
- **Impact:** A user who has spent 2+ minutes reviewing a generated plan, notices something on the board, switches tabs to check, and switches back — loses their entire plan and must regenerate. This is a severe productivity loss for the target user.
- **Recommendation:** Options in increasing durability: (A) Hoist `ForgePanel` state into `App.tsx` and keep the mounted `ForgePanel` hidden (CSS `display: none`) when the Kanban view is active — preserves all state without refactoring. (B) Persist Forge state to `sessionStorage` and restore on mount. (C) Route-based navigation (React Router) where each view has its own URL — state is preserved in component tree. Option A is the lowest-effort fix. Files: `App.tsx`, `ForgePanel.tsx`.

---

### Finding F-WF-005: No "Launch Execution" Path Exists in the UI

- **Severity:** CRITICAL
- **Workflow:** One-Shot, Kanban-Oversight
- **Component(s):** `client.ts` (missing method), `KanbanCard.tsx` (missing button), `ForgePanel.tsx:305-328` (saved phase)
- **Description:** The saved phase in `ForgePanel` (`phase === 'saved'`) shows a success state with a "New Plan" button and a "Back to Board" button. The user is told the plan is "Saved & Queued" — but there is no button to actually start execution. The API route `POST /api/v1/executions` exists (referenced in `requests.py:StartExecutionRequest`) but is not mapped in `client.ts` and is not called from any PMO component. The user must run `baton execute start` in the terminal to begin execution after saving a plan from the Forge.
- **Evidence:**
  - `ForgePanel.tsx:305-328`: The `saved` phase UI has two buttons — "New Plan" and "Back to Board". No "Start Execution" button.
  - `client.ts:31-90`: No `startExecution` method exists in the `api` object.
  - `forge.py:66-88` (`save_plan()`): Explicitly states "Does NOT create an ExecutionState — that happens when `baton execute start` is run."
- **Impact:** The entire Forge flow — intake, generate, preview, edit, approve — ends with the user having to switch to a terminal and run a CLI command. This is the primary CLI forced-fallback in the system and directly contradicts the product promise of the UI.
- **Recommendation:** Add `startExecution(planId: string)` to `client.ts` calling `POST /api/v1/executions` with `{ plan_id }`. Add a "Start Execution" button to the `saved` phase in `ForgePanel` that calls this method and shows loading/success state. Wire the success callback to call board `refresh`. Files: `client.ts`, `ForgePanel.tsx`.

---

### Finding F-WF-006: Error Recovery on Forge Generation Is Partial

- **Severity:** MEDIUM
- **Workflow:** Forge-Author
- **Component(s):** `ForgePanel.tsx:69-90` (`handleGenerate`), `ForgePanel.tsx:105-127` (`handleRegenerate`)
- **Description:** On generation failure, `setPhase('intake')` is called and `generateError` is set, showing the error message. The intake form remains populated. This is correct. However:

  1. **During `generating` phase, there is no cancel/abort button** in the UI. `abortRef` is wired to abort the fetch request (`ForgePanel.tsx:71-72`), but the only way to cancel is to navigate away (losing all state per F-WF-004) or wait for the timeout. The loading state shows only "Generating..." text with a disabled button — no escape hatch.

  2. **On approve failure**, `saveError` is set but the `saveError` display element is in the preview section (`ForgePanel.tsx:284-289`), which remains visible. The user can retry `handleApprove`. This part works correctly.

  3. **On re-generation failure** (`handleRegenerate`), `setPhase('preview')` is called — the user returns to the previous plan. `generateError` is set but the error display is only rendered when `phase === 'intake' || phase === 'generating'` (`ForgePanel.tsx:183-262`), so the error message is **invisible** when on the preview phase.
- **Evidence:**
  - `ForgePanel.tsx:121-127`: `catch` block calls `setGenerateError(...)` and `setPhase('preview')`, but the error element at `ForgePanel.tsx:241-245` is inside the `phase === 'intake' || phase === 'generating'` block.
  - `ForgePanel.tsx:247-261`: No cancel button for the `generating` state.
- **Impact:** (1) Users who click "Regenerate" and get an error receive no feedback — the UI silently returns to preview. They may not know the regeneration failed. (2) Users stuck in `generating` with a slow/hanging request have no way to abort without losing all work.
- **Recommendation:** (1) Move `generateError` display outside the intake-phase guard so it is visible in `preview` phase after a failed regeneration. (2) Add a "Cancel" button during `generating`/`regenerating` phases that calls `abortRef.current?.abort()` and resets to the appropriate prior phase. Files: `ForgePanel.tsx`.

---

### Finding F-WF-007: Board-to-Forge Navigation Is One-Way Only

- **Severity:** HIGH
- **Workflow:** Board-Forge Navigation, Kanban-Oversight
- **Component(s):** `KanbanCard.tsx:59-208`, `App.tsx:104-116`
- **Description:** The user can navigate from the board to the Forge (via the "+ New Plan" button or the signal "Forge" button). There is no path from a card on the board back to the Forge. A user viewing a `queued` card cannot click "Edit Plan" to open that plan in the Forge editor. A user with a `failed` card cannot click "Re-forge" to iterate on it. The `card.card_id` is the `task_id` of the saved plan, which is the filename key in the project's team-context. This information is available in the card data but is never used for navigation.
- **Evidence:**
  - `KanbanCard.tsx:59-208`: No `onClick` on any element that calls a parent navigation callback. The only `onClick` is the expand toggle at line 68.
  - `App.tsx:104-109`: `KanbanBoard` receives `onNewPlan` and `onSignalToForge` but no `onCardToForge` callback.
  - `KanbanBoard.tsx:203-204`: `KanbanCard` is rendered with only `card` and `columnColor` props — no action callbacks.
- **Impact:** The Forge and Board are functionally isolated. The board is a display-only view. No feedback loop exists between observing a problem on the board and authoring a response plan.
- **Recommendation:** Add an `onCardAction` callback through `KanbanBoard` → `KanbanCard`. For the re-forge case, this would call `App.openForge()` with a synthetic `PmoSignal` constructed from the card's error and title. This requires adding a card action interface and wiring callbacks through two component layers. Files: `App.tsx`, `KanbanBoard.tsx`, `KanbanCard.tsx`.

---

### Finding F-WF-008: Signals Bar Loads Once and Does Not Re-Poll

- **Severity:** MEDIUM
- **Workflow:** Triage, Signal Management
- **Component(s):** `SignalsBar.tsx:23-28`
- **Description:** `SignalsBar` fetches signals in a single `useEffect` with an empty dependency array. There is no polling, no refresh button, and no event-driven update. The board (`usePmoBoard`) polls every 5 seconds, but signals are static after mount. If a new signal is created externally (e.g., via a webhook or another CLI user), the `SignalsBar` will not show it until the component is unmounted and remounted (i.e., the user closes and reopens the Signals panel).

  Additionally, when the user resolves a signal (`handleResolve`), the `resolveSignal` API returns a `PmoSignal` object (the resolved signal), but the `PmoSignalResponse` type definition in `pmo.py:379-394` actually returns `{"resolved": True, "signal_id": signal_id}` — a plain dict, not a `PmoSignal`. The frontend code at `SignalsBar.tsx:33` treats the response as a `PmoSignal` and calls `prev.map(s => s.signal_id === id ? updated : s)`, replacing the signal with an object that has `resolved: true` and `signal_id` but no `status`, `title`, `severity`, etc. This would cause a render crash or display corruption.
- **Evidence:**
  - `SignalsBar.tsx:23-28`: `useEffect(() => { api.getSignals()... }, [])` — no interval, no refresh.
  - `pmo.py:379-394`: `resolve_signal` returns `{"resolved": True, "signal_id": signal_id}` — not a `PmoSignalResponse`.
  - `client.ts:68-70`: `resolveSignal` is typed as `Promise<PmoSignal>` but the actual API response shape is `{ resolved: boolean, signal_id: string }`.
  - `SignalsBar.tsx:32-33`: The `updated` value is mapped back into the signals array as if it were a `PmoSignal`, which it is not.
- **Impact:** (1) Signals go stale immediately — the manager sees an outdated triage queue. (2) Resolving a signal may replace the signal object with a malformed partial object that causes display corruption (missing `title`, `severity` fields used in rendering at lines 182, 188-198).
- **Recommendation:** (1) Add a short polling interval (15-30s) or a refresh button to `SignalsBar`. (2) Fix the `resolveSignal` type mismatch: either change the API to return the updated `PmoSignal`, or change the frontend to filter the resolved signal out of state rather than trying to replace it with the response. The simplest fix: `setSignals(prev => prev.filter(s => s.signal_id !== id))`. Files: `SignalsBar.tsx`, `client.ts`, `pmo.py`.

---

### Finding F-WF-009: No Batch Operations Exist

- **Severity:** MEDIUM
- **Workflow:** Cross-cutting, Triage, Kanban-Oversight
- **Component(s):** `SignalsBar.tsx`, `KanbanBoard.tsx`, `KanbanCard.tsx`
- **Description:** The system has no multi-select or batch operation capability at any level:
  - Signals: The user cannot select multiple signals and bulk-forge or bulk-resolve them.
  - Cards: The user cannot select multiple queued cards and batch-start execution.
  - Filter: The program filter on the board (`KanbanBoard.tsx:44-61`) is single-select — clicking a different program deactivates the current filter rather than adding to a multi-filter set.
  An engineering manager overseeing multiple programs who wants to triage 5 critical bugs must click "Forge" five times, navigate through the full Forge flow five times separately.
- **Evidence:** No checkbox or multi-select UI in any component. No batch API endpoints in `pmo.py`. Program filter logic at `KanbanBoard.tsx:21-23` uses `=== filter` string comparison (single value only).
- **Impact:** High-volume triage workflows require repetitive manual steps. No workaround within the UI exists; CLI automation is the only alternative.
- **Recommendation:** This is a feature gap rather than a bug. For the highest-value improvement: add a "Resolve All" button to `SignalsBar` for bulk signal resolution. For cards, multi-select with batch execution start is higher complexity but high value. Files: `SignalsBar.tsx`, `KanbanBoard.tsx`, `client.ts`, `pmo.py` (batch endpoints needed).

---

### Finding F-WF-010: State Persistence Across Page Reload Is Zero

- **Severity:** LOW
- **Workflow:** Cross-cutting
- **Component(s):** `App.tsx`, `ForgePanel.tsx`, `KanbanBoard.tsx`
- **Description:** None of the UI state is persisted to `localStorage`, `sessionStorage`, or the URL. On a full page reload:
  - Active view resets to `'kanban'` (`App.tsx:10`)
  - Active program filter resets to `'all'` (`KanbanBoard.tsx:17`)
  - Signals panel visibility resets to `false` (`KanbanBoard.tsx:18`)
  - Any in-progress Forge state is lost (see F-WF-004 for the tab-switch case)
  The board data itself (`usePmoBoard`) re-fetches on mount, so card data is not lost — it is just re-loaded from the server.
- **Evidence:** No calls to `localStorage`, `sessionStorage`, or `URLSearchParams` anywhere in the codebase.
- **Impact:** Low impact for board view (data re-fetches quickly). High impact if the user is mid-Forge and the browser tab is accidentally closed or refreshed. The engineering manager loses an in-progress plan authoring session with no warning.
- **Recommendation:** Persist the Forge draft to `sessionStorage` on every state change (debounced), restore it on mount. Persist active filter and signals-panel state to `sessionStorage`. URL-based state for the active view (`?view=forge`) would also allow bookmark/share. Files: `App.tsx`, `ForgePanel.tsx`, `KanbanBoard.tsx`.

---

### Finding F-WF-011: Signal Creation Hardcodes signal_type to "bug"

- **Severity:** LOW
- **Workflow:** Signal Management
- **Component(s):** `SignalsBar.tsx:43-49`
- **Description:** The "Add Signal" form in `SignalsBar` allows the user to set a title and severity, but `signal_type` is hardcoded to `'bug'` in the `createSignal` call. The `PmoSignal` model supports `bug | escalation | blocker`. An engineering manager creating an escalation or a blocker cannot accurately categorize it. The `CreateSignalRequest` schema on the backend enforces `pattern="^(bug|escalation|blocker)$"`, so the type field is validated — but the UI never sends anything other than `'bug'`.
- **Evidence:** `SignalsBar.tsx:44`: `signal_type: 'bug'` — hardcoded literal.
- **Impact:** All manually-created signals are classified as bugs regardless of actual type. This corrupts any future analytics or filtering that depends on signal type.
- **Recommendation:** Add a `signal_type` selector to the add-signal form alongside the existing severity selector. Options: Bug, Escalation, Blocker. Files: `SignalsBar.tsx`.

---

### Finding F-WF-012: PlanPreview Component Is Defined but Not Used

- **Severity:** LOW
- **Workflow:** Forge-Author
- **Component(s):** `PlanPreview.tsx`, `ForgePanel.tsx:264-293`
- **Description:** `PlanPreview.tsx` is a read-only plan viewer (shows stats, summary, phases, steps). `PlanEditor.tsx` is a fully interactive plan editor (same data, with add/remove/reorder/edit capabilities). In the `preview` phase, `ForgePanel` renders `PlanEditor` directly (line 291: `<PlanEditor plan={plan} onPlanChange={setPlan} />`). `PlanPreview` is never imported or used in `ForgePanel` or any other component. It exists as a standalone file but has no call sites.

  The naming is also potentially confusing: the Forge "preview" phase renders an editor, not a preview. A user arriving at the "preview" phase can immediately start editing phases and steps, which is the intended behavior — but the component name `PlanPreview` suggests otherwise.
- **Evidence:** `PlanPreview.tsx` exists at full implementation. No `import` of `PlanPreview` exists in any component file (`grep` of imports would confirm, but the code reading confirms `ForgePanel.tsx` imports only `PlanEditor`).
- **Impact:** Dead code. No direct user impact, but creates confusion for future developers about which component is authoritative for plan display. If `PlanPreview` is intended to be a read-only mode (e.g., for card detail view), it should be wired in; otherwise it should be removed.
- **Recommendation:** Either (A) use `PlanPreview` in `KanbanCard` expanded detail to show a read-only plan summary (requires fetching the plan JSON for a card — a new API endpoint `GET /pmo/plans/{task_id}`), or (B) remove the file. Files: `PlanPreview.tsx`, `ForgePanel.tsx`, `KanbanCard.tsx`.

---

### Finding F-WF-013: "Planning" Column Has No Entry Path

- **Severity:** LOW
- **Workflow:** Kanban-Oversight
- **Component(s):** `tokens.ts:29`, `pmo.py` (status mapping), `models/pmo.py:22-28`
- **Description:** The COLUMNS definition in `tokens.ts` includes a `planning` column (described as "Claude decomposing scope into steps"). The `_STATUS_TO_COLUMN` map in `models/pmo.py:22-28` does not map any `ExecutionState.status` value to `"planning"`. The only way a card would appear in this column is if a card's `column` field were set to `"planning"` at construction time — which does not happen in `scan_project()`. Plans without an execution state get `column="queued"`. Plans with execution state use the status mapping, which has no `planning` entry. The column will always be empty.
- **Evidence:** `models/pmo.py:22-28`: `_STATUS_TO_COLUMN` has no `"planning"` value. `scanner.py:138-150`: New plan-without-execution cards get `column="queued"` directly. No code path sets `column="planning"`.
- **Impact:** The "Planning" column is permanently empty, which confuses the user about what the column represents and creates a visual gap in the board flow.
- **Recommendation:** Either map an appropriate `ExecutionState.status` value to `"planning"` (e.g., if an early-execution status represents plan decomposition), or remove the column from `COLUMNS` in `tokens.ts` if the concept is not yet implemented. Files: `models/pmo.py`, `tokens.ts`.

---

## Workflow Trace Summary

### Workflow 1: Forge Full Flow

**Path:** `App.tsx → ForgePanel → api.forgePlan → PlanEditor → api.forgeApprove → saved phase`

- Intake form populates correctly from `initialSignal` when launched from a signal.
- `handleGenerate` calls `POST /pmo/forge/plan` — works end-to-end.
- `phase === 'preview'` renders `PlanEditor` (interactive) — works.
- Regenerate path: "Regenerate" button → `api.forgeInterview` → `InterviewPanel` → `api.forgeRegenerate` → back to `preview` — works structurally, but error feedback on failed re-generation is invisible (F-WF-006).
- "Approve & Queue" calls `api.forgeApprove` → saves plan JSON to disk. Works.
- **Dead ends:** (1) No cancel button during generation (F-WF-006). (2) No "Start Execution" in saved phase (F-WF-005). (3) Signal not linked back after approval (F-WF-003). (4) Board not refreshed (F-WF-001). (5) All state lost on tab switch (F-WF-004).

### Workflow 2: Kanban Card Interaction

**Path:** `KanbanBoard → KanbanCard (click to expand)`

- Card renders with title, meta, step progress pips, current phase, error text — works.
- Expand reveals program, gates_passed, agent chips — works.
- **Dead ends:** No actions available. Everything from "view full plan" to "launch execution" to "resolve gate" requires CLI (F-WF-002, F-WF-005, F-WF-007).

### Workflow 3: Signal Management

**Path:** Board toolbar → "Signals" button → `SignalsBar` → signal list → "Forge" or "Resolve"

- Toggle shows/hides the `SignalsBar` panel — works.
- Signals load once on mount (no re-poll) — partial (F-WF-008).
- "Add Signal" form works but hardcodes `signal_type = 'bug'` (F-WF-011).
- "Forge" button calls `openForge(sig)` → opens Forge with signal pre-populated — works for Path A.
- "Resolve" button has a type mismatch that corrupts the signal object in state (F-WF-008).
- Signal is never linked back to the plan after approval (F-WF-003).

### Workflow 4: Board to Forge Navigation

**Path:** `App.tsx → view state toggle`

- Clicking "The Forge" tab calls `openForge()` (no signal) — opens blank Forge. Works.
- Clicking "AI Kanban" tab calls `backToBoard()` — destroys all Forge state. **State lost** (F-WF-004).
- No path from a specific card to its plan in Forge (F-WF-007).

### Workflow 5: One-Shot Plan from Board

**Path:** Does not exist end-to-end.

- "+ New Plan" button on the board calls `openForge()` — opens blank Forge intake. The user can author a plan.
- After approval, execution cannot be started from the UI (F-WF-005).
- The user cannot generate a plan from a specific existing card — only from a blank intake or a signal.

### Workflow 6: Error Recovery

- Generation failure: Restores intake form with error message — works.
- Regeneration failure: Returns to preview but error message is invisible — broken (F-WF-006).
- Approval failure: `saveError` shown in preview section — works.
- Board polling failure: Error banner with "retrying every 5s" message shown in `KanbanBoard.tsx:144-155` — works.
- No cancel/abort affordance during generation or regeneration (F-WF-006).

### Workflow 7: Batch Operations

**Path:** Does not exist.

- No multi-select on signals.
- No multi-select on cards.
- No batch API endpoints.
- Program filter is single-select only.
- (F-WF-009)

### Workflow 8: State Persistence

- Board data: Re-fetches on mount in <5s. Effectively persistent via server.
- Board filter: Lost on reload.
- Signals panel open/closed: Lost on reload.
- Forge mid-flow: Lost on tab switch AND on reload.
- No `localStorage` / `sessionStorage` / URL state anywhere.
- (F-WF-010)

---

## Workflow Coverage Matrix

| Workflow | Status | Key Gap | Severity |
|----------|--------|---------|----------|
| Forge: Intake to Generation | Working | No cancel during generation | MEDIUM |
| Forge: Preview and Edit | Working | — | — |
| Forge: Regenerate via Interview | Working | Error feedback invisible on failure | MEDIUM |
| Forge: Approve and Queue | Partial | No execution start; board not refreshed; signal not linked | HIGH |
| Forge: State Persistence | Broken | All state lost on tab switch | HIGH |
| Kanban: View Card Status | Working | — | — |
| Kanban: Act on Card | Broken | No action buttons exist | CRITICAL |
| Kanban: Launch Execution from UI | Missing | No start-execution path in entire UI | CRITICAL |
| Kanban: Navigate Card to Forge | Missing | No card-to-Forge navigation | HIGH |
| Signals: Create Signal | Working | signal_type hardcoded to "bug" | LOW |
| Signals: View Signal List | Working | No re-polling; goes stale | MEDIUM |
| Signals: Triage Signal to Forge | Partial | Signal not linked back to plan after approval | HIGH |
| Signals: Resolve Signal | Broken | Type mismatch corrupts signal in state | MEDIUM |
| One-Shot: Bug Card to Fix Plan | Missing | Requires two CLI commands + full Forge flow | CRITICAL |
| Batch: Multi-signal triage | Missing | No multi-select or batch API | MEDIUM |
| Batch: Multi-card execution start | Missing | No multi-select or batch API | MEDIUM |
| Error Recovery: API failure (board) | Working | — | — |
| Error Recovery: API failure (Forge) | Partial | Regeneration error invisible; no cancel | MEDIUM |
| State: Page reload | Broken | Filter, Forge draft, view all reset | LOW |

---

## Prioritized Fix List

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| 1 | F-WF-002: Add action buttons to expanded card | Medium | Unblocks all board-driven workflows |
| 2 | F-WF-005: Add "Start Execution" to Forge saved phase + client.ts | Low | Removes primary CLI fallback |
| 3 | F-WF-001: Refresh board after Forge approval | Low | Immediate visual confirmation |
| 4 | F-WF-004: Preserve Forge state on tab switch | Low (hide/show) | Prevents data loss |
| 5 | F-WF-008: Fix resolveSignal type mismatch (filter vs. replace) | Low | Prevents render corruption |
| 6 | F-WF-003: Link signal to plan on approval | Low | Closes the signal→plan audit trail |
| 7 | F-WF-006: Fix invisible error on regeneration failure; add cancel button | Low | Restores user feedback |
| 8 | F-WF-007: Add card-to-Forge navigation callback | Medium | Closes board↔Forge loop |
| 9 | F-WF-013: Remove or implement "Planning" column | Low | Eliminates empty phantom column |
| 10 | F-WF-011: Add signal_type selector | Low | Correct signal categorization |
| 11 | F-WF-012: Remove or wire PlanPreview | Low | Dead code cleanup |
| 12 | F-WF-010: sessionStorage persistence for Forge draft | Medium | Prevents draft loss on reload |
| 13 | F-WF-009: Batch operations | High | High-volume triage efficiency |
