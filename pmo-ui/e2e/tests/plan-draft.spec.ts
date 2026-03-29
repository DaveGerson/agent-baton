/**
 * plan-draft.spec.ts — Save Draft feature for the PlanEditor.
 *
 * Covers:
 *   1. "Save Draft" button is visible in the preview phase stats bar.
 *   2. Clicking "Save Draft" shows "Saved ✓" confirmation for ~2 seconds.
 *   3. The orange dirty-dot appears after editing a step description.
 *   4. Navigating away and back to preview shows the draft restore banner.
 *   5. Clicking "Restore" loads the saved draft into the editor.
 *   6. Clicking "Dismiss" hides the banner and clears the draft from storage.
 */

/// <reference types="node" />

import { test, expect } from '../fixtures/test-fixtures.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Navigate to Forge, generate a plan (mocked), and wait for the preview phase.
 */
async function openForgePreview(
  forge: import('../pages/ForgePage.js').ForgePage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await mockAll();
  await forge.goto('/');
  await forge.waitForAppReady();
  await forge.switchToForge();
  await forge.assertIntakePhase();
  await forge.fillAndGenerate('Add JWT authentication to the API gateway');
  await forge.assertPreviewPhase();
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('PlanEditor — Save Draft', () => {
  /**
   * Test 1: "Save Draft" button is visible in the stats bar when the
   * preview phase is active.
   */
  test('save draft button is visible in preview phase', async ({ forge, mockAll }) => {
    await openForgePreview(forge, mockAll);

    const saveDraftButton = forge.page.getByRole('button', { name: /Save Draft/i });
    await expect(saveDraftButton).toBeVisible({ timeout: 5_000 });
  });

  /**
   * Test 2: Editing a step description makes the dirty-dot appear.
   * The orange dot is a 4px span with aria-label="unsaved changes".
   */
  test('dirty dot appears after editing a step description', async ({ forge, planEditor, mockAll }) => {
    await openForgePreview(forge, mockAll);

    // The first phase (Design & Schema) is already expanded by default.
    // Click the first step description to enter edit mode.
    const firstStepDesc = 'Define JWT token schema';
    await planEditor.startEditStep(firstStepDesc);

    // Overwrite with new text.
    await planEditor.activeStepInput.selectText();
    await planEditor.activeStepInput.fill('Updated JWT schema description');
    await planEditor.activeStepInput.press('Enter');

    // Dirty dot should now be visible.
    const dirtyDot = forge.page.locator('span[aria-label="unsaved changes"]');
    await expect(dirtyDot).toBeVisible({ timeout: 3_000 });
  });

  /**
   * Test 3: Clicking "Save Draft" shows "Saved ✓" confirmation and writes
   * to localStorage.
   */
  test('clicking save draft shows confirmation and persists to storage', async ({
    forge,
    mockAll,
  }) => {
    await openForgePreview(forge, mockAll);

    // Clear any pre-existing draft so the test starts clean.
    await forge.page.evaluate(() => localStorage.removeItem('pmo:plan-draft'));

    const saveDraftButton = forge.page.getByRole('button', { name: /Save Draft/i });
    await saveDraftButton.click();

    // Button should briefly show "Saved ✓".
    await expect(
      forge.page.getByRole('button', { name: /Saved/i }),
    ).toBeVisible({ timeout: 3_000 });

    // localStorage key should now exist with valid JSON.
    const draftJson = await forge.page.evaluate(() =>
      localStorage.getItem('pmo:plan-draft'),
    );
    expect(draftJson).not.toBeNull();
    const draft = JSON.parse(draftJson!);
    expect(draft).toHaveProperty('task_id');
    expect(draft).toHaveProperty('phases');
  });

  /**
   * Test 4: Navigate away (back to board) and back to Forge — the draft
   * restore banner must appear.
   *
   * This simulates the canonical "user saves draft, leaves, comes back"
   * flow. Because the app is a SPA, "navigating away" means switching
   * to the kanban view and then opening Forge again.
   */
  test('draft restore banner appears after navigating away and back', async ({
    forge,
    kanban,
    mockAll,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();

    // -- Open Forge and generate a plan --
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await forge.fillAndGenerate('Add rate limiting to public endpoints');
    await forge.assertPreviewPhase();

    // -- Save the draft --
    const saveDraftButton = forge.page.getByRole('button', { name: /Save Draft/i });
    await saveDraftButton.click();
    // Wait for the confirmation to appear (proves the write completed).
    await expect(forge.page.getByRole('button', { name: /Saved/i })).toBeVisible({
      timeout: 3_000,
    });

    // -- Navigate back to the kanban board --
    // The "dirty plan" guard asks for confirmation — accept it.
    forge.page.once('dialog', dialog => dialog.accept());
    await forge.goBackToBoard();
    await kanban.assertAllColumnsVisible();

    // -- Open Forge again (generates a new plan to enter preview) --
    await kanban.clickNewPlan();
    await forge.assertIntakePhase();
    await forge.fillAndGenerate('Add rate limiting to public endpoints');
    await forge.assertPreviewPhase();

    // -- Draft restore banner must be visible --
    const draftBanner = forge.page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeVisible({ timeout: 5_000 });
    await expect(draftBanner).toContainText('Draft available');
    await expect(draftBanner.getByRole('button', { name: 'Restore' })).toBeVisible();
    await expect(draftBanner.getByRole('button', { name: 'Dismiss' })).toBeVisible();
  });

  /**
   * Test 5: Clicking "Restore" on the banner loads the saved draft into
   * the plan editor and hides the banner.
   */
  test('restore button loads the saved draft into the editor', async ({
    forge,
    mockAll,
    planEditor,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    // Manually seed a draft into localStorage before opening the preview.
    // This avoids the round-trip and makes the test deterministic.
    const draftPlan = {
      task_id: 'task-draft-restore-test',
      task_summary: 'Restored draft summary — unique marker',
      risk_level: 'LOW',
      budget_tier: 'economy',
      execution_mode: 'sequential',
      git_strategy: 'feature-branch',
      shared_context: '',
      pattern_source: null,
      created_at: '2025-03-28T12:00:00Z',
      phases: [
        {
          phase_id: 0,
          name: 'Draft Phase',
          steps: [
            {
              step_id: '1.1',
              agent_name: 'frontend-engineer',
              task_description: 'Draft step — should appear after restore',
              model: 'haiku',
              depends_on: [],
              deliverables: [],
              allowed_paths: [],
              blocked_paths: [],
              context_files: [],
            },
          ],
        },
      ],
    };
    await forge.page.evaluate(
      (plan) => localStorage.setItem('pmo:plan-draft', JSON.stringify(plan)),
      draftPlan,
    );

    // Generate a new (different) plan so the preview has something to replace.
    await forge.fillAndGenerate('Original description — not the draft');
    await forge.assertPreviewPhase();

    // The banner should be visible because we seeded a draft.
    const draftBanner = forge.page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeVisible({ timeout: 5_000 });

    // Click Restore.
    await draftBanner.getByRole('button', { name: 'Restore' }).click();

    // Banner should disappear after restore.
    await expect(draftBanner).toBeHidden({ timeout: 3_000 });

    // The summary block should now contain the draft summary text.
    await expect(forge.planSummaryBlock).toContainText('Restored draft summary', {
      timeout: 3_000,
    });

    // The phase name from the draft should be visible in the editor.
    await expect(planEditor.phaseHeader('Draft Phase')).toBeVisible({ timeout: 3_000 });
  });

  /**
   * Test 6: Clicking "Dismiss" hides the banner and removes the draft
   * from localStorage.
   */
  test('dismiss button hides the banner and clears the draft', async ({
    forge,
    mockAll,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    // Seed a draft.
    await forge.page.evaluate(() =>
      localStorage.setItem(
        'pmo:plan-draft',
        JSON.stringify({ task_id: 'draft-dismiss-test', phases: [] }),
      ),
    );

    await forge.fillAndGenerate('Some task description');
    await forge.assertPreviewPhase();

    const draftBanner = forge.page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeVisible({ timeout: 5_000 });

    // Dismiss.
    await draftBanner.getByRole('button', { name: 'Dismiss' }).click();

    // Banner must be gone.
    await expect(draftBanner).toBeHidden({ timeout: 3_000 });

    // localStorage key must be cleared.
    const draftAfter = await forge.page.evaluate(() =>
      localStorage.getItem('pmo:plan-draft'),
    );
    expect(draftAfter).toBeNull();
  });
});
