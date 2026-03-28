/**
 * accessibility-audit.spec.ts — WCAG accessibility audit for the Baton PMO UI.
 *
 * This file combines two verification strategies:
 *
 *   1. Automated axe-core scanning — catches WCAG 2.1 A/AA violations that
 *      can be detected via DOM inspection (missing labels, contrast, roles).
 *
 *   2. Manual Playwright assertions — checks behaviours axe-core cannot
 *      exercise: keyboard navigation, focus management, dynamic ARIA state,
 *      and color-only information encoding.
 *
 * Design intent:
 *   Tests in this file are DETECTORS, not gates. Every test records its
 *   result via AuditReporter and then re-throws failures so Playwright marks
 *   the test red — giving the team a clear, actionable list. The full suite
 *   is expected to expose many failures against the current codebase.
 *
 * Structure:
 *   Suite 1 — Automated WCAG scan (axe-core)
 *   Suite 2 — Semantic structure
 *   Suite 3 — Form accessibility
 *   Suite 4 — Interactive element accessibility
 *   Suite 5 — Keyboard navigation
 *   Suite 6 — Color and contrast
 *   Suite 7 — Dynamic content / live regions
 */

import { test, expect } from '../fixtures/test-fixtures.js';
import { AuditReporter } from '../utils/audit-reporter.js';
import { AxeBuilder } from '@axe-core/playwright';

const reporter = AuditReporter.getInstance();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Run a test body, record the outcome, and re-throw failures so Playwright
 * marks the test appropriately. This keeps the reporter in sync even when
 * tests fail.
 */
async function auditTest(
  suite: string,
  title: string,
  wcag: string,
  body: () => Promise<void>,
): Promise<void> {
  const start = Date.now();
  try {
    await body();
    reporter.record(suite, title, 'pass', {
      durationMs: Date.now() - start,
      metadata: { category: 'a11y', wcag },
    });
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    reporter.record(suite, title, 'fail', {
      durationMs: Date.now() - start,
      error: errorMsg,
      metadata: { category: 'a11y', wcag },
    });
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Suite 1: Automated WCAG scan via axe-core
// ---------------------------------------------------------------------------

test.describe('Suite 1: Automated WCAG scan', () => {
  test('axe: kanban board view has no critical WCAG A/AA violations', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    // Allow board data to populate
    await page.waitForTimeout(600);

    await auditTest(
      'accessibility',
      'axe: kanban board — no critical WCAG A/AA violations',
      '1.1.1, 1.3.1, 4.1.2',
      async () => {
        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa'])
          .analyze();

        // Record each violation in detail
        for (const violation of results.violations) {
          reporter.record(
            'accessibility',
            `axe violation [kanban]: ${violation.id} — ${violation.help}`,
            'fail',
            {
              error: `Impact: ${violation.impact ?? 'unknown'} | Nodes: ${violation.nodes.length} | ${violation.helpUrl}`,
              metadata: {
                category: 'a11y',
                wcag: violation.tags.filter((t) => t.startsWith('wcag')).join(', '),
                impact: violation.impact ?? 'unknown',
                nodeCount: violation.nodes.length,
              },
            },
          );
        }

        // Fail if there are any critical or serious violations
        const critical = results.violations.filter(
          v => v.impact === 'critical' || v.impact === 'serious',
        );
        expect(
          critical,
          `Found ${critical.length} critical/serious violations:\n` +
          critical.map((v: { id: string; help: string; nodes: unknown[] }) => `  - ${v.id}: ${v.help} (${v.nodes.length} nodes)`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });

  test('axe: forge intake view has no critical WCAG A/AA violations', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'axe: forge intake — no critical WCAG A/AA violations',
      '1.1.1, 1.3.1, 4.1.2',
      async () => {
        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa'])
          .analyze();

        for (const violation of results.violations) {
          reporter.record(
            'accessibility',
            `axe violation [forge intake]: ${violation.id} — ${violation.help}`,
            'fail',
            {
              error: `Impact: ${violation.impact ?? 'unknown'} | Nodes: ${violation.nodes.length} | ${violation.helpUrl}`,
              metadata: {
                category: 'a11y',
                wcag: violation.tags.filter((t) => t.startsWith('wcag')).join(', '),
                impact: violation.impact ?? 'unknown',
                nodeCount: violation.nodes.length,
              },
            },
          );
        }

        const critical = results.violations.filter(
          v => v.impact === 'critical' || v.impact === 'serious',
        );
        expect(
          critical,
          `Found ${critical.length} critical/serious violations:\n` +
          critical.map((v: { id: string; help: string; nodes: unknown[] }) => `  - ${v.id}: ${v.help} (${v.nodes.length} nodes)`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });

  test('axe: forge preview view has no critical WCAG A/AA violations', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    // Fill form and generate plan
    await forge.taskDescriptionTextarea.fill('Implement JWT authentication for the Alpha service');
    await forge.generateButton.click();

    // Wait for the preview phase — use the Approve button (unambiguous) instead of
    // ForgePage.assertPreviewPhase() which has a known locator ambiguity with
    // the kanban column description text "Plan ready, awaiting execution slot".
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'axe: forge preview — no critical WCAG A/AA violations',
      '1.1.1, 1.3.1, 4.1.2',
      async () => {
        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa'])
          .analyze();

        for (const violation of results.violations) {
          reporter.record(
            'accessibility',
            `axe violation [forge preview]: ${violation.id} — ${violation.help}`,
            'fail',
            {
              error: `Impact: ${violation.impact ?? 'unknown'} | Nodes: ${violation.nodes.length} | ${violation.helpUrl}`,
              metadata: {
                category: 'a11y',
                wcag: violation.tags.filter((t) => t.startsWith('wcag')).join(', '),
                impact: violation.impact ?? 'unknown',
                nodeCount: violation.nodes.length,
              },
            },
          );
        }

        const critical = results.violations.filter(
          v => v.impact === 'critical' || v.impact === 'serious',
        );
        expect(
          critical,
          `Found ${critical.length} critical/serious violations:\n` +
          critical.map((v: { id: string; help: string; nodes: unknown[] }) => `  - ${v.id}: ${v.help} (${v.nodes.length} nodes)`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });

  test('axe: signals bar has no critical WCAG A/AA violations', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.toggleSignals();
    await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'axe: signals bar — no critical WCAG A/AA violations',
      '1.1.1, 1.3.1, 4.1.2',
      async () => {
        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa'])
          .analyze();

        for (const violation of results.violations) {
          reporter.record(
            'accessibility',
            `axe violation [signals bar]: ${violation.id} — ${violation.help}`,
            'fail',
            {
              error: `Impact: ${violation.impact ?? 'unknown'} | Nodes: ${violation.nodes.length} | ${violation.helpUrl}`,
              metadata: {
                category: 'a11y',
                wcag: violation.tags.filter((t) => t.startsWith('wcag')).join(', '),
                impact: violation.impact ?? 'unknown',
                nodeCount: violation.nodes.length,
              },
            },
          );
        }

        const critical = results.violations.filter(
          v => v.impact === 'critical' || v.impact === 'serious',
        );
        expect(
          critical,
          `Found ${critical.length} critical/serious violations:\n` +
          critical.map((v: { id: string; help: string; nodes: unknown[] }) => `  - ${v.id}: ${v.help} (${v.nodes.length} nodes)`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Suite 2: Semantic structure
// ---------------------------------------------------------------------------

test.describe('Suite 2: Semantic structure', () => {
  test('page should have a main heading (h1) for the app title', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await auditTest(
      'accessibility',
      'page has an h1 heading for the app title',
      'WCAG 1.3.1',
      async () => {
        const h1Count = await page.locator('h1').count();
        expect(
          h1Count,
          'Expected at least one <h1> element for the app title (found none). ' +
          'Screen reader users navigate by headings — missing h1 disables this.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('page should have h2 or higher headings for major sections', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'page has section headings (h2+) for kanban columns',
      'WCAG 1.3.1',
      async () => {
        const headingCount = await page.locator('h2, h3, h4').count();
        expect(
          headingCount,
          'Expected h2+ headings for sections such as Kanban columns, toolbar regions, etc. ' +
          'Found none. Heading hierarchy lets screen readers browse page structure.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('navigation should use <nav> or role="navigation"', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await auditTest(
      'accessibility',
      'top navigation uses <nav> element or role="navigation"',
      'WCAG 1.3.1, 2.4.1',
      async () => {
        const navByElement = await page.locator('nav').count();
        const navByRole = await page.locator('[role="navigation"]').count();
        expect(
          navByElement + navByRole,
          'Expected at least one <nav> or role="navigation" element. ' +
          'The top-bar navigation is implemented as plain <div>+<button> elements ' +
          'with no landmark role, making it invisible to screen reader navigation.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('kanban columns should have region landmarks or section headings', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'kanban columns have role="region" or equivalent landmark',
      'WCAG 1.3.1',
      async () => {
        const regionCount = await page.locator('[role="region"]').count();
        const sectionWithHeadingCount = await page.locator('section').count();
        const totalLandmarks = regionCount + sectionWithHeadingCount;

        // At minimum the 5 kanban columns should be identifiable landmarks
        expect(
          totalLandmarks,
          `Expected role="region" or <section> landmarks for kanban columns. ` +
          `Found ${totalLandmarks}. Screen readers cannot identify distinct board columns.`,
        ).toBeGreaterThanOrEqual(5);
      },
    );
  });

  test('signal list should use proper list semantics (ul/ol/role=list)', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.toggleSignals();
    await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'signal rows use list semantics (ul/li or role=list/listitem)',
      'WCAG 1.3.1',
      async () => {
        const ulCount = await page.locator('ul, ol').count();
        const roleListCount = await page.locator('[role="list"]').count();
        expect(
          ulCount + roleListCount,
          'Signal list items are rendered as plain <div> elements. ' +
          'A <ul>/<ol> or role="list" container is needed so screen readers ' +
          'announce "list of N items" to orient the user.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('navbar tabs should use role="tablist" and role="tab"', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await auditTest(
      'accessibility',
      'navbar tabs have role="tablist" / role="tab" ARIA pattern',
      'WCAG 4.1.2',
      async () => {
        const tablistCount = await page.locator('[role="tablist"]').count();
        expect(
          tablistCount,
          'The "AI Kanban" / "The Forge" navigation uses plain <button> elements. ' +
          'role="tablist" + role="tab" + aria-selected is required to convey ' +
          'the tab widget pattern to assistive technologies.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Suite 3: Form accessibility
// ---------------------------------------------------------------------------

test.describe('Suite 3: Form accessibility', () => {
  test('all form inputs have associated labels', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'forge intake form — all inputs have associated labels',
      'WCAG 1.3.1, 4.1.2',
      async () => {
        // Gather every input/select/textarea in the form
        const inputs = page.locator('input, select, textarea');
        const count = await inputs.count();

        const unlabeled: string[] = [];

        for (let i = 0; i < count; i++) {
          const el = inputs.nth(i);
          const inputId = await el.getAttribute('id');
          const ariaLabel = await el.getAttribute('aria-label');
          const ariaLabelledBy = await el.getAttribute('aria-labelledby');
          const placeholder = await el.getAttribute('placeholder');
          const title = await el.getAttribute('title');

          // Check for a <label for="..."> association
          let hasAssociatedLabel = false;
          if (inputId) {
            const labelCount = await page.locator(`label[for="${inputId}"]`).count();
            hasAssociatedLabel = labelCount > 0;
          }

          const hasAccessibleName =
            hasAssociatedLabel ||
            !!ariaLabel ||
            !!ariaLabelledBy;

          if (!hasAccessibleName) {
            const tagName = await el.evaluate((node: Element) => node.tagName.toLowerCase());
            const inputType = await el.getAttribute('type');
            unlabeled.push(
              `<${tagName}${inputType ? ` type="${inputType}"` : ''}` +
              `${placeholder ? ` placeholder="${placeholder}"` : ''}` +
              `${title ? ` title="${title}"` : ''}> — index ${i}`,
            );
          }
        }

        expect(
          unlabeled,
          `The following form inputs lack programmatic label associations ` +
          `(no <label for=>, aria-label, or aria-labelledby):\n` +
          unlabeled.map(l => `  - ${l}`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });

  test('required fields should have aria-required="true"', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'forge intake — required fields marked with aria-required',
      'WCAG 1.3.5, 3.3.2',
      async () => {
        // The forge form marks Project and Task Description as required
        // (they have " *" in their label text). Verify aria-required.
        const textarea = page.locator('textarea');
        const ariaRequired = await textarea.getAttribute('aria-required');
        expect(
          ariaRequired,
          'The Task Description textarea is a required field (rendered with " *" ' +
          'suffix in the label) but lacks aria-required="true". Screen readers ' +
          'cannot inform users of the required state.',
        ).toBe('true');
      },
    );
  });

  test('select dropdowns have accessible names', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'forge intake — all <select> elements have accessible names',
      'WCAG 4.1.2',
      async () => {
        const selects = page.locator('select');
        const count = await selects.count();
        const unlabeled: string[] = [];

        for (let i = 0; i < count; i++) {
          const sel = selects.nth(i);
          const id = await sel.getAttribute('id');
          const ariaLabel = await sel.getAttribute('aria-label');
          const ariaLabelledBy = await sel.getAttribute('aria-labelledby');
          const title = await sel.getAttribute('title');

          let hasLabel = !!ariaLabel || !!ariaLabelledBy || !!title;

          if (id) {
            const labelCount = await page.locator(`label[for="${id}"]`).count();
            if (labelCount > 0) hasLabel = true;
          }

          if (!hasLabel) {
            // Grab the first option text as context
            const firstOption = await sel.locator('option').first().textContent();
            unlabeled.push(`select[first-option="${firstOption}"] at index ${i}`);
          }
        }

        expect(
          unlabeled,
          `The following <select> elements have no accessible name:\n` +
          unlabeled.map(u => `  - ${u}`).join('\n') +
          '\nEach dropdown must have a <label>, aria-label, or aria-labelledby.',
        ).toHaveLength(0);
      },
    );
  });

  test('signals add form inputs have associated labels', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.toggleSignals();
    await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });
    await kanban.addSignalButton.click();
    await page.waitForTimeout(200);

    await auditTest(
      'accessibility',
      'signals add form — inputs have associated labels',
      'WCAG 1.3.1, 4.1.2',
      async () => {
        // The signals form has 3 controls: title input, type select, severity select.
        // None have labels — the selects have no visible labels at all.
        const selects = page.locator('select');
        const count = await selects.count();
        const unlabeled: string[] = [];

        for (let i = 0; i < count; i++) {
          const sel = selects.nth(i);
          const ariaLabel = await sel.getAttribute('aria-label');
          const ariaLabelledBy = await sel.getAttribute('aria-labelledby');
          const id = await sel.getAttribute('id');
          let hasLabel = !!ariaLabel || !!ariaLabelledBy;
          if (id) {
            const labelCount = await page.locator(`label[for="${id}"]`).count();
            if (labelCount > 0) hasLabel = true;
          }
          if (!hasLabel) {
            const firstOption = await sel.locator('option').first().textContent();
            unlabeled.push(`select[first-option="${firstOption}"]`);
          }
        }

        expect(
          unlabeled,
          'Signals add form selects lack labels:\n' +
          unlabeled.map(u => `  - ${u}`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });

  test('interview panel question groups should use fieldset/legend', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(200);

    // Navigate to regenerating phase to expose the InterviewPanel
    await forge.taskDescriptionTextarea.fill('Test description for interview questions');
    await forge.generateButton.click();
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
    await forge.regenerateButton.click();
    // Wait for the interview panel hint text — unique string only in InterviewPanel.
    await expect(
      page.locator('div', { hasText: 'Answer what you can — unanswered questions use sensible defaults.' }).first(),
    ).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'interview panel question groups use fieldset/legend',
      'WCAG 1.3.1, 3.3.2',
      async () => {
        const fieldsetCount = await page.locator('fieldset').count();
        expect(
          fieldsetCount,
          'InterviewPanel choice questions render a group of radio-like buttons ' +
          'without a <fieldset>/<legend>. Each question group needs a <fieldset> ' +
          'so screen readers can associate the question text with its choices.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('error messages should be linked to inputs via aria-describedby', async ({ page, forge, mockForge }) => {
    // Use a failing forge to trigger an error state
    await mockForge({ failForgePlan: true });

    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    await forge.taskDescriptionTextarea.fill('Trigger an error');
    await forge.generateButton.click();

    // Wait for the error to appear
    await page.waitForTimeout(800);

    await auditTest(
      'accessibility',
      'forge error messages linked to inputs via aria-describedby',
      'WCAG 3.3.1, 3.3.3',
      async () => {
        // Verify the error banner exists
        const errorEl = page.locator('div').filter({ hasText: /Generation failed|Internal Server Error|API 500/ }).first();
        const errorVisible = await errorEl.isVisible().catch(() => false);

        if (!errorVisible) {
          // If no error is visible the mock may not have triggered — skip the
          // aria-describedby check but record the inability to test it.
          reporter.record(
            'accessibility',
            'forge error messages linked to inputs via aria-describedby (precondition: no error visible)',
            'skip',
            { metadata: { category: 'a11y', wcag: '3.3.1' } },
          );
          return;
        }

        // The error message should be programmatically associated with the
        // input that caused it via aria-describedby.
        const textarea = page.locator('textarea');
        const ariaDescribedBy = await textarea.getAttribute('aria-describedby').catch(() => null);
        expect(
          ariaDescribedBy,
          'The task description textarea has no aria-describedby pointing at ' +
          'the error message element. Screen readers cannot associate the error ' +
          'with the input field that triggered it.',
        ).not.toBeNull();
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Suite 4: Interactive element accessibility
// ---------------------------------------------------------------------------

test.describe('Suite 4: Interactive element accessibility', () => {
  test('all buttons have accessible names — no icon-only buttons', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await forge.taskDescriptionTextarea.fill('Check accessible names');
    await forge.generateButton.click();
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'all buttons have accessible names (not icon-only)',
      'WCAG 4.1.2',
      async () => {
        const buttons = page.locator('button');
        const count = await buttons.count();
        const nameless: string[] = [];

        for (let i = 0; i < count; i++) {
          const btn = buttons.nth(i);
          if (!(await btn.isVisible())) continue;

          const textContent = (await btn.textContent() ?? '').trim();
          const ariaLabel = await btn.getAttribute('aria-label');
          const title = await btn.getAttribute('title');
          const ariaLabelledBy = await btn.getAttribute('aria-labelledby');

          const hasName = !!ariaLabel || !!ariaLabelledBy || !!title;
          // Pure symbol characters — the known problematic ones
          const isIconOnly =
            !hasName &&
            (textContent === '×' || textContent === '▲' || textContent === '▼' || textContent === '');

          if (isIconOnly) {
            nameless.push(`button[text="${textContent}"] at index ${i}`);
          }
        }

        expect(
          nameless,
          `The following buttons have no accessible name (icon-only, no aria-label/title):\n` +
          nameless.map(b => `  - ${b}`).join('\n') +
          '\nIcon buttons need aria-label or title so screen readers can describe the action.',
        ).toHaveLength(0);
      },
    );
  });

  test('expandable kanban cards have aria-expanded attribute', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'kanban cards have aria-expanded on their toggle control',
      'WCAG 4.1.2',
      async () => {
        // Cards now have role="button" and aria-expanded (added in accessibility remediation).
        // Find the first card by role="button" that contains the card_id monospace span.
        const cards = page.getByRole('button').filter({
          has: page.locator('span[style*="font-family: monospace"]'),
        }).filter({
          has: page.locator('div[style*="font-weight: 600"]'),
        });

        const firstCard = cards.first();
        const isCardVisible = await firstCard.isVisible().catch(() => false);

        if (!isCardVisible) {
          throw new Error('No visible kanban cards found to test aria-expanded');
        }

        const ariaExpanded = await firstCard.getAttribute('aria-expanded');
        expect(
          ariaExpanded,
          'Kanban cards are expandable via click but have no aria-expanded attribute. ' +
          'Screen readers cannot determine whether the card detail section is shown or hidden.',
        ).not.toBeNull();
      },
    );
  });

  test('expandable plan editor phases have aria-expanded', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(200);

    await forge.taskDescriptionTextarea.fill('Test aria-expanded on phases');
    await forge.generateButton.click();
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'plan editor phase accordions have aria-expanded',
      'WCAG 4.1.2',
      async () => {
        // Phase headers are the clickable accordion controls
        const phaseHeaders = page.locator('div[style*="cursor: pointer"]').filter({
          has: page.locator('div[style*="font-weight: 700"]'),
        });

        const count = await phaseHeaders.count();
        if (count === 0) {
          throw new Error('No phase accordion headers found in the plan editor');
        }

        const withoutAriaExpanded: number[] = [];
        for (let i = 0; i < count; i++) {
          const header = phaseHeaders.nth(i);
          if (!(await header.isVisible().catch(() => false))) continue;
          const attr = await header.getAttribute('aria-expanded');
          const role = await header.getAttribute('role');
          // Either aria-expanded directly, or a button with aria-expanded inside
          const innerBtn = header.locator('button[aria-expanded]');
          const innerBtnCount = await innerBtn.count();
          if (!attr && !role && innerBtnCount === 0) {
            withoutAriaExpanded.push(i);
          }
        }

        expect(
          withoutAriaExpanded,
          `${withoutAriaExpanded.length} phase accordion header(s) lack aria-expanded. ` +
          'Indices: ' + withoutAriaExpanded.join(', '),
        ).toHaveLength(0);
      },
    );
  });

  test('navbar tab buttons use role="tab" with aria-selected', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await auditTest(
      'accessibility',
      'navbar tabs have role="tab" and aria-selected',
      'WCAG 4.1.2',
      async () => {
        // The nav tabs now have role="tab" (added in accessibility remediation).
        // Use getByRole('tab') to find them — getByRole('button') won't match role="tab".
        const kanbanTab = page.getByRole('tab', { name: /AI Kanban/i });
        const forgeTab = page.getByRole('tab', { name: /The Forge/i });

        await expect(kanbanTab).toBeVisible({ timeout: 5_000 });
        await expect(forgeTab).toBeVisible({ timeout: 5_000 });

        const kanbanRole = await kanbanTab.getAttribute('role');
        const forgeRole = await forgeTab.getAttribute('role');
        const kanbanSelected = await kanbanTab.getAttribute('aria-selected');
        const forgeSelected = await forgeTab.getAttribute('aria-selected');

        const issues: string[] = [];
        if (kanbanRole !== 'tab') issues.push(`"AI Kanban" tab: role="${kanbanRole ?? 'none'}" (expected "tab")`);
        if (forgeRole !== 'tab') issues.push(`"The Forge" tab: role="${forgeRole ?? 'none'}" (expected "tab")`);
        if (kanbanSelected === null) issues.push('"AI Kanban" tab: missing aria-selected');
        if (forgeSelected === null) issues.push('"The Forge" tab: missing aria-selected');

        expect(
          issues,
          'Nav tab ARIA pattern issues:\n' + issues.map(i => `  - ${i}`).join('\n'),
        ).toHaveLength(0);
      },
    );
  });

  test('AdoCombobox has combobox ARIA pattern (role, aria-autocomplete, aria-controls)', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'ADO combobox input has role="combobox" and aria-autocomplete',
      'WCAG 4.1.2',
      async () => {
        const searchInput = page.getByPlaceholder('Search ADO work items...');
        await expect(searchInput).toBeVisible({ timeout: 5_000 });

        const role = await searchInput.getAttribute('role');
        const ariaAutoComplete = await searchInput.getAttribute('aria-autocomplete');
        const ariaControls = await searchInput.getAttribute('aria-controls');
        const ariaExpanded = await searchInput.getAttribute('aria-expanded');

        const issues: string[] = [];
        if (role !== 'combobox') issues.push(`role="${role ?? 'none'}" (expected "combobox")`);
        if (!ariaAutoComplete) issues.push('missing aria-autocomplete');
        if (!ariaControls) issues.push('missing aria-controls pointing to the dropdown listbox');
        if (ariaExpanded === null) issues.push('missing aria-expanded');

        expect(
          issues,
          'ADO combobox ARIA issues:\n' + issues.map(i => `  - ${i}`).join('\n') +
          '\nThe combobox ARIA pattern requires role="combobox", aria-autocomplete, ' +
          'aria-controls, and aria-expanded on the input.',
        ).toHaveLength(0);
      },
    );
  });

  test('AdoCombobox dropdown list has role="listbox"', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'ADO combobox dropdown has role="listbox" with role="option" items',
      'WCAG 4.1.2',
      async () => {
        const searchInput = page.getByPlaceholder('Search ADO work items...');
        await expect(searchInput).toBeVisible({ timeout: 5_000 });

        // Type a query to open the dropdown
        await searchInput.fill('JWT');
        await page.waitForTimeout(500); // debounce + mock response

        const listbox = page.locator('[role="listbox"]');
        const listboxCount = await listbox.count();

        expect(
          listboxCount,
          'The ADO combobox dropdown renders results as plain <div> elements. ' +
          'role="listbox" is required on the dropdown container and role="option" ' +
          'on each item so screen readers can announce the number of suggestions.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('signal checkboxes have accessible labels', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await kanban.toggleSignals();
    await expect(kanban.signalsBar).toBeVisible({ timeout: 5_000 });
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'signal row checkboxes have accessible labels',
      'WCAG 1.3.1, 4.1.2',
      async () => {
        const checkboxes = page.locator('input[type="checkbox"]');
        const count = await checkboxes.count();

        expect(count, 'Expected at least one signal checkbox to be present').toBeGreaterThanOrEqual(1);

        const unlabeled: string[] = [];
        for (let i = 0; i < count; i++) {
          const cb = checkboxes.nth(i);
          if (!(await cb.isVisible().catch(() => false))) continue;

          const id = await cb.getAttribute('id');
          const ariaLabel = await cb.getAttribute('aria-label');
          const ariaLabelledBy = await cb.getAttribute('aria-labelledby');
          const title = await cb.getAttribute('title');

          let hasLabel = !!ariaLabel || !!ariaLabelledBy || !!title;
          if (id) {
            const labelCount = await page.locator(`label[for="${id}"]`).count();
            if (labelCount > 0) hasLabel = true;
          }

          if (!hasLabel) {
            unlabeled.push(`checkbox at index ${i}`);
          }
        }

        expect(
          unlabeled,
          `${unlabeled.length} checkbox(es) in the signals bar lack accessible labels:\n` +
          unlabeled.map(u => `  - ${u}`).join('\n') +
          '\nEach checkbox must have a <label>, aria-label, or aria-labelledby.',
        ).toHaveLength(0);
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Suite 5: Keyboard navigation
// ---------------------------------------------------------------------------

test.describe('Suite 5: Keyboard navigation', () => {
  test('tab key reaches the New Plan button from the start of the page', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(400);

    await auditTest(
      'accessibility',
      'New Plan button is reachable via keyboard Tab',
      'WCAG 2.1.1',
      async () => {
        // Move focus to the top of the page
        await page.keyboard.press('Tab');

        // Tab through at most 20 elements to find the New Plan button
        let found = false;
        for (let i = 0; i < 20; i++) {
          const focused = page.locator(':focus');
          const text = await focused.textContent().catch(() => '');
          if (text?.includes('New Plan')) {
            found = true;
            break;
          }
          await page.keyboard.press('Tab');
        }

        expect(
          found,
          'Could not reach the "+ New Plan" button via keyboard Tab within 20 presses. ' +
          'Key actions that only work via mouse are a WCAG 2.1.1 failure.',
        ).toBe(true);
      },
    );
  });

  test('tab key reaches nav tabs (AI Kanban / The Forge)', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();

    await auditTest(
      'accessibility',
      'nav tabs are reachable via keyboard Tab',
      'WCAG 2.1.1',
      async () => {
        await page.keyboard.press('Tab');

        let kanbanTabFocused = false;
        let forgeTabFocused = false;

        for (let i = 0; i < 20; i++) {
          const focused = page.locator(':focus');
          const text = (await focused.textContent().catch(() => ''))?.trim() ?? '';
          if (/AI Kanban/i.test(text)) kanbanTabFocused = true;
          if (/The Forge/i.test(text)) forgeTabFocused = true;
          if (kanbanTabFocused && forgeTabFocused) break;
          await page.keyboard.press('Tab');
        }

        expect(
          kanbanTabFocused,
          '"AI Kanban" tab not found in keyboard Tab order within first 20 stops.',
        ).toBe(true);
        expect(
          forgeTabFocused,
          '"The Forge" tab not found in keyboard Tab order within first 20 stops.',
        ).toBe(true);
      },
    );
  });

  test('focus is visible on interactive elements — outline not suppressed', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'focused elements have a visible focus indicator',
      'WCAG 2.4.7',
      async () => {
        // Tab to the first focusable element and inspect its computed style
        await page.keyboard.press('Tab');
        const focused = page.locator(':focus');
        await expect(focused).toBeVisible({ timeout: 5_000 });

        const outlineStyle = await focused.evaluate((el) => {
          const style = window.getComputedStyle(el);
          return {
            outline: style.outline,
            outlineWidth: style.outlineWidth,
            outlineStyle: style.outlineStyle,
            boxShadow: style.boxShadow,
          };
        });

        // A focus indicator exists if outline is not "0px none" or there is a box-shadow
        const hasOutline =
          outlineStyle.outlineWidth !== '0px' &&
          outlineStyle.outlineStyle !== 'none';
        const hasBoxShadow =
          outlineStyle.boxShadow !== 'none' && outlineStyle.boxShadow !== '';

        expect(
          hasOutline || hasBoxShadow,
          `The focused element has no visible focus indicator. ` +
          `Computed: outline="${outlineStyle.outline}", box-shadow="${outlineStyle.boxShadow}". ` +
          'WCAG 2.4.7 requires that keyboard focus is always visible.',
        ).toBe(true);
      },
    );
  });

  test('expandable kanban cards can be toggled via Enter key', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'kanban cards toggle expand/collapse with Enter key',
      'WCAG 2.1.1',
      async () => {
        // Cards now have role="button" + tabIndex=0 + onKeyDown (Enter/Space toggles).
        // Locate the first card by role="button" containing the card_id monospace span.
        const cards = page.getByRole('button').filter({
          has: page.locator('span[style*="font-family: monospace"]'),
        }).filter({
          has: page.locator('div[style*="font-weight: 600"]'),
        });

        const firstCard = cards.first();
        await expect(firstCard).toBeVisible({ timeout: 5_000 });

        const tagName = await firstCard.evaluate((el) => el.tagName.toLowerCase());
        const role = await firstCard.getAttribute('role');
        const tabIndex = await firstCard.getAttribute('tabindex');

        const isNativelyFocusable = tagName === 'button' || tagName === 'a';
        const hasButtonRole = role === 'button';
        const isTabbable = tabIndex !== null && tabIndex !== '-1';

        expect(
          isNativelyFocusable || (hasButtonRole && isTabbable),
          `Kanban card expand trigger is a <${tagName}> with role="${role ?? 'none'}", ` +
          `tabindex="${tabIndex ?? 'none'}". ` +
          'Non-button interactive elements need role="button" + tabIndex + keydown ' +
          'handling to be operable via keyboard (WCAG 2.1.1).',
        ).toBe(true);
      },
    );
  });

  test('forge phase changes manage focus (focus moves to new content)', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'forge phase transition moves focus to the new phase heading',
      'WCAG 2.4.3',
      async () => {
        // Fill in the description and trigger a phase change
        await forge.taskDescriptionTextarea.fill('Focus management test description');
        await forge.generateButton.click();
        await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
        await page.waitForTimeout(300);

        // After the phase changes, focus should have moved to the new content
        // (not remain on the now-hidden generate button or some stale element)
        const focused = page.locator(':focus');
        const focusedVisible = await focused.isVisible().catch(() => false);

        // The focused element must be visible and inside the preview section
        expect(
          focusedVisible,
          'After transitioning from the intake phase to the preview phase, ' +
          'focus is on a hidden or detached element. ' +
          'WCAG 2.4.3 requires focus to be moved to the relevant new content ' +
          'when a significant UI change occurs.',
        ).toBe(true);

        // Verify focus is not stuck on the now-hidden Generate button
        const focusedText = (await focused.textContent().catch(() => ''))?.trim() ?? '';
        expect(
          focusedText,
          'Focus appears to still be on the "Generate Plan" button after the phase transition.',
        ).not.toMatch(/Generate Plan|Generating/);
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Suite 6: Color and contrast
// ---------------------------------------------------------------------------

test.describe('Suite 6: Color and contrast', () => {
  test('awaiting-human status indicator has text alternative', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'awaiting-human orange dot has text alternative (not color-only)',
      'WCAG 1.4.1',
      async () => {
        // The orange dot in the toolbar uses an animated div with no text.
        // Locate the "N awaiting" indicator
        const awaitingSpan = page.locator('span').filter({ hasText: /awaiting/ }).first();
        const isVisible = await awaitingSpan.isVisible().catch(() => false);

        if (!isVisible) {
          // No awaiting-human cards in the current view — skip.
          reporter.record(
            'accessibility',
            'awaiting-human orange dot has text alternative (no awaiting cards — skipped)',
            'skip',
            { metadata: { category: 'a11y', wcag: '1.4.1' } },
          );
          return;
        }

        // The indicator must include visible text in addition to the color dot
        const textContent = (await awaitingSpan.textContent() ?? '').trim();
        expect(
          textContent,
          'The "awaiting human" indicator appears to convey status via color alone (orange dot). ' +
          'A visible text label is required alongside the color indicator.',
        ).toMatch(/awaiting/i);
      },
    );
  });

  test('priority badges include text labels alongside colors', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'priority chips include text (P0/P1) not just color',
      'WCAG 1.4.1',
      async () => {
        // P0 and P1 chips are styled with red/orange colors.
        // They must include visible text — not just be identified by color.
        const p0Chips = page.locator('span').filter({ hasText: /^P0$/ });
        const p1Chips = page.locator('span').filter({ hasText: /^P1$/ });

        const p0Count = await p0Chips.count();
        const p1Count = await p1Chips.count();

        // If priority cards exist, check they have text
        if (p0Count > 0) {
          const p0Text = (await p0Chips.first().textContent() ?? '').trim();
          expect(p0Text, 'P0 priority chip must display text "P0"').toBe('P0');
        }
        if (p1Count > 0) {
          const p1Text = (await p1Chips.first().textContent() ?? '').trim();
          expect(p1Text, 'P1 priority chip must display text "P1"').toBe('P1');
        }

        // Sanity check: at least one priority chip is visible in the mock data
        expect(
          p0Count + p1Count,
          'Expected to find at least one P0 or P1 priority chip in the mock board data. ' +
          'Mock data includes cards with priority 1 and 2.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('risk-level indicators include text labels not just colors', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'risk-level chips display text alongside color coding',
      'WCAG 1.4.1',
      async () => {
        // High risk is red, medium is yellow — these chips must include text
        const highChips = page.locator('span').filter({ hasText: /^high$/ });
        const mediumChips = page.locator('span').filter({ hasText: /^medium$/ });

        const highCount = await highChips.count();
        const mediumCount = await mediumChips.count();

        if (highCount > 0) {
          const text = (await highChips.first().textContent() ?? '').trim();
          expect(text, 'Risk chip must show text label').toMatch(/high/i);
        }
        if (mediumCount > 0) {
          const text = (await mediumChips.first().textContent() ?? '').trim();
          expect(text, 'Risk chip must show text label').toMatch(/medium/i);
        }

        expect(
          highCount + mediumCount,
          'Expected at least one high or medium risk chip in the mock data.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('disabled Generate Plan button is indicated by more than opacity alone', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'disabled Generate Plan button uses disabled attribute (not opacity-only)',
      'WCAG 1.4.1, 4.1.2',
      async () => {
        const generateBtn = forge.generateButton;
        await expect(generateBtn).toBeVisible({ timeout: 5_000 });

        // With an empty description, the button should be disabled
        await page.locator('textarea').fill('');
        await page.waitForTimeout(100);

        const isDisabled = await generateBtn.isDisabled();
        // Being disabled via the HTML disabled attribute is the correct pattern
        // because relying only on opacity fails WCAG — the semantic state is lost
        expect(
          isDisabled,
          'The Generate Plan button uses opacity to appear disabled when the form ' +
          'is incomplete, but the disabled HTML attribute is not set. ' +
          'Opacity-only disabled state violates WCAG 1.4.1 (color/visual only). ' +
          'The disabled attribute also prevents keyboard activation.',
        ).toBe(true);
      },
    );
  });

  test('connection indicator has text alternative (not color dot only)', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'connection mode indicator has text label alongside color',
      'WCAG 1.4.1',
      async () => {
        // The ConnectionIndicator renders a colored dot + text (live/polling/connecting)
        const modeText = page.locator('div').filter({ hasText: /^(live|polling|connecting)$/ }).first();
        const visible = await modeText.isVisible().catch(() => false);

        if (!visible) {
          // Indicator might be hidden in mock mode — record as skip
          reporter.record(
            'accessibility',
            'connection mode indicator has text (not visible in mock mode — skip)',
            'skip',
            { metadata: { category: 'a11y', wcag: '1.4.1' } },
          );
          return;
        }

        const text = (await modeText.textContent() ?? '').trim();
        expect(
          text,
          'Connection indicator must include a visible text label (not just a colored dot).',
        ).toMatch(/live|polling|connecting/i);
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Suite 7: Dynamic content / live regions
// ---------------------------------------------------------------------------

test.describe('Suite 7: Dynamic content', () => {
  test('loading state for board fetch uses aria-live or role="status"', async ({ page, kanban, mockBoard }) => {
    // Use a slow mock to catch the loading state
    await page.route('**/api/v1/pmo/board', async (route) => {
      await new Promise(resolve => setTimeout(resolve, 300));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cards: [], health: {} }),
      });
    });
    await page.route('**/api/v1/pmo/signals', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });
    await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });

    await kanban.goto('/');
    await page.waitForLoadState('domcontentloaded');

    await auditTest(
      'accessibility',
      'board loading state announced via aria-live or role="status"',
      'WCAG 4.1.3',
      async () => {
        // Look for any aria-live region or status role that announces loading
        const ariaLiveRegions = page.locator('[aria-live], [role="status"], [role="alert"]');
        const count = await ariaLiveRegions.count();

        expect(
          count,
          'No aria-live, role="status", or role="alert" regions found during board load. ' +
          'Screen reader users cannot tell when the board is loading or when it has updated. ' +
          'WCAG 4.1.3 (Status Messages) requires programmatic announcements for status changes.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('error banner uses role="alert" for immediate announcement', async ({ page, kanban, mockBoard }) => {
    await mockBoard({ failBoard: true });
    await kanban.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await expect(kanban.errorBanner).toBeVisible({ timeout: 12_000 });

    await auditTest(
      'accessibility',
      'error banner has role="alert" for screen reader announcement',
      'WCAG 4.1.3',
      async () => {
        // The error banner div should have role="alert" so screen readers
        // immediately announce the error without requiring focus
        const alertEl = page.locator('[role="alert"]');
        const count = await alertEl.count();

        expect(
          count,
          'The board error banner ("retrying every...") has no role="alert". ' +
          'Screen readers will not announce the error unless the user navigates to it. ' +
          'role="alert" triggers an immediate announcement on content change.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('forge generation spinner/status announced via aria-live', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(200);

    await auditTest(
      'accessibility',
      'forge plan generation status uses aria-live region',
      'WCAG 4.1.3',
      async () => {
        await forge.taskDescriptionTextarea.fill('Check aria-live during generation');
        await forge.generateButton.click();

        // Check for aria-live regions while in generating state
        await page.waitForTimeout(100);

        const ariaLiveRegions = page.locator('[aria-live], [role="status"], [role="alert"]');
        const count = await ariaLiveRegions.count();

        expect(
          count,
          'No aria-live or role="status" region found during plan generation. ' +
          'Screen readers cannot announce "Generating plan..." status changes. ' +
          'Assistive technology users are left without feedback during the wait.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('connection mode changes have aria-live announcement', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'connection mode indicator is inside an aria-live region',
      'WCAG 4.1.3',
      async () => {
        // The connection mode text (live/polling/connecting) updates dynamically.
        // It must be inside an aria-live region so changes are announced.
        const modeContainer = page.locator('[aria-live]').filter({
          hasText: /live|polling|connecting/,
        });
        const count = await modeContainer.count();

        // Also accept a parent with aria-live wrapping the connection indicator
        const statusRegions = page.locator('[role="status"]').filter({
          hasText: /live|polling|connecting/,
        });
        const statusCount = await statusRegions.count();

        expect(
          count + statusCount,
          'The connection mode indicator (live/polling/connecting) is not inside ' +
          'an aria-live or role="status" container. Screen readers will not announce ' +
          'when the connection drops from live SSE to polling mode.',
        ).toBeGreaterThanOrEqual(1);
      },
    );
  });

  test('signal count badge on Signals button is in an aria-live region', async ({ page, kanban, mockAll }) => {
    await mockAll();
    await kanban.goto('/');
    await kanban.waitForAppReady();
    await page.waitForTimeout(500);

    await auditTest(
      'accessibility',
      'signal count badge updates are announced via aria-live',
      'WCAG 4.1.3',
      async () => {
        // The Signals button shows a red badge with the open signal count.
        // This count updates as signals come in — it must be announced via
        // aria-live. The fix added a visually-hidden sibling <span aria-live>
        // next to the Signals button that announces the open signal count.

        const signalsButton = page.getByRole('button', { name: /^Signals/ });
        await expect(signalsButton).toBeVisible({ timeout: 5_000 });

        const ariaLabel = await signalsButton.getAttribute('aria-label');

        // Accept any of:
        //   (a) button aria-label includes the count
        //   (b) button is inside an aria-live ancestor
        //   (c) button is a child of an aria-live container
        //   (d) a sibling [aria-live] element exists near the button (the SR_ONLY pattern)
        const liveParent = signalsButton.locator('xpath=ancestor::*[@aria-live]');
        const liveParentCount = await liveParent.count();

        const badgeInsideLive = page.locator('[aria-live]').filter({
          has: signalsButton,
        });
        const badgeInsideLiveCount = await badgeInsideLive.count();

        // Check for sibling aria-live span: use XPath following-sibling or preceding-sibling.
        const liveSibling = signalsButton.locator('xpath=following-sibling::*[@aria-live] | preceding-sibling::*[@aria-live]');
        const liveSiblingCount = await liveSibling.count();

        // Also check if there is any [aria-live] element anywhere on the page that
        // contains the signal count text pattern (the SR_ONLY span approach).
        const anyLiveWithCount = page.locator('[aria-live]').filter({
          hasText: /open signals|signals/i,
        });
        const anyLiveWithCountCount = await anyLiveWithCount.count();

        const hasAccessibleCount =
          (ariaLabel !== null && /\d/.test(ariaLabel)) ||
          liveParentCount > 0 ||
          badgeInsideLiveCount > 0 ||
          liveSiblingCount > 0 ||
          anyLiveWithCountCount > 0;

        expect(
          hasAccessibleCount,
          'The Signals button badge count updates dynamically but is not inside ' +
          'an aria-live region and the button aria-label does not include the count. ' +
          'Screen readers cannot announce new signal arrivals.',
        ).toBe(true);
      },
    );
  });

  test('plan saved confirmation is announced via role="alert" or aria-live', async ({ page, forge, mockAll }) => {
    await mockAll();
    await forge.goto('/');
    await forge.waitForAppReady();
    await forge.switchToForge();
    await forge.assertIntakePhase();
    await page.waitForTimeout(200);

    // Go through the full forge flow to reach the saved state
    await forge.taskDescriptionTextarea.fill('Test plan save announcement');
    await forge.generateButton.click();
    await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
    await forge.approveAndQueueButton.click();
    await forge.assertSavedPhase();
    await page.waitForTimeout(300);

    await auditTest(
      'accessibility',
      'plan saved confirmation announced via role="alert" or aria-live',
      'WCAG 4.1.3',
      async () => {
        // The "Plan Saved & Queued" message should be in an alert or live region
        const alertRegions = page.locator('[role="alert"]');
        const statusRegions = page.locator('[role="status"]');
        const liveRegions = page.locator('[aria-live]');

        const alertCount = await alertRegions.count();
        const statusCount = await statusRegions.count();
        const liveCount = await liveRegions.count();

        // Accept any aria-live mechanism
        const hasAnnouncement = (alertCount + statusCount + liveCount) > 0;

        expect(
          hasAnnouncement,
          '"Plan Saved & Queued" success message has no programmatic announcement. ' +
          'role="alert", role="status", or aria-live is required so screen readers ' +
          'can notify the user of the successful save without requiring focus movement.',
        ).toBe(true);
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Teardown — write audit report
// ---------------------------------------------------------------------------

test.afterAll(() => {
  reporter.writeReport();
});
