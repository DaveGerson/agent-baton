/**
 * forge-saved-phase.spec.ts — Focused tests for R2-10 and R2-23.
 *
 *   R2-10: Double-submit guard on "Approve & Queue" — the second click must
 *          be a no-op; exactly one API call is sent regardless of click speed.
 *
 *   R2-23: SavedPhase sub-component — verifies every interactive element and
 *          piece of rendered output in the saved phase:
 *            - Checkmark and "Plan Saved & Queued" heading
 *            - Filename extracted from the full save path (monospace)
 *            - "Start Execution" button launches and shows PID status
 *            - "Start Execution" button becomes disabled after a successful launch
 *            - Execution error rendered when the launch API returns an error
 *            - "New Plan" button resets the forge back to intake
 *            - "Back to Board" button in the saved phase returns to kanban
 *
 * Run with:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3100/pmo/ npx playwright test e2e/tests/forge-saved-phase.spec.ts --project=desktop
 */

/// <reference types="node" />

import { test, expect } from '../fixtures/test-fixtures.js';

// ---------------------------------------------------------------------------
// Shared helper — navigate to preview phase
// ---------------------------------------------------------------------------

/**
 * Navigate to Forge, fill the intake form, generate a plan (mocked), and wait
 * until the preview phase is ready ("Approve & Queue" visible).
 *
 * Routes MUST be installed before calling this (pass `mockForge` or `mockAll`).
 *
 * Mirrors the `loadPlanEditor` helper used in functionality-breaks.spec.ts so
 * the two suites are consistent.
 */
async function loadPreviewPhase(
  forge: import('../pages/ForgePage.js').ForgePage,
): Promise<void> {
  await forge.goto('/');
  await forge.waitForAppReady();
  await forge.switchToForge();
  await forge.assertIntakePhase();
  await forge.fillAndGenerate('Implement JWT authentication middleware');
  await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
  // Short settle — mirrors the 300 ms pause in functionality-breaks helpers.
  await forge.page.waitForTimeout(300);
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Forge — Saved Phase & Double-Submit Guard', () => {
  // -------------------------------------------------------------------------
  // R2-05 / R2-10 : button disabled during in-flight approve request
  // -------------------------------------------------------------------------

  test('R2-05: Approve button shows "Queuing…" and is disabled while save is in flight', async ({
    page, forge, mockForge,
  }) => {
    // Override approve with a deliberate delay so we can inspect mid-flight state.
    await mockForge();
    await page.route('**/api/v1/pmo/forge/approve', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ saved: true, path: '/tmp/plan.json' }),
      });
    });

    await loadPreviewPhase(forge);

    await forge.approveAndQueueButton.click();

    // Button should transition to "Queuing…" text and be disabled.
    const queuingBtn = page.getByRole('button', { name: /Queuing/i });
    await expect(queuingBtn).toBeVisible({ timeout: 2_000 });
    await expect(queuingBtn).toBeDisabled();
  });

  test('R2-10: rapid double-click on Approve sends exactly one API call', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    let approveCallCount = 0;
    await page.route('**/api/v1/pmo/forge/approve', async (route) => {
      approveCallCount++;
      await new Promise(resolve => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ saved: true, path: '/tmp/plan.json' }),
      });
    });

    await loadPreviewPhase(forge);

    // First click — kicks off the save.
    await forge.approveAndQueueButton.click();

    // 50 ms later the button should be disabled ("Queuing…") — attempt a second
    // click via the force option.  The button must be disabled so the second
    // click is a no-op.
    await page.waitForTimeout(50);
    const queuingBtn = page.getByRole('button', { name: /Queuing/i });
    const isDisabledAfterFirst = await queuingBtn.isDisabled().catch(() => false);
    expect(isDisabledAfterFirst).toBe(true);

    // Force-click the disabled button to confirm the guard ignores it.
    await queuingBtn.click({ force: true }).catch(() => {/* disabled buttons swallow clicks */});

    // Wait for the save to complete.
    await page.waitForTimeout(700);
    await forge.assertSavedPhase();

    // Exactly one network call — not two.
    expect(approveCallCount).toBe(1);
  });

  // -------------------------------------------------------------------------
  // R2-23 : SavedPhase component rendering
  // -------------------------------------------------------------------------

  test('R2-23: saved phase shows "Plan Saved & Queued" heading and checkmark', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    await loadPreviewPhase(forge);

    await forge.approveAndQueueButton.click();

    await forge.assertSavedPhase();

    // savedHeader uses getByText('Plan Saved & Queued')
    await expect(forge.savedHeader).toBeVisible({ timeout: 5_000 });

    // savedCheckmark matches the ✓ inside a border-radius:50% div
    await expect(forge.savedCheckmark).toBeVisible();
  });

  test('R2-23: saved phase shows filename extracted from full save path', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    // Override with a custom deep path so we can assert only the filename portion.
    await page.route('**/api/v1/pmo/forge/approve', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ saved: true, path: '/home/user/.claude/team-context/plan.json' }),
      });
    });

    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();

    // savedPathText is the monospace div — it should show just the filename.
    await expect(forge.savedPathText).toBeVisible({ timeout: 5_000 });
    // The component strips everything before the last "/" — assert filename only.
    await expect(forge.savedPathText).toHaveText('plan.json');
  });

  test('R2-23: Start Execution button launches and shows PID in status area', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    // Override execute to return a known PID.
    await page.route('**/api/v1/pmo/execute/**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ task_id: 'task-forge-001', pid: 54321, status: 'launched', model: 'claude-sonnet-4-5', dry_run: false }),
      });
    });

    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();

    await forge.startExecutionButton.click();

    // The component sets execResult to "Execution launched (PID N)".
    // Multiple role="status" elements exist on the page so we target the
    // text directly — it is unique once execResult is set.
    await expect(page.getByText(/Execution launched.*PID 54321/)).toBeVisible({ timeout: 5_000 });
  });

  test('R2-23: Start Execution button is disabled after a successful launch', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    await page.route('**/api/v1/pmo/execute/**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ task_id: 'task-forge-001', pid: 99, status: 'launched', model: 'claude-sonnet-4-5', dry_run: false }),
      });
    });

    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();

    await forge.startExecutionButton.click();

    // After a successful launch the component sets execResult to "Execution launched (PID N)".
    await expect(page.getByText(/Execution launched/)).toBeVisible({ timeout: 5_000 });

    // The button disables once execResult starts with "Execution launched" to prevent re-launch.
    await expect(forge.startExecutionButton).toBeDisabled({ timeout: 3_000 });
  });

  test('R2-23: Start Execution error is shown when launch API fails', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    await page.route('**/api/v1/pmo/execute/**', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Backend unavailable' }),
      });
    });

    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();

    await forge.startExecutionButton.click();

    // On failure the component renders err.message (or "Launch failed") in the status div.
    // The rendered text must not be an "Execution launched" success message.
    const errorText = page.getByText(/Launch failed|Backend unavailable|500/);
    await expect(errorText).toBeVisible({ timeout: 5_000 });

    // The button must NOT be disabled on error — user can retry.
    await expect(forge.startExecutionButton).not.toBeDisabled({ timeout: 3_000 });
  });

  test('R2-23: New Plan button resets forge back to intake phase', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();

    // Accept the "unsaved plan" guard dialog if it fires.
    page.once('dialog', dialog => dialog.accept());
    await forge.newPlanButton.click();

    // Should be back in the intake phase.
    await forge.assertIntakePhase();
    // Description textarea should be empty (form was reset).
    await expect(forge.taskDescriptionTextarea).toHaveValue('');
  });

  test('R2-23: Back to Board button in saved phase returns to kanban', async ({
    page, forge, kanban, mockAll,
  }) => {
    await mockAll();
    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();

    await forge.backToBoardFromSavedButton.click();

    // Kanban board columns must be visible.
    await kanban.assertAllColumnsVisible();
  });

  // -------------------------------------------------------------------------
  // R2-23 : edge cases
  // -------------------------------------------------------------------------

  test('R2-23: save error on approve keeps plan in preview and shows error', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    // Override approve with a 500 to trigger the error branch.
    await page.route('**/api/v1/pmo/forge/approve', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' }),
      });
    });

    await loadPreviewPhase(forge);
    await forge.approveAndQueueButton.click();

    // Should remain in preview — not transition to saved.
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 5_000 });

    // A save-error element identified by its id (see ForgePanel.tsx).
    const saveError = page.locator('#forge-save-error');
    await expect(saveError).toBeVisible({ timeout: 5_000 });
    const errorText = await saveError.textContent();
    expect(errorText?.trim().length).toBeGreaterThan(0);
  });

  test('R2-23: approve after a failed approve attempt succeeds on retry', async ({
    page, forge, mockForge,
  }) => {
    await mockForge();
    let callCount = 0;
    await page.route('**/api/v1/pmo/forge/approve', async (route) => {
      callCount++;
      if (callCount === 1) {
        // First attempt fails.
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Transient error' }),
        });
      } else {
        // Second attempt succeeds.
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ saved: true, path: '/tmp/plan.json' }),
        });
      }
    });

    await loadPreviewPhase(forge);

    // First click — fails.
    await forge.approveAndQueueButton.click();
    const saveError = page.locator('#forge-save-error');
    await expect(saveError).toBeVisible({ timeout: 5_000 });

    // Second click — should succeed and transition to saved phase.
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();
    expect(callCount).toBe(2);
  });
});
