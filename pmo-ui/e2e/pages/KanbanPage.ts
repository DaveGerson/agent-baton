import type { Page, Locator } from '@playwright/test';
import { expect } from '@playwright/test';
import { BasePage } from './BasePage.js';

/**
 * KanbanPage — selectors and interactions for the KanbanBoard view.
 *
 * The Kanban view is the default view after app load.  It contains:
 *   - HealthBar (program progress cards at the top)
 *   - Toolbar (filter buttons, signals toggle, status indicators, New Plan CTA)
 *   - Kanban columns (Queued / Executing / Awaiting Human / Validating / Deployed)
 *   - KanbanCard entries inside each column
 *   - SignalsBar (conditionally rendered below the toolbar)
 *   - Error banner (rendered when backend is unreachable)
 *
 * Because there are no CSS classes, we rely on:
 *   - data-testid (when added in Phase 4)
 *   - Text content (column headers, button labels, status text)
 *   - ARIA roles
 *   - Structural CSS attribute selectors as a last resort
 */
export class KanbanPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  // ---------------------------------------------------------------------------
  // HealthBar
  // ---------------------------------------------------------------------------

  /**
   * The health bar container — the first horizontally-scrolling flex row
   * that appears below the navbar.
   */
  get healthBar(): Locator {
    // HealthBar renders "No programs tracked yet." when empty, or program cards.
    return this.page.locator('div').filter({
      hasText: /No programs tracked yet\.|plans/,
    }).first();
  }

  get noPrograms(): Locator {
    return this.page.getByText('No programs tracked yet.');
  }

  /**
   * Return a locator for a specific program card in the health bar.
   */
  programCard(name: string): Locator {
    return this.page.locator('div').filter({ hasText: name }).filter({
      has: this.page.locator('div', { hasText: /plans/ }),
    }).first();
  }

  /**
   * Return the completion percentage text for a program (e.g. "75%").
   */
  programCompletion(name: string): Locator {
    return this.programCard(name).locator('span', { hasText: /%/ }).first();
  }

  // ---------------------------------------------------------------------------
  // Toolbar
  // ---------------------------------------------------------------------------

  get toolbar(): Locator {
    return this.page.getByRole('button', { name: '+ New Plan' }).locator('../..');
  }

  get newPlanButton(): Locator {
    return this.page.getByRole('button', { name: '+ New Plan' });
  }

  get signalsToggleButton(): Locator {
    // The Signals button contains the text "Signals" and optionally a badge.
    return this.page.getByRole('button', { name: /^Signals/ });
  }

  /**
   * Filter button for "All" programs.
   */
  get allFilterButton(): Locator {
    return this.page.getByRole('button', { name: /^All$/ });
  }

  /**
   * Filter button for a named program.
   */
  programFilterButton(name: string): Locator {
    return this.page.getByRole('button', { name: new RegExp(`^${name}$`) });
  }

  /**
   * Connection mode indicator (live / polling / connecting).
   */
  get connectionIndicator(): Locator {
    return this.page.locator('div').filter({ hasText: /^(live|polling|connecting)$/ }).first();
  }

  /**
   * The "N awaiting" badge shown when cards are in awaiting_human column.
   */
  get awaitingBadge(): Locator {
    return this.page.locator('span', { hasText: /awaiting/ }).filter({
      has: this.page.locator('div[style*="border-radius: 50%"]'),
    }).first();
  }

  /**
   * The plan count text (e.g. "5 plans", "3 executing · 5 plans").
   */
  get planCountText(): Locator {
    return this.page.locator('span', { hasText: /plans$/ }).last();
  }

  // ---------------------------------------------------------------------------
  // Kanban columns
  // ---------------------------------------------------------------------------

  /**
   * Column container by canonical column id / label.
   * Column labels from tokens.ts: Queued, Executing, Awaiting Human,
   * Validating, Deployed.
   */
  column(label: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('span', { hasText: label }).and(
        this.page.locator('span[style*="font-weight: 700"]'),
      ),
    }).first();
  }

  /**
   * Card count badge inside a column header (the number badge).
   */
  columnCardCount(columnLabel: string): Locator {
    return this.column(columnLabel).locator('span', { hasText: /^\d+$/ }).first();
  }

  /**
   * "Empty" placeholder shown when a column has no cards.
   */
  columnEmptyState(columnLabel: string): Locator {
    return this.column(columnLabel).getByText('Empty');
  }

  /**
   * All KanbanCard elements in a given column.
   * Cards are identified by containing the card_id monospace text + title text.
   */
  cardsInColumn(columnLabel: string): Locator {
    return this.column(columnLabel).locator('div[style*="background"]').filter({
      has: this.page.locator('div[style*="font-weight: 600"]'),
    });
  }

  // ---------------------------------------------------------------------------
  // KanbanCard interactions
  // ---------------------------------------------------------------------------

  /**
   * Locate a card by its title text (partial match).
   */
  cardByTitle(title: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: title }).and(
        this.page.locator('div[style*="font-weight: 600"]'),
      ),
    }).filter({
      has: this.page.locator('span[style*="font-family: monospace"]'),
    }).first();
  }

  /**
   * Expand a card by clicking on it.
   */
  async expandCard(cardLocator: Locator): Promise<void> {
    await cardLocator.click();
    // Wait for the expanded detail section to appear.
    await this.page.waitForTimeout(100);
  }

  /**
   * Execute button inside an expanded queued card.
   */
  get executeButton(): Locator {
    return this.page.getByRole('button', { name: /Execute/ });
  }

  /**
   * Re-forge button inside an expanded card.
   */
  get reForgeButton(): Locator {
    return this.page.getByRole('button', { name: 'Re-forge' });
  }

  /**
   * View Plan / Hide Plan toggle inside an expanded card.
   */
  get viewPlanButton(): Locator {
    return this.page.getByRole('button', { name: /View Plan|Hide Plan/ });
  }

  /**
   * "Edit Plan" shortcut button inside an expanded card that has a plan
   * (steps_total > 0).  Clicking it navigates directly to the Forge editor.
   */
  get editPlanButton(): Locator {
    return this.page.getByRole('button', { name: /Edit Plan/ });
  }

  /**
   * The plan preview container that appears after clicking View Plan.
   * Identified by the "No plan available" fallback or phase content.
   */
  get planPreviewContainer(): Locator {
    return this.page.locator('div').filter({
      hasText: /No plan available|Phases|Steps/,
    }).last();
  }

  // ---------------------------------------------------------------------------
  // SignalsBar
  // ---------------------------------------------------------------------------

  /**
   * The signals bar container — only rendered when signals toggle is on.
   */
  get signalsBar(): Locator {
    return this.page.locator('div').filter({
      hasText: /Signals — \d+ open/,
    }).first();
  }

  get signalsHeader(): Locator {
    return this.page.locator('span', { hasText: /Signals — \d+ open/ });
  }

  get addSignalButton(): Locator {
    return this.page.getByRole('button', { name: '+ Add Signal' });
  }

  get cancelAddSignalButton(): Locator {
    return this.page.getByRole('button', { name: 'Cancel' }).first();
  }

  get signalTitleInput(): Locator {
    return this.page.getByPlaceholder('Signal description...');
  }

  get signalTypeSelect(): Locator {
    // The first select in the add form (signal type: bug/escalation/blocker)
    return this.signalTitleInput.locator('../select').first();
  }

  get signalSeveritySelect(): Locator {
    // Second select (severity: critical/high/medium/low)
    return this.signalTitleInput.locator('../select').nth(1);
  }

  get addSignalSubmitButton(): Locator {
    return this.page.getByRole('button', { name: 'Add' });
  }

  /**
   * Locate a signal row by its title text.
   */
  signalRow(title: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('span', { hasText: title }),
    }).filter({
      has: this.page.getByRole('button', { name: 'Forge' }),
    }).first();
  }

  /**
   * The Forge button on a specific signal row.
   */
  signalForgeButton(title: string): Locator {
    return this.signalRow(title).getByRole('button', { name: 'Forge' });
  }

  /**
   * The Resolve button on a specific signal row.
   */
  signalResolveButton(title: string): Locator {
    return this.signalRow(title).getByRole('button', { name: 'Resolve' });
  }

  get batchResolveButton(): Locator {
    return this.page.getByRole('button', { name: /Resolve Selected/ });
  }

  get selectAllCheckbox(): Locator {
    // The "select all" checkbox has aria-label="Select all open signals" (no title attribute).
    return this.page.getByLabel('Select all open signals');
  }

  // ---------------------------------------------------------------------------
  // Error banner
  // ---------------------------------------------------------------------------

  get errorBanner(): Locator {
    // The red error div rendered when board fetch fails.
    return this.page.locator('div').filter({
      hasText: /retrying every/,
    }).first();
  }

  // ---------------------------------------------------------------------------
  // High-level interactions
  // ---------------------------------------------------------------------------

  /**
   * Toggle the signals bar on or off.
   */
  async toggleSignals(): Promise<void> {
    await this.signalsToggleButton.click();
    await this.page.waitForTimeout(100);
  }

  /**
   * Click a program filter and wait for the card list to update.
   */
  async filterByProgram(name: string): Promise<void> {
    await this.programFilterButton(name).click();
    await this.page.waitForTimeout(150);
  }

  /**
   * Reset filter to All.
   */
  async clearFilter(): Promise<void> {
    await this.allFilterButton.click();
    await this.page.waitForTimeout(150);
  }

  /**
   * Click New Plan and wait for the Forge view to become visible.
   */
  async clickNewPlan(): Promise<void> {
    await this.newPlanButton.click();
    await this.page.waitForTimeout(150);
  }

  /**
   * Add a signal via the signals bar form.
   */
  async addSignal(title: string, type: 'bug' | 'escalation' | 'blocker' = 'bug', severity = 'medium'): Promise<void> {
    await this.addSignalButton.click();
    await this.signalTitleInput.fill(title);
    await this.page.selectOption('select', type);
    // Submit via Enter
    await this.signalTitleInput.press('Enter');
    await this.page.waitForTimeout(300);
  }

  /**
   * Assert that a specific column is visible and has the expected label.
   */
  async assertColumnVisible(columnLabel: string): Promise<void> {
    await expect(this.page.getByText(columnLabel).first()).toBeVisible();
  }

  /**
   * Assert all five Kanban columns are rendered.
   */
  async assertAllColumnsVisible(): Promise<void> {
    for (const label of ['Queued', 'Executing', 'Awaiting Human', 'Validating', 'Deployed']) {
      await this.assertColumnVisible(label);
    }
  }
}
