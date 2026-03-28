/**
 * mock-data.ts — realistic test data matching the TypeScript types in
 * src/api/types.ts.  Used by test fixtures and individual tests to seed
 * API route intercepts without needing a live backend.
 *
 * Data is intentionally varied to exercise different UI states:
 *   - Cards spanning all 5 column types
 *   - Different priority levels, risk levels, agent rosters
 *   - Signals with different severities and statuses
 *   - A forge plan with 3 phases, 2 gates, mixed agent types
 */

import type {
  PmoCard,
  PmoProject,
  ProgramHealth,
  PmoSignal,
  BoardResponse,
  ForgePlanResponse,
  ForgePlanPhase,
  InterviewResponse,
  AdoSearchResponse,
  ExecuteCardResponse,
  ForgeApproveResponse,
} from '../../src/api/types.js';

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export const MOCK_PROJECTS: PmoProject[] = [
  {
    project_id: 'proj-alpha',
    name: 'Alpha Service',
    path: '/home/user/projects/alpha',
    program: 'ALPHA',
    color: '#1e40af',
    description: 'Core service layer for the alpha programme',
    registered_at: '2025-01-15T09:00:00Z',
    ado_project: 'AlphaADO',
  },
  {
    project_id: 'proj-beta',
    name: 'Beta Frontend',
    path: '/home/user/projects/beta',
    program: 'BETA',
    color: '#7c3aed',
    description: 'React frontend for the beta programme',
    registered_at: '2025-02-01T11:00:00Z',
    ado_project: 'BetaADO',
  },
];

// ---------------------------------------------------------------------------
// Cards — one per column + extras for interesting states
// ---------------------------------------------------------------------------

export const MOCK_CARD_QUEUED: PmoCard = {
  card_id: 'card-001',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Implement authentication middleware',
  column: 'queued',
  risk_level: 'medium',
  priority: 1,  // P1 — high
  agents: ['backend-engineer', 'security-reviewer'],
  steps_completed: 0,
  steps_total: 5,
  gates_passed: 0,
  current_phase: 'Ready for execution',
  error: '',
  created_at: '2025-03-01T08:00:00Z',
  updated_at: '2025-03-28T10:00:00Z',
  external_id: 'ADO-1234',
};

export const MOCK_CARD_EXECUTING: PmoCard = {
  card_id: 'card-002',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Migrate user profile schema to PostgreSQL',
  column: 'executing',
  risk_level: 'high',
  priority: 2,  // P0 — critical
  agents: ['backend-engineer', 'data-engineer', 'test-engineer'],
  steps_completed: 3,
  steps_total: 8,
  gates_passed: 1,
  current_phase: 'Phase 2: Data migration scripts',
  error: '',
  created_at: '2025-03-25T14:00:00Z',
  updated_at: '2025-03-28T09:30:00Z',
  external_id: 'ADO-1235',
};

export const MOCK_CARD_AWAITING_HUMAN: PmoCard = {
  card_id: 'card-003',
  project_id: 'proj-beta',
  program: 'BETA',
  title: 'Review API contract changes with stakeholders',
  column: 'awaiting_human',
  risk_level: 'high',
  priority: 2,  // P0 — critical
  agents: ['architect', 'backend-engineer'],
  steps_completed: 2,
  steps_total: 6,
  gates_passed: 1,
  current_phase: 'Step 3: Awaiting stakeholder approval on breaking changes',
  error: '',
  created_at: '2025-03-20T10:00:00Z',
  updated_at: '2025-03-28T08:00:00Z',
  external_id: 'ADO-1236',
};

export const MOCK_CARD_VALIDATING: PmoCard = {
  card_id: 'card-004',
  project_id: 'proj-alpha',
  program: 'ALPHA',
  title: 'Add rate limiting to public endpoints',
  column: 'validating',
  risk_level: 'low',
  priority: 0,  // P2 — normal
  agents: ['test-engineer'],
  steps_completed: 6,
  steps_total: 7,
  gates_passed: 2,
  current_phase: 'Gate: pytest + coverage baseline',
  error: '',
  created_at: '2025-03-22T12:00:00Z',
  updated_at: '2025-03-28T07:45:00Z',
  external_id: 'ADO-1237',
};

export const MOCK_CARD_DEPLOYED: PmoCard = {
  card_id: 'card-005',
  project_id: 'proj-beta',
  program: 'BETA',
  title: 'Dark mode toggle — design system tokens',
  column: 'deployed',
  risk_level: 'low',
  priority: 0,  // P2 — normal
  agents: ['frontend-engineer'],
  steps_completed: 4,
  steps_total: 4,
  gates_passed: 2,
  current_phase: '',
  error: '',
  created_at: '2025-03-10T09:00:00Z',
  updated_at: '2025-03-27T16:00:00Z',
  external_id: 'ADO-1238',
};

export const MOCK_CARD_WITH_ERROR: PmoCard = {
  card_id: 'card-006',
  project_id: 'proj-beta',
  program: 'BETA',
  title: 'Refactor event bus to typed payloads',
  column: 'queued',
  risk_level: 'medium',
  priority: 1,
  agents: ['backend-engineer'],
  steps_completed: 0,
  steps_total: 3,
  gates_passed: 0,
  current_phase: '',
  error: 'Step 1 failed: module not found — check PYTHONPATH',
  created_at: '2025-03-26T10:00:00Z',
  updated_at: '2025-03-28T06:00:00Z',
  external_id: '',
};

export const ALL_MOCK_CARDS: PmoCard[] = [
  MOCK_CARD_QUEUED,
  MOCK_CARD_EXECUTING,
  MOCK_CARD_AWAITING_HUMAN,
  MOCK_CARD_VALIDATING,
  MOCK_CARD_DEPLOYED,
  MOCK_CARD_WITH_ERROR,
];

// ---------------------------------------------------------------------------
// Program health
// ---------------------------------------------------------------------------

export const MOCK_HEALTH: Record<string, ProgramHealth> = {
  ALPHA: {
    program: 'ALPHA',
    total_plans: 4,
    active: 2,
    completed: 1,
    blocked: 0,
    failed: 0,
    completion_pct: 25,
  },
  BETA: {
    program: 'BETA',
    total_plans: 3,
    active: 1,
    completed: 1,
    blocked: 1,
    failed: 0,
    completion_pct: 33,
  },
};

// ---------------------------------------------------------------------------
// Board response
// ---------------------------------------------------------------------------

export const MOCK_BOARD_RESPONSE: BoardResponse = {
  cards: ALL_MOCK_CARDS,
  health: MOCK_HEALTH,
};

export const MOCK_EMPTY_BOARD_RESPONSE: BoardResponse = {
  cards: [],
  health: {},
};

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

export const MOCK_SIGNAL_CRITICAL: PmoSignal = {
  signal_id: 'sig-crit-001',
  signal_type: 'blocker',
  title: 'Authentication service returning 500 in prod',
  description: 'All auth requests failing since 08:00 UTC. Affects 100% of logins.',
  severity: 'critical',
  status: 'open',
  created_at: '2025-03-28T08:00:00Z',
  forge_task_id: '',
  source_project_id: 'proj-alpha',
};

export const MOCK_SIGNAL_MEDIUM: PmoSignal = {
  signal_id: 'sig-med-002',
  signal_type: 'escalation',
  title: 'ADO sync lagging by 2+ hours',
  description: 'Board state is stale — ADO adapter has not synced since 06:00.',
  severity: 'medium',
  status: 'open',
  created_at: '2025-03-28T06:00:00Z',
  forge_task_id: '',
  source_project_id: 'proj-beta',
};

export const MOCK_SIGNAL_RESOLVED: PmoSignal = {
  signal_id: 'sig-low-003',
  signal_type: 'bug',
  title: 'Dashboard chart labels truncated on mobile',
  description: 'Minor display issue on 375px viewport.',
  severity: 'low',
  status: 'resolved',
  created_at: '2025-03-27T14:00:00Z',
  forge_task_id: '',
  source_project_id: 'proj-beta',
};

export const ALL_MOCK_SIGNALS: PmoSignal[] = [
  MOCK_SIGNAL_CRITICAL,
  MOCK_SIGNAL_MEDIUM,
  MOCK_SIGNAL_RESOLVED,
];

// ---------------------------------------------------------------------------
// Forge plan — realistic 3-phase plan matching ForgePlanResponse schema
// ---------------------------------------------------------------------------

export const MOCK_FORGE_PLAN: ForgePlanResponse = {
  task_id: 'task-forge-001',
  task_summary:
    'Implement JWT-based authentication middleware for the Alpha service API. ' +
    'Covers token validation, refresh flow, and rate-limiting integration.',
  risk_level: 'MEDIUM',
  budget_tier: 'standard',
  execution_mode: 'sequential',
  git_strategy: 'feature-branch',
  shared_context:
    'Project uses FastAPI + SQLAlchemy. Auth library: python-jose. ' +
    'Tests use pytest + httpx. CI runs on GitHub Actions.',
  pattern_source: 'auth-patterns-v2',
  created_at: '2025-03-28T10:00:00Z',
  phases: [
    {
      phase_id: 0,
      name: 'Design & Schema',
      steps: [
        {
          step_id: '1.1',
          agent_name: 'architect',
          task_description: 'Define JWT token schema, refresh token storage strategy, and API contract for /auth endpoints',
          model: 'sonnet',
          depends_on: [],
          deliverables: ['docs/auth-design.md'],
          allowed_paths: ['docs/'],
          blocked_paths: [],
          context_files: ['docs/architecture.md'],
        },
        {
          step_id: '1.2',
          agent_name: 'backend-engineer',
          task_description: 'Create Pydantic models for AuthToken, RefreshToken, and LoginRequest',
          model: 'sonnet',
          depends_on: ['1.1'],
          deliverables: ['api/models/auth.py'],
          allowed_paths: ['api/models/'],
          blocked_paths: [],
          context_files: ['api/models/__init__.py'],
        },
      ],
      gate: {
        gate_type: 'lint',
        command: 'ruff check api/models/auth.py',
        description: 'Ensure new auth models pass linting',
        fail_on: ['E', 'F'],
      },
    },
    {
      phase_id: 1,
      name: 'Implementation',
      steps: [
        {
          step_id: '2.1',
          agent_name: 'backend-engineer',
          task_description: 'Implement JWT validation middleware using python-jose, with expiry and signature checks',
          model: 'sonnet',
          depends_on: ['1.2'],
          deliverables: ['api/middleware/auth.py'],
          allowed_paths: ['api/middleware/'],
          blocked_paths: ['api/admin/'],
          context_files: ['api/models/auth.py', 'requirements.txt'],
        },
        {
          step_id: '2.2',
          agent_name: 'backend-engineer',
          task_description: 'Implement refresh token endpoint with sliding window expiry',
          model: 'sonnet',
          depends_on: ['2.1'],
          deliverables: ['api/routers/auth.py'],
          allowed_paths: ['api/routers/'],
          blocked_paths: [],
          context_files: ['api/middleware/auth.py'],
        },
        {
          step_id: '2.3',
          agent_name: 'security-reviewer',
          task_description: 'Review auth implementation for OWASP A07 (Identification and Authentication Failures)',
          model: 'sonnet',
          depends_on: ['2.1', '2.2'],
          deliverables: ['docs/security-review-auth.md'],
          allowed_paths: ['docs/'],
          blocked_paths: [],
          context_files: ['api/middleware/auth.py', 'api/routers/auth.py'],
        },
      ],
      gate: {
        gate_type: 'test',
        command: 'pytest tests/test_auth.py -v --tb=short',
        description: 'Auth unit tests must pass',
        fail_on: ['FAILED', 'ERROR'],
      },
    },
    {
      phase_id: 2,
      name: 'Test Coverage',
      steps: [
        {
          step_id: '3.1',
          agent_name: 'test-engineer',
          task_description: 'Write integration tests for /auth/login, /auth/refresh, and /auth/logout endpoints',
          model: 'sonnet',
          depends_on: ['2.2'],
          deliverables: ['tests/test_auth.py', 'tests/test_auth_integration.py'],
          allowed_paths: ['tests/'],
          blocked_paths: [],
          context_files: ['api/routers/auth.py', 'api/middleware/auth.py'],
        },
      ],
    } as ForgePlanPhase,
  ],
};

// A minimal plan for simple tests that don't need full complexity
export const MOCK_FORGE_PLAN_MINIMAL: ForgePlanResponse = {
  task_id: 'task-forge-min',
  task_summary: 'Add dark mode toggle to the settings panel.',
  risk_level: 'LOW',
  budget_tier: 'economy',
  execution_mode: 'sequential',
  git_strategy: 'feature-branch',
  shared_context: '',
  pattern_source: null,
  created_at: '2025-03-28T11:00:00Z',
  phases: [
    {
      phase_id: 0,
      name: 'Frontend Changes',
      steps: [
        {
          step_id: '1.1',
          agent_name: 'frontend-engineer',
          task_description: 'Add dark mode toggle component to settings panel',
          model: 'haiku',
          depends_on: [],
          deliverables: ['src/components/DarkModeToggle.tsx'],
          allowed_paths: ['src/'],
          blocked_paths: [],
          context_files: ['src/styles/tokens.ts'],
        },
      ],
    } as ForgePlanPhase,
  ],
};

// ---------------------------------------------------------------------------
// Interview questions
// ---------------------------------------------------------------------------

export const MOCK_INTERVIEW_RESPONSE: InterviewResponse = {
  questions: [
    {
      id: 'q1',
      question: 'Should the JWT tokens use RS256 (asymmetric) or HS256 (symmetric) signing?',
      context: 'RS256 is preferred for distributed systems where multiple services verify tokens.',
      answer_type: 'choice',
      choices: ['RS256 (recommended)', 'HS256 (simpler)'],
    },
    {
      id: 'q2',
      question: 'What should the access token expiry be?',
      context: 'Short-lived tokens reduce the blast radius of token theft.',
      answer_type: 'choice',
      choices: ['15 minutes', '1 hour', '24 hours'],
    },
    {
      id: 'q3',
      question: 'Are there existing user roles that need to be embedded in the JWT claims?',
      context: 'If so, list the role names so they can be included in the token schema.',
      answer_type: 'text',
    },
  ],
};

// ---------------------------------------------------------------------------
// ADO work items
// ---------------------------------------------------------------------------

export const MOCK_ADO_ITEMS: AdoSearchResponse = {
  items: [
    {
      id: 'ADO-1234',
      title: 'Implement JWT authentication for API gateway',
      type: 'User Story',
      program: 'ALPHA',
      owner: 'Alice Smith',
      priority: 'High',
      description: 'As a user, I need secure authentication to access protected API endpoints.',
    },
    {
      id: 'ADO-1235',
      title: 'Migrate legacy auth to OAuth 2.0',
      type: 'Epic',
      program: 'ALPHA',
      owner: 'Bob Jones',
      priority: 'Critical',
      description: 'Full OAuth 2.0 migration including PKCE support.',
    },
    {
      id: 'ADO-1236',
      title: 'Add MFA support to login flow',
      type: 'Feature',
      program: 'BETA',
      owner: 'Carol Wu',
      priority: 'Medium',
      description: 'TOTP-based multi-factor authentication using authenticator apps.',
    },
  ],
};

// ---------------------------------------------------------------------------
// Execution response
// ---------------------------------------------------------------------------

export const MOCK_EXECUTE_RESPONSE: ExecuteCardResponse = {
  task_id: 'card-001',
  pid: 12345,
  status: 'launched',
  model: 'claude-sonnet-4-5',
  dry_run: false,
};

// ---------------------------------------------------------------------------
// Forge approve response
// ---------------------------------------------------------------------------

export const MOCK_APPROVE_RESPONSE: ForgeApproveResponse = {
  saved: true,
  path: '/home/user/projects/alpha/.claude/team-context/plan.json',
};

// ---------------------------------------------------------------------------
// Helper: build a BoardResponse with only specific columns populated
// ---------------------------------------------------------------------------

export function boardWithCards(cards: PmoCard[]): BoardResponse {
  const programs = Array.from(new Set(cards.map(c => c.program)));
  const health: Record<string, ProgramHealth> = {};
  for (const prog of programs) {
    const progCards = cards.filter(c => c.program === prog);
    health[prog] = {
      program: prog,
      total_plans: progCards.length,
      active: progCards.filter(c => c.column === 'executing').length,
      completed: progCards.filter(c => c.column === 'deployed').length,
      blocked: progCards.filter(c => c.column === 'awaiting_human').length,
      failed: progCards.filter(c => c.error).length,
      completion_pct: Math.round(
        (progCards.filter(c => c.column === 'deployed').length / progCards.length) * 100,
      ),
    };
  }
  return { cards, health };
}
