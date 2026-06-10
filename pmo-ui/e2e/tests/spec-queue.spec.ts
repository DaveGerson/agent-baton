/**
 * spec-queue.spec.ts — E2E tests for the Spec Queue tab (007 Phase I).
 *
 * All tests use Playwright route mocking — no live backend required.
 * The mockSpecQueue fixture intercepts /api/v1/pmo/specs/*.
 *
 * 7 tests:
 *   1. Spec Queue tab is visible in the navbar
 *   2. Clicking the tab shows the panel header
 *   3. List renders mock spec drafts
 *   4. Status filter chips are rendered
 *   5. Clicking a spec row opens the detail pane
 *   6. Submit form expands and fields are visible
 *   7. Error state is shown when list fetch fails
 */

import { test, expect } from '../fixtures/test-fixtures.js';

// ---------------------------------------------------------------------------
// Helper — navigate to the Spec Queue tab
// ---------------------------------------------------------------------------

async function openSpecQueueTab(page: import('@playwright/test').Page) {
  const tab = page.getByRole('tab', { name: /Spec Queue/i });
  await expect(tab).toBeVisible({ timeout: 8_000 });
  await tab.click();
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Spec Queue tab — 007 Phase I', () => {

  /**
   * Test 1: Spec Queue nav tab is present in the top nav.
   */
  test('spec queue tab is visible in the navbar', async ({ kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    const tab = kanban.page.getByRole('tab', { name: /Spec Queue/i });
    await expect(tab).toBeVisible();
  });

  /**
   * Test 2: Clicking the Spec Queue tab switches the view and shows the panel header.
   */
  test('clicking spec queue tab shows panel header', async ({ page, kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecQueueTab(page);

    // Panel heading text contains "Spec Queue"
    const heading = page.getByRole('tabpanel', { name: /spec.queue/i }).getByText('Spec Queue').first();
    await expect(heading).toBeVisible({ timeout: 6_000 });
  });

  /**
   * Test 3: Spec draft list renders rows from mock data.
   */
  test('list renders mock spec drafts', async ({ page, kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecQueueTab(page);

    // At least the first mock spec title should appear
    const row = page.getByText('Add OAuth2 login flow');
    await expect(row).toBeVisible({ timeout: 8_000 });

    // Second mock spec should also be visible
    const row2 = page.getByText('Migrate auth tokens to Redis');
    await expect(row2).toBeVisible({ timeout: 5_000 });
  });

  /**
   * Test 4: Status filter chips are rendered (All, Submitted, Enriched, etc.).
   */
  test('status filter chips are rendered', async ({ page, kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecQueueTab(page);

    // Wait for panel to fully render
    await page.getByText('Add OAuth2 login flow').waitFor({ timeout: 8_000 });

    // "All" chip should be present
    await expect(page.getByRole('button', { name: 'All' }).first()).toBeVisible({ timeout: 5_000 });

    // Status-specific chips
    await expect(page.getByRole('button', { name: 'Submitted', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Enriched',  exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approved',  exact: true })).toBeVisible();
  });

  /**
   * Test 5: Clicking a spec row opens the detail pane showing metadata.
   */
  test('clicking a spec row opens the detail pane', async ({ page, kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecQueueTab(page);

    // Click the enriched spec row (has enrichment data)
    const row = page.getByText('Migrate auth tokens to Redis');
    await expect(row).toBeVisible({ timeout: 8_000 });
    await row.click();

    // Detail pane should show "Details" section header
    await expect(page.getByText('Details').first()).toBeVisible({ timeout: 5_000 });

    // Enrichment section should appear since this spec is enriched
    await expect(page.getByText('Enrichment').first()).toBeVisible({ timeout: 5_000 });

    // Approve and Bounce buttons should be available for enriched specs
    await expect(page.getByRole('button', { name: 'Approve', exact: true })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByRole('button', { name: 'Bounce',  exact: true })).toBeVisible({ timeout: 5_000 });
  });

  /**
   * Test 6: Submit form expands when clicked and shows title/body fields.
   */
  test('submit form expands and shows input fields', async ({ page, kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecQueueTab(page);

    // "Submit New Spec" toggle should be present (collapsed initially)
    const toggle = page.getByRole('button', { name: /Submit New Spec/i });
    await expect(toggle).toBeVisible({ timeout: 8_000 });
    await toggle.click();

    // After expanding, the title input should be visible
    const titleInput = page.getByPlaceholder('Short descriptive title');
    await expect(titleInput).toBeVisible({ timeout: 5_000 });

    // Body textarea should be visible
    const bodyText = page.getByPlaceholder(/Detailed description/i);
    await expect(bodyText).toBeVisible({ timeout: 5_000 });

    // Source mode radios: Manual, GitHub Issue, Azure DevOps
    await expect(page.getByRole('radio', { name: 'Manual' })).toBeVisible();
    await expect(page.getByRole('radio', { name: /GitHub Issue/i })).toBeVisible();
  });

  /**
   * Test 7: Error state is shown when the list fetch fails.
   */
  test('error banner shown when list fetch fails', async ({ page, kanban, mockAll, mockSpecQueue }) => {
    await mockAll();
    await mockSpecQueue({ failList: true });
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecQueueTab(page);

    // An error alert should be visible
    const errorBanner = page.getByRole('alert').first();
    await expect(errorBanner).toBeVisible({ timeout: 8_000 });
  });

});
