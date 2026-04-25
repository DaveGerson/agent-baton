// ===================================================================
// O1.6 NOC Agent Workforce API client
//
// Aggregate cross-project endpoints expected under /api/v1/workforce.
// These endpoints DO NOT EXIST YET on the backend (tracked in bd-1bf1).
// Each fetcher tries the live endpoint with a short timeout and falls
// back to deterministic fixture data on failure (404, ECONNREFUSED, etc.)
// so the UI stays reviewable without backend support.
//
// When the endpoints land, this module needs no changes — `_isLive`
// flips automatically the moment a real response arrives.
// ===================================================================

const BASE = '/api/v1/workforce';
const TIMEOUT_MS = 5_000;

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

export interface WorkforceKpis {
  active_executions: number;
  completed_24h: number;
  token_spend_24h_usd: number;
  open_warnings: number;
  generated_at: string; // ISO 8601
}

export interface AgentActivity {
  agent: string;
  completed: number;
  failed: number;
  running: number;
  total: number;
}

export interface ProjectActivity {
  project_id: string;
  project_name: string;
  activity_score: number; // 0..1
  step_count: number;
  error_count: number;
}

export type WorkforceEventType =
  | 'step_started'
  | 'step_completed'
  | 'gate_passed'
  | 'gate_failed'
  | 'override_fired'
  | 'escalation_opened';

export type WorkforceSeverity = 'info' | 'warn' | 'error';

export interface WorkforceEvent {
  event_id: string;
  ts: string;
  type: WorkforceEventType;
  agent?: string;
  project_id?: string;
  message: string;
  severity: WorkforceSeverity;
}

export interface WorkforceAlert {
  bead_id: string;
  project_id: string;
  type: 'warning' | 'error';
  content: string;
  created_at: string;
  ack: boolean;
}

export interface WorkforceSnapshot {
  kpis: WorkforceKpis;
  by_agent: AgentActivity[];
  by_project: ProjectActivity[];
  events: WorkforceEvent[];
  alerts: WorkforceAlert[];
  source: 'live' | 'fixture';
  fetched_at: string;
}

// -------------------------------------------------------------------
// Fetch helpers
// -------------------------------------------------------------------

async function tryFetch<T>(path: string): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE}${path}`, { signal: controller.signal });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// -------------------------------------------------------------------
// Public API
// -------------------------------------------------------------------

export async function fetchWorkforceSnapshot(): Promise<WorkforceSnapshot> {
  const [kpis, byAgent, byProject, events, alerts] = await Promise.all([
    tryFetch<WorkforceKpis>('/kpis'),
    tryFetch<AgentActivity[]>('/by-agent?window=24h'),
    tryFetch<ProjectActivity[]>('/by-project?window=1h'),
    tryFetch<WorkforceEvent[]>('/events?limit=30'),
    tryFetch<WorkforceAlert[]>('/alerts'),
  ]);

  // If KPIs are live, treat the snapshot as live (per-panel fallbacks
  // still apply individually).
  const isLive = kpis !== null;

  return {
    kpis: kpis ?? FIXTURE.kpis(),
    by_agent: byAgent ?? FIXTURE.byAgent(),
    by_project: byProject ?? FIXTURE.byProject(),
    events: events ?? FIXTURE.events(),
    alerts: alerts ?? FIXTURE.alerts(),
    source: isLive ? 'live' : 'fixture',
    fetched_at: new Date().toISOString(),
  };
}

export async function ackAlert(beadId: string): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE}/alerts/${encodeURIComponent(beadId)}/ack`, {
      method: 'POST',
      signal: controller.signal,
    });
    return res.ok;
  } catch {
    return false; // fixture mode — caller should optimistic-update
  } finally {
    clearTimeout(timer);
  }
}

// -------------------------------------------------------------------
// Fixture data (TODO bd-1bf1: remove once endpoints ship)
// -------------------------------------------------------------------

function isoMinutesAgo(m: number): string {
  return new Date(Date.now() - m * 60_000).toISOString();
}

export const FIXTURE = {
  kpis(): WorkforceKpis {
    return {
      active_executions: 7,
      completed_24h: 184,
      token_spend_24h_usd: 42.18,
      open_warnings: 5,
      generated_at: new Date().toISOString(),
    };
  },
  byAgent(): AgentActivity[] {
    return [
      { agent: 'backend-engineer',   completed: 38, failed: 2, running: 2, total: 42 },
      { agent: 'frontend-engineer',  completed: 24, failed: 1, running: 1, total: 26 },
      { agent: 'test-engineer',      completed: 31, failed: 4, running: 0, total: 35 },
      { agent: 'code-reviewer',      completed: 22, failed: 0, running: 1, total: 23 },
      { agent: 'architect',          completed: 12, failed: 0, running: 0, total: 12 },
      { agent: 'security-reviewer',  completed:  8, failed: 1, running: 0, total:  9 },
      { agent: 'auditor',            completed:  6, failed: 0, running: 1, total:  7 },
      { agent: 'data-engineer',      completed:  5, failed: 0, running: 0, total:  5 },
      { agent: 'devops-engineer',    completed:  4, failed: 1, running: 1, total:  6 },
      { agent: 'subject-matter-expert', completed: 3, failed: 0, running: 0, total: 3 },
      { agent: 'visualization-expert',  completed: 2, failed: 0, running: 0, total: 2 },
      { agent: 'plan-reviewer',      completed: 14, failed: 0, running: 1, total: 15 },
    ];
  },
  byProject(): ProjectActivity[] {
    return [
      { project_id: 'agent-baton',         project_name: 'Agent Baton',         activity_score: 0.92, step_count: 48, error_count: 2 },
      { project_id: 'pmo-ui',              project_name: 'PMO UI',              activity_score: 0.71, step_count: 22, error_count: 0 },
      { project_id: 'forge-headless',      project_name: 'Forge Headless',      activity_score: 0.45, step_count: 11, error_count: 1 },
      { project_id: 'central-db',          project_name: 'Central DB',          activity_score: 0.18, step_count:  4, error_count: 0 },
      { project_id: 'spec-archive',        project_name: 'Spec Archive',        activity_score: 0.05, step_count:  1, error_count: 0 },
      { project_id: 'audit-remediation',   project_name: 'Audit Remediation',   activity_score: 0.66, step_count: 19, error_count: 0 },
      { project_id: 'token-burn',          project_name: 'Token Burn',          activity_score: 0.34, step_count:  8, error_count: 0 },
      { project_id: 'g1-governance',       project_name: 'G1 Governance',       activity_score: 0.81, step_count: 29, error_count: 1 },
      { project_id: 'o16-noc',             project_name: 'O1.6 NOC',            activity_score: 0.55, step_count: 14, error_count: 0 },
      { project_id: 'sandbox',             project_name: 'Sandbox',             activity_score: 0.00, step_count:  0, error_count: 0 },
      { project_id: 'roadmap-strategic',   project_name: 'Strategic Roadmap',   activity_score: 0.27, step_count:  6, error_count: 0 },
      { project_id: 'pmo-ux',              project_name: 'PMO UX',              activity_score: 0.12, step_count:  3, error_count: 0 },
    ];
  },
  events(): WorkforceEvent[] {
    return [
      { event_id: 'e1',  ts: isoMinutesAgo(0.2),  type: 'step_started',     agent: 'backend-engineer',  project_id: 'agent-baton',       message: 'Step 4.2 — implement aggregate KPIs endpoint', severity: 'info' },
      { event_id: 'e2',  ts: isoMinutesAgo(0.6),  type: 'gate_passed',      agent: 'test-engineer',     project_id: 'pmo-ui',            message: 'Unit tests gate passed (47/47)',                severity: 'info' },
      { event_id: 'e3',  ts: isoMinutesAgo(1.1),  type: 'step_completed',   agent: 'code-reviewer',     project_id: 'agent-baton',       message: 'Reviewed PR #58',                                severity: 'info' },
      { event_id: 'e4',  ts: isoMinutesAgo(1.7),  type: 'gate_failed',      agent: 'security-reviewer', project_id: 'g1-governance',     message: 'Secrets scan flagged 1 finding in deps',         severity: 'error' },
      { event_id: 'e5',  ts: isoMinutesAgo(2.4),  type: 'step_started',     agent: 'frontend-engineer', project_id: 'o16-noc',           message: 'Step 1.1 — scaffold workforce view',             severity: 'info' },
      { event_id: 'e6',  ts: isoMinutesAgo(3.0),  type: 'override_fired',   agent: 'orchestrator',      project_id: 'forge-headless',    message: 'Override: cap_extension_human_only',             severity: 'warn' },
      { event_id: 'e7',  ts: isoMinutesAgo(3.8),  type: 'step_completed',   agent: 'architect',         project_id: 'agent-baton',       message: 'Drafted ADR-018',                                severity: 'info' },
      { event_id: 'e8',  ts: isoMinutesAgo(4.6),  type: 'escalation_opened',agent: 'orchestrator',      project_id: 'token-burn',        message: 'Escalated to human: budget exceeded',            severity: 'error' },
      { event_id: 'e9',  ts: isoMinutesAgo(5.1),  type: 'gate_passed',      agent: 'auditor',           project_id: 'audit-remediation', message: 'Compliance check OK',                            severity: 'info' },
      { event_id: 'e10', ts: isoMinutesAgo(5.9),  type: 'step_completed',   agent: 'data-engineer',     project_id: 'central-db',        message: 'Migration 0042 applied',                         severity: 'info' },
      { event_id: 'e11', ts: isoMinutesAgo(6.7),  type: 'step_started',     agent: 'plan-reviewer',     project_id: 'roadmap-strategic', message: 'Reviewing plan p-2026-04-25-1',                  severity: 'info' },
      { event_id: 'e12', ts: isoMinutesAgo(7.4),  type: 'step_completed',   agent: 'test-engineer',     project_id: 'agent-baton',       message: 'pytest -k engine OK (412 tests)',                severity: 'info' },
      { event_id: 'e13', ts: isoMinutesAgo(8.0),  type: 'gate_passed',      agent: 'code-reviewer',     project_id: 'pmo-ui',            message: 'Type-check + build OK',                          severity: 'info' },
      { event_id: 'e14', ts: isoMinutesAgo(9.3),  type: 'step_started',     agent: 'devops-engineer',   project_id: 'agent-baton',       message: 'Tagged release v0.4.1-rc',                       severity: 'info' },
      { event_id: 'e15', ts: isoMinutesAgo(10.5), type: 'gate_failed',      agent: 'test-engineer',     project_id: 'forge-headless',    message: 'Flaky test isolation failed (1/214)',            severity: 'warn' },
      { event_id: 'e16', ts: isoMinutesAgo(11.2), type: 'step_completed',   agent: 'frontend-engineer', project_id: 'pmo-ui',            message: 'Plan editor a11y pass landed',                   severity: 'info' },
      { event_id: 'e17', ts: isoMinutesAgo(12.0), type: 'step_started',     agent: 'subject-matter-expert', project_id: 'g1-governance', message: 'SME review of redaction overrides',              severity: 'info' },
      { event_id: 'e18', ts: isoMinutesAgo(13.5), type: 'step_completed',   agent: 'auditor',           project_id: 'audit-remediation', message: 'Audit trail snapshot exported',                  severity: 'info' },
      { event_id: 'e19', ts: isoMinutesAgo(14.4), type: 'gate_passed',      agent: 'security-reviewer', project_id: 'pmo-ui',            message: 'No new high-severity findings',                  severity: 'info' },
      { event_id: 'e20', ts: isoMinutesAgo(15.1), type: 'step_completed',   agent: 'visualization-expert', project_id: 'o16-noc',        message: 'Heat-grid color spec finalized',                 severity: 'info' },
      { event_id: 'e21', ts: isoMinutesAgo(16.0), type: 'step_started',     agent: 'backend-engineer',  project_id: 'central-db',        message: 'Step 2.0 — federated sync probe',                severity: 'info' },
      { event_id: 'e22', ts: isoMinutesAgo(17.7), type: 'step_completed',   agent: 'plan-reviewer',     project_id: 'agent-baton',       message: 'Plan reviewed and approved',                     severity: 'info' },
      { event_id: 'e23', ts: isoMinutesAgo(18.6), type: 'gate_passed',      agent: 'orchestrator',      project_id: 'pmo-ux',            message: 'INTERACT phase 3 closed',                        severity: 'info' },
      { event_id: 'e24', ts: isoMinutesAgo(19.5), type: 'step_completed',   agent: 'data-analyst',      project_id: 'token-burn',        message: 'Spend report rendered',                          severity: 'info' },
      { event_id: 'e25', ts: isoMinutesAgo(20.4), type: 'override_fired',   agent: 'orchestrator',      project_id: 'agent-baton',       message: 'Override: opus_step_for_security_reviewer',      severity: 'warn' },
      { event_id: 'e26', ts: isoMinutesAgo(21.3), type: 'step_started',     agent: 'frontend-engineer', project_id: 'pmo-ui',            message: 'Step 3.4 — kanban virtualization',               severity: 'info' },
      { event_id: 'e27', ts: isoMinutesAgo(22.2), type: 'step_completed',   agent: 'devops-engineer',   project_id: 'g1-governance',     message: 'Hooks redeployed',                               severity: 'info' },
      { event_id: 'e28', ts: isoMinutesAgo(23.1), type: 'gate_passed',      agent: 'code-reviewer',     project_id: 'audit-remediation', message: 'Diff review OK',                                 severity: 'info' },
      { event_id: 'e29', ts: isoMinutesAgo(24.0), type: 'step_completed',   agent: 'backend-engineer',  project_id: 'forge-headless',    message: 'Headless subprocess wrapper merged',             severity: 'info' },
      { event_id: 'e30', ts: isoMinutesAgo(25.0), type: 'gate_passed',      agent: 'test-engineer',     project_id: 'roadmap-strategic', message: 'Smoke suite OK',                                 severity: 'info' },
    ];
  },
  alerts(): WorkforceAlert[] {
    return [
      { bead_id: 'bd-1bf1', project_id: 'agent-baton',     type: 'warning', content: 'O1.6 NOC dashboard needs aggregate cross-project endpoints (see bead body)', created_at: isoMinutesAgo(2),    ack: false },
      { bead_id: 'bd-91c7', project_id: 'agent-baton',     type: 'warning', content: 'O1.6 strategic roadmap milestone in progress',                                created_at: isoMinutesAgo(48),   ack: false },
      { bead_id: 'bd-token-1', project_id: 'token-burn',   type: 'error',   content: 'Budget exceeded on token-burn project (115% of cap)',                         created_at: isoMinutesAgo(15),   ack: false },
      { bead_id: 'bd-sec-1', project_id: 'g1-governance',  type: 'error',   content: 'Secrets-scan finding in third-party dep (cve-pending triage)',                created_at: isoMinutesAgo(8),    ack: false },
      { bead_id: 'bd-flake-1', project_id: 'forge-headless', type: 'warning', content: 'Test isolation flake (1/214) — needs deterministic seed',                   created_at: isoMinutesAgo(35),   ack: false },
    ];
  },
};
