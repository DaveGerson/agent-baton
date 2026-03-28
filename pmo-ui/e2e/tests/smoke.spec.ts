/**
 * smoke.spec.ts — Infrastructure validation smoke test.
 *
 * Goals:
 *   1. Confirm Playwright can reach the app (either the Python backend at
 *      localhost:8741/pmo/ or the Vite dev server at localhost:3000/pmo/).
 *   2. Verify the React shell hydrates and the navbar renders.
 *   3. Capture a screenshot to confirm the screenshot utility works.
 *   4. Validate that route mocking works end-to-end.
 *
 * This suite uses the full fixture stack (mockAll) so it runs correctly
 * even when the Python backend is not running.
 *
 * Connection failure handling:
 *   If both the backend and the dev server are unavailable the test will
 *   fail at `goto()` with a net::ERR_CONNECTION_REFUSED error, which is the
 *   correct behaviour — the smoke test is the first gate that must pass.
 */

/// <reference types="node" />
import * as fs from 'node:fs';
import { test, expect } from '../fixtures/test-fixtures.js';
import { captureFullPage } from '../utils/screenshots.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Attempt navigation, gracefully handle connection-refused errors.
 * Returns true if the page loaded, false if the server is unreachable.
 *
 * The smoke test records the connectivity state rather than hard-failing so
 * that CI can report a meaningful "infrastructure not running" message
 * instead of a cryptic Playwright error.
 */
async function tryNavigate(page: import('@playwright/test').Page, url: string): Promise<boolean> {
  try {
    const response = await page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: 10_000,
    });
    return response !== null && response.status() < 500;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    // ERR_CONNECTION_REFUSED = server not running; treat as soft-skip.
    if (msg.includes('ERR_CONNECTION_REFUSED') || msg.includes('ECONNREFUSED')) {
      return false;
    }
    throw err; // Re-throw unexpected errors.
  }
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Smoke — infrastructure validation', () => {
  /**
   * Test 1: Navbar renders with mocked API.
   *
   * This is the primary smoke test.  It uses mockAll to intercept every API
   * call so the board can fully render without a live backend.  Verifies:
   *   - App shell renders
   *   - Navbar brand ("Baton PMO") is visible
   *   - Navigation tabs are present
   *   - Keyboard hint line is present
   *   - Screenshot capture succeeds
   */
  test('navbar renders with mocked board data', async ({ page, kanban, mockAll }) => {
    // Install all API mocks before navigation.
    await mockAll();

    // Navigate to the app root.
    await kanban.goto('/');

    // Wait for the React app to hydrate.
    await kanban.waitForAppReady();

    // --- Navbar assertions ---

    // Brand title must be visible
    await expect(kanban.brandTitle).toBeVisible();

    // Subtitle
    await expect(kanban.brandSubtitle).toBeVisible();

    // Navigation tabs
    await expect(kanban.navTabKanban).toBeVisible();
    await expect(kanban.navTabForge).toBeVisible();

    // Keyboard shortcut hint
    await expect(kanban.keyboardHint).toBeVisible();

    // Footer label
    await expect(kanban.agentBatonLabel).toBeVisible();

    // --- Screenshot ---
    const screenshotPath = await captureFullPage(page, 'smoke-navbar');
    // Verify the file was actually written (non-empty).
    const { statSync } = fs;
    const stat = statSync(screenshotPath);
    expect(stat.size).toBeGreaterThan(0);
  });

  /**
   * Test 2: Kanban view renders with all 5 columns after mock data loads.
   */
  test('kanban board renders 5 columns', async ({ kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    // All 5 column labels from tokens.ts COLUMNS array
    await kanban.assertAllColumnsVisible();
  });

  /**
   * Test 3: Health bar renders program cards from mocked data.
   */
  test('health bar shows program cards', async ({ kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    // Wait for the board data to load (the health bar is populated after fetch)
    await kanban.page.waitForTimeout(500);

    // The MOCK_HEALTH data has ALPHA and BETA programs.
    // At least one program card should be visible.
    const alphaCard = kanban.programCard('ALPHA');
    await expect(alphaCard).toBeVisible({ timeout: 8_000 });
  });

  /**
   * Test 4: New Plan button navigates to the Forge view.
   */
  test('new plan button switches to forge view', async ({ kanban, forge, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await kanban.clickNewPlan();

    // The forge title should now be visible
    await forge.assertForgeVisible();

    // The intake form should be in the default phase
    await forge.assertIntakePhase();
  });

  /**
   * Test 5: Hotkey 'n' opens the Forge (keyboard navigation).
   */
  test('hotkey n switches to forge view', async ({ kanban, forge, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    // Press 'n' hotkey (defined in App.tsx useHotkeys)
    await kanban.pressHotkey('n');

    await forge.assertForgeVisible();
  });

  /**
   * Test 6: Forge back button returns to kanban.
   */
  test('forge back button returns to kanban', async ({ kanban, forge, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await kanban.switchToForge();
    await forge.assertForgeVisible();

    await forge.goBackToBoard();

    // Kanban columns should be visible again
    await kanban.assertAllColumnsVisible();
  });

  /**
   * Test 7: Signals bar toggles on and off.
   */
  test('signals toggle shows and hides signals bar', async ({ kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    // Signals bar should not be visible initially
    await expect(kanban.signalsBar).toBeHidden();

    // Toggle on
    await kanban.toggleSignals();
    await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });

    // Toggle off
    await kanban.toggleSignals();
    await expect(kanban.signalsBar).toBeHidden({ timeout: 5_000 });
  });

  /**
   * Test 8: Error banner shown when board API fails.
   */
  test('error banner shown when board api returns 503', async ({ kanban, mockBoard }) => {
    await mockBoard({ failBoard: true });

    await kanban.goto('/');
    // Do not call waitForAppReady — the app may not fully hydrate if board fails.
    // Wait for DOM content instead.
    await kanban.page.waitForLoadState('domcontentloaded');

    // The error banner should appear within the polling interval.
    // With SSE aborted and board failing, the polling fallback fires at 5 s.
    // We give it 12 s total.
    await expect(kanban.errorBanner).toBeVisible({ timeout: 12_000 });
  });

  /**
   * Test 9: Forge project selector loads projects from mock API.
   */
  test('forge project selector renders mocked projects', async ({ forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();

    // Project selector should not show the "loading" state — projects load fast.
    await expect(forge.projectsLoadingText).toBeHidden({ timeout: 5_000 });

    // Should have project options (Alpha Service and Beta Frontend in mock data)
    const projectSelect = forge.projectSelect;
    await expect(projectSelect).toBeVisible();

    const optionCount = await projectSelect.evaluate(
      (el: HTMLSelectElement) => el.options.length,
    );
    expect(optionCount).toBe(2);
  });

  /**
   * Test 10: Screenshot utility captures the Forge intake form.
   */
  test('screenshot utility works for forge view', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();

    const screenshotPath = await captureFullPage(page, 'smoke-forge-intake');
    const { statSync } = fs;
    const stat = statSync(screenshotPath);
    expect(stat.size).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Standalone connectivity check (doesn't use mockAll — tests raw server)
// ---------------------------------------------------------------------------

test.describe('Smoke — connectivity', () => {
  /**
   * This test tries to reach the configured baseURL without any mocks.
   * It reports connectivity state but does not fail hard if the server is
   * down — the other smoke tests above (which use mocks) are the real gates.
   */
  test('app base url is reachable or server is not running', async ({ page }) => {
    const baseUrl = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:8741/pmo/';
    const reachable = await tryNavigate(page, baseUrl);

    if (!reachable) {
      // Log clearly rather than failing — this is informational.
      console.log(
        `[smoke] Base URL ${baseUrl} is not reachable. ` +
        'Run the backend or vite dev server before running live tests.',
      );
      test.skip(true, `${baseUrl} not reachable — skipping connectivity assertion`);
      return;
    }

    // Server responded — assert the page has at least some content.
    const bodyText = await page.locator('body').textContent();
    expect(bodyText).not.toBeNull();
  });
});
