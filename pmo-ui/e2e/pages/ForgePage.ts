import type { Page, Locator } from '@playwright/test';
import { expect } from '@playwright/test';
import { BasePage } from './BasePage.js';

/**
 * ForgePage — selectors and interactions for the ForgePanel view.
 *
 * The Forge is a 5-phase state machine:
 *   intake → generating → preview → regenerating → saved
 *
 * Each phase renders different UI — selectors are grouped by phase below.
 * The phase label is rendered in the header bar next to "The Forge" title.
 */
export class ForgePage extends BasePage {
  constructor(page: Page) {
    super(page);
  }

  // ---------------------------------------------------------------------------
  // Header bar (always visible when Forge view is active)
  // ---------------------------------------------------------------------------

  get forgeTitle(): Locator {
    // Match the 11px bold header inside ForgePanel, not the nav tab button
    return this.page.locator('span').filter({ hasText: 'The Forge' }).locator('visible=true').first();
  }

  /**
   * The phase subtitle next to the title (e.g. "Describe the work to generate
   * a plan", "Generating plan...", etc.)
   */
  get phaseLabel(): Locator {
    // The small text immediately after "The Forge" title in the header bar.
    // Identified by its content matching known phase descriptions.
    return this.page.locator('span').filter({
      hasText: /Describe the work|Generating plan|Review, edit|Answer refinement|Plan saved/,
    }).first();
  }

  get backToBoardButton(): Locator {
    return this.page.getByRole('button', { name: /← Board/ });
  }

  get editIntakeButton(): Locator {
    return this.page.getByRole('button', { name: /← Edit Intake/ });
  }

  /**
   * "from signal: <id>" badge shown when the forge was opened from a signal.
   */
  fromSignalBadge(signalId?: string): Locator {
    const pattern = signalId ? `from signal: ${signalId}` : /from signal:/;
    return this.page.locator('span', { hasText: pattern });
  }

  // ---------------------------------------------------------------------------
  // Intake phase fields
  // ---------------------------------------------------------------------------

  get adoSearchInput(): Locator {
    return this.page.getByPlaceholder('Search ADO work items...');
  }

  get projectSelect(): Locator {
    // The first <select> in the form (project selector)
    return this.page.locator('select').first();
  }

  get taskTypeSelect(): Locator {
    // Second select — Task Type (Auto-detect / Feature / Bug Fix / etc.)
    return this.page.locator('select').nth(1);
  }

  get prioritySelect(): Locator {
    // Third select — Priority (P0/P1/P2)
    return this.page.locator('select').nth(2);
  }

  get taskDescriptionTextarea(): Locator {
    return this.page.getByPlaceholder('Describe the work: what needs to be built, fixed, or analyzed.');
  }

  get generateButton(): Locator {
    return this.page.getByRole('button', { name: /Generate Plan|Generating\.\.\./ });
  }

  /**
   * Informational text rendered when no projects are registered.
   */
  get noProjectsMessage(): Locator {
    return this.page.getByText(/No projects registered/);
  }

  get projectsLoadingText(): Locator {
    return this.page.getByText('Loading projects...');
  }

  // ---------------------------------------------------------------------------
  // Generating / regenerating phase — cancel button
  // ---------------------------------------------------------------------------

  get cancelButton(): Locator {
    return this.page.getByRole('button', { name: 'Cancel' });
  }

  // ---------------------------------------------------------------------------
  // Preview phase
  // ---------------------------------------------------------------------------

  get planReadyHeader(): Locator {
    return this.page.getByText('Plan Ready');
  }

  get approveAndQueueButton(): Locator {
    return this.page.getByRole('button', { name: 'Approve & Queue' });
  }

  get regenerateButton(): Locator {
    return this.page.getByRole('button', { name: /^Regenerate$|^Loading\.\.\.$/ });
  }

  /**
   * Save error displayed when approve fails.
   */
  get saveErrorBanner(): Locator {
    // The save error is a small red div rendered when approve fails.
    return this.page.locator('div[style*="color: rgb(239, 68, 68)"]').filter({
      hasNot: this.page.locator('button'),
    }).first();
  }

  /**
   * Generate error banner (shown at top of body in any phase).
   */
  get generateErrorBanner(): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div[style*="background: rgb"]'),
      hasText: /API \d+:|failed|Failed/,
    }).first();
  }

  // ---------------------------------------------------------------------------
  // PlanEditor (rendered in preview phase)
  // ---------------------------------------------------------------------------

  /**
   * The plan editor stats bar (Phases / Steps / Gates / Risk tiles).
   */
  get planEditorStatsBar(): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: 'Phases' }).and(
        this.page.locator('div', { hasText: 'Steps' }),
      ),
    }).first();
  }

  statTile(label: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: label }),
    }).filter({
      has: this.page.locator('div[style*="font-family: monospace"]'),
    }).first();
  }

  get planSummaryBlock(): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: 'Summary' }),
    }).filter({
      has: this.page.locator('div[style*="border-left"]'),
    }).first();
  }

  /**
   * A phase accordion header in the plan editor.
   */
  phaseHeader(name: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: name }).and(
        this.page.locator('div[style*="font-weight: 700"]'),
      ),
    }).filter({
      has: this.page.locator('span', { hasText: /steps/ }),
    }).first();
  }

  /**
   * The "gate" badge on a phase header.
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
   * A step row identified by its task description text.
   */
  stepRow(taskDescription: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: taskDescription }).and(
        this.page.locator('div[style*="cursor: text"]'),
      ),
    }).first();
  }

  /**
   * The inline edit input that appears when a step is clicked.
   */
  get stepEditInput(): Locator {
    return this.page.locator('input[style*="border: 1px solid rgb(59, 130, 246)"]');
  }

  /**
   * Add step button at the bottom of an expanded phase.
   */
  get addStepButton(): Locator {
    return this.page.getByRole('button', { name: '+ Add step' });
  }

  /**
   * Agent badge/select on a step (cyan chip).
   */
  stepAgentBadge(taskDescription: string): Locator {
    return this.stepRow(taskDescription).locator('span[style*="color: rgb(6, 182, 212)"]');
  }

  // ---------------------------------------------------------------------------
  // Regenerating phase — InterviewPanel
  // ---------------------------------------------------------------------------

  get interviewHeader(): Locator {
    return this.page.getByText('Refinement Questions');
  }

  get interviewHint(): Locator {
    return this.page.getByText('Answer what you can — unanswered questions use sensible defaults.');
  }

  /**
   * A question card in the interview panel, by question number (1-based).
   */
  questionCard(index: number): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('div', { hasText: String(index) }).and(
        this.page.locator('div[style*="border-radius: 50%"]'),
      ),
    }).first();
  }

  /**
   * A choice button for a specific option in a choice-type question.
   */
  choiceButton(choice: string): Locator {
    return this.page.getByRole('button', { name: choice });
  }

  get skipButton(): Locator {
    return this.page.getByRole('button', { name: 'skip' });
  }

  get regenerateWithAnswersButton(): Locator {
    return this.page.getByRole('button', { name: /Re-generate/ });
  }

  get backToPlanButton(): Locator {
    return this.page.getByRole('button', { name: 'Back to Plan' });
  }

  // ---------------------------------------------------------------------------
  // Saved phase
  // ---------------------------------------------------------------------------

  get savedHeader(): Locator {
    return this.page.getByText('Plan Saved & Queued');
  }

  get savedCheckmark(): Locator {
    return this.page.locator('div', { hasText: '✓' }).filter({
      has: this.page.locator('div[style*="border-radius: 50%"]'),
    }).first();
  }

  get savedPathText(): Locator {
    // The monospace path displayed after save
    return this.page.locator('div[style*="font-family: monospace"]').last();
  }

  get startExecutionButton(): Locator {
    return this.page.getByRole('button', { name: /Start Execution/ });
  }

  get newPlanButton(): Locator {
    return this.page.getByRole('button', { name: 'New Plan' });
  }

  get backToBoardFromSavedButton(): Locator {
    return this.page.getByRole('button', { name: 'Back to Board' });
  }

  // ---------------------------------------------------------------------------
  // ADO Combobox (within intake form)
  // ---------------------------------------------------------------------------

  /**
   * ADO search results dropdown item by title.
   */
  adoDropdownItem(title: string): Locator {
    return this.page.locator('div').filter({
      has: this.page.locator('span', { hasText: title }),
    }).filter({
      has: this.page.locator('span[style*="monospace"]'),
    }).first();
  }

  // ---------------------------------------------------------------------------
  // High-level interactions
  // ---------------------------------------------------------------------------

  /**
   * Navigate back to the kanban board via the header button.
   */
  async goBackToBoard(): Promise<void> {
    await this.backToBoardButton.click();
    await this.page.waitForTimeout(150);
  }

  /**
   * Fill in the intake form and click Generate Plan.
   */
  async fillAndGenerate(description: string): Promise<void> {
    await this.taskDescriptionTextarea.fill(description);
    await this.generateButton.click();
  }

  /**
   * Assert that the forge header is visible (confirming we are in the Forge view).
   */
  async assertForgeVisible(): Promise<void> {
    await expect(this.forgeTitle).toBeVisible({ timeout: 5_000 });
  }

  /**
   * Assert the intake form is rendered (phase = intake).
   */
  async assertIntakePhase(): Promise<void> {
    await expect(this.taskDescriptionTextarea).toBeVisible({ timeout: 5_000 });
    await expect(this.generateButton).toBeVisible();
  }

  /**
   * Assert the preview phase is rendered (phase = preview).
   */
  async assertPreviewPhase(): Promise<void> {
    await expect(this.planReadyHeader).toBeVisible({ timeout: 15_000 });
    await expect(this.approveAndQueueButton).toBeVisible();
  }

  /**
   * Assert the saved phase is rendered (phase = saved).
   */
  async assertSavedPhase(): Promise<void> {
    await expect(this.savedHeader).toBeVisible({ timeout: 15_000 });
    await expect(this.savedCheckmark).toBeVisible();
  }

  /**
   * Assert the interview panel is rendered (phase = regenerating).
   */
  async assertRegeneratingPhase(): Promise<void> {
    await expect(this.interviewHeader).toBeVisible({ timeout: 10_000 });
  }
}
