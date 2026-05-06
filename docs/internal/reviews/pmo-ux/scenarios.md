# PMO UX Review -- User Scenarios

**Date:** 2026-03-24
**Persona:** Engineering manager / senior tech lead managing a portfolio of
products across multiple projects and programs. No dedicated support staff.
Balances hands-on delivery with oversight. Uses the PMO system to triage
bugs, decompose features into executable plans, and monitor agent execution
on the Kanban board.

---

## Scenario 1: Triage 3 Bugs Across 2 Projects

### Context

It is Monday morning. Over the weekend, three bug reports came in from
production monitoring:

1. **NDS project** -- "R2 blocks missing on Off day" (critical severity)
2. **NDS project** -- "Crew assignment overlap on double-shift" (high severity)
3. **ATL project** -- "Dashboard KPI drill-down returns stale cache" (medium severity)

All three have been logged as signals in the PMO system. The manager has
30 minutes before standup and needs all three triaged into queued plans so
the team can discuss priorities. Two registered projects are involved: NDS
(program: NDS) and ATL (program: ATL).

### Steps

1. Open the PMO UI. Land on the AI Kanban board.
2. Click the **Signals** toggle in the toolbar to expand the Signals Bar.
3. See all 3 open signals listed with severity color-coding and source info.
4. For the first signal ("R2 blocks missing on Off day"):
   - Click the **Forge** button on that signal row.
   - The system navigates to The Forge with the signal context pre-filled
     (description, severity, signal ID visible in header badge).
   - Confirm the project is set to NDS, task type is auto-detected as
     "bugfix", priority is P0.
   - Click **Generate Plan**.
   - Review the generated plan in the preview -- verify it has a sensible
     phase structure for a bug fix (investigation, fix, test).
   - Click **Approve & Queue**. See the "saved" confirmation.
   - Click **Back to Board**.
5. For the second signal ("Crew assignment overlap"):
   - The Signals Bar should still be visible (or re-toggle it).
   - Click **Forge** on that signal row.
   - Repeat: confirm NDS project, P1, generate, review, approve, back.
6. For the third signal ("Dashboard KPI drill-down"):
   - Click **Forge** on the ATL signal.
   - Confirm the project switches to ATL, P2, generate, review, approve, back.
7. On the board, visually confirm three new cards now appear in the
   **Queued** column -- two under NDS, one under ATL.

### Success Criteria

- All 3 signals produced queued plans linked to the correct projects.
- The signal context (title, severity, type) was pre-filled each time
  without manual re-entry.
- Each signal's status transitions to "triaged" after forge completion.
- The board shows 3 new cards in Queued with correct program tags.

### Speed Target

Under 5 minutes for all 3, including plan review. Each forge-approve cycle
should take under 90 seconds.

---

## Scenario 2: Forge a Feature Plan from a PRD

### Context

The team has a new feature to build: "Phase 3 Flight Ops Optimization"
(F-4202 in ADO). The manager has a detailed PRD with success criteria,
data dependencies, and technical constraints. The goal is to decompose this
PRD into an executable multi-phase plan, refine it through the interview
mechanism, and get it queued on the board. This is a significant feature
(estimated 8-12 agent steps across 3-4 phases).

### Steps

1. Navigate to **The Forge** via the top nav tab.
2. In the Intake form:
   - Use the **Import from ADO** combobox to search for "F-4202" or
     "Flight Ops". Select the matching work item. The description field
     should pre-fill with the Feature description.
   - Verify the project selector shows "NDS" (the correct project).
   - Set task type to "New Feature" and priority to P0.
   - Optionally append additional PRD context to the description textarea.
3. Click **Generate Plan**.
4. In the Preview phase:
   - Review the plan stats bar (phases, steps, gates, risk level).
   - Expand each phase to inspect step descriptions and agent assignments.
   - The plan should decompose the feature into logical phases (e.g.,
     data modeling, implementation, testing, deployment).
5. The manager thinks the testing phase is too thin. Click **Regenerate**.
6. In the Interview panel:
   - The system generates 3-5 structured questions analyzing the plan
     (e.g., "The plan has no dedicated test phase -- what testing strategy
     do you want?" or "Multiple agents are involved -- how should they
     coordinate?").
   - Answer the relevant questions using choice chips or free-text inputs.
     Skip questions that are not relevant.
   - Click **Re-generate with N answers**.
7. Review the regenerated plan. The testing phase should now be more
   substantive based on the interview answers.
8. Use the PlanEditor to make a manual adjustment: reorder a step using
   the up/down arrow buttons, and edit one step's description inline.
9. Click **Approve & Queue**.
10. See the success confirmation with the saved file path.
11. Click **Back to Board** and confirm a new card appears in Queued.

### Success Criteria

- ADO combobox search finds the work item and pre-fills the description.
- Plan generation completes within 120 seconds (the timeout boundary).
- Interview questions are contextually relevant to the plan's structure.
- Regenerated plan visibly incorporates the interview answers.
- Manual edits (reorder, inline text edit) persist through to approval.
- The approved plan appears on the board as a Queued card with correct
  program, risk level, and agent list.

### Speed Target

8-12 minutes for the full cycle including interview refinement and manual
edits. This is a deliberate, high-stakes planning activity -- speed is
secondary to plan quality.

---

## Scenario 3: One-Shot Bug Fix from the Kanban Board

### Context

The manager is reviewing the board during a quick check-in. They notice a
card in the **Executing** column for NDS has an error flag -- a bug surfaced
during execution. They want to create a targeted fix plan and get it queued
immediately without a lengthy Forge session. The board already has 8 cards
across various columns.

### Steps

1. On the AI Kanban board, scan for cards with error indicators (red
   border-left, error text in the card body).
2. Click the card to expand its detail section. Read the error message
   and note the project (NDS) and relevant context.
3. Click **+ New Plan** in the toolbar (there is no direct "Forge from
   card" action on the card itself -- this is a potential UX gap).
4. In The Forge intake form:
   - Select the NDS project.
   - Set task type to "Bug Fix", priority to P0.
   - Paste or type the error context from the failed card into the
     description field.
5. Click **Generate Plan**.
6. Review the plan quickly -- for a targeted bug fix, it should be 1-2
   phases with 2-4 steps.
7. Click **Approve & Queue** immediately (no regeneration needed for a
   focused fix).
8. Click **Back to Board**. The new fix plan appears in Queued.

### Success Criteria

- The manager can move from identifying a board error to having a queued
  fix plan without leaving the PMO UI.
- The round-trip (board observation to forge to approval to board) is
  smooth with no dead ends.
- The new card appears in Queued alongside the existing cards.

### Speed Target

Under 2 minutes from spotting the error to having the fix plan queued.

**UX friction note:** The current design requires manually copying error
context from the expanded card into the Forge description. There is no
"Forge fix" action on a card. The evaluator should assess whether this
gap is acceptable or whether a card-level action is needed.

---

## Scenario 4: Portfolio Health Check Across 4 Programs

### Context

It is Wednesday afternoon. The manager oversees 4 programs: NDS, ATL, COM,
and RW. There are roughly 15-20 active plans across all programs. Before
the weekly leadership sync, the manager needs to:

1. Assess overall portfolio health.
2. Identify the worst blocker across all programs.
3. Take action on it (either escalate, re-plan, or unblock).

### Steps

1. Open the PMO UI. The **HealthBar** at the top of the Kanban board shows
   all 4 programs with completion percentages, progress bars, and status
   breakdowns (active, done, blocked, failed counts).
2. Scan the HealthBar:
   - NDS: 45% complete, 2 active, 1 blocked.
   - ATL: 70% complete, 1 active, 0 blocked.
   - COM: 20% complete, 3 active, 1 failed.
   - RW: 90% complete, 0 active, 0 blocked.
3. COM has the worst health: 20% completion with a failed plan. Click the
   **COM** filter button in the toolbar to isolate COM cards.
4. On the filtered board, locate the failed card (it will be in the
   **Executing** column with an error indicator). Click it to expand.
5. Read the error message and agents involved. Determine whether the
   failure is recoverable (re-plan) or needs human intervention.
6. Decision: re-plan. Click **+ New Plan** to open The Forge.
7. In the Forge, select the COM project, describe the remediation work,
   generate a plan, approve, and queue it.
8. Switch back to the board, click **All** filter to see the full
   portfolio again. Verify COM now has 1 more queued plan.
9. Check the **Awaiting Human** column across all programs -- note any
   plans that need human unblocking. The toolbar shows the pulsing
   "N awaiting" indicator if any exist.
10. Check the Signals Bar for any new signals that need triage.

### Success Criteria

- The HealthBar gives an at-a-glance summary of all 4 programs without
  any clicks or navigation.
- Program filtering correctly isolates cards for a single program while
  preserving health data for context.
- Failed/blocked plans are visually distinct and easy to locate.
- The manager can identify the worst problem and take corrective action
  (forge a new plan) in a single flow.
- The "awaiting human" indicator provides a clear count without needing
  to scan individual columns.

### Speed Target

Under 3 minutes to assess all 4 programs and identify the worst blocker.
Action (re-planning) adds 2-3 minutes. Total: under 6 minutes.

---

## Scenario 5: Signal-to-Plan Escalation for a Critical Bug

### Context

A critical production bug just surfaced: the crew scheduling system is
assigning pilots to flights that conflict with mandatory rest periods.
This is a safety-critical issue. The manager needs to:

1. Log it as a signal immediately.
2. Triage it through the Forge into an execution plan.
3. Get it queued as P0 -- Critical.

The bug affects the NDS project.

### Steps

1. On the Kanban board, click **Signals** to expand the Signals Bar.
2. Click **+ Add Signal** in the Signals Bar header.
3. Fill in the inline add form:
   - Title: "Pilot rest-period conflict in crew scheduling"
   - Severity: Critical (dropdown).
4. Click **Add**. The signal appears at the top of the Signals Bar list
   with a red "critical" severity badge.
5. Immediately click the **Forge** button on the newly created signal.
6. The Forge opens with pre-filled context:
   - Description includes: "Signal: Pilot rest-period conflict in crew
     scheduling / Severity: critical / Type: bug"
   - The header badge shows "from signal: sig-XXXXXXXXX".
7. Select NDS project. Verify priority is set to P0 (or set it manually).
   Set task type to "Bug Fix".
8. Click **Generate Plan**.
9. In the preview, verify the plan is appropriately scoped for a critical
   fix (investigation + fix + regression test + validation gate).
10. This is safety-critical -- click **Regenerate** to request more detail.
11. In the interview, answer questions about testing rigor and validation
    requirements. Click **Re-generate**.
12. Review the regenerated plan. Verify it now includes a validation gate.
13. Click **Approve & Queue**.
14. Return to the board. The new card should be in Queued with a P0 badge
    and visible risk indicator.

### Success Criteria

- Signal creation to forge pre-fill is a single click (no manual
  re-entry of bug details).
- The severity of the signal flows through to the plan priority.
- The forge-to-board pipeline preserves the signal linkage (the signal's
  `forge_task_id` field is populated).
- The critical plan is visually prominent on the board (P0 badge, risk
  chip).
- Interview refinement adds meaningful rigor to the plan for a safety
  concern.

### Speed Target

Under 4 minutes from signal creation to queued plan, including one
regeneration pass.

---

## Scenario 6: Resume an Interrupted Forge Session

### Context

The manager was halfway through forging a complex feature plan. They had
generated a plan, reviewed the preview, and clicked **Regenerate** to
enter the interview phase. Before completing the interview answers, they
had to close the tab for an urgent meeting. Thirty minutes later, they
return to the PMO UI to finish.

### Steps

1. Open the PMO UI. It loads to the Kanban board (default view).
2. Navigate to **The Forge** via the top nav tab.
3. The Forge loads in the **Intake** phase -- the previous session state
   is gone. The description field is empty. The interview answers are
   lost.
4. The manager must start over:
   - Re-enter or paste the feature description.
   - Select the project and task type.
   - Click **Generate Plan** again.
   - Wait for generation.
   - Click **Regenerate** again.
   - Re-answer the interview questions.
   - Click **Re-generate**.
   - Review and approve.

### Success Criteria

This scenario intentionally tests a **known limitation**: the current
design explicitly does not persist Forge session state. Per the design
spec: "If the user navigates away from ForgePanel during generation or
interview, the in-flight request is aborted. Progress is lost -- user
starts fresh."

The evaluator should assess:

- Is the loss of state acceptable for the target persona (busy manager)?
- How painful is it to reconstruct the session from scratch?
- Should the system provide a warning before navigating away from an
  in-progress Forge session ("You have unsaved work")?
- Should minimal state (description + project + last plan JSON) be cached
  in localStorage or sessionStorage for recovery?

### Speed Target

The restart adds 5-8 minutes of rework. For a feature plan that originally
took 10 minutes, this nearly doubles the total time investment. The
evaluator should determine whether this is an acceptable trade-off
against implementation complexity.

---

## Scenario 7: Batch Triage -- 5 Signals Overnight

### Context

It is Tuesday morning. Five signals accumulated overnight from automated
monitoring and on-call escalations:

1. NDS -- "Training pipeline OOM on large batches" (high)
2. NDS -- "API timeout on crew-list endpoint" (medium)
3. ATL -- "Dashboard export CSV truncated at 10k rows" (medium)
4. COM -- "Revenue forecast model drift detected" (high)
5. COM -- "Cargo capacity optimizer returning negative values" (critical)

The manager needs to process all 5 into queued plans efficiently before
the 9:00 AM standup. They want to handle the critical one first, then
batch through the rest.

### Steps

1. Open the PMO UI. Click **Signals** to expand the Signals Bar.
2. See all 5 open signals listed. Note the severity color-coding:
   critical (red), high (orange), medium (yellow).
3. The signals are listed in creation order, not severity order. The
   manager must visually scan for the critical one.
4. Start with the critical signal ("Cargo capacity optimizer returning
   negative values"):
   - Click **Forge** on that row.
   - The Forge opens pre-filled. Verify COM project, P0, Bug Fix.
   - **Generate Plan** --> preview --> **Approve & Queue**.
   - Click **Back to Board**.
5. Re-open the Signals Bar. The triaged signal should now show as
   "triaged" (or be absent from the open list). 4 signals remain.
6. Process signal #2 ("Revenue forecast model drift"):
   - Click **Forge** --> COM project, P1, Bug Fix.
   - Generate --> Approve --> Back to Board.
7. Process signal #3 ("Training pipeline OOM"):
   - Click **Forge** --> NDS project, P1, Bug Fix.
   - Generate --> Approve --> Back to Board.
8. Process signal #4 ("API timeout on crew-list endpoint"):
   - Click **Forge** --> NDS project, P2, Bug Fix.
   - Generate --> Approve --> Back to Board.
9. Process signal #5 ("Dashboard export CSV truncated"):
   - Click **Forge** --> ATL project, P2, Bug Fix.
   - Generate --> Approve --> Back to Board.
10. Check the board: 5 new cards in Queued across 3 programs. Check the
    Signals Bar: 0 open signals.

### Success Criteria

- All 5 signals are processable from the Signals Bar without manual
  data re-entry.
- Each forge cycle is fast (under 90 seconds per signal).
- The Signals Bar updates correctly after each triage -- resolved/triaged
  signals disappear from the open list.
- The board reflects all 5 new plans in the correct programs.
- Severity-to-priority mapping feels natural (critical -> P0, high -> P1,
  medium -> P2).

### Speed Target

Under 8 minutes for all 5 signals. This requires each forge-approve
cycle to average under 90 seconds, plus transition time.

**UX friction note:** The evaluator should assess the round-trip cost of
the Board --> Forge --> Board loop repeated 5 times. The current flow
requires: (1) toggle Signals Bar, (2) click Forge on signal, (3) view
navigates to Forge, (4) generate, (5) approve, (6) click Back to Board,
(7) re-toggle Signals Bar. Steps 1, 6, and 7 are overhead that compounds
across 5 signals. A batch-forge mode or auto-return-to-signals behavior
could reduce this.

---

## Scenario 8: Board-to-Forge Re-Plan

### Context

A plan was queued two days ago for the NDS project: "Migrate crew roster
data to new schema." After further discussion with the team, the approach
needs to change -- they decided to do an incremental migration instead of
a big-bang cutover. The existing plan in the Queued column needs to be
reworked. The manager wants to pull it back into the Forge, edit the
approach, and re-approve a revised plan.

### Steps

1. On the Kanban board, locate the card "Migrate crew roster data to new
   schema" in the **Queued** column.
2. Click the card to expand its detail section. See the plan ID, agents,
   program, and gates info.
3. The manager wants to re-forge this plan with a new approach. However,
   there is **no "Re-forge" or "Edit in Forge" action** on the card.
4. Workaround path:
   - Click **+ New Plan** or navigate to **The Forge** tab.
   - Manually type a new description: "Migrate crew roster data to new
     schema -- use incremental migration pattern instead of big-bang."
   - Select NDS project, task type "Migration", same priority.
   - Click **Generate Plan**.
   - Review the new plan. Use the PlanEditor to adjust phases:
     - Add a step for backward-compatibility shim.
     - Reorder the rollback step to be earlier in the sequence.
     - Edit a step description to reference the incremental approach.
   - Click **Approve & Queue**.
5. Return to the board. The old plan card is still in Queued (it was not
   automatically replaced or archived).
6. The manager now has two cards for the same logical work item -- the
   original and the revised one. There is no mechanism to retire or
   archive the old card from the UI.

### Success Criteria

This scenario intentionally tests a **gap in the current system**: there
is no board-to-forge re-plan flow. The evaluator should assess:

- The friction of manually reconstructing a revised plan description
  when the original context is not carried over.
- The risk of duplicate cards on the board (old plan + new plan for the
  same work) and the lack of a card-level archive/delete action.
- Whether the PlanEditor's CRUD capabilities (add/remove/reorder steps,
  inline edit) are sufficient for making the needed changes directly
  in the preview, avoiding a full re-forge.
- Whether a "Duplicate to Forge" action on a card (pre-filling Forge
  with the card's plan data) would close the gap.

### Speed Target

The workaround takes 5-7 minutes including manual description writing
and plan editing. A direct re-forge action could reduce this to 3-4
minutes. The evaluator should assess whether the workaround cost is
tolerable for the frequency of re-planning events.

---

## Summary of Scenario Coverage

| # | Scenario | Primary Surface | Tests |
|---|----------|-----------------|-------|
| 1 | Triage 3 bugs | Signals Bar, Forge, Board | Signal-to-forge pipeline, multi-project context switching |
| 2 | Forge a feature plan | Forge (full cycle) | ADO import, generation, interview, manual editing, approval |
| 3 | One-shot bug fix from board | Board, Forge | Board-to-forge transition, quick forge for simple bugs |
| 4 | Portfolio health check | HealthBar, Board, Forge | Cross-program visibility, filtering, blocker identification |
| 5 | Signal escalation (critical) | Signals Bar, Forge | End-to-end signal lifecycle, severity handling, safety rigor |
| 6 | Resume interrupted session | Forge | Session persistence (known gap), recovery cost |
| 7 | Batch triage (5 signals) | Signals Bar, Forge, Board | Repetitive workflow efficiency, transition overhead |
| 8 | Board-to-forge re-plan | Board, Forge | Re-planning flow (known gap), duplicate card risk |

### Key UX Gaps Flagged for Evaluation

1. **No card-level "Forge fix" action** -- moving from a board card to
   a Forge session requires manual context transfer (Scenarios 3, 8).
2. **No Forge session persistence** -- navigating away loses all state
   including interview answers (Scenario 6).
3. **No batch-forge mode** -- processing multiple signals requires
   repetitive Board-Forge-Board round-trips (Scenario 7).
4. **No card archive/retire action** -- re-planned work creates duplicate
   cards with no way to clean up the old one from the UI (Scenario 8).
5. **Signals not sorted by severity** -- the manager must visually scan
   for critical items rather than seeing them at the top (Scenario 7).
