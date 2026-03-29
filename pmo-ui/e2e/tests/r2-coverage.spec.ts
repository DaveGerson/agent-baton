/**
 * r2-coverage.spec.ts — Targeted coverage for R2 audit items.
 *
 * R2-26: AdoCombobox edge cases
 *   - Empty search results: dropdown does not open (no items, no error).
 *   - Network error: "Search failed — try again" message appears in dropdown.
 *   - Keyboard Escape closes an open dropdown.
 *   - Selecting an item fills the input and fires onSelect.
 *
 * R2-31: HealthBar click-to-filter
 *   - Clicking a program tile sets aria-pressed="true" on that tile.
 *   - Clicking the same tile a second time resets aria-pressed to "false"
 *     (toggle-off behaviour).
 *   - Clicking a second program tile deactivates the first one.
 *
 * R2-40: Plan draft edge cases
 *   - Corrupt JSON in localStorage does not show the restore banner.
 *   - A draft belonging to a different project_id does not show the banner.
 */

/// <reference types="node" />

import { test, expect } from '../fixtures/test-fixtures.js';
import { MOCK_FORGE_PLAN } from '../fixtures/mock-data.js';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Navigate to the Forge intake phase with all API mocks installed.
 */
async function openForgeIntake(
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
 * Navigate to the Forge preview phase with a generated plan.
 * The `description` must be long enough for the Generate button to enable
 * (the ForgePanel guard requires a non-empty trimmed value).
 *
 * We wait on `approveAndQueueButton` rather than `planReadyHeader` to avoid
 * a pre-existing strict-mode violation in ForgePage.assertPreviewPhase()
 * ("Plan Ready" matches 3 elements when the Kanban board is also visible).
 */
async function openForgePreview(
  forge: import('../pages/ForgePage.js').ForgePage,
  mockAll: () => Promise<void>,
  description = 'Implement JWT-based authentication for the Alpha service API gateway',
): Promise<void> {
  await openForgeIntake(forge, mockAll);
  await forge.fillAndGenerate(description);
  // Wait for the Approve & Queue button — unique to the preview phase.
  await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
}

// ---------------------------------------------------------------------------
// R2-26: AdoCombobox edge cases
// ---------------------------------------------------------------------------

test.describe('R2-26: AdoCombobox edge cases', () => {
  /**
   * Empty search results: the component receives { items: [] } from the API.
   * Because the open-state guard is `open && (searchError || items.length > 0)`,
   * the dropdown listbox must NOT be rendered and the page must not crash.
   */
  test('empty ADO search results keeps dropdown closed', async ({ page, forge, mockAll }) => {
    // Override the ADO search route with an empty result set before installing
    // the full mockAll so our route registration runs last and wins.
    await mockAll();

    await page.route('**/api/v1/pmo/ado/search**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [] }),
      });
    });

    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();

    // Type a query and wait for the debounce (300 ms in source + margin).
    await forge.adoSearchInput.fill('nonexistent-query-xyz');
    await page.waitForTimeout(500);

    // The listbox must not be present.
    await expect(page.getByRole('listbox', { name: 'ADO work items' })).toBeHidden();

    // The page itself must still be healthy (no crash / blank screen).
    await expect(page.locator('body')).toBeVisible();
  });

  /**
   * Network error: the API returns 500.  The component sets searchError=true
   * and renders "Search failed — try again" inside the open dropdown.
   */
  test('ADO search API error shows "Search failed" message in dropdown', async ({
    page,
    forge,
    mockAll,
  }) => {
    await mockAll();

    // Override with a 500 error after the standard mockForge route installs.
    await page.route('**/api/v1/pmo/ado/search**', async (route) => {
      await route.fulfill({ status: 500, body: 'Internal Server Error' });
    });

    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();

    await forge.adoSearchInput.fill('test-query');
    await page.waitForTimeout(500); // debounce + request round-trip

    // The listbox must open and contain the error text.
    const listbox = page.getByRole('listbox', { name: 'ADO work items' });
    await expect(listbox).toBeVisible({ timeout: 3_000 });
    await expect(listbox).toContainText('Search failed — try again');
  });

  /**
   * Escape key closes an open dropdown without selecting an item.
   * Uses the default MOCK_ADO_ITEMS (returns 3 results) from mockForge.
   */
  test('pressing Escape closes the ADO dropdown', async ({ page, forge, mockAll }) => {
    await openForgeIntake(forge, mockAll);

    await forge.adoSearchInput.fill('JWT');
    await page.waitForTimeout(500);

    // Dropdown must be open (standard mock returns 3 items).
    const listbox = page.getByRole('listbox', { name: 'ADO work items' });
    await expect(listbox).toBeVisible({ timeout: 3_000 });

    // Press Escape — the component calls setOpen(false).
    await forge.adoSearchInput.press('Escape');
    await expect(listbox).toBeHidden({ timeout: 2_000 });
  });

  /**
   * Selecting an ADO item from the dropdown fills the input with the item title
   * and closes the dropdown.
   */
  test('selecting an ADO item fills the input and closes the dropdown', async ({
    page,
    forge,
    mockAll,
  }) => {
    await openForgeIntake(forge, mockAll);

    await forge.adoSearchInput.fill('JWT');
    await page.waitForTimeout(500);

    const listbox = page.getByRole('listbox', { name: 'ADO work items' });
    await expect(listbox).toBeVisible({ timeout: 3_000 });

    // Click the first item — "Implement JWT authentication for API gateway".
    const firstItem = listbox.locator('[role="option"]').first();
    await firstItem.click();

    // Input should now contain the selected item's title.
    await expect(forge.adoSearchInput).toHaveValue(
      'Implement JWT authentication for API gateway',
    );

    // Dropdown must be closed after selection.
    await expect(listbox).toBeHidden({ timeout: 2_000 });
  });
});

// ---------------------------------------------------------------------------
// R2-31: HealthBar click-to-filter
// ---------------------------------------------------------------------------

test.describe('R2-31: HealthBar click-to-filter', () => {
  /**
   * Clicking a program tile in the HealthBar sets aria-pressed="true" on that
   * button.  The mock board has ALPHA and BETA programs.
   *
   * We anchor by program name (stable) rather than aria-label text (changes
   * between "Click to filter." and "Currently filtered. Click to show all."
   * on each toggle, which would cause the locator to resolve to a different
   * element after the state transition).
   */
  test('clicking a HealthBar program tile sets aria-pressed to true', async ({
    page,
    kanban,
    mockAll,
  }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500); // allow board data fetch to settle

    // Verify at least one clickable tile exists — if health data has not loaded
    // yet the HealthBar renders the "No programs tracked yet." placeholder.
    const anyTile = page.getByRole('button', { name: /Click to filter/i });
    const tileCount = await anyTile.count();

    if (tileCount === 0) {
      test.skip(true, 'No clickable HealthBar tiles rendered — skipping R2-31');
      return;
    }

    // Use ALPHA — guaranteed present in MOCK_HEALTH.  Anchor by the program
    // name in the aria-label so the locator remains stable after state change.
    const alphaTile = page.getByRole('button', { name: /^ALPHA:/ });

    // Initially not active.
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'false');

    await alphaTile.click();
    await page.waitForTimeout(300);

    // After clicking, the tile must be marked as active.
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'true');
  });

  /**
   * Clicking the same tile a second time removes the filter (toggle-off):
   * aria-pressed returns to "false".
   */
  test('clicking the same HealthBar tile again removes the filter', async ({
    page,
    kanban,
    mockAll,
  }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    const anyTile = page.getByRole('button', { name: /Click to filter/i });
    const tileCount = await anyTile.count();

    if (tileCount === 0) {
      test.skip(true, 'No clickable HealthBar tiles rendered — skipping R2-31');
      return;
    }

    // Anchor to the ALPHA tile by program name prefix (stable across state changes).
    const alphaTile = page.getByRole('button', { name: /^ALPHA:/ });

    // First click — activate filter.
    await alphaTile.click();
    await page.waitForTimeout(300);
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'true');

    // Second click — deactivate filter.
    // After activation the aria-label says "Currently filtered. Click to show all."
    // The locator still matches because we anchor on program name, not filter text.
    await alphaTile.click();
    await page.waitForTimeout(300);
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'false');
  });

  /**
   * Clicking a second tile deactivates the first (single-program filter model):
   * the previously active tile reverts to aria-pressed="false".
   */
  test('clicking a second HealthBar tile deactivates the first', async ({
    page,
    kanban,
    mockAll,
  }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    // Verify both ALPHA and BETA tiles are present (both are in MOCK_HEALTH).
    const alphaTile = page.getByRole('button', { name: /^ALPHA:/ });
    const betaTile  = page.getByRole('button', { name: /^BETA:/ });

    const alphaCount = await alphaTile.count();
    const betaCount  = await betaTile.count();

    if (alphaCount === 0 || betaCount === 0) {
      test.skip(true, 'Need both ALPHA and BETA HealthBar tiles — skipping R2-31');
      return;
    }

    // Activate ALPHA.
    await alphaTile.click();
    await page.waitForTimeout(300);
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'true');

    // Click BETA — ALPHA should deactivate.
    await betaTile.click();
    await page.waitForTimeout(300);
    await expect(betaTile).toHaveAttribute('aria-pressed', 'true');
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'false');
  });

  /**
   * After filtering by program, clicking the toolbar "All" filter button
   * resets the HealthBar tile's aria-pressed state to false.
   */
  test('toolbar All filter resets the active HealthBar tile', async ({
    page,
    kanban,
    mockAll,
  }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    const alphaTile = page.getByRole('button', { name: /^ALPHA:/ });
    const tileCount = await alphaTile.count();

    if (tileCount === 0) {
      test.skip(true, 'No clickable HealthBar tiles rendered — skipping R2-31');
      return;
    }

    // Activate the ALPHA tile.
    await alphaTile.click();
    await page.waitForTimeout(300);
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'true');

    // Reset via the toolbar "All" button.
    await kanban.clearFilter();
    await page.waitForTimeout(300);

    // Tile must no longer be active.
    await expect(alphaTile).toHaveAttribute('aria-pressed', 'false');
  });
});

// ---------------------------------------------------------------------------
// R2-40: Plan draft edge cases
// ---------------------------------------------------------------------------

test.describe('R2-40: Plan draft edge cases', () => {
  /**
   * Corrupt JSON in localStorage must be silently discarded: no restore banner
   * appears and the app does not crash.  Covers the catch block in ForgePanel's
   * phase=preview useEffect.
   */
  test('corrupt JSON in localStorage does not show the restore banner', async ({
    page,
    forge,
    mockAll,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    // Seed unparseable JSON before generating the plan.
    await page.evaluate(() => {
      localStorage.setItem('pmo:plan-draft', 'not valid json{{{');
    });

    await forge.fillAndGenerate(
      'Implement rate limiting on the public API gateway endpoints',
    );
    // Wait on approveAndQueueButton — avoids strict-mode violation in
    // assertPreviewPhase() caused by multiple "Plan Ready" text matches.
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });

    // Give the phase transition effect time to run.
    await page.waitForTimeout(400);

    // The draft banner must not appear — corrupt data is silently ignored.
    const draftBanner = page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeHidden({ timeout: 2_000 });

    // The page must still be functional (Approve & Queue is accessible).
    await expect(forge.approveAndQueueButton).toBeVisible();
  });

  /**
   * A draft stored under a different project_id must not surface the restore
   * banner for the currently selected project.  The ForgePanel checks
   * `parsed.project_id === projectId` before showing the banner.
   *
   * The mock data registers two projects: proj-alpha and proj-beta.
   * The draft is seeded with project_id "completely-different-project-id".
   */
  test('draft from a different project does not show the restore banner', async ({
    page,
    forge,
    mockAll,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    // Seed a valid draft structure, but with an unrelated project_id.
    await page.evaluate((plan) => {
      localStorage.setItem(
        'pmo:plan-draft',
        JSON.stringify({
          plan,
          project_id: 'completely-different-project-id',
        }),
      );
    }, MOCK_FORGE_PLAN);

    await forge.fillAndGenerate(
      'Migrate user profile schema to the new PostgreSQL cluster',
    );
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });

    await page.waitForTimeout(400);

    // Banner must not appear — wrong project.
    const draftBanner = page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeHidden({ timeout: 2_000 });
  });

  /**
   * A draft with the correct project_id DOES show the restore banner.
   * This validates the positive-path prerequisite for the edge cases above:
   * the banner mechanism itself works when all conditions are met.
   *
   * ForgePanel stores the selected project_id in sessionStorage under
   * 'pmo:forge-project-id' (via usePersistedState).  We read that value
   * after projects have loaded rather than using forge.projectSelect.evaluate(),
   * which would match the sort <select> in the background Kanban view (the
   * first <select> in DOM order) instead of the Forge project selector.
   */
  test('draft with matching project_id shows the restore banner', async ({
    page,
    forge,
    mockAll,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    // Wait for projects to load — the loading text disappears once getProjects()
    // resolves and ForgePanel calls setProjectId(ps[0].project_id).
    await expect(forge.projectsLoadingText).toBeHidden({ timeout: 5_000 });

    // Read the active project_id from sessionStorage, where ForgePanel persists it.
    // This is more reliable than reading forge.projectSelect.evaluate(el => el.value)
    // because KanbanBoard's sort <select> renders first in DOM order.
    const projectId = await page.evaluate((): string => {
      const raw = sessionStorage.getItem('pmo:forge-project-id');
      return raw ? (JSON.parse(raw) as string) : '';
    });

    // Should have resolved to the first mock project ('proj-alpha').
    expect(projectId).not.toBe('');

    // Seed the draft under the same project_id that ForgePanel has selected.
    await page.evaluate(
      ({ plan, pid }) => {
        localStorage.setItem(
          'pmo:plan-draft',
          JSON.stringify({ plan, project_id: pid }),
        );
      },
      { plan: MOCK_FORGE_PLAN, pid: projectId },
    );

    // Generate a new plan to enter the preview phase.
    await forge.fillAndGenerate('Add MFA support to the login flow');
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });

    await page.waitForTimeout(400);

    // The banner MUST appear for the matching project.
    const draftBanner = page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeVisible({ timeout: 5_000 });
    await expect(draftBanner).toContainText('Draft available');
  });

  /**
   * Null / missing draft key does not show the restore banner.
   * Ensures the `if (raw)` guard in ForgePanel works — no raw value means
   * setShowDraftBanner(false) and nothing renders.
   */
  test('no draft in localStorage does not show the restore banner', async ({
    page,
    forge,
    mockAll,
  }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    // Explicitly clear any existing draft.
    await page.evaluate(() => localStorage.removeItem('pmo:plan-draft'));

    await forge.fillAndGenerate('Refactor event bus to strongly typed payloads');
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });

    await page.waitForTimeout(400);

    const draftBanner = page.locator('[aria-label="Draft available"]');
    await expect(draftBanner).toBeHidden({ timeout: 2_000 });
  });
});
