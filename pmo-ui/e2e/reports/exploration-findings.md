# PMO UI — E2E Exploration Findings

**Date:** 2026-03-28
**Tests:** 161 total (54 journey + 60 functionality + 47 output) — 160 passed, 1 genuine bug

---

## Category 1: Clunky User Journeys

### HIGH — Fix Required

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| CJ-01 | **Reorder buttons are 8x8px** — below any reasonable interaction target (WCAG recommends 24px). Keyboard and mouse users struggle to hit them. | PlanEditor.tsx | Users can't reorder steps reliably |
| CJ-02 | **3 clicks to edit a plan from board** — expand card → View Plan → Re-forge. No direct "Edit Plan" shortcut from the card. | KanbanCard.tsx | Slow iteration workflow |
| CJ-03 | **No explicit Save in plan editor** — edits live in memory until "Approve & Queue". No save feedback. Navigating away loses all edits silently. | PlanEditor.tsx / ForgePanel.tsx | Users lose work |
| CJ-04 | **Board API `{}` crashes to blank screen** — when backend returns empty object instead of `{cards:[], health:{}}`, React throws on `undefined.map()` | KanbanBoard.tsx / usePmoBoard.ts | App crashes |

### MEDIUM — Should Fix

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| CJ-05 | **Collapsed card has no expand affordance** — no chevron, disclosure triangle, or "click to expand" hint. Users must guess cards are expandable. | KanbanCard.tsx | Discoverability |
| CJ-06 | **Signal resolve failure is silent** — `handleResolve` catches errors with no user feedback. Signal stays in list with no indication it failed. | SignalsBar.tsx | Lost feedback |
| CJ-07 | **ADO search failure is silent** — `catch` block swallows errors. User sees empty dropdown with no "no results" or error message. | AdoCombobox.tsx | Confusing behavior |
| CJ-08 | **Error banner not actionable** — says "retrying every 5s" but doesn't explain root cause or suggest user action (e.g., "Check if backend is running"). | KanbanBoard.tsx | Unhelpful error |
| CJ-09 | **Saved plan shows raw filesystem path** — `/home/user/.claude/team-context/plan.json` is meaningless to users. Should show project name or summary. | ForgePanel.tsx | Confusing output |
| CJ-10 | **Connection indicator says "polling"** — technical jargon. Should be "Live" / "Delayed" / "Reconnecting". | KanbanBoard.tsx | Confusing status |

### LOW — Nice to Have

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| CJ-11 | **No cancel/back button in interview** — users can't return to plan preview once interview starts. Only option is to complete or reload. | ForgePanel.tsx | Stuck in flow |
| CJ-12 | **Empty phase after deleting all steps** — no prompt to add steps or remove the empty phase. | PlanEditor.tsx | Dead state |

---

## Category 2: UI Functionality Breaks

### HIGH — Fix Required

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| FB-01 | **StatTile overflow** — `task_id` longer than ~12 chars overflows the 120px max-width constraint. Text bleeds through container. | PlanPreview.tsx | Visual break |
| FB-02 | **setState during render** — `onOpenCountChange?.(openCount)` called inside `setSignals()` updater triggers React warning: "Cannot update KanbanBoard while rendering SignalsBar". | SignalsBar.tsx | React warning (cosmetic) |
| FB-03 | **Border shorthand mixing** — Signal `<li>` uses both `border` and `borderLeft` inline props, triggering React dev-mode style warning. | SignalsBar.tsx | React warning (cosmetic) |

### MEDIUM — Should Fix

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| FB-04 | **Empty step description renders invisible div** — step with `task_description: ''` renders a zero-size click target. | PlanEditor.tsx | Broken interaction |
| FB-05 | **Health stats don't sum** — `total_plans` doesn't always equal `active + done + blocked + failed`. Data integrity issue. | HealthBar.tsx | Misleading numbers |

---

## Category 3: Complicated or Broken Outputs

### HIGH — Fix Required

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| BO-01 | **Card shows internal ID, not ADO ID** — collapsed card shows `card-001` hash. The external ADO ID (human-readable) is only visible when expanded. | KanbanCard.tsx | Users can't identify cards |
| BO-02 | **Signal IDs are cryptic** — truncated to 12 chars of an internal hash. Not useful for identification. | SignalsBar.tsx | Meaningless data |
| BO-03 | **Agent names are technical** — "backend-engineer--python" shown as-is. Users see implementation detail rather than role description. | KanbanCard.tsx, PlanEditor.tsx, PlanPreview.tsx | Confusing labels |
| BO-04 | **Signal types are machine labels** — "stale_plan", "missing_gate" shown raw. No human-readable label or icon. | SignalsBar.tsx | Cryptic categorization |

### MEDIUM — Should Fix

| ID | Finding | Component | Impact |
|----|---------|-----------|--------|
| BO-05 | **Progress pips tiny at 4px** — hard to perceive completion visually. Step count text (e.g., "3/5") is more useful. | KanbanCard.tsx | Low information density |
| BO-06 | **Health bar % ambiguous** — "33%" with no label. Percentage of what? Plans completed? Steps? | HealthBar.tsx | Ambiguous metric |
| BO-07 | **Plan phase names may be generic** — "Phase 1", "Phase 2" rather than descriptive action names. | PlanPreview.tsx / PlanEditor.tsx | Low information scent |
| BO-08 | **Signal description truncated to 160px** — may cut off critical context mid-sentence. | SignalsBar.tsx | Lost information |

---

## Recommended Fix Priority

### Wave 1 — Critical (fix now)
1. **CJ-01**: Enlarge reorder buttons to 24px minimum
2. **CJ-04**: Defensive null check for board API response
3. **FB-01**: Fix StatTile overflow with text-overflow: ellipsis + tooltip
4. **CJ-05**: Add expand chevron/indicator on collapsed cards

### Wave 2 — High (fix soon)
5. **CJ-02**: Add direct "Edit Plan" button on card actions
6. **CJ-06/07**: Add error feedback for signal resolve and ADO search failures
7. **BO-01**: Show ADO ID on collapsed card (next to internal ID)
8. **BO-03**: Add agent display name mapping (technical → friendly)
9. **BO-04**: Add signal type display name mapping
10. **CJ-09**: Show project name instead of filesystem path on saved plan
11. **CJ-10**: Rename "polling" to "Reconnecting"

### Wave 3 — Medium (address when convenient)
12. **FB-02/03**: Fix React warnings (useEffect for count propagation, clean up border shorthand)
13. **CJ-03**: Add auto-save or explicit save button in plan editor
14. **CJ-08**: Make error banner more actionable
15. **BO-05**: Add step count text alongside or instead of pips
16. **BO-06**: Add label to health bar percentage
17. **BO-08**: Expand signal description truncation or add tooltip
