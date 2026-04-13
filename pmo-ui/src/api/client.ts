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
} from './types';

const BASE = '/api/v1/pmo';

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
  forgePlan(body: ForgePlanBody): Promise<ForgePlanResponse> {
    return request('/forge/plan', { method: 'POST', body: JSON.stringify(body) });
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
    return request('/signals/batch/resolve', { method: 'POST', body: JSON.stringify({ ids }) });
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
};

// Re-export types for convenience
export type { PmoCard, PmoProject, ProgramHealth, PmoSignal, BoardResponse, PlanResponse, ForgePlanBody, ForgePlanResponse, ForgeApproveBody, ForgeApproveResponse, InterviewResponse, RegenerateBody, AdoSearchResponse, ExecuteCardBody, ExecuteCardResponse, ExternalItem, ExternalMapping, PendingGate, GateApproveBody, GateRejectBody, GateActionResponse };
