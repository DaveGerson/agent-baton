/**
 * forge-back-to-plan.spec.ts — Verifies the "Back to Plan" escape hatch
 * from the ForgePanel interview (regenerating) phase.
 *
 * Covers two entry points for the back navigation:
 *   1. The button added above the InterviewPanel (top of regenerating phase).
 *   2. The button inside InterviewPanel's own action row (bottom of the form).
 *
 * Both must return to the preview phase with the existing plan intact.
 *
 * Run with:
 *   npx playwright test e2e/tests/forge-back-to-plan.spec.ts --project=desktop
 */

/// <reference types="node" />
import { test, expect } from '../fixtures/test-fixtures.js';

// ---------------------------------------------------------------------------
// Shared setup helper
// ---------------------------------------------------------------------------

/**
 * Navigate to the Forge preview phase with the mock plan loaded.
 * Returns when "Approve & Queue" is visible.
 */
async function loadPreviewPhase(
  forge: import('../pages/ForgePage.js').ForgePage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await mockAll();
  await forge.goto('/');
  await forge.waitForAppReady();
  await forge.switchToForge();
  await forge.assertIntakePhase();
  await forge.fillAndGenerate('Add JWT authentication to the API gateway');
  await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
}

/**
 * Click "Regenerate" and wait for the interview panel to appear.
 * The /forge/interview mock returns immediately so the transition is fast.
 */
async function enterInterviewPhase(
  forge: import('../pages/ForgePage.js').ForgePage,
): Promise<void> {
  await forge.regenerateButton.click();
  await forge.assertRegeneratingPhase();
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Forge — Back to Plan from interview phase', () => {
  /**
   * Test 1: "Back to Plan" button is visible at the top of the interview phase.
   *
   * This button was added above the InterviewPanel so users can escape without
   * scrolling to the bottom of a long question list.
   */
  test('back-to-plan button is visible above the interview form', async ({ forge, mockAll }) => {
    await loadPreviewPhase(forge, mockAll);
    await enterInterviewPhase(forge);

    // The top-level back button rendered in ForgePanel (above InterviewPanel)
    // and the one inside InterviewPanel both have the same accessible name.
    // We assert the first one (top of DOM) is immediately visible.
    const backButtons = forge.page.getByRole('button', { name: /Back to Plan/ });
    // At least two should be present: one above the form, one inside it.
    await expect(backButtons.first()).toBeVisible();
    const count = await backButtons.count();
    expect(count).toBeGreaterThanOrEqual(2);
  });

  /**
   * Test 2: The top "Back to Plan" button returns to the preview phase.
   *
   * Clicking it should unmount the InterviewPanel and restore the plan
   * preview (Approve & Queue visible, interview header gone).
   */
  test('top back-to-plan button returns to preview with plan intact', async ({ forge, mockAll }) => {
    await loadPreviewPhase(forge, mockAll);
    await enterInterviewPhase(forge);

    // Click the first (top-level) Back to Plan button.
    await forge.page.getByRole('button', { name: /Back to Plan/ }).first().click();

    // Preview phase must be restored.
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });
    await expect(forge.regenerateButton).toBeVisible();

    // Interview panel must be gone.
    await expect(forge.interviewHeader).toBeHidden();
  });

  /**
   * Test 3: The InterviewPanel's own "Back to Plan" button (bottom of form)
   * also returns to preview.
   *
   * This is the existing onCancel button inside InterviewPanel — verifying
   * it still works after the ForgePanel wrapper was added.
   */
  test('interview panel cancel button returns to preview with plan intact', async ({ forge, mockAll }) => {
    await loadPreviewPhase(forge, mockAll);
    await enterInterviewPhase(forge);

    // Use the ForgePage helper which targets the button by accessible name.
    await forge.backToPlanButton.click();

    // Preview phase must be restored.
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });
    await expect(forge.regenerateButton).toBeVisible();

    // Interview panel must be gone.
    await expect(forge.interviewHeader).toBeHidden();
  });

  /**
   * Test 4: After returning to preview, the plan can still be approved.
   *
   * Ensures the plan state is fully intact — not partially mutated — after
   * navigating away from the interview without submitting.
   */
  test('plan can be approved after cancelling the interview', async ({ forge, mockAll }) => {
    await loadPreviewPhase(forge, mockAll);
    await enterInterviewPhase(forge);

    // Cancel the interview.
    await forge.page.getByRole('button', { name: /Back to Plan/ }).first().click();
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });

    // Approve the plan — should succeed with the mock.
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();
  });

  /**
   * Test 5: Re-entering the interview after cancelling works correctly.
   *
   * Guards against state leaks where a cancelled interview poisons the next
   * interview fetch (e.g. stale questions array).
   */
  test('can re-enter interview after cancelling once', async ({ forge, mockAll }) => {
    await loadPreviewPhase(forge, mockAll);

    // First interview entry — cancel it.
    await enterInterviewPhase(forge);
    await forge.page.getByRole('button', { name: /Back to Plan/ }).first().click();
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });

    // Second interview entry — should load normally.
    await enterInterviewPhase(forge);
    await expect(forge.interviewHeader).toBeVisible();
    await expect(forge.interviewHint).toBeVisible();
  });
});
