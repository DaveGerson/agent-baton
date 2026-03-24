export interface PmoCard {
  card_id: string;
  project_id: string;
  program: string;
  title: string;
  column: 'queued' | 'planning' | 'executing' | 'awaiting_human' | 'validating' | 'deployed';
  risk_level: string;
  priority: number;
  agents: string[];
  steps_completed: number;
  steps_total: number;
  gates_passed: number;
  current_phase: string;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface PmoProject {
  project_id: string;
  name: string;
  path: string;
  program: string;
  color: string;
  description: string;
}

export interface ProgramHealth {
  program: string;
  total_plans: number;
  active: number;
  completed: number;
  blocked: number;
  failed: number;
  completion_pct: number;
}

export interface PmoSignal {
  signal_id: string;
  signal_type: string;
  title: string;
  description: string;
  severity: string;
  status: string;
  created_at: string;
  forge_task_id: string;
}

export interface BoardResponse {
  cards: PmoCard[];
  health: Record<string, ProgramHealth>;
}

export interface PlanPhase {
  phase_id: string;
  name: string;
  description: string;
  steps: PlanStep[];
}

export interface PlanStep {
  step_id: string;
  name: string;
  agent: string;
  description: string;
}

export interface PlanResponse {
  plan_id: string;
  task_summary: string;
  phases: PlanPhase[];
  project_id?: string;
  program?: string;
  task_type?: string;
  priority?: string;
}

export interface ForgeApproveBody {
  plan: PlanResponse;
  project_id: string;
}

export interface ForgeApproveResponse {
  saved: true;
  path: string;
}

export interface ForgePlanBody {
  description: string;
  program: string;
  project_id: string;
  task_type?: string;
  priority?: number;
}
