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
} from './types';

const BASE = '/api/v1/pmo';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
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
  signalToForge(id: string, projectId: string): Promise<PlanResponse> {
    return request(`/signals/${encodeURIComponent(id)}/forge`, {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId }),
    });
  },

  // Forge interview & regeneration
  forgeInterview(body: { plan: ForgePlanResponse; feedback?: string }): Promise<InterviewResponse> {
    return request('/forge/interview', { method: 'POST', body: JSON.stringify(body) });
  },
  forgeRegenerate(body: RegenerateBody): Promise<ForgePlanResponse> {
    return request('/forge/regenerate', { method: 'POST', body: JSON.stringify(body) });
  },

  // ADO search (placeholder)
  searchAdo(q: string): Promise<AdoSearchResponse> {
    return request(`/ado/search?q=${encodeURIComponent(q)}`);
  },
};

// Re-export types for convenience
export type { PmoCard, PmoProject, ProgramHealth, PmoSignal, BoardResponse, PlanResponse, ForgePlanBody, ForgePlanResponse, ForgeApproveBody, ForgeApproveResponse, InterviewResponse, RegenerateBody, AdoSearchResponse };
