import type { Page, Locator } from '@playwright/test';
import { expect } from '@playwright/test';

/**
 * BasePage — shared navigation helpers and wait utilities used by all page
 * objects.  Concrete pages extend this class and add component-specific
 * selectors on top.
 *
 * Selector strategy (no CSS classes in this codebase):
 *   1. data-testid  — preferred; added progressively in Phase 4
 *   2. ARIA role + accessible name  — getByRole / getByText
 *   3. Placeholder text  — getByPlaceholder
 *   4. CSS attribute selectors  — last resort, fragile
 */
export class BasePage {
  /** Playwright Page instance.  Public so test code can use it directly. */
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  // ---------------------------------------------------------------------------
  // Navigation
  // ---------------------------------------------------------------------------

  /**
   * Navigate to the board root.  Handles both the Python backend path
   * (/pmo/) and the Vite dev server path (/pmo/) — both are equivalent
   * because vite.config.ts sets base: '/pmo/'.
   */
  async goto(path = ''): Promise<void> {
    // baseURL already ends with /pmo/ so we navigate relative to it.
    await this.page.goto(path || '/');
  }

  /**
   * Wait for the React app shell to hydrate.  The root <div id="root">
   * is populated only after JS executes — we wait for a descendant that
   * always renders (the navbar brand text) before proceeding.
   */
  async waitForAppReady(): Promise<void> {
    await this.page.waitForSelector('text=Baton PMO', {
      state: 'visible',
      timeout: 15_000,
    });
  }

  // ---------------------------------------------------------------------------
  // Navbar locators — always visible once app hydrates
  // ---------------------------------------------------------------------------

  get navbar(): Locator {
    // The top nav is the first flex row that contains the brand and nav tabs.
    // Identified by the brand text, since there are no CSS classes.
    return this.page.locator('div').filter({ hasText: 'Baton PMO' }).first();
  }

  get brandLogo(): Locator {
    // The "B" logo div (22x22 gradient square)
    return this.page.locator('div', { hasText: 'B' }).filter({
      has: this.page.locator('[style*="border-radius: 4px"]'),
    }).first();
  }

  get brandTitle(): Locator {
    return this.page.getByText('Baton PMO', { exact: true });
  }

  get brandSubtitle(): Locator {
    return this.page.getByText('Orchestration Board');
  }

  get navTabKanban(): Locator {
    return this.page.getByRole('tab', { name: /AI Kanban/i });
  }

  get navTabForge(): Locator {
    return this.page.getByRole('tab', { name: /The Forge/i });
  }

  get keyboardHint(): Locator {
    return this.page.getByText(/n=new/);
  }

  get agentBatonLabel(): Locator {
    return this.page.getByText('agent-baton pmo');
  }

  // ---------------------------------------------------------------------------
  // Navigation actions
  // ---------------------------------------------------------------------------

  async switchToKanban(): Promise<void> {
    await this.navTabKanban.click();
    await this.page.waitForTimeout(150); // allow CSS display toggle
  }

  async switchToForge(): Promise<void> {
    await this.navTabForge.click();
    await this.page.waitForTimeout(150);
  }

  // ---------------------------------------------------------------------------
  // Wait helpers
  // ---------------------------------------------------------------------------

  /**
   * Wait for a network request to a PMO API endpoint to complete.
   * Useful for synchronising tests after actions that trigger API calls.
   */
  async waitForApiCall(urlPattern: string | RegExp): Promise<void> {
    await this.page.waitForResponse(
      (resp) => {
        const url = resp.url();
        return typeof urlPattern === 'string'
          ? url.includes(urlPattern)
          : urlPattern.test(url);
      },
      { timeout: 15_000 },
    );
  }

  /**
   * Wait for all in-flight fetch requests to settle (best-effort).
   * Uses a short idle window rather than polling network events.
   */
  async waitForNetworkIdle(timeout = 5_000): Promise<void> {
    await this.page.waitForLoadState('networkidle', { timeout });
  }

  /**
   * Poll until a locator is visible.  Wraps expect().toBeVisible() with
   * an informative error message on timeout.
   */
  async assertVisible(locator: Locator, _description = ''): Promise<void> {
    await expect(locator).toBeVisible({
      timeout: 10_000,
    });
  }

  /**
   * Assert that a locator is hidden / not present.
   */
  async assertHidden(locator: Locator, _description = ''): Promise<void> {
    await expect(locator).toBeHidden({ timeout: 5_000 });
  }

  /**
   * Dismiss any open dropdown / overlay by clicking outside.
   */
  async clickOutside(): Promise<void> {
    await this.page.mouse.click(0, 0);
  }

  /**
   * Press a hotkey in the context of the full page.
   */
  async pressHotkey(key: string): Promise<void> {
    await this.page.keyboard.press(key);
    await this.page.waitForTimeout(100);
  }
}
