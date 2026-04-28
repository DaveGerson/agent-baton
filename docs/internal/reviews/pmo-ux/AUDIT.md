# PMO UX Audit — Scored Dashboard

## Executive Summary

The PMO system has a structurally sound foundation: the Kanban board provides zero-click portfolio health via the HealthBar, the Forge plan-generation wizard handles the happy-path intake-generate-preview-approve flow, and the signal-to-Forge context propagation eliminates manual data re-entry for triage. The design token system, color-consistent program identity, and inline step editing in the PlanEditor all reflect careful interaction design choices that should be preserved.

However, the system is **not yet self-contained as a UI product**. Two critical backend bugs (Pydantic 422 on signal-to-forge, resolveSignal return type mismatch) block the triage pipeline entirely. Beyond those bugs, the Kanban board is a read-only status monitor -- cards have zero action buttons, there is no way to launch execution from the UI, and there is no path from a board card back to the Forge. The Forge flow ends at "Saved & Queued" with no execution launch, forcing a CLI fallback for the most important next step. All Forge state is ephemeral and lost on tab switch without warning. Batch operations do not exist at any level, making high-volume Monday-morning triage a repetitive 5x-forge-roundtrip exercise.

The headline score is **4.3 / 10**. Two dimensions score below 4 (Triage Velocity and Board-Forge Integration), flagging them as critical gaps requiring priority remediation. The system needs 2 critical bug fixes, 4 high-severity workflow completions, and 8 medium-severity improvements before it can serve as a standalone management interface for the target persona.

## Scoring Dashboard

| Dimension | Score (0-10) | Key Evidence |
|-----------|:---:|-------------|
| Workflow Completeness | 4 | 5 of 8 scenarios have dead ends or require CLI fallback (F-WF-002, F-WF-005). Forge→Board→Execute chain broken. Card actions missing entirely. |
| Triage Velocity | 3 | **CRITICAL GAP.** Signal-to-forge endpoint returns 422 (F-AF-1). No quick-queue path -- every signal requires full Forge roundtrip. No batch operations. Signals panel hidden by default. Each bug triage = 5-6 clicks + view transition. |
| Forge Authoring Flow | 6 | Happy path works end-to-end. Interview refinement and PlanEditor inline editing are solid. Degraded by: invisible regeneration error (F-WF-006), no cancel button during generation, agent assignment not editable (F-IA-04), all state lost on tab switch (F-WF-004). |
| Board ↔ Forge Integration | 2 | **CRITICAL GAP.** Board→Forge is one-way only (F-WF-007). No card-to-Forge navigation. Board not refreshed after Forge approval (F-WF-001). Tab switch destroys Forge state. No execution launch from either surface. Forge and Board are functionally isolated. |
| Interaction Efficiency | 5 | Click counts reasonable for happy paths (5-6 for triage, 5-9 for feature plan). Degraded by: sub-9px font sizes (F-IA-07), no keyboard shortcuts anywhere (F-IA-12), card expansion reveals minimal new info (F-IA-09), HealthBar not clickable (F-IA-14). |
| API-UX Alignment | 6 | Core Forge and board APIs work. Degraded by: 2 broken endpoints (F-AF-1, F-AF-2), missing card detail endpoint (F-AF-14), dead forge session code (F-AF-10), ADO search returns mock data only (F-AF-11), double scan per board request (F-AF-13), TypeScript types missing 3 fields. |
| **Overall (unweighted avg)** | **4.3** | |

**Critical gaps (below 4):** Triage Velocity (3) and Board ↔ Forge Integration (2).

## Workflow Heatmap

| Workflow | Completeness | Speed | Integration | Notes |
|----------|:---:|:---:|:---:|-------|
| Triage | ❌ Broken | ❌ Slow | ⚠️ Partial | Signal-to-forge 422 blocks the path. No quick-queue. Signal not linked back after approval. No batch triage. |
| Forge-Author | ⚠️ Partial | ⚠️ Partial | ❌ Missing | Happy path works. State lost on tab switch. No execution launch. Regen error invisible. Agent not editable. |
| One-Shot | ❌ Missing | ❌ N/A | ❌ Missing | No card-to-forge action exists. No one-shot API. Manager must manually copy context to blank Forge. |
| Kanban-Oversight | ⚠️ Partial | ✅ Good | ❌ Missing | HealthBar excellent. Cards are read-only -- no actions, no launch, no resolve gate. Board not refreshed after changes. |

## Consolidated Finding List

Findings are deduplicated across all 4 research agents, merged with the highest severity where agents disagree, and sorted by severity then workflow.

---

### CF-001: Kanban Cards Have No Action Buttons — Board Is Read-Only

- **Severity:** CRITICAL
- **Workflow(s):** Kanban-Oversight, One-Shot, Triage
- **Source agents:** Workflow Auditor (F-WF-002), Interaction Analyst (F-IA-01, F-IA-09), Architecture Fitness (F-AF-3)
- **Component(s):** `KanbanCard.tsx:59-208`, `KanbanBoard.tsx:203-204`, `App.tsx:104-116`
- **Description:** The expanded card panel shows metadata only (program, gates_passed, agent chips). There are zero action buttons. Users cannot: launch execution for a queued card, retry/re-forge a failed card, resolve a gate for an awaiting_human card, view the full plan, or open the card in the Forge for editing. All of these require CLI commands. The card expansion reveals minimal new information beyond what is already visible in the collapsed state, making the expand interaction feel unrewarding. No `onCardAction` or `onForge` callback is passed to `KanbanCard`.
- **Impact:** The board is a display-only monitor. An engineering manager cannot act on anything they see. Cards stuck in awaiting_human require CLI to unblock. Queued plans require `baton execute start`. The board-to-Forge feedback loop is completely broken.
- **Recommendation:** Add context-sensitive action buttons based on `card.column`: queued → "Start Execution" + "Edit in Forge"; awaiting_human → "Resolve Gate"; failed → "Re-forge"; any → "View Plan". Wire `onCardAction` callback through `KanbanBoard` → `KanbanCard`. Expand section should also show full untruncated phase/error text and elapsed time.

---

### CF-002: No "Launch Execution" Path Exists in the Entire UI

- **Severity:** CRITICAL
- **Workflow(s):** Forge-Author, One-Shot, Kanban-Oversight
- **Source agents:** Workflow Auditor (F-WF-005), Architecture Fitness (F-AF-4)
- **Component(s):** `ForgePanel.tsx:305-328` (saved phase), `client.ts` (missing method), `KanbanCard.tsx`
- **Description:** After plan approval, the Forge shows "Saved & Queued" with only "New Plan" and "Back to Board" buttons. No "Start Execution" button exists. The execution API (`POST /api/v1/executions`) exists and accepts a plan dict, but `client.ts` has no `startExecution` method and no PMO component invokes it. The entire Forge flow -- intake, generate, preview, edit, approve -- ends with the user having to switch to a terminal and run `baton execute start`.
- **Impact:** This is the primary CLI forced-fallback in the system. It directly contradicts the product promise of the UI. Every plan created through the Forge requires a terminal to actually execute.
- **Recommendation:** Add `startExecution(planId)` to `client.ts`. Add "Start Execution" button to ForgePanel saved phase and to queued cards on KanbanCard. Wire success callback to trigger board refresh.

---

### CF-003: Signal-to-Forge Endpoint Rejects All Frontend Requests (Pydantic 422)

- **Severity:** CRITICAL
- **Workflow(s):** Triage
- **Source agents:** Architecture Fitness (F-AF-1)
- **Component(s):** `agent_baton/api/routes/pmo.py:397-442`, `agent_baton/api/models/requests.py:203-214`, `pmo-ui/src/api/client.ts:71-76`
- **Description:** The `POST /pmo/signals/{signal_id}/forge` endpoint declares `ApproveForgeRequest` as its request body, which requires `plan: dict = Field(...)` (no default). The frontend sends only `{ project_id }`, omitting the `plan` field entirely. Pydantic validation rejects the request with HTTP 422 before the handler runs. The route docstring says "plan field is ignored" but validation fires first.
- **Impact:** The entire signal-to-forge triage flow via the API is non-functional. While the UI currently uses Path A (interactive Forge) which avoids this endpoint, the endpoint itself is broken and cannot be used for quick-queue or automation.
- **Recommendation:** Create a dedicated `ForgeSignalRequest(project_id: str)` model for this endpoint. The semantics differ from `ApproveForgeRequest` and warrant a separate model.

---

### CF-004: resolveSignal Return Type Mismatch Corrupts Signal Display

- **Severity:** CRITICAL
- **Workflow(s):** Triage
- **Source agents:** Workflow Auditor (F-WF-008), Architecture Fitness (F-AF-2)
- **Component(s):** `agent_baton/api/routes/pmo.py:379-394`, `pmo-ui/src/api/client.ts:68-70`, `pmo-ui/src/components/SignalsBar.tsx:31-33`
- **Description:** The backend `resolve_signal` returns `{"resolved": true, "signal_id": "..."}` (a plain dict). The frontend types this as `Promise<PmoSignal>` and replaces the signal in the local array with the response object. Since the response lacks `title`, `severity`, `status`, etc., the signal row renders with undefined fields causing display corruption.
- **Impact:** After resolving a signal, the Signals Bar shows a corrupted entry until the next data refresh. The signal is correctly resolved on the backend (data integrity preserved), but the UI breaks.
- **Recommendation:** Either change the backend to return the full updated `PmoSignalResponse` after resolution, or change the frontend to filter the resolved signal out of state: `setSignals(prev => prev.filter(s => s.signal_id !== id))`.

---

### CF-005: All Forge State Is Ephemeral — Lost on Tab Switch Without Warning

- **Severity:** HIGH
- **Workflow(s):** Forge-Author, Cross-cutting
- **Source agents:** Workflow Auditor (F-WF-004), Interaction Analyst (F-IA-05), Scenarios (Scenario 6)
- **Component(s):** `App.tsx:10-11,18-21,74-77`, `ForgePanel.tsx:31-50`
- **Description:** All Forge state is held in React `useState` variables with no persistence mechanism. Clicking the "AI Kanban" tab unmounts ForgePanel entirely, clearing all state. Clicking "The Forge" tab remounts with defaults. There is no confirmation dialog, no auto-save, and no draft persistence via localStorage/sessionStorage. A user 2+ minutes into plan review who checks the board loses their entire plan.
- **Impact:** Data loss scenario for interrupted managers. For complex feature plans (8-12 minutes of work), this nearly doubles the total time investment if the session is interrupted.
- **Recommendation:** Option A (lowest effort): Keep ForgePanel mounted but hidden (CSS `display: none`) when Kanban view is active. Option B: Persist Forge state to sessionStorage and restore on mount. Add a `window.confirm` guard when navigating away from a non-intake phase.

---

### CF-006: Board Does Not Refresh After Forge Approval

- **Severity:** HIGH
- **Workflow(s):** Forge-Author, Kanban-Oversight
- **Source agents:** Workflow Auditor (F-WF-001)
- **Component(s):** `ForgePanel.tsx:129-139`, `App.tsx:18-21`, `usePmoBoard.ts:43-54`
- **Description:** After plan approval, "Back to Board" sets `view = 'kanban'` and clears `forgeSignal`. The board waits for the next 5-second poll cycle before the new card appears. There is no immediate refresh trigger. The `refresh` callback is exported from `usePmoBoard` but never passed to ForgePanel.
- **Impact:** The user returns to the board, does not see the new card, and cannot tell whether approval worked. They may click "Approve" again or check the CLI.
- **Recommendation:** Pass `refresh` from `usePmoBoard` to ForgePanel via App.tsx. Call it inside `handleApprove` after API success, before transitioning to saved phase.

---

### CF-007: Signal Not Linked Back to Plan After Forge Approval

- **Severity:** HIGH
- **Workflow(s):** Triage
- **Source agents:** Workflow Auditor (F-WF-003)
- **Component(s):** `ForgePanel.tsx:36-39,129-139`, `client.ts:71-76`, `pmo.py:397-442`
- **Description:** When a user Forges a plan from a signal via the UI (Path A), the signal's `signal_id` is never sent to the backend during approval. The signal stays in `open` status, is never marked `triaged`, and never linked to the created plan via `forge_task_id`. The board card has no signal reference.
- **Impact:** The signal-to-plan audit trail is broken. Open signals are never cleared after being processed, so the Signals Bar always shows stale unresolved signals.
- **Recommendation:** In `ForgePanel.handleApprove()`, when `initialSignal` is set, call `api.resolveSignal(initialSignal.signal_id)` after approval succeeds.

---

### CF-008: Board-to-Forge Navigation Is One-Way Only

- **Severity:** HIGH
- **Workflow(s):** Kanban-Oversight, One-Shot
- **Source agents:** Workflow Auditor (F-WF-007), Interaction Analyst (F-IA-01), Architecture Fitness (F-AF-3)
- **Component(s):** `KanbanCard.tsx:59-208`, `App.tsx:104-116`, `KanbanBoard.tsx:203-204`
- **Description:** The user can navigate from board to Forge (via "+ New Plan" or signal "Forge" button), but there is no path from a card on the board back to the Forge. A user viewing a queued card cannot click "Edit Plan" to open that plan in the Forge editor. A user with a failed card cannot click "Re-forge" to iterate. The `card.card_id` contains the `task_id` needed for navigation but is never used.
- **Impact:** The Forge and Board are functionally isolated. No feedback loop exists between observing a problem on the board and authoring a response plan.
- **Recommendation:** Add `onCardAction` callback through `KanbanBoard` → `KanbanCard`. For re-forge, call `App.openForge()` with a synthetic `PmoSignal` constructed from the card's error and title.

---

### CF-009: Signals Bar Does Not Re-Poll and Goes Stale

- **Severity:** HIGH
- **Workflow(s):** Triage
- **Source agents:** Workflow Auditor (F-WF-008), Interaction Analyst (F-IA-02)
- **Component(s):** `SignalsBar.tsx:23-28`, `KanbanBoard.tsx:17`
- **Description:** SignalsBar fetches signals once on mount (`useEffect` with empty dependency array). There is no polling, no refresh button, and no event-driven update. Additionally, the Signals panel is hidden by default (`showSignals = false`), requiring 1 click + 1 network round-trip before any signals are visible. Critical signals are invisible on board landing.
- **Impact:** Signals go stale immediately. New signals created externally are not visible until the panel is closed and reopened. Critical-severity signals requiring immediate attention are hidden until the user manually opens the panel.
- **Recommendation:** Add polling interval (15-30s) or a refresh button. Show open signal count badge on the Signals toggle button (always visible). Pre-fetch signal count alongside board data in `usePmoBoard`.

---

### CF-010: Font Sizes Below 9px Throughout the Application

- **Severity:** HIGH
- **Workflow(s):** Cross-cutting
- **Source agents:** Interaction Analyst (F-IA-07)
- **Component(s):** `KanbanCard.tsx`, `KanbanBoard.tsx`, `HealthBar.tsx`, `ForgePanel.tsx`, `App.tsx`
- **Description:** Aggressive use of sub-10px font sizes: card title 9px, card metadata 7-8px, column headers 9px/7px, HealthBar stats 7px, Forge labels 8px, nav tabs 9px. Sub-9px text is below comfortable reading threshold at normal viewing distances and fails WCAG 2.1 guidance (14px body, 11px supplementary).
- **Impact:** Increased time to read card state. Accessibility failure. Eye fatigue during extended triage sessions.
- **Recommendation:** Establish type scale floor: 11px minimum for scannable content, 9px only for truly supplementary metadata. Card titles 12-13px. Add named size constants to `tokens.ts`.

---

### CF-011: No Keyboard Shortcuts Anywhere in the Application

- **Severity:** HIGH
- **Workflow(s):** Cross-cutting
- **Source agents:** Interaction Analyst (F-IA-12)
- **Component(s):** All components
- **Description:** No keyboard shortcuts exist for any primary action. The only keyboard handlers are `Enter` in SignalsBar add-form and PlanEditor step edit. No application-level shortcuts for: New Plan (N), Toggle Signals (S), Switch views, Close/Cancel (Esc).
- **Impact:** Mouse-only workflow adds 200-500ms per primary action for keyboard-first users. Over a 50-action triage session this adds 10-25 seconds of unnecessary mousing.
- **Recommendation:** Add a `useHotkeys` hook in App.tsx for application-level shortcuts. Priority: `N` = New Plan, `S` = Signals, `Esc` = close/cancel.

---

### CF-012: No Batch Operations Exist at Any Level

- **Severity:** MEDIUM
- **Workflow(s):** Triage, Kanban-Oversight, Cross-cutting
- **Source agents:** Workflow Auditor (F-WF-009), Interaction Analyst (F-IA-10), Architecture Fitness (F-AF-5), Scenarios (Scenario 7)
- **Component(s):** `SignalsBar.tsx`, `KanbanBoard.tsx`, `KanbanCard.tsx`, `pmo.py`
- **Description:** No multi-select or batch operation capability exists: no batch forge/resolve for signals, no batch execution start for cards, no batch API endpoints. Program filter is single-select. Processing 5 signals requires 5 separate Forge roundtrips.
- **Impact:** High-volume triage (Monday morning with 5+ signals) requires repetitive manual steps. Each bug triage is a full Board→Forge→Board round-trip.
- **Recommendation:** Add batch resolve/forge API endpoints. Add checkbox selection to SignalsBar and KanbanCard. Add floating action bar for bulk operations.

---

### CF-013: Error Recovery on Forge Regeneration Is Invisible

- **Severity:** MEDIUM
- **Workflow(s):** Forge-Author
- **Source agents:** Workflow Auditor (F-WF-006), Interaction Analyst (F-IA-16)
- **Component(s):** `ForgePanel.tsx:105-127,183-262,241-245,247-261`
- **Description:** On regeneration failure, `setPhase('preview')` is called and `generateError` is set, but the error display element is only rendered when `phase === 'intake' || phase === 'generating'`, so the error message is invisible in preview phase. Additionally, there is no cancel/abort button during generation -- the only way to cancel is to navigate away (losing all state). Error messages are raw API text, not user-friendly.
- **Impact:** Users who click "Regenerate" and get an error receive no feedback -- the UI silently returns to preview. Users stuck in generation with a slow request have no way to abort without losing all work.
- **Recommendation:** Move `generateError` display outside the intake-phase guard. Add a "Cancel" button during generating/regenerating phases. Map common API errors to friendly messages.

---

### CF-014: Agent Assignment Not Editable in PlanEditor

- **Severity:** HIGH
- **Workflow(s):** Forge-Author
- **Source agents:** Interaction Analyst (F-IA-04)
- **Component(s):** `PlanEditor.tsx:188-196`
- **Description:** Each step shows `agent_name` as a static cyan chip with no edit affordance. The click-to-edit input only covers `task_description`. Changing agent assignment requires a full regenerate cycle (3-4 additional clicks + LLM latency).
- **Impact:** Any agent assignment the LLM gets wrong requires full regeneration rather than a 2-second inline correction.
- **Recommendation:** Make the agent chip a dropdown select when the step is in edit mode, populated from a static list of known agents.

---

### CF-015: Forge Session State Not Tracked (Dead Code)

- **Severity:** MEDIUM
- **Workflow(s):** Forge-Author
- **Source agents:** Architecture Fitness (F-AF-10), Scenarios (Scenario 6)
- **Component(s):** `agent_baton/core/storage/pmo_sqlite.py:233-272`, `agent_baton/api/routes/pmo.py`, `agent_baton/core/pmo/forge.py`
- **Description:** `PmoSqliteStore` has full forge session management (create, complete, list). The database schema includes a `forge_sessions` table. However, no API endpoint exposes forge sessions, `ForgeSession` never calls the store methods, and the entire persistence layer is dead code.
- **Impact:** Session recovery after interruption is impossible. No audit trail of forge activity. No visibility into in-progress forge work from other team members.
- **Recommendation:** Wire forge session tracking into the workflow (create on `forge/plan`, update on `forge/approve`, expose via `GET /pmo/forge/sessions`).

---

### CF-016: Double Scan in Board Endpoint

- **Severity:** MEDIUM
- **Workflow(s):** Kanban-Oversight
- **Source agents:** Architecture Fitness (F-AF-12, F-AF-13)
- **Component(s):** `agent_baton/api/routes/pmo.py:56-67`, `agent_baton/core/pmo/scanner.py:156-171,183`
- **Description:** `get_board` calls `scanner.scan_all()` on line 60 and `scanner.program_health()` on line 61. Inside `program_health()`, `scan_all()` is called again. Every board request performs two complete filesystem/database scans. With 5-second polling, this is O(2N) I/O per request.
- **Impact:** At 50 projects, noticeable latency and unnecessary I/O pressure. Double the necessary work on every poll.
- **Recommendation:** Refactor `program_health()` to accept a pre-computed card list, or compute health inline from already-fetched cards.

---

### CF-017: No Real-Time Board Updates (Polling Only, No SSE)

- **Severity:** MEDIUM
- **Workflow(s):** Kanban-Oversight
- **Source agents:** Architecture Fitness (F-AF-6), Interaction Analyst (F-IA-08)
- **Component(s):** `pmo-ui/src/hooks/usePmoBoard.ts`, `agent_baton/api/routes/events.py`
- **Description:** Board uses 5-second polling. An SSE endpoint exists for per-task events (`GET /api/v1/events/{task_id}`) but there is no board-level SSE stream. Layout shifts occur when cards move columns between polls with no transition animation.
- **Impact:** Users see stale data for up to 5 seconds. At scale (50 projects), polling becomes expensive.
- **Recommendation:** Add board-level SSE endpoint subscribing to PMO-relevant EventBus topics. Frontend should use SSE for real-time updates and fall back to polling when SSE is unavailable.

---

### CF-018: ADO Search Returns Mock Data Only

- **Severity:** MEDIUM
- **Workflow(s):** Forge-Author
- **Source agents:** Architecture Fitness (F-AF-11), Interaction Analyst (F-IA-13)
- **Component(s):** `agent_baton/api/routes/pmo.py:314-329`, `agent_baton/core/storage/adapters/ado.py`, `ForgePanel.tsx:190-193`, `AdoCombobox.tsx:51`
- **Description:** The `GET /pmo/ado/search` endpoint returns hardcoded mock data. A fully implemented `AdoAdapter` exists with WIQL queries, batch fetching, and PAT auth but is not connected to the route. The UI labels the field "Import from ADO (placeholder)" which creates user confusion.
- **Impact:** ADO import feature is non-functional. Users see placeholder data. The word "placeholder" in production UI labels is a development artifact.
- **Recommendation:** Wire the route to use `AdoAdapter` when `ADO_PAT` is set, falling back to mock data otherwise. Remove "placeholder" from user-visible strings. Hide the field entirely when integration is not configured.

---

### CF-019: TypeScript Types Missing Fields From Backend Models

- **Severity:** MEDIUM
- **Workflow(s):** Cross-cutting
- **Source agents:** Architecture Fitness (F-AF-7, F-AF-8, F-AF-9)
- **Component(s):** `pmo-ui/src/api/types.ts`
- **Description:** Three TypeScript interfaces are missing fields present in their Python counterparts: `PmoSignal` missing `source_project_id`, `PmoProject` missing `registered_at` and `ado_project`, `PmoCard` missing `external_id`. The data is available in API responses but TypeScript code cannot access it type-safely.
- **Impact:** Frontend cannot display originating project for signals (reduces triage context), cannot show ADO project association, and cannot link cards to ADO work items.
- **Recommendation:** Add missing fields to TypeScript interfaces and update components to display them.

---

### CF-020: No Card Detail / Plan View Endpoint

- **Severity:** MEDIUM
- **Workflow(s):** Kanban-Oversight
- **Source agents:** Architecture Fitness (F-AF-14)
- **Component(s):** `agent_baton/api/routes/pmo.py`
- **Description:** No `GET /pmo/cards/{card_id}` or `GET /pmo/cards/{card_id}/plan` endpoint exists. Cards are only available through the bulk board endpoint. The frontend cannot drill into a card to see the full plan, execution history, or step details. Deep linking is impossible.
- **Impact:** Users cannot review full plan details from the board. No deep linking to specific cards/plans.
- **Recommendation:** Add `GET /pmo/cards/{card_id}` returning the card with full plan and execution state.

---

### CF-021: HealthBar Is Non-Interactive

- **Severity:** MEDIUM
- **Workflow(s):** Kanban-Oversight
- **Source agents:** Interaction Analyst (F-IA-14)
- **Component(s):** `HealthBar.tsx:44-84`
- **Description:** HealthBar program tiles show blocked/failed counts but are completely non-interactive. No `onClick`, no cursor pointer. A manager who sees "2 blocked" on a program must manually scan all Kanban columns to find those cards.
- **Impact:** HealthBar raises a question without providing a path to answer it. Investigation requires manual column scanning.
- **Recommendation:** Make each tile clickable to filter the board to that program. Add cursor pointer and hover state.

---

### CF-022: Interview Submit Blocked With Zero Answers

- **Severity:** MEDIUM
- **Workflow(s):** Forge-Author
- **Source agents:** Interaction Analyst (F-IA-06)
- **Component(s):** `InterviewPanel.tsx:116-123`
- **Description:** The "Re-generate" button is disabled when `answeredCount === 0`. The helper text says "unanswered questions use sensible defaults" but submitting with zero answers is impossible, contradicting the stated behavior.
- **Impact:** Users forced to answer at least one question to proceed, even when defaults are acceptable.
- **Recommendation:** Remove the `answeredCount === 0` disabled guard. Allow empty submit to trigger regeneration with defaults.

---

### CF-023: Signal Creation Hardcodes signal_type to "bug"

- **Severity:** LOW
- **Workflow(s):** Triage
- **Source agents:** Workflow Auditor (F-WF-011)
- **Component(s):** `SignalsBar.tsx:43-49`
- **Description:** The add-signal form hardcodes `signal_type: 'bug'`. The backend supports `bug | escalation | blocker` but the UI always sends `bug`.
- **Impact:** All manually-created signals are classified as bugs regardless of actual type.
- **Recommendation:** Add a signal_type selector dropdown to the add-signal form.

---

### CF-024: "Planning" Column Has No Entry Path

- **Severity:** LOW
- **Workflow(s):** Kanban-Oversight
- **Source agents:** Workflow Auditor (F-WF-013)
- **Component(s):** `tokens.ts:29`, `models/pmo.py:22-28`
- **Description:** The COLUMNS definition includes a "planning" column but no status maps to it. No code path sets `column="planning"`. The column is always empty.
- **Impact:** Permanently empty column confuses users about board flow.
- **Recommendation:** Either map an appropriate execution status to "planning" or remove the column from COLUMNS.

---

### CF-025: PlanPreview Component Is Dead Code

- **Severity:** LOW
- **Workflow(s):** Forge-Author
- **Source agents:** Workflow Auditor (F-WF-012), Interaction Analyst (F-IA-15)
- **Component(s):** `PlanPreview.tsx`, `ForgePanel.tsx`
- **Description:** `PlanPreview.tsx` is a fully implemented read-only plan viewer but is never imported or rendered. ForgePanel renders `PlanEditor` directly in the preview phase. The naming is confusing ("preview" phase renders an editor).
- **Impact:** Dead code with maintenance cost and developer confusion. No direct user impact.
- **Recommendation:** Either use PlanPreview in KanbanCard for read-only plan display, or delete it.

---

### CF-026: No State Persistence Across Page Reload

- **Severity:** LOW
- **Workflow(s):** Cross-cutting
- **Source agents:** Workflow Auditor (F-WF-010)
- **Component(s):** `App.tsx`, `ForgePanel.tsx`, `KanbanBoard.tsx`
- **Description:** No UI state is persisted to localStorage, sessionStorage, or URL. Page reload resets active view, program filter, signals panel visibility, and all Forge state. Board data re-fetches from server (not lost), but UI preferences and in-progress work are cleared.
- **Impact:** Low for board view (data re-fetches). High if mid-Forge and browser refreshes.
- **Recommendation:** Persist Forge draft to sessionStorage. Persist filter/panel state. Add URL-based view state.

## Strengths to Preserve

The following aspects are well-designed and should NOT be changed during remediation:

1. **HealthBar always visible** -- Zero-click portfolio health is the strongest part of the design. Per-program tiles with completion%, plan count, and blocked/failed states pack high signal density into a compact strip. (Scenario 4: health check = 0 clicks.)

2. **Signal-to-Forge context propagation** -- When a signal is opened in Forge, the description textarea is pre-filled with all signal metadata (title, severity, type, description). This eliminates manual copy-paste and is the correct behavior.

3. **PlanEditor inline editing** -- Click-to-edit on step description with autoFocus, Enter-to-commit, and blur-to-commit is a solid inline editing pattern. Phase 0 auto-expand reduces clicks for the most common review case.

4. **Choice buttons in InterviewPanel** -- Rendering choice questions as toggleable buttons (not dropdown selects) allows single-click answers, which is the right interaction for fast-answer workflows.

5. **Color-consistent program identity** -- Deterministic hash of program name assigns stable colors across sessions, consistent between HealthBar tiles and card dots. Creates coherent visual identity without configuration.

6. **Abort controller on generate** -- The `abortRef` pattern correctly cancels in-flight generation requests on unmount or re-generate, preventing stale plan responses from overwriting newer requests.

7. **"Awaiting Human" pulsing indicator** -- The animated orange dot in the toolbar communicates urgency at a glance and survives context-switching back to the board.

8. **5-second polling with mount guard** -- The `mountedRef` guard and conditional state updates prevent zombie state on unmounted components. Board data is effectively persistent via server re-fetch.

9. **Board error recovery** -- Error banner with "retrying every 5s" message on board polling failure is correct and informative.

10. **Forge intake preservation on generation failure** -- When `handleGenerate` fails, the intake form remains populated (description, project, type, priority). The user does not lose their input.
