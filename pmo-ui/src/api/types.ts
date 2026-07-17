export interface PmoCard {
  card_id: string;
  project_id: string;
  program: string;
  title: string;
  column: 'intake' | 'queued' | 'executing' | 'awaiting_human' | 'validating' | 'deployed' | 'review' | 'awaiting_review';
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

/** Wrapped response from POST /forge/plan — contains session_id for SSE progress tracking */
export interface ForgePlanWrappedResponse {
  session_id: string;
  plan: ForgePlanResponse;
}

/** Event emitted by GET /api/v1/pmo/forge/progress/{sessionId} */
export interface ForgeProgressEvent {
  stage: 'analyzing' | 'routing' | 'sizing' | 'generating' | 'validating' | 'complete';
  progress_pct: number;
  message: string;
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
  step_type?: 'planning' | 'developing' | 'testing' | 'reviewing' | 'consulting' | 'task' | 'automation';
  command?: string;
  interactive?: boolean;
  max_turns?: number;
}

export interface ForgePlanGate {
  gate_type: string;
  command: string;
  description: string;
  fail_on: string[];
  approval_required?: boolean;
}

export interface UpdatePlanResponse {
  saved: true;
  card_id: string;
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
  /** True when this plan was built with ManagerModePlanner post-processing
   * (Phase 7 "Turn PMO into the director console") -- gates whether the
   * `/pmo/manager/{card_id}/...` artifact API has anything to return. */
  manager_mode?: boolean;
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

// ---------------------------------------------------------------------------
// Execution control types
// ---------------------------------------------------------------------------

export interface ExecutionControlResponse {
  status: string;
  task_id: string;
  step_id?: string;
  message?: string;
}

// ---------------------------------------------------------------------------
// Agent registry types
// ---------------------------------------------------------------------------

export interface Agent {
  name: string;
  description: string;
  model: string;
  category: string;
  color: string;
  tools: string[];
  base_name: string;
  flavor: string | null;
}

export interface AgentsResponse {
  count: number;
  agents: Agent[];
}

// ---------------------------------------------------------------------------
// Policy preset types
// ---------------------------------------------------------------------------

export interface PolicyPreset {
  name: string;
  label: string;
  description: string;
}

export interface PoliciesResponse {
  presets: PolicyPreset[];
}

// ---------------------------------------------------------------------------
// Webhooks types
// ---------------------------------------------------------------------------

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  created_at?: string;
}

export interface WebhooksResponse {
  webhooks: Webhook[];
}

// ---------------------------------------------------------------------------
// Spec types (F0.1 — First-Class Spec Entity)
// ---------------------------------------------------------------------------

export type SpecState =
  | 'draft'
  | 'reviewed'
  | 'approved'
  | 'executing'
  | 'completed'
  | 'archived';

export interface SpecScore {
  clarity?: number;
  completeness?: number;
  feasibility?: number;
  testability?: number;
  [key: string]: number | undefined;
}

export interface Spec {
  spec_id: string;
  title: string;
  state: SpecState;
  task_type: string;
  author_id: string;
  template_id: string | null;
  content: string;
  linked_plan_ids: string[];
  score: SpecScore | null;
  created_at: string;
  updated_at: string;
}

export interface SpecListResponse {
  specs: Spec[];
  total: number;
}

export interface SpecApproveResponse {
  spec_id: string;
  state: SpecState;
  updated_at: string;
}

export interface SpecMarkReviewedResponse {
  spec_id: string;
  state: SpecState;
  updated_at: string;
}

export interface SpecArchiveResponse {
  spec_id: string;
  state: SpecState;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// H3 view types — scorecards, arch beads, playbooks, CRP, and bead graph
// ---------------------------------------------------------------------------

export interface DeveloperScorecard {
  user_id: string;
  window_days: number;
  tasks_completed: number;
  avg_cycle_time_minutes: number;
  gate_pass_rate: number;
  incidents_authored: number;
  incidents_resolved: number;
  knowledge_contributions: number;
}

export interface ArchBead {
  bead_id: string;
  bead_type: string;
  agent_name: string;
  content: string;
  affected_files: string[];
  status: string;
  created_at: string;
  tags: string[];
}

export interface ArchReviewBody {
  action: 'approve' | 'reject';
  reason?: string;
  reviewer?: string;
}

export interface ArchReviewResponse {
  bead_id: string;
  follow_up_bead_id: string;
  action: string;
}

export interface Playbook {
  slug: string;
  title: string;
  body: string;
}

export interface CRPRequestBody {
  title: string;
  scope: string[];
  rationale: string;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  suggested_agent: string;
}

export interface CRPResponse {
  crp_id: string;
  plan_summary: string;
  suggested_phases: string[];
  risk_level: string;
  submitted_at: string;
}

/** Lightweight bead representation used by BeadGraphView and BeadTimelineView. */
export interface BeadNode {
  bead_id: string;
  bead_type: string;
  agent_name: string;
  content: string;
  status: string;
  created_at: string;
  tags: string[];
}

export type HumanRole =
  | 'junior'
  | 'senior'
  | 'tech_lead'
  | 'architect'
  | 'eng_manager'
  | 'qa';

// ---------------------------------------------------------------------------
// Spec Queue types (007 Phase I — Spec Federation MVP)
// ---------------------------------------------------------------------------

export type SpecQueueStatus =
  | 'submitted'
  | 'enriched'
  | 'approved'
  | 'bounced'
  | 'fired';

export interface SpecQualityReport {
  score: number;
  missing: string[];
  notes: string[];
}

export interface SpecDraftEnrichment {
  risk_level: string;
  guardrail_preset: string;
  required_reviewers: string[];
  signals_found: string[];
  confidence: string;
  est_usd_low: number;
  est_usd_mid: number;
  est_usd_high: number;
  cost_confidence: string;
  breakdown: Array<{
    agent_name: string;
    model: string;
    est_steps: number;
    est_tokens: number;
    est_usd: number;
  }>;
  enriched_at: string;
  spec_quality: SpecQualityReport | null;
}

export interface SpecDraftReview {
  action: 'approved' | 'bounced';
  actor: string;
  feedback: string;
  reviewed_at: string;
}

export interface SpecDraft {
  id: string;
  title: string;
  body: string;
  source: 'manual' | 'github' | 'ado';
  source_ref: string;
  submitted_by: string;
  submitted_at: string;
  status: SpecQueueStatus;
  enrichment: SpecDraftEnrichment | null;
  review: SpecDraftReview | null;
  task_id: string | null;
  updated_at: string;
}

export interface SubmitSpecDraftBody {
  title: string;
  body?: string;
  source?: 'manual' | 'github' | 'ado';
  source_ref?: string;
}

export interface BounceSpecDraftBody {
  feedback: string;
}

export interface FireSpecDraftBody {
  project_id: string;
}

export interface ImportSpecDraftBody {
  source: 'github' | 'ado';
  ref: string;
  owner?: string;
  repo?: string;
}

export interface FireSpecDraftResponse {
  spec_id: string;
  task_id: string;
  status: 'fired';
}

// ---------------------------------------------------------------------------
// Manager-mode PMO API types (Phase 7 -- "Turn PMO into the director console")
// Backend: agent_baton/api/routes/pmo_manager.py + agent_baton/api/models/responses.py
// Domain shapes: agent_baton/models/manager.py
// ---------------------------------------------------------------------------

/** Common envelope for a single manager-mode artifact read. `revision`/
 * `published_at` are `null` when nothing has ever been published for this
 * task -- treat that as "artifacts exist but are unversioned", not an error. */
export interface ManagerArtifactEnvelope {
  task_id: string;
  revision: number | null;
  published_at: string | null;
}

export interface ManagerCharterResponse extends ManagerArtifactEnvelope {
  /** Rendered project-charter.md contents (Markdown -- no JSON sidecar exists). */
  markdown: string;
}

export interface Workstream {
  id: string;
  name: string;
  objective: string;
  likely_paths: string[];
  allowed_paths: string[];
  owner_role: string;
  dependencies: string[];
  deliverables: string[];
  risks: string[];
}

export interface ScopeMapData {
  task_id: string;
  workstreams: Workstream[];
  cross_cutting_concerns: string[];
  out_of_scope: string[];
  scope_expansion_policy: string;
}

export interface ManagerScopeMapResponse extends ManagerArtifactEnvelope {
  scope_map: ScopeMapData;
}

export interface ManagerWorkstreamPhaseLink {
  phase_id: number;
  phase_name: string;
  workstream: Workstream;
}

export interface ManagerWorkstreamsResponse extends ManagerArtifactEnvelope {
  links: ManagerWorkstreamPhaseLink[];
}

export interface TeamBlueprintRole {
  role: string;
  agent_name: string;
  mission: string;
  owns: string[];
  does_not_own: string[];
  required_knowledge_packs: string[];
  default_context_budget: number;
  expected_handoffs: string[];
  escalation_triggers: string[];
}

export interface TeamBlueprintData {
  task_id: string;
  team_name: string;
  mission: string;
  roles: TeamBlueprintRole[];
  workstream_assignments: Record<string, string>;
  collaboration_rules: string[];
  escalation_triggers: string[];
  phase_policies: Record<string, unknown>;
}

export interface ManagerTeamBlueprintResponse extends ManagerArtifactEnvelope {
  team_blueprint: TeamBlueprintData;
}

/** One role's card, as rendered Markdown (the canonical dispatch form). */
export interface ManagerRoleCard {
  role: string;
  markdown: string;
}

export interface ManagerRoleCardsResponse extends ManagerArtifactEnvelope {
  role_cards: ManagerRoleCard[];
}

export interface KnowledgePackReference {
  name: string;
  path: string;
  reason: string;
  confidence: string;
  status: string;
  token_estimate: number;
  documents: string[];
}

export interface MissingKnowledgePack {
  name: string;
  reason: string;
  proposed_sources: string[];
}

export interface KnowledgePlanData {
  task_id: string;
  selected_packs: KnowledgePackReference[];
  missing_packs: MissingKnowledgePack[];
  stale_packs: string[];
  per_role_packs: Record<string, string[]>;
  per_step_packs: Record<string, string[]>;
}

export interface ManagerKnowledgePlanResponse extends ManagerArtifactEnvelope {
  knowledge_plan: KnowledgePlanData;
}

/** One step's scope-contract listing entry. */
export interface ManagerScopeContractSummary {
  step_id: string;
  agent_name: string;
  workstream_id: string;
  allowed_paths: string[];
}

export interface ManagerScopeContractsResponse extends ManagerArtifactEnvelope {
  contracts: ManagerScopeContractSummary[];
}

export interface ScopeContractData {
  step_id: string;
  agent_name: string;
  workstream_id: string;
  mission: string;
  in_scope: string[];
  out_of_scope: string[];
  allowed_paths: string[];
  expected_artifacts: string[];
  definition_of_done: string[];
  escalation_triggers: string[];
}

export interface ManagerScopeContractResponse extends ManagerArtifactEnvelope {
  step_id: string;
  contract: ScopeContractData;
  markdown: string;
}

export interface ContextReference {
  path: string;
  kind: 'file' | 'doc' | 'handoff' | 'bead';
  reason: string;
  token_estimate: number;
}

/** Metadata-only view of a per-step context bundle (no full document bodies). */
export interface ManagerContextBundleSummary {
  step_id: string;
  agent_name: string;
  must_read_count: number;
  reference_only_count: number;
  knowledge_pack_count: number;
  token_budget: number;
  estimated_tokens: number;
  truncation_warnings: string[];
}

export interface ManagerContextBundlesResponse extends ManagerArtifactEnvelope {
  bundles: ManagerContextBundleSummary[];
}

export interface ContextBundleData {
  task_id: string;
  step_id: string;
  agent_name: string;
  scope_contract_path: string;
  must_read: ContextReference[];
  reference_only: ContextReference[];
  knowledge_packs: KnowledgePackReference[];
  prior_handoffs: string[];
  decisions: string[];
  constraints: string[];
  token_budget: number;
  estimated_tokens: number;
  truncation_warnings: string[];
}

export interface ManagerContextBundleResponse extends ManagerArtifactEnvelope {
  step_id: string;
  bundle: ContextBundleData;
}

export interface ManagerReportResponse extends ManagerArtifactEnvelope {
  /** manager-brief.md contents (always present post-save). */
  manager_brief: string;
  /** manager-report.md contents -- a retrospective, only present post-execution. */
  manager_report: string;
}

/** One entry from decision-log.jsonl (a typed ManagerDecision packet). */
export interface ManagerDecision {
  decision_id: string;
  decision_type: 'scope_expansion' | 'ambiguity' | 'knowledge_gap' | 'review_veto' | 'approval' | string;
  task_id: string;
  summary: string;
  context: string;
  options: string[];
  recommended_option: string;
  created_at: string;
  resolved_at: string | null;
  resolution: string | null;
  markdown: string;
}

export interface ManagerDecisionListResponse {
  task_id: string;
  count: number;
  decisions: ManagerDecision[];
}

export interface ManagerDecisionResolveBody {
  resolution: 'approve' | 'reject';
  additional_paths?: string[];
}

export interface ManagerDecisionResolveResponse {
  applied: boolean;
  resolution: string | null;
  step_id: string;
  decision_id: string;
  new_allowed_paths: string[];
  error: string | null;
}

export interface ManagerVersionResponse {
  task_id: string;
  published: boolean;
  revision: number;
  prior_revision: number;
  trigger: string;
  created_at: string;
  plan_fingerprint: string;
  phase_count: number;
  step_count: number;
  published_paths: string[];
}

export interface ManagerValidationResponse {
  task_id: string;
  published: boolean;
  valid: boolean;
  fingerprint_match: boolean;
  revision: number;
  current_plan_fingerprint: string;
  published_plan_fingerprint: string;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Generic execution decision inbox (APPROVAL / FEEDBACK / INTERACT actions)
// Backend: agent_baton/api/routes/pmo.py -- /pmo/execute/{card_id}/decisions
// ---------------------------------------------------------------------------

export interface ExecutionDecision {
  request_id: string;
  task_id: string;
  decision_type: string;
  summary: string;
  options: string[];
  deadline: string | null;
  context_files: string[];
  created_at: string;
  status: 'pending' | 'resolved' | 'expired' | string;
  context_file_contents?: Record<string, string> | null;
}

export interface ExecutionDecisionListResponse {
  count: number;
  decisions: ExecutionDecision[];
}

export interface ResolveExecutionDecisionBody {
  option: string;
  rationale?: string;
  resolved_by?: string;
}

export interface ResolveExecutionDecisionResponse {
  resolved: boolean;
  execution_resumed: boolean;
}

// ---------------------------------------------------------------------------
// Card execution detail -- GET /pmo/cards/{card_id}/execution
// ---------------------------------------------------------------------------

export interface ExecutionStepEvent {
  event_type: string;
  step_id: string;
  agent?: string | null;
  status?: string | null;
  timestamp: string;
  message?: string | null;
}

export interface ExecutionGoalOverlay {
  completion_condition: string | null;
  goal_status: string;
  amend_cycles_used: number;
  max_amend_cycles: number;
  checks_count: number;
  last_check_met: boolean | null;
}

export interface CardExecutionDetail {
  task_id: string;
  status: string;
  current_phase: string;
  steps: ExecutionStepEvent[];
  started_at: string;
  elapsed_seconds: number;
  turn_count: number;
  tokens_used_usd: number;
  goal: ExecutionGoalOverlay;
}
