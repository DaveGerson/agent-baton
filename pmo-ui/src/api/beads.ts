// ---------------------------------------------------------------------------
// Beads API client (DX.6)
// ---------------------------------------------------------------------------
// The PMO surfaces beads as a graph + timeline.  The backend route at
// `/api/v1/pmo/beads` is being authored in parallel (see bead bd-aade);
// until it lands, this module falls back to a representative fixture so
// the UI is reviewable end-to-end.
// ---------------------------------------------------------------------------

const BASE = '/api/v1/pmo';

export type BeadType =
  | 'planning'
  | 'warning'
  | 'decision'
  | 'outcome'
  | 'discovery'
  | 'task'
  | 'message'
  | 'message_ack';

export type BeadStatus = 'open' | 'closed' | 'archived';

export type BeadLinkType =
  | 'blocks'
  | 'blocked_by'
  | 'relates_to'
  | 'discovered_from'
  | 'validates'
  | 'contradicts'
  | 'extends';

export interface BeadLink {
  target_bead_id: string;
  link_type: BeadLinkType;
  created_at?: string;
}

export interface Bead {
  bead_id: string;
  task_id: string;
  step_id: string;
  agent_name: string;
  bead_type: BeadType;
  content: string;
  confidence: 'high' | 'medium' | 'low';
  scope: 'step' | 'phase' | 'task' | 'project';
  tags: string[];
  affected_files: string[];
  status: BeadStatus;
  created_at: string;
  closed_at: string;
  summary: string;
  links: BeadLink[];
  source: string;
  token_estimate: number;
  retrieval_count?: number;
  quality_score?: number;
}

export interface BeadListResponse {
  beads: Bead[];
  total: number;
  fixture?: boolean;
}

// ---------------------------------------------------------------------------
// Fixture — mirrors the strategic-roadmap bead lineage so the graph
// looks meaningful without a backend.  Removed once the live route ships.
// ---------------------------------------------------------------------------

const _FIXTURE: Bead[] = [
  {
    bead_id: 'bd-d136',
    task_id: 'plan-strategic-roadmap',
    step_id: 'planning',
    agent_name: 'orchestrator',
    bead_type: 'planning',
    content: 'DX.6 — Visual bead graph + temporal timeline view in the PMO. Operators currently only see beads as flat CLI output; this surfaces blocks/blocked-by/relates-to graph and chronological history.',
    confidence: 'high',
    scope: 'project',
    tags: ['dx', 'pmo', 'visualization', 'roadmap'],
    affected_files: ['pmo-ui/src/views/BeadGraphView.tsx', 'pmo-ui/src/views/BeadTimelineView.tsx'],
    status: 'open',
    created_at: '2026-04-25T08:00:00Z',
    closed_at: '',
    summary: '',
    links: [
      { target_bead_id: 'bd-aade', link_type: 'blocked_by' },
      { target_bead_id: 'bd-ec46', link_type: 'relates_to' },
    ],
    source: 'planning-capture',
    token_estimate: 240,
    retrieval_count: 12,
    quality_score: 0.85,
  },
  {
    bead_id: 'bd-aade',
    task_id: 'plan-strategic-roadmap',
    step_id: 'planning',
    agent_name: 'frontend-engineer--react',
    bead_type: 'warning',
    content: 'DX.6 PMO UI Beads view needs API endpoint: GET /api/v1/pmo/beads. Backend agent should add a route in agent_baton/api/routes/beads.py wired off BeadStore.query() + read().',
    confidence: 'high',
    scope: 'project',
    tags: ['dx', 'api', 'backend-needed'],
    affected_files: ['agent_baton/api/routes/beads.py'],
    status: 'open',
    created_at: '2026-04-25T09:30:00Z',
    closed_at: '',
    summary: '',
    links: [
      { target_bead_id: 'bd-d136', link_type: 'blocks' },
    ],
    source: 'agent-signal',
    token_estimate: 180,
    retrieval_count: 3,
    quality_score: 0.7,
  },
  {
    bead_id: 'bd-ec46',
    task_id: 'plan-strategic-roadmap',
    step_id: 'planning',
    agent_name: 'orchestrator',
    bead_type: 'planning',
    content: 'Strategic roadmap: lift code-reviewer/test-engineer/auditor/security-reviewer/plan-reviewer to opus before execute on roadmap-lineage plans.',
    confidence: 'high',
    scope: 'project',
    tags: ['roadmap', 'strategy', 'reviewers'],
    affected_files: [],
    status: 'open',
    created_at: '2026-04-24T18:00:00Z',
    closed_at: '',
    summary: '',
    links: [],
    source: 'planning-capture',
    token_estimate: 320,
    retrieval_count: 21,
    quality_score: 0.92,
  },
  {
    bead_id: 'bd-1795',
    task_id: 'plan-audit-remediation',
    step_id: 'phase-c',
    agent_name: 'auditor',
    bead_type: 'discovery',
    content: 'Phase C audit found planner does not split 4+ file implementation phases into parallel concerns. See feedback_planner_parallelization.md.',
    confidence: 'medium',
    scope: 'task',
    tags: ['audit', 'planner', 'parallelization'],
    affected_files: ['agent_baton/core/engine/planner.py'],
    status: 'open',
    created_at: '2026-04-22T14:00:00Z',
    closed_at: '',
    summary: '',
    links: [
      { target_bead_id: 'bd-ab50', link_type: 'relates_to' },
    ],
    source: 'agent-signal',
    token_estimate: 140,
    retrieval_count: 5,
    quality_score: 0.6,
  },
  {
    bead_id: 'bd-ab50',
    task_id: 'plan-audit-remediation',
    step_id: 'phase-a',
    agent_name: 'auditor',
    bead_type: 'decision',
    content: 'Decision: token burn reduction via JSONL scanner accepted; baton usage counter now corrected (Apr 2026).',
    confidence: 'high',
    scope: 'project',
    tags: ['audit', 'tokens', 'cost'],
    affected_files: ['agent_baton/core/engine/usage.py'],
    status: 'closed',
    created_at: '2026-04-20T10:00:00Z',
    closed_at: '2026-04-23T16:00:00Z',
    summary: 'Implemented JSONL scanner; verified live spend.',
    links: [],
    source: 'retrospective',
    token_estimate: 220,
    retrieval_count: 8,
    quality_score: 0.88,
  },
  {
    bead_id: 'bd-3f10',
    task_id: 'plan-pmo-ux',
    step_id: 'phase-3',
    agent_name: 'frontend-engineer--react',
    bead_type: 'outcome',
    content: 'PMO UX remediation phases 1-3 shipped: keyboard nav, signals dock, persisted view state.',
    confidence: 'high',
    scope: 'task',
    tags: ['pmo', 'ux', 'shipped'],
    affected_files: ['pmo-ui/src/App.tsx', 'pmo-ui/src/components/KanbanBoard.tsx'],
    status: 'closed',
    created_at: '2026-04-18T11:00:00Z',
    closed_at: '2026-04-21T17:00:00Z',
    summary: 'Phases 1-3 of PMO UX remediation merged.',
    links: [
      { target_bead_id: 'bd-d136', link_type: 'relates_to' },
    ],
    source: 'agent-signal',
    token_estimate: 190,
    retrieval_count: 14,
    quality_score: 0.9,
  },
  {
    bead_id: 'bd-7c21',
    task_id: 'plan-teams',
    step_id: 'phase-5',
    agent_name: 'team-lead',
    bead_type: 'discovery',
    content: 'Multi-team support: nested team execution validated end-to-end with two leaders + messaging.',
    confidence: 'high',
    scope: 'project',
    tags: ['teams', 'orchestration', 'validated'],
    affected_files: ['agent_baton/core/teams/'],
    status: 'closed',
    created_at: '2026-04-19T09:00:00Z',
    closed_at: '2026-04-24T12:00:00Z',
    summary: 'Teams Phase 5 verified.',
    links: [],
    source: 'agent-signal',
    token_estimate: 280,
    retrieval_count: 11,
    quality_score: 0.95,
  },
  {
    bead_id: 'bd-9d44',
    task_id: 'plan-strategic-roadmap',
    step_id: 'planning',
    agent_name: 'plan-reviewer',
    bead_type: 'warning',
    content: 'Concurrent agent subagents on different git branches MUST use isolation: "worktree". Branch checkout alone does not isolate uncommitted changes.',
    confidence: 'high',
    scope: 'project',
    tags: ['concurrency', 'git', 'safety'],
    affected_files: [],
    status: 'open',
    created_at: '2026-04-23T15:00:00Z',
    closed_at: '',
    summary: '',
    links: [
      { target_bead_id: 'bd-ec46', link_type: 'relates_to' },
    ],
    source: 'agent-signal',
    token_estimate: 110,
    retrieval_count: 7,
    quality_score: 0.82,
  },
];

function _fixtureResponse(): BeadListResponse {
  return { beads: _FIXTURE, total: _FIXTURE.length, fixture: true };
}

async function _fetchJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface BeadListParams {
  /** Filter by status: 'open' | 'closed' | 'archived' | 'all'. Default 'open'. */
  status?: string;
  /** Filter to a single bead_type (e.g. 'warning'). */
  bead_type?: BeadType | string;
  /** Comma-separated tags; AND semantics. */
  tags?: string | string[];
  /** Filter to beads from a specific task/execution. */
  task_id?: string;
  /** Filter by project (reserved — currently passed through to backend). */
  project_id?: string;
  /** Maximum number of beads to return. Default 200, max 1000. */
  limit?: number;
}

export const beadsApi = {
  /**
   * List beads from the project's bead store.  Falls back to the
   * fixture when the backend route is missing (404 / network failure).
   */
  async list(params?: BeadListParams): Promise<BeadListResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const qs = new URLSearchParams();
      if (params?.status)     qs.set('status', params.status);
      if (params?.bead_type)  qs.set('bead_type', String(params.bead_type));
      if (params?.task_id)    qs.set('task_id', params.task_id);
      if (params?.project_id) qs.set('project_id', params.project_id);
      if (params?.limit)      qs.set('limit', String(params.limit));
      if (params?.tags) {
        const tagsStr = Array.isArray(params.tags)
          ? params.tags.join(',')
          : params.tags;
        if (tagsStr) qs.set('tags', tagsStr);
      }
      const suffix = qs.toString() ? `?${qs.toString()}` : '';
      const data = await _fetchJSON<BeadListResponse>(`/beads${suffix}`, controller.signal);
      // Defensive: backend may return raw array.
      if (Array.isArray(data)) {
        return { beads: data as unknown as Bead[], total: (data as unknown as Bead[]).length };
      }
      return data;
    } catch {
      return _fixtureResponse();
    } finally {
      clearTimeout(timeout);
    }
  },

  /** Fetch a single bead by ID.  Falls back to fixture lookup. */
  async get(beadId: string): Promise<Bead | null> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      return await _fetchJSON<Bead>(`/beads/${encodeURIComponent(beadId)}`, controller.signal);
    } catch {
      return _FIXTURE.find(b => b.bead_id === beadId) ?? null;
    } finally {
      clearTimeout(timeout);
    }
  },
};

// ---------------------------------------------------------------------------
// Visual mappings (shared by graph + timeline)
// ---------------------------------------------------------------------------

import { T } from '../styles/tokens';

export const BEAD_TYPE_COLOR: Record<BeadType, string> = {
  planning:    '#4b9bff',         // blue
  warning:     T.tangerine,        // orange
  decision:    T.blueberry,        // purple
  outcome:     T.mint,             // green
  discovery:   T.text2,            // gray
  task:        T.crust,            // golden
  message:     T.cherrySoft,       // soft pink
  message_ack: T.mintSoft,         // soft mint
};

export const BEAD_TYPE_LABEL: Record<BeadType, string> = {
  planning:    'Planning',
  warning:     'Warning',
  decision:    'Decision',
  outcome:     'Outcome',
  discovery:   'Discovery',
  task:        'Task',
  message:     'Message',
  message_ack: 'Message Ack',
};

export const LINK_TYPE_STYLE: Record<BeadLinkType, { color: string; dash: string; label: string }> = {
  blocks:           { color: T.cherry,     dash: '',     label: 'blocks' },
  blocked_by:       { color: T.cherry,     dash: '6 4',  label: 'blocked by' },
  relates_to:       { color: T.text2,      dash: '2 4',  label: 'relates to' },
  discovered_from:  { color: T.blueberry,  dash: '4 2',  label: 'discovered from' },
  validates:        { color: T.mintDark,   dash: '',     label: 'validates' },
  contradicts:      { color: T.cherryDark, dash: '6 2',  label: 'contradicts' },
  extends:          { color: T.crustDark,  dash: '',     label: 'extends' },
};

/** Node sizing — uses retrieval_count when non-zero, else token_estimate. */
export function beadSize(b: Bead): number {
  const r = b.retrieval_count ?? 0;
  if (r > 0) return r;
  return Math.max(1, Math.min(40, Math.round((b.token_estimate ?? 0) / 20)));
}
