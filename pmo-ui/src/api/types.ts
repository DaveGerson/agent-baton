export interface PmoCard {
  card_id: string;
  project_id: string;
  program: string;
  title: string;
  column: 'queued' | 'executing' | 'awaiting_human' | 'validating' | 'deployed' | 'review' | 'awaiting_review';
  consolidation_result?: ConsolidationResult;
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
  external_id: string;
}

export interface PmoProject {
  project_id: string;
  name: string;
  path: string;
  program: string;
  color: string;
  description: string;
  registered_at: string;
  ado_project: string;
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
  source_project_id: string;
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
  plan: ForgePlanResponse;
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

// ---------------------------------------------------------------------------
// Forge-specific plan types (match raw MachinePlan.to_dict() output)
// ---------------------------------------------------------------------------

export interface ForgePlanStep {
  step_id: string;
  agent_name: string;
  task_description: string;
  model: string;
  depends_on: string[];
  deliverables: string[];
  allowed_paths: string[];
  blocked_paths: string[];
  context_files: string[];
}

export interface ForgePlanGate {
  gate_type: string;
  command: string;
  description: string;
  fail_on: string[];
}

export interface ForgePlanPhase {
  phase_id: number;
  name: string;
  steps: ForgePlanStep[];
  gate?: ForgePlanGate;
}

export interface ForgePlanResponse {
  task_id: string;
  task_summary: string;
  risk_level: string;
  budget_tier: string;
  execution_mode: string;
  git_strategy: string;
  phases: ForgePlanPhase[];
  shared_context: string;
  pattern_source: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Interview types
// ---------------------------------------------------------------------------

export interface InterviewQuestion {
  id: string;
  question: string;
  context: string;
  answer_type: 'choice' | 'text';
  choices?: string[];
}

export interface InterviewAnswer {
  question_id: string;
  answer: string;
}

export interface InterviewResponse {
  questions: InterviewQuestion[];
}

export interface RegenerateBody {
  project_id: string;
  description: string;
  task_type?: string;
  priority?: number;
  original_plan: ForgePlanResponse;
  answers: InterviewAnswer[];
}

// ---------------------------------------------------------------------------
// External items types
// ---------------------------------------------------------------------------

export interface ExternalItem {
  id: number;
  source_id: string;
  external_id: string;
  item_type: string;
  title: string;
  description: string;
  state: string;
  assigned_to: string;
  priority: string;
  tags: string[];
  url: string;
  updated_at: string;
  source_type: string;
}

export interface ExternalMapping {
  id: number;
  source_id: string;
  external_id: string;
  project_id: string;
  task_id: string;
  mapping_type: string;
  created_at: string;
  item?: ExternalItem;
}

// ---------------------------------------------------------------------------
// ADO types
// ---------------------------------------------------------------------------

export interface AdoWorkItem {
  id: string;
  title: string;
  type: string;
  program: string;
  owner: string;
  priority: string;
  description: string;
}

export interface AdoSearchResponse {
  items: AdoWorkItem[];
  message?: string;
}

// ---------------------------------------------------------------------------
// Execution launch types
// ---------------------------------------------------------------------------

export interface ExecuteCardBody {
  model?: string;
  dry_run?: boolean;
  max_steps?: number;
}

export interface ExecuteCardResponse {
  task_id: string;
  pid: number;
  status: 'launched';
  model: string;
  dry_run: boolean;
}

// ---------------------------------------------------------------------------
// Gate approval types
// ---------------------------------------------------------------------------

export interface PendingGate {
  task_id: string;
  project_id: string;
  phase_id: number;
  phase_name: string;
  approval_context: string;
  approval_options: string[];
  task_summary: string;
  current_phase_name: string;
}

export interface GateApproveBody {
  phase_id: number;
  notes?: string;
}

export interface GateRejectBody {
  phase_id: number;
  reason: string;
}

export interface GateActionResponse {
  task_id: string;
  phase_id: number;
  result: 'approve' | 'reject' | 'approve-with-feedback';
  recorded: boolean;
}

// ---------------------------------------------------------------------------
// Changelist / consolidation types
// ---------------------------------------------------------------------------

export interface FileAttribution {
  file_path: string;
  step_id: string;
  agent_name: string;
  insertions: number;
  deletions: number;
}

export interface ConsolidationResult {
  status: 'success' | 'partial' | 'conflict';
  rebased_commits: Array<{
    step_id: string;
    agent_name: string;
    original_hash: string;
    new_hash: string;
  }>;
  final_head: string;
  base_commit: string;
  files_changed: string[];
  total_insertions: number;
  total_deletions: number;
  attributions: FileAttribution[];
  conflict_files: string[];
  conflict_step_id: string;
  skipped_steps: string[];
  error: string;
}

export interface MergeResponse {
  merge_commit: string;
  cleaned_worktrees: string[];
}

export interface CreatePrResponse {
  pr_url: string;
  pr_number: number;
}

// ---------------------------------------------------------------------------
// Review / approval log types
// ---------------------------------------------------------------------------

export interface ApprovalLogEntry {
  log_id: string;
  task_id: string;
  phase_id: string;
  user_id: string;
  action: 'approve' | 'reject' | 'request_review' | 'feedback';
  notes: string;
  created_at: string;
}

export interface ApprovalLogResponse {
  entries: ApprovalLogEntry[];
}

export interface RequestReviewBody {
  reviewer_id?: string;
  notes: string;
}

export interface RequestReviewResponse {
  task_id: string;
  status: 'review_requested';
}
