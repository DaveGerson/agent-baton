import type {
  BoardResponse,
  PmoCard,
  PmoProject,
  ProgramHealth,
  PmoSignal,
  PlanResponse,
  ForgeApproveBody,
  ForgeApproveResponse,
  ForgePlanBody,
  ForgePlanResponse,
  ForgePlanWrappedResponse,
  InterviewResponse,
  RegenerateBody,
  AdoSearchResponse,
  ExecuteCardBody,
  ExecuteCardResponse,
  ExternalItem,
  ExternalMapping,
  PendingGate,
  GateApproveBody,
  GateRejectBody,
  GateActionResponse,
  ConsolidationResult,
  MergeResponse,
  CreatePrResponse,
  ApprovalLogEntry,
  ApprovalLogResponse,
  RequestReviewBody,
  RequestReviewResponse,
  ExecutionControlResponse,
  UpdatePlanResponse,
  Agent,
  AgentsResponse,
  PolicyPreset,
  PoliciesResponse,
  Webhook,
  WebhooksResponse,
  Spec,
  SpecState,
  SpecScore,
  SpecListResponse,
  SpecApproveResponse,
  SpecMarkReviewedResponse,
  SpecArchiveResponse,
  DeveloperScorecard,
  ArchBead,
  ArchReviewBody,
  ArchReviewResponse,
  Playbook,
  CRPRequestBody,
  CRPResponse,
} from './types';
import { beadsApi, type BeadListParams, type BeadListResponse, type Bead } from './beads';

const BASE = '/api/v1/pmo';
const BASE_V1 = '/api/v1';


async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...init?.headers },
      ...init,
      signal: init?.signal ?? controller.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status}: ${text.slice(0, 500)}`);
    }
    return res.json() as Promise<T>;
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  // Board
  getBoard(): Promise<BoardResponse> {
    return request('/board');
  },
  getBoardByProgram(program: string): Promise<BoardResponse> {
    return request(`/board/${encodeURIComponent(program)}`);
  },

  // Projects
  getProjects(): Promise<PmoProject[]> {
    return request('/projects');
  },
  createProject(body: Omit<PmoProject, 'color' | 'description'> & { color?: string; description?: string }): Promise<PmoProject> {
    return request('/projects', { method: 'POST', body: JSON.stringify(body) });
  },

  // Health
  getHealth(): Promise<Record<string, ProgramHealth>> {
    return request('/health');
  },

  // Forge
  forgePlan(body: ForgePlanBody): Promise<ForgePlanWrappedResponse> {
    return request('/forge/plan', { method: 'POST', body: JSON.stringify(body) });
  },
  /** Returns the URL for the SSE progress stream for a given session. */
  forgeProgressUrl(sessionId: string): string {
    return `${BASE}/forge/progress/${encodeURIComponent(sessionId)}`;
  },
  forgeApprove(body: ForgeApproveBody): Promise<ForgeApproveResponse> {
    return request('/forge/approve', { method: 'POST', body: JSON.stringify(body) });
  },

  // Signals
  getSignals(): Promise<PmoSignal[]> {
    return request('/signals');
  },
  createSignal(body: Partial<PmoSignal>): Promise<PmoSignal> {
    return request('/signals', { method: 'POST', body: JSON.stringify(body) });
  },
  resolveSignal(id: string): Promise<PmoSignal> {
    return request(`/signals/${encodeURIComponent(id)}/resolve`, { method: 'POST' });
  },
  batchResolveSignals(ids: string[]): Promise<{ resolved: string[]; count: number }> {
    return request('/signals/batch/resolve', { method: 'POST', body: JSON.stringify({ signal_ids: ids }) });
  },
  // Forge interview & regeneration
  forgeInterview(body: { plan: ForgePlanResponse; feedback?: string }): Promise<InterviewResponse> {
    return request('/forge/interview', { method: 'POST', body: JSON.stringify(body) });
  },
  forgeRegenerate(body: RegenerateBody): Promise<ForgePlanResponse> {
    return request('/forge/regenerate', { method: 'POST', body: JSON.stringify(body) });
  },

  // Cards — response is a flat PmoCard with optional `plan` field (not nested { card, plan })
  getCardDetail(cardId: string): Promise<PmoCard & { plan: ForgePlanResponse | null }> {
    return request(`/cards/${encodeURIComponent(cardId)}`);
  },

  // Execution
  executeCard(cardId: string, body: ExecuteCardBody = {}): Promise<ExecuteCardResponse> {
    return request(`/execute/${encodeURIComponent(cardId)}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  pauseExecution(cardId: string): Promise<ExecutionControlResponse> {
    return request(`/execute/${encodeURIComponent(cardId)}/pause`, { method: 'POST' });
  },
  resumeExecution(cardId: string): Promise<ExecutionControlResponse> {
    return request(`/execute/${encodeURIComponent(cardId)}/resume`, { method: 'POST' });
  },
  cancelExecution(cardId: string): Promise<ExecutionControlResponse> {
    return request(`/execute/${encodeURIComponent(cardId)}/cancel`, { method: 'POST' });
  },
  retryStep(cardId: string, stepId: string): Promise<ExecutionControlResponse> {
    return request(`/execute/${encodeURIComponent(cardId)}/retry-step`, {
      method: 'POST',
      body: JSON.stringify({ step_id: stepId }),
    });
  },
  skipStep(cardId: string, stepId: string, reason: string): Promise<ExecutionControlResponse> {
    return request(`/execute/${encodeURIComponent(cardId)}/skip-step`, {
      method: 'POST',
      body: JSON.stringify({ step_id: stepId, reason }),
    });
  },

  // ADO search
  searchAdo(q: string): Promise<AdoSearchResponse> {
    return request(`/ado/search?q=${encodeURIComponent(q)}`);
  },

  // External items (adapter data surfaced in PMO dashboard)
  getExternalItems(source?: string, projectId?: string, status?: string): Promise<ExternalItem[]> {
    const params = new URLSearchParams();
    if (source)    params.set('source', source);
    if (projectId) params.set('project_id', projectId);
    if (status)    params.set('status', status);
    const qs = params.toString();
    return request(`/external-items${qs ? `?${qs}` : ''}`);
  },
  getExternalItemMappings(itemId: number): Promise<ExternalMapping[]> {
    return request(`/external-items/${itemId}/mappings`);
  },

  // Gate approval
  listPendingGates(): Promise<PendingGate[]> {
    return request('/gates/pending');
  },
  approveGate(taskId: string, body: GateApproveBody): Promise<GateActionResponse> {
    return request(`/gates/${encodeURIComponent(taskId)}/approve`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  rejectGate(taskId: string, body: GateRejectBody): Promise<GateActionResponse> {
    return request(`/gates/${encodeURIComponent(taskId)}/reject`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  // Changelist / consolidation
  getChangelist(cardId: string): Promise<ConsolidationResult> {
    return request(`/cards/${encodeURIComponent(cardId)}/changelist`);
  },
  mergeCard(cardId: string): Promise<MergeResponse> {
    return request(`/cards/${encodeURIComponent(cardId)}/merge`, { method: 'POST' });
  },
  createPr(cardId: string, params: { title: string; body?: string; base_branch?: string }): Promise<CreatePrResponse> {
    return request(`/cards/${encodeURIComponent(cardId)}/create-pr`, {
      method: 'POST',
      body: JSON.stringify(params),
    });
  },

  // Plan update
  updatePlan(cardId: string, plan: ForgePlanResponse): Promise<UpdatePlanResponse> {
    return request(`/cards/${encodeURIComponent(cardId)}/plan`, {
      method: 'POST',
      body: JSON.stringify({ plan }),
    });
  },

  // Review workflow
  requestReview(cardId: string, body: RequestReviewBody): Promise<RequestReviewResponse> {
    return request(`/cards/${encodeURIComponent(cardId)}/request-review`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
  getApprovalLog(cardId: string): Promise<ApprovalLogResponse> {
    return request(`/cards/${encodeURIComponent(cardId)}/approval-log`);
  },

  // Agent registry
  getAgents(): Promise<AgentsResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    return fetch(`${BASE_V1}/agents`, { signal: controller.signal })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<AgentsResponse>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  // Policy presets — no REST endpoint yet; returns hardcoded list
  getPolicies(): Promise<PoliciesResponse> {
    // Try a real endpoint first; fall back to a client-side constant if absent.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    return fetch(`${BASE_V1}/policies`, { signal: controller.signal })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<PoliciesResponse>;
      })
      .catch(() => {
        clearTimeout(timeout);
        // Offline fallback — names match the Python PolicyPreset registry.
        return {
          presets: [
            { name: 'standard_dev',   label: 'Standard Dev',       description: 'Default preset for everyday engineering work. Balanced guardrails.' },
            { name: 'data_analysis',  label: 'Data & Analytics',   description: 'Optimised for read-heavy exploration. Relaxed write-path restrictions.' },
            { name: 'infrastructure', label: 'Infrastructure',      description: 'Strict path guards on /deploy and /infra. Gate required at each phase.' },
            { name: 'regulated',      label: 'Regulated Domain',    description: 'Full audit trail. Requires SME + auditor review before gate passage.' },
            { name: 'security',       label: 'Security Review',     description: 'Mandatory security scan gate. Blocks any .env write. Opus-only steps.' },
          ] satisfies PoliciesResponse['presets'],
        } as PoliciesResponse;
      });
  },

  // Specs (F0.1) — /api/v1/specs
  listSpecs(params?: { state?: string; task_type?: string }): Promise<SpecListResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    const qs = params
      ? '?' + new URLSearchParams(
          Object.entries(params).filter(([, v]) => v !== undefined) as [string, string][]
        ).toString()
      : '';
    return fetch(`${BASE_V1}/specs${qs}`, { signal: controller.signal })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<SpecListResponse>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  getSpec(specId: string): Promise<Spec> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    return fetch(`${BASE_V1}/specs/${encodeURIComponent(specId)}`, { signal: controller.signal })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<Spec>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  approveSpec(specId: string): Promise<SpecApproveResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    return fetch(`${BASE_V1}/specs/${encodeURIComponent(specId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<SpecApproveResponse>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  markSpecReviewed(specId: string): Promise<SpecMarkReviewedResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    return fetch(`${BASE_V1}/specs/${encodeURIComponent(specId)}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<SpecMarkReviewedResponse>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  archiveSpec(specId: string): Promise<SpecArchiveResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    return fetch(`${BASE_V1}/specs/${encodeURIComponent(specId)}/archive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<SpecArchiveResponse>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  // Webhooks
  getWebhooks(): Promise<WebhooksResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5_000);
    return fetch(`${BASE_V1}/webhooks`, { signal: controller.signal })
      .then(res => {
        clearTimeout(timeout);
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<WebhooksResponse>;
      })
      .catch(err => { clearTimeout(timeout); throw err; });
  },

  // -------------------------------------------------------------------------
  // H3 endpoints — scorecards, arch review, playbooks, CRP
  // -------------------------------------------------------------------------

  getDeveloperScorecard(userId: string): Promise<DeveloperScorecard> {
    return request(`/scorecard/${encodeURIComponent(userId)}`);
  },

  listArchBeads(status: string = 'open'): Promise<ArchBead[]> {
    return request(`/arch-beads?status=${encodeURIComponent(status)}`);
  },

  reviewArchBead(beadId: string, body: ArchReviewBody): Promise<ArchReviewResponse> {
    return request(`/arch-beads/${encodeURIComponent(beadId)}/review`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  listPlaybooks(): Promise<Playbook[]> {
    return request('/playbooks');
  },

  submitCrp(body: CRPRequestBody): Promise<CRPResponse> {
    return request('/crp', { method: 'POST', body: JSON.stringify(body) });
  },

  // -------------------------------------------------------------------------
  // DX.6 — Beads (graph + timeline) — wraps the dedicated beadsApi module.
  // -------------------------------------------------------------------------

  /** List beads from the project's bead store with optional filters. */
  getBeads(params?: BeadListParams): Promise<BeadListResponse> {
    return beadsApi.list(params);
  },

  /** Fetch a single bead by ID. */
  getBead(beadId: string): Promise<Bead | null> {
    return beadsApi.get(beadId);
  },

  /**
   * HEAD-ping an endpoint path (relative to /api/v1/pmo).
   * Resolves true if the server responds with a non-5xx status,
   * false if the endpoint is absent or the request fails.
   */
  async checkEndpoint(path: string): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5_000);
      const res = await fetch(`${BASE}${path}`, {
        method: 'HEAD',
        signal: controller.signal,
      });
      clearTimeout(timeout);
      return res.status < 500;
    } catch {
      return false;
    }
  },
};

// Re-export types for convenience
export type { PmoCard, PmoProject, ProgramHealth, PmoSignal, BoardResponse, PlanResponse, ForgePlanBody, ForgePlanResponse, ForgePlanWrappedResponse, ForgeApproveBody, ForgeApproveResponse, InterviewResponse, RegenerateBody, AdoSearchResponse, ExecuteCardBody, ExecuteCardResponse, ExternalItem, ExternalMapping, PendingGate, GateApproveBody, GateRejectBody, GateActionResponse, ConsolidationResult, MergeResponse, CreatePrResponse, ApprovalLogEntry, ApprovalLogResponse, RequestReviewBody, RequestReviewResponse, ExecutionControlResponse, UpdatePlanResponse, Agent, AgentsResponse, PolicyPreset, PoliciesResponse, Webhook, WebhooksResponse, Spec, SpecState, SpecScore, SpecListResponse, SpecApproveResponse, SpecMarkReviewedResponse, SpecArchiveResponse, DeveloperScorecard, ArchBead, ArchReviewBody, ArchReviewResponse, Playbook, CRPRequestBody, CRPResponse };
