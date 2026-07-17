import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { ManagerWorkspaceView } from '../ManagerWorkspaceView';
import { api } from '../../api/client';
import { ToastProvider } from '../../contexts/ToastContext';
import type {
  BoardResponse,
  CardExecutionDetail,
  PmoCard,
} from '../../api/types';

function renderWithToast(ui: ReactElement) {
  return render(<ToastProvider>{ui}</ToastProvider>);
}

afterEach(() => {
  vi.restoreAllMocks();
});

const baseCard: PmoCard = {
  card_id: 'task-1',
  project_id: 'proj-a',
  program: 'core',
  title: 'Ship the widget',
  column: 'executing',
  risk_level: 'MEDIUM',
  priority: 1,
  agents: ['frontend-engineer'],
  steps_completed: 2,
  steps_total: 5,
  gates_passed: 1,
  current_phase: 'Build',
  error: '',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-10T00:00:00Z',
  external_id: '',
};

const emptyExecution: CardExecutionDetail = {
  task_id: 'task-1',
  status: 'executing',
  current_phase: 'Build',
  steps: [],
  started_at: '2026-07-01T00:00:00Z',
  elapsed_seconds: 120,
  turn_count: 3,
  tokens_used_usd: 0.5,
  goal: {
    completion_condition: null,
    goal_status: '',
    amend_cycles_used: 0,
    max_amend_cycles: 0,
    checks_count: 0,
    last_check_met: null,
  },
};

/** Stubs every endpoint the workspace calls for a NON manager-mode card. */
function mockNonManagerDefaults(card: PmoCard = baseCard) {
  vi.spyOn(api, 'getBoard').mockResolvedValue({ cards: [card], health: {} } as BoardResponse);
  vi.spyOn(api, 'getCardDetail').mockResolvedValue({ ...card, plan: null });
  vi.spyOn(api, 'getCardExecution').mockResolvedValue({ ...emptyExecution, task_id: card.card_id });
  vi.spyOn(api, 'listExecutionDecisions').mockResolvedValue({ count: 0, decisions: [] });
  vi.spyOn(api, 'listPendingGates').mockResolvedValue([]);
}

/** Stubs every endpoint the workspace calls for a manager-mode card,
 * including the full manager-artifact fan-out. */
function mockManagerDefaults(card: PmoCard = baseCard) {
  vi.spyOn(api, 'getBoard').mockResolvedValue({ cards: [card], health: {} } as BoardResponse);
  vi.spyOn(api, 'getCardDetail').mockResolvedValue({
    ...card,
    plan: {
      task_id: card.card_id,
      task_summary: 'Ship the widget',
      risk_level: 'MEDIUM',
      budget_tier: 'standard',
      execution_mode: 'sequential',
      git_strategy: 'branch',
      phases: [
        {
          phase_id: 1,
          name: 'Build',
          steps: [
            {
              step_id: '1.1',
              agent_name: 'frontend-engineer',
              task_description: 'Build the UI',
              model: 'sonnet',
              depends_on: [],
              deliverables: [],
              allowed_paths: [],
              blocked_paths: [],
              context_files: [],
            },
          ],
        },
      ],
      shared_context: '',
      pattern_source: null,
      created_at: '2026-07-01T00:00:00Z',
      manager_mode: true,
    },
  });
  vi.spyOn(api, 'getCardExecution').mockResolvedValue({ ...emptyExecution, task_id: card.card_id });
  vi.spyOn(api, 'listExecutionDecisions').mockResolvedValue({ count: 0, decisions: [] });
  vi.spyOn(api, 'listPendingGates').mockResolvedValue([]);

  vi.spyOn(api, 'getManagerCharter').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    markdown: '# Charter\n\nObjective: ship the widget on time.',
  });
  vi.spyOn(api, 'getManagerScopeMap').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    scope_map: {
      task_id: card.card_id,
      workstreams: [],
      cross_cutting_concerns: ['logging'],
      out_of_scope: ['billing'],
      scope_expansion_policy: 'queue_for_manager',
    },
  });
  vi.spyOn(api, 'getManagerWorkstreams').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    links: [
      {
        phase_id: 1,
        phase_name: 'Build',
        workstream: {
          id: 'ws-1',
          name: 'Frontend workstream',
          objective: 'Build the UI',
          likely_paths: [],
          allowed_paths: ['pmo-ui/src/'],
          owner_role: 'frontend-engineer',
          dependencies: [],
          deliverables: [],
          risks: [],
        },
      },
    ],
  });
  vi.spyOn(api, 'getManagerTeamBlueprint').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    team_blueprint: {
      task_id: card.card_id,
      team_name: 'Console Squad',
      mission: 'Build the director console',
      roles: [
        {
          role: 'frontend-engineer',
          agent_name: 'frontend-engineer',
          mission: 'Build the UI',
          owns: ['pmo-ui/src/'],
          does_not_own: [],
          required_knowledge_packs: [],
          default_context_budget: 12000,
          expected_handoffs: [],
          escalation_triggers: [],
        },
      ],
      workstream_assignments: { 'ws-1': 'frontend-engineer' },
      collaboration_rules: [],
      escalation_triggers: [],
      phase_policies: {},
    },
  });
  vi.spyOn(api, 'listManagerRoleCards').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    role_cards: [{ role: 'frontend-engineer', markdown: '# frontend-engineer\n\nBuild the UI.' }],
  });
  vi.spyOn(api, 'getManagerKnowledgePlan').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    knowledge_plan: {
      task_id: card.card_id,
      selected_packs: [],
      missing_packs: [],
      stale_packs: [],
      per_role_packs: {},
      per_step_packs: {},
    },
  });
  vi.spyOn(api, 'listManagerScopeContracts').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    contracts: [{ step_id: '1.1', agent_name: 'frontend-engineer', workstream_id: 'ws-1', allowed_paths: ['pmo-ui/src/'] }],
  });
  vi.spyOn(api, 'listManagerContextBundles').mockResolvedValue({
    task_id: card.card_id,
    revision: 1,
    published_at: '2026-07-01T00:00:00Z',
    bundles: [],
  });
  vi.spyOn(api, 'getManagerVersion').mockResolvedValue({
    task_id: card.card_id,
    published: true,
    revision: 1,
    prior_revision: 0,
    trigger: 'manual',
    created_at: '2026-07-01T00:00:00Z',
    plan_fingerprint: 'fp-abc',
    phase_count: 1,
    step_count: 1,
    published_paths: [],
  });
  vi.spyOn(api, 'getManagerValidation').mockResolvedValue({
    task_id: card.card_id,
    published: true,
    valid: true,
    fingerprint_match: true,
    revision: 1,
    current_plan_fingerprint: 'fp-abc',
    published_plan_fingerprint: 'fp-abc',
    errors: [],
  });
  vi.spyOn(api, 'listManagerDecisions').mockResolvedValue({ task_id: card.card_id, count: 0, decisions: [] });
}

async function openCard(id = baseCard.card_id) {
  const user = userEvent.setup();
  renderWithToast(<ManagerWorkspaceView />);
  const select = await screen.findByLabelText('Choose a plan');
  await user.selectOptions(select, id);
  return user;
}

describe('ManagerWorkspaceView', () => {
  it('lets an operator pick a plan from the board and see its header and status', async () => {
    mockNonManagerDefaults();
    await openCard();

    expect(await screen.findByRole('heading', { name: 'Ship the widget' })).toBeInTheDocument();
    expect(screen.getByTestId('execution-status-badge')).toHaveTextContent('Executing');
  });

  it('shows a not-manager-mode banner and skips manager artifact calls for a plain plan', async () => {
    mockNonManagerDefaults();
    const charterSpy = vi.spyOn(api, 'getManagerCharter');
    await openCard();

    expect(await screen.findByTestId('not-manager-mode-banner')).toBeInTheDocument();
    expect(charterSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('section-charter')).not.toBeInTheDocument();
  });

  it('renders charter, workstream, team, and scope-contract evidence for a manager-mode plan', async () => {
    mockManagerDefaults();
    await openCard();

    expect(await screen.findByText(/Objective: ship the widget on time/)).toBeInTheDocument();
    expect(screen.queryByTestId('not-manager-mode-banner')).not.toBeInTheDocument();

    // Phase/workstream health links the phase to its owning workstream, not just a raw id.
    expect(screen.getByText(/owned by/)).toBeInTheDocument();
    expect(screen.getAllByText(/Frontend workstream/).length).toBeGreaterThan(0);

    // Team blueprint role summary.
    expect(screen.getByText('Console Squad')).toBeInTheDocument();
    expect(screen.getAllByText(/Build the UI/).length).toBeGreaterThan(0);

    // Scope contract summary row (step id + agent + workstream, not a raw path).
    expect(screen.getByText(/1\.1/)).toBeInTheDocument();
  });

  it('shows failed distinctly whenever the card carries an error, even mid-execution', async () => {
    mockNonManagerDefaults({ ...baseCard, column: 'executing', error: 'agent crashed on step 2' });
    await openCard();

    expect(await screen.findByTestId('execution-status-badge')).toHaveTextContent('Failed');
  });

  it('shows completed distinctly for a deployed card', async () => {
    mockNonManagerDefaults({ ...baseCard, column: 'deployed' });
    await openCard();

    expect(await screen.findByTestId('execution-status-badge')).toHaveTextContent('Completed');
  });

  it('shows paused distinctly after the operator pauses execution from the workspace', async () => {
    mockNonManagerDefaults();
    vi.spyOn(api, 'pauseExecution').mockResolvedValue({ status: 'paused', task_id: baseCard.card_id });
    const user = await openCard();

    await screen.findByTestId('execution-status-badge');
    await user.click(screen.getByTestId('pause-button'));

    expect(await screen.findByTestId('execution-status-badge')).toHaveTextContent('Paused');
    expect(api.pauseExecution).toHaveBeenCalledWith(baseCard.card_id);
  });

  it('resolves a pending decision with a rationale, refreshes, and shows resuming', async () => {
    mockNonManagerDefaults({ ...baseCard, column: 'awaiting_human' });
    vi.spyOn(api, 'listExecutionDecisions').mockResolvedValue({
      count: 1,
      decisions: [
        {
          request_id: 'req-1',
          task_id: baseCard.card_id,
          decision_type: 'approval',
          summary: 'Approve the deploy step?',
          options: ['approve', 'reject'],
          deadline: null,
          context_files: [],
          created_at: '2026-07-05T00:00:00Z',
          status: 'pending',
        },
      ],
    });
    const resolveSpy = vi.spyOn(api, 'resolveExecutionDecision').mockResolvedValue({
      resolved: true,
      execution_resumed: true,
    });

    const user = await openCard();
    const form = await screen.findByTestId('execution-decision-form');

    await user.click(within(form).getByLabelText('approve'));
    await user.type(within(form).getByLabelText('Rationale (optional)'), 'Looks safe to ship.');
    await user.click(within(form).getByRole('button', { name: /resolve decision/i }));

    expect(resolveSpy).toHaveBeenCalledWith(baseCard.card_id, 'req-1', {
      option: 'approve',
      rationale: 'Looks safe to ship.',
    });
    expect(await screen.findByTestId('execution-status-badge')).toHaveTextContent('Resuming');
  });

  it('renders GateApprovalPanel for a card awaiting human gate review', async () => {
    mockNonManagerDefaults({ ...baseCard, column: 'awaiting_human' });
    vi.spyOn(api, 'listPendingGates').mockResolvedValue([
      {
        task_id: baseCard.card_id,
        project_id: baseCard.project_id,
        phase_id: 1,
        phase_name: 'Build',
        approval_context: 'Ready for review.',
        approval_options: ['approve', 'reject'],
        task_summary: baseCard.title,
        current_phase_name: 'Build',
      },
    ]);
    await openCard();

    expect(await screen.findByText(/Ding! Pick up, chef!/)).toBeInTheDocument();
  });

  it('approves a scope-expansion decision with additional paths', async () => {
    mockManagerDefaults();
    vi.spyOn(api, 'listManagerDecisions').mockResolvedValue({
      task_id: baseCard.card_id,
      count: 1,
      decisions: [
        {
          decision_id: 'dec-1',
          decision_type: 'scope_expansion',
          task_id: baseCard.card_id,
          summary: 'Step 1.1 touched files outside its contract',
          context: 'Diff touched pmo-ui/src/api/client.ts',
          options: ['approve', 'reject'],
          recommended_option: 'approve',
          created_at: '2026-07-05T00:00:00Z',
          resolved_at: null,
          resolution: null,
          markdown: '',
        },
      ],
    });
    const resolveSpy = vi.spyOn(api, 'resolveManagerDecision').mockResolvedValue({
      applied: true,
      resolution: 'approve',
      step_id: '1.1',
      decision_id: 'dec-1',
      new_allowed_paths: ['pmo-ui/src/api/client.ts'],
      error: null,
    });

    const user = await openCard();
    const decisionCard = await screen.findByTestId('scope-expansion-decision');

    await user.type(
      within(decisionCard).getByLabelText(/Additional allowed paths/),
      'pmo-ui/src/api/client.ts',
    );
    await user.click(within(decisionCard).getByRole('button', { name: /approve expansion/i }));

    expect(resolveSpy).toHaveBeenCalledWith(baseCard.card_id, 'dec-1', {
      resolution: 'approve',
      additional_paths: ['pmo-ui/src/api/client.ts'],
    });
  });

  it('exposes the card picker and manual task-id input as labeled, keyboard-operable controls', async () => {
    mockNonManagerDefaults();
    renderWithToast(<ManagerWorkspaceView />);

    expect(await screen.findByLabelText('Choose a plan')).toBeInTheDocument();
    expect(screen.getByLabelText('Or open by task ID')).toBeInTheDocument();
  });

  it('denies (rejects) a pending decision with a rationale', async () => {
    mockNonManagerDefaults({ ...baseCard, column: 'awaiting_human' });
    vi.spyOn(api, 'listExecutionDecisions').mockResolvedValue({
      count: 1,
      decisions: [
        {
          request_id: 'req-2',
          task_id: baseCard.card_id,
          decision_type: 'approval',
          summary: 'Approve the deploy step?',
          options: ['approve', 'reject'],
          deadline: null,
          context_files: [],
          created_at: '2026-07-05T00:00:00Z',
          status: 'pending',
        },
      ],
    });
    const resolveSpy = vi.spyOn(api, 'resolveExecutionDecision').mockResolvedValue({
      resolved: true,
      execution_resumed: false,
    });

    const user = await openCard();
    const form = await screen.findByTestId('execution-decision-form');

    await user.click(within(form).getByLabelText('reject'));
    await user.type(within(form).getByLabelText('Rationale (optional)'), 'Needs another security pass.');
    await user.click(within(form).getByRole('button', { name: /resolve decision/i }));

    expect(resolveSpy).toHaveBeenCalledWith(baseCard.card_id, 'req-2', {
      option: 'reject',
      rationale: 'Needs another security pass.',
    });
    // A rejection that does not resume execution must not show "Resuming".
    expect(await screen.findByTestId('execution-status-badge')).not.toHaveTextContent('Resuming');
  });

  it('denies a scope-expansion decision', async () => {
    mockManagerDefaults();
    vi.spyOn(api, 'listManagerDecisions').mockResolvedValue({
      task_id: baseCard.card_id,
      count: 1,
      decisions: [
        {
          decision_id: 'dec-2',
          decision_type: 'scope_expansion',
          task_id: baseCard.card_id,
          summary: 'Step 1.1 touched files outside its contract',
          context: 'Diff touched pmo-ui/src/api/client.ts',
          options: ['approve', 'reject'],
          recommended_option: 'approve',
          created_at: '2026-07-05T00:00:00Z',
          resolved_at: null,
          resolution: null,
          markdown: '',
        },
      ],
    });
    const resolveSpy = vi.spyOn(api, 'resolveManagerDecision').mockResolvedValue({
      applied: true,
      resolution: 'reject',
      step_id: '1.1',
      decision_id: 'dec-2',
      new_allowed_paths: [],
      error: null,
    });

    const user = await openCard();
    const decisionCard = await screen.findByTestId('scope-expansion-decision');

    await user.click(within(decisionCard).getByRole('button', { name: /deny expansion/i }));

    expect(resolveSpy).toHaveBeenCalledWith(baseCard.card_id, 'dec-2', {
      resolution: 'reject',
      additional_paths: undefined,
    });
  });

  it('shows a stale-artifacts banner when published artifacts no longer match the current plan', async () => {
    mockManagerDefaults();
    vi.spyOn(api, 'getManagerValidation').mockResolvedValue({
      task_id: baseCard.card_id,
      published: true,
      valid: false,
      fingerprint_match: false,
      revision: 3,
      current_plan_fingerprint: 'fp-new',
      published_plan_fingerprint: 'fp-old',
      errors: ['published revision 3 was built from a different plan shape'],
    });
    await openCard();

    const banner = await screen.findByTestId('stale-artifacts-banner');
    expect(banner).toHaveTextContent(/stale/i);
    expect(banner).toHaveTextContent(/revision 3/);
  });

  it('keeps the rest of the workspace usable when one manager artifact fails to load', async () => {
    mockManagerDefaults();
    vi.spyOn(api, 'getManagerCharter').mockRejectedValue(new Error('charter service unavailable'));
    await openCard();

    // The failing section surfaces its own error instead of crashing the page...
    expect(await screen.findByText(/charter service unavailable/)).toBeInTheDocument();
    // ...while an unrelated, successfully-loaded section still renders.
    expect(screen.getByText('Console Squad')).toBeInTheDocument();
    expect(screen.getByTestId('manager-workspace')).toBeInTheDocument();
  });

  it('does not leak one card artifacts into another when the operator switches plans', async () => {
    const cardB: PmoCard = { ...baseCard, card_id: 'task-2', title: 'Ship the gadget' };
    mockManagerDefaults();
    vi.spyOn(api, 'getBoard').mockResolvedValue({
      cards: [baseCard, cardB],
      health: {},
    } as BoardResponse);

    const user = userEvent.setup();
    renderWithToast(<ManagerWorkspaceView />);
    const select = await screen.findByLabelText('Choose a plan');

    await user.selectOptions(select, baseCard.card_id);
    expect(await screen.findByText(/Objective: ship the widget on time/)).toBeInTheDocument();

    // Second card has its own (empty) charter and a distinct title -- none
    // of card A's manager-mode content should still be on screen.
    vi.spyOn(api, 'getCardDetail').mockResolvedValue({ ...cardB, plan: null });
    vi.spyOn(api, 'getManagerCharter').mockClear();

    await user.selectOptions(select, cardB.card_id);
    expect(await screen.findByRole('heading', { name: 'Ship the gadget' })).toBeInTheDocument();
    expect(screen.getByTestId('not-manager-mode-banner')).toBeInTheDocument();
    expect(screen.queryByText(/Objective: ship the widget on time/)).not.toBeInTheDocument();
    expect(screen.queryByText('Ship the widget')).not.toBeInTheDocument();
  });

  it('exposes accessible names, roles, and live regions for key operator controls', async () => {
    mockManagerDefaults();
    await openCard();

    // Status badge is an ARIA live region so a status change is announced.
    const badge = await screen.findByTestId('execution-status-badge');
    expect(badge).toHaveAttribute('role', 'status');
    expect(badge).toHaveAttribute('aria-live', 'polite');

    // Step-completion progress bar carries real min/max/now values, not
    // just a visual bar.
    const progress = screen.getByRole('progressbar', { name: 'Plan step completion' });
    expect(progress).toHaveAttribute('aria-valuemin', '0');
    expect(progress).toHaveAttribute('aria-valuemax', String(baseCard.steps_total));
    expect(progress).toHaveAttribute('aria-valuenow', String(baseCard.steps_completed));

    // Primary action buttons all have accessible (non-empty) names.
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument();

    // Card picker + manual task-id input are both properly labeled.
    expect(screen.getByLabelText('Choose a plan')).toBeInTheDocument();
    expect(screen.getByLabelText('Or open by task ID')).toBeInTheDocument();
  });
});
