/**
 * edit-plan-shortcut.spec.ts — Tests for the "Edit Plan" shortcut button on KanbanCard.
 *
 * The "Edit Plan" button is a 1-click shortcut that replaces the previous
 * 3-click path (expand card → View Plan → Re-forge).  It appears in the
 * expanded card action row when the card has a plan (steps_total > 0), and
 * navigates directly to the Forge editor pre-loaded with the card's data.
 *
 * Coverage:
 *   1. Button visible on expanded card that has steps
 *   2. Button absent on expanded card with no steps
 *   3. Clicking the button navigates to the Forge view
 *   4. Forge opens with the card's project context (signal title matches)
 *   5. Re-forge button still present alongside Edit Plan (no regression)
 */

import { test, expect } from '../fixtures/test-fixtures.js';
import type { PmoCard, BoardResponse, ProgramHealth } from '../../src/api/types.js';
import { MOCK_BOARD_RESPONSE, MOCK_FORGE_PLAN } from '../fixtures/mock-data.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeBoardWithCards(cards: PmoCard[]): BoardResponse {
  const health: Record<string, ProgramHealth> = {};
  for (const c of cards) {
    if (!health[c.program]) {
      health[c.program] = {
        program: c.program,
        total_plans: 0,
        active: 0,
        completed: 0,
        blocked: 0,
        failed: 0,
        completion_pct: 0,
      };
    }
    health[c.program].total_plans += 1;
  }
  return { cards, health };
}

/** A queued card that has a plan (steps_total > 0). */
const CARD_WITH_PLAN: PmoCard = {
  card_id: 'card-edit-plan-001',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Add OAuth2 integration',
  column: 'queued',
  risk_level: 'medium',
  priority: 1,
  agents: ['backend-engineer', 'security-reviewer'],
  steps_completed: 2,
  steps_total: 5,
  gates_passed: 1,
  current_phase: 'Implement token refresh',
  error: '',
  created_at: '2025-03-01T08:00:00Z',
  updated_at: '2025-03-28T10:00:00Z',
  external_id: '',
};

/** A queued card with no plan yet (steps_total === 0). */
const CARD_WITHOUT_PLAN: PmoCard = {
  card_id: 'card-edit-plan-002',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Refactor config loader',
  column: 'queued',
  risk_level: 'low',
  priority: 0,
  agents: [],
  steps_completed: 0,
  steps_total: 0,
  gates_passed: 0,
  current_phase: '',
  error: '',
  created_at: '2025-03-02T09:00:00Z',
  updated_at: '2025-03-28T11:00:00Z',
  external_id: '',
};

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Edit Plan shortcut button', () => {
  /**
   * Test 1: "Edit Plan" button is visible when expanded card has a plan.
   */
  test('button is visible on expanded card with steps_total > 0', async ({ kanban, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITH_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    // Expand the card
    const card = kanban.cardByTitle('Add OAuth2 integration');
    await kanban.expandCard(card);

    // "Edit Plan" button must be visible
    await expect(kanban.editPlanButton).toBeVisible({ timeout: 3_000 });
  });

  /**
   * Test 2: "Edit Plan" button is NOT rendered when the card has no steps.
   */
  test('button is absent on expanded card with steps_total === 0', async ({ kanban, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITHOUT_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    const card = kanban.cardByTitle('Refactor config loader');
    await kanban.expandCard(card);

    // Button must not be in the DOM (strict hidden check)
    await expect(kanban.editPlanButton).toBeHidden();
  });

  /**
   * Test 3: Clicking "Edit Plan" navigates to the Forge view.
   */
  test('clicking Edit Plan opens the Forge view', async ({ kanban, forge, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITH_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    const card = kanban.cardByTitle('Add OAuth2 integration');
    await kanban.expandCard(card);

    await kanban.editPlanButton.click();

    // Forge panel must now be visible
    await forge.assertForgeVisible();
  });

  /**
   * Test 4: Forge is pre-loaded with the card's signal context after clicking
   * "Edit Plan".  The ForgePanel renders a "from signal: <id>" badge when
   * opened via a card — the badge should reference the card's card_id.
   */
  test('Forge is pre-populated with the card context after Edit Plan click', async ({ kanban, forge, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITH_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    const card = kanban.cardByTitle('Add OAuth2 integration');
    await kanban.expandCard(card);
    await kanban.editPlanButton.click();

    // The Forge header badge includes the card_id as the signal/task id
    await expect(forge.fromSignalBadge(CARD_WITH_PLAN.card_id)).toBeVisible({ timeout: 5_000 });
  });

  /**
   * Test 5: Re-forge button is still present alongside Edit Plan — no regression.
   */
  test('Re-forge button still present when Edit Plan is shown', async ({ kanban, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITH_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    const card = kanban.cardByTitle('Add OAuth2 integration');
    await kanban.expandCard(card);

    await expect(kanban.reForgeButton).toBeVisible({ timeout: 3_000 });
    await expect(kanban.editPlanButton).toBeVisible({ timeout: 3_000 });
  });

  /**
   * Test 6: Both a card with a plan and a card without a plan render correctly
   * side-by-side — only the plan card shows the button.
   */
  test('button only appears on the card that has a plan when both are expanded', async ({ kanban, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITH_PLAN, CARD_WITHOUT_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    // Expand card with plan
    const cardWithPlan = kanban.cardByTitle('Add OAuth2 integration');
    await kanban.expandCard(cardWithPlan);

    // Expand card without plan
    const cardWithoutPlan = kanban.cardByTitle('Refactor config loader');
    await kanban.expandCard(cardWithoutPlan);

    // Exactly one "Edit Plan" button must be visible in the page
    const editButtons = kanban.page.getByRole('button', { name: /Edit Plan/ });
    await expect(editButtons).toHaveCount(1);
  });

  /**
   * Test 7: Pressing Escape in the Forge after navigating from Edit Plan
   * returns to the kanban board (existing hotkey regression).
   */
  test('Escape hotkey returns to kanban after Edit Plan navigation', async ({ kanban, forge, mockBoard, mockForge }) => {
    const board = makeBoardWithCards([CARD_WITH_PLAN]);
    await mockBoard({ boardResponse: board });
    await mockForge();

    await kanban.goto('/');
    await kanban.waitForAppReady();

    const card = kanban.cardByTitle('Add OAuth2 integration');
    await kanban.expandCard(card);
    await kanban.editPlanButton.click();
    await forge.assertForgeVisible();

    // Escape should return to kanban
    await kanban.pressHotkey('Escape');
    await kanban.assertAllColumnsVisible();
  });
});
