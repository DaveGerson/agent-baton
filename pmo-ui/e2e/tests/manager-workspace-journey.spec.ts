/**
 * manager-workspace-journey.spec.ts — Phase 7 7.3 "test-engineer": a real
 * browser journey through the "Manager" tab (ManagerWorkspaceView), Phase 7
 * "Turn PMO into the director console".
 *
 * Covers, end to end against a mocked backend (route interception -- the
 * external model/agent boundary is what's mocked; the React app, its
 * routing, and its API client all run for real in a real browser):
 *
 *   - opening a manager-mode plan and rendering every artifact category
 *     (charter, scope map / workstreams, team blueprint / role cards,
 *     knowledge plan, scope contracts, version / validation)
 *   - approving a scope-expansion decision
 *   - denying (rejecting) a pending execution decision with a rationale
 *   - resuming execution and refreshing status
 *   - task isolation: switching between two different manager-mode plans
 *     never leaks one plan's artifacts into the other
 *   - accessibility: no critical/serious WCAG A/AA violations (axe-core)
 *
 * Self-contained: all API routes used by this journey are mocked directly
 * in this file (rather than the shared fixtures/mock-data.ts, which predate
 * the manager-mode API) so this spec has no hidden coupling to unrelated
 * fixture changes.
 */

import { test, expect } from '../fixtures/test-fixtures.js';
import { AxeBuilder } from '@axe-core/playwright';
import type { Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock data -- two independent manager-mode "cards"/plans
// ---------------------------------------------------------------------------

const CARD_A = {
  card_id: 'mgr-e2e-task-a',
  project_id: 'proj-mgr-a',
  program: 'MGR',
  title: 'Add the reporting endpoint',
  column: 'awaiting_human',
  risk_level: 'MEDIUM',
  priority: 1,
  agents: ['backend-engineer'],
  steps_completed: 1,
  steps_total: 2,
  gates_passed: 0,
  current_phase: 'Implement',
  error: '',
  created_at: '2026-07-10T00:00:00Z',
  updated_at: '2026-07-16T00:00:00Z',
  external_id: '',
};

const CARD_B = {
  ...CARD_A,
  card_id: 'mgr-e2e-task-b',
  title: 'Ship the billing export',
  current_phase: 'Design',
};

function planFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    task_summary: card.title,
    risk_level: 'MEDIUM',
    budget_tier: 'standard',
    execution_mode: 'sequential',
    git_strategy: 'branch',
    shared_context: '',
    pattern_source: null,
    created_at: '2026-07-10T00:00:00Z',
    manager_mode: true,
    phases: [
      {
        phase_id: 1,
        name: 'Implement',
        steps: [
          {
            step_id: '1.1',
            agent_name: 'backend-engineer',
            task_description: card.title,
            model: 'sonnet',
            depends_on: [],
            deliverables: [],
            allowed_paths: ['app/**'],
            blocked_paths: [],
            context_files: [],
          },
        ],
      },
    ],
  };
}

const EMPTY_EXECUTION = (card: typeof CARD_A) => ({
  task_id: card.card_id,
  status: card.column,
  current_phase: card.current_phase,
  steps: [],
  started_at: '2026-07-10T00:00:00Z',
  elapsed_seconds: 60,
  turn_count: 2,
  tokens_used_usd: 0.2,
  goal: {
    completion_condition: null,
    goal_status: '',
    amend_cycles_used: 0,
    max_amend_cycles: 0,
    checks_count: 0,
    last_check_met: null,
  },
});

function charterFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    markdown: `# Charter\n\nObjective: ${card.title}.`,
  };
}

function scopeMapFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    scope_map: {
      task_id: card.card_id,
      workstreams: [
        {
          id: 'ws-1',
          name: `${card.title} workstream`,
          objective: card.title,
          likely_paths: [],
          allowed_paths: ['app/**'],
          owner_role: 'backend-engineer',
          dependencies: [],
          deliverables: [],
          risks: [],
        },
      ],
      cross_cutting_concerns: [],
      out_of_scope: [],
      scope_expansion_policy: 'queue_for_manager',
    },
  };
}

function workstreamsFor(card: typeof CARD_A) {
  const scope = scopeMapFor(card).scope_map;
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    links: [{ phase_id: 1, phase_name: 'Implement', workstream: scope.workstreams[0] }],
  };
}

function teamBlueprintFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    team_blueprint: {
      task_id: card.card_id,
      team_name: `${card.title} team`,
      mission: card.title,
      roles: [
        {
          role: 'backend-engineer',
          agent_name: 'backend-engineer',
          mission: card.title,
          owns: ['app/**'],
          does_not_own: [],
          required_knowledge_packs: [],
          default_context_budget: 12000,
          expected_handoffs: [],
          escalation_triggers: [],
        },
      ],
      workstream_assignments: { 'ws-1': 'backend-engineer' },
      collaboration_rules: [],
      escalation_triggers: [],
      phase_policies: {},
    },
  };
}

function roleCardsFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    role_cards: [{ role: 'backend-engineer', markdown: `# backend-engineer\n\n${card.title}.` }],
  };
}

function knowledgePlanFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    knowledge_plan: {
      task_id: card.card_id,
      selected_packs: [],
      missing_packs: [],
      stale_packs: [],
      per_role_packs: {},
      per_step_packs: {},
    },
  };
}

function scopeContractsFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-10T00:05:00Z',
    contracts: [{ step_id: '1.1', agent_name: 'backend-engineer', workstream_id: 'ws-1', allowed_paths: ['app/**'] }],
  };
}

function contextBundlesFor(card: typeof CARD_A) {
  return { task_id: card.card_id, revision: 1, published_at: '2026-07-10T00:05:00Z', bundles: [] };
}

function versionFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    published: true,
    revision: 1,
    prior_revision: 0,
    trigger: 'forge_approve',
    created_at: '2026-07-10T00:05:00Z',
    plan_fingerprint: `fp-${card.card_id}`,
    phase_count: 1,
    step_count: 1,
    published_paths: [],
  };
}

function validationFor(card: typeof CARD_A) {
  return {
    task_id: card.card_id,
    published: true,
    valid: true,
    fingerprint_match: true,
    revision: 1,
    current_plan_fingerprint: `fp-${card.card_id}`,
    published_plan_fingerprint: `fp-${card.card_id}`,
    errors: [],
  };
}

const PENDING_EXEC_DECISION = {
  request_id: 'req-e2e-1',
  task_id: CARD_A.card_id,
  decision_type: 'approval',
  summary: 'Approve phase 1 completion?',
  options: ['approve', 'reject'],
  deadline: null,
  context_files: [],
  created_at: '2026-07-15T00:00:00Z',
  status: 'pending',
};

const PENDING_SCOPE_DECISION = {
  decision_id: 'dec-e2e-1',
  decision_type: 'scope_expansion',
  task_id: CARD_A.card_id,
  summary: 'Step 1.1 touched files outside its contract',
  context: 'Diff touched app/extra.ts',
  options: ['approve', 'reject'],
  recommended_option: 'approve',
  created_at: '2026-07-15T00:00:00Z',
  resolved_at: null,
  resolution: null,
  markdown: '',
};

// ---------------------------------------------------------------------------
// Route mocking
// ---------------------------------------------------------------------------

async function mockManagerWorkspaceRoutes(page: Page): Promise<void> {
  await page.route('**/api/v1/pmo/board', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ cards: [CARD_A, CARD_B], health: {} }),
    });
  });

  await page.route('**/api/v1/pmo/cards/**', async (route) => {
    const url = route.request().url();
    const id = url.includes(CARD_B.card_id) ? CARD_B.card_id : CARD_A.card_id;
    const card = id === CARD_B.card_id ? CARD_B : CARD_A;
    if (url.endsWith('/execution')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(EMPTY_EXECUTION(card)) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...card, plan: planFor(card) }),
    });
  });

  await page.route('**/api/v1/pmo/gates/pending', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/v1/pmo/execute/*/decisions', async (route) => {
    const url = route.request().url();
    const decisions = url.includes(CARD_A.card_id) ? [PENDING_EXEC_DECISION] : [];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ count: decisions.length, decisions }),
    });
  });

  await page.route('**/api/v1/pmo/execute/*/decisions/*/resolve', async (route) => {
    const body = JSON.parse(route.request().postData() ?? '{}');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ resolved: true, execution_resumed: body.option === 'approve' }),
    });
  });

  await page.route('**/api/v1/pmo/execute/*/pause', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'paused', task_id: CARD_A.card_id }),
    });
  });

  await page.route('**/api/v1/pmo/execute/*/resume', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'running', task_id: CARD_A.card_id }),
    });
  });

  const manager = (fn: (card: typeof CARD_A) => unknown) => async (route: Parameters<Parameters<Page['route']>[1]>[0]) => {
    const url = route.request().url();
    const card = url.includes(CARD_B.card_id) ? CARD_B : CARD_A;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fn(card)) });
  };

  await page.route('**/api/v1/pmo/manager/*/charter', manager(charterFor));
  await page.route('**/api/v1/pmo/manager/*/scope-map', manager(scopeMapFor));
  await page.route('**/api/v1/pmo/manager/*/workstreams', manager(workstreamsFor));
  await page.route('**/api/v1/pmo/manager/*/team-blueprint', manager(teamBlueprintFor));
  await page.route('**/api/v1/pmo/manager/*/role-cards', manager(roleCardsFor));
  await page.route('**/api/v1/pmo/manager/*/knowledge-plan', manager(knowledgePlanFor));
  await page.route('**/api/v1/pmo/manager/*/scope-contracts', manager(scopeContractsFor));
  await page.route('**/api/v1/pmo/manager/*/context-bundles', manager(contextBundlesFor));
  await page.route('**/api/v1/pmo/manager/*/version', manager(versionFor));
  await page.route('**/api/v1/pmo/manager/*/validation', manager(validationFor));

  await page.route('**/api/v1/pmo/manager/*/decisions', async (route) => {
    const url = route.request().url();
    const decisions = url.includes(CARD_A.card_id) ? [PENDING_SCOPE_DECISION] : [];
    const card = url.includes(CARD_B.card_id) ? CARD_B : CARD_A;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ task_id: card.card_id, count: decisions.length, decisions }),
    });
  });

  await page.route('**/api/v1/pmo/manager/*/decisions/*/resolve', async (route) => {
    const body = JSON.parse(route.request().postData() ?? '{}');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        applied: true,
        resolution: body.resolution,
        step_id: '1.1',
        decision_id: PENDING_SCOPE_DECISION.decision_id,
        new_allowed_paths: body.resolution === 'approve' ? ['app/**', 'app/extra.ts'] : [],
        error: null,
      }),
    });
  });

  await page.route('**/api/v1/pmo/events', async (route) => {
    await route.abort();
  });
}

async function openManagerTab(page: Page): Promise<void> {
  await page.goto('/');
  await page.waitForSelector('text=Baton PMO', { state: 'visible', timeout: 15_000 });
  await page.getByRole('tab', { name: /Manager/i }).click();
  await expect(page.getByTestId('manager-workspace')).toBeVisible();
}

async function selectCard(page: Page, cardId: string): Promise<void> {
  await page.getByLabel('Choose a plan').selectOption(cardId);
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('Manager Workspace — full director-console journey', () => {
  test('renders every manager artifact category for the selected plan', async ({ page }) => {
    await mockManagerWorkspaceRoutes(page);
    await openManagerTab(page);
    await selectCard(page, CARD_A.card_id);

    await expect(page.getByRole('heading', { name: CARD_A.title })).toBeVisible();
    await expect(page.getByText(/Objective: Add the reporting endpoint/)).toBeVisible();
    await expect(page.getByText(/owned by/)).toBeVisible();
    await expect(page.getByText(`${CARD_A.title} team`)).toBeVisible();
    await expect(page.getByTestId('validation-status')).toContainText(/version-consistent/i);
  });

  test('approves a pending scope-expansion decision', async ({ page }) => {
    await mockManagerWorkspaceRoutes(page);
    await openManagerTab(page);
    await selectCard(page, CARD_A.card_id);

    const decisionCard = page.getByTestId('scope-expansion-decision');
    await expect(decisionCard).toBeVisible();
    const [resolveResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/decisions/dec-e2e-1/resolve') && r.request().method() === 'POST'),
      decisionCard.getByRole('button', { name: /approve expansion/i }).click(),
    ]);
    expect(resolveResponse.status()).toBe(200);
    await expect(page.getByText(/scope expansion approved/i)).toBeVisible();
  });

  test('denies a pending execution decision with a rationale, then refreshes status', async ({ page }) => {
    await mockManagerWorkspaceRoutes(page);
    await openManagerTab(page);
    await selectCard(page, CARD_A.card_id);

    const form = page.getByTestId('execution-decision-form');
    await expect(form).toBeVisible();
    await form.getByLabel('reject').check();
    await form.getByLabel('Rationale (optional)').fill('Not ready — needs a security pass first.');

    const [resolveResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/decisions/req-e2e-1/resolve') && r.request().method() === 'POST'),
      form.getByRole('button', { name: /resolve decision/i }).click(),
    ]);
    const requestBody = JSON.parse(resolveResponse.request().postData() ?? '{}');
    expect(requestBody).toMatchObject({ option: 'reject', rationale: 'Not ready — needs a security pass first.' });

    // "Refresh" re-issues the read calls -- status stays observable, not stuck.
    await page.getByTestId('refresh-button').click();
    await expect(page.getByTestId('execution-status-badge')).toBeVisible();
  });

  test('resumes execution from the workspace', async ({ page }) => {
    await mockManagerWorkspaceRoutes(page);
    await openManagerTab(page);
    await selectCard(page, CARD_A.card_id);

    const [resumeResponse] = await Promise.all([
      page.waitForResponse((r) => r.url().includes(`/execute/${CARD_A.card_id}/resume`) && r.request().method() === 'POST'),
      page.getByTestId('resume-button').click(),
    ]);
    expect(resumeResponse.status()).toBe(200);
    await expect(page.getByTestId('execution-status-badge')).toHaveText(/Running|Resuming/);
  });

  test('task isolation: switching plans never leaks the previous plan artifacts', async ({ page }) => {
    await mockManagerWorkspaceRoutes(page);
    await openManagerTab(page);

    await selectCard(page, CARD_A.card_id);
    await expect(page.getByText(/Objective: Add the reporting endpoint/)).toBeVisible();
    await expect(page.getByTestId('scope-expansion-decision')).toBeVisible();

    await selectCard(page, CARD_B.card_id);
    await expect(page.getByRole('heading', { name: CARD_B.title })).toBeVisible();
    await expect(page.getByText(/Objective: Ship the billing export/)).toBeVisible();
    // Card A's charter text and pending scope-expansion decision must be gone.
    await expect(page.getByText(/Objective: Add the reporting endpoint/)).not.toBeVisible();
    await expect(page.getByTestId('scope-expansion-decision')).not.toBeVisible();
  });

  test('axe: manager workspace has no critical/serious WCAG A/AA violations', async ({ page }) => {
    await mockManagerWorkspaceRoutes(page);
    await openManagerTab(page);
    await selectCard(page, CARD_A.card_id);
    await expect(page.getByText(/Objective: Add the reporting endpoint/)).toBeVisible();
    await page.waitForTimeout(300);

    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
    const critical = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
    expect(
      critical,
      `Found ${critical.length} critical/serious violations:\n` +
        critical.map((v) => `  - ${v.id}: ${v.help} (${v.nodes.length} nodes)`).join('\n'),
    ).toHaveLength(0);
  });
});
