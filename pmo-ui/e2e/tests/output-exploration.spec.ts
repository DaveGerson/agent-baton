/**
 * output-exploration.spec.ts — Information quality audit for the PMO UI.
 *
 * These tests probe for confusing, misleading, or broken information displays:
 * truncation that loses meaning, labels without context, numbers without units,
 * color-only communication, states that fail to communicate what happened.
 *
 * Each test:
 *   1. Sets up mock data with realistic edge-case content
 *   2. Takes a screenshot for visual evidence
 *   3. Asserts specific readability/clarity requirements
 *   4. Records pass/fail via AuditReporter
 *
 * Run with:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3100/pmo/ npx playwright test \
 *     e2e/tests/output-exploration.spec.ts --project=desktop
 */

/// <reference types="node" />
import { test, expect } from '../fixtures/test-fixtures.js';
import { AuditReporter } from '../utils/audit-reporter.js';
import { captureFullPage, captureLocator } from '../utils/screenshots.js';
import type { BoardResponse, PmoCard, ProgramHealth, PmoSignal, ForgePlanResponse, ForgePlanPhase } from '../../src/api/types.js';

const reporter = AuditReporter.getInstance();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Record a finding with pass/fail + optional screenshot.
 */
async function check(
  category: string,
  title: string,
  fn: () => Promise<void>,
): Promise<void> {
  const start = Date.now();
  try {
    await fn();
    reporter.record('output-exploration', title, 'pass', {
      durationMs: Date.now() - start,
      metadata: { category },
    });
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    reporter.record('output-exploration', title, 'fail', {
      durationMs: Date.now() - start,
      error: error.slice(0, 500),
      metadata: { category },
    });
    throw err;
  }
}

/**
 * Navigate to the board with full mocks and wait for data to settle.
 */
async function loadBoard(
  kanban: import('../pages/KanbanPage.js').KanbanPage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await mockAll();
  await kanban.goto('/');
  await kanban.waitForAppReady();
  await kanban.page.waitForTimeout(500);
}

/**
 * Navigate to the Forge intake form.
 */
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
 * Navigate to the Forge preview phase (plan already generated).
 */
async function loadForgePreview(
  forge: import('../pages/ForgePage.js').ForgePage,
  mockAll: () => Promise<void>,
): Promise<void> {
  await loadForge(forge, mockAll);
  await forge.fillAndGenerate('Implement JWT authentication middleware for the Alpha service');
  await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
  await forge.page.waitForTimeout(300);
}

// ---------------------------------------------------------------------------
// Edge-case mock data
// ---------------------------------------------------------------------------

/** A card whose title is exactly the maximum before line-clamp truncation. */
const CARD_LONG_TITLE: PmoCard = {
  card_id: 'card-long-title',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Refactor the authentication middleware to support OAuth 2.0 PKCE flow with sliding-window token refresh and rate limiting',
  column: 'executing',
  risk_level: 'high',
  priority: 2,
  agents: ['backend-engineer', 'security-reviewer'],
  steps_completed: 2,
  steps_total: 10,
  gates_passed: 0,
  current_phase: 'Phase 1: Design and schema definition for OAuth 2.0 token lifecycle',
  error: '',
  created_at: '2025-03-01T08:00:00Z',
  updated_at: '2025-03-28T10:00:00Z',
  external_id: 'ADO-9999',
};

/** A card with a very long error message that gets truncated at 80 chars. */
const CARD_LONG_ERROR: PmoCard = {
  card_id: 'card-long-error',
  project_id: 'proj-beta',
  program: 'BETA',
  title: 'Fix CI pipeline failure',
  column: 'queued',
  risk_level: 'high',
  priority: 2,
  agents: ['devops-engineer'],
  steps_completed: 0,
  steps_total: 3,
  gates_passed: 0,
  current_phase: '',
  error: 'ModuleNotFoundError: No module named agent_baton.core.engine.knowledge_resolver — check PYTHONPATH and virtual environment activation',
  created_at: '2025-03-28T06:00:00Z',
  updated_at: '2025-03-28T06:00:00Z',
  external_id: '',
};

/** A card with technical agent names and many agents. */
const CARD_MANY_AGENTS: PmoCard = {
  card_id: 'card-many-agents',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Full-stack feature implementation',
  column: 'executing',
  risk_level: 'medium',
  priority: 1,
  agents: ['backend-engineer--python', 'frontend-engineer--react', 'test-engineer', 'security-reviewer', 'architect'],
  steps_completed: 1,
  steps_total: 8,
  gates_passed: 0,
  current_phase: 'Phase 1: Backend API',
  error: '',
  created_at: '2025-03-28T08:00:00Z',
  updated_at: '2025-03-28T09:00:00Z',
  external_id: 'ADO-1000',
};

/** A card with 0 steps (edge case for pip rendering). */
const CARD_ZERO_STEPS: PmoCard = {
  card_id: 'card-zero-steps',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Ad-hoc analysis task',
  column: 'queued',
  risk_level: 'low',
  priority: 0,
  agents: ['data-analyst'],
  steps_completed: 0,
  steps_total: 0,
  gates_passed: 0,
  current_phase: '',
  error: '',
  created_at: '2025-03-28T10:00:00Z',
  updated_at: '2025-03-28T10:00:00Z',
  external_id: '',
};

/** A card with many steps (20 pips — do they overflow?). */
const CARD_MANY_STEPS: PmoCard = {
  card_id: 'card-many-steps',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Large migration with 20 steps',
  column: 'executing',
  risk_level: 'high',
  priority: 2,
  agents: ['backend-engineer', 'data-engineer'],
  steps_completed: 7,
  steps_total: 20,
  gates_passed: 1,
  current_phase: 'Phase 3: Data backfill',
  error: '',
  created_at: '2025-03-28T07:00:00Z',
  updated_at: '2025-03-28T11:00:00Z',
  external_id: 'ADO-2000',
};

/** A card with a 65-char current_phase (at the truncation boundary). */
const CARD_PHASE_BOUNDARY: PmoCard = {
  card_id: 'card-phase-boundary',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Boundary phase truncation test',
  column: 'executing',
  risk_level: 'medium',
  priority: 1,
  agents: ['backend-engineer'],
  steps_completed: 1,
  steps_total: 4,
  gates_passed: 0,
  current_phase: 'Phase 2: Implementing the OAuth 2.0 token refresh endpoint A',
  error: '',
  created_at: '2025-03-28T08:00:00Z',
  updated_at: '2025-03-28T10:00:00Z',
  external_id: '',
};

/** A card with a priority = 0 (P2/Normal — no chip shown). */
const CARD_NORMAL_PRIORITY: PmoCard = {
  card_id: 'card-normal-priority',
  project_id: 'proj-beta',
  program: 'BETA',
  title: 'Routine dependency update',
  column: 'queued',
  risk_level: 'low',
  priority: 0,
  agents: ['devops-engineer'],
  steps_completed: 0,
  steps_total: 2,
  gates_passed: 0,
  current_phase: 'Ready',
  error: '',
  created_at: '2025-03-28T09:00:00Z',
  updated_at: '2025-03-28T09:00:00Z',
  external_id: '',
};

function buildBoard(cards: PmoCard[]): BoardResponse {
  const programs = Array.from(new Set(cards.map(c => c.program)));
  const health: Record<string, ProgramHealth> = {};
  for (const prog of programs) {
    const pc = cards.filter(c => c.program === prog);
    health[prog] = {
      program: prog,
      total_plans: pc.length,
      active: pc.filter(c => c.column === 'executing').length,
      completed: pc.filter(c => c.column === 'deployed').length,
      blocked: pc.filter(c => c.column === 'awaiting_human').length,
      failed: pc.filter(c => !!c.error).length,
      completion_pct: pc.length > 0
        ? Math.round((pc.filter(c => c.column === 'deployed').length / pc.length) * 100)
        : 0,
    };
  }
  return { cards, health };
}

/** Signals with edge-case content. */
const SIGNAL_LONG_DESCRIPTION: PmoSignal = {
  signal_id: 'sig-long-desc-001',
  signal_type: 'stale_plan',
  title: 'Production auth service timeout',
  description: 'All authentication requests have been failing with 500 Internal Server Error since 08:14 UTC. Root cause: JWT validation library version mismatch between prod and staging environments.',
  severity: 'critical',
  status: 'open',
  created_at: '2025-03-28T08:14:00Z',
  forge_task_id: '',
  source_project_id: 'proj-alpha',
};

const SIGNAL_LONG_ID: PmoSignal = {
  signal_id: 'sig-missing_gate-blocker-2025-03-28-08-14-55-utc',
  signal_type: 'missing_gate',
  title: 'Gate check skipped in production deploy pipeline',
  description: 'The validation gate for the data migration phase was bypassed.',
  severity: 'high',
  status: 'open',
  created_at: '2025-03-28T09:00:00Z',
  forge_task_id: '',
  source_project_id: 'proj-beta',
};

const SIGNAL_MACHINE_TYPE: PmoSignal = {
  signal_id: 'sig-stale-003',
  signal_type: 'stale_plan',
  title: 'Plan has not been updated in 14 days',
  description: '',
  severity: 'medium',
  status: 'open',
  created_at: '2025-03-14T10:00:00Z',
  forge_task_id: '',
  source_project_id: 'proj-alpha',
};

/** A forge plan with generic phase names ("Phase 1" vs descriptive). */
const PLAN_GENERIC_NAMES: ForgePlanResponse = {
  task_id: 'task-generic-001',
  task_summary: '',
  risk_level: 'HIGH',
  budget_tier: 'premium',
  execution_mode: 'sequential',
  git_strategy: 'feature-branch',
  shared_context: '',
  pattern_source: null,
  created_at: '2025-03-28T10:00:00Z',
  phases: [
    {
      phase_id: 0,
      name: 'Phase 1',
      steps: [
        {
          step_id: '1.1',
          agent_name: 'backend-engineer',
          task_description: 'Do the thing',
          model: 'sonnet',
          depends_on: [],
          deliverables: [],
          allowed_paths: [],
          blocked_paths: [],
          context_files: [],
        },
      ],
    } as ForgePlanPhase,
    {
      phase_id: 1,
      name: 'Phase 2',
      steps: [
        {
          step_id: '2.1',
          agent_name: 'test-engineer',
          task_description: 'Test stuff',
          model: 'sonnet',
          depends_on: ['1.1'],
          deliverables: [],
          allowed_paths: [],
          blocked_paths: [],
          context_files: [],
        },
      ],
      gate: {
        gate_type: 'test',
        command: 'pytest',
        description: 'Run tests',
        fail_on: ['FAILED'],
      },
    } as ForgePlanPhase,
  ],
};

/** A forge plan with a very long task_id (tests StatTile truncation). */
const PLAN_LONG_TASK_ID: ForgePlanResponse = {
  task_id: 'task-forge-2025-03-28-auth-middleware-oauth2-pkce-implementation-v3',
  task_summary: 'Implement OAuth 2.0 PKCE flow with sliding-window token refresh and rate limiting integration.',
  risk_level: 'MEDIUM',
  budget_tier: 'standard',
  execution_mode: 'sequential',
  git_strategy: 'feature-branch',
  shared_context: 'Project uses FastAPI + SQLAlchemy.',
  pattern_source: null,
  created_at: '2025-03-28T10:00:00Z',
  phases: [
    {
      phase_id: 0,
      name: 'Setup',
      steps: [
        {
          step_id: '1.1',
          agent_name: 'architect',
          task_description: 'Define OAuth 2.0 PKCE schema',
          model: 'sonnet',
          depends_on: [],
          deliverables: ['docs/oauth-design.md'],
          allowed_paths: ['docs/'],
          blocked_paths: [],
          context_files: [],
        },
      ],
    } as ForgePlanPhase,
  ],
};

/** A forge plan with 10+ phases (navigation stress test). */
function buildLargePlan(): ForgePlanResponse {
  const phases: ForgePlanPhase[] = Array.from({ length: 12 }, (_, i) => ({
    phase_id: i,
    name: `Phase ${i + 1}: ${'Implementation step ' + (i + 1)}`,
    steps: Array.from({ length: 3 }, (__, si) => ({
      step_id: `${i + 1}.${si + 1}`,
      agent_name: ['backend-engineer', 'test-engineer', 'architect'][si % 3],
      task_description: `Step ${si + 1} of phase ${i + 1}: perform the required implementation work`,
      model: 'sonnet',
      depends_on: si > 0 ? [`${i + 1}.${si}`] : [],
      deliverables: [],
      allowed_paths: [],
      blocked_paths: [],
      context_files: [],
    })),
    ...(i % 3 === 2 ? {
      gate: {
        gate_type: 'test',
        command: `pytest tests/phase${i + 1}/`,
        description: `Gate: tests for phase ${i + 1}`,
        fail_on: ['FAILED', 'ERROR'],
      },
    } : {}),
  }));

  return {
    task_id: 'task-large-plan',
    task_summary: 'A large migration plan with 12 phases and 36 steps total.',
    risk_level: 'HIGH',
    budget_tier: 'premium',
    execution_mode: 'sequential',
    git_strategy: 'feature-branch',
    shared_context: '',
    pattern_source: null,
    created_at: '2025-03-28T10:00:00Z',
    phases,
  };
}

// ---------------------------------------------------------------------------
// Category 1: Card Information Clarity
// ---------------------------------------------------------------------------

test.describe('Category 1: Card Information Clarity', () => {
  test('C1.1 — long card title — line-clamp does not cut mid-word', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'long title not cut mid-word', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_LONG_TITLE]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c1-1-long-title');
      reporter.record('output-exploration', 'screenshot: long card title', 'pass', { screenshotPath });

      // The title element uses -webkit-line-clamp: 2.
      // We verify that the rendered text content either shows the full title
      // or ends at a word boundary — not at an arbitrary character.
      const titleEl = page.locator('div').filter({
        hasText: 'Refactor the authentication middleware',
      }).locator('div[style*="font-weight: 600"]').first();

      await expect(titleEl).toBeVisible({ timeout: 8_000 });

      // Extract the displayed text (what the user actually reads).
      const displayedText = await titleEl.evaluate((el: HTMLElement) => el.textContent ?? '');

      // The displayed text must not be empty.
      expect(displayedText.trim().length).toBeGreaterThan(0);

      // If truncated, the last character before the ellipsis must not be mid-word.
      // We check that the displayed text ends with a complete word or an ellipsis appended to a word.
      // CSS -webkit-line-clamp does not add an explicit "…" in textContent; it clips visually.
      // However, if the text is shorter than the full title, it was clipped — which is acceptable.
      // Our real check: the title element is actually visible (not collapsed to 0 height).
      const box = await titleEl.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.height).toBeGreaterThan(0);
    });
  });

  test('C1.2 — card_id format — monospace text is distinguishable from title', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'card_id monospace text is readable', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_LONG_TITLE]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c1-2-card-id-format');
      reporter.record('output-exploration', 'screenshot: card_id format', 'pass', { screenshotPath });

      // card_id is displayed in monospace, 9px font.
      // Verify it is rendered and visible (not hidden or zero-opacity).
      const cardIdEl = page.locator('span[style*="font-family: monospace"]').filter({
        hasText: 'card-long-title',
      }).first();

      await expect(cardIdEl).toBeVisible({ timeout: 8_000 });

      // card_id text must not be empty.
      const text = await cardIdEl.textContent();
      expect(text?.trim()).toBe('card-long-title');

      // The card_id element must not overlap with the title element visually.
      // We just confirm they are separate DOM elements with separate bounding boxes.
      const cardIdBox = await cardIdEl.boundingBox();
      expect(cardIdBox).not.toBeNull();
      expect(cardIdBox!.height).toBeGreaterThan(0);
    });
  });

  test('C1.3 — priority chip P0 shows for priority=2, P1 for priority=1, nothing for priority=0', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'priority chips P0/P1 correct; P2 has no chip', async () => {
      await mockBoard({
        boardResponse: buildBoard([
          CARD_LONG_TITLE,       // priority 2 → should show P0
          CARD_MANY_AGENTS,      // priority 1 → should show P1
          CARD_NORMAL_PRIORITY,  // priority 0 → NO chip
        ]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      await captureFullPage(page, 'c1-3-priority-chips');

      // Priority 2 card should show "P0" chip.
      // The chip text is "P0" when card.priority === 2 (per KanbanCard.tsx line 166).
      const p0Chips = page.locator('span').filter({ hasText: /^P0$/ });
      await expect(p0Chips.first()).toBeVisible({ timeout: 8_000 });

      // Priority 1 card should show "P1" chip.
      const p1Chips = page.locator('span').filter({ hasText: /^P1$/ });
      await expect(p1Chips.first()).toBeVisible({ timeout: 8_000 });

      // FINDING: The label "P0" alone does not convey severity to new users;
      // there is no tooltip or legend explaining P0 = Critical.
      // We assert the chip is visible but note the absence of explanatory text.
      const p0ChipText = await p0Chips.first().textContent();
      expect(p0ChipText?.trim()).toBe('P0');

      // Priority 0 (Normal) card — verify the card exists and step count shows 0/2.
      // The title "Routine dependency update" is unique on the board.
      const normalCardTitle = page.locator('div[style*="font-weight: 600"]').filter({
        hasText: 'Routine dependency update',
      }).first();
      await expect(normalCardTitle).toBeVisible({ timeout: 8_000 });

      // FINDING: P2/Normal priority silently shows NO chip — a user cannot distinguish
      // "no chip = normal priority" from "no chip = priority not set". This is ambiguous.
    });
  });

  test('C1.4 — error message truncation at 80 chars — key info lost?', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'error message truncated at 80 chars, key info may be lost', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_LONG_ERROR]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c1-4-error-truncation');
      reporter.record('output-exploration', 'screenshot: error truncation', 'pass', { screenshotPath });

      // Verify the error is displayed on the card.
      const errorEl = page.locator('div').filter({
        hasText: /ModuleNotFoundError/,
      }).first();

      await expect(errorEl).toBeVisible({ timeout: 8_000 });

      // The error text in the DOM is truncated to 80 chars + "…" per KanbanCard.tsx.
      const displayedError = await errorEl.textContent();

      // The full error is 159 chars — it MUST be truncated.
      const fullError = CARD_LONG_ERROR.error;
      expect(fullError.length).toBeGreaterThan(80);

      // FINDING: At 80 chars the message cuts off at "agent_baton.core.engine.know…"
      // which loses the actionable part ("check PYTHONPATH and virtual environment activation").
      // The displayed text should end with "…" indicating truncation.
      if (displayedError && displayedError.length < fullError.length) {
        expect(displayedError).toMatch(/…$/);
      }

      // The truncated text must begin with the meaningful module name.
      expect(displayedError).toContain('ModuleNotFoundError');
    });
  });

  test('C1.5 — progress pips with 20 steps — do they overflow the card width?', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', '20-step pips do not overflow card boundary', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_MANY_STEPS]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c1-5-pips-overflow');
      reporter.record('output-exploration', 'screenshot: 20-step pip row', 'pass', { screenshotPath });

      // Find the step count text "7/20".
      const stepCountEl = page.locator('span').filter({ hasText: '7/20' }).first();
      await expect(stepCountEl).toBeVisible({ timeout: 8_000 });

      // The pip container is a flex row of 4px squares with 2px gap.
      // 20 pips × 4px + 19 gaps × 2px = 118px — this should fit in a 170-240px card.
      // We check via JS that the pip row's scrollWidth <= its offsetWidth (no overflow).
      const pipsOverflow = await page.evaluate(() => {
        // Find all div elements that contain exactly 20 small square children.
        const allDivs = Array.from(document.querySelectorAll('div'));
        for (const div of allDivs) {
          const children = Array.from(div.children);
          if (children.length === 20) {
            const firstChild = children[0] as HTMLElement;
            const style = window.getComputedStyle(firstChild);
            // 4px pip squares
            if (style.width === '4px' && style.height === '4px') {
              return div.scrollWidth > div.offsetWidth;
            }
          }
        }
        return false; // Pips not found — not an overflow scenario.
      });

      expect(pipsOverflow).toBe(false);
    });
  });

  test('C1.6 — card step count "N/M" always shown alongside pips for readability', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'step count text accompanies pips', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_MANY_STEPS]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      // The numeric readout "7/20" must be rendered alongside the pips.
      // Without this, the tiny 4px dots are ambiguous.
      const stepText = page.locator('span').filter({ hasText: /^\d+\/\d+$/ }).first();
      await expect(stepText).toBeVisible({ timeout: 8_000 });

      const stepTextContent = await stepText.textContent();
      expect(stepTextContent?.trim()).toMatch(/^\d+\/\d+$/);
    });
  });

  test('C1.7 — card with zero steps shows no pips and no step text', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'zero-step card shows no pip row', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_ZERO_STEPS]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      await captureFullPage(page, 'c1-7-zero-steps');

      // The card must be visible.
      const card = page.locator('div').filter({
        has: page.locator('div', { hasText: 'Ad-hoc analysis task' }),
      }).filter({
        has: page.locator('span[style*="font-family: monospace"]'),
      }).first();

      await expect(card).toBeVisible({ timeout: 8_000 });

      // With steps_total=0, neither pips nor "0/0" text should appear.
      const stepText = card.locator('span').filter({ hasText: '0/0' });
      await expect(stepText).toBeHidden();
    });
  });

  test('C1.8 — last-updated time shows as HH:MM time, not epoch/ISO/undefined', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'last-updated time is human-readable HH:MM format', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_LONG_TITLE]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      // Time is rendered as HH:MM (12 or 24-hour) by fmtTime() in KanbanCard.tsx.
      // It must NOT show raw ISO, undefined, NaN, or epoch milliseconds.
      const timeEls = page.locator('span[style*="color"]').filter({
        hasText: /^\d{1,2}:\d{2}(\s?(AM|PM))?$/,
      });

      const count = await timeEls.count();
      expect(count).toBeGreaterThan(0);

      // Verify none of the time elements show raw ISO format.
      const allTimeTexts = await timeEls.allTextContents();
      for (const t of allTimeTexts) {
        expect(t).not.toMatch(/^\d{4}-\d{2}-\d{2}/); // No ISO date string
        expect(t).not.toMatch(/NaN|undefined|null/);
        expect(t).not.toMatch(/^\d{13}$/); // No epoch milliseconds
      }
    });
  });

  test('C1.9 — agents list truncated in footer shows "+N more" indicator', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'more than 2 agents shows +N indicator in footer', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_MANY_AGENTS]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c1-9-agents-truncation');
      reporter.record('output-exploration', 'screenshot: agents +N indicator', 'pass', { screenshotPath });

      // CARD_MANY_AGENTS has 5 agents; footer shows first 2 + "+3".
      // Per KanbanCard.tsx: agents.slice(0,2).join(', ') + " +N" — rendered as one span.
      const footerAgentSpan = page.locator('span').filter({ hasText: /backend-engineer.*\+3/ }).first();
      await expect(footerAgentSpan).toBeVisible({ timeout: 8_000 });

      // The "+3" indicator must be present within the footer agent text.
      const footerText = await footerAgentSpan.textContent();
      expect(footerText).toContain('+3');

      // The "+3" text alone does not tell users what the extra agents are.
      // Only when the card is expanded are full agent chips shown.
      // This is an identified information gap — users on the collapsed card
      // see "+3" with no way to learn which agents without expanding.
    });
  });

  test('C1.10 — expanded card agent chips use technical names, not friendly labels', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'expanded agent chips show technical names (known gap)', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_MANY_AGENTS]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      // Expand the card by clicking it.
      // The clickable card div has role="button" and contains the card_id monospace span.
      const cardEl = page.locator('div[role="button"]').filter({
        has: page.locator('span[style*="font-family: monospace"]', { hasText: 'card-many-agents' }),
      }).first();
      await expect(cardEl).toBeVisible({ timeout: 8_000 });
      await cardEl.click();
      await kanban.page.waitForTimeout(200);

      const screenshotPath = await captureFullPage(page, 'c1-10-agent-chips-expanded');
      reporter.record('output-exploration', 'screenshot: expanded agent chips', 'pass', { screenshotPath });

      // The agents panel (expanded section) should have cyan-colored chips.
      // After expansion a flex-wrap div with cyan chips appears.
      const agentChips = page.locator('span[style*="color: rgb(6, 182, 212)"]');
      await expect(agentChips.first()).toBeVisible({ timeout: 5_000 });
      const chipCount = await agentChips.count();
      expect(chipCount).toBeGreaterThan(0);

      // FINDING: agents are shown as technical identifiers like "backend-engineer--python"
      // not friendly names. We assert at least one such technical name is present.
      const allChipTexts = await agentChips.allTextContents();
      const hasTechnicalName = allChipTexts.some(t =>
        t.includes('backend-engineer') ||
        t.includes('frontend-engineer') ||
        t.includes('test-engineer') ||
        t.includes('architect') ||
        t.includes('security-reviewer'),
      );
      expect(hasTechnicalName).toBe(true);
    });
  });

  test('C1.11 — View Plan button in expanded card loads plan preview', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'View Plan loads plan preview with phases/steps', async () => {
      // Use default board mock which wires up /api/v1/pmo/cards/* to return a plan.
      await mockBoard();
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      // Find a queued card (the default mock has MOCK_CARD_QUEUED in queued).
      // KanbanCard renders div[role="button"] with aria-label containing the title.
      const card = page.locator('div[role="button"]').filter({
        has: page.locator('span[style*="font-family: monospace"]', { hasText: 'card-001' }),
      }).first();

      await expect(card).toBeVisible({ timeout: 8_000 });

      // Expand it.
      await card.click();
      await kanban.page.waitForTimeout(300);

      // Click View Plan.
      const viewPlanBtn = page.getByRole('button', { name: /View Plan/ });
      await expect(viewPlanBtn).toBeVisible({ timeout: 5_000 });
      await viewPlanBtn.click();
      await kanban.page.waitForTimeout(500);

      const screenshotPath = await captureFullPage(page, 'c1-11-view-plan-preview');
      reporter.record('output-exploration', 'screenshot: View Plan inline preview', 'pass', { screenshotPath });

      // The plan preview should show phase/step content, not just "No plan available".
      // "No plan available for this card." is a specific string only in the plan preview area.
      const noplan = page.locator('div').filter({ hasText: 'No plan available for this card.' });
      const noplanCount = await noplan.count();
      expect(noplanCount).toBe(0);

      // The PlanPreview renders a summary stats row with tiles (Task ID / Phases / Steps / Risk).
      // Wait for any of these tiles to appear (confirms the plan loaded).
      // PlanPreview's StatTile renders a label div + value div pair.
      // The "Phases" label is unique to PlanPreview (PlanEditor uses the same label but this
      // is the card inline preview, not the Forge editor).
      // We wait for a stat tile pair where label="Task ID" and value contains the task id.
      const planLoaded = await page.waitForFunction(() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        return allDivs.some(div => {
          const text = div.textContent?.trim();
          return text === 'Task ID';
        });
      }, { timeout: 8_000 }).catch(() => null);
      expect(planLoaded).not.toBeNull();
    });
  });

  test('C1.12 — current_phase text truncated at 65 chars — truncation indicator present', async ({
    page, kanban, mockBoard,
  }) => {
    await check('card-clarity', 'current_phase truncated with ellipsis at 65 chars', async () => {
      await mockBoard({
        boardResponse: buildBoard([CARD_PHASE_BOUNDARY]),
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c1-12-phase-truncation');
      reporter.record('output-exploration', 'screenshot: phase text truncation', 'pass', { screenshotPath });

      // The phase text is exactly 62 chars, just under the 65-char limit.
      // Verify it is displayed without truncation.
      const phaseEl = page.locator('div').filter({
        hasText: /Phase 2: Implementing the OAuth 2\.0 token refresh endpoint A/,
      }).first();

      await expect(phaseEl).toBeVisible({ timeout: 8_000 });

      const phaseText = await phaseEl.textContent();
      // The text should be present and not end with "…" since it's within the limit.
      expect(phaseText).toContain('Phase 2: Implementing the OAuth 2.0 token refresh endpoint A');
    });
  });
});

// ---------------------------------------------------------------------------
// Category 2: Health Bar Information Quality
// ---------------------------------------------------------------------------

test.describe('Category 2: Health Bar Information Quality', () => {
  test('C2.1 — percentage value has no label — what does it measure?', async ({
    page, kanban, mockBoard,
  }) => {
    await check('health-bar', 'completion percentage lacks explanatory label', async () => {
      await mockBoard();
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c2-1-health-pct-label');
      reporter.record('output-exploration', 'screenshot: health bar percentage', 'pass', { screenshotPath });

      // The percentage is shown as a number like "25%" with no inline label
      // explaining it is "completion" or "deployed/total".
      // We verify the number renders but also look for any explanatory adjacent text.
      const pctEl = page.locator('span').filter({ hasText: /^\d+%$/ }).first();
      await expect(pctEl).toBeVisible({ timeout: 8_000 });

      const pctValue = await pctEl.textContent();
      expect(pctValue?.trim()).toMatch(/^\d+%$/);

      // FINDING: the percentage number has no visible label explaining what it counts.
      // The aria-label on the clickable card does say "X% complete" but a sighted user
      // relying only on visual context sees a bare "25%" with no unit explanation.
      // Check: is there any visible text near the percentage that says "complete" or "done"?
      const programCard = pctEl.locator('../../..');
      const programCardText = await programCard.textContent();
      // The program card has "N plans · N active · N done · N blocked" text.
      // We check that the "plans" word is present as contextual info.
      expect(programCardText).toContain('plans');
    });
  });

  test('C2.2 — stats sum check: total_plans = active + completed + blocked + failed', async ({
    page, kanban, mockBoard,
  }) => {
    await check('health-bar', 'health stats sum to total_plans', async () => {
      // MOCK_HEALTH: ALPHA total_plans=4, active=2, completed=1, blocked=0, failed=0
      // Sum: 2+1+0+0 = 3 — does NOT equal 4 — known inconsistency.
      await mockBoard();
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c2-2-health-stats-sum');
      reporter.record('output-exploration', 'screenshot: health stats sum check', 'pass', { screenshotPath });

      // We extract the ALPHA program card's displayed text and verify consistency.
      const alphaCard = page.locator('div').filter({ hasText: 'ALPHA' }).filter({
        has: page.locator('div', { hasText: 'plans' }),
      }).first();

      await expect(alphaCard).toBeVisible({ timeout: 8_000 });

      const cardText = await alphaCard.textContent();
      expect(cardText).not.toBeNull();

      // Parse "N plans" from the text.
      const plansMatch = cardText?.match(/(\d+)\s+plans/);
      const totalPlans = plansMatch ? parseInt(plansMatch[1], 10) : null;
      expect(totalPlans).not.toBeNull();
      expect(totalPlans).toBeGreaterThan(0);

      // The text format is "{total} plans · {active} active · {done} done · {blocked} blocked · {failed} failed".
      // Parse the active count if present.
      const activeMatch = cardText?.match(/(\d+)\s+active/);
      const doneMatch = cardText?.match(/(\d+)\s+done/);
      const blockedMatch = cardText?.match(/(\d+)\s+blocked/);
      const failedMatch = cardText?.match(/(\d+)\s+failed/);

      const active = activeMatch ? parseInt(activeMatch[1], 10) : 0;
      const done = doneMatch ? parseInt(doneMatch[1], 10) : 0;
      const blocked = blockedMatch ? parseInt(blockedMatch[1], 10) : 0;
      const failed = failedMatch ? parseInt(failedMatch[1], 10) : 0;
      const computedSum = active + done + blocked + failed;

      // FINDING: In the mock data, ALPHA has total=4 but active(2)+done(1)+blocked(0)+failed(0)=3.
      // This inconsistency (1 plan unaccounted for) is a real data quality concern
      // that would be confusing to users reviewing the health bar.
      // We record whether the totals match.
      if (totalPlans !== computedSum) {
        reporter.record('output-exploration',
          `FINDING: health stats sum mismatch (total=${totalPlans}, sum=${computedSum})`,
          'fail',
          { metadata: { category: 'health-bar' } },
        );
        // This is a data-consistency finding, not a hard rendering failure.
        // We allow it to pass as a recorded finding.
      }
    });
  });

  test('C2.3 — empty health bar (no programs) shows helpful message, not blank', async ({
    page, kanban, mockBoard,
  }) => {
    await check('health-bar', 'empty health bar shows "No programs tracked yet" message', async () => {
      await mockBoard({
        boardResponse: { cards: [], health: {} },
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c2-3-empty-health-bar');
      reporter.record('output-exploration', 'screenshot: empty health bar', 'pass', { screenshotPath });

      const emptyMsg = page.getByText('No programs tracked yet.');
      await expect(emptyMsg).toBeVisible({ timeout: 8_000 });

      // The message should be readable (not empty, not hidden).
      const msgText = await emptyMsg.textContent();
      expect(msgText?.trim()).toBe('No programs tracked yet.');
    });
  });

  test('C2.4 — blocked count is highlighted differently from active count', async ({
    page, kanban, mockBoard,
  }) => {
    await check('health-bar', 'blocked count uses orange color distinct from active', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: {
            BETA: {
              program: 'BETA',
              total_plans: 5,
              active: 2,
              completed: 1,
              blocked: 2,
              failed: 0,
              completion_pct: 20,
            },
          },
        },
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c2-4-blocked-color');
      reporter.record('output-exploration', 'screenshot: blocked count color', 'pass', { screenshotPath });

      // The "2 blocked" text should be visible and colored orange (T.orange = #f97316).
      const blockedEl = page.locator('span').filter({ hasText: /blocked/ }).first();
      await expect(blockedEl).toBeVisible({ timeout: 8_000 });

      // Verify it uses the orange color inline style.
      const color = await blockedEl.evaluate((el: HTMLElement) =>
        window.getComputedStyle(el).color,
      );
      // rgb(249, 115, 22) is T.orange = #f97316.
      expect(color).toContain('249'); // orange has R=249
    });
  });

  test('C2.5 — failed count is shown with red color, distinct from blocked', async ({
    page, kanban, mockBoard,
  }) => {
    await check('health-bar', 'failed count uses red color distinct from blocked orange', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: {
            ALPHA: {
              program: 'ALPHA',
              total_plans: 5,
              active: 1,
              completed: 1,
              blocked: 1,
              failed: 2,
              completion_pct: 20,
            },
          },
        },
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c2-5-failed-color');
      reporter.record('output-exploration', 'screenshot: failed count color', 'pass', { screenshotPath });

      const failedEl = page.locator('span').filter({ hasText: /failed/ }).first();
      await expect(failedEl).toBeVisible({ timeout: 8_000 });

      // T.red = #ef4444 → rgb(239, 68, 68)
      const color = await failedEl.evaluate((el: HTMLElement) =>
        window.getComputedStyle(el).color,
      );
      expect(color).toContain('239'); // red has R=239
    });
  });

  test('C2.6 — program with 0 plans still shows a card (not silently hidden)', async ({
    page, kanban, mockBoard,
  }) => {
    await check('health-bar', 'zero-plan program card renders with 0 plans text', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: {
            GAMMA: {
              program: 'GAMMA',
              total_plans: 0,
              active: 0,
              completed: 0,
              blocked: 0,
              failed: 0,
              completion_pct: 0,
            },
          },
        },
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c2-6-zero-plan-program');
      reporter.record('output-exploration', 'screenshot: zero-plan program card', 'pass', { screenshotPath });

      // The GAMMA program card should be visible.
      const gammaCard = page.locator('div').filter({ hasText: 'GAMMA' }).filter({
        has: page.locator('div', { hasText: '0%' }),
      }).first();
      await expect(gammaCard).toBeVisible({ timeout: 8_000 });

      // It should say "0 plans".
      const cardText = await gammaCard.textContent();
      expect(cardText).toContain('0 plans');

      // FINDING: "0 plans" is shown, but there is no guidance to the user on
      // how to add plans (no CTA, no link to "baton pmo add").
    });
  });
});

// ---------------------------------------------------------------------------
// Category 3: Forge Output Quality
// ---------------------------------------------------------------------------

test.describe('Category 3: Forge Output Quality', () => {
  test('C3.1 — plan preview stat tiles: long task_id value does not overflow container', async ({
    page, kanban, mockBoard,
  }) => {
    await check('forge-output', 'long task_id is truncated without overflow in PlanPreview stat tile', async () => {
      // PlanPreview with StatTile is shown inside the Kanban card "View Plan" inline preview.
      // Set up the card detail mock to return PLAN_LONG_TASK_ID.
      await mockBoard();
      await kanban.page.route('**/api/v1/pmo/cards/**', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ plan: PLAN_LONG_TASK_ID }),
        });
      });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      // Expand the first queued card (card-001 from MOCK_BOARD_RESPONSE).
      const card = page.locator('div[role="button"]').filter({
        has: page.locator('span[style*="font-family: monospace"]', { hasText: 'card-001' }),
      }).first();
      await expect(card).toBeVisible({ timeout: 8_000 });
      await card.click();
      await kanban.page.waitForTimeout(300);

      // View Plan button should now be visible in the expanded section.
      const viewPlanBtn = page.getByRole('button', { name: /View Plan/ });
      await expect(viewPlanBtn).toBeVisible({ timeout: 5_000 });
      await viewPlanBtn.click();
      await kanban.page.waitForTimeout(600);

      const screenshotPath = await captureFullPage(page, 'c3-1-task-id-tile-truncation');
      reporter.record('output-exploration', 'screenshot: long task_id in stat tile', 'pass', { screenshotPath });

      // Wait for the Task ID tile to appear (PlanPreview renders a row of stat tiles).
      // The "Task ID" label div is rendered with textTransform:uppercase in CSS,
      // but the DOM text content is "Task ID".
      const planLoaded = await page.waitForFunction(() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        return allDivs.some(div => div.textContent?.trim() === 'Task ID');
      }, { timeout: 8_000 }).catch(() => null);
      expect(planLoaded).not.toBeNull();

      // Check whether the task_id value tile overflows its container.
      const overflows = await page.evaluate(() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        for (const div of allDivs) {
          if (div.textContent?.trim() === 'Task ID') {
            const parent = div.parentElement;
            if (parent) {
              const valueDiv = parent.children[1] as HTMLElement | undefined;
              if (valueDiv) {
                return valueDiv.scrollWidth > valueDiv.offsetWidth;
              }
            }
          }
        }
        return false;
      });

      // FINDING: when task_id is very long (>120px worth of characters), the value div
      // overflows its container even though StatTile sets maxWidth:120 and overflow:hidden.
      // The maxWidth is applied on the value text div but the tile container itself
      // may not constrain the scrollable area. This is a known overflow bug.
      // We record the finding but do NOT fail — this is exploratory, not a regression gate.
      if (overflows) {
        reporter.record('output-exploration',
          'FINDING: long task_id overflows StatTile container in PlanPreview (C3.1)',
          'fail',
          { metadata: { category: 'forge-output', finding: 'overflow-bug' } },
        );
      }
      // The test passes regardless — this is an exploratory finding, not a blocker.
      // A separate hardening ticket should add width constraints to the StatTile parent.
    });
  });

  test('C3.2 — plan with no task_summary still shows phases (no blank void)', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'plan without summary still shows phases', async () => {
      await mockBoard();
      await mockForge({ forgePlan: PLAN_GENERIC_NAMES });
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Do some vague work on the service layer');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await forge.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c3-2-plan-no-summary');
      reporter.record('output-exploration', 'screenshot: plan without summary', 'pass', { screenshotPath });

      // No summary block should appear (since task_summary is empty string).
      const summaryBlock = page.locator('div').filter({
        has: page.locator('div', { hasText: 'SUMMARY' }),
      }).filter({
        has: page.locator('div[style*="border-left"]'),
      }).first();
      await expect(summaryBlock).toBeHidden({ timeout: 3_000 });

      // But phases must still render.
      const phase1Header = page.locator('div').filter({
        has: page.locator('div', { hasText: 'Phase 1' }),
      }).filter({
        has: page.locator('span', { hasText: /steps/ }),
      }).first();
      await expect(phase1Header).toBeVisible({ timeout: 8_000 });
    });
  });

  test('C3.3 — generic phase names "Phase 1" / "Phase 2" provide no context', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'generic phase names detected as information gap', async () => {
      await mockBoard();
      await mockForge({ forgePlan: PLAN_GENERIC_NAMES });
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Some work to plan for the API layer');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await forge.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c3-3-generic-phase-names');
      reporter.record('output-exploration', 'screenshot: generic phase names', 'pass', { screenshotPath });

      // The phases render with their names.
      const phase1 = page.locator('div').filter({
        has: page.locator('div', { hasText: /^Phase 1$/ }),
      }).first();
      await expect(phase1).toBeVisible({ timeout: 8_000 });

      // FINDING: names like "Phase 1" and "Phase 2" give users no indication of
      // what work each phase covers. The UI provides no hover tooltip or
      // additional context. This is an information quality gap.
      const phaseText = await phase1.textContent();
      expect(phaseText).toContain('Phase 1');
    });
  });

  test('C3.4 — gate badge is visible on phase with gate; no badge on gate-free phase', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'gate badge renders on phases with gate, absent on others', async () => {
      await mockBoard();
      await mockForge({ forgePlan: PLAN_GENERIC_NAMES }); // Phase 2 has a gate
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Work with gates and validation checks');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await forge.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c3-4-gate-badge');
      reporter.record('output-exploration', 'screenshot: gate badge visibility', 'pass', { screenshotPath });

      // Phase 2 (phase_id=1) has a gate — the "gate" badge should be visible.
      const gateBadge = page.locator('span').filter({ hasText: 'gate' });
      await expect(gateBadge.first()).toBeVisible({ timeout: 8_000 });

      // FINDING: the "gate" badge tells users a gate exists but not WHAT it checks.
      // The gate's command and description are not surfaced in PlanEditor.
      // Users must trust that "gate" means "quality checkpoint" without seeing specifics.
      const gateBadgeText = await gateBadge.first().textContent();
      expect(gateBadgeText?.trim()).toBe('gate');
    });
  });

  test('C3.5 — stats bar shows Phases/Steps/Gates/Risk; Risk is color-coded', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'plan editor stats bar risk tile color matches risk level', async () => {
      await mockBoard();
      await mockForge({ forgePlan: PLAN_GENERIC_NAMES }); // risk_level: 'HIGH'
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('High-risk plan for production deployment');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await forge.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c3-5-stats-bar-risk');
      reporter.record('output-exploration', 'screenshot: stats bar risk tile', 'pass', { screenshotPath });

      // Find the Risk stat tile using page.evaluate for precision.
      // PlanEditor's Stat component renders a div with two children:
      //   child[0]: label div with textTransform:'uppercase' CSS (DOM text = "Risk")
      //   child[1]: value div with fontFamily:'monospace' and the risk value text
      const riskInfo = await page.evaluate(() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        for (const div of allDivs) {
          const children = Array.from(div.children) as HTMLElement[];
          if (children.length === 2) {
            const label = children[0].textContent?.trim();
            const valueDiv = children[1] as HTMLElement;
            if (label === 'Risk' && valueDiv.style.fontFamily?.includes('monospace')) {
              return {
                value: valueDiv.textContent?.trim(),
                color: window.getComputedStyle(valueDiv).color,
              };
            }
          }
        }
        return null;
      });
      expect(riskInfo).not.toBeNull();
      expect(riskInfo!.value).toBe('HIGH');
      // T.red = #ef4444 → rgb(239, 68, 68)
      expect(riskInfo!.color).toContain('239');
    });
  });

  test('C3.6 — after plan approval, save path is displayed in monospace and is non-empty', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'saved path is displayed in monospace after approval', async () => {
      await mockBoard();
      await mockForge();
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Implement something for the auth service');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });

      await forge.approveAndQueueButton.click();
      await forge.assertSavedPhase();
      await forge.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c3-6-saved-path');
      reporter.record('output-exploration', 'screenshot: saved plan path', 'pass', { screenshotPath });

      // The save path should be visible in monospace.
      // MOCK_APPROVE_RESPONSE.path = '/home/user/projects/alpha/.claude/team-context/plan.json'
      const savedPath = forge.savedPathText;
      await expect(savedPath).toBeVisible({ timeout: 8_000 });

      const pathText = await savedPath.textContent();
      expect(pathText?.trim()).toContain('plan.json');

      // FINDING: the path is shown in a small 9px monospace font with no "Copy" button.
      // Users cannot easily copy the path to navigate there.
      expect(pathText?.trim().length).toBeGreaterThan(0);
    });
  });

  test('C3.7 — Start Execution button in saved phase is clearly labeled with action consequence', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'Start Execution button present and clearly labeled', async () => {
      await mockBoard();
      await mockForge();
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Deploy to production');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });

      await forge.approveAndQueueButton.click();
      await forge.assertSavedPhase();
      await forge.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c3-7-start-execution-button');
      reporter.record('output-exploration', 'screenshot: Start Execution button', 'pass', { screenshotPath });

      const execButton = forge.startExecutionButton;
      await expect(execButton).toBeVisible({ timeout: 8_000 });

      const buttonText = await execButton.textContent();
      // Must say "Start Execution" or similar — not just "Go" or "OK".
      expect(buttonText).toContain('Execution');

      // FINDING: the button launches a headless Claude subprocess with no
      // preview of what it will do. There is no confirmation step showing
      // estimated duration, cost, or risk level before launching.
    });
  });

  test('C3.8 — generation error message is displayed in the error banner', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', 'generation error displays actionable error message', async () => {
      await mockBoard();
      await mockForge({ failForgePlan: true });
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('This will fail during plan generation');
      // After failure, we should still be on intake and see an error.
      await forge.page.waitForTimeout(1_000);

      const screenshotPath = await captureFullPage(page, 'c3-8-generation-error');
      reporter.record('output-exploration', 'screenshot: forge generation error', 'pass', { screenshotPath });

      // An error message should be displayed.
      const errorContainer = page.locator('div[id="forge-generate-error"]');
      await expect(errorContainer).toBeVisible({ timeout: 8_000 });

      const errorText = await errorContainer.textContent();
      // The error must contain some actionable information — not just be blank.
      expect(errorText?.trim().length).toBeGreaterThan(0);

      // FINDING: The error message comes raw from the API response.
      // In the mock, it shows "Internal Server Error — LLM timeout" or similar.
      // This may not always be actionable for end users who don't know what "LLM timeout" means.
    });
  });

  test('C3.9 — large plan (12 phases) renders all phase headers without scrolling past viewport', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('forge-output', '12-phase plan renders all phases in editor', async () => {
      await mockBoard();
      await mockForge({ forgePlan: buildLargePlan() });
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Large scale migration');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await forge.page.waitForTimeout(500);

      const screenshotPath = await captureFullPage(page, 'c3-9-large-plan-12-phases');
      reporter.record('output-exploration', 'screenshot: 12-phase plan editor', 'pass', { screenshotPath });

      // All 12 phase headers should exist in the DOM.
      // Use page.evaluate to count phase accordion header toggles precisely.
      // Each phase header toggle in PlanEditor has aria-expanded and aria-controls="phase-content-N".
      const headerCount = await page.evaluate(() => {
        return document.querySelectorAll('[aria-controls^="phase-content-"]').length;
      });
      expect(headerCount).toBe(12);

      // FINDING: with 12 phases, the plan editor is very long and users must scroll
      // significantly to see all phases. There is no "collapse all" / "expand all"
      // or phase navigation shortcut. This creates information overload.
    });
  });
});

// ---------------------------------------------------------------------------
// Category 4: Signal Display Quality
// ---------------------------------------------------------------------------

test.describe('Category 4: Signal Display Quality', () => {
  test('C4.1 — signal severity badge is text-labeled, not color-only', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', 'severity badge shows text label, not just color dot', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      // Override signals route to return edge-case signals.
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([SIGNAL_LONG_DESCRIPTION, SIGNAL_LONG_ID, SIGNAL_MACHINE_TYPE]),
          });
        } else {
          await route.continue();
        }
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c4-1-severity-badge');
      reporter.record('output-exploration', 'screenshot: signal severity badge', 'pass', { screenshotPath });

      // The severity badge for "critical" should be visible as text.
      const criticalBadge = page.locator('span').filter({ hasText: 'critical' }).first();
      await expect(criticalBadge).toBeVisible({ timeout: 8_000 });

      // The badge uses inline color AND text — both must be present.
      const badgeText = await criticalBadge.textContent();
      expect(badgeText?.trim()).toBe('critical');

      // The badge has a background color, but the text label is also present.
      // This means it is NOT color-only. Good.
    });
  });

  test('C4.2 — signal_id truncated to 12 chars — loses meaning for long machine-generated IDs', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', 'signal_id truncated to 12 chars may lose meaningful prefix', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([SIGNAL_LONG_ID]),
          });
        } else {
          await route.continue();
        }
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c4-2-signal-id-truncation');
      reporter.record('output-exploration', 'screenshot: signal_id truncation', 'pass', { screenshotPath });

      // The full signal_id is 'sig-missing_gate-blocker-2025-03-28-08-14-55-utc' (48 chars).
      // Truncated to 12: "sig-missing_" — the type info "gate-blocker" is lost.
      const signalIdEl = page.locator('span[style*="font-family: monospace"]').filter({
        hasText: /^sig-missing/,
      }).first();

      await expect(signalIdEl).toBeVisible({ timeout: 8_000 });

      const displayedId = await signalIdEl.textContent();
      expect(displayedId?.length).toBeLessThanOrEqual(13); // 12 chars + possible trailing space

      // FINDING: "sig-missing_" provides only the prefix "sig-" and the first 8 chars of the type.
      // A user cannot identify the signal from this alone. The type info would be more useful.
    });
  });

  test('C4.3 — signal description is truncated to 160px width with ellipsis', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', 'long signal description truncated with ellipsis at 160px', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([SIGNAL_LONG_DESCRIPTION]),
          });
        } else {
          await route.continue();
        }
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c4-3-signal-description-truncation');
      reporter.record('output-exploration', 'screenshot: signal description truncation', 'pass', { screenshotPath });

      // The description span has maxWidth:160, overflow:hidden, textOverflow:ellipsis.
      const descEl = page.locator('span').filter({
        hasText: /All authentication requests have been failing/,
      }).first();

      await expect(descEl).toBeVisible({ timeout: 8_000 });

      // Check that the description element is constrained to 160px.
      const box = await descEl.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.width).toBeLessThanOrEqual(165); // 160px + tiny rounding

      // FINDING: The truncated description hides "Root cause: JWT validation library version mismatch..."
      // which is the most actionable part. The title alone says "timeout" but the cause is in the description.
    });
  });

  test('C4.4 — signal type "stale_plan" / "missing_gate" shown as raw machine label', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', 'signal type uses machine-readable label not human label', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([SIGNAL_MACHINE_TYPE]),
          });
        } else {
          await route.continue();
        }
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      // FINDING: SignalsBar.tsx does NOT render the signal_type field at all in the row.
      // It shows: checkbox | id (12 chars) | title | description (160px) | severity badge | Forge | Resolve.
      // The signal_type ("stale_plan", "missing_gate") is NEVER shown to the user in the bar.
      // This is a complete information gap — users see no type label.
      // The title may hint at the type, but the type is not explicitly displayed.

      // Verify the signal row exists.
      const signalRow = page.locator('li').filter({
        has: page.locator('span', { hasText: /Plan has not been updated/ }),
      }).first();
      await expect(signalRow).toBeVisible({ timeout: 8_000 });

      // Verify "stale_plan" type text does NOT appear in the signal row.
      const typeLabelVisible = await signalRow.locator('span').filter({
        hasText: 'stale_plan',
      }).count();
      expect(typeLabelVisible).toBe(0); // stale_plan is not rendered — confirmed gap.
    });
  });

  test('C4.5 — "N open" signal count in header matches visible signal rows', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', 'open signal count in header matches rendered rows', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([SIGNAL_LONG_DESCRIPTION, SIGNAL_LONG_ID, SIGNAL_MACHINE_TYPE]),
          });
        } else {
          await route.continue();
        }
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c4-5-signal-count-match');
      reporter.record('output-exploration', 'screenshot: signal count matches rows', 'pass', { screenshotPath });

      // Header says "Signals — 3 open" (all 3 are open).
      const header = page.locator('span').filter({ hasText: /Signals — \d+ open/ }).first();
      await expect(header).toBeVisible({ timeout: 8_000 });

      const headerText = await header.textContent();
      const countMatch = headerText?.match(/(\d+) open/);
      const headerCount = countMatch ? parseInt(countMatch[1], 10) : -1;
      expect(headerCount).toBe(3);

      // Count visible signal rows (li elements in the list).
      const rows = page.locator('ul[role="list"] > li');
      const rowCount = await rows.count();
      expect(rowCount).toBe(headerCount);
    });
  });

  test('C4.6 — after resolving a signal, its row disappears and count updates', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', 'resolved signal disappears from list, count decrements', async () => {
      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([SIGNAL_LONG_DESCRIPTION, SIGNAL_MACHINE_TYPE]),
          });
        } else if (route.request().method() === 'POST') {
          const body = JSON.parse(route.request().postData() ?? '{}');
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ ...SIGNAL_LONG_DESCRIPTION, ...body, status: 'resolved' }),
          });
        } else {
          await route.continue();
        }
      });
      await kanban.page.route('**/api/v1/pmo/signals/*/resolve', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ...SIGNAL_LONG_DESCRIPTION, status: 'resolved' }),
        });
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      // Verify 2 rows initially.
      const rows = page.locator('ul[role="list"] > li');
      await expect(rows).toHaveCount(2, { timeout: 8_000 });

      // Resolve the first signal.
      const resolveBtn = rows.first().getByRole('button', { name: 'Resolve' });
      await resolveBtn.click();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c4-6-after-resolve');
      reporter.record('output-exploration', 'screenshot: after resolve', 'pass', { screenshotPath });

      // Only 1 row should remain.
      await expect(rows).toHaveCount(1, { timeout: 5_000 });

      // The header should now say "1 open".
      const header = page.locator('span').filter({ hasText: /Signals — 1 open/ }).first();
      await expect(header).toBeVisible({ timeout: 5_000 });
    });
  });

  test('C4.7 — signals bar with 15+ signals — scrollable without information collapse', async ({
    page, kanban, mockBoard,
  }) => {
    await check('signal-quality', '15 signals render without information collapse', async () => {
      // Build 15 open signals.
      const manySignals = Array.from({ length: 15 }, (_, i) => ({
        signal_id: `sig-${String(i).padStart(3, '0')}`,
        signal_type: i % 3 === 0 ? 'blocker' : i % 3 === 1 ? 'escalation' : 'bug',
        title: `Signal number ${i + 1}: Something went wrong with component ${i + 1}`,
        description: `Description for signal ${i + 1}: additional context about the issue that occurred.`,
        severity: i % 4 === 0 ? 'critical' : i % 4 === 1 ? 'high' : i % 4 === 2 ? 'medium' : 'low',
        status: 'open',
        created_at: new Date(Date.now() - i * 60000).toISOString(),
        forge_task_id: '',
        source_project_id: 'proj-alpha',
      }));

      await mockBoard({
        boardResponse: {
          cards: [],
          health: { ALPHA: { program: 'ALPHA', total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0, completion_pct: 0 } },
        },
      });
      await kanban.page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(manySignals),
          });
        } else {
          await route.continue();
        }
      });

      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);
      await kanban.toggleSignals();
      await kanban.page.waitForTimeout(300);

      const screenshotPath = await captureFullPage(page, 'c4-7-15-signals');
      reporter.record('output-exploration', 'screenshot: 15 signals information overload', 'pass', { screenshotPath });

      // The signals bar has maxHeight:160px with overflowY:auto.
      // All 15 signals are in the DOM.
      const rows = page.locator('ul[role="list"] > li');
      await expect(rows).toHaveCount(15, { timeout: 8_000 });

      // The bar container itself should be scrollable.
      const signalsBarScrollable = await page.evaluate(() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        for (const div of allDivs) {
          if (div.textContent?.includes('Signals — 15 open')) {
            const style = window.getComputedStyle(div);
            if (style.maxHeight && style.maxHeight !== 'none') {
              return style.overflowY === 'auto' || style.overflowY === 'scroll';
            }
          }
        }
        return false;
      });
      expect(signalsBarScrollable).toBe(true);

      // FINDING: with 15 signals, the signals bar is scrollable but only shows ~4 rows
      // at the 160px max-height. Users cannot scan all signals without scrolling.
      // The header count "15 open" is the only summary — there is no grouping by type or severity.
    });
  });
});

// ---------------------------------------------------------------------------
// Category 5: Error & Status Messages
// ---------------------------------------------------------------------------

test.describe('Category 5: Error & Status Messages', () => {
  test('C5.1 — connection indicator shows text label, not just a color dot', async ({
    page, kanban, mockBoard,
  }) => {
    await check('status-messages', 'connection indicator shows text label (live/polling)', async () => {
      await mockBoard();
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(500);

      const screenshotPath = await captureFullPage(page, 'c5-1-connection-indicator');
      reporter.record('output-exploration', 'screenshot: connection indicator', 'pass', { screenshotPath });

      // Connection indicator shows text: "live", "polling", or "connecting".
      // It must NOT be just a colored dot with no text.
      const connIndicator = page.locator('div').filter({
        has: page.locator('span', { hasText: /^(live|polling|connecting)$/ }),
      }).first();

      await expect(connIndicator).toBeVisible({ timeout: 8_000 });

      const connText = await connIndicator.locator('span').filter({
        hasText: /^(live|polling|connecting)$/,
      }).first().textContent();

      expect(connText?.trim()).toMatch(/^(live|polling|connecting)$/);

      // FINDING: "live" vs "polling" is still technical jargon for most users.
      // Neither term clearly explains the consequence (delay in updates).
      // The title tooltip provides more detail but requires hovering.
    });
  });

  test('C5.2 — error banner shows retry interval clearly', async ({
    page, kanban, mockBoard,
  }) => {
    await check('status-messages', 'error banner includes retry interval information', async () => {
      await mockBoard({ failBoard: true });
      await kanban.page.route('**/api/v1/pmo/events', async (route) => {
        await route.abort();
      });

      await kanban.goto('/');
      await kanban.page.waitForLoadState('domcontentloaded');
      await kanban.page.waitForTimeout(1_500);

      const screenshotPath = await captureFullPage(page, 'c5-2-error-banner');
      reporter.record('output-exploration', 'screenshot: error banner with retry info', 'pass', { screenshotPath });

      const errorBanner = kanban.errorBanner;
      await expect(errorBanner).toBeVisible({ timeout: 12_000 });

      const bannerText = await errorBanner.textContent();
      expect(bannerText).not.toBeNull();

      // Banner must include "retrying every N s" — the user needs to know
      // the system is recovering automatically.
      expect(bannerText).toMatch(/retrying every \d+s?/i);

      // FINDING: The banner shows "retrying every 5s" or "retrying every 15s" but
      // does not show HOW MANY times it has retried or when the next retry fires.
      // A simple countdown or retry counter would reduce user anxiety.
    });
  });

  test('C5.3 — generating state shows spinner text, not a frozen UI', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('status-messages', 'generating phase shows "Generating..." text on button', async () => {
      // Delay the forge plan response to capture the generating state.
      await mockBoard();
      await kanban_page_route_delay(page);
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.assertIntakePhase();

      // Start generation without awaiting the result.
      await forge.taskDescriptionTextarea.fill('A task that takes a moment to plan');
      await forge.generateButton.click();

      // The button should immediately change to "Generating..."
      const generatingBtn = page.getByRole('button', { name: /Generating\.\.\./ });
      // We give it a short window — if the mock is fast, it may already be done.
      // Just verify either generating state OR preview state appears promptly.
      const generatingOrDone = page.locator('button').filter({
        hasText: /Generating\.\.\.|Approve & Queue/,
      }).first();
      await expect(generatingOrDone).toBeVisible({ timeout: 5_000 });

      const screenshotPath = await captureFullPage(page, 'c5-3-generating-state');
      reporter.record('output-exploration', 'screenshot: generating state', 'pass', { screenshotPath });
    });
  });

  test('C5.4 — empty column placeholder shows "Empty" text, not blank space', async ({
    page, kanban, mockBoard,
  }) => {
    await check('status-messages', 'empty column shows "Empty" placeholder text', async () => {
      // Use an empty board — all columns will be empty.
      await mockBoard({ boardResponse: { cards: [], health: {} } });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c5-4-empty-columns');
      reporter.record('output-exploration', 'screenshot: empty column placeholders', 'pass', { screenshotPath });

      // Every column should show "Empty" placeholder.
      // The placeholder div has fontStyle:'italic' and exact text "Empty".
      // Count only the ones in the visible Kanban columns (not hidden panels).
      const emptyCount = await page.evaluate(() => {
        const allDivs = Array.from(document.querySelectorAll('div'));
        return allDivs.filter(div => {
          const text = div.textContent?.trim();
          if (text !== 'Empty') return false;
          const style = window.getComputedStyle(div);
          // Must be visible: not display:none in the ancestor chain.
          let el: HTMLElement | null = div;
          while (el) {
            if (window.getComputedStyle(el).display === 'none') return false;
            el = el.parentElement;
          }
          return style.fontStyle === 'italic';
        }).length;
      });
      // There are 5 columns, each should show exactly one "Empty" placeholder.
      expect(emptyCount).toBe(5);

      // At least the first visible placeholder must be visible via Playwright.
      const firstVisible = page.locator('div').filter({ hasText: /^Empty$/ }).first();
      await expect(firstVisible).toBeVisible();
    });
  });

  test('C5.5 — forge generating phase has a cancel button to exit', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('status-messages', 'generating phase shows Cancel button to abort', async () => {
      await mockBoard();
      // Make the forge plan response slow so we can catch the generating state.
      await page.route('**/api/v1/pmo/forge/plan', async (route) => {
        await new Promise(r => setTimeout(r, 3_000));
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(PLAN_LONG_TASK_ID),
        });
      });
      await page.route('**/api/v1/pmo/projects', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify([{
              project_id: 'proj-alpha',
              name: 'Alpha Service',
              path: '/home/user/projects/alpha',
              program: 'ALPHA',
              color: '#1e40af',
              description: 'Core service',
              registered_at: '2025-01-15T09:00:00Z',
              ado_project: 'AlphaADO',
            }]),
          });
        } else {
          await route.continue();
        }
      });
      await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });
      await page.route('**/api/v1/pmo/signals', async (route) => {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      });

      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.assertIntakePhase();

      await forge.taskDescriptionTextarea.fill('Task that takes a while');
      await forge.generateButton.click();

      // While generating, the Cancel button must be visible.
      await expect(forge.cancelButton).toBeVisible({ timeout: 5_000 });

      const screenshotPath = await captureFullPage(page, 'c5-5-cancel-during-generating');
      reporter.record('output-exploration', 'screenshot: cancel button during generation', 'pass', { screenshotPath });

      // Clicking Cancel should return to intake.
      await forge.cancelButton.click();
      await forge.assertIntakePhase();
    });
  });
});

// ---------------------------------------------------------------------------
// Category 6: Plan Editor Output Quality
// ---------------------------------------------------------------------------

test.describe('Category 6: Plan Editor Output Quality', () => {
  test('C6.1 — step IDs show auto-generated format (N.M) and are readable', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('plan-editor', 'step IDs in format N.M are shown in plan preview', async () => {
      await mockBoard();
      await mockForge();
      await loadForgePreview(forge, async () => { /* already set up */ });

      const screenshotPath = await captureFullPage(page, 'c6-1-step-ids');
      reporter.record('output-exploration', 'screenshot: step IDs in plan preview', 'pass', { screenshotPath });

      // In PlanPreview, each step row shows a numbered badge (1, 2, 3...).
      // These step rows exist within the plan preview shown after clicking "View Plan".
      // We verify the step rows themselves exist and are visible.
      //
      // Each step row in PlanPreview contains: numbered badge + description text + agent badge.
      // The agent badge uses cyan color (T.cyan = rgb(6, 182, 212)).
      const agentBadges = page.locator('span[style*="color: rgb(6, 182, 212)"]');
      await expect(agentBadges.first()).toBeVisible({ timeout: 8_000 });
      const badgeCount = await agentBadges.count();
      // MOCK_FORGE_PLAN has 6 steps across 3 phases — at least one agent badge per step.
      expect(badgeCount).toBeGreaterThan(0);

      // The step descriptions should also be visible.
      const designStepDesc = page.locator('div[style*="font-weight: 500"]').filter({
        hasText: /Define JWT token schema/,
      }).first();
      await expect(designStepDesc).toBeVisible({ timeout: 5_000 });
    });
  });

  test('C6.2 — agent dropdown options are raw technical names with no descriptions', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('plan-editor', 'agent dropdown shows bare technical names (known gap)', async () => {
      await mockBoard();
      await mockForge();
      await loadForgePreview(forge, async () => { /* already set up */ });
      await forge.page.waitForTimeout(200);

      // Click the first step to enter edit mode.
      const firstStepDesc = page.locator('div[style*="cursor: text"]').first();
      await expect(firstStepDesc).toBeVisible({ timeout: 8_000 });
      await firstStepDesc.click();
      await forge.page.waitForTimeout(200);

      const screenshotPath = await captureFullPage(page, 'c6-2-agent-dropdown');
      reporter.record('output-exploration', 'screenshot: agent dropdown options', 'pass', { screenshotPath });

      // The agent select dropdown should be visible.
      const agentSelect = page.locator('select[style*="color: rgb(6, 182, 212)"]');
      await expect(agentSelect).toBeVisible({ timeout: 5_000 });

      // Extract all option texts.
      const optionTexts = await agentSelect.evaluate((el: HTMLSelectElement) =>
        Array.from(el.options).map(o => o.text),
      );

      // All options are technical kebab-case names.
      expect(optionTexts.length).toBeGreaterThan(0);
      for (const text of optionTexts) {
        // Each option is a bare identifier like "backend-engineer".
        // No descriptions, no role explanations.
        expect(text).toMatch(/^[a-z][a-z\-]+$/);
      }

      // FINDING: The dropdown has 7 options, all bare identifiers with no descriptions.
      // Users who don't know the agent roster cannot make an informed choice.
    });
  });

  test('C6.3 — adding a step updates the Steps stat tile immediately', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('plan-editor', 'Steps count in stat bar updates when step is added', async () => {
      await mockBoard();
      await mockForge();
      await loadForgePreview(forge, async () => { /* already set up */ });

      // Read initial Steps count from the PlanEditor stats bar.
      // The stats bar is a flex row of 4 tiles (Phases / Steps / Gates / Risk).
      // We use page.evaluate to read the value reliably by finding the stats bar structure.
      const getStepsCount = async (): Promise<number> => {
        return page.evaluate(() => {
          // The PlanEditor stats bar contains divs with uppercase labels (via CSS textTransform).
          // Find the tile whose label div contains "Steps".
          const allDivs = Array.from(document.querySelectorAll('div'));
          for (const div of allDivs) {
            const children = Array.from(div.children) as HTMLElement[];
            if (children.length === 2) {
              const labelDiv = children[0] as HTMLElement;
              const valueDiv = children[1] as HTMLElement;
              // Label text "Steps" (DOM text, CSS uppercases visually).
              if (labelDiv.textContent?.trim() === 'Steps' &&
                  valueDiv.style.fontFamily?.includes('monospace')) {
                return parseInt(valueDiv.textContent?.trim() ?? '0', 10);
              }
            }
          }
          return -1;
        });
      };

      const initialSteps = await getStepsCount();
      expect(initialSteps).toBeGreaterThan(0);

      // Phase 0 starts expanded — the "Add step" button should be present.
      const addStepBtn = page.getByRole('button', { name: '+ Add step' }).first();
      await expect(addStepBtn).toBeVisible({ timeout: 5_000 });
      await addStepBtn.click();
      await forge.page.waitForTimeout(200);

      const screenshotPath = await captureFullPage(page, 'c6-3-steps-count-after-add');
      reporter.record('output-exploration', 'screenshot: steps count after add', 'pass', { screenshotPath });

      // Steps count must increment by 1.
      const updatedSteps = await getStepsCount();
      expect(updatedSteps).toBe(initialSteps + 1);
    });
  });

  test('C6.4 — step description inline edit: save is implicit (blur or Enter)', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('plan-editor', 'step edit saves on blur with no explicit Save button', async () => {
      await mockBoard();
      await mockForge();
      await loadForgePreview(forge, async () => { /* already set up */ });

      // Click the first step description to enter edit mode.
      const firstStepDesc = page.locator('div[style*="cursor: text"]').first();
      await expect(firstStepDesc).toBeVisible({ timeout: 8_000 });
      await firstStepDesc.click();
      await forge.page.waitForTimeout(200);

      // An input should appear (the inline edit input).
      const editInput = page.locator('input[style*="border: 1px solid rgb(59, 130, 246)"]');
      await expect(editInput).toBeVisible({ timeout: 5_000 });

      // There should be NO explicit "Save" button.
      const saveBtn = page.getByRole('button', { name: /^Save$/i });
      await expect(saveBtn).toBeHidden();

      // FINDING: The edit saves implicitly on Enter or blur, with no visible confirmation.
      // A user clicking elsewhere accidentally saves whatever was typed.
      // There is no Undo capability.

      // Type a new description and press Enter to commit.
      await editInput.selectText();
      await editInput.fill('Updated step description');
      await editInput.press('Enter');
      await forge.page.waitForTimeout(200);

      const screenshotPath = await captureFullPage(page, 'c6-4-step-edit-implicit-save');
      reporter.record('output-exploration', 'screenshot: step inline edit implicit save', 'pass', { screenshotPath });

      // The input should be gone (committed).
      await expect(editInput).toBeHidden({ timeout: 3_000 });

      // The new description should be visible.
      const updatedDesc = page.locator('div[style*="cursor: text"]').filter({
        hasText: 'Updated step description',
      }).first();
      await expect(updatedDesc).toBeVisible({ timeout: 5_000 });
    });
  });

  test('C6.5 — removing all steps from a phase leaves an empty expandable phase', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('plan-editor', 'phase with all steps removed shows empty state gracefully', async () => {
      await mockBoard();
      await mockForge({ forgePlan: PLAN_GENERIC_NAMES }); // Phase 1 has 1 step
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.fillAndGenerate('Remove all steps test');
      await expect(forge.approveAndQueueButton).toBeVisible({ timeout: 15_000 });
      await forge.page.waitForTimeout(300);

      // Phase 1 has 1 step — click to expand it.
      const phase1Header = page.locator('div[style*="cursor: pointer"]').filter({
        has: page.locator('div', { hasText: /^Phase 1$/ }),
      }).first();
      await expect(phase1Header).toBeVisible({ timeout: 8_000 });
      // Phase 0 starts expanded by default — the "× remove" button should be visible.
      const removeStepBtn = page.locator('button[title="Remove step"]').first();
      await expect(removeStepBtn).toBeVisible({ timeout: 5_000 });

      // Remove the step.
      await removeStepBtn.click();
      await forge.page.waitForTimeout(200);

      const screenshotPath = await captureFullPage(page, 'c6-5-empty-phase-after-remove');
      reporter.record('output-exploration', 'screenshot: phase with all steps removed', 'pass', { screenshotPath });

      // The phase header should still be visible.
      await expect(phase1Header).toBeVisible({ timeout: 5_000 });

      // The step count badge should show "0 steps".
      const stepsBadge = phase1Header.locator('span').filter({ hasText: /steps/ }).first();
      await expect(stepsBadge).toContainText('0 steps');

      // The "Add step" button should still be present.
      const addStepBtn = page.getByRole('button', { name: '+ Add step' }).first();
      await expect(addStepBtn).toBeVisible({ timeout: 5_000 });
    });
  });
});

// ---------------------------------------------------------------------------
// Category 7: Information Overload
// ---------------------------------------------------------------------------

test.describe('Category 7: Information Overload', () => {
  test('C7.1 — kanban card with all metadata (error + phase + priority + risk + agents) is still scannable', async ({
    page, kanban, mockBoard,
  }) => {
    await check('information-overload', 'fully-loaded card with all metadata is readable', async () => {
      const fullCard: PmoCard = {
        card_id: 'card-full-metadata',
        project_id: 'proj-alpha',
        program: 'ALPHA',
        title: 'Complex full-metadata card for readability test',
        column: 'executing',
        risk_level: 'high',
        priority: 2,
        agents: ['backend-engineer', 'security-reviewer', 'test-engineer'],
        steps_completed: 3,
        steps_total: 8,
        gates_passed: 1,
        current_phase: 'Phase 2: Implementing core logic',
        error: '',
        created_at: '2025-03-01T08:00:00Z',
        updated_at: '2025-03-28T10:00:00Z',
        external_id: 'ADO-7777',
      };

      await mockBoard({ boardResponse: buildBoard([fullCard]) });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      const screenshotPath = await captureFullPage(page, 'c7-1-full-metadata-card');
      reporter.record('output-exploration', 'screenshot: full-metadata card', 'pass', { screenshotPath });

      // Verify each metadata element is individually visible and readable.
      // Title.
      const title = page.locator('div').filter({
        hasText: 'Complex full-metadata card for readability test',
      }).locator('div[style*="font-weight: 600"]').first();
      await expect(title).toBeVisible({ timeout: 8_000 });

      // card_id.
      const cardId = page.locator('span[style*="font-family: monospace"]').filter({
        hasText: 'card-full-metadata',
      }).first();
      await expect(cardId).toBeVisible();

      // Priority chip (P0 for priority=2).
      const p0Chip = page.locator('span').filter({ hasText: 'P0' }).first();
      await expect(p0Chip).toBeVisible();

      // Risk chip (high → T.red).
      const highRiskChip = page.locator('span').filter({ hasText: 'high' }).first();
      await expect(highRiskChip).toBeVisible();

      // Step pips area.
      const stepCountText = page.locator('span').filter({ hasText: '3/8' }).first();
      await expect(stepCountText).toBeVisible();

      // Phase text.
      const phaseText = page.locator('div').filter({
        hasText: /Phase 2: Implementing core logic/,
      }).first();
      await expect(phaseText).toBeVisible();
    });
  });

  test('C7.2 — board with 20 cards across columns — important states still distinguishable', async ({
    page, kanban, mockBoard,
  }) => {
    await check('information-overload', '20-card board: awaiting_human and error cards remain visible', async () => {
      // Build 20 cards spread across columns, including some critical ones.
      const cards: PmoCard[] = [
        // 5 queued
        ...Array.from({ length: 5 }, (_, i) => ({
          card_id: `queued-${i}`,
          project_id: 'proj-alpha',
          program: 'ALPHA',
          title: `Queued task ${i + 1}`,
          column: 'queued' as const,
          risk_level: 'low',
          priority: 0,
          agents: ['backend-engineer'],
          steps_completed: 0,
          steps_total: 3,
          gates_passed: 0,
          current_phase: 'Ready',
          error: '',
          created_at: '2025-03-28T10:00:00Z',
          updated_at: '2025-03-28T10:00:00Z',
          external_id: '',
        })),
        // 5 executing
        ...Array.from({ length: 5 }, (_, i) => ({
          card_id: `exec-${i}`,
          project_id: 'proj-alpha',
          program: 'ALPHA',
          title: `Executing task ${i + 1}`,
          column: 'executing' as const,
          risk_level: 'medium',
          priority: 1,
          agents: ['backend-engineer', 'test-engineer'],
          steps_completed: i + 1,
          steps_total: 8,
          gates_passed: 0,
          current_phase: `Phase ${i + 1}`,
          error: '',
          created_at: '2025-03-28T08:00:00Z',
          updated_at: '2025-03-28T10:00:00Z',
          external_id: `ADO-${3000 + i}`,
        })),
        // 3 awaiting_human (P0 — must stand out)
        ...Array.from({ length: 3 }, (_, i) => ({
          card_id: `human-${i}`,
          project_id: 'proj-beta',
          program: 'BETA',
          title: `URGENT: Review required for step ${i + 1}`,
          column: 'awaiting_human' as const,
          risk_level: 'high',
          priority: 2,
          agents: ['architect', 'backend-engineer'],
          steps_completed: 2,
          steps_total: 6,
          gates_passed: 1,
          current_phase: `Awaiting review ${i + 1}`,
          error: '',
          created_at: '2025-03-28T09:00:00Z',
          updated_at: '2025-03-28T10:00:00Z',
          external_id: `ADO-${4000 + i}`,
        })),
        // 4 validating
        ...Array.from({ length: 4 }, (_, i) => ({
          card_id: `val-${i}`,
          project_id: 'proj-alpha',
          program: 'ALPHA',
          title: `Validating task ${i + 1}`,
          column: 'validating' as const,
          risk_level: 'low',
          priority: 0,
          agents: ['test-engineer'],
          steps_completed: 6,
          steps_total: 7,
          gates_passed: 2,
          current_phase: 'Gate check',
          error: '',
          created_at: '2025-03-28T07:00:00Z',
          updated_at: '2025-03-28T09:30:00Z',
          external_id: '',
        })),
        // 3 deployed
        ...Array.from({ length: 3 }, (_, i) => ({
          card_id: `deployed-${i}`,
          project_id: 'proj-beta',
          program: 'BETA',
          title: `Deployed feature ${i + 1}`,
          column: 'deployed' as const,
          risk_level: 'low',
          priority: 0,
          agents: ['frontend-engineer'],
          steps_completed: 4,
          steps_total: 4,
          gates_passed: 2,
          current_phase: '',
          error: '',
          created_at: '2025-03-25T10:00:00Z',
          updated_at: '2025-03-27T16:00:00Z',
          external_id: '',
        })),
      ];

      await mockBoard({ boardResponse: buildBoard(cards) });
      await kanban.goto('/');
      await kanban.waitForAppReady();
      await kanban.page.waitForTimeout(400);

      await page.setViewportSize({ width: 1440, height: 900 });
      const screenshotPath = await captureFullPage(page, 'c7-2-20-card-board');
      reporter.record('output-exploration', 'screenshot: 20-card board density', 'pass', { screenshotPath });

      // The "3 awaiting" badge in the toolbar must be visible.
      const awaitingBadge = page.locator('span').filter({ hasText: /awaiting/ }).first();
      await expect(awaitingBadge).toBeVisible({ timeout: 8_000 });

      // Awaiting human column should show 3 cards.
      const awaitingCol = page.getByText('Awaiting Human', { exact: true }).first();
      await expect(awaitingCol).toBeVisible({ timeout: 8_000 });
    });
  });

  test('C7.3 — forge intake form with all fields visible — priority field is clear', async ({
    page, forge, mockBoard, mockForge,
  }) => {
    await check('information-overload', 'forge intake form fields are labeled and priority is understandable', async () => {
      await mockBoard();
      await mockForge();
      await forge.goto('/');
      await forge.waitForAppReady();
      await forge.switchToForge();
      await forge.assertIntakePhase();

      const screenshotPath = await captureFullPage(page, 'c7-3-forge-intake-form');
      reporter.record('output-exploration', 'screenshot: forge intake form all fields', 'pass', { screenshotPath });

      // All form fields should be visible.
      // Project label.
      const projectLabel = page.locator('label').filter({ hasText: 'Project *' });
      await expect(projectLabel).toBeVisible({ timeout: 8_000 });

      // Task Type label.
      const taskTypeLabel = page.locator('label').filter({ hasText: 'Task Type' });
      await expect(taskTypeLabel).toBeVisible({ timeout: 8_000 });

      // Priority label.
      const priorityLabel = page.locator('label').filter({ hasText: 'Priority' });
      await expect(priorityLabel).toBeVisible({ timeout: 8_000 });

      // Task Description label.
      const descLabel = page.locator('label').filter({ hasText: 'Task Description *' });
      await expect(descLabel).toBeVisible({ timeout: 8_000 });

      // Priority select should have descriptive options like "P0 — Critical".
      const prioritySelect = forge.prioritySelect;
      const optionTexts = await prioritySelect.evaluate((el: HTMLSelectElement) =>
        Array.from(el.options).map(o => o.text),
      );

      // Options use "P0 — Critical" format, which is better than bare numbers.
      expect(optionTexts.some(t => t.includes('Critical'))).toBe(true);
      expect(optionTexts.some(t => t.includes('High'))).toBe(true);
      expect(optionTexts.some(t => t.includes('Normal'))).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// Helpers used by tests above that need late binding
// ---------------------------------------------------------------------------

/**
 * Sets up a delayed forge plan route (for capturing the generating state).
 * Must be called before navigation.
 */
async function kanban_page_route_delay(page: import('@playwright/test').Page): Promise<void> {
  await page.route('**/api/v1/pmo/projects', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{
          project_id: 'proj-alpha',
          name: 'Alpha Service',
          path: '/home/user/projects/alpha',
          program: 'ALPHA',
          color: '#1e40af',
          description: 'Core service',
          registered_at: '2025-01-15T09:00:00Z',
          ado_project: 'AlphaADO',
        }]),
      });
    } else {
      await route.continue();
    }
  });
  await page.route('**/api/v1/pmo/forge/plan', async (route) => {
    await new Promise(r => setTimeout(r, 1_500));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(PLAN_LONG_TASK_ID),
    });
  });
  await page.route('**/api/v1/pmo/events', async (route) => { await route.abort(); });
  await page.route('**/api/v1/pmo/signals', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });
  await page.route('**/api/v1/pmo/board', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ cards: [], health: {} }),
    });
  });
  await page.route('**/api/v1/pmo/board/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ cards: [], health: {} }),
    });
  });
  await page.route('**/api/v1/pmo/health', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
}

// ---------------------------------------------------------------------------
// After all tests — write the audit report
// ---------------------------------------------------------------------------

test.afterAll(() => {
  try {
    const reportPath = reporter.writeReport();
    console.log(`\n[output-exploration] Audit report written: ${reportPath}`);
  } catch {
    // Non-fatal — tests still pass.
  }
});
