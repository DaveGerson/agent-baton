# PMO UI — Interaction Analysis
**Reviewer:** Interaction Analyst Agent
**Date:** 2026-03-24
**Scope:** Kanban Board + The Forge — full click-count tracing, cognitive load,
friction identification, and information density audit
**Persona:** Busy engineering manager, no support staff, constant context-switching.
Speed is paramount.

---

## 1. Component Map & Data Flow

```
App.tsx
  state: view ('kanban' | 'forge'), forgeSignal (PmoSignal | null)
  │
  ├── KanbanBoard  [view === 'kanban']
  │     usePmoBoard() — polls /api/v1/pmo/board every 5 s
  │     ├── HealthBar        — top stripe, program completion %
  │     ├── Toolbar          — filter chips, signals toggle, + New Plan
  │     ├── SignalsBar        — collapsible panel, loads /signals on open
  │     └── KanbanCard[]     — click-to-expand, static (no further API calls)
  │
  └── ForgePanel  [view === 'forge']
        state: phase (intake | generating | preview | regenerating | saved)
        ├── [intake]       — description textarea, project/type/priority selects
        │     └── AdoCombobox — debounced search, /ado/search?q=
        ├── [generating]   — same intake UI, button disabled + "Generating..."
        ├── [preview]      — PlanEditor (inline edit) + Approve / Regenerate
        ├── [regenerating] — InterviewPanel — answers → /forge/regenerate
        └── [saved]        — confirmation screen + "New Plan" / "Back to Board"
```

---

## 2. Click Count Analysis

### Task Traces

#### Task 1 — Triage one bug signal into a queued plan (start: Kanban board)

```
Step 1   Click "Signals" toggle button in toolbar           (+1 = 1)
         → SignalsBar mounts, fetches /signals (async load)
Step 2   Click "Forge" button on the target signal row      (+1 = 2)
         → setShowSignals(false); App.openForge(signal)
         → ForgePanel mounts with initialSignal pre-filling description
Step 3   Verify/adjust project selector (auto-selects first project)  (0 or +1 = 2–3)
Step 4   Click "Generate Plan →"                            (+1 = 3–4)
         → API call to /forge/plan — full round-trip wait
Step 5   Review plan in PlanEditor                          (read)
Step 6   Click "Approve & Queue"                            (+1 = 4–5)
         → /forge/approve — saved state shown
Step 7   Click "Back to Board"                              (+1 = 5–6)
```

**Minimum clicks: 5** (if project auto-selected correctly)
**Typical clicks: 6–7** (project correction + possible phase expand to read plan)

---

#### Task 2 — Forge a new feature plan (start: Forge tab, empty state)

```
Step 1   Click "The Forge" tab in nav                       (+1 = 1)
Step 2   Wait for projects to load (async, no user click)
Step 3   Type description in textarea                       (keyboard, not click)
Step 4   Click project selector if default wrong            (0 or +1)
Step 5   Click task type selector → select "New Feature"    (+1 = 2–3)
Step 6   Click priority selector if P1 not desired          (0 or +1)
Step 7   Click "Generate Plan →"                            (+1 = 3–5)
Step 8   Expand phase(s) in PlanEditor to review            (+N = 4–5+N)
         Note: Phase 0 is auto-expanded (expandedPhase initializes to 0)
         Each additional phase requires 1 click to expand
Step 9   Click "Approve & Queue"                            (+1)
Step 10  Click "Back to Board"                              (+1)
```

**Minimum clicks: 5** (2 selectors skipped, 1 phase already open)
**Typical clicks: 7–9** (selectors + expanding 2–3 phases to read them)

---

#### Task 3 — Check portfolio health (start: Kanban board)

```
Step 1   None — HealthBar renders at the top of KanbanBoard by default
```

**Minimum clicks: 0** — health is always visible on the board.
**Gap from ideal: 0** — this is the strongest part of the current design.

---

#### Task 4 — Expand a card and view its details (start: board view)

```
Step 1   Click anywhere on the card body                    (+1 = 1)
         → expanded = true; shows program, gates_passed, agent chips
```

**Minimum clicks: 1** — the whole card is a click target. This is correct.
**Limitation:** The expanded section only adds program, gates_passed, and agent list.
Step count, current phase, error, and priority are already visible collapsed.
There is nothing in the expanded section that cannot be gleaned from the collapsed card,
making the expand feel unrewarding — covered under F-IA-09.

---

#### Task 5 — Edit a step in a plan during Forge preview

```
Step 1   (Assumed already in preview phase with PlanEditor visible)
         Phase 0 is auto-expanded. For phase 0 steps:
Step 1   Click step text to enter edit mode                 (+1 = 1)
         → editingStep set, input autoFocused
Step 2   Type replacement text                              (keyboard)
Step 3   Press Enter or click away to commit                (+1 = 2)

         For a step in phases 1+:
Step 1   Click phase header to expand                       (+1 = 1)
Step 2   Click step text                                    (+1 = 2)
Step 3   Type and commit                                    (+1 = 3)
```

**Minimum clicks for phase-0 step: 2** (click + commit)
**Minimum clicks for other phases: 3** (expand + click + commit)

**Critical gap:** There is no way to edit the step's agent_name via the UI.
The agent tag is rendered as a static `<span>`. Changing agent assignment requires
regeneration or no mechanism at all — covered under F-IA-04.

---

#### Task 6 — Answer interview questions and re-generate (start: Forge preview)

```
Step 1   Click "Regenerate" button in preview header        (+1 = 1)
         → /forge/interview API call; InterviewPanel renders
         For each "choice" question: 1 click per answered question (+N)
         For "text" questions: keyboard input, 0 clicks
Step 2   Click "Re-generate with N answers" button          (+1 = 2+N)
         → /forge/regenerate; returns to preview phase
```

**Minimum clicks: 3** (Regenerate + 1 choice answer + Submit)
**With 3 questions (2 choice + 1 text): 4 clicks**
**Penalty:** If you answer 0 questions, the Submit button is disabled (answeredCount === 0 guard). You cannot re-generate with defaults alone without typing something — covered under F-IA-06.

---

#### Task 7 — Switch between Kanban and Forge (round trip)

```
Kanban → Forge:
Step 1   Click "The Forge" nav tab                          (+1 = 1)

Forge → Kanban:
Step 2   Click "← Board" button in ForgePanel header        (+1 = 2)
         OR click "AI Kanban" nav tab                        (+1 = 2)
```

**Minimum clicks: 2 total for round trip.**
**Gap:** Clicking "The Forge" tab always opens Forge in fresh intake state
(openForge() with no signal), discarding any in-progress work without warning
— covered under F-IA-10.

---

#### Task 8 — Filter board by program (start: unfiltered)

```
Step 1   Click a program filter chip in toolbar             (+1 = 1)
```

**Minimum clicks: 1** — immediate, no confirmation.
**Gap:** No multi-select (can only filter one program at a time) — covered under F-IA-11.

---

### Summary Click Count Table

| Task | Min Clicks (actual) | Ideal Clicks | Gap | Notes |
|------|--------------------:|-------------:|----:|-------|
| 1. Triage bug signal → queued plan | 5 | 3 | 2 | Signals panel not persistent; project always requires verification |
| 2. Forge new feature plan | 5 | 4 | 1 | Phase expansion cost; type/priority selectors add clicks |
| 3. Check portfolio health | 0 | 0 | 0 | Perfect — always visible |
| 4. Expand card | 1 | 1 | 0 | Correct but expanded content is thin |
| 5. Edit a step (phase 0) | 2 | 2 | 0 | Inline edit works well for phase 0 |
| 5. Edit a step (phase 1+) | 3 | 2 | 1 | Extra click to expand phase |
| 6. Interview + re-generate | 3–4 | 2 | 1–2 | Can't submit with zero answers; no "use defaults" path |
| 7. Kanban ↔ Forge round trip | 2 | 2 | 0 | But nav discards in-progress Forge work |
| 8. Filter by program | 1 | 1 | 0 | No multi-select |

---

## 3. Findings

### Finding F-IA-01: No One-Shot Plan Trigger From the Board
- **Severity:** CRITICAL
- **Workflow:** Triage / One-Shot
- **Component(s):** `KanbanBoard.tsx:117-131` (+ New Plan), `SignalsBar.tsx:202-215` (Forge button)
- **Description:** There is no path from a Kanban card to a forge action. The
  "Signals" path works only for signals. The "+ New Plan" button opens Forge with
  a blank intake form. For a busy manager triaging bugs visible on the board (not yet
  in the signals panel), there is no way to right-click or context-activate a plan
  generation from a card already on the board.
- **Evidence:** `KanbanCard.tsx` has no action buttons, no "Create follow-up plan" affordance.
  `KanbanBoard.tsx` passes only `onNewPlan` (blank) and `onSignalToForge` to children.
  `KanbanCard` receives only `card` and `columnColor` — no `onForge` callback.
- **Impact:** Manager must manually copy card title/description, switch to Forge, paste.
  Estimated additional time: 45–90 seconds per triage. At 10 bugs/day this is 15 minutes
  of pure context-switching cost.
- **Recommendation:** Add a "Forge Plan" action button to the expanded card view in
  `KanbanCard.tsx`. Add an `onForge?: (card: PmoCard) => void` prop. In `KanbanBoard.tsx`
  pass `onSignalToForge` adapted to accept `PmoCard`. In `App.tsx` map `PmoCard` to a
  minimal `PmoSignal`-compatible shape before calling `openForge`.

---

### Finding F-IA-02: Signals Panel is Hidden by Default — Adds 1 Required Click to Every Triage
- **Severity:** HIGH
- **Workflow:** Triage
- **Component(s):** `KanbanBoard.tsx:66-80` (Signals toggle), `KanbanBoard.tsx:134-142`
- **Description:** The Signals panel is collapsed by default (`showSignals = false`).
  Every single triage workflow starts with clicking "Signals" to reveal the panel.
  The panel then fires a fresh API call (`getSignals`) on every open because it mounts
  on demand. This means: 1 extra click + 1 network round-trip overhead before the manager
  can see any signals.
- **Evidence:** `KanbanBoard.tsx:17` — `const [showSignals, setShowSignals] = useState(false)`.
  `SignalsBar.tsx:23-28` — `useEffect` fires `api.getSignals()` on mount.
- **Impact:** Every triage starts with a click + async load. With constant context
  switching, this is a repeated interruption. For critical-severity signals that need
  immediate attention, the hidden state is dangerous — they are invisible on landing.
- **Recommendation:** Show the count of open critical/high signals in the toolbar always
  (badge on the Signals button) so the manager can assess urgency without opening the
  panel. Pre-fetch signals in the background using the 5 s poll alongside board data
  (add to `usePmoBoard`). For managers with critical signals open, default `showSignals`
  to `true` based on count. Files: `usePmoBoard.ts`, `KanbanBoard.tsx`, `SignalsBar.tsx`.

---

### Finding F-IA-03: No Inline "Approve & Queue" Path From Signal — Full Forge Roundtrip Required Even For Simple Bugs
- **Severity:** HIGH
- **Workflow:** Triage / One-Shot
- **Component(s):** `SignalsBar.tsx:202-215`, `ForgePanel.tsx:69-90`, `client.ts:71-75`
- **Description:** The `api.signalToForge()` method exists in `client.ts` (line 71) and
  calls `POST /signals/{id}/forge`. This is a one-shot "generate a plan and queue it from
  a signal" endpoint. However, **this endpoint is never called from the UI**. The "Forge"
  button in SignalsBar navigates to the full multi-step Forge panel instead. A simple P1
  bug fix that is well-described in the signal should take 2 clicks (Forge + Approve).
  Currently it takes 5–6 clicks plus a full page transition.
- **Evidence:** `client.ts:71-75` — `signalToForge` is defined but not imported anywhere
  in component code. `SignalsBar.tsx:202-215` — `onClick={() => onForge(sig)}`
  triggers `App.openForge(signal)` which opens the full Forge panel, not the one-shot path.
- **Impact:** Every signal triage forces the full generate → review → approve cycle.
  For well-specified bugs this is 3 unnecessary steps and a view transition.
- **Recommendation:** Add a "Quick Queue" (one-shot) button alongside "Forge" in
  `SignalsBar.tsx` that calls `api.signalToForge(sig.signal_id, projectId)` directly.
  The project ID can be inferred from the first registered project (same heuristic as
  Forge's auto-select). Show a toast/inline confirmation on success. Preserve "Forge"
  for complex signals that need full plan authoring. Files: `SignalsBar.tsx`,
  `KanbanBoard.tsx` (for project context prop).

---

### Finding F-IA-04: Agent Assignment Not Editable in PlanEditor
- **Severity:** HIGH
- **Workflow:** Forge-Author
- **Component(s):** `PlanEditor.tsx:188-196`
- **Description:** Each step in the PlanEditor shows the `agent_name` as a static cyan
  chip. There is no affordance to change the assigned agent. The click-to-edit inline
  input (`editingStep`) only covers `task_description`. An architect reviewing a
  generated plan who wants to swap `backend-engineer` for `python-engineer` on a specific
  step has no way to do so without regenerating.
- **Evidence:** `PlanEditor.tsx:188-196` — agent tag rendered as `<span>`, no
  `onClick`, no edit state for this field. `editingStep` state tracks only step ID for
  description editing (`PlanEditor.tsx:13`).
- **Impact:** Any agent assignment the LLM gets wrong requires a full regenerate cycle
  (3–4 additional clicks + LLM latency) rather than a 2-second inline correction.
- **Recommendation:** Make the agent chip a dropdown select when `editingStep === step.step_id`
  OR add a dedicated agent select rendered when the step row is in edit mode. A minimal
  fix is to render the agent tag as a `<select>` populated from a static list of known
  agents (the same roster in CLAUDE.md). Files: `PlanEditor.tsx`.

---

### Finding F-IA-05: No Unsaved-Work Guard When Switching Views
- **Severity:** HIGH
- **Workflow:** Cross-cutting / Forge-Author
- **Component(s):** `App.tsx:13-16`, `App.tsx:71-77`
- **Description:** Clicking the "AI Kanban" nav tab while in Forge calls `backToBoard()`
  which sets `view = 'kanban'` and `forgeSignal = null`. This unmounts `ForgePanel`
  entirely. Any description typed, plan generated, or edits made in `PlanEditor` are
  **silently discarded**. There is no confirmation dialog, no auto-save, no draft
  persistence.
- **Evidence:** `App.tsx:18-21` — `backToBoard` resets `forgeSignal` to null.
  `App.tsx:74-77` — nav tab `onClick` calls `backToBoard()` with no guard.
  `ForgePanel` state is all local (`useState`) — nothing is persisted to
  `localStorage` or URL params.
- **Impact:** An interrupted manager (Slack ping, Jira alert) naturally clicks
  Kanban to check the board, losing their entire in-progress plan. This is a data
  loss scenario.
- **Recommendation:** (1) Persist Forge intake form state to `localStorage` so it
  survives tab switches. (2) Add a `beforeUnload`-style guard: if `phase !== 'intake'`,
  show a simple inline confirmation before navigating away. The bar for this is low —
  a single `window.confirm` is acceptable for v1. Files: `App.tsx`, `ForgePanel.tsx`.

---

### Finding F-IA-06: Interview Submit Blocked When Zero Answers Given — Cannot "Use Defaults"
- **Severity:** MEDIUM
- **Workflow:** Forge-Author
- **Component(s):** `InterviewPanel.tsx:116-123`
- **Description:** The InterviewPanel disables the "Re-generate" button when
  `answeredCount === 0`. The helper text says "unanswered questions use sensible
  defaults," but you cannot actually submit with zero answers to trigger that behavior.
  If a user reads all the questions and decides the AI should use its defaults for all of
  them, there is no way to proceed — they must type at least one answer (even a space
  won't work: `filter(([, v]) => v.trim())`).
- **Evidence:** `InterviewPanel.tsx:116` — `disabled={loading || answeredCount === 0}`.
  `InterviewPanel.tsx:20-22` — `.filter(([, v]) => v.trim())` removes blank answers.
- **Impact:** User is forced to answer at least one question to proceed, contradicting
  the stated behavior of the helper text. This is a promise the UI makes and then
  breaks, generating confusion.
- **Recommendation:** Remove the `answeredCount === 0` disabled guard. An empty submit
  is valid and should trigger regeneration with defaults. Rename the button to
  "Re-generate (use defaults)" when `answeredCount === 0`. Files: `InterviewPanel.tsx`.

---

### Finding F-IA-07: Font Sizes Are Too Small for Rapid Scanning
- **Severity:** HIGH
- **Workflow:** Cross-cutting
- **Component(s):** All components — pervasive
- **Description:** The UI makes aggressive use of sub-10px font sizes for information
  that managers need to scan quickly under time pressure. Key examples:
  - Card title: 9px (`KanbanCard.tsx:97`) — primary information on the board
  - Card metadata (ID, priority chip, phase): 7–8px (`KanbanCard.tsx:110-125`)
  - Column headers: 9px label, 7px description (`KanbanBoard.tsx:184,198`)
  - HealthBar program name: 10px; stat line: 7px (`HealthBar.tsx:58,72`)
  - Forge FormField labels: 8px (`ForgePanel.tsx:337`)
  - Nav tabs: 9px (`App.tsx:81`)
  Sub-9px text is below the threshold for comfortable reading at normal viewing
  distances on 1080p displays. A manager glancing at the board from across the room
  or under stress will miss critical information.
- **Evidence:** Systematic search of font size values in all component files shows
  no primary text element above 12px. The body font in `index.css:15` sets DM Sans
  which is well-suited to small sizes, but sub-9px text is inaccessible per WCAG 2.1
  guidance on minimum text size (14px for body, 11px for supplementary).
- **Impact:** Increased time to read card state. Accessibility failure for users with
  any vision impairment. Eye fatigue during extended triage sessions.
- **Recommendation:** Establish a type scale floor: 11px minimum for any scannable
  content, 9px only for truly supplementary metadata (timestamps, IDs). Card titles
  should be 12–13px. Column headers should be 11px. Phase/status text inside the card
  should be 9px minimum. Update `tokens.ts` with named size constants to enforce this
  across the codebase. Files: `tokens.ts`, `KanbanCard.tsx`, `KanbanBoard.tsx`,
  `HealthBar.tsx`, `ForgePanel.tsx`.

---

### Finding F-IA-08: 5-Second Polling Causes "refreshing..." Flicker Without Progress Feedback
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight
- **Component(s):** `usePmoBoard.ts:14,48`, `KanbanBoard.tsx:114`
- **Description:** The board polls every 5 seconds (`POLL_INTERVAL_MS = 5000`).
  During each poll cycle, `setLoading(true)` is called... but looking at
  `fetchBoard`, the loading flag is only set to `false` in the `finally` block, and
  `setLoading(true)` is called in the `useEffect` before the initial fetch, not on
  each poll. The toolbar shows a "refreshing…" text indicator that appears
  momentarily during each poll. This creates a 5-second visual tick. For a stationary
  board (no changes), this creates unnecessary visual noise.
- **Evidence:** `usePmoBoard.ts:43-45` — `setLoading(true)` is inside the
  `useEffect` before the initial `fetchBoard()` call. On subsequent poll calls
  (the `setInterval`), `setLoading` is NOT called again because `fetchBoard` itself
  doesn't call `setLoading(true)` — only the `finally` calls `setLoading(false)`.
  So the loading flicker may be a non-issue for polls; however the `lastUpdated`
  timestamp visibly updates every 5 s, and the toolbar timestamp refresh is visible.
  The larger issue is that when data changes between polls, cards can reorder or
  appear/disappear with no transition, causing layout shift.
- **Impact:** Minor visual noise from timestamp update. Potential layout shift when
  cards move columns (e.g., queued → planning). No transition signals to the user
  which specific card changed.
- **Recommendation:** (1) Add a `useRef` to track previous card state and only
  animate cards whose `updated_at` or `column` changed (CSS transition on card
  border or background flash). (2) Replace the "refreshing…" label with a subtle
  dot indicator that pulses once per successful refresh rather than text. (3)
  Consider moving `lastUpdated` to a tooltip on the dot rather than the toolbar.
  Files: `usePmoBoard.ts`, `KanbanBoard.tsx`.

---

### Finding F-IA-09: Card Expansion Reveals Minimal New Information
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight
- **Component(s):** `KanbanCard.tsx:181-205`
- **Description:** The expanded card section shows only: program name, gates_passed
  count, and agent chips. All of these are either already visible in collapsed
  state (program via the dot, agents truncated in footer) or are low-value for
  triage decisions. Missing from expanded view:
  - Full `current_phase` text (truncated at 65 chars in collapsed state)
  - Full `error` text (truncated at 80 chars in collapsed state)
  - `created_at` (age of the plan)
  - Full `card_id` (truncated to monospace 7px in collapsed)
  - `steps_completed/steps_total` rationale (which specific steps are done)
  - A link/button to act (forge follow-up, escalate, mark resolved)
- **Evidence:** `KanbanCard.tsx:181-205` — expanded section content.
  `KanbanCard.tsx:143-161` — collapsed truncations.
- **Impact:** Managers expand cards hoping to get actionable detail, find nothing
  new, and lose the 1 click. The expand affordance trains users to distrust it.
- **Recommendation:** Expand section should show: (1) full untruncated
  `current_phase` and `error` text with word-wrap, (2) elapsed time since
  `created_at`, (3) full list of all agents, (4) action buttons relevant to the
  card's column (e.g., "Forge follow-up" for deployed cards, "View error" for
  errored cards). Files: `KanbanCard.tsx`.

---

### Finding F-IA-10: No Bulk Operations on Kanban Columns
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight / Triage
- **Component(s):** `KanbanBoard.tsx` — no bulk selection mechanism
- **Description:** There is no way to select multiple cards and perform a batch
  action. Common manager workflows that require bulk operations:
  - Triage 3 queued P0 bugs at once → forge 3 plans in sequence
  - Resolve all signals from a single source
  - Mark a group of "deployed" cards as reviewed
  The board has no checkboxes, no shift-click, no column-level actions.
- **Evidence:** `KanbanCard.tsx` — no selection state, no `onSelect` prop.
  `KanbanBoard.tsx` — no multi-select state in the component.
- **Impact:** Monday morning triage of 5 queued bugs takes 5 separate Forge
  roundtrips instead of 1 bulk queue operation.
- **Recommendation:** Add a hover-revealed checkbox to each card for multi-select.
  Show a floating action bar at the bottom of the board when items are selected:
  "Forge All Selected", "Resolve Signals", "Export". Files: `KanbanCard.tsx`,
  `KanbanBoard.tsx`, new `BulkActionBar.tsx`.

---

### Finding F-IA-11: Program Filter is Single-Select Only
- **Severity:** LOW
- **Workflow:** Kanban-Oversight
- **Component(s):** `KanbanBoard.tsx:16,21-23`
- **Description:** The `filter` state is a single string (`useState<string>('all')`).
  Clicking a second program chip replaces the first. A manager overseeing two related
  programs (e.g., "Platform" and "Infrastructure") cannot view both simultaneously.
- **Evidence:** `KanbanBoard.tsx:16` — `useState<string>('all')`.
  `KanbanBoard.tsx:21-23` — `filter === 'all' ? cards : cards.filter(c => c.program === filter)`.
- **Impact:** Cross-program triage requires two board views, doubling navigation cost.
- **Recommendation:** Change `filter` to `Set<string>`. FilterBtn toggles membership.
  Update the filter predicate accordingly. Files: `KanbanBoard.tsx`.

---

### Finding F-IA-12: No Keyboard Shortcuts Anywhere in the Application
- **Severity:** HIGH
- **Workflow:** Cross-cutting
- **Component(s):** All components — missing entirely
- **Description:** There are no keyboard shortcuts for any primary action in the UI.
  For a manager who lives in a keyboard-heavy workflow (terminal, IDE, Jira), the
  absence of shortcuts forces mouse usage for every board interaction. Critical paths
  with no keyboard alternative:
  - `N` — New Plan
  - `S` or `/` — Toggle Signals
  - `1`–`6` or `G F` — Switch to Forge / back to Kanban
  - `Esc` — Close expanded card, cancel Forge in-progress
  - `Enter` on Forge intake — submit Generate Plan (not connected; Enter in textarea
    inserts a newline; there is a `onKeyDown` Enter handler in `SignalsBar` add-form
    but not on the main Generate button)
- **Evidence:** Global search for `onKeyDown`, `useHotkeys`, `addEventListener('keydown')`
  in component files — only found in `SignalsBar.tsx:104` (add-signal form Enter key)
  and `PlanEditor.tsx:171` (step edit Enter to commit). No application-level shortcuts.
- **Impact:** Mouse-only workflow adds 200–500ms per primary action for keyboard-first
  users. Over a 50-action triage session this is 10–25 seconds of unnecessary mousing.
- **Recommendation:** Add a lightweight `useHotkeys` hook (or native `addEventListener`)
  in `App.tsx` for application-level shortcuts. Document shortcuts in a `?` overlay or
  tooltip. Priority shortcuts: `N` = New Plan, `S` = Signals, `Tab`/`Shift+Tab` =
  cycle board columns. Files: `App.tsx`, new `useHotkeys.ts`.

---

### Finding F-IA-13: ADO Import Is Labeled "placeholder" — Creates User Confusion
- **Severity:** LOW
- **Workflow:** Forge-Author
- **Component(s):** `ForgePanel.tsx:190-193`, `AdoCombobox.tsx:51`
- **Description:** The ADO import field in Forge intake is labeled
  "Import from ADO (placeholder)" and the input placeholder says
  "Search ADO work items (placeholder)...". The word "placeholder" appearing in a
  production UI label is a development artifact. A manager who sees this may attempt
  to use it seriously (since the component does fire API calls to `/ado/search`),
  only to get no results or an error.
- **Evidence:** `ForgePanel.tsx:190` — `<FormField label="Import from ADO (placeholder)">`.
  `AdoCombobox.tsx:51` — `placeholder="Search ADO work items (placeholder)..."`.
- **Impact:** Ambiguity about whether the feature works. If a manager types a bug ID
  and gets no results, they won't know if it's because the integration is disabled
  or their query is wrong.
- **Recommendation:** Either (1) hide the ADO field entirely when the integration is
  not configured, with a "Connect ADO" setup link, or (2) label it clearly as
  "Import from ADO (not configured)" with a muted style. Remove the word
  "placeholder" from user-visible strings. Files: `ForgePanel.tsx`, `AdoCombobox.tsx`.

---

### Finding F-IA-14: HealthBar Has No Drill-Down — Cannot Act on a Blocked Program
- **Severity:** MEDIUM
- **Workflow:** Kanban-Oversight
- **Component(s):** `HealthBar.tsx:44-84`
- **Description:** The HealthBar program tiles show `blocked` count in orange and
  `failed` count in red, but are completely non-interactive. Clicking a program tile
  does nothing. A manager who sees "2 blocked" on "Platform" has to manually locate
  blocked cards in the Kanban columns below — scrolling 6 columns looking for orange-
  bordered cards.
- **Evidence:** `HealthBar.tsx:44` — the program `<div>` has no `onClick`, no
  `cursor: pointer`, no `title` attribute indicating interactivity.
- **Impact:** The HealthBar raises a question ("Platform: 2 blocked") without
  providing a path to answer it. The manager must switch modes to investigate.
- **Recommendation:** Make each HealthBar tile clickable to filter the board to
  that program (same as clicking the program filter chip). Add `cursor: pointer`
  and a hover state. On click, call `setFilter(pg.program)` (requires lifting
  filter state or passing a callback to `HealthBar`). Files: `HealthBar.tsx`,
  `KanbanBoard.tsx`.

---

### Finding F-IA-15: Forge Preview and PlanEditor Are Separate Components But Only Editor Is Rendered
- **Severity:** LOW
- **Workflow:** Forge-Author
- **Component(s):** `PlanPreview.tsx` (entire file), `ForgePanel.tsx:265-293`
- **Description:** `PlanPreview.tsx` is a fully implemented read-only plan view
  component. `PlanEditor.tsx` is the editable version. In `ForgePanel.tsx`, the
  preview phase renders only `<PlanEditor>` — `PlanPreview` is never used anywhere
  in the current render tree (it is not imported in `ForgePanel.tsx`). This means
  there is dead code that carries maintenance cost, and the preview phase skips
  straight to an editable state (which is fine for efficiency) but the component
  naming is misleading. The ForgePanel `phaseLabel` calls phase "preview" but renders
  a full editor.
- **Evidence:** `ForgePanel.tsx:1-7` — imports do not include `PlanPreview`.
  `ForgePanel.tsx:265-291` — renders `<PlanEditor>` in preview phase.
  `PlanPreview.tsx` — complete standalone component, unused.
- **Impact:** Maintenance cost for dead code. Developer confusion about intended
  design. No direct user impact — the editor-as-preview is actually better UX than
  a separate read-only view.
- **Recommendation:** Either (1) delete `PlanPreview.tsx` and confirm the editor
  satisfies all preview needs, or (2) use `PlanPreview` for a quick-read mode and
  `PlanEditor` only when the user explicitly clicks "Edit". Option 1 reduces code
  debt; option 2 adds a useful read/edit distinction. Files: `PlanPreview.tsx`
  (candidate for deletion), `ForgePanel.tsx`.

---

### Finding F-IA-16: No Error Recovery Path in Forge — Generate Failure Drops User Back to Intake Silently
- **Severity:** MEDIUM
- **Workflow:** Forge-Author
- **Component(s):** `ForgePanel.tsx:85-89`, `ForgePanel.tsx:240-245`
- **Description:** When `handleGenerate()` fails (line 85-89), the error is stored
  in `generateError` state and the phase returns to `'intake'`. The error is displayed
  in a small red box above the Generate button. However: (1) the error message is raw
  API text (e.g., "API 500: Internal Server Error"), not user-readable, (2) there is
  no retry button distinct from clicking "Generate Plan →" again, (3) the user's
  description is preserved (good) but it is not obvious they are re-submitting after
  an error vs. a first submission.
- **Evidence:** `ForgePanel.tsx:85-89` — `setGenerateError(...); setPhase('intake')`.
  `ForgePanel.tsx:240-245` — error renders in the intake form.
- **Impact:** A manager who hits a backend error may not notice the small red text,
  especially on a dark theme with high information density. They might think generation
  is still in progress.
- **Recommendation:** (1) Map common API error codes to friendly messages ("Plan
  generation failed — please try again"). (2) Show the error more prominently with
  an explicit "Retry" button that re-calls `handleGenerate` without requiring the user
  to re-click the main CTA. Files: `ForgePanel.tsx`.

---

## 4. Cognitive Load Assessment

### Per-Task Decision Count

| Task | Decisions Required | Ideal | Notes |
|------|-----------------:|------:|-------|
| Triage bug → plan | 4 (open signals, pick signal, verify project, read plan, approve) | 2 | Signal panel hidden; project selection adds uncertainty |
| Forge new feature | 5 (type/priority/project/description/approve) | 3 | Type and priority are rarely changed; should default silently |
| Check health | 0 | 0 | Correct |
| Expand card | 0 | 0 | Correct |
| Edit step | 1 (click target text) | 1 | Discoverability low; cursor:text is subtle |
| Interview + regen | 2–5 (read questions + 1 per question) | 1–2 | Well-designed for a secondary workflow |
| Board filter | 1 (pick program) | 1 | Correct |

### Visibility Assessment

**What is visible on the board without any clicks:**
- Portfolio health per program: completion %, plan count, blocked/failed counts
- Card title (2-line clamp), priority badge (P0/P1 only), risk badge (medium/high only),
  step progress pips + n/m count, current phase snippet (65 chars), error snippet (80 chars),
  program dot color, agent list (2 names + overflow count), last updated time
- Column card counts
- "N awaiting" pulse indicator when cards need human input
- Total plan count, executing count

**What requires expansion (1 click):**
- Full program name (already in dot tooltip via `title` attribute)
- Gates passed count
- Full agent list (if > 2 agents)

**What is not visible at any level from the board (missing):**
- Signal severity breakdown (only count visible after toggling signals panel)
- Age of a plan (created_at not shown anywhere on card)
- Which specific step is blocked on "awaiting_human" cards (current_phase is truncated)
- Error detail beyond 80 chars
- Plan file path

### Status Indicator Quality

| Indicator | Clear? | Actionable? | Notes |
|-----------|--------|-------------|-------|
| Priority chips (P0/P1) | Yes — color coded red/orange | No direct action | Only P0/P1 shown; P2 cards show nothing |
| Risk chips (medium/high) | Yes — yellow/red | No direct action | Low risk not shown (correct) |
| Step pips | Partially — shows n/m but no ETA | No | Good for quick progress read |
| "Awaiting" pulse | Yes — orange animated dot | No — must find the card manually | Critical: high visibility but no navigation shortcut |
| HealthBar completion% | Yes | Via filter (hidden interaction) | |
| HealthBar blocked/failed | Yes | No click path | See F-IA-14 |
| Column color accents | Yes — consistent with token system | N/A | |

### 10-Second Board State Comprehension Test

A manager landing on the board can determine in under 10 seconds:
- Which programs are healthy vs. struggling (HealthBar)
- How many plans are at each stage (column count badges)
- Which plans are blocked waiting for human input (orange pulse in toolbar)
- Which high-priority plans are in progress (P0/P1 chips visible on cards)

A manager **cannot** determine in under 10 seconds:
- How many open signals exist (Signals panel must be toggled)
- Which specific plan is awaiting human input (must scan all columns for orange-bordered cards)
- Which programs have the most blocked items (must mentally aggregate from HealthBar blocked counts, then correlate with filter)

---

## 5. Friction Heatmap

| Component | Form Fill | Discovery | Visual Clarity | Data Loss Risk | Missing Action | Keyboard |
|-----------|:---------:|:---------:|:--------------:|:--------------:|:--------------:|:--------:|
| KanbanBoard toolbar | LOW | MED | LOW | — | MED | HIGH |
| SignalsBar | MED | HIGH | LOW | — | HIGH | MED |
| KanbanCard (collapsed) | — | LOW | MED | — | HIGH | HIGH |
| KanbanCard (expanded) | — | LOW | HIGH | — | HIGH | HIGH |
| HealthBar | — | HIGH | LOW | — | HIGH | HIGH |
| ForgePanel intake | MED | LOW | MED | HIGH | LOW | MED |
| PlanEditor | LOW | MED | LOW | HIGH | HIGH | MED |
| InterviewPanel | LOW | LOW | LOW | — | MED | LOW |
| AdoCombobox | HIGH | HIGH | MED | — | MED | LOW |

Severity scale: LOW = minor friction, MED = measurable time cost, HIGH = workflow blocker

---

## 6. Missing Capabilities

| Capability | Workflow | Priority | Notes |
|------------|----------|----------|-------|
| One-shot plan generation from a Kanban card | Triage | P0 | F-IA-01 |
| "Quick Queue" (one-shot) from a signal without full Forge | Triage | P0 | F-IA-03 |
| Persistent signal count badge in toolbar | Triage | P1 | F-IA-02 |
| Agent assignment editing in PlanEditor | Forge-Author | P1 | F-IA-04 |
| Unsaved work guard / draft persistence | Forge-Author | P1 | F-IA-05 |
| Keyboard shortcuts for primary actions | Cross-cutting | P1 | F-IA-12 |
| HealthBar tile click → board filter | Kanban-Oversight | P1 | F-IA-14 |
| Bulk card selection and batch actions | Triage | P2 | F-IA-10 |
| Multi-program filter | Kanban-Oversight | P2 | F-IA-11 |
| Plan age (created_at) visible on card | Kanban-Oversight | P2 | F-IA-09 |
| "Use defaults" path in InterviewPanel | Forge-Author | P2 | F-IA-06 |
| Font size floor at 11px for scannable content | Cross-cutting | P1 | F-IA-07 |
| Friendly error messages in Forge | Forge-Author | P2 | F-IA-16 |
| "Awaiting Human" quick-jump (click pulse → scroll to card) | Kanban-Oversight | P2 | — |
| Plan search / filter by title keyword | Kanban-Oversight | P3 | — |
| Column collapse (hide empty columns) | Kanban-Oversight | P3 | — |

---

## 7. Priority Remediation Order

### Phase 1 — Remove blockers for core triage workflow (P0)

1. **F-IA-01** — Add "Forge Plan" action to expanded KanbanCard
2. **F-IA-03** — Wire `api.signalToForge` to a "Quick Queue" button in SignalsBar

### Phase 2 — Reduce daily friction for power users (P1)

3. **F-IA-05** — Draft persistence + unsaved-work guard in ForgePanel
4. **F-IA-12** — Keyboard shortcuts (`N`, `S`, `Esc`, `Enter` on Generate)
5. **F-IA-07** — Raise font sizes to 11px minimum across the board
6. **F-IA-02** — Always-visible signal count badge; pre-fetch signals in background
7. **F-IA-04** — Make agent assignment editable in PlanEditor
8. **F-IA-14** — HealthBar tile → board filter click-through

### Phase 3 — Refinement and completeness (P2/P3)

9. **F-IA-09** — Expand card with meaningful content (full phase text, age, actions)
10. **F-IA-10** — Bulk card selection and batch Forge
11. **F-IA-11** — Multi-program filter
12. **F-IA-06** — Allow zero-answer interview submission
13. **F-IA-16** — Friendly Forge error messages
14. **F-IA-13** — Clean up ADO placeholder labeling
15. **F-IA-15** — Delete or integrate PlanPreview

---

## 8. Positive Observations

The following aspects are well-designed and should be preserved:

- **HealthBar always visible** — zero-click portfolio health is the right choice.
  The per-program tiles with completion%, plan count, and blocked/failed states
  pack a high amount of signal into a compact strip.
- **Signal → Forge context propagation** — when a signal is opened in Forge,
  the description textarea is pre-filled with all signal metadata (title, severity,
  type, description). This is correct and eliminates manual copy-paste.
- **Phase auto-expand (phase 0)** — `expandedPhase` initializing to `0` in
  `PlanEditor` means the first phase is always visible on preview, reducing clicks
  for the most common review case.
- **Abort controller on generate** — the `abortRef` pattern in `ForgePanel` correctly
  cancels in-flight generation requests when the component unmounts or re-generates.
  This prevents stale plan responses from overwriting a newer request.
- **"Awaiting Human" pulsing indicator** — the animated orange dot in the toolbar
  communicates urgency at a glance and survives context-switching back to the board.
- **Inline step editing with autofocus** — click-to-edit on step description with
  `autoFocus`, Enter-to-commit, and blur-to-commit is a solid inline editing pattern.
- **Choice buttons in InterviewPanel** — rendering choice questions as toggleable
  buttons (not a dropdown select) allows single-click answers, which is the right
  interaction for a mobile-inspired fast-answer pattern.
- **5-second polling without full re-render** — the `mountedRef` guard and
  conditional state updates prevent zombie state on unmounted components.
- **Color-consistent program identity** — using a deterministic hash of the program
  name to assign a color from `DOT_PALETTE` / `PROGRAM_PALETTE` means a program's
  color is stable across sessions and consistent between the HealthBar tile and
  the card dot, creating a coherent visual identity without any configuration.
