import type { Page, Locator } from '@playwright/test';
import { expect } from '@playwright/test';
import { BasePage } from './BasePage.js';

/**
 * PlanEditorPage — focused selectors for the PlanEditor component.
 *
 * PlanEditor is rendered inside the Forge preview phase.  It shows:
 *   - Stats bar: Phases / Steps / Gates / Risk
 *   - Task summary block
 *   - Accordion phases (click header to expand / collapse)
 *   - Per-step rows with: reorder buttons, inline-editable description,
 *     agent badge/select, remove button
 *   - "Add step" button at the bottom of each expanded phase
 *
 * This page object extends BasePage and focuses exclusively on the editor
 * tree.  It is usually used alongside ForgePage in the same test.
 */
export class PlanEditorPage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  // ---------------------------------------------------------------------------
  // Stats bar
  // ---------------------------------------------------------------------------

  /**
   * A stats tile identified by its uppercase label.
   * Returns the containing div so you can assert .toContainText() on the value.
   */
  statTile(label: 'Phases' | 'Steps' | 'Gates' | 'Risk'): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: label }),
    }).filter({
      // Stats tiles have a monospace value
      has: this.page.locator('div[style*="font-family: monospace"]'),
    }).first();
  }

  // ---------------------------------------------------------------------------
  // Summary block
  // ---------------------------------------------------------------------------

  get summaryBlock(): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: 'SUMMARY' }),
    }).filter({
      has: this.page.locator('div[style*="border-left"]'),
    }).first();
  }

  get summaryText(): Locator {
    return this.summaryBlock.locator('div[style*="line-height"]').last();
  }

  // ---------------------------------------------------------------------------
  // Phase accordion
  // ---------------------------------------------------------------------------

  /**
   * Phase header row (the clickable bar that expands/collapses a phase).
   * Identified by the phase name text inside a bold div, plus the step-count
   * badge.
   */
  phaseHeader(phaseName: string): Locator {
    return this.page.locator('div[style*="cursor: pointer"]').filter({
      has: this.page.locator('div', { hasText: phaseName }),
    }).first();
  }

  /**
   * The numeric label badge (1, 2, 3…) inside a phase header.
   */
  phaseNumberBadge(phaseIndex: number): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: String(phaseIndex) }).and(
        this.page.locator('div[style*="border-radius: 3px"]'),
      ),
    }).first();
  }

  /**
   * Step-count badge in a phase header (e.g. "3 steps").
   */
  phaseStepCountBadge(phaseName: string): Locator {
    return this.phaseHeader(phaseName).locator('span', { hasText: /\d+ steps/ });
  }

  /**
   * Gate badge on a phase header.
   */
  phaseGateBadge(phaseName: string): Locator {
    return this.phaseHeader(phaseName).locator('span', { hasText: 'gate' });
  }

  /**
   * Remove phase button (×) on a phase header.
   * The button has title="Remove phase" and aria-label="Remove phase N: ..." (accessibility
   * remediation). Use the title attribute for reliable selection.
   */
  removePhaseButton(phaseName: string): Locator {
    return this.phaseHeader(phaseName).locator('button[title="Remove phase"]');
  }

  /**
   * Expand a phase accordion by clicking its header.
   * Passing the same phase again collapses it.
   */
  async togglePhase(phaseName: string): Promise<void> {
    await this.phaseHeader(phaseName).click();
    await this.page.waitForTimeout(100);
  }

  // ---------------------------------------------------------------------------
  // Step rows (visible when phase is expanded)
  // ---------------------------------------------------------------------------

  /**
   * A step row by its task description text.
   * The row contains the description, agent badge, and action buttons.
   */
  stepRow(taskDescription: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: taskDescription }),
    }).filter({
      // Step rows have reorder buttons with aria-label "Move step N up" (added in
      // accessibility remediation). Match via title attribute which is also present.
      has: this.page.locator('button[aria-label*="Move step"][aria-label$=" up"]'),
    }).first();
  }

  /**
   * Move-up button for a step.
   */
  stepMoveUpButton(taskDescription: string): Locator {
    return this.stepRow(taskDescription).locator('button[aria-label*="Move step"][aria-label$=" up"]');
  }

  /**
   * Move-down button for a step.
   */
  stepMoveDownButton(taskDescription: string): Locator {
    return this.stepRow(taskDescription).locator('button[aria-label*="Move step"][aria-label$=" down"]');
  }

  /**
   * The agent badge (cyan text span) on a step when NOT in edit mode.
   */
  stepAgentBadge(taskDescription: string): Locator {
    return this.stepRow(taskDescription).locator('span[style*="color: rgb(6, 182, 212)"]');
  }

  /**
   * Remove step button (×) on a step row.
   * The button has title="Remove step" and aria-label="Remove step N: ..." (accessibility
   * remediation). Use the title attribute for reliable scoped selection.
   */
  removeStepButton(taskDescription: string): Locator {
    return this.stepRow(taskDescription).locator('button[title="Remove step"]');
  }

  /**
   * Click a step's description text to enter inline edit mode.
   */
  async startEditStep(taskDescription: string): Promise<void> {
    const descDiv = this.stepRow(taskDescription).locator('div[style*="cursor: text"]');
    await descDiv.click();
    await this.page.waitForTimeout(100);
  }

  /**
   * The inline edit input (only present when a step is being edited).
   * The input has a blue border (T.accent = #3b82f6).
   */
  get activeStepInput(): Locator {
    return this.page.locator('input[style*="border: 1px solid rgb(59, 130, 246)"]');
  }

  /**
   * The agent dropdown select (only present when a step is being edited).
   */
  get activeAgentSelect(): Locator {
    return this.page.locator('select[style*="color: rgb(6, 182, 212)"]');
  }

  /**
   * Type a new description into the active step input and commit with Enter.
   */
  async editStepDescription(taskDescription: string, newDescription: string): Promise<void> {
    await this.startEditStep(taskDescription);
    await this.activeStepInput.selectText();
    await this.activeStepInput.fill(newDescription);
    await this.activeStepInput.press('Enter');
    await this.page.waitForTimeout(100);
  }

  /**
   * Change the agent on a step (must be in edit mode).
   */
  async setStepAgent(taskDescription: string, agentName: string): Promise<void> {
    await this.startEditStep(taskDescription);
    await this.activeAgentSelect.selectOption(agentName);
    // Commit by pressing Tab or clicking outside
    await this.activeStepInput.press('Tab');
    await this.page.waitForTimeout(100);
  }

  // ---------------------------------------------------------------------------
  // Add step
  // ---------------------------------------------------------------------------

  /**
   * Add step button at the bottom of an expanded phase.
   */
  get addStepButton(): Locator {
    return this.page.getByRole('button', { name: '+ Add step' });
  }

  async addStep(): Promise<void> {
    await this.addStepButton.click();
    await this.page.waitForTimeout(100);
  }

  // ---------------------------------------------------------------------------
  // Assertions
  // ---------------------------------------------------------------------------

  /**
   * Assert that the stats bar shows the expected phase and step counts.
   */
  async assertStats(phases: number, steps: number): Promise<void> {
    await expect(this.statTile('Phases')).toContainText(String(phases));
    await expect(this.statTile('Steps')).toContainText(String(steps));
  }

  /**
   * Assert that a phase is expanded (its steps section is visible).
   * We verify this by looking for the "Add step" button, which only renders
   * when a phase is expanded.
   */
  async assertPhaseExpanded(): Promise<void> {
    await expect(this.addStepButton).toBeVisible({ timeout: 3_000 });
  }

  /**
   * Assert that a phase is collapsed (no Add step button visible).
   */
  async assertPhaseCollapsed(): Promise<void> {
    await expect(this.addStepButton).toBeHidden({ timeout: 3_000 });
  }
}
