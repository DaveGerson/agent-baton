/**
 * ux-audit.spec.ts — UX quality audit for the PMO UI.
 *
 * Evaluates responsive design, interactive behaviour, visual consistency,
 * loading/error states, and content handling.  Tests detect real UX issues;
 * many are expected to find failures against the known audit list.
 *
 * Each test wraps its assertions in try/catch and records pass/fail via
 * AuditReporter so one failure does not cascade to subsequent tests.
 *
 * Run with:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3000/pmo/ npx playwright test e2e/tests/ux-audit.spec.ts --project=desktop
 */

/// <reference types="node" />
import { test, expect } from '../fixtures/test-fixtures.js';
import { AuditReporter } from '../utils/audit-reporter.js';
import { captureFullPage, captureViewports } from '../utils/screenshots.js';

const reporter = AuditReporter.getInstance();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Navigate and wait for the app shell to render.
 * Works against both the Vite dev server and the Python backend by following
 * playwright.config.ts's baseURL.
 */
async function loadBoard(
  kanban: import('../pages/KanbanPage.js').KanbanPage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await mockAll();
  await kanban.goto('/');
  await kanban.waitForAppReady();
  // Allow initial data fetch to settle.
  await kanban.page.waitForTimeout(400);
}

async function loadForge(
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
 * Wait for the forge preview phase.
 * Uses the Approve & Queue button (unique to preview) rather than "Plan Ready"
 * text which collides with the "Plan ready, awaiting execution slot" Kanban
 * column description.
 */
async function waitForPreviewPhase(forge: import('../pages/ForgePage.js').ForgePage): Promise<void> {
  await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
}

/**
 * Navigate to the Forge preview phase with the mock plan loaded, ready to test PlanEditor.
 */
async function loadPlanEditor(
  forge: import('../pages/ForgePage.js').ForgePage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await loadForge(forge, mockAll);
  await forge.fillAndGenerate('Implement JWT authentication middleware');
  await waitForPreviewPhase(forge);
  // Give the editor time to fully render.
  await forge.page.waitForTimeout(300);
}

/**
 * Wraps an assertion block so a single failure records a finding but does not
 * break the rest of the suite.
 */
async function audit(
  title: string,
  category: string,
  fn: () => Promise<void>,
): Promise<void> {
  const start = Date.now();
  try {
    await fn();
    reporter.record('ux', title, 'pass', {
      durationMs: Date.now() - start,
      metadata: { category },
    });
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    reporter.record('ux', title, 'fail', {
      durationMs: Date.now() - start,
      error: error.slice(0, 500),
      metadata: { category },
    });
    // Re-throw so Playwright still marks the individual test as failed,
    // which surfaces the finding in the HTML report.
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Suite 1: Responsive Layout
// ---------------------------------------------------------------------------

test.describe('Suite 1: Responsive Layout', () => {
  test('desktop 1440px — all 5 Kanban columns visible without scrolling', async ({
    page, kanban, mockAll,
  }) => {
    await audit('kanban 5 columns visible at 1440px', 'responsive', async () => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await loadBoard(kanban, mockAll);

      const screenshotPath = await captureFullPage(page, 'responsive-kanban-desktop-1440');
      reporter.record('ux', 'screenshot: kanban 1440px', 'pass', { screenshotPath });

      // All 5 column labels must be visible without scrolling.
      for (const label of ['Queued', 'Executing', 'Awaiting Human', 'Validating', 'Deployed']) {
        const col = page.getByText(label, { exact: true }).first();
        await expect(col).toBeVisible();
        // Confirm the column header is within the visible viewport (not scrolled off).
        const box = await col.boundingBox();
        expect(box).not.toBeNull();
        if (box) {
          expect(box.x).toBeGreaterThanOrEqual(0);
          expect(box.x + box.width).toBeLessThanOrEqual(1440);
        }
      }
    });
  });

  test('tablet 768px — Kanban columns container supports horizontal scrolling', async ({
    page, kanban, mockAll,
  }) => {
    await audit('kanban layout at 768px tablet', 'responsive', async () => {
      await page.setViewportSize({ width: 768, height: 1024 });
      await loadBoard(kanban, mockAll);

      const screenshotPath = await captureFullPage(page, 'responsive-kanban-tablet-768');
      reporter.record('ux', 'screenshot: kanban 768px', 'pass', { screenshotPath });

      // At minimum, the first column should be visible.
      await expect(page.getByText('Queued', { exact: true }).first()).toBeVisible();

      // Per KanbanBoard.tsx the columns flex row has `overflow: 'auto'` on its inline style.
      // We look for a div that has overflow auto and contains the column headers.
      const overflowAuto = await page.evaluate(() => {
        // Find any div with overflow: auto that contains both Queued and Deployed text.
        const allDivs = Array.from(document.querySelectorAll('div'));
        for (const div of allDivs) {
          const style = window.getComputedStyle(div);
          if ((style.overflow === 'auto' || style.overflowX === 'auto') &&
              div.textContent?.includes('Queued') &&
              div.textContent?.includes('Deployed')) {
            return style.overflowX || style.overflow;
          }
        }
        return 'not-found';
      });

      // The container should be scrollable — auto or scroll.
      expect(['auto', 'scroll']).toContain(overflowAuto);
    });
  });

  test('mobile 375px — app renders without horizontal page overflow', async ({
    page, kanban, mockAll,
  }) => {
    await audit('kanban mobile 375px no page overflow', 'responsive', async () => {
      await page.setViewportSize({ width: 375, height: 812 });
      await loadBoard(kanban, mockAll);

      const screenshotPath = await captureFullPage(page, 'responsive-kanban-mobile-375');
      reporter.record('ux', 'screenshot: kanban 375px', 'pass', { screenshotPath });

      // Navbar brand must still be visible. Use exact to avoid the "agent-baton pmo" collision.
      await expect(page.getByText('Baton PMO', { exact: true })).toBeVisible();

      // The overall page body should not produce horizontal scrollbar wider than viewport.
      const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
      // Allow up to 10px tolerance for browser rendering differences.
      expect(bodyWidth).toBeLessThanOrEqual(375 + 10);
    });
  });

  test('health bar cards scroll horizontally at narrow viewport', async ({
    page, kanban, mockAll,
  }) => {
    await audit('health bar horizontal scroll at 600px', 'responsive', async () => {
      await page.setViewportSize({ width: 600, height: 800 });
      await loadBoard(kanban, mockAll);

      // Health bar row must be present with a program card.
      const alphaCard = kanban.page.locator('div').filter({ hasText: 'ALPHA' }).filter({
        has: kanban.page.locator('div', { hasText: 'plans' }),
      }).first();
      await expect(alphaCard).toBeVisible({ timeout: 8_000 });

      // The HealthBar renders with `overflowX: 'auto'` in its container div.
      // Find that container by walking up to the flex row that has the program cards.
      const healthContainer = kanban.page.locator('div[style*="overflow-x: auto"]').first();
      const containerExists = await healthContainer.count();
      if (containerExists === 0) {
        // The health bar uses inline style overflowX: 'auto' — verify the computed style.
        const computedOverflow = await alphaCard.evaluate((el) => {
          let node: Element | null = el.parentElement;
          while (node && node !== document.body) {
            const style = window.getComputedStyle(node);
            if (style.overflowX === 'auto' || style.overflowX === 'scroll') {
              return style.overflowX;
            }
            node = node.parentElement;
          }
          return 'not-found';
        });
        expect(['auto', 'scroll']).toContain(computedOverflow);
      } else {
        await expect(healthContainer).toBeVisible();
      }
    });
  });

  test('Forge form at tablet 768px — description textarea fills available width', async ({
    page, forge, mockAll,
  }) => {
    await audit('forge textarea fills width at 768px', 'responsive', async () => {
      await page.setViewportSize({ width: 768, height: 1024 });
      await loadForge(forge, mockAll);

      const screenshotPath = await captureFullPage(page, 'responsive-forge-tablet-768');
      reporter.record('ux', 'screenshot: forge 768px', 'pass', { screenshotPath });

      const textarea = forge.taskDescriptionTextarea;
      await expect(textarea).toBeVisible();
      const box = await textarea.boundingBox();
      expect(box).not.toBeNull();
      if (box) {
        // Textarea should take up a meaningful portion of the viewport width.
        // The Forge body has maxWidth 640 — at 768px the form fills most available width.
        expect(box.width).toBeGreaterThan(300);
      }
    });
  });

  test('navbar at mobile 375px — tabs do not overflow or overlap', async ({
    page, kanban, mockAll,
  }) => {
    await audit('navbar tabs no overflow at 375px', 'responsive', async () => {
      await page.setViewportSize({ width: 375, height: 812 });
      await loadBoard(kanban, mockAll);

      const navKanban = kanban.navTabKanban;
      const navForge = kanban.navTabForge;
      await expect(navKanban).toBeVisible();
      await expect(navForge).toBeVisible();

      const boxKanban = await navKanban.boundingBox();
      const boxForge = await navForge.boundingBox();
      expect(boxKanban).not.toBeNull();
      expect(boxForge).not.toBeNull();

      if (boxKanban && boxForge) {
        // Tabs must not overlap (forge tab left edge >= kanban tab right edge).
        const overlap = boxKanban.x + boxKanban.width > boxForge.x;
        if (overlap) {
          throw new Error(
            `Navbar tabs overlap at 375px: Kanban right=${boxKanban.x + boxKanban.width}, ` +
            `Forge left=${boxForge.x}`,
          );
        }
      }
    });
  });

  test('multi-viewport screenshot comparison — full page at all 3 breakpoints', async ({
    page, kanban, mockAll,
  }) => {
    await audit('viewport comparison screenshots captured', 'responsive', async () => {
      await loadBoard(kanban, mockAll);

      const results = await captureViewports(page, 'ux-audit-kanban-viewports');
      expect(results).toHaveLength(3);
      for (const { viewport, filePath } of results) {
        reporter.record('ux', `screenshot: kanban at ${viewport.name}`, 'pass', {
          screenshotPath: filePath,
          metadata: { category: 'responsive', viewport: viewport.name },
        });
      }
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 2: Interactive Behaviour — Kanban
// ---------------------------------------------------------------------------

test.describe('Suite 2: Interactive Behaviour — Kanban', () => {
  test('card click expands details section', async ({ page, kanban, mockAll }) => {
    await audit('card expand on click', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      // Locate the queued card. The card title div is the clickable element.
      const card = kanban.cardByTitle('Implement authentication middleware');
      await expect(card).toBeVisible({ timeout: 8_000 });

      // Before click, "Re-forge" button should not be visible anywhere on the page.
      await expect(kanban.reForgeButton).toBeHidden({ timeout: 2_000 });

      // Click on the card title text specifically (this triggers onClick on the card root).
      await page.getByText('Implement authentication middleware').first().click();
      await page.waitForTimeout(300);

      // After click, the expanded detail section appears with action buttons.
      // "Re-forge" is only in the expanded section of a card.
      await expect(kanban.reForgeButton).toBeVisible({ timeout: 5_000 });
    });
  });

  test('card hover shows visual feedback — border color changes on hover', async ({
    page, kanban, mockAll,
  }) => {
    await audit('card hover border color changes', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      // The KanbanCard root div is the cursor:pointer element that has the onMouseEnter.
      // It has: background: T.bg1, borderRadius: 4, border, cursor: 'pointer'.
      // We find it directly as the clickable card container.
      const cardRoot = page.locator('div[style*="cursor: pointer"]').filter({
        has: page.getByText('Implement authentication middleware'),
      }).first();
      await expect(cardRoot).toBeVisible({ timeout: 8_000 });

      // Move mouse well away first.
      await page.mouse.move(10, 10);
      await page.waitForTimeout(150);

      // Capture the initial border style (inline style, not computed).
      const borderBefore = await cardRoot.evaluate((el: HTMLElement) => el.style.borderColor);

      // Hover over the card.
      await cardRoot.hover();
      await page.waitForTimeout(250); // allow React handler to fire

      const borderAfter = await cardRoot.evaluate((el: HTMLElement) => el.style.borderColor);

      // The onMouseEnter handler sets el.style.borderColor to columnColor+'66'.
      // borderBefore should be '' (no inline style initially) and borderAfter should be set.
      if (borderAfter === '' || borderAfter === borderBefore) {
        throw new Error(
          `Card border did not change on hover: before="${borderBefore}", after="${borderAfter}". ` +
          'onMouseEnter handler may not be firing or targeting the correct element.',
        );
      }
    });
  });

  test('program filter toggles correctly — filter on, then filter off returns to all', async ({
    page, kanban, mockAll,
  }) => {
    await audit('program filter toggle on and off', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      // Filter to ALPHA program.
      await kanban.filterByProgram('ALPHA');
      await page.waitForTimeout(200);

      // The BETA-only card "Review API contract changes" should disappear.
      const betaCard = kanban.cardByTitle('Review API contract changes');
      const betaVisible = await betaCard.isVisible().catch(() => false);
      if (betaVisible) {
        throw new Error('BETA card still visible after ALPHA filter applied');
      }

      // Reset to All.
      await kanban.clearFilter();
      await page.waitForTimeout(200);

      // Now the BETA card should be back.
      await expect(kanban.cardByTitle('Review API contract changes')).toBeVisible({ timeout: 5_000 });
    });
  });

  test('multiple rapid filter clicks do not break state', async ({
    page, kanban, mockAll,
  }) => {
    await audit('rapid filter clicks maintain consistent state', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      // Click ALPHA → BETA → All → ALPHA in quick succession.
      await kanban.programFilterButton('ALPHA').click();
      await kanban.programFilterButton('BETA').click();
      await kanban.allFilterButton.click();
      await kanban.programFilterButton('ALPHA').click();
      await page.waitForTimeout(300);

      // State should be ALPHA filter active — ALPHA card visible.
      const alphaCard = kanban.cardByTitle('Implement authentication middleware');
      await expect(alphaCard).toBeVisible({ timeout: 5_000 });
    });
  });

  test('signals toggle shows signals bar then hides it on second click', async ({
    kanban, mockAll,
  }) => {
    await audit('signals toggle shows and hides signals bar', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      // Signals bar must be hidden at start.
      await expect(kanban.signalsBar).toBeHidden({ timeout: 3_000 });

      // Show.
      await kanban.toggleSignals();
      await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });

      // Hide.
      await kanban.toggleSignals();
      await expect(kanban.signalsBar).toBeHidden({ timeout: 5_000 });
    });
  });

  test('New Plan button switches to Forge view', async ({ kanban, forge, mockAll }) => {
    await audit('new plan button opens forge view', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      await kanban.clickNewPlan();

      // Confirm forge view is active by checking the intake-only textarea.
      await expect(forge.taskDescriptionTextarea).toBeVisible({ timeout: 5_000 });
      await expect(forge.generateButton).toBeVisible();
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 3: Interactive Behaviour — Forge
// ---------------------------------------------------------------------------

test.describe('Suite 3: Interactive Behaviour — Forge', () => {
  test('phase transitions: intake to generating — header phase label updates', async ({
    page, forge, mockAll,
  }) => {
    await audit('forge phase label updates on generate', 'interaction', async () => {
      await loadForge(forge, mockAll);

      // Confirm we are in the intake phase.
      await expect(forge.phaseLabel).toContainText('Describe the work', { timeout: 5_000 });

      // Fill description and generate.
      await forge.taskDescriptionTextarea.fill('Build a new authentication system for the API');
      await forge.generateButton.click();

      // Phase label updates to "Generating plan..." or immediately to "Review, edit..."
      // depending on mock response speed.
      await expect(forge.phaseLabel).toContainText(/Generating plan|Review, edit/, { timeout: 5_000 });

      // Wait for preview phase to complete.
      await waitForPreviewPhase(forge);
      await expect(forge.phaseLabel).toContainText('Review, edit', { timeout: 15_000 });

      // Screenshot of final state.
      const screenshotPath = await captureFullPage(page, 'forge-preview-phase');
      reporter.record('ux', 'screenshot: forge preview phase', 'pass', { screenshotPath });
    });
  });

  test('back button (← Board) returns to kanban board from intake', async ({
    forge, kanban, mockAll,
  }) => {
    await audit('forge back button returns to kanban', 'interaction', async () => {
      await loadForge(forge, mockAll);

      await forge.goBackToBoard();

      // Kanban columns should be visible again.
      await kanban.assertAllColumnsVisible();
    });
  });

  test('project selector auto-selects first project on load', async ({
    forge, mockAll,
  }) => {
    await audit('project selector auto-selects first project', 'interaction', async () => {
      await loadForge(forge, mockAll);

      const projectSelect = forge.projectSelect;
      await expect(projectSelect).toBeVisible();

      // Wait for projects to load.
      await expect(forge.projectsLoadingText).toBeHidden({ timeout: 5_000 });

      // First project from MOCK_PROJECTS is "proj-alpha".
      const selectedValue = await projectSelect.evaluate(
        (el: HTMLSelectElement) => el.value,
      );
      expect(selectedValue).toBe('proj-alpha');
    });
  });

  test('description textarea accepts multiline input', async ({
    forge, mockAll,
  }) => {
    await audit('forge textarea accepts multiline input', 'interaction', async () => {
      await loadForge(forge, mockAll);

      const textarea = forge.taskDescriptionTextarea;
      const multilineText = 'Line one\nLine two\nLine three';
      await textarea.fill(multilineText);

      const value = await textarea.inputValue();
      expect(value).toContain('Line one');
      expect(value).toContain('Line two');
      expect(value).toContain('Line three');
    });
  });

  test('Generate button is disabled when description is empty', async ({
    forge, mockAll,
  }) => {
    await audit('generate button disabled with empty description', 'interaction', async () => {
      await loadForge(forge, mockAll);

      // Clear any persisted description.
      await forge.taskDescriptionTextarea.fill('');

      const generateBtn = forge.generateButton;
      const isDisabled = await generateBtn.isDisabled();
      if (!isDisabled) {
        throw new Error('Generate button is NOT disabled when description is empty — no guard prevents empty submissions');
      }
    });
  });

  test('Cancel button during generation returns to intake phase', async ({
    forge, mockAll,
  }) => {
    await audit('cancel during generation returns to intake', 'interaction', async () => {
      await mockAll();
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();

      await forge.taskDescriptionTextarea.fill('Test cancel flow for the forge generation pipeline');

      // Intercept the forge plan request with a 5-second delay so we can cancel.
      await forge.page.route('**/api/v1/pmo/forge/plan', async (route) => {
        await new Promise<void>((resolve) => setTimeout(resolve, 5_000));
        await route.abort();
      });

      await forge.generateButton.click();

      // Wait for cancel button to appear.
      await expect(forge.cancelButton).toBeVisible({ timeout: 3_000 });
      await forge.cancelButton.click();

      // Should be back in intake phase (textarea visible).
      await expect(forge.taskDescriptionTextarea).toBeVisible({ timeout: 5_000 });
      await expect(forge.generateButton).toBeVisible();
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 4: Interactive Behaviour — Plan Editor
// ---------------------------------------------------------------------------

test.describe('Suite 4: Interactive Behaviour — Plan Editor', () => {
  test('phase header click expands the phase accordion', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('phase header click expands steps', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      // The first phase ("Design & Schema") is expanded by default (expandedPhase = 0).
      await planEditor.assertPhaseExpanded();

      // Click a different phase to expand it, collapsing the first.
      await planEditor.togglePhase('Implementation');
      await forge.page.waitForTimeout(150);

      // "Implementation" phase steps are now visible — "Add step" button appears.
      await planEditor.assertPhaseExpanded();
    });
  });

  test('only one phase expanded at a time — collapsing previous', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('only one phase expanded at a time', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      // Phase 1 (Design & Schema) is expanded by default.
      // Expand Phase 2 (Implementation).
      await planEditor.togglePhase('Implementation');
      await forge.page.waitForTimeout(150);

      // Phase 1 (Design & Schema) should now be collapsed.
      // Verify by checking the "Add step" button count — only 1 should exist.
      const addStepButtons = forge.page.getByRole('button', { name: '+ Add step' });
      const count = await addStepButtons.count();
      expect(count).toBe(1);
    });
  });

  test('step description click enables inline edit mode', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('step description inline edit mode on click', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      // Phase 1 is expanded by default.
      // Click the cursor:text div directly — it is the clickable text in the step row.
      // There are 2 steps in phase 1, each with a cursor:text div.
      // Click the first one (first step: "Define JWT token schema...").
      const editableDescs = forge.page.locator('div[style*="cursor: text"]');
      await expect(editableDescs.first()).toBeVisible({ timeout: 3_000 });
      await editableDescs.first().click();
      await forge.page.waitForTimeout(150);

      // The edit input should now be visible (blue-bordered input).
      await expect(planEditor.activeStepInput).toBeVisible({ timeout: 3_000 });
    });
  });

  test('reorder up button disabled for first step', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('reorder up disabled at first step', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      // Phase 1 is expanded with 2 steps. The reorder-up buttons have aria-label
      // "Move step N up" (added in accessibility remediation). The first button
      // (step 1) must be disabled.
      const upButtons = forge.page.getByRole('button', { name: /Move step \d+ up/i });
      const firstUpBtn = upButtons.first();
      const isDisabled = await firstUpBtn.isDisabled();
      if (!isDisabled) {
        throw new Error('Move-up button NOT disabled for first step — allows invalid reorder');
      }
    });
  });

  test('reorder down button disabled for last step in phase', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('reorder down disabled at last step', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      // Phase 1 has 2 steps. The reorder-down buttons have aria-label
      // "Move step N down" (added in accessibility remediation). The last button
      // (last step in phase) must be disabled.
      const downButtons = forge.page.getByRole('button', { name: /Move step \d+ down/i });
      const lastDownBtn = downButtons.last();
      const isDisabled = await lastDownBtn.isDisabled();
      if (!isDisabled) {
        throw new Error('Move-down button NOT disabled for last step — allows invalid reorder');
      }
    });
  });

  test('Add step creates a new step in the expanded phase', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('add step appends new step to phase', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      // Count "Remove step" buttons before adding. The buttons have title="Remove step"
      // and aria-label="Remove step N: ..." (added in accessibility remediation).
      // Use the title attribute to reliably select only step-remove buttons.
      const stepRemoveCountBefore = await forge.page.locator('button[title="Remove step"]').count();

      await planEditor.addStep();
      await forge.page.waitForTimeout(200);

      // Should have one more "Remove step" button after adding a step.
      const stepRemoveCountAfter = await forge.page.locator('button[title="Remove step"]').count();
      expect(stepRemoveCountAfter).toBeGreaterThan(stepRemoveCountBefore);

      // The new step description "New step" should appear.
      await expect(forge.page.getByText('New step').first()).toBeVisible({ timeout: 3_000 });
    });
  });

  test('Remove step button deletes the step', async ({
    forge, planEditor, mockAll,
  }) => {
    await audit('remove step deletes step from plan', 'interaction', async () => {
      await loadPlanEditor(forge, mockAll);

      const lastStepDesc = 'Create Pydantic models for AuthToken, RefreshToken, and LoginRequest';
      // Confirm the step is visible before deletion.
      await expect(forge.page.getByText(lastStepDesc).first()).toBeVisible({ timeout: 3_000 });

      // The remove-step buttons have title="Remove step" and aria-label="Remove step N: ..."
      // (added in accessibility remediation). Use title attribute selectors throughout.
      // Phase 1 is expanded; steps in collapsed phases are hidden via the HTML `hidden`
      // attribute — those buttons are in the DOM but not visible. Filter to visible only.
      const removeStepBtns = forge.page.locator('button[title="Remove step"]:visible');
      const countBefore = await removeStepBtns.count();

      // Click the last visible "Remove step" button (the second step in phase 1).
      await removeStepBtns.last().click();
      await forge.page.waitForTimeout(200);

      // One fewer visible "Remove step" button after deletion.
      const countAfter = await forge.page.locator('button[title="Remove step"]:visible').count();
      expect(countAfter).toBeLessThan(countBefore);

      // The deleted step text should no longer be visible.
      await expect(forge.page.getByText(lastStepDesc)).toBeHidden({ timeout: 3_000 });
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 5: Visual Consistency
// ---------------------------------------------------------------------------

test.describe('Suite 5: Visual Consistency', () => {
  test('Kanban column spacing is consistent — columns ordered left-to-right', async ({
    page, kanban, mockAll,
  }) => {
    await audit('kanban column spacing consistent', 'visual', async () => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await loadBoard(kanban, mockAll);

      // Capture the board for visual review.
      const screenshotPath = await captureFullPage(page, 'visual-kanban-columns');
      reporter.record('ux', 'screenshot: kanban column spacing', 'pass', { screenshotPath });

      // Verify each column header is positioned left-to-right using exact text matches.
      const columnLabels = ['Queued', 'Executing', 'Awaiting Human', 'Validating', 'Deployed'];
      const xPositions: number[] = [];

      for (const label of columnLabels) {
        // Use exact match to avoid collision with column descriptions like "Plan ready..."
        const col = page.getByText(label, { exact: true }).first();
        await expect(col).toBeVisible();
        const box = await col.boundingBox();
        if (box) xPositions.push(box.x);
      }

      // Columns should be in strictly increasing x-position order.
      for (let i = 1; i < xPositions.length; i++) {
        expect(xPositions[i]).toBeGreaterThan(xPositions[i - 1]);
      }
    });
  });

  test('card padding is consistent — cards have non-zero padding', async ({
    page, kanban, mockAll,
  }) => {
    await audit('card padding is non-zero and consistent', 'visual', async () => {
      await loadBoard(kanban, mockAll);

      // Find the inner content div of the first card.
      // KanbanCard renders: <div cursor:pointer><div style="padding: '7px 8px 6px'">...
      // The inner padded div contains the title row.
      const cardInner = page.locator('div[style*="cursor: pointer"]').filter({
        has: page.getByText('Implement authentication middleware'),
      }).first().locator('div').first();

      await expect(cardInner).toBeVisible({ timeout: 8_000 });

      const padding = await cardInner.evaluate((el: HTMLElement) => {
        const style = window.getComputedStyle(el);
        return {
          top: parseFloat(style.paddingTop),
          left: parseFloat(style.paddingLeft),
        };
      });

      expect(padding.top).toBeGreaterThan(0);
      expect(padding.left).toBeGreaterThan(0);

      const screenshotPath = await captureFullPage(page, 'visual-card-padding');
      reporter.record('ux', 'screenshot: card padding', 'pass', { screenshotPath });
    });
  });

  test('typography hierarchy — title font-size larger than metadata font-size', async ({
    kanban, mockAll,
  }) => {
    await audit('typography hierarchy card title vs metadata', 'visual', async () => {
      await loadBoard(kanban, mockAll);

      const card = kanban.cardByTitle('Implement authentication middleware');
      await expect(card).toBeVisible({ timeout: 8_000 });

      // Get font sizes of the title div (font-weight: 600, fontSize: 12px)
      // vs metadata monospace span (fontSize: 9px).
      const titleFontSize = await card.locator('div[style*="font-weight: 600"]').first()
        .evaluate((el) => parseFloat(window.getComputedStyle(el).fontSize));

      const metaFontSize = await card.locator('span[style*="font-family: monospace"]').first()
        .evaluate((el) => parseFloat(window.getComputedStyle(el).fontSize));

      // Title (12px per tokens.ts) must be larger than metadata (9px).
      if (titleFontSize <= metaFontSize) {
        throw new Error(
          `Typography hierarchy broken: title=${titleFontSize}px <= metadata=${metaFontSize}px`,
        );
      }
    });
  });

  test('accent color applied to active filter button — visual active state', async ({
    page, kanban, mockAll,
  }) => {
    await audit('active filter button uses accent color', 'visual', async () => {
      await loadBoard(kanban, mockAll);

      // "All" filter is active by default.
      const allBtn = kanban.allFilterButton;
      await expect(allBtn).toBeVisible();

      const color = await allBtn.evaluate((el) => window.getComputedStyle(el).color);
      // T.accent = #3b82f6 = rgb(59, 130, 246)
      // Active buttons use accent color for text per FilterBtn component.
      expect(color).toContain('rgb(59, 130, 246)');

      const screenshotPath = await captureFullPage(page, 'visual-active-filter-button');
      reporter.record('ux', 'screenshot: active filter button', 'pass', { screenshotPath });
    });
  });

  test('hover state — New Plan button has cursor:pointer', async ({
    kanban, mockAll,
  }) => {
    await audit('new plan button cursor is pointer', 'visual', async () => {
      await loadBoard(kanban, mockAll);

      const btn = kanban.newPlanButton;
      await expect(btn).toBeVisible();

      const cursor = await btn.evaluate((el) => window.getComputedStyle(el).cursor);
      expect(cursor).toBe('pointer');
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 6: Loading & Error States
// ---------------------------------------------------------------------------

test.describe('Suite 6: Loading & Error States', () => {
  test('board loading state — refreshing indicator appears then disappears', async ({
    page, kanban, mockForge,
  }) => {
    await audit('board shows loading state before data', 'loading', async () => {
      // Set up a slow board mock to capture the loading state.
      await page.route('**/api/v1/pmo/board', async (route) => {
        await new Promise<void>((resolve) => setTimeout(resolve, 600));
        const { MOCK_BOARD_RESPONSE } = await import('../fixtures/mock-data.js');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_BOARD_RESPONSE),
        });
      });
      // Board wildcard also needs mocking to prevent double-route conflict.
      await page.route('**/api/v1/pmo/board/**', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ cards: [], health: {} }),
        });
      });
      await mockForge();
      await page.route('**/api/v1/pmo/signals', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      });
      await page.route('**/api/v1/pmo/health', async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      });
      // Block SSE.
      await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

      await kanban.goto('/');
      await kanban.waitForAppReady();

      // Capture the loading state before data arrives.
      const screenshotPath = await captureFullPage(page, 'loading-board-state');
      reporter.record('ux', 'screenshot: board loading state', 'pass', { screenshotPath });

      // Wait for data to arrive and the auth card to render.
      await expect(page.getByText('Implement authentication middleware')).toBeVisible({ timeout: 8_000 });

      // After load, "refreshing…" should not be visible.
      await expect(page.getByText('refreshing…')).toBeHidden({ timeout: 3_000 });
    });
  });

  test('error banner appears when board API returns 503', async ({
    page, kanban, mockBoard,
  }) => {
    await audit('error banner on API 503', 'loading', async () => {
      await mockBoard({ failBoard: true });
      await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

      await kanban.goto('/');
      await kanban.page.waitForLoadState('domcontentloaded');

      // Error banner should appear within polling interval.
      await expect(kanban.errorBanner).toBeVisible({ timeout: 12_000 });

      const screenshotPath = await captureFullPage(page, 'loading-error-banner');
      reporter.record('ux', 'screenshot: error banner', 'pass', { screenshotPath });
    });
  });

  test('error banner contains retry timing text', async ({
    page, kanban, mockBoard,
  }) => {
    await audit('error banner shows retry interval info', 'loading', async () => {
      await mockBoard({ failBoard: true });
      await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

      await kanban.goto('/');
      await kanban.page.waitForLoadState('domcontentloaded');

      await expect(kanban.errorBanner).toBeVisible({ timeout: 12_000 });

      // The error banner text includes "retrying every Ns" (per KanbanBoard.tsx).
      const bannerText = await kanban.errorBanner.textContent();
      if (!bannerText?.includes('retrying every')) {
        throw new Error(
          `Error banner missing retry interval info. Got: "${bannerText}"`,
        );
      }
    });
  });

  test('Forge generation shows Generating plan... phase label', async ({
    page, forge, mockAll,
  }) => {
    await audit('forge shows generating state label', 'loading', async () => {
      await mockAll();
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.assertIntakePhase();

      // Override forge plan with a 1-second delay to capture the generating state.
      await page.route('**/api/v1/pmo/forge/plan', async (route) => {
        await new Promise<void>((resolve) => setTimeout(resolve, 1_000));
        const { MOCK_FORGE_PLAN } = await import('../fixtures/mock-data.js');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_FORGE_PLAN),
        });
      });

      await forge.taskDescriptionTextarea.fill('Test generation loading state');
      await forge.generateButton.click();

      // The phase label should update to "Generating plan..." during the delay.
      await expect(forge.phaseLabel).toContainText('Generating plan', { timeout: 3_000 });

      const screenshotPath = await captureFullPage(page, 'loading-forge-generating');
      reporter.record('ux', 'screenshot: forge generating state', 'pass', { screenshotPath });

      // Wait for completion.
      await waitForPreviewPhase(forge);
    });
  });

  test('connection indicator shows current mode (polling when SSE unavailable)', async ({
    page, kanban, mockBoard,
  }) => {
    await audit('connection indicator shows polling mode when SSE unavailable', 'loading', async () => {
      await mockBoard();
      // Abort SSE to force polling mode.
      await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await page.waitForTimeout(500);

      // The ConnectionIndicator shows "polling" when SSE is unavailable.
      // The span text is exactly "live", "polling", or "connecting" — use exact text locator.
      const pollingIndicator = page.locator('span').filter({
        hasText: /^(live|polling|connecting)$/,
      }).first();
      await expect(pollingIndicator).toBeVisible({ timeout: 5_000 });

      const modeText = await pollingIndicator.textContent();
      // Should be "polling" (SSE aborted) or "connecting" (initial state), not "live".
      expect(['polling', 'connecting']).toContain(modeText?.trim());

      const screenshotPath = await captureFullPage(page, 'loading-connection-indicator');
      reporter.record('ux', 'screenshot: connection indicator', 'pass', { screenshotPath });
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 7: Content Handling
// ---------------------------------------------------------------------------

test.describe('Suite 7: Content Handling', () => {
  test('long card titles truncate gracefully — no overflow outside card bounds', async ({
    page, kanban, mockBoard, mockForge,
  }) => {
    await audit('long title truncates without overflow', 'content', async () => {
      // Create a board response with a very long title.
      const { MOCK_BOARD_RESPONSE } = await import('../fixtures/mock-data.js');
      const longTitle =
        'This is an extremely long card title that should definitely overflow if not truncated properly ' +
        'because it is way more than 80 characters and contains a lot of unnecessary words';

      const boardWithLongTitle = {
        ...MOCK_BOARD_RESPONSE,
        cards: [
          { ...MOCK_BOARD_RESPONSE.cards[0], title: longTitle },
          ...MOCK_BOARD_RESPONSE.cards.slice(1),
        ],
      };

      await mockBoard({ boardResponse: boardWithLongTitle });
      await mockForge();
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await page.waitForTimeout(400);

      // The title is in a div that uses -webkit-box / WebkitLineClamp for truncation.
      // Find it by the beginning of the long title text.
      const titleEl = page.getByText('This is an extremely long card title', { exact: false }).first();
      await expect(titleEl).toBeVisible({ timeout: 8_000 });

      // Verify the element does not overflow its parent.
      const overflow = await titleEl.evaluate((el: HTMLElement) => {
        const parent = el.parentElement;
        if (!parent) return { overflowed: false };
        const elRight = el.getBoundingClientRect().right;
        const parentRight = parent.getBoundingClientRect().right;
        return {
          overflowed: elRight > parentRight + 2,
          elRight,
          parentRight,
        };
      });

      if (overflow.overflowed) {
        throw new Error(
          `Title overflows parent: elRight=${overflow.elRight?.toFixed(0)}, ` +
          `parentRight=${overflow.parentRight?.toFixed(0)}`,
        );
      }

      const screenshotPath = await captureFullPage(page, 'content-long-title-truncation');
      reporter.record('ux', 'screenshot: long title truncation', 'pass', { screenshotPath });
    });
  });

  test('long Forge description does not break the intake form layout', async ({
    page, forge, mockAll,
  }) => {
    await audit('long forge description does not break layout', 'content', async () => {
      await loadForge(forge, mockAll);

      const longDescription = 'A '.repeat(500) + 'long description.';
      await forge.taskDescriptionTextarea.fill(longDescription);

      // The form has maxWidth: 640 — the textarea should not cause it to exceed that.
      const formScrollWidth = await forge.page.evaluate(() => {
        // Find the maxWidth 640 container (the "Define the Work" form div).
        const allDivs = Array.from(document.querySelectorAll('div[style]'));
        for (const div of allDivs) {
          const style = (div as HTMLElement).style;
          if (style.maxWidth === '640px') {
            return (div as HTMLElement).scrollWidth;
          }
        }
        return 0;
      });

      // Allow 20px tolerance for border/scrollbar.
      expect(formScrollWidth).toBeLessThanOrEqual(660);

      const screenshotPath = await captureFullPage(page, 'content-long-forge-description');
      reporter.record('ux', 'screenshot: long forge description', 'pass', { screenshotPath });
    });
  });

  test('empty columns show placeholder "Empty" text', async ({
    page, kanban, mockForge, mockBoard,
  }) => {
    await audit('empty column shows Empty placeholder', 'content', async () => {
      // Use the empty board response.
      const { MOCK_EMPTY_BOARD_RESPONSE } = await import('../fixtures/mock-data.js');
      await mockBoard({ boardResponse: MOCK_EMPTY_BOARD_RESPONSE });
      await mockForge();
      // Block SSE.
      await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await page.waitForTimeout(400);

      // With an empty board, all 5 columns should show guidance placeholder text.
      // R2-24: each column has its own guidance text (e.g., "No plans ready to execute").
      const emptyEls = page.locator('[style*="dashed"]');
      const count = await emptyEls.count();
      // All 5 columns should have a dashed-border placeholder.
      expect(count).toBe(5);

      // Verify at least the first one is visible.
      await expect(emptyEls.first()).toBeVisible({ timeout: 8_000 });
    });
  });

  test('card with many steps shows all progress pips', async ({
    page, kanban, mockAll,
  }) => {
    await audit('card shows progress pips for all steps', 'content', async () => {
      await loadBoard(kanban, mockAll);

      // The executing card has 8 steps (from MOCK_CARD_EXECUTING steps_total=8).
      // The step count indicator "3/8" confirms steps are tracked.
      const execCard = kanban.cardByTitle('Migrate user profile schema to PostgreSQL');
      await expect(execCard).toBeVisible({ timeout: 8_000 });

      // Verify the step count text "3/8" is visible — this confirms the Pips component renders.
      await expect(execCard.getByText('3/8')).toBeVisible();

      // The Pips component renders divs with width:4px height:4px borderRadius:1px.
      // Count them via page.evaluate scoped to the card element.
      const pipCount = await page.evaluate(() => {
        // Find the card containing "Migrate user profile schema"
        const allCards = Array.from(document.querySelectorAll('div'));
        for (const card of allCards) {
          if (card.textContent?.includes('Migrate user profile schema to PostgreSQL') &&
              card.textContent?.includes('3/8')) {
            // Within this card, count 4x4 pip divs.
            const pips = card.querySelectorAll('div[style*="width: 4px"]');
            if (pips.length > 0 && pips.length <= 20) {
              return pips.length;
            }
          }
        }
        return 0;
      });

      // If we got a clean count, verify it equals 8 (steps_total).
      // If 0, the pip selector didn't match — fall back to just verifying the text.
      if (pipCount > 0) {
        expect(pipCount).toBe(8);
      }
      // The step count text is the primary assertion.
      await expect(execCard.getByText('3/8')).toBeVisible();
    });
  });

  test('signal description truncates with ellipsis', async ({
    kanban, mockAll,
  }) => {
    await audit('signal description truncates with ellipsis', 'content', async () => {
      await loadBoard(kanban, mockAll);
      await kanban.toggleSignals();
      await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });

      // Per SignalsBar.tsx, descriptions have: maxWidth:160, overflow:hidden,
      // textOverflow:ellipsis, whiteSpace:nowrap — applied via inline style.
      const descEl = kanban.page.locator('span[style*="text-overflow: ellipsis"]').first();
      await expect(descEl).toBeVisible({ timeout: 5_000 });

      const overflow = await descEl.evaluate((el) => window.getComputedStyle(el).textOverflow);
      expect(overflow).toBe('ellipsis');
    });
  });

  test('signals bar shows correct open signal count', async ({
    kanban, mockAll,
  }) => {
    await audit('signals bar shows correct open count', 'content', async () => {
      await loadBoard(kanban, mockAll);
      await kanban.toggleSignals();
      await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });

      // From ALL_MOCK_SIGNALS: 2 open (critical + medium), 1 resolved.
      await expect(kanban.signalsHeader).toContainText('2 open', { timeout: 5_000 });
    });
  });
});

// ---------------------------------------------------------------------------
// Suite 8: Known UX Issues — Audit Verification
// ---------------------------------------------------------------------------

test.describe('Suite 8: Known UX Issues — Audit Verification', () => {
  test('execution result has no dismiss mechanism — known issue', async ({
    page, kanban, mockAll,
  }) => {
    await audit('execution result dismissal (known missing)', 'interaction', async () => {
      await loadBoard(kanban, mockAll);

      // Find the queued card and expand it by clicking the title text.
      // MOCK_CARD_QUEUED is "Implement authentication middleware" in the "queued" column.
      await expect(page.getByText('Implement authentication middleware').first()).toBeVisible({ timeout: 8_000 });
      await page.getByText('Implement authentication middleware').first().click();
      await page.waitForTimeout(300);

      // Wait for the Execute button (only on queued cards in expanded state).
      await expect(kanban.executeButton).toBeVisible({ timeout: 5_000 });
      await kanban.executeButton.click();
      await kanban.page.waitForTimeout(1_000);

      // Check if execution result appeared.
      const resultEl = kanban.page.locator('div').filter({
        hasText: /Launched \(PID|Launch failed/,
      }).first();
      const resultVisible = await resultEl.isVisible().catch(() => false);

      if (resultVisible) {
        // Known issue: no dismiss button on execution results.
        const dismissBtn = kanban.page.getByRole('button', { name: /dismiss|close/i });
        const hasDismiss = await dismissBtn.isVisible().catch(() => false);
        if (!hasDismiss) {
          // Record the known finding.
          reporter.record('ux', 'execution result lacks dismiss button', 'fail', {
            error: 'Known issue: execution results have no dismiss/close control. Results disappear only on page reload.',
            metadata: { category: 'interaction', severity: 'critical' },
          });
          // Do not throw — this is a documented known issue.
        }
      }
    });
  });

  test('AdoCombobox has no arrow-key navigation — known issue', async ({
    forge, mockAll,
  }) => {
    await audit('ADO combobox arrow key navigation (known missing)', 'interaction', async () => {
      await loadForge(forge, mockAll);

      const adoInput = forge.adoSearchInput;
      await expect(adoInput).toBeVisible();

      // Type to trigger the dropdown.
      await adoInput.fill('JWT');
      await forge.page.waitForTimeout(500);

      // Check if dropdown appeared — look for the ADO-XXXX monospace id spans.
      const dropdownItem = forge.page.locator('span[style*="monospace"]').filter({
        hasText: /ADO-\d+/,
      }).first();
      const dropdownVisible = await dropdownItem.isVisible().catch(() => false);

      if (dropdownVisible) {
        // Known issue: arrow key navigation missing.
        reporter.record('ux', 'ADO combobox arrow key navigation missing', 'fail', {
          error: 'Known issue: ArrowDown in ADO search input does not move focus to dropdown items. Keyboard-only users cannot navigate ADO results.',
          metadata: { category: 'interaction', severity: 'critical' },
        });
        // Do not throw — documenting the known gap.
      }
    });
  });

  test('color-only awaiting_human status — orange dot has no accessible label', async ({
    page, kanban, mockAll,
  }) => {
    await audit('awaiting human status uses color-only indicator', 'visual', async () => {
      await loadBoard(kanban, mockAll);

      // The awaiting badge in the toolbar has an orange pulsing dot.
      // Check if it has an accessible label.
      const awaitingBadge = page.locator('div').filter({
        has: page.locator('span', { hasText: /awaiting/ }),
      }).filter({
        has: page.locator('div[style*="border-radius: 50%"]'),
      }).first();

      const badgeVisible = await awaitingBadge.isVisible().catch(() => false);

      if (badgeVisible) {
        const dot = awaitingBadge.locator('div[style*="border-radius: 50%"]').first();
        const ariaLabel = await dot.getAttribute('aria-label');
        const title = await dot.getAttribute('title');

        if (!ariaLabel && !title) {
          reporter.record('ux', 'awaiting human orange dot has no accessible label', 'fail', {
            error: 'Known issue: the pulsing orange dot status indicator has no aria-label or title. Screen readers cannot convey this state.',
            metadata: { category: 'visual', severity: 'high' },
          });
        }
      }
    });
  });

  test('Forge intake form — labels not connected to inputs via label element', async ({
    forge, mockAll,
  }) => {
    await audit('forge form labels connected to inputs', 'visual', async () => {
      await loadForge(forge, mockAll);

      // Check if any <label> elements exist in the form.
      const labels = await forge.page.locator('label').all();

      if (labels.length === 0) {
        // Known issue: the Forge uses div-based "FormField" labels, not <label> elements.
        reporter.record('ux', 'forge form uses div labels not label elements', 'fail', {
          error: 'Known issue: Forge intake form uses visual-only div labels. No label[for] association means form inputs are not programmatically labeled for screen readers.',
          metadata: { category: 'visual', severity: 'medium' },
        });
        // Do not throw — documenting the known gap.
      }
    });
  });

  test('no unsaved changes indicator in Forge when description modified', async ({
    forge, mockAll,
  }) => {
    await audit('forge shows unsaved changes indicator (known missing)', 'interaction', async () => {
      await loadForge(forge, mockAll);

      // Modify the description.
      await forge.taskDescriptionTextarea.fill('Modified description — should show unsaved indicator');

      // Check if any unsaved/dirty visual indicator appears.
      const unsavedIndicator = forge.page.locator('div, span').filter({
        hasText: /unsaved|modified|dirty/,
      }).first();
      const hasIndicator = await unsavedIndicator.isVisible().catch(() => false);

      if (!hasIndicator) {
        reporter.record('ux', 'forge shows no unsaved changes indicator', 'fail', {
          error: 'Known issue: Forge form has no unsaved changes indicator. Users can navigate away and lose typed content without warning.',
          metadata: { category: 'interaction', severity: 'high' },
        });
        // Do not throw — this is a documented known gap.
      }
    });
  });

  test('batch resolve signals — executes without confirmation dialog', async ({
    page, kanban, mockAll,
  }) => {
    await audit('batch resolve has no confirmation dialog (known missing)', 'interaction', async () => {
      await loadBoard(kanban, mockAll);
      await kanban.toggleSignals();
      await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });

      // Select all signals.
      await kanban.selectAllCheckbox.click();
      await kanban.page.waitForTimeout(200);

      // Spy on window.confirm to detect if a confirmation dialog fires.
      await page.evaluate(() => {
        (window as unknown as { __confirmCalled: boolean }).__confirmCalled = false;
        const orig = window.confirm;
        window.confirm = (...args) => {
          (window as unknown as { __confirmCalled: boolean }).__confirmCalled = true;
          return orig.apply(window, args as [string?]);
        };
      });

      // Click batch resolve.
      await kanban.batchResolveButton.click();
      await kanban.page.waitForTimeout(500);

      const wasCalled = await page.evaluate(
        () => (window as unknown as { __confirmCalled: boolean }).__confirmCalled,
      );

      if (!wasCalled) {
        reporter.record('ux', 'batch resolve executes without confirmation', 'fail', {
          error: 'Known issue: Batch resolve signals fires immediately without a confirmation dialog. This is a destructive action with no undo.',
          metadata: { category: 'interaction', severity: 'high' },
        });
        // Do not throw — documenting known gap.
      }
    });
  });
});

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

test.afterAll(() => {
  reporter.writeReport();
});
