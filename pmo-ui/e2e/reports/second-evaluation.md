# PMO UI — Second E2E Evaluation Report

**Date:** 2026-03-28
**Evaluator:** Test Engineer (second pass)
**App URL:** http://localhost:3100/pmo/
**Files reviewed:** 17 source files + 9 spec files (831 tests across 3 Playwright projects)

---

## Executive Summary

The first remediation round was thorough — the accessibility violations and the
core workflow rough-edges are resolved. This second pass surfaces the next
tranche: gaps that live at the intersection of feature completeness, data
robustness, architecture cleanliness, and test coverage of untested user paths.

**44 findings** across 5 categories. Organized by priority: HIGH (12),
MEDIUM (19), LOW (13).

---

## HIGH Priority

---

### R2-01
**Category:** Robustness
**Priority:** HIGH
**Description:** `useHotkeys` recreates the event listener on every render because
`bindings` is an inline object literal in `App.tsx`. The object reference changes
on every render, which means `addEventListener` / `removeEventListener` are called
on every render cycle. The `n`, `s`, and `escape` handlers use `useCallback` with
empty dependency arrays but `openForge` and `backToBoard` are plain functions that
close over `setView`, so the bindings object is new on every render triggered by
`setView`.
**Recommendation:** Wrap the bindings object in `useMemo` inside `App.tsx`, or
move it to a stable `useRef`. Alternatively, have `useHotkeys` accept individual
key/handler pairs and memoize the listener internally.
**Effort:** small

---

### R2-02
**Category:** Robustness
**Priority:** HIGH
**Description:** `SignalsBar` renders two identical `role="alert"` divs for
`resolveError` — one at the top of the component (lines 184–199) and one inside
the signal list (lines 349–354). The second one fires a second screen-reader
announcement for the same error and will double-display the message visually
if both conditions are true simultaneously.
**Recommendation:** Remove the second `role="alert"` block (lines 349–354 in
`SignalsBar.tsx`). The first one is correct and correctly placed above the
signal list.
**Effort:** small

---

### R2-03
**Category:** Robustness
**Priority:** HIGH
**Description:** `KanbanCard.handleExecute` uses `setTimeout(() => setExecResult(null), 8000)`
to auto-clear the result banner, but the timeout is not cleared on component
unmount. If the card is removed from the board while the timeout is pending (e.g.
it transitions columns via SSE), the `setExecResult` call fires on an unmounted
component, which in React 18 is a no-op but logs a warning in development and will
become an error in future React versions.
**Recommendation:** Store the timeout ID in a `useRef` and clear it in a
`useEffect` cleanup, or use a custom `useSafeTimeout` hook.
**Effort:** small

---

### R2-04
**Category:** Feature
**Priority:** HIGH
**Description:** There is no toast/notification system. Every action feedback is
either inline (exec result banner inside card, save error banner inside Forge)
or completely silent. Cross-cutting actions — plan approved, signal created,
batch resolve — have no app-level feedback after the local inline state clears.
The `handleAddSignal` failure is silently swallowed (`// silent`). A user who
clicks "Add Signal" and gets a network error receives no feedback.
**Recommendation:** Add a lightweight toast system. A `ToastContext` + `useToast`
hook with a small fixed-position container in `App.tsx` would serve all
components. The add-signal failure and forge-approve success are the two highest
priority call sites to wire up first.
**Effort:** medium

---

### R2-05
**Category:** Robustness
**Priority:** HIGH
**Description:** `ForgePanel.handleApprove` does not disable the "Approve & Queue"
button while the save request is in flight. There is `saveError` state but no
`saving` boolean. A user who double-clicks "Approve & Queue" in a slow network
environment will fire two concurrent `POST /forge/approve` calls, potentially
creating two cards on the board.
**Recommendation:** Add a `saving` state boolean, set it to `true` during the
request, and use `disabled={saving}` on the Approve button. Pair with a loading
label like "Queuing…" for visual feedback.
**Effort:** small

---

### R2-06
**Category:** Architecture
**Priority:** HIGH
**Description:** `ForgePanel` is 660 lines and manages six distinct concern areas:
(1) project/form state, (2) SSE/generate lifecycle, (3) interview/regen flow,
(4) draft persistence, (5) navigation guard, (6) saved/launch phase. Each
phase (`intake`, `generating`, `preview`, `regenerating`, `saved`) contains
significant JSX. The component is at the outer limit of reviewable size and will
become hard to maintain as the interview/regen flow grows.
**Recommendation:** Extract at minimum: `IntakeForm` (the form fields + ADO
combobox area), `PreviewHeader` (approve/regen/error bar), and `SavedPhase` is
already extracted correctly. The `Phase` state machine could also be lifted into
a `useForgeWorkflow` hook that owns all transition logic, leaving `ForgePanel`
as a thin renderer.
**Effort:** large

---

### R2-07
**Category:** Robustness
**Priority:** HIGH
**Description:** `usePmoBoard` runs both SSE and a polling interval simultaneously
from the first mount. On initial load the polling fires at 5s intervals AND SSE
is attempted. If SSE connects within the first 5 seconds, there is a window where
both paths are active and `fetchBoard` can be called twice nearly simultaneously,
overwriting state with whichever response arrives last. Because both calls are
async and uncoordinated, the second response could be stale (fetched slightly
before the first but arriving after).
**Recommendation:** Add a request-ID (incrementing counter) or a `lastFetchTime`
guard in `fetchBoard` so a response that arrives out-of-order is discarded if a
newer response has already been applied.
**Effort:** medium

---

### R2-08
**Category:** Feature
**Priority:** HIGH
**Description:** The "Execute" button on a queued card launches execution
(`POST /execute/{id}`) but the card's column on the board is not updated
optimistically. The user sees "Launched (PID 12345)" then the banner clears after
8 seconds — but the card still shows as `queued` until the next SSE event or
polling cycle. On a slow/failed SSE connection this can be 5 seconds, leaving the
user uncertain whether the launch actually worked.
**Recommendation:** After a successful `executeCard` response, optimistically
update the card's `column` to `'executing'` in local state. The next board poll
or SSE event will reconcile. This requires `usePmoBoard` to expose a `mutateCard`
function or the board hook to accept an optimistic-update callback.
**Effort:** medium

---

### R2-09
**Category:** Robustness
**Priority:** HIGH
**Description:** `PlanEditor.addStep` generates new step IDs using
`${phase.phase_id + 1}.${maxStepNum + 1}`. If `phase.phase_id` is a non-integer
(e.g., the string `"phase-1"` or a UUID from a future backend change), the
arithmetic `phase.phase_id + 1` would produce `"phase-11"` (string concatenation)
rather than a numeric ID. The `ForgePlanPhase` type declares `phase_id: number`
but the type system only guarantees this at compile time; runtime data from the
API could differ.
**Recommendation:** Use `pi` (the array index, which is always a number) instead
of `phase.phase_id` for the step ID prefix, or validate/coerce `phase_id` to a
number explicitly before arithmetic. Also add a runtime check in `addStep`.
**Effort:** small

---

### R2-10
**Category:** Testing
**Priority:** HIGH
**Description:** The "Approve & Queue" double-submit race (R2-05) has no test
coverage. The `handleApprove` double-click path is untested. Similarly, the
`addStep` step-ID generation with non-numeric `phase_id` (R2-09) has no test.
The `useHotkeys` re-registration on every render (R2-01) has no unit test.
**Recommendation:** Add tests: (1) a functionality-break test that simulates two
rapid clicks on "Approve & Queue" and asserts only one API call is made,
(2) a unit test for the `addStep` step-ID collision edge case,
(3) a test that verifies the hotkey bindings are stable across renders.
**Effort:** medium

---

### R2-11
**Category:** Robustness
**Priority:** HIGH
**Description:** `usePersistedState` persists to `sessionStorage` but `PlanEditor`
saves drafts to `localStorage` (via `localStorage.setItem('pmo:plan-draft', ...)`).
The draft restore banner in `ForgePanel` also reads from `localStorage`. These two
storage systems are inconsistent. Session-level state (active view, filter, forge
form fields) uses `sessionStorage` as intended, but the plan draft — which is
explicitly described as persisting "from a previous session" — correctly uses
`localStorage`. However the `pmo:forge-description` key (which feeds the intake
form) uses `sessionStorage` via `usePersistedState`, so closing and reopening the
browser loses the description even though the plan draft itself survives.
**Recommendation:** Decide on a consistent persistence strategy. If description
should survive browser restarts (to match draft behavior), switch
`pmo:forge-description` to `localStorage`. If not, document the asymmetry. At
minimum the inconsistency should be commented so future maintainers don't
accidentally mix strategies.
**Effort:** small

---

### R2-12
**Category:** Testing
**Priority:** HIGH
**Description:** The SSE + polling overlap scenario (R2-07) — concurrent board
fetches from both SSE event and active poll timer — has no test. There is also no
test covering the SSE reconnect back-off (that `backoffRef` doubles up to 30s),
nor any test verifying the `ConnectionIndicator` correctly cycles through
`connecting → sse → polling` states.
**Recommendation:** Add unit tests for `usePmoBoard`: (1) verify that when SSE
fires `card_update` and the poll timer fires simultaneously, `fetchBoard` is
called at most twice but the final state is the latest response,
(2) verify back-off caps at 30s, (3) verify `connectionMode` state transitions.
These can be isolated with `vi.useFakeTimers()` in a Vitest unit test context.
**Effort:** medium

---

## MEDIUM Priority

---

### R2-13
**Category:** Feature
**Priority:** MEDIUM
**Description:** The Kanban board has no text search. With 50+ cards (tested in
`functionality-breaks.spec.ts` but not user-facing), there is no way to find a
specific card by title, project, or ADO ID beyond the program filter. The program
filter chips are the only filtering mechanism.
**Recommendation:** Add a search input to the toolbar, filtering `cards` on
`title`, `project_id`, `external_id`, and `current_phase`. A debounced client-side
filter would be immediate. The filter text could be persisted in `sessionStorage`
alongside the program filter.
**Effort:** medium

---

### R2-14
**Category:** Feature
**Priority:** MEDIUM
**Description:** Cards within a column have no sort order control. They render in
the order returned by the API (insertion order). There is no way to sort by
priority, updated time, risk level, or steps remaining. High-priority cards may
be buried below low-priority ones.
**Recommendation:** Add a sort dropdown to the toolbar (`Priority`, `Last updated`,
`Risk`, `Progress`). Default to `Priority desc` so P0 cards always float to the
top. The sort key could be persisted in `sessionStorage`.
**Effort:** medium

---

### R2-15
**Category:** Feature
**Priority:** MEDIUM
**Description:** There is no keyboard shortcut help panel. The `useHotkeys` hook
binds three keys (`n`, `s`, `escape`) but the hint in the navbar (`n=new s=signals
esc=board`) is a static text element at 9px font size — effectively invisible to
most users. There is no `?` shortcut to open a help overlay listing all bindings.
**Recommendation:** Add a keyboard shortcut reference. A minimal implementation:
bind `?` to open a modal overlay listing all shortcuts in a table. More
comprehensively, this could be a `<dialog>` element with a `?` button in the
navbar. The hint text in the navbar should at minimum reference `?` to open help.
**Effort:** medium

---

### R2-16
**Category:** Polish
**Priority:** MEDIUM
**Description:** The `HealthBar` and `KanbanCard.programDotColor` functions both
define the same 8-color palette (`DOT_PALETTE` in `KanbanCard.tsx` and
`PROGRAM_PALETTE` in `HealthBar.tsx`) with identical values and the same hash
function. This is duplicated code that must be kept in sync manually.
**Recommendation:** Extract the palette + hash function to `styles/tokens.ts`
as `programColor(program: string): string`, and import it in both `KanbanCard.tsx`
and `HealthBar.tsx`. The `tokens.ts` file already exports design tokens and is the
natural home.
**Effort:** small

---

### R2-17
**Category:** Architecture
**Priority:** MEDIUM
**Description:** `KanbanCard` has four independent loading/state flags
(`planLoading`, `planData`, `execLoading`, `execResult`) plus the `expanded` and
`showPlan` booleans — six pieces of local state for what are effectively two
distinct sub-behaviors (execute action and view-plan action). Each sub-behavior
has its own loading, result, and error state but they are all flat in one
component without cleanup on collapse.
**Recommendation:** Extract `usePlanPreview(cardId)` and `useExecuteCard(cardId)`
as local hooks inside `KanbanCard.tsx`. This keeps the component JSX readable
and the state logic isolated. Both hooks should also abort/clean up on unmount.
**Effort:** medium

---

### R2-18
**Category:** Polish
**Priority:** MEDIUM
**Description:** The `FONT_SIZES` token object in `tokens.ts` defines a scale
(`xs: 9px` through `xl: 16px`) but it is never actually imported or used anywhere
in the codebase — all font sizes are hard-coded inline (e.g., `fontSize: 9`,
`fontSize: 12`, `fontSize: 11`). The token system is therefore partially
decorative; the enforcement it implies does not exist.
**Recommendation:** Either use `FONT_SIZES.xs` etc. in at least the most common
components, or remove the unused export to avoid misleading future developers into
thinking the size scale is enforced. If adopting it, a global search-and-replace
of the most frequent literal sizes (`9`, `10`, `11`, `12`) would be the fastest
path.
**Effort:** medium

---

### R2-19
**Category:** Feature
**Priority:** MEDIUM
**Description:** The `PlanEditor` has no "Add Phase" button. A user can add
steps within an existing phase and remove phases, but cannot add a new phase to
the plan. If the generated plan is missing a phase the user wants, they must
trigger a full regeneration rather than manually adding a phase inline.
**Recommendation:** Add an "+ Add Phase" button below the phase list that appends
a new phase with a default name and an empty step list. This is a symmetric
operation to "Remove Phase" which already exists.
**Effort:** small

---

### R2-20
**Category:** Robustness
**Priority:** MEDIUM
**Description:** `AdoCombobox` uses the string literal `'ado-results-listbox'` as
a hardcoded `id` for the dropdown `<ul>`. If `AdoCombobox` were ever rendered
twice on the same page (e.g., in a future multi-project comparison view), both
instances would share the same `id`, violating HTML uniqueness requirements and
breaking ARIA `aria-controls` pointing.
**Recommendation:** Generate a stable unique ID using `useId()` (React 18+) or
a module-level incrementing counter. Replace the hardcoded `'ado-results-listbox'`
with the generated ID.
**Effort:** small

---

### R2-21
**Category:** Architecture
**Priority:** MEDIUM
**Description:** `ForgePanel` stores `projectId` in plain `useState` (not
`usePersistedState`), meaning a user who navigates away from the Forge and back
loses their project selection. All other intake fields (`description`, `taskType`,
`priority`) are persisted. The inconsistency means that on returning to Forge
the user sees their description and priority but must re-select the project.
**Recommendation:** Change `projectId` state initialization to use
`usePersistedState('pmo:forge-project-id', '')` so it is preserved across
navigation round-trips consistently with the other intake fields.
**Effort:** small

---

### R2-22
**Category:** Testing
**Priority:** MEDIUM
**Description:** The `InterviewPanel` component has no dedicated tests in the
existing 831-test suite. The choice-type radio buttons (`role="radio"` on
`<button>`) are visually functional but the ARIA pattern requires them to be
grouped in a `role="radiogroup"` container. There is a `fieldset`/`legend`
wrapper that partially addresses this, but the individual buttons use
`role="radio"` without being children of a `role="radiogroup"`. This is an ARIA
pattern violation not caught by the existing accessibility audit (which focuses
on the kanban view and intake form).
**Recommendation:** Add accessibility tests for `InterviewPanel`: (1) verify
choice buttons have correct ARIA state, (2) verify the submit button count
updates as answers are selected, (3) verify the "Back to Plan" button works from
the regenerating phase. Also fix the `role="radio"` grouping by either using
actual `<input type="radio">` elements or adding `role="radiogroup"` to the
fieldset equivalent.
**Effort:** medium

---

### R2-23
**Category:** Testing
**Priority:** MEDIUM
**Description:** The `SavedPhase` sub-component in `ForgePanel` (the post-approve
state showing checkmark, file path, and "Start Execution" button) is not
explicitly tested in any spec file. The journey-exploration and ux-audit specs
cover the intake and preview phases but stop at "Approve & Queue". The saved
state, the execution launch from saved state, and the "New Plan" reset path are
untested.
**Recommendation:** Add a `forge-saved-phase.spec.ts` (or extend
`journey-exploration.spec.ts`) that: (1) navigates through intake → generate →
approve → saved, (2) verifies the checkmark and file path are visible,
(3) clicks "Start Execution" and verifies the PID message, (4) clicks "New Plan"
and verifies the form resets.
**Effort:** medium

---

### R2-24
**Category:** Polish
**Priority:** MEDIUM
**Description:** The column empty-state placeholder shows only the word "Empty"
in 7px italic gray text. For new users this provides no guidance: it does not
explain how cards arrive in that column or what action to take. The "Queued"
column empty state is especially confusing — a user who creates a plan may
not immediately know it should appear there.
**Recommendation:** Replace generic "Empty" with column-specific guidance:
- Queued: "No plans ready to execute. Create one in The Forge."
- Executing: "No active executions."
- Awaiting Human: "No decisions required."
- Validating: "No plans under validation."
- Deployed: "No completed plans yet."
Each could include a micro-action link (e.g., the Queued state links to "+ New Plan").
**Effort:** medium

---

### R2-25
**Category:** Robustness
**Priority:** MEDIUM
**Description:** `api.client.ts` uses a single generic `request<T>` function that
reads the full response body as text on error (`res.text()`). For large error
responses (e.g., a 500 that returns an HTML error page from a reverse proxy) this
reads the entire response body into memory. There is also no request timeout —
a hanging API call will wait indefinitely, blocking the UI state indefinitely.
**Recommendation:** Add an `AbortSignal` with a configurable timeout (e.g., 30s)
to all requests via `AbortController`. Truncate error text to a reasonable limit
(e.g., 500 chars) before surfacing to UI. Both are one-line changes in the
`request()` helper.
**Effort:** small

---

### R2-26
**Category:** Testing
**Priority:** MEDIUM
**Description:** The `AdoCombobox` keyboard navigation (ArrowUp/ArrowDown/Enter/
Escape) is tested in `ux-audit.spec.ts` but only for the happy path. Edge cases
not covered: (1) selecting an item that has no `description` field (the `onSelect`
callback receives `item.description || item.title`; if description is `null` or
`undefined` rather than an empty string, the `||` fallback works, but this is
not tested), (2) rapid query changes while a debounced search is in flight
(the previous timer is cancelled by the `clearTimeout` but the previous fetch
result could still arrive and overwrite newer state if `setItems` fires after
a subsequent response), (3) an empty result set — the dropdown closes but there
is no "No results" message.
**Recommendation:** Add tests for: empty ADO results (show "No results found"
message), rapid re-query during in-flight search, and ADO item with null
description.
**Effort:** medium

---

### R2-27
**Category:** Robustness
**Priority:** MEDIUM
**Description:** `ForgePanel` uses `window.confirm()` for the dirty-state
navigation guard ("You have an unsaved plan. Leave anyway?"). Browser-native
`confirm()` is blocked in embedded iframes, has inconsistent styling across
browsers, cannot be tested with Playwright without special setup, and violates
the app's otherwise React-controlled UI pattern. The same issue applies to
`SignalsBar.handleBatchResolve` which also uses `window.confirm()`.
**Recommendation:** Replace both `window.confirm()` calls with a lightweight
in-app confirmation component — a small modal or inline confirmation row with
"Confirm" / "Cancel" buttons. This is testable and consistent with the design
system.
**Effort:** medium

---

### R2-28
**Category:** Feature
**Priority:** MEDIUM
**Description:** The Signals panel has no way to view resolved signals. The
`SignalsBar` component filters to `open` signals only (line 164: `const open =
signals.filter(s => s.status !== 'resolved')`). Once a signal is resolved it
disappears. There is no "show resolved" toggle, no signal history, and no way to
audit past resolutions.
**Recommendation:** Add a "Show resolved" toggle button next to the "Add Signal"
button. When active, append resolved signals below the open list with muted
styling (strikethrough or reduced opacity) and without the Forge/Resolve action
buttons.
**Effort:** medium

---

### R2-29
**Category:** Architecture
**Priority:** MEDIUM
**Description:** `PlanPreview.tsx` and `KanbanCard.tsx` (the `InlinePlanView`
function) both render a nearly identical view of a `ForgePlanResponse`: phase
header with numbered badge, step list with agent chip. The `InlinePlanView`
function inside `KanbanCard.tsx` is a private, non-exported function that
duplicates the rendering logic from `PlanPreview.tsx`. The two components will
drift over time.
**Recommendation:** Export `PlanPreview` as the canonical read-only plan renderer
and use it inside `KanbanCard`'s expanded plan section instead of the
`InlinePlanView` copy. The only difference (`InlinePlanView` supports expand/
collapse per phase) could be added as an optional `collapsible` prop to
`PlanPreview`.
**Effort:** medium

---

### R2-30
**Category:** Feature
**Priority:** MEDIUM
**Description:** The Forge intake form has no character count or length guidance
for the task description textarea. The description is used to prompt an LLM; very
short descriptions (< 30 chars) produce poor plans, and very long ones (> 4000
chars) may be truncated by the API. There is no visual cue about these limits.
**Recommendation:** Add a live character count below the textarea
(`{description.length} / 4000`), color-coding the count yellow at 3000+ and red
at 4000+. Add a minimum-length hint: if the user tries to generate with < 20
characters, show an inline warning rather than submitting.
**Effort:** small

---

### R2-31
**Category:** Testing
**Priority:** MEDIUM
**Description:** There are no tests for the `HealthBar` component's interactive
behavior (clicking a program tile to filter the board, clicking again to
deselect). The `HealthBar` component acts as a secondary filter that duplicates
the toolbar filter chips, but their synchronization is not tested. If both are
used simultaneously (click ALPHA in health bar, then click ALPHA chip in toolbar)
the filter state is toggled via `handleProgramClick` in `KanbanBoard` — this
double-filter path is untested.
**Recommendation:** Add tests in `functionality-breaks.spec.ts` or a new
`health-bar.spec.ts`: (1) click a HealthBar program tile and verify cards filter,
(2) click again and verify "all" is restored, (3) use both HealthBar and toolbar
filter for the same program to verify idempotent filter toggle.
**Effort:** medium

---

## LOW Priority

---

### R2-32
**Category:** Polish
**Priority:** LOW
**Description:** The `PlanEditor` "Save Draft" button shows a brief "Saved ✓"
label for 2 seconds then reverts to "Save Draft". The visual feedback (the
dirty-state orange dot) disappears on save but the "Plan Ready" header above does
not acknowledge the save. A user who is away from the keyboard for 2 seconds
misses all feedback.
**Recommendation:** In addition to the 2-second label, add a subtle timestamp
("Draft saved at 14:23") that persists below the stats bar until the next save
or until the draft is cleared.
**Effort:** small

---

### R2-33
**Category:** Polish
**Priority:** LOW
**Description:** The Kanban board toolbar's separator (`<div style={{ width: 1,
height: 14, background: T.border }} />`) is a presentational `<div>` not marked
with `role="separator"`. There are two such separators in the toolbar — between
program filters and signals button, and between the keyboard hint and version
label in the navbar. These are cosmetic but fail strict ARIA audits.
**Recommendation:** Replace with `<hr aria-hidden="true">` styled to match, or
add `role="separator"` and `aria-orientation="vertical"` to the existing div.
**Effort:** small

---

### R2-34
**Category:** Polish
**Priority:** LOW
**Description:** `ForgePanel` shows the signal ID badge ("from signal: sig-abc-123")
in the header when the Forge is opened from a signal. The badge shows the raw
`signal_id` string, which is an internal UUID-style identifier. Users know the
signal by its title, not its ID.
**Recommendation:** Change the badge to show `from signal: {initialSignal.title}`
(truncated to ~40 chars with a title attribute containing the full title and ID).
**Effort:** small

---

### R2-35
**Category:** Architecture
**Priority:** LOW
**Description:** `PlanEditor`'s `originalPlanRef` tracks the snapshot for dirty
detection (`isDirty = JSON.stringify(plan) !== originalPlanRef.current`). For
large plans (20 phases × 30 steps), `JSON.stringify` is called on every render.
This is O(plan-size) on every re-render, including the rapid micro-renders during
drag-and-drop (`handleDragOver` fires on every pixel of movement).
**Recommendation:** Move the dirty check into a `useMemo` with `plan` as a
dependency, or cache the stringified current plan in a `useRef` updated by a
`useEffect` and compare lazily (only when needed, e.g., on "Save Draft" click).
For typical plan sizes this is negligible but will become noticeable at 20+
phase plans.
**Effort:** small

---

### R2-36
**Category:** Polish
**Priority:** LOW
**Description:** The `KanbanCard` renders step progress as pips (squares) for up
to the full `steps_total`. A card with 50 steps renders 50 individual `<div>`
elements in a flex row. The `functionality-breaks.spec.ts` creates a 50-step card
for testing, and the pips will overflow their container without wrapping, because
the pip container uses `display: flex` with no `flex-wrap`. The number label
(`steps_completed / steps_total`) is always present, so the pips could be
conditionally hidden above a threshold.
**Recommendation:** Hide the pip row when `steps_total > 12` and show only the
numeric `N/M` label, or add `flex-wrap: wrap` and limit pip display to the first
20 with a "+N more" indicator.
**Effort:** small

---

### R2-37
**Category:** Feature
**Priority:** LOW
**Description:** There is no dark/light theme toggle. The app is dark-only. The
`tokens.ts` color system is structured with semantic names (`bg0`–`bg4`, `text0`–
`text4`) which would make a light theme straightforward to add by swapping the
token values at runtime. No user preference is respected.
**Recommendation:** Add a theme toggle button in the navbar. Implement a
`LightTheme` token set and wrap the app in a context that swaps token values.
Persist the preference in `localStorage`. Respect `prefers-color-scheme: light`
as the default for first-time users.
**Effort:** large

---

### R2-38
**Category:** Feature
**Priority:** LOW
**Description:** The Kanban board has no bulk card actions. Users cannot select
multiple cards to execute in batch or move multiple cards between columns. The
Signals panel has batch resolve (already implemented) but the Kanban board
lacks the equivalent.
**Recommendation:** Add a checkbox to each `KanbanCard` (visible on hover/focus
or as a persistent tiny checkbox). When one or more are checked, show a bulk
action toolbar: "Execute selected (N)", "Archive selected". This is a larger
feature but the checkbox infrastructure is established in `SignalsBar` and
can be reused.
**Effort:** large

---

### R2-39
**Category:** Robustness
**Priority:** LOW
**Description:** `localStorage` draft cleanup has a potential stale-draft risk.
If the user generates a plan, saves a draft, then changes projects and generates
a different plan, the draft banner will appear offering to restore the first
plan (which belonged to a different project). The draft is not keyed by project or
task ID.
**Recommendation:** Key the draft by `project_id` + `task_type` (or just use the
`task_id` as the storage key), or store the `project_id` alongside the plan in
the draft JSON and validate it matches the current selection before showing the
restore banner.
**Effort:** small

---

### R2-40
**Category:** Testing
**Priority:** LOW
**Description:** The `plan-draft.spec.ts` file tests draft save and restore via
`localStorage` but does not test: (1) the stale-draft scenario (draft from a
different project is offered for restore), (2) corrupt JSON in `localStorage`
(`JSON.parse` throws, caught silently), (3) `localStorage` unavailable/full
(the `try/catch` around `localStorage.setItem` is exercised silently but never
verified in a test).
**Recommendation:** Extend `plan-draft.spec.ts` with: a test that seeds corrupt
JSON in `localStorage` before loading the Forge and verifies the banner is NOT
shown (or shows gracefully), and a test with a different-project draft verifying
the behavior.
**Effort:** small

---

### R2-41
**Category:** Polish
**Priority:** LOW
**Description:** The `ConnectionIndicator` in `KanbanBoard.tsx` animates the dot
with `animation: 'pulse 1.5s infinite'` when connecting, but the `pulse`
keyframe is never defined in a `<style>` block or CSS file. The animation is
defined for the `awaitingHuman` dot in `KanbanBoard.tsx` the same way. The pulse
animation will only work if a global CSS file elsewhere defines the `pulse`
keyframe. If the pulse keyframe is not defined, the dots are static — there is
no visual connecting indicator.
**Recommendation:** Verify whether `pulse` is defined globally (in `index.html`
or a CSS file not reviewed here). If it is not defined, add it via a `<style>`
tag injected in `App.tsx` or via a CSS-in-JS approach, or use a CSS class defined
in a proper `.css` file.
**Effort:** small

---

### R2-42
**Category:** Testing
**Priority:** LOW
**Description:** Mobile responsiveness is tested in `ux-audit.spec.ts` (mobile
Playwright project), but the tests only verify that critical elements are
present. No test verifies that the Kanban columns are horizontally scrollable at
375px width (the overflow container uses `overflow: auto`), that the HealthBar
tiles wrap or scroll, or that the Forge form does not overflow horizontally.
**Recommendation:** Add mobile-specific layout tests: (1) at 375px the kanban
column area is scrollable and the first column header is visible, (2) the Forge
intake form at 375px does not produce a horizontal scrollbar on `<body>`,
(3) the toolbar at 375px wraps without overflow.
**Effort:** medium

---

### R2-43
**Category:** Architecture
**Priority:** LOW
**Description:** `App.tsx` passes `onEditPlan={handleCardForge}` and
`onCardForge={handleCardForge}` as separate props (both pointing to the same
function). This is vestigial — the two props are used in `KanbanBoard.tsx` for
different visual purposes (Re-forge vs. Edit Plan buttons), but both invoke the
same handler. The duplicate prop adds cognitive overhead with no behavioral
difference.
**Recommendation:** Keep only `onCardForge` as the canonical callback and route
both the Re-forge and Edit Plan button actions through it, passing a second
parameter or action type if the Forge panel needs to know which button was
pressed. Or rename for clarity: `onForgeCard` (Re-forge) and `onEditPlan`
(Edit Plan — currently identical). At minimum, document the intent difference
in a comment.
**Effort:** small

---

### R2-44
**Category:** Testing
**Priority:** LOW
**Description:** The `useHotkeys` hook does not suppress bindings when a
`<select>` or `role="combobox"` element is focused, only `HTMLInputElement` and
`HTMLTextAreaElement`. In `ForgePanel`, when the Project selector `<select>` has
focus, pressing `n` would navigate to the Forge (no-op since already there) but
pressing `escape` would navigate back to the board — abandoning the intake form
while the user was interacting with a select element.
**Recommendation:** Extend the `useHotkeys` guard to also skip when
`e.target instanceof HTMLSelectElement`. Add a test: focus a select in the Forge
intake form, press `escape`, and verify the Forge is still visible (no navigation
occurred).
**Effort:** small

---

## Coverage Notes

The 831-test suite (across desktop/mobile Playwright projects) is comprehensive
for:
- Smoke / infrastructure validation
- WCAG accessibility scanning (axe-core) and manual keyboard tests
- UX audit of board rendering and interactive states
- Functionality-break tests for edge-case data (empty titles, long errors,
  50-card columns, 20-phase plans)
- Journey exploration for the Forge intake → generate → preview → approve flow
- Plan draft save/restore cycle
- Edit Plan shortcut and Forge back-navigation

Known intentional coverage gaps (not worth filling at this stage):
- No unit tests for React hooks (all coverage is integration/E2E via Playwright)
- No test for the SSE event stream itself (would require a live backend)
- No visual regression tests / snapshot comparisons

---

## Assumptions

1. The `pulse` CSS keyframe animation referenced in `KanbanBoard.tsx` is assumed
   to be defined somewhere not reviewed (possibly `index.html` or a global CSS
   file). If it is missing, R2-41 is a silent visual bug rather than a crash.

2. The `PlanResponse` type is imported in `api/client.ts` but never used in any
   component; assumed to be a legacy type retained for backward compatibility
   with an older board API route.

3. The `api.getHealth()` method exists in `client.ts` but is never called from
   any component (health is fetched as part of the board response). Assumed
   intentional but could indicate a future standalone health endpoint.

4. Type assertions (`as ForgePlanPhase`) in `mock-data.ts` for phases without a
   `gate` property indicate the `gate` field is optional in practice but typed
   as `ForgePlanGate | undefined` — assumed correct.
