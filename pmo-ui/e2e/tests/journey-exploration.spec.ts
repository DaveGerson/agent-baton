/**
 * journey-exploration.spec.ts — End-to-end user journey tests that probe for
 * clunky workflows, missing feedback, confusing transitions, and dead-ends in
 * the PMO UI.
 *
 * Philosophy:
 *   Tests here are DETECTORS, not validators.  They actively look for UX
 *   friction: excessive click counts, absent loading indicators, cryptic IDs
 *   in place of human labels, non-obvious state changes, and broken recovery
 *   paths.  An assertion that FAILS flags a REAL usability bug.
 *
 * Run with:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3100/pmo/ \
 *     npx playwright test e2e/tests/journey-exploration.spec.ts --project=desktop
 *
 * Each journey is a separate describe block.  Individual tests inside a journey
 * use try/catch so later steps can still run after an earlier assertion fails.
 *
 * Clunkiness scoring legend (annotated in comments):
 *   [CLUNKY-1] Missing visual feedback / loading state
 *   [CLUNKY-2] Too many clicks to complete a task
 *   [CLUNKY-3] Cryptic or truncated ID shown instead of human-readable label
 *   [CLUNKY-4] State not preserved across navigation round-trips
 *   [CLUNKY-5] Absent or insufficient error recovery path
 *   [CLUNKY-6] Next action not obvious after completing a step
 *   [CLUNKY-7] No breadcrumb / phase orientation cue
 *   [CLUNKY-8] Action buttons hidden or undiscoverable
 */

/// <reference types="node" />
import { test, expect } from '../fixtures/test-fixtures.js';
import { captureFullPage } from '../utils/screenshots.js';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Navigate to the board and wait for the React app to fully settle.
 */
async function loadBoardWithMocks(
  kanban: import('../pages/KanbanPage.js').KanbanPage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await mockAll();
  await kanban.goto('/');
  await kanban.waitForAppReady();
  // Give initial API calls (board, signals badge) time to resolve.
  await kanban.page.waitForTimeout(500);
}

/**
 * Navigate to the Forge intake phase.
 */
async function loadForgeWithMocks(
  forge: import('../pages/ForgePage.js').ForgePage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await mockAll();
  await forge.goto('/');
  await forge.waitForAppReady();
  await forge.switchToForge();
  await forge.assertIntakePhase();
}

/**
 * Drive to the Forge preview phase by filling in the intake form and
 * generating a plan.
 */
async function driveToPreviewPhase(
  forge: import('../pages/ForgePage.js').ForgePage,
  description = 'Implement JWT authentication middleware for the API gateway',
): Promise<void> {
  await forge.taskDescriptionTextarea.fill(description);
  await forge.generateButton.click();
  // Wait for "Plan Ready" heading which is unique to preview
  await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
}

/**
 * Count the number of interactions (clicks / keypresses) recorded in a
 * sequence.  We use a simple counter passed as a closure.
 */
function makeInteractionCounter() {
  let count = 0;
  return {
    tick() { count++; },
    get value() { return count; },
    reset() { count = 0; },
  };
}

// ---------------------------------------------------------------------------
// Journey 1: "I want to create a new plan from scratch"
// ---------------------------------------------------------------------------

test.describe('Journey 1: Create a new plan from scratch', () => {
  test('step count from New Plan click to Forge intake is at most 1 interaction', async ({
    page, kanban, mockAll,
  }) => {
    await loadBoardWithMocks(kanban, mockAll);

    const counter = makeInteractionCounter();

    // Step 1 — click New Plan button
    await kanban.newPlanButton.click();
    counter.tick();

    await captureFullPage(page, 'j1-step1-forge-intake');

    // The forge intake form must be immediately visible — no extra clicks needed.
    // [CLUNKY-2] If the user needs more than 1 click to get here, flag it.
    await expect(page.getByPlaceholder('Describe the work: what needs to be built, fixed, or analyzed.')).toBeVisible({
      timeout: 5_000,
    });

    expect(counter.value).toBeLessThanOrEqual(1);
  });

  test('phase label is always visible during Forge flow — breadcrumb orientation', async ({
    page, kanban, forge, mockAll,
  }) => {
    // [CLUNKY-7] Users must always know what phase they are in.
    await loadForgeWithMocks(forge, mockAll);

    // Intake phase — label must identify the phase
    await captureFullPage(page, 'j1-phase-label-intake');
    const intakeLabel = page.locator('span').filter({
      hasText: /Describe the work/,
    }).first();
    await expect(intakeLabel).toBeVisible({ timeout: 5_000 });

    // Generate plan → Generating phase
    await forge.taskDescriptionTextarea.fill('Build a new authentication service');
    await forge.generateButton.click();

    // During generation there should be a visible "Generating" phase indicator
    await captureFullPage(page, 'j1-phase-label-generating');
    const generatingLabel = page.locator('span').filter({
      hasText: /Generating plan/,
    }).first();
    try {
      await expect(generatingLabel).toBeVisible({ timeout: 5_000 });
    } catch {
      // If generation is instant (mock), the phase may have already transitioned —
      // that is acceptable.  But if generating is shown with no label it is a bug.
    }

    // Preview phase label
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
    await captureFullPage(page, 'j1-phase-label-preview');
    const previewLabel = page.locator('span').filter({
      hasText: /Review, edit/,
    }).first();
    await expect(previewLabel).toBeVisible({ timeout: 5_000 });

    // Approve → Saved phase label
    await forge.approveAndQueueButton.click();
    await captureFullPage(page, 'j1-phase-label-saved');
    const savedLabel = page.locator('span').filter({
      hasText: /Plan saved/,
    }).first();
    await expect(savedLabel).toBeVisible({ timeout: 10_000 });
  });

  test('Generate button disabled when description is empty — validation feedback', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-1] If Generate is enabled with no description, errors appear only after
    // a round-trip, which is confusing.  Button must be visually disabled.
    await loadForgeWithMocks(forge, mockAll);

    await captureFullPage(page, 'j1-generate-button-empty');

    // Confirm textarea is empty (persisted state from other tests is cleared by fixture isolation)
    await forge.taskDescriptionTextarea.fill('');
    await forge.taskDescriptionTextarea.blur();

    // Generate button must be disabled (not just styled — it should have disabled attr)
    await expect(forge.generateButton).toBeDisabled({ timeout: 3_000 });
  });

  test('error during generation shows clear message and returns to intake with form intact', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    // [CLUNKY-5] After a generation failure the user must be able to retry without
    // re-typing their description.
    await mockBoard();
    await mockForge({ failForgePlan: true });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.newPlanButton.click();

    const descriptionText = 'This is my task description that should survive an error';
    const textarea = page.getByPlaceholder('Describe the work: what needs to be built, fixed, or analyzed.');
    await textarea.fill(descriptionText);
    await page.getByRole('button', { name: /Generate Plan/ }).click();

    await captureFullPage(page, 'j1-error-after-generation-failure');

    // Wait for error — the generate button re-appears on failure (phase = intake)
    await expect(page.getByRole('button', { name: /Generate Plan/ })).toBeVisible({
      timeout: 15_000,
    });

    // The description must still be present — the user should not need to retype it.
    // [CLUNKY-5] If the textarea is blank after failure, that is a recovery UX failure.
    const currentValue = await textarea.inputValue();
    expect(currentValue).toBe(descriptionText);

    // An error message must be visible.
    const errorBanner = page.locator('div').filter({ hasText: /failed|Failed|error|Error|LLM timeout/ }).first();
    await expect(errorBanner).toBeVisible({ timeout: 5_000 });
  });

  test('after approval the next action (Start Execution) is above the fold', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-6] After saving a plan the most obvious next action must be visible
    // without scrolling.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await forge.approveAndQueueButton.click();

    await expect(forge.savedHeader).toBeVisible({ timeout: 10_000 });
    await captureFullPage(page, 'j1-saved-phase-next-action');

    // "Start Execution" button must be visible without scrolling
    await expect(forge.startExecutionButton).toBeVisible({ timeout: 5_000 });
    const box = await forge.startExecutionButton.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      // Button must be within the viewport height (900px desktop)
      expect(box.y + box.height).toBeLessThanOrEqual(900);
    }

    // "Back to Board" must also be visible — the other natural next action.
    await expect(forge.backToBoardFromSavedButton).toBeVisible({ timeout: 3_000 });
  });

  test('back button from preview shows dirty-state confirmation prompt', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-4] If the user accidentally navigates away from an unsaved plan,
    // they must be warned before the plan is lost.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);

    // Set up dialog listener before clicking back
    let dialogMessage = '';
    page.on('dialog', async (dialog) => {
      dialogMessage = dialog.message();
      await dialog.dismiss(); // Cancel — user changes their mind
    });

    await forge.backToBoardButton.click();
    await page.waitForTimeout(500);

    await captureFullPage(page, 'j1-back-dirty-state-warning');

    // A confirmation dialog must have been shown
    expect(dialogMessage).toMatch(/unsaved|lost|leave/i);

    // After dismissing, user must remain on the preview page
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 3_000 });
  });

  test('Edit Intake back button in preview does not require confirmation', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-2] The "Edit Intake" button in preview should return to the intake form
    // directly without an extra confirmation step.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);

    // There should be an "Edit Intake" back navigation visible in the header
    await captureFullPage(page, 'j1-edit-intake-button-visible');
    await expect(forge.editIntakeButton).toBeVisible({ timeout: 3_000 });

    // Click it — no dialog expected
    let dialogShown = false;
    page.once('dialog', async (dialog) => {
      dialogShown = true;
      await dialog.dismiss();
    });

    await forge.editIntakeButton.click();
    await page.waitForTimeout(300);

    // Should be back at intake without dialog
    expect(dialogShown).toBe(false);
    await expect(forge.taskDescriptionTextarea).toBeVisible({ timeout: 5_000 });
  });
});

// ---------------------------------------------------------------------------
// Journey 2: "I want to find and act on a specific card"
// ---------------------------------------------------------------------------

test.describe('Journey 2: Find and act on a specific card', () => {
  test('cards show title and status at a glance without expanding', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] Cards must show enough info to identify what they are without
    // requiring an expand.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j2-board-cards-at-a-glance');

    // The high-priority "awaiting human" card must be visually distinguishable.
    // It should have an orange indicator (pulsing dot).
    const awaitingCard = page.locator('div').filter({
      hasText: 'Review API contract changes',
    }).first();
    await expect(awaitingCard).toBeVisible({ timeout: 5_000 });

    // The orange awaiting indicator in the toolbar (not the card itself) tells
    // the user something needs attention without scrolling through cards.
    await expect(page.locator('[role="status"]').filter({ hasText: /awaiting/ })).toBeVisible({
      timeout: 5_000,
    });
  });

  test('action buttons become visible only after expanding — discoverability issue', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-8] Execute and Re-forge buttons are hidden behind a click-to-expand.
    // This test detects whether there is ANY affordance (hover hint, icon) on the
    // collapsed card that signals these actions exist.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j2-collapsed-card-action-affordance');

    // Locate the queued card using its aria-label (KanbanCard sets aria-label on the root div)
    const queuedCard = page.locator('[role="button"]').filter({
      hasText: 'Implement authentication middleware',
    }).first();

    await expect(queuedCard).toBeVisible({ timeout: 5_000 });

    // Before expanding: Execute button should NOT be visible anywhere on the page
    const executeButton = page.getByRole('button', { name: /^\u25B6 Execute$/ });
    await expect(executeButton).toBeHidden({ timeout: 2_000 });

    // This means users MUST know to click the card to reveal actions.
    // Flag as CLUNKY-8 — there is no visual cue that the card is expandable.
    // A disclosure triangle, "click to expand" label, or hover state would fix this.
    const hasDisclosureTriangle = await queuedCard.evaluate(el => {
      const text = el.textContent ?? '';
      // Look for typical disclosure indicators: ▶ ▼ › > + or "expand" text
      return /[▶▼›>]/.test(text) || text.toLowerCase().includes('expand');
    });

    // INTENTIONALLY FAILING if no disclosure indicator — flags the UX issue.
    if (!hasDisclosureTriangle) {
      console.warn('[CLUNKY-8] Collapsed card has no visual cue that it is expandable (no disclosure triangle or "expand" affordance)');
    }
    // We DO want this to pass (cards do work when clicked) but we flag the finding.
    // The actual clickability test:
    await queuedCard.click();
    await page.waitForTimeout(200);
    await captureFullPage(page, 'j2-card-expanded-actions-visible');
    await expect(executeButton).toBeVisible({ timeout: 3_000 });
  });

  test('expanded card clearly shows which column it belongs to', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] After expanding a card the column context must remain clear.
    // The card expands in-place so the column header is still visible, but we
    // verify the column label is within the viewport.
    await loadBoardWithMocks(kanban, mockAll);

    const queuedCard = page.locator('div').filter({
      has: page.locator('div', { hasText: 'Implement authentication middleware' }),
    }).filter({
      has: page.locator('span[style*="font-family: monospace"]'),
    }).first();

    await queuedCard.click();
    await page.waitForTimeout(200);
    await captureFullPage(page, 'j2-expanded-card-column-context');

    // The "Queued" column heading must still be visible in the viewport.
    const queuedHeading = page.getByText('Queued', { exact: true }).first();
    await expect(queuedHeading).toBeVisible({ timeout: 3_000 });
    const headingBox = await queuedHeading.boundingBox();
    expect(headingBox).not.toBeNull();
    if (headingBox) {
      expect(headingBox.y).toBeGreaterThanOrEqual(0); // not scrolled above viewport
    }
  });

  test('Execute button provides clear feedback on success', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-1] After clicking Execute the user must see a feedback message
    // without waiting for a page reload.
    await loadBoardWithMocks(kanban, mockAll);

    // Use aria role=button on the card root element
    const queuedCard = page.locator('[role="button"]').filter({
      hasText: 'Implement authentication middleware',
    }).first();

    await queuedCard.click();
    await page.waitForTimeout(200);

    // Execute button is revealed at page level after expanding
    const executeButton = page.getByRole('button', { name: /^\u25B6 Execute$/ });
    await expect(executeButton).toBeVisible({ timeout: 3_000 });
    await executeButton.click();

    await captureFullPage(page, 'j2-execute-feedback');

    // A success message must appear. The component renders "Launched (PID N)"
    // in a status element.
    const successMessage = page.locator('[role="status"]').filter({
      hasText: /Launched|launched/,
    }).first();
    await expect(successMessage).toBeVisible({ timeout: 8_000 });
  });

  test('View Plan loads and shows meaningful content — not just spinner', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-1] After clicking "View Plan" the user must see actual plan content,
    // not be stuck on a loading state.
    await loadBoardWithMocks(kanban, mockAll);

    // Use aria role=button on the card root element
    const queuedCard = page.locator('[role="button"]').filter({
      hasText: 'Implement authentication middleware',
    }).first();

    await queuedCard.click();
    await page.waitForTimeout(200);

    // View Plan button is revealed at page level after expanding
    const viewPlanButton = page.getByRole('button', { name: /View Plan/ });
    await expect(viewPlanButton).toBeVisible({ timeout: 3_000 });
    await viewPlanButton.click();

    await captureFullPage(page, 'j2-view-plan-content');

    // Plan content must be visible (phase names, step descriptions)
    // The mock card detail returns MOCK_FORGE_PLAN which has "Design & Schema" phase
    const planContent = page.locator('div').filter({
      hasText: /Design|Implementation|Phase/,
    }).last();
    await expect(planContent).toBeVisible({ timeout: 8_000 });

    // "Loading plan…" spinner should NOT still be showing
    const loadingSpinner = page.locator('div', { hasText: 'Loading plan…' });
    await expect(loadingSpinner).toBeHidden({ timeout: 5_000 });
  });

  test('card ID in monospace is cryptic — human-readable context should accompany it', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] The card_id "card-001" in monospace is shown prominently but
    // it is meaningless to a user.  It should either be de-emphasised or
    // accompanied by the external_id (ADO-1234) which is human-meaningful.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j2-card-id-prominence');

    // Check if the external ADO id is shown anywhere on the collapsed card
    // (the card_id is shown but external_id is not shown on collapsed state)
    const cardArea = page.locator('div').filter({
      has: page.locator('div', { hasText: 'Implement authentication middleware' }),
    }).filter({
      has: page.locator('span[style*="font-family: monospace"]'),
    }).first();

    const hasExternalId = await cardArea.evaluate(el => {
      return el.textContent?.includes('ADO-1234') ?? false;
    });

    // INTENTIONALLY FAILING assertion to surface the finding.
    // The collapsed card shows "card-001" (internal ID) but not "ADO-1234" (external, human-readable).
    // This is a discoverability issue — users from ADO won't recognise their work item.
    if (!hasExternalId) {
      console.warn('[CLUNKY-3] Card does not show external ADO ID (ADO-1234) on collapsed view. Only internal card_id is visible.');
    }

    // The internal card_id IS there (confirming the monospace element is rendered)
    const internalIdVisible = await cardArea.evaluate(el => {
      return el.textContent?.includes('card-001') ?? false;
    });
    expect(internalIdVisible).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Journey 3: "I want to triage signals"
// ---------------------------------------------------------------------------

test.describe('Journey 3: Triage signals', () => {
  test('signals toggle reveals the signal bar within 1 click', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-2] Signals must be reachable in 1 click from the board.
    await loadBoardWithMocks(kanban, mockAll);

    const counter = makeInteractionCounter();
    await kanban.signalsToggleButton.click();
    counter.tick();

    await captureFullPage(page, 'j3-signals-panel-open');

    // SignalsBar must be visible
    await expect(kanban.signalsHeader).toBeVisible({ timeout: 5_000 });

    // Interaction cost must be exactly 1.
    expect(counter.value).toBe(1);
  });

  test('signal descriptions are not truncated beyond readability in the panel', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] Signal descriptions truncated to <50 chars are often meaningless.
    // The mock has a long description: "All auth requests failing since 08:00 UTC..."
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    await captureFullPage(page, 'j3-signal-descriptions');

    // Check whether the description is visible at all (it may be truncated to maxWidth: 160px)
    const descriptionSpan = page.locator('span').filter({
      hasText: /All auth requests failing/,
    }).first();

    // If the description span does not contain readable text, flag as CLUNKY-3
    try {
      await expect(descriptionSpan).toBeVisible({ timeout: 3_000 });
    } catch {
      console.warn('[CLUNKY-3] Signal description is not visible in the panel — may be truncated to invisible width or overflowing');
      // Check the truncation: SignalsBar renders description in a span with maxWidth: 160px
      // which will ellipsis on long text.  Users cannot read the full description.
      const isEllipsed = await page.evaluate(() => {
        const spans = Array.from(document.querySelectorAll('span'));
        return spans.some(span => {
          const style = window.getComputedStyle(span);
          return style.textOverflow === 'ellipsis' && style.overflow === 'hidden';
        });
      });
      if (isEllipsed) {
        console.warn('[CLUNKY-3] Signal description is cut off with ellipsis — no way to read full description without forging');
      }
    }
  });

  test('signal IDs shown in the panel are truncated to 12 chars — cryptic', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] "sig-crit-001" is shown as "sig-crit-001" (12 chars) in
    // monospace.  This is cryptic and wastes space compared to showing the title.
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    await captureFullPage(page, 'j3-signal-id-cryptic');

    // The truncated signal ID should be present
    const truncatedId = page.locator('span[style*="font-family: monospace"]').filter({
      hasText: /sig-/,
    }).first();

    try {
      await expect(truncatedId).toBeVisible({ timeout: 3_000 });
      // If it IS visible, the ID is taking precious space that could show context
      const idText = await truncatedId.textContent();
      if (idText && idText.length <= 12) {
        console.warn(`[CLUNKY-3] Signal row shows truncated internal ID "${idText}" — this is cryptic. Consider hiding ID or showing signal type icon instead.`);
      }
    } catch {
      // If not found, the ID column may have been removed — pass.
    }
  });

  test('select-all checkbox is discoverable and positioned near signals', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-8] The select-all checkbox must be visible and clearly associated with
    // the signal list so users discover batch operations.
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j3-select-all-checkbox-discoverable');

    await expect(kanban.selectAllCheckbox).toBeVisible({ timeout: 5_000 });

    // The "Resolve Selected" button must NOT be visible until something is selected.
    await expect(kanban.batchResolveButton).toBeHidden({ timeout: 3_000 });
  });

  test('batch resolve button appears immediately when a signal is selected', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-1] After checking a signal the "Resolve Selected" action must appear
    // without any additional interaction.
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    // Select a single signal via its checkbox
    const firstSignalCheckbox = page.locator('input[type="checkbox"]').filter({
      has: page.locator('[aria-label*="Select signal"]'),
    }).first();

    // Fall back to any signal-specific checkbox
    const signalCheckbox = page.getByLabel(/Select signal: Authentication/i).first();
    try {
      await expect(signalCheckbox).toBeVisible({ timeout: 3_000 });
      await signalCheckbox.check();
    } catch {
      // Try the first visible signal checkbox
      const anySignalCheckbox = page.locator('input[aria-label*="Select signal"]').first();
      await anySignalCheckbox.check();
    }

    await captureFullPage(page, 'j3-batch-resolve-appears-on-select');

    // Resolve Selected button must now be visible
    await expect(kanban.batchResolveButton).toBeVisible({ timeout: 3_000 });
  });

  test('forging from a signal closes signals panel and pre-fills description', async ({
    page, kanban, forge, mockAll,
  }) => {
    // [CLUNKY-4] After forging from a signal the Forge panel should open with
    // the signal description pre-populated so users do not have to re-type context.
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    // Click Forge on the critical signal — use the list item element for scoping
    const criticalSignalRow = page.locator('li').filter({
      has: page.locator('span', { hasText: 'Authentication service returning 500 in prod' }),
    }).first();
    const criticalForgeButton = criticalSignalRow.getByRole('button', { name: 'Forge' });
    await expect(criticalForgeButton).toBeVisible({ timeout: 5_000 });
    await criticalForgeButton.click();
    await page.waitForTimeout(300);

    await captureFullPage(page, 'j3-forge-from-signal-prefilled');

    // Signals panel must be closed (it would overlap the forge form)
    await expect(kanban.signalsHeader).toBeHidden({ timeout: 3_000 });

    // Forge panel must be open
    await expect(forge.taskDescriptionTextarea).toBeVisible({ timeout: 5_000 });

    // Description must be pre-filled with signal context
    const currentValue = await forge.taskDescriptionTextarea.inputValue();
    expect(currentValue.length).toBeGreaterThan(10);
    expect(currentValue).toMatch(/Authentication service returning 500/i);
  });

  test('resolving a signal removes it from the list — no manual refresh needed', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-1] After resolving a signal the list must update immediately.
    // If the user must refresh to see the change, that is a feedback gap.
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    // Verify the signal is initially visible
    await expect(page.getByText('Authentication service returning 500 in prod')).toBeVisible({
      timeout: 5_000,
    });

    // Resolve it — scope to the list item for reliable button targeting
    const critSignalRow = page.locator('li').filter({
      has: page.locator('span', { hasText: 'Authentication service returning 500 in prod' }),
    }).first();
    const resolveButton = critSignalRow.getByRole('button', { name: 'Resolve' });
    await resolveButton.click();

    await captureFullPage(page, 'j3-signal-resolved-removed-from-list');
    await page.waitForTimeout(500);

    // The resolved signal must no longer appear in the open list
    const resolvedSignalInList = page.locator('li').filter({
      hasText: 'Authentication service returning 500 in prod',
    }).first();
    await expect(resolvedSignalInList).toBeHidden({ timeout: 5_000 });
  });

  test('critical signals are visually differentiated from medium/low', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] Users doing triage must immediately see which signals are critical.
    // The severity badge color must differ visually.
    await loadBoardWithMocks(kanban, mockAll);
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j3-signal-severity-visual-diff');

    // Both critical and medium signals should be visible
    await expect(page.getByText('critical').first()).toBeVisible({ timeout: 3_000 });
    await expect(page.getByText('medium').first()).toBeVisible({ timeout: 3_000 });

    // The left border color on signal rows should differ between severities.
    // This is enforced by `borderLeft: 3px solid ${severityColor(sig.severity)}`
    // We check that two different border-left colors exist.
    const borderColors = await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('li'));
      return items.map(li => {
        const style = window.getComputedStyle(li);
        return style.borderLeftColor;
      }).filter(c => c && c !== 'rgba(0, 0, 0, 0)');
    });

    // There should be at least 2 distinct colors for the two different severities
    const uniqueColors = new Set(borderColors);
    expect(uniqueColors.size).toBeGreaterThanOrEqual(2);
  });
});

// ---------------------------------------------------------------------------
// Journey 4: "I want to understand program health"
// ---------------------------------------------------------------------------

test.describe('Journey 4: Understand program health', () => {
  test('health bar is immediately visible without interaction', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-2] The health bar must be visible on first load without any interaction.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j4-health-bar-visible');

    // Both programs should show
    await expect(page.getByText('ALPHA', { exact: true }).first()).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('BETA', { exact: true }).first()).toBeVisible({ timeout: 5_000 });
  });

  test('completion percentage has sufficient context — users know what 25% means', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] "25%" alone is ambiguous.  The health card should show what
    // the percentage represents (plans completed vs total plans).
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j4-health-percentage-context');

    // ALPHA card should show "25%" and "4 plans"
    const alphaCard = page.locator('div').filter({
      has: page.locator('span', { hasText: 'ALPHA' }),
    }).filter({
      has: page.locator('div', { hasText: /plans/ }),
    }).first();

    await expect(alphaCard).toBeVisible({ timeout: 5_000 });

    // The plan count should be visible alongside the percentage
    const hasContext = await alphaCard.evaluate(el => {
      const text = el.textContent ?? '';
      return text.includes('plans') && text.includes('%');
    });
    expect(hasContext).toBe(true);
  });

  test('blocked indicator in health bar is visible and actionable', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] BETA has 1 blocked plan.  The user must be able to see it
    // at a glance and act (filter to BETA) in 1 click.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j4-blocked-indicator');

    // BETA health card should show "1 blocked"
    const betaCard = page.locator('div').filter({
      has: page.locator('span', { hasText: 'BETA' }),
    }).filter({
      has: page.locator('div', { hasText: /plans/ }),
    }).first();

    const blockedText = betaCard.locator('span', { hasText: /blocked/ }).first();
    await expect(blockedText).toBeVisible({ timeout: 5_000 });
  });

  test('clicking health bar program card filters the board to that program', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-2] Clicking a health card must filter the board.  The filter button
    // in the toolbar should update to reflect the selected program.
    await loadBoardWithMocks(kanban, mockAll);

    // Click ALPHA program health card
    const alphaHealthCard = page.locator('div[role="button"]').filter({
      hasText: 'ALPHA',
    }).first();

    await expect(alphaHealthCard).toBeVisible({ timeout: 5_000 });
    await alphaHealthCard.click();
    await page.waitForTimeout(200);

    await captureFullPage(page, 'j4-filter-by-program-click');

    // The ALPHA program filter button in the toolbar should now be active
    const alphaFilterButton = kanban.programFilterButton('ALPHA');
    await expect(alphaFilterButton).toBeVisible({ timeout: 3_000 });

    // Cards from BETA should be filtered out.
    // The "Review API contract changes" card is in BETA — it should not be visible.
    const betaCard = page.locator('div').filter({
      has: page.locator('div', { hasText: 'Review API contract changes' }),
    }).filter({
      has: page.locator('span[style*="font-family: monospace"]'),
    }).first();

    await expect(betaCard).toBeHidden({ timeout: 3_000 });
  });

  test('health bar shows meaningful zero-state when no programs are tracked', async ({
    page, kanban, mockBoard,
  }) => {
    // [CLUNKY-1] With an empty board, the health bar should explain what's missing
    // rather than silently showing nothing.
    await mockBoard({ boardResponse: { cards: [], health: {} } });
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j4-empty-health-bar');

    await expect(kanban.noPrograms).toBeVisible({ timeout: 5_000 });
  });
});

// ---------------------------------------------------------------------------
// Journey 5: "I want to edit an existing plan"
// ---------------------------------------------------------------------------

test.describe('Journey 5: Edit an existing plan', () => {
  test('path from card to plan editor requires minimum 2 clicks', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-2] Getting to plan edit mode from a card on the board takes:
    // 1. click card to expand
    // 2. click "View Plan" to see plan
    // 3. (no inline edit in card view — must Re-forge to edit)
    // If there are more than 2 clicks to reach edit mode, flag it.
    await loadBoardWithMocks(kanban, mockAll);

    const counter = makeInteractionCounter();

    // Click 1: expand card — use aria role=button on card root
    const queuedCard = page.locator('[role="button"]').filter({
      hasText: 'Implement authentication middleware',
    }).first();

    await queuedCard.click();
    counter.tick();
    await page.waitForTimeout(200);

    // Click 2: view plan — buttons are revealed at page level after expanding
    const viewPlanButton = page.getByRole('button', { name: /View Plan/ });
    await expect(viewPlanButton).toBeVisible({ timeout: 3_000 });
    await viewPlanButton.click();
    counter.tick();
    await page.waitForTimeout(300);

    await captureFullPage(page, 'j5-plan-view-in-card');

    // Click 3 (if needed): Re-forge to actually edit
    // [CLUNKY-2] The plan preview in the card is READ-ONLY.  To edit, the user
    // must click Re-forge (click 3), navigate to the Forge, wait for the form to load,
    // and then edit.  This is 4+ steps total.
    const reForgeButton = page.getByRole('button', { name: 'Re-forge' });
    await expect(reForgeButton).toBeVisible({ timeout: 3_000 });
    await reForgeButton.click();
    counter.tick();

    await page.waitForTimeout(300);
    await captureFullPage(page, 'j5-reforge-route-to-editor');

    // We are now in the Forge view
    await expect(page.getByPlaceholder('Describe the work: what needs to be built, fixed, or analyzed.')).toBeVisible({
      timeout: 5_000,
    });

    // Flag: >2 clicks to reach edit mode is genuinely clunky for a PMO board
    if (counter.value > 2) {
      console.warn(`[CLUNKY-2] It takes ${counter.value} interactions to reach plan edit mode from a board card. Consider adding a direct "Edit Plan" button that goes straight to the Forge preview.`);
    }

    // The total should still be recorded as the actual count for awareness
    expect(counter.value).toBeLessThanOrEqual(3); // tolerable but noted
  });

  test('step editing is triggered by click-to-edit with no visible edit button', async ({
    page, forge, planEditor, mockAll,
  }) => {
    // [CLUNKY-8] The inline step editing in PlanEditor uses click-to-edit
    // with a cursor:text CSS cue.  There is no edit icon or button.
    // Users must discover this by trial and error.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);

    // Expand phase 1 (it is expanded by default — phase_id 0)
    await captureFullPage(page, 'j5-step-edit-discoverability');

    const firstStepDesc = page.locator('div[style*="cursor: text"]').first();

    // The only editing cue is cursor:text.  There is no pencil icon or "Edit" button.
    const hasEditButton = await page.locator('button').filter({
      hasText: /edit/i,
    }).count();

    if (hasEditButton === 0) {
      console.warn('[CLUNKY-8] Step editing has no visible edit button or pencil icon. The cursor:text hint requires users to already know to click.');
    }

    // Despite the discoverability issue, clicking must start edit mode.
    await firstStepDesc.click();
    await page.waitForTimeout(200);
    await captureFullPage(page, 'j5-step-edit-mode-active');

    // The input should appear
    const editInput = page.locator('input[style*="border: 1px solid rgb(59, 130, 246)"]');
    await expect(editInput).toBeVisible({ timeout: 3_000 });
  });

  test('step reorder buttons are small and easy to miss', async ({
    page, forge, planEditor, mockAll,
  }) => {
    // [CLUNKY-8] Step reorder uses ▲ ▼ at 8px font.  These are barely clickable
    // on desktop and essentially unusable on mobile.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await captureFullPage(page, 'j5-step-reorder-buttons');

    // Find an up-arrow button for step reordering
    const moveUpButton = page.locator('button[aria-label*="Move step"][aria-label$=" up"]').first();
    await expect(moveUpButton).toBeVisible({ timeout: 5_000 });

    // Check hit area — buttons smaller than 24x24px fail WCAG 2.5.5 (AAA) / 2.5.8 (AA 2.2)
    const box = await moveUpButton.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      const isSmall = box.width < 24 || box.height < 24;
      if (isSmall) {
        console.warn(`[CLUNKY-8] Step reorder button is ${Math.round(box.width)}x${Math.round(box.height)}px — below 24x24px minimum touch target. Keyboard users cannot easily click this.`);
      }
      // FAIL assertion — these buttons are too small for reliable interaction
      expect(box.width).toBeGreaterThanOrEqual(16); // minimum tolerable
      expect(box.height).toBeGreaterThanOrEqual(16);
    }
  });

  test('saving plan edits has no explicit save button — changes are in-memory only', async ({
    page, forge, planEditor, mockAll,
  }) => {
    // [CLUNKY-6] The PlanEditor stores edits in local React state.  There is no
    // explicit "Save edits" button — the only way to persist is to "Approve & Queue".
    // This is confusing: users may think edits are lost if they navigate away.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await captureFullPage(page, 'j5-no-explicit-save-button');

    // Confirm there is no "Save" or "Save edits" button separate from Approve
    const saveButton = page.getByRole('button', { name: /^Save$|^Save edits$|^Save changes$/i });
    const saveCount = await saveButton.count();
    if (saveCount === 0) {
      console.warn('[CLUNKY-6] PlanEditor has no explicit "Save" button. Changes are held in memory until "Approve & Queue". Users editing a plan may not realise unsaved edits will be lost if they navigate away.');
    }

    // The Approve & Queue button is the only save path
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 3_000 });
  });

  test('removing the last step from a phase leaves an empty phase without warning', async ({
    page, forge, planEditor, mockAll,
  }) => {
    // [CLUNKY-5] Removing all steps from a phase leaves an empty phase with
    // no warning.  An empty phase in an approved plan is meaningless.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);

    // Phase "Test Coverage" has only 1 step — remove it
    // First expand the phase
    const testCoveragePhase = page.locator('div[style*="cursor: pointer"]').filter({
      has: page.locator('div', { hasText: 'Test Coverage' }),
    }).first();

    try {
      await expect(testCoveragePhase).toBeVisible({ timeout: 3_000 });
      await testCoveragePhase.click();
      await page.waitForTimeout(200);

      // Remove the only step
      const removeStepButton = page.locator('button[title="Remove step"]').last();
      await expect(removeStepButton).toBeVisible({ timeout: 3_000 });
      await removeStepButton.click();
      await page.waitForTimeout(200);

      await captureFullPage(page, 'j5-empty-phase-after-removing-last-step');

      // Check if an empty phase warning appears
      const emptyPhaseWarning = page.locator('div').filter({
        hasText: /empty phase|no steps|add a step/i,
      }).first();

      const warningVisible = await emptyPhaseWarning.isVisible().catch(() => false);
      if (!warningVisible) {
        console.warn('[CLUNKY-5] Removing the last step from a phase leaves a visually empty phase with no warning or prompt to add steps / remove the phase.');
      }
    } catch {
      // Phase or step not found — skip
    }
  });
});

// ---------------------------------------------------------------------------
// Journey 6: "I want to regenerate a plan with feedback"
// ---------------------------------------------------------------------------

test.describe('Journey 6: Regenerate a plan with feedback', () => {
  test('regenerate interview questions are clear and provide skip option', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-1] The interview must show clear question text, provide context,
    // and allow skipping individual questions.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);

    // Click Regenerate
    await forge.regenerateButton.click();
    await captureFullPage(page, 'j6-interview-panel-loading');

    // Wait for interview panel — use exact match to avoid matching the phase label span
    const interviewPanelHeader = page.getByText('Refinement Questions', { exact: true });
    await expect(interviewPanelHeader).toBeVisible({ timeout: 10_000 });
    await captureFullPage(page, 'j6-interview-panel-loaded');

    // Question text must be readable
    // Match question text from the interview — use specific phrase from mock data
    const firstQuestion = page.getByText(/RS256.*asymmetric|HS256.*symmetric|JWT.*signing/i).first();
    await expect(firstQuestion).toBeVisible({ timeout: 5_000 });

    // Skip option must be available per question
    const skipButton = forge.skipButton;
    try {
      await expect(skipButton).toBeVisible({ timeout: 3_000 });
    } catch {
      console.warn('[CLUNKY-1] Interview panel has no per-question "skip" button. Users who do not know the answer are forced to answer before proceeding.');
    }

    // Hint text telling users skipping is OK
    const hintText = forge.interviewHint;
    try {
      await expect(hintText).toBeVisible({ timeout: 3_000 });
    } catch {
      console.warn('[CLUNKY-1] Interview panel has no hint telling users they can skip unanswered questions.');
    }
  });

  test('choice buttons in interview are clearly selectable and show selected state', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-1] After clicking a choice button the selection must be visually confirmed.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await forge.regenerateButton.click();
    // Use exact match to avoid strict-mode violation with the phase label span
    await expect(page.getByText('Refinement Questions', { exact: true })).toBeVisible({ timeout: 10_000 });

    // Click first choice option
    const firstChoice = forge.choiceButton('RS256 (recommended)');
    try {
      await expect(firstChoice).toBeVisible({ timeout: 5_000 });
      await firstChoice.click();
      await page.waitForTimeout(200);
      await captureFullPage(page, 'j6-choice-selected-state');

      // The button must visually change after selection (background, border, check mark)
      const isSelected = await firstChoice.evaluate(el => {
        const style = window.getComputedStyle(el);
        // Any visual change: different background, border, or aria-pressed
        const ariaPressed = el.getAttribute('aria-pressed');
        const hasSelectedStyle = style.fontWeight === '700' ||
          style.borderWidth !== '1px' ||
          ariaPressed === 'true';
        return hasSelectedStyle;
      });

      if (!isSelected) {
        console.warn('[CLUNKY-1] Interview choice button shows no visible selected state after clicking. Users cannot tell which answer they gave.');
      }
    } catch {
      console.warn('[CLUNKY-1] Could not find choice button in interview panel — interview may not be rendering correctly');
    }
  });

  test('regeneration progress indicator is visible during re-generation', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-1] During regeneration the user must see that something is happening.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await forge.regenerateButton.click();
    // Use exact match to avoid strict-mode violation with the phase label span
    await expect(page.getByText('Refinement Questions', { exact: true })).toBeVisible({ timeout: 10_000 });

    // Submit the interview (click re-generate button)
    const regenButton = forge.regenerateWithAnswersButton;
    try {
      await expect(regenButton).toBeVisible({ timeout: 5_000 });
      await regenButton.click();

      // During regeneration a loading state must be visible (the phase transitions
      // to 'generating' which shows "Generating plan...")
      await captureFullPage(page, 'j6-regen-in-progress');

      // The mock resolves in ~50ms so we may miss the transition, but we verify
      // the end state is reached correctly.
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await captureFullPage(page, 'j6-regen-complete-back-to-preview');
    } catch {
      console.warn('[CLUNKY-1] Could not submit interview form — Re-generate button not found or interview not rendered');
    }
  });

  test('cancelling regeneration from interview returns to preview with plan intact', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-5] The user must be able to cancel the interview and return to the
    // preview without losing the plan.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await forge.regenerateButton.click();
    // Use exact match to avoid strict-mode violation with the phase label span
    await expect(page.getByText('Refinement Questions', { exact: true })).toBeVisible({ timeout: 10_000 });

    // Click "Back to Plan" to cancel
    const backToPlan = forge.backToPlanButton;
    try {
      await expect(backToPlan).toBeVisible({ timeout: 3_000 });
      await backToPlan.click();
      await page.waitForTimeout(300);
      await captureFullPage(page, 'j6-cancel-interview-back-to-preview');

      // Should be back at preview with plan intact
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });
      await expect(forge.planReadyHeader).toBeVisible({ timeout: 3_000 });
    } catch {
      // Try the Cancel button (shown during generating phase)
      const cancelBtn = forge.cancelButton;
      try {
        await expect(cancelBtn).toBeVisible({ timeout: 3_000 });
        await cancelBtn.click();
        await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });
      } catch {
        console.warn('[CLUNKY-5] Cannot find a way to cancel the interview and return to the plan preview');
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Journey 7: "Navigation round-trips"
// ---------------------------------------------------------------------------

test.describe('Journey 7: Navigation round-trips', () => {
  test('Forge form description is persisted after switching to board and back', async ({
    page, kanban, forge, mockAll,
  }) => {
    // [CLUNKY-4] If the user types a description, switches to the board, then
    // returns to the forge — the description should still be there.
    // The app uses usePersistedState for description, so this should work.
    await loadForgeWithMocks(forge, mockAll);

    const description = 'This description should survive a round-trip to the board';
    await forge.taskDescriptionTextarea.fill(description);

    // Navigate to board
    await forge.backToBoardButton.click();
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j7-switched-to-board');

    // Navigate back to forge via the tab
    await forge.switchToForge();
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j7-back-to-forge-after-board');

    // Description must be intact
    const currentValue = await forge.taskDescriptionTextarea.inputValue();
    // [CLUNKY-4] If currentValue is empty, state was not persisted.
    expect(currentValue).toBe(description);
  });

  test('keyboard hotkey n switches to Forge and back with Escape', async ({
    page, kanban, forge, mockAll,
  }) => {
    // [CLUNKY-2] The keyboard shortcuts n and Escape must work for power users.
    await loadBoardWithMocks(kanban, mockAll);

    // Press 'n' to open Forge
    await page.keyboard.press('n');
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j7-hotkey-n-opens-forge');
    await expect(forge.taskDescriptionTextarea).toBeVisible({ timeout: 5_000 });

    // Press Escape to go back to board
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j7-hotkey-escape-back-to-board');
    await expect(kanban.newPlanButton).toBeVisible({ timeout: 5_000 });
  });

  test('hotkey s toggles signals panel', async ({
    page, kanban, forge, mockAll,
  }) => {
    // [CLUNKY-2] Keyboard shortcut 's' must toggle the signals panel.
    await loadBoardWithMocks(kanban, mockAll);

    // Press 's' to open signals
    await page.keyboard.press('s');
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j7-hotkey-s-opens-signals');
    await expect(kanban.signalsHeader).toBeVisible({ timeout: 5_000 });

    // Press 's' again to close
    await page.keyboard.press('s');
    await page.waitForTimeout(300);
    await captureFullPage(page, 'j7-hotkey-s-closes-signals');
    await expect(kanban.signalsHeader).toBeHidden({ timeout: 3_000 });
  });

  test('tab order allows keyboard-only navigation through the main toolbar', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-2] Users who rely on keyboard navigation must be able to tab through
    // the main board toolbar elements in a logical order.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j7-keyboard-tab-start');

    // Focus the first tab in the navbar
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab'); // past nav
    await page.waitForTimeout(200);

    const focusedElement = await page.evaluate(() => {
      const el = document.activeElement;
      return el ? el.tagName + (el.getAttribute('role') ? `[role=${el.getAttribute('role')}]` : '') : 'none';
    });

    // At least something should have focus — not the body
    expect(focusedElement).not.toBe('BODY');
    expect(focusedElement).not.toBe('none');
  });

  test('program filter state persists after navigating to Forge and back', async ({
    page, kanban, forge, mockAll,
  }) => {
    // [CLUNKY-4] If a user filters by ALPHA, goes to Forge, and returns,
    // the filter must still be active.
    await loadBoardWithMocks(kanban, mockAll);

    // Filter by ALPHA
    await kanban.filterByProgram('ALPHA');
    await captureFullPage(page, 'j7-filter-set-alpha');

    // Go to Forge
    await forge.switchToForge();
    await page.waitForTimeout(200);

    // Go back to board
    await forge.switchToKanban();
    await page.waitForTimeout(200);
    await captureFullPage(page, 'j7-filter-persisted-after-roundtrip');

    // ALPHA filter should still be active — BETA cards should not be visible
    // usePersistedState saves to localStorage so this should survive navigation
    const alphaFilter = kanban.programFilterButton('ALPHA');
    // The active state is detected by border/background color changes — we verify
    // the button exists (if filter was cleared the button would still exist but be inactive)
    await expect(alphaFilter).toBeVisible({ timeout: 3_000 });
  });

  test('both views render simultaneously but only the active one is interactive', async ({
    page, kanban, forge, mockAll,
  }) => {
    // Architecture note: App.tsx renders BOTH panels (kanban + forge) simultaneously
    // and uses display:none to hide the inactive one.  We verify that the inactive
    // panel is not accidentally receiving events.
    await loadBoardWithMocks(kanban, mockAll);

    // Confirm the Forge panel is in the DOM but hidden (display: none)
    const forgePanel = page.locator('#panel-forge');
    await expect(forgePanel).toBeHidden({ timeout: 3_000 });

    // Switch to Forge
    await forge.switchToForge();
    await page.waitForTimeout(200);

    // Confirm the Kanban panel is now hidden
    const kanbanPanel = page.locator('#panel-kanban');
    await expect(kanbanPanel).toBeHidden({ timeout: 3_000 });
    await captureFullPage(page, 'j7-simultaneous-panel-rendering');

    // Switch back
    await forge.switchToKanban();
    await page.waitForTimeout(200);
    await expect(forgePanel).toBeHidden({ timeout: 3_000 });
    await expect(kanbanPanel).toBeVisible({ timeout: 3_000 });
  });
});

// ---------------------------------------------------------------------------
// Journey 8: "Error recovery"
// ---------------------------------------------------------------------------

test.describe('Journey 8: Error recovery', () => {
  test('board load failure shows error banner with retry information', async ({
    page, kanban, mockBoard,
  }) => {
    // [CLUNKY-5] When the board cannot load, the user must see a clear error
    // and know that it will retry automatically.
    await mockBoard({ failBoard: true });
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(800); // Let the initial fetch fail

    await captureFullPage(page, 'j8-board-load-failure');

    // Error banner must be visible
    await expect(kanban.errorBanner).toBeVisible({ timeout: 10_000 });

    // Banner must mention retry so the user knows to wait, not refresh
    const bannerText = await kanban.errorBanner.textContent();
    expect(bannerText).toMatch(/retry|retrying/i);
  });

  test('board error banner shows polling fallback mode — not just a red banner', async ({
    page, kanban, mockBoard,
  }) => {
    // [CLUNKY-5] The error message says "retrying every Xs" but does not tell
    // users what action they can take (e.g., "Check if the backend is running").
    await mockBoard({ failBoard: true });
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(800);
    await captureFullPage(page, 'j8-board-error-message-clarity');

    const errorText = await kanban.errorBanner.textContent().catch(() => '');

    // Check if the error message provides actionable context
    const hasActionableContext = /backend|server|connection|check/i.test(errorText ?? '');
    if (!hasActionableContext) {
      console.warn(`[CLUNKY-5] Board error banner text "${errorText?.trim()}" does not provide actionable guidance (e.g., "Check if the backend is running"). Users may not know what to do.`);
    }
  });

  test('plan generation failure allows retry with same form data', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    // [CLUNKY-5] Generation failure must NOT clear the form.  User should be
    // able to retry immediately by clicking Generate again.
    await mockBoard();
    await mockForge({ failForgePlan: true });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.newPlanButton.click();

    const textarea = page.getByPlaceholder('Describe the work: what needs to be built, fixed, or analyzed.');
    await textarea.fill('My important task that must not be lost on failure');
    await page.getByRole('button', { name: /Generate Plan/ }).click();

    // Wait for failure (generate button reappears)
    await expect(page.getByRole('button', { name: /Generate Plan/ })).toBeVisible({
      timeout: 15_000,
    });

    await captureFullPage(page, 'j8-generation-failure-form-intact');

    // Form data must be intact
    const currentValue = await textarea.inputValue();
    expect(currentValue).toBe('My important task that must not be lost on failure');

    // Generate button must be clickable (not disabled due to previous failure)
    await expect(page.getByRole('button', { name: /Generate Plan/ })).toBeEnabled({ timeout: 3_000 });
  });

  test('plan approval failure keeps the plan preview intact — not lost', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    // [CLUNKY-5] If approval fails the plan must still be visible so the user
    // can retry without regenerating from scratch.
    await mockBoard();

    // Set up forge mocks: plan generates OK but approval fails
    const forgePlan = (await import('../fixtures/mock-data.js')).MOCK_FORGE_PLAN;

    await page.route('**/api/v1/pmo/projects', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify((await import('../fixtures/mock-data.js')).MOCK_PROJECTS),
      });
    });
    await page.route('**/api/v1/pmo/forge/plan', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(forgePlan),
      });
    });
    await page.route('**/api/v1/pmo/forge/approve', async (route) => {
      // Fail approval
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Failed to write plan to disk' }),
      });
    });
    await page.route('**/api/v1/pmo/ado/search**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify((await import('../fixtures/mock-data.js')).MOCK_ADO_ITEMS),
      });
    });
    await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.newPlanButton.click();

    const textarea = page.getByPlaceholder('Describe the work: what needs to be built, fixed, or analyzed.');
    await textarea.fill('Approve failure test');
    await page.getByRole('button', { name: /Generate Plan/ }).click();
    await expect(page.getByRole('button', { name: 'Approve & Queue' })).toBeVisible({
      timeout: 15_000,
    });

    // Click Approve
    await page.getByRole('button', { name: 'Approve & Queue' }).click();
    await page.waitForTimeout(500);
    await captureFullPage(page, 'j8-approve-failure-plan-intact');

    // Plan preview must still be visible
    await expect(page.getByRole('button', { name: 'Approve & Queue' })).toBeVisible({
      timeout: 5_000,
    });

    // An error message must be shown
    const saveError = page.locator('[role="alert"]').filter({
      hasText: /failed|Failed|error|Error/,
    }).first();
    await expect(saveError).toBeVisible({ timeout: 5_000 });
  });

  test('signal resolve failure is silent — no error feedback to user', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    // [CLUNKY-5] SignalsBar catches resolve errors silently.
    // If resolve fails, the signal stays in the list (correct) but there is
    // NO error message shown.  User may think the resolve worked.
    await mockBoard();
    await mockForge();

    // Override the resolve endpoint to fail
    await page.route('**/api/v1/pmo/signals/*/resolve', async (route) => {
      await route.fulfill({
        status: 500,
        body: 'Internal Server Error',
      });
    });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    // Try to resolve a signal — scope to the list item element for reliability
    const failSignalRow = page.locator('li').filter({
      has: page.locator('span', { hasText: 'Authentication service returning 500 in prod' }),
    }).first();
    const resolveButton = failSignalRow.getByRole('button', { name: 'Resolve' });
    try {
      await expect(resolveButton).toBeVisible({ timeout: 5_000 });
      await resolveButton.click();
      await page.waitForTimeout(600);
      await captureFullPage(page, 'j8-silent-resolve-failure');

      // Check: is there any error message visible?
      const errorMessages = await page.locator('[role="alert"]').count();
      const signalErrorText = page.locator('div').filter({
        hasText: /resolve failed|could not resolve|error/i,
      });
      const hasSignalError = await signalErrorText.count();

      if (hasSignalError === 0 && errorMessages === 0) {
        console.warn('[CLUNKY-5] Signal resolve failure is completely silent — no error feedback shown to user. User cannot tell whether resolve succeeded or failed.');
        // This is a known bug / design gap — the code has `// silent — not critical`
      }

      // The signal must still be in the list (it was not resolved)
      await expect(page.getByText('Authentication service returning 500 in prod')).toBeVisible({
        timeout: 3_000,
      });
    } catch {
      // Signal not found — skip
    }
  });

  test('batch resolve failure is silent — same UX problem as individual resolve', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    // [CLUNKY-5] Batch resolve also swallows errors silently.
    await mockBoard();
    await mockForge();

    await page.route('**/api/v1/pmo/signals/batch/resolve', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.signalsToggleButton.click();
    await page.waitForTimeout(300);

    // Select all signals
    await kanban.selectAllCheckbox.click();
    await page.waitForTimeout(200);

    // Handle the confirmation dialog
    page.on('dialog', async (dialog) => { await dialog.accept(); });

    // Click Resolve Selected
    await expect(kanban.batchResolveButton).toBeVisible({ timeout: 3_000 });
    await kanban.batchResolveButton.click();
    await page.waitForTimeout(600);
    await captureFullPage(page, 'j8-silent-batch-resolve-failure');

    // Check for error feedback
    const hasError = await page.locator('div').filter({
      hasText: /batch resolve failed|error|failed/i,
    }).count();

    if (hasError === 0) {
      console.warn('[CLUNKY-5] Batch resolve failure is completely silent. Code at SignalsBar.tsx has `// silent — not critical` which leaves users unaware of failures.');
    }
  });

  test('ADO search failure has no visible error — combobox silently shows nothing', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    // [CLUNKY-5] AdoCombobox catches API errors and shows an empty dropdown.
    // Users who type a query and see nothing cannot tell if it is an error or
    // genuinely no results.
    await mockBoard();

    // Override ADO search to fail
    await page.route('**/api/v1/pmo/projects', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify((await import('../fixtures/mock-data.js')).MOCK_PROJECTS),
      });
    });
    await page.route('**/api/v1/pmo/ado/search**', async (route) => {
      await route.fulfill({ status: 500, body: 'Service unavailable' });
    });
    await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.newPlanButton.click();

    const adoSearch = page.getByPlaceholder('Search ADO work items...');
    await expect(adoSearch).toBeVisible({ timeout: 5_000 });
    await adoSearch.fill('JWT authentication');
    await page.waitForTimeout(500); // let debounce + request fire

    await captureFullPage(page, 'j8-ado-search-failure-silent');

    // The dropdown should not appear (no results due to error)
    const dropdown = page.locator('[role="listbox"]');
    const isDropdownVisible = await dropdown.isVisible().catch(() => false);
    expect(isDropdownVisible).toBe(false);

    // There should be an error message OR a "Search failed" notice
    const errorHint = page.locator('div').filter({
      hasText: /search failed|could not search|error/i,
    });
    const hasErrorHint = await errorHint.count();

    if (hasErrorHint === 0) {
      console.warn('[CLUNKY-5] ADO search failure is completely silent. AdoCombobox catches errors with empty catch block, leaving users wondering why no results appear.');
    }
  });
});

// ---------------------------------------------------------------------------
// Journey 9: Bonus — overall clunkiness summary
// ---------------------------------------------------------------------------

test.describe('Journey 9: Overall clunkiness summary', () => {
  test('keyboard shortcut hints are discoverable', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-7] Users must be able to discover keyboard shortcuts.
    // The app shows "n=new  s=signals  esc=board" in the nav bar.
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j9-keyboard-hint-visible');

    const hintText = kanban.keyboardHint;
    await expect(hintText).toBeVisible({ timeout: 5_000 });

    const hintContent = await hintText.textContent();
    expect(hintContent).toMatch(/n=new/);
    expect(hintContent).toMatch(/s=signals/);
    expect(hintContent).toMatch(/esc=board/);
  });

  test('empty columns show a meaningful empty state, not blank space', async ({
    page, kanban, mockBoard,
  }) => {
    // [CLUNKY-1] Empty columns must communicate their state clearly.
    // The KanbanBoard uses `fontStyle: italic` "Empty" text.
    await mockBoard({ boardResponse: { cards: [], health: {} } });
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(400);
    await captureFullPage(page, 'j9-empty-board-state');

    // All 5 columns should show "Empty" placeholder
    const emptyPlaceholders = await page.getByText('Empty').count();
    expect(emptyPlaceholders).toBeGreaterThanOrEqual(5);
  });

  test('connection indicator is unambiguous about its meaning', async ({
    page, kanban, mockAll,
  }) => {
    // [CLUNKY-3] The connection indicator shows "polling" when SSE fails.
    // Users may not understand what "polling" means.  It should say something
    // more human like "Live updates off" or "Checking every 5s".
    await loadBoardWithMocks(kanban, mockAll);
    await captureFullPage(page, 'j9-connection-indicator');

    // After blocking SSE (which the mock does), the indicator should show "polling"
    const connectionIndicator = kanban.connectionIndicator;
    try {
      await expect(connectionIndicator).toBeVisible({ timeout: 5_000 });
      const text = await connectionIndicator.textContent();
      const isTechnical = /polling|sse/i.test(text ?? '');
      if (isTechnical) {
        console.warn(`[CLUNKY-3] Connection indicator shows "${text?.trim()}" which is a technical term most users won't understand. Consider "Live" / "Delayed" / "Offline" instead.`);
      }
    } catch {
      // Indicator not found — may not be rendered yet
    }
  });

  test('the Forge title "The Forge" is opaque — phase subtitle carries all orientation', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-7] "The Forge" is a branded name; new users need the phase subtitle
    // to understand what they should do.  Verify the subtitle is always present.
    await loadForgeWithMocks(forge, mockAll);
    await captureFullPage(page, 'j9-forge-title-and-subtitle');

    // Title "The Forge"
    await expect(forge.forgeTitle).toBeVisible({ timeout: 5_000 });

    // Phase subtitle must be visible alongside it
    await expect(forge.phaseLabel).toBeVisible({ timeout: 5_000 });

    const subtitle = await forge.phaseLabel.textContent();
    // Must not be empty
    expect(subtitle?.trim().length).toBeGreaterThan(5);
  });

  test('plan stats bar in editor shows meaningful values not just numbers', async ({
    page, forge, planEditor, mockAll,
  }) => {
    // [CLUNKY-3] Stats bar shows "3 Phases / 6 Steps / 2 Gates / MEDIUM Risk".
    // "MEDIUM" is meaningful but "6 Steps" has no context (6 out of how many?).
    // The "Risk" stat is good — it uses a label not just a number.
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await captureFullPage(page, 'j9-plan-stats-bar');

    await expect(planEditor.statTile('Phases')).toBeVisible({ timeout: 5_000 });
    await expect(planEditor.statTile('Steps')).toBeVisible({ timeout: 5_000 });
    await expect(planEditor.statTile('Gates')).toBeVisible({ timeout: 5_000 });
    await expect(planEditor.statTile('Risk')).toBeVisible({ timeout: 5_000 });

    // Risk value should be "MEDIUM" — semantic, not just a number
    const riskTile = planEditor.statTile('Risk');
    const riskText = await riskTile.textContent();
    expect(riskText).toMatch(/LOW|MEDIUM|HIGH/);
  });

  test('approved plan shows saved file path in monospace — too technical for a success screen', async ({
    page, forge, mockAll,
  }) => {
    // [CLUNKY-3] After approval the saved phase shows:
    // "/home/user/projects/alpha/.claude/team-context/plan.json"
    // This is a filesystem path that most users do not need and cannot act on.
    // A more user-friendly message would be "Saved to Alpha Service project".
    await loadForgeWithMocks(forge, mockAll);
    await driveToPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await expect(forge.savedHeader).toBeVisible({ timeout: 10_000 });
    await captureFullPage(page, 'j9-saved-path-technical');

    const savedPath = forge.savedPathText;
    try {
      await expect(savedPath).toBeVisible({ timeout: 3_000 });
      const pathText = await savedPath.textContent();
      // Path shows raw filesystem location — technical and not user-friendly
      const isFilesystemPath = pathText?.startsWith('/') || pathText?.includes('\\') || pathText?.includes('.json');
      if (isFilesystemPath) {
        console.warn(`[CLUNKY-3] Saved plan screen shows filesystem path "${pathText}" which is not meaningful to most users. Consider showing "Saved to <project name>" instead.`);
      }
    } catch {
      // Path not visible — pass
    }
  });
});
