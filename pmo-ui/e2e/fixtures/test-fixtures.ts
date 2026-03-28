/**
 * test-fixtures.ts — extended Playwright test with page objects and API
 * route mocks auto-instantiated for every test.
 *
 * Usage in test files:
 *
 *   import { test, expect } from '../fixtures/test-fixtures.js';
 *
 *   test('board renders 5 columns', async ({ kanban, mockBoard }) => {
 *     await kanban.goto();
 *     await kanban.waitForAppReady();
 *     await kanban.assertAllColumnsVisible();
 *   });
 *
 * Fixtures provided:
 *   - kanban     — KanbanPage instance
 *   - forge      — ForgePage instance
 *   - planEditor — PlanEditorPage instance
 *   - mockBoard  — sets up API route mocks for the board endpoints
 *   - mockForge  — sets up API route mocks for forge endpoints
 *
 * Route mocking strategy:
 *   All intercepts target relative paths (e.g. /api/v1/pmo/board) which
 *   work regardless of whether the Vite dev server or the Python backend
 *   is the active base URL.  Tests that need the live backend should use
 *   the raw `page` fixture and skip mock setup.
 */

import { test as base, expect } from '@playwright/test';
import { KanbanPage } from '../pages/KanbanPage.js';
import { ForgePage } from '../pages/ForgePage.js';
import { PlanEditorPage } from '../pages/PlanEditorPage.js';
import {
  MOCK_BOARD_RESPONSE,
  MOCK_EMPTY_BOARD_RESPONSE,
  MOCK_PROJECTS,
  ALL_MOCK_SIGNALS,
  MOCK_FORGE_PLAN,
  MOCK_INTERVIEW_RESPONSE,
  MOCK_APPROVE_RESPONSE,
  MOCK_EXECUTE_RESPONSE,
  MOCK_ADO_ITEMS,
} from './mock-data.js';

// ---------------------------------------------------------------------------
// Type declarations for custom fixtures
// ---------------------------------------------------------------------------

export type MockBoardOptions = {
  /** Override the default board response. */
  boardResponse?: typeof MOCK_BOARD_RESPONSE;
  /** Whether to simulate a failed board fetch (triggers error banner). */
  failBoard?: boolean;
};

export type MockForgeOptions = {
  /** Override the default forge plan returned by POST /forge/plan. */
  forgePlan?: typeof MOCK_FORGE_PLAN;
  /** Simulate forge plan generation failure. */
  failForgePlan?: boolean;
};

export type MyFixtures = {
  /** Pre-wired KanbanPage instance. */
  kanban: KanbanPage;
  /** Pre-wired ForgePage instance. */
  forge: ForgePage;
  /** Pre-wired PlanEditorPage instance. */
  planEditor: PlanEditorPage;
  /**
   * Call this fixture to install standard API mocks for the kanban board.
   * Returns a function to reconfigure mocks mid-test if needed.
   */
  mockBoard: (options?: MockBoardOptions) => Promise<void>;
  /**
   * Call this fixture to install standard API mocks for the forge workflow.
   */
  mockForge: (options?: MockForgeOptions) => Promise<void>;
  /**
   * Install all API mocks (board + forge + signals + projects).
   * Convenience for tests that exercise the full app.
   */
  mockAll: () => Promise<void>;
};

// ---------------------------------------------------------------------------
// Fixture definitions
// ---------------------------------------------------------------------------

export const test = base.extend<MyFixtures>({
  // Page objects — auto-instantiated from the current `page`.
  kanban: async ({ page }, use) => {
    await use(new KanbanPage(page));
  },

  forge: async ({ page }, use) => {
    await use(new ForgePage(page));
  },

  planEditor: async ({ page }, use) => {
    await use(new PlanEditorPage(page));
  },

  // -------------------------------------------------------------------------
  // mockBoard — intercepts board + signals + projects API calls
  // -------------------------------------------------------------------------
  mockBoard: async ({ page }, use) => {
    const setup = async (options: MockBoardOptions = {}) => {
      const boardResp = options.boardResponse ?? MOCK_BOARD_RESPONSE;
      const failBoard = options.failBoard ?? false;

      // GET /api/v1/pmo/board
      await page.route('**/api/v1/pmo/board', async (route) => {
        if (failBoard) {
          await route.fulfill({ status: 503, body: 'Service unavailable' });
        } else {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(boardResp),
          });
        }
      });

      // GET /api/v1/pmo/board/:program (program-specific board)
      await page.route('**/api/v1/pmo/board/**', async (route) => {
        if (failBoard) {
          await route.fulfill({ status: 503, body: 'Service unavailable' });
        } else {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(boardResp),
          });
        }
      });

      // GET /api/v1/pmo/signals
      await page.route('**/api/v1/pmo/signals', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(ALL_MOCK_SIGNALS),
          });
        } else if (route.request().method() === 'POST') {
          // Create signal
          const body = JSON.parse(route.request().postData() ?? '{}');
          const newSignal = {
            signal_id: body.signal_id ?? `sig-new-${Date.now()}`,
            signal_type: body.signal_type ?? 'bug',
            title: body.title ?? 'New Signal',
            description: body.description ?? '',
            severity: body.severity ?? 'medium',
            status: 'open',
            created_at: new Date().toISOString(),
            forge_task_id: '',
            source_project_id: body.source_project_id ?? '',
          };
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(newSignal),
          });
        } else {
          await route.continue();
        }
      });

      // POST /api/v1/pmo/signals/:id/resolve
      await page.route('**/api/v1/pmo/signals/*/resolve', async (route) => {
        const url = route.request().url();
        const match = url.match(/\/signals\/([^/]+)\/resolve/);
        const signalId = match?.[1] ?? 'unknown';
        const signal = ALL_MOCK_SIGNALS.find(s => s.signal_id === signalId);
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ...(signal ?? {}), signal_id: signalId, status: 'resolved' }),
        });
      });

      // POST /api/v1/pmo/signals/batch/resolve
      await page.route('**/api/v1/pmo/signals/batch/resolve', async (route) => {
        const body = JSON.parse(route.request().postData() ?? '{"ids":[]}');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ resolved: body.ids, count: body.ids.length }),
        });
      });

      // GET /api/v1/pmo/health
      await page.route('**/api/v1/pmo/health', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(boardResp.health),
        });
      });

      // POST /api/v1/pmo/execute/:cardId
      await page.route('**/api/v1/pmo/execute/**', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_EXECUTE_RESPONSE),
        });
      });

      // GET /api/v1/pmo/cards/:cardId (card detail with plan)
      await page.route('**/api/v1/pmo/cards/**', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ...MOCK_BOARD_RESPONSE.cards[0], plan: MOCK_FORGE_PLAN }),
        });
      });

      // Block SSE to prevent test flakiness from live event streams
      await page.route('**/api/v1/pmo/events', async (route) => {
        await route.abort();
      });
    };

    await use(setup);
  },

  // -------------------------------------------------------------------------
  // mockForge — intercepts forge + project API calls
  // -------------------------------------------------------------------------
  mockForge: async ({ page }, use) => {
    const setup = async (options: MockForgeOptions = {}) => {
      const forgePlan = options.forgePlan ?? MOCK_FORGE_PLAN;
      const failForgePlan = options.failForgePlan ?? false;

      // GET /api/v1/pmo/projects
      await page.route('**/api/v1/pmo/projects', async (route) => {
        if (route.request().method() === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(MOCK_PROJECTS),
          });
        } else {
          await route.continue();
        }
      });

      // POST /api/v1/pmo/forge/plan
      await page.route('**/api/v1/pmo/forge/plan', async (route) => {
        if (failForgePlan) {
          await route.fulfill({
            status: 500,
            body: JSON.stringify({ detail: 'Internal Server Error — LLM timeout' }),
            contentType: 'application/json',
          });
        } else {
          // Simulate realistic generation delay
          await new Promise(resolve => setTimeout(resolve, 50));
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify(forgePlan),
          });
        }
      });

      // POST /api/v1/pmo/forge/approve
      await page.route('**/api/v1/pmo/forge/approve', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_APPROVE_RESPONSE),
        });
      });

      // POST /api/v1/pmo/forge/interview
      await page.route('**/api/v1/pmo/forge/interview', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_INTERVIEW_RESPONSE),
        });
      });

      // POST /api/v1/pmo/forge/regenerate
      await page.route('**/api/v1/pmo/forge/regenerate', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(forgePlan),
        });
      });

      // GET /api/v1/pmo/ado/search
      await page.route('**/api/v1/pmo/ado/search**', async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(MOCK_ADO_ITEMS),
        });
      });
    };

    await use(setup);
  },

  // -------------------------------------------------------------------------
  // mockAll — convenience: board + forge + all shared routes
  // -------------------------------------------------------------------------
  mockAll: async ({ mockBoard, mockForge }, use) => {
    const setup = async () => {
      await mockBoard();
      await mockForge();
    };
    await use(setup);
  },
});

// Re-export expect so test files only need one import.
export { expect };
