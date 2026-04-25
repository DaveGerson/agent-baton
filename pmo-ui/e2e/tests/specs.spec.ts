/**
 * specs.spec.ts — Smoke tests for the Specs tab (F0.1).
 *
 * These tests use Playwright route mocking so they run without a live backend.
 * The Specs API calls fall back to inline mock data in api/client.ts when
 * /api/v1/specs returns a non-200, so mocking the route as 404 is sufficient
 * to exercise the full UI path via the offline fallback.
 *
 * When backend agent 3.1.a ships the real routes, add a `mockSpecs` fixture
 * that intercepts /api/v1/specs and returns real-shaped data.
 */

import { test, expect } from '../fixtures/test-fixtures.js';

// ---------------------------------------------------------------------------
// Helper — navigate to Specs tab
// ---------------------------------------------------------------------------

async function openSpecsTab(page: import('@playwright/test').Page) {
  // The Specs tab button is identified by its label text.
  const specsTab = page.getByRole('tab', { name: /Specs/i });
  await expect(specsTab).toBeVisible({ timeout: 8_000 });
  await specsTab.click();
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Specs tab — F0.1 smoke', () => {
  /**
   * Test 1: Specs nav tab is present in the top nav.
   */
  test('specs tab is visible in the navbar', async ({ kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    const specsTab = kanban.page.getByRole('tab', { name: /Specs/i });
    await expect(specsTab).toBeVisible();
  });

  /**
   * Test 2: Clicking the Specs tab switches view and shows the panel header.
   */
  test('clicking specs tab shows specs panel header', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecsTab(page);

    // Panel heading "Specs" should appear
    const heading = page.getByRole('tabpanel', { name: /specs/i }).getByText('Specs').first();
    await expect(heading).toBeVisible({ timeout: 6_000 });
  });

  /**
   * Test 3: Spec list renders rows from mock data (via client-side fallback).
   * We block the /api/v1/specs endpoint so the fallback mock fires.
   */
  test('spec list renders mock specs via client fallback', async ({ page, kanban, mockAll }) => {
    await mockAll();

    // Block the real specs endpoint so the offline fallback in client.ts fires.
    await page.route('**/api/v1/specs**', async (route) => {
      await route.fulfill({ status: 404, body: 'not implemented yet' });
    });

    await kanban.goto('/');
    await kanban.waitForAppReady();

    await openSpecsTab(page);

    // Wait for the list to populate (fallback is synchronous but fetch is async)
    const firstSpecTitle = page.getByText('F0.1 — First-Class Spec Entity');
    await expect(firstSpecTitle).toBeVisible({ timeout: 8_000 });
  });

  /**
   * Test 4: State filter chips are rendered.
   */
  test('state filter chips are rendered', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await page.route('**/api/v1/specs**', async (route) => {
      await route.fulfill({ status: 404, body: 'not implemented yet' });
    });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await openSpecsTab(page);

    // "All" chip and at least "Draft" and "Approved" should appear as filter chips.
    // Use exact match to avoid collisions with spec row buttons that contain state names.
    await expect(page.getByRole('button', { name: 'All' }).first()).toBeVisible({ timeout: 6_000 });
    await expect(page.getByRole('button', { name: 'Draft', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approved', exact: true })).toBeVisible();
  });

  /**
   * Test 5: Clicking a spec row opens the detail pane.
   */
  test('clicking a spec row opens the detail pane', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await page.route('**/api/v1/specs**', async (route) => {
      await route.fulfill({ status: 404, body: 'not implemented yet' });
    });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await openSpecsTab(page);

    // Wait for list
    const row = page.getByText('F0.1 — First-Class Spec Entity');
    await expect(row).toBeVisible({ timeout: 8_000 });
    await row.click();

    // Detail pane should show "Content" section header
    await expect(page.getByText('Content').first()).toBeVisible({ timeout: 5_000 });

    // spec-f01-001 state is 'approved' so Archive is available (not Approve).
    // Use exact match to avoid the "Archived" filter chip in the background.
    await expect(page.getByRole('button', { name: 'Archive', exact: true })).toBeVisible({ timeout: 5_000 });
  });

  /**
   * Test 6: The "Back to Rail" button in the Specs panel returns to kanban.
   */
  test('back button in specs panel returns to kanban', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await page.route('**/api/v1/specs**', async (route) => {
      await route.fulfill({ status: 404, body: 'not implemented yet' });
    });

    await kanban.goto('/');
    await kanban.waitForAppReady();
    await openSpecsTab(page);

    // Click the back button (labeled "← Rail")
    const backBtn = page.getByRole('button', { name: /Rail/i }).first();
    await expect(backBtn).toBeVisible({ timeout: 6_000 });
    await backBtn.click();

    // The Specs tab should no longer be selected (kanban view is active).
    const specsTab = page.getByRole('tab', { name: /Specs/i });
    await expect(specsTab).toHaveAttribute('aria-selected', 'false', { timeout: 5_000 });

    // The kanban tab should be selected.
    const kanbanTab = page.getByRole('tab', { name: /The Rail/i });
    await expect(kanbanTab).toHaveAttribute('aria-selected', 'true', { timeout: 5_000 });
  });
});
