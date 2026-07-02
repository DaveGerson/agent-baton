# Manager-Mode PMO Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement manager-mode PMO planning + execution (all 9 milestones of `docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md`) as a post-processor around the existing planner, per the approved design in `docs/internal/manager-mode-pmo-design.md`.

**Architecture:** `ManagerModePlanner` post-processes `IntelligentPlanner.create_plan()` output into sidecar PMO artifacts under `.claude/team-context/executions/<task_id>/`; `PhasePolicyApplier` is the only plan mutation (review-step injection); execution consumes artifacts via three narrow hooks (dispatch prompt kwargs, phase-completion funnel, completion report). One additive plan field: `MachinePlan.manager_mode: bool`.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, argparse CLI (cooperative parser pattern), pytest.

**Staffing:** Sonnet subagents for TDD + implementation. Fable review gate at end of each wave, then milestone commits merge to `worktree-manager-mode-pmo`. Opus escalation only if a Sonnet agent stalls twice on the same failure.

**Required reading for every subagent (in order):**
1. `docs/internal/manager-mode-pmo-design.md` — locked decisions + reconnaissance facts (file:line anchors).
2. `docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md` §16 (your milestone's required test cases are normative).
3. Root `CLAUDE.md` + the component `CLAUDE.md` for each directory touched.

**Rules for every task:**
- TDD: write the failing test, run it, watch it fail, implement minimally, run to green, commit. Never weaken an existing test.
- Test commands: `python -m pytest tests/<area>/<file>.py -x -q` for the task's tests; before committing a task, also run the closest existing suite for files you modified (e.g. `python -m pytest tests/engine/test_dispatcher*.py -q` when touching `dispatcher.py`).
- No live Claude calls anywhere in tests. `BATON_MANAGER_ENRICH` paths are tested only with the stub.
- Commit message prefix: `feat(manager): M<n> <what>` / `test(manager): M<n> <what>`. End every commit with the two trailer lines already used on this branch (`Co-Authored-By: Claude Fable 5 …`, `Claude-Session: …`).
- Do NOT modify: `_print_action()` in `cli/commands/execution/execute.py`, any `planning/stages/*` file, `core/govern/packs.py`, `templates/CLAUDE.md`.

---

## File structure (created/modified across all waves)

```
agent_baton/core/config/manager.py            NEW  ManagerConfig + section models + loader
agent_baton/models/manager.py                 NEW  PMO Pydantic models (round-trippable)
agent_baton/core/manager/__init__.py          NEW  package exports
agent_baton/core/manager/paths.py             NEW  ManagerArtifactPaths (sidecar conventions)
agent_baton/core/manager/artifacts.py         NEW  ManagerArtifacts container + sidecar writer
agent_baton/core/manager/charter.py           NEW  ProjectCharterBuilder (M2)
agent_baton/core/manager/scope.py             NEW  ScopeMapBuilder (M2)
agent_baton/core/manager/enrich.py            NEW  BATON_MANAGER_ENRICH hook, stub default (M2)
agent_baton/core/manager/team_blueprint.py    NEW  TeamBlueprintBuilder (M3)
agent_baton/core/manager/role_cards.py        NEW  role-card Markdown renderer (M3)
agent_baton/core/manager/knowledge_plan.py    NEW  KnowledgePlanBuilder (M5)
agent_baton/core/manager/context_bundles.py   NEW  ScopeContract + ContextBundle builders (M4)
agent_baton/core/manager/phase_policy.py      NEW  PhasePolicyApplier (M6)
agent_baton/core/manager/reports.py           NEW  ManagerReportBuilder (M7)
agent_baton/core/manager/decisions.py         NEW  DecisionPacketBuilder (M7)
agent_baton/core/manager/planner.py           NEW  ManagerModePlanner composition (W0 skeleton, W3 full)
agent_baton/core/engine/scope_expansion.py    NEW  SCOPE_EXPANSION: line parser (M9, models on gate_addition.py)
agent_baton/cli/commands/config_cmd.py        NEW  baton config init|validate|show (W3)
agent_baton/cli/commands/report_cmd.py        NEW  baton report [--json] (M7)
agent_baton/cli/commands/team_cmd.py          NEW  baton team status|show (M7)
agent_baton/models/execution.py               MOD  add MachinePlan.manager_mode field (W0)
agent_baton/cli/commands/execution/plan_cmd.py MOD --manager-mode flag + post-processor call (W0, W3)
agent_baton/core/orchestration/knowledge_registry.py MOD parse new manifest keys (M5)
agent_baton/models/knowledge.py               MOD  KnowledgePack new fields; fix pack.yaml docstring (M5)
agent_baton/cli/commands/knowledge/…          MOD  list/scan/show verbs + audit in doctor_cmd.py (M5)
agent_baton/cli/commands/bead_cmd.py          MOD  fix pack.yaml→knowledge.yaml latent bug (M5)
agent_baton/core/engine/dispatcher.py         MOD  2 new kwargs on build_delegation_prompt (M4)
agent_baton/core/engine/executor.py           MOD  3 hooks: dispatch lookup, post-phase, complete (M9)
agent_baton/cli/main.py                       MOD  _COMMAND_GROUPS entries for config/report/team (W3)
tests/manager/…                               NEW  test_manager_config, test_manager_models, test_artifact_paths,
                                                   test_project_charter, test_scope_map, test_team_blueprint,
                                                   test_knowledge_packs, test_context_bundles, test_phase_policy,
                                                   test_manager_reports, test_decision_packets
tests/engine/test_manager_context_prompt.py   NEW  (M4)
tests/cli/test_knowledge_cli.py               NEW  (M5)
tests/cli/test_report_cli.py                  NEW  (M7)
tests/cli/test_config_cli.py                  NEW  (W3)
tests/fixtures/medium_project_repo/           NEW  fixture repo (M8)
tests/e2e/test_manager_mode_planning.py       NEW  (M8)
tests/e2e/test_manager_mode_execution_dry_run.py NEW (M9)
docs/cli-reference.md, docs/architecture/package-layout.md,
docs/design-decisions.md, CLAUDE.md, GEMINI.md MOD  docs matrix (W4)
```

---

## Wave 0 — Contracts (M1). One Sonnet agent, sequential, works directly on `worktree-manager-mode-pmo`.

### Task 1: ManagerConfig models + loader

**Files:** Create `agent_baton/core/config/manager.py`, `tests/manager/__init__.py`, `tests/manager/test_manager_config.py`. Modify `agent_baton/core/config/__init__.py` (export).

- [ ] **Step 1: Write failing tests** — `tests/manager/test_manager_config.py` with exactly these cases (PRD M1 list):
  - `test_defaults_when_no_config_file` — `ManagerConfig.load(tmp_path)` returns defaults (`manager_mode.enabled_by_default is False`, `context.default_step_token_budget == 12000`, `policies.phase_completion.adversarial_review == "always"`).
  - `test_loads_claude_baton_yaml` — write spec §9.1 YAML to `tmp_path/.claude/baton.yaml`; assert parsed values (`team.max_agents_by_complexity["medium"] == 5`, `scoping.scope_expansion_policy == "queue_for_manager"`).
  - `test_claude_dir_takes_precedence_over_root` — both `tmp_path/.claude/baton.yaml` and `tmp_path/baton.yaml` present with different `context.default_step_token_budget`; `.claude/` wins.
  - `test_cli_overrides_beat_project_config` — `ManagerConfig.load(tmp_path, cli_overrides={"gates": {"gate_scope": "full"}})` beats file value.
  - `test_invalid_policy_value_raises` — `adversarial_review: sometimes` → `pytest.raises(ManagerConfigError)` with message containing the bad value and the valid options.
  - `test_unknown_top_level_key_warns_not_crashes` — YAML with `frobnicate: 1` loads; `"frobnicate"` mentioned in `config.warnings`; ProjectConfig keys (`default_agents`, `default_gates`, `default_risk_level`, `auto_route_rules`, `excluded_paths`, `default_isolation`) are silently ignored (no warning).
  - `test_enabled_by_default_flag` — `manager_mode.enabled_by_default: true` → `config.manager_mode.enabled_by_default is True`.
  - `test_round_trip` — `ManagerConfig.from_dict(cfg.to_dict()) == cfg`.
- [ ] **Step 2:** Run `python -m pytest tests/manager/test_manager_config.py -q` — expect ImportError/failures.
- [ ] **Step 3: Implement** `agent_baton/core/config/manager.py`. Contract (implement exactly — later waves import this):

```python
"""Manager-mode (PMO) configuration — spec: docs/internal/manager-mode-pmo-design.md."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Literal
import logging, yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)
CONFIG_BASENAME = "baton.yaml"
Size = Literal["light", "medium", "heavy"]

class ManagerConfigError(ValueError):
    """Raised for unparseable YAML or invalid nested values. Message must name the offending key/value and valid options."""

class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore")  # unknown nested KEYS ignored; invalid VALUES fail via Literal

class ManagerModeConfig(_Section):
    enabled_by_default: bool = False
    project_size_default: Size = "medium"
    manager_decision_threshold: Size = "medium"
    assumptions_policy: Literal["record_and_continue", "ask_always"] = "record_and_continue"
    ambiguity_policy: Literal["ask_when_high_impact", "always_ask", "record_and_continue"] = "ask_when_high_impact"

class TeamConfig(_Section):
    max_agents_by_complexity: dict[str, int] = Field(default_factory=lambda: {"light": 2, "medium": 5, "heavy": 8})
    require_role_cards: bool = True
    require_workstream_owners: bool = True
    prefer_specialists_over_generalists: bool = True
    allow_talent_builder: bool = True
    default_roles: list[str] = Field(default_factory=lambda: ["architect", "backend-engineer", "test-engineer"])

class ScopingConfig(_Section):
    require_scope_contracts: bool = True
    require_allowed_paths: bool = True
    allow_cross_scope_edits: Literal["manager_approval", "allow", "block"] = "manager_approval"
    scope_expansion_policy: Literal["allow_with_note", "queue_for_manager", "block"] = "queue_for_manager"
    out_of_scope_policy: Literal["block_or_escalate", "warn"] = "block_or_escalate"

class ContextConfig(_Section):
    default_step_token_budget: int = 12000
    max_knowledge_docs_per_step: int = 6
    include_prior_phase_handoff: bool = True
    include_full_prior_outputs: bool = False
    summarize_prior_outputs: bool = True
    dedupe_knowledge_across_session: bool = True
    context_bundle_format: Literal["json"] = "json"

class KnowledgePackConfig(_Section):
    discovery_paths: list[str] = Field(default_factory=lambda: [".claude/knowledge", "docs", "."])
    default_packs: list[str] = Field(default_factory=lambda: ["repo-architecture", "coding-conventions", "testing-strategy"])
    required_for_code_steps: list[str] = Field(default_factory=lambda: ["coding-conventions", "testing-strategy"])
    stale_after_days: int = 90
    missing_pack_policy: Literal["propose", "warn", "ignore"] = "propose"

class PhaseCompletionPolicy(_Section):
    adversarial_review: Literal["always", "risk_based", "off"] = "always"
    handoff_required: bool = True
    gates: Literal["project_configured", "focused", "full", "smoke", "off"] = "project_configured"

class ProjectCompletionPolicy(_Section):
    adversarial_review: Literal["always", "off"] = "always"
    manager_report: Literal["required", "optional"] = "required"
    retrospective: Literal["required", "optional"] = "required"

class ReviewAgentsConfig(_Section):
    adversarial_review: str = "code-reviewer"
    project_review: str = "auditor"

class PoliciesConfig(_Section):
    phase_completion: PhaseCompletionPolicy = Field(default_factory=PhaseCompletionPolicy)
    project_completion: ProjectCompletionPolicy = Field(default_factory=ProjectCompletionPolicy)
    review_agents: ReviewAgentsConfig = Field(default_factory=ReviewAgentsConfig)

class GatesConfig(_Section):
    mode: Literal["project_configured", "focused", "full", "smoke", "off"] = "project_configured"
    gate_scope: Literal["focused", "full", "smoke"] = "focused"
    allow_smoke_fallback: bool = True
    missing_gate_policy: Literal["warn_and_request_manager_decision", "warn", "fail"] = "warn_and_request_manager_decision"

class ReportingConfig(_Section):
    write_manager_brief: bool = True
    write_manager_report: bool = True
    decision_log: bool = True
    include_raw_logs_by_default: bool = False

_KNOWN_SECTIONS = {"version", "manager_mode", "team", "scoping", "context", "knowledge_packs", "policies", "gates", "reporting"}
_PROJECT_CONFIG_KEYS = {"default_agents", "default_gates", "default_risk_level", "auto_route_rules", "excluded_paths", "default_isolation"}

class ManagerConfig(_Section):
    version: int = 1
    manager_mode: ManagerModeConfig = Field(default_factory=ManagerModeConfig)
    team: TeamConfig = Field(default_factory=TeamConfig)
    scoping: ScopingConfig = Field(default_factory=ScopingConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    knowledge_packs: KnowledgePackConfig = Field(default_factory=KnowledgePackConfig)
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    source_path: Path | None = Field(default=None, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)

    def to_dict(self) -> dict[str, Any]: return self.model_dump(mode="json")
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManagerConfig": return cls._validated(data)

    @classmethod
    def _validated(cls, raw: dict[str, Any], *, source: Path | None = None) -> "ManagerConfig":
        """Split known/unknown top-level keys (warn on unknown non-ProjectConfig keys); wrap ValidationError in ManagerConfigError naming key, value, and valid options."""
    @classmethod
    def from_yaml(cls, path: Path) -> "ManagerConfig":
        """yaml.safe_load; parse error or non-dict root → ManagerConfigError (fail early, unlike ProjectConfig's best-effort)."""
    @classmethod
    def find_config_file(cls, start_dir: Path | None = None) -> Path | None:
        """Walk UP from start_dir (default cwd): at each level check <dir>/.claude/baton.yaml then <dir>/baton.yaml; first hit wins. Reuses ProjectConfig's walk shape (project_config.py)."""
    @classmethod
    def load(cls, start_dir: Path | None = None, *, cli_overrides: dict[str, Any] | None = None) -> "ManagerConfig":
        """defaults < ~/.baton/config.yaml (if exists) < project file (find_config_file) < cli_overrides. Deep-merge dicts key-wise, then _validated once on the merged dict."""
```

- [ ] **Step 4:** Run to green: `python -m pytest tests/manager/test_manager_config.py -q`.
- [ ] **Step 5:** Commit: `feat(manager): M1 ManagerConfig models and loader`.

### Task 2: PMO domain models

**Files:** Create `agent_baton/models/manager.py`, `tests/manager/test_manager_models.py`.

- [ ] **Step 1: Failing tests** — for each model below: construct → `to_dict()` → `json.dumps` → `from_dict` → equality. Plus `test_charter_requires_task_id_and_objective` (ValidationError when missing).
- [ ] **Step 2:** Run, watch fail.
- [ ] **Step 3: Implement.** Pydantic v2, base `ManagerModel` with `model_config = ConfigDict(extra="ignore")` and `to_dict`/`from_dict` like Task 1. Models + fields (spec §10, exact — all list fields `Field(default_factory=list)`, optional strings default `""`):
  - `ProjectCharter`: task_id, title, objective, background, in_scope, out_of_scope, assumptions, constraints, risks, manager_decision_points, success_criteria, likely_repo_areas.
  - `Workstream`: id, name, objective, likely_paths, allowed_paths, owner_role, dependencies, deliverables, risks.
  - `ScopeMap`: task_id, workstreams: list[Workstream], cross_cutting_concerns, out_of_scope, scope_expansion_policy: str = "queue_for_manager".
  - `RoleCard`: role, agent_name, mission, owns, does_not_own, required_knowledge_packs, default_context_budget: int = 12000, expected_handoffs, escalation_triggers.
  - `TeamBlueprint`: task_id, team_name, mission, roles: list[RoleCard], workstream_assignments: dict[str, str], collaboration_rules, escalation_triggers, phase_policies: dict[str, Any].
  - `ScopeContract`: step_id, agent_name, workstream_id, mission, in_scope, out_of_scope, allowed_paths, expected_artifacts, definition_of_done, escalation_triggers.
  - `ContextReference`: path, kind: Literal["file","doc","handoff","bead"] = "file", reason: str = "", token_estimate: int = 0.
  - `KnowledgePackReference`: name, path: str = "", reason: str = "", confidence: str = "medium", status: str = "active", token_estimate: int = 0, documents: list[str].
  - `MissingKnowledgePack`: name, reason: str = "", proposed_sources: list[str].
  - `ContextBundle`: task_id, step_id, agent_name, scope_contract_path: str = "", must_read: list[ContextReference], reference_only: list[ContextReference], knowledge_packs: list[KnowledgePackReference], prior_handoffs: list[str], decisions: list[str], constraints: list[str], token_budget: int = 12000, estimated_tokens: int = 0, truncation_warnings: list[str].
  - `KnowledgePlan`: task_id, selected_packs: list[KnowledgePackReference], missing_packs: list[MissingKnowledgePack], stale_packs: list[str], per_role_packs: dict[str, list[str]], per_step_packs: dict[str, list[str]].
  - `ManagerDecision`: decision_id, task_id, decision_type: Literal["scope_expansion","ambiguity","knowledge_gap","review_veto","approval"], summary, context: str = "", options: list[str], recommended_option: str = "", created_at: str = "", resolved_at: str | None = None, resolution: str | None = None.
- [ ] **Step 4:** Green. **Step 5:** Commit: `feat(manager): M1 PMO domain models`.

### Task 3: Artifact paths + writer

**Files:** Create `agent_baton/core/manager/__init__.py`, `agent_baton/core/manager/paths.py`, `agent_baton/core/manager/artifacts.py`, `tests/manager/test_artifact_paths.py`.

- [ ] **Step 1: Failing tests** — `test_paths_layout` (every property matches the design-doc tree, rooted at `<team_context_dir>/executions/<task_id>/`), `test_step_id_sanitized` (`step_id="2/1"` → filename `2_1`), `test_write_json_and_md_create_parents`, `test_decision_log_appends_jsonl`.
- [ ] **Step 3: Implement.**

```python
# paths.py — single source of truth for sidecar locations. Never hardcode these paths elsewhere.
class ManagerArtifactPaths:
    def __init__(self, team_context_dir: Path, task_id: str): self.root = Path(team_context_dir) / "executions" / task_id
    # properties: charter (project-charter.md), scope_map (scope-map.json), team_blueprint (team-blueprint.json),
    # role_cards_dir, knowledge_plan (knowledge-plan.json), manager_brief (manager-brief.md),
    # manager_report (manager-report.md), scope_contracts_dir, context_bundles_dir, handoffs_dir,
    # decisions_dir, decision_log (decision-log.jsonl)
    # methods: role_card(role)->role-cards/<role>.md ; scope_contract(step_id, ext="md"|"json") ;
    # context_bundle(step_id)->context-bundles/<sid>.json ; phase_handoff(n)->handoffs/phase-<n>-handoff.md ;
    # decision(decision_id)->decisions/<id>.md ; static _sanitize(s): re.sub(r"[^A-Za-z0-9._-]", "_", s)

# artifacts.py
class ManagerArtifacts(BaseModel):  # container passed between builders; extra="ignore"
    charter: ProjectCharter | None = None
    scope_map: ScopeMap | None = None
    blueprint: TeamBlueprint | None = None
    role_cards_md: dict[str, str] = Field(default_factory=dict)      # role -> rendered markdown
    knowledge_plan: KnowledgePlan | None = None
    scope_contracts: dict[str, ScopeContract] = Field(default_factory=dict)   # step_id -> contract
    scope_contracts_md: dict[str, str] = Field(default_factory=dict)
    context_bundles: dict[str, ContextBundle] = Field(default_factory=dict)
    brief_md: str = ""
    warnings: list[str] = Field(default_factory=list)

def write_json(path: Path, model) -> None      # mkdir parents, model.to_dict() → json.dumps(indent=2), utf-8
def write_text(path: Path, text: str) -> None  # mkdir parents, utf-8
def append_decision_log(paths: ManagerArtifactPaths, decision: ManagerDecision) -> None  # one JSON per line
def write_all(paths: ManagerArtifactPaths, artifacts: ManagerArtifacts) -> list[Path]
    # writes every non-None/non-empty artifact to its conventional location; returns written paths
```

- [ ] **Step 4:** Green. **Step 5:** Commit: `feat(manager): M1 artifact path conventions and sidecar writer`.

### Task 4: `--manager-mode` flag + plan field + planner skeleton

**Files:** Modify `agent_baton/models/execution.py` (MachinePlan), `agent_baton/cli/commands/execution/plan_cmd.py`. Create `agent_baton/core/manager/planner.py`, `tests/manager/test_manager_mode_flag.py`.

- [ ] **Step 1: Failing tests** — `test_machine_plan_manager_mode_round_trips` (`MachinePlan.from_dict(plan.to_dict()).manager_mode is True` — remember `to_dict` is a hand-written allow-list: add the key there AND in `from_dict`); `test_plan_cmd_flag_sets_manager_mode` (invoke plan handler with `--manager-mode --dry-run` on a stub planner, assert plan.manager_mode); `test_enabled_by_default_config_turns_on_manager_mode`; `test_non_manager_plan_unchanged` (no flag + no config → `manager_mode is False`, no `core.manager` import side effects, plan.to_dict() has `manager_mode: False` only as the single delta).
- [ ] **Step 3: Implement.** `MachinePlan.manager_mode: bool = False` (+ serializers + `to_markdown` line `**Manager mode:** yes` only when true). `plan_cmd.py`: add `--manager-mode` store_true; after plan creation: `manager_requested = args.manager_mode or ManagerConfig.load(...).manager_mode.enabled_by_default`; when true set `plan.manager_mode = True` and call `ManagerModePlanner(config, project_root=…, team_context_dir=ctx_dir).build_and_write(plan, task_summary)`. Also record whether `--gate-scope` was explicitly passed (`args_explicit_gate_scope = "--gate-scope" in argv` pattern or argparse default sentinel `None`) and pass it through — M6 needs it. `planner.py` skeleton:

```python
class ManagerModePlanner:
    """Post-processor. Wave 0: builds nothing yet — writes nothing, returns empty ManagerArtifacts.
    Wave 3 fills composition in THIS ORDER (do not reorder):
    charter → scope map → blueprint+role cards → knowledge plan → PhasePolicyApplier.apply (mutates plan)
    → scope contracts + context bundles (over the FINAL step list, so injected review steps get bundles)
    → manager brief → write_all."""
    def __init__(self, config: ManagerConfig, *, project_root: Path, team_context_dir: Path): ...
    def build(self, plan: "MachinePlan", task_summary: str) -> ManagerArtifacts: ...
    def build_and_write(self, plan, task_summary) -> ManagerArtifacts:  # build + write_all when --save; skip writes on dry-run
```

- [ ] **Step 4:** Green + `python -m pytest tests/planning -q -x` (existing planner suite must stay green). **Step 5:** Commit: `feat(manager): M1 --manager-mode flag, plan field, post-processor skeleton`.

**Wave 0 exit:** Fable review of the four commits → fixes → proceed.

---

## Wave 1 — Parallel builders (M2, M3, M5). Three Sonnet agents in isolated worktrees branched from Wave-0 HEAD.

Each agent: confirm `git rev-parse HEAD` matches the SHA in your dispatch prompt and `git worktree list` shows your assigned worktree BEFORE editing. Only touch your listed files (tests/manager/* filenames are disjoint by design).

### Task 5 (Agent A): ProjectCharterBuilder + ScopeMapBuilder + enrich hook (M2)

**Files:** Create `agent_baton/core/manager/charter.py`, `scope.py`, `enrich.py`, `tests/manager/test_project_charter.py`, `tests/manager/test_scope_map.py`.

- [ ] **Step 1: Failing tests** (PRD M2 list, as): `test_medium_project_charter_nonempty` (objective/in_scope/out_of_scope/assumptions/success_criteria all non-empty), `test_multipart_task_creates_two_plus_workstreams`, `test_workstream_has_owner_deliverables_paths_risks`, `test_ambiguous_task_records_assumptions` (summary `"improve things"` → ≥1 assumption, no invented paths), `test_high_impact_ambiguity_creates_decision_point` (ambiguity + `ambiguity_policy=ask_when_high_impact` → `charter.manager_decision_points` non-empty), `test_charter_markdown_renders_all_sections`, `test_scope_map_json_round_trip`, `test_enrich_stub_is_noop_and_default`. Build inputs from a hand-constructed `MachinePlan` (2 phases × 2 steps, agents `backend-engineer`/`test-engineer`, `detected_stack={"language": "python"}`) — no planner invocation needed.
- [ ] **Step 3: Implement** deterministic rules:
  - `ProjectCharterBuilder(config).build(plan, task_summary, project_root) -> ProjectCharter`. objective = task_summary stripped/sentence-cased. background = template over `plan.task_type`, `plan.complexity`, `plan.detected_stack`. in_scope = phase names + union of step deliverables. out_of_scope = `["Repo areas outside the scope map", "Unrelated refactors"]` + config-driven extras. likely_repo_areas = step `allowed_paths`∪`context_files` first-segments; else top-level dirs of `project_root` whose names appear as whole words in the summary; else empty + assumption recorded. Ambiguity heuristic (document in docstring): ambiguous if `len(summary.split()) < 8` or no likely_repo_areas found; high-impact if additionally `plan.complexity in ("medium","heavy")` or `plan.risk_level` ≥ medium → append to `manager_decision_points` per `ambiguity_policy`. risks = `plan.risk_level` narrative + `plan.foresight_insights`. success_criteria = gate commands + "all workstream deliverables produced". Markdown renderer `charter_to_markdown(charter)` with `## Objective/Background/In Scope/Out of Scope/Assumptions/Constraints/Risks/Manager Decision Points/Success Criteria/Likely Repo Areas`.
  - `ScopeMapBuilder(config).build(charter, plan) -> ScopeMap`: one `Workstream` per phase (`id=f"ws-{i}"`); light complexity + single phase → one workstream. owner_role = modal `agent_name` in phase steps. dependencies = `ws-<j>` for any cross-phase `depends_on` edge (fallback: previous phase). allowed_paths = union step allowed_paths else charter.likely_repo_areas. `scope_expansion_policy` from config.
  - `enrich.py`: `maybe_enrich_charter(charter, task_summary) -> ProjectCharter` — reads `BATON_MANAGER_ENRICH` (`off` default/unset → return input unchanged). For haiku/sonnet/opus: follow the client pattern in the goal evaluator (`BATON_GOAL_EVALUATOR` implementation, grep it) — polish objective/background/assumptions only, never invent paths; any exception → log debug, return input. Tests only assert the off/stub path.
- [ ] **Step 4:** Green. **Step 5:** Commit: `feat(manager): M2 project charter and scope map builders`.

### Task 6 (Agent B): TeamBlueprintBuilder + role cards (M3)

**Files:** Create `agent_baton/core/manager/team_blueprint.py`, `role_cards.py`, `tests/manager/test_team_blueprint.py`.

- [ ] **Step 1: Failing tests** (PRD M3): `test_blueprint_written_fields_complete`, `test_every_workstream_has_owner`, `test_every_role_has_role_card`, `test_role_card_required_sections` (owns/does_not_own/required_knowledge_packs/escalation_triggers non-empty), `test_prefer_specialists_avoids_single_broad_role` (2-workstream medium plan whose steps all name `claude` → blueprint assigns ≥2 distinct owner roles via `planning/rules/phase_roles` mapping), `test_adversarial_always_adds_review_role` (blueprint.roles includes config `review_agents.adversarial_review`), `test_adversarial_off_no_review_role`, `test_blueprint_round_trip`.
- [ ] **Step 3: Implement.** `TeamBlueprintBuilder(config).build(scope_map, plan) -> tuple[TeamBlueprint, dict[str, RoleCard]]`. Roles = unique step `agent_name`s → one `RoleCard` each: mission = "Own <owned workstream names>"; owns = owned workstreams' deliverables; does_not_own = other workstreams' names + `["product requirements", "final adversarial review", "unrelated refactors"]`; required_knowledge_packs = config `required_for_code_steps` for implementation roles (step_type developing/testing) else `[]`; default_context_budget = config `context.default_step_token_budget`; expected_handoffs = "handoff to <next dependent workstream owner>". Specialist rule: if `prefer_specialists_over_generalists` and one role owns >1 workstream and plan complexity ≠ light → reassign owners via `agent_baton/core/engine/planning/rules/phase_roles.py` name→role mapping (read that file first; use its public mapping). Review role: when `policies.phase_completion.adversarial_review != "off"` or `project_completion.adversarial_review == "always"` → append RoleCard(role=config.policies.review_agents.adversarial_review, mission="Adversarial phase review", owns=["phase review verdicts"], does_not_own=["implementation"]). `workstream_assignments = {ws.id: owner_role}`. collaboration_rules/escalation_triggers = fixed template lists + config-driven entries. `role_cards.py`: `render_role_card(card) -> str` following spec §14.2 template exactly (`# Role Card: <role>` + `## Mission/Owns/Does Not Own/Required Knowledge Packs/Context Budget/Escalation Triggers/Handoff Requirements`).
- [ ] **Step 4:** Green. **Step 5:** Commit: `feat(manager): M3 team blueprint and role cards`.

### Task 7 (Agent C): knowledge.yaml extension + KnowledgePlanBuilder + CLI verbs (M5)

**Files:** Modify `agent_baton/models/knowledge.py` (KnowledgePack fields + fix `pack.yaml` docstring at ~L76), `agent_baton/core/orchestration/knowledge_registry.py` (`_load_pack`), `agent_baton/cli/commands/knowledge/doctor_cmd.py` (audit checks), `agent_baton/cli/commands/bead_cmd.py` (~L884: write `knowledge.yaml` not `pack.yaml`). Create `agent_baton/core/manager/knowledge_plan.py`, `agent_baton/cli/commands/knowledge/pack_cmds.py` (list/scan/show), `tests/manager/test_knowledge_packs.py`, `tests/cli/test_knowledge_cli.py`.

- [ ] **Step 1: Failing tests** (PRD M5): `test_extended_manifest_parses` (knowledge.yaml with status/confidence/source_files/last_reviewed/stale_after_days → fields on KnowledgePack; absent → defaults active/medium/[]/None/None), `test_invalid_status_fails_audit` (status `bogus` → doctor/audit error, registry still loads pack degraded), `test_scan_discovers_packs_and_docs` (fixture tree with 1 pack + README.md + pyproject.toml → knowledge-scan.json lists them), `test_missing_required_pack_in_plan` (config requires `coding-conventions`, registry lacks it → `knowledge_plan.missing_packs` entry with reason `"config: required_for_code_steps"`), `test_stale_pack_flagged` (last_reviewed 200 days ago, stale_after_days 90 → in stale_packs; freeze time via monkeypatched `_today` hook, no real clock in assertions), `test_role_pack_attaches_to_role` (`target_agents: [test-engineer]` → in `per_role_packs["test-engineer"]`), `test_required_for_code_steps_attach` (implementation step → pack in `per_step_packs[step_id]`), `test_audit_reports_missing_source_file`, `test_propose_writes_draft_from_repeated_gaps` (≥2 KnowledgeGapRecord fixtures with same gap → proposal .md under `.claude/team-context/knowledge-proposals/`), CLI: `test_knowledge_list_shows_status`, `test_knowledge_show_pack`, `test_knowledge_scan_writes_json`.
- [ ] **Step 3: Implement.** Registry: parse new optional keys in `_load_pack`, keep graceful degradation. Resolver behavior change (minimal): in `KnowledgeResolver._make_attachment` skip packs with `status == "deprecated"` for non-explicit layers (explicit always allowed). `KnowledgePlanBuilder(config, registry).build(plan, blueprint_roles: list[str]) -> KnowledgePlan`: selected = union of step.knowledge attachments (as KnowledgePackReference with `reason` from attachment.source) + config default_packs present in registry (reason `"config: default_packs"`); missing = default+required names absent; stale = pack-level `last_reviewed`+`stale_after_days` (fallback config value) exceeded; per_role from registry.packs_for_agent per blueprint role; per_step from step.knowledge. CLI: `list` (name/status/confidence/docs/token est table), `show PACK`, `scan [--root]` (discover packs via registry + README*/CONTRIBUTING*/ARCHITECTURE*/docs/**/*.md + pyproject.toml/package.json → write `.claude/team-context/knowledge-scan.json`), `propose` (read gap records via existing gap store, group by normalized description, ≥2 occurrences → draft md), `audit` = doctor with `--strict` semantics + new checks (invalid status, stale, missing source_files, no-metadata). Use the cooperative parser (`get_or_create_parser`/`register_handler` in `knowledge/__init__.py`) — read `doctor_cmd.py` for the registration pattern first.
- [ ] **Step 4:** Green + `python -m pytest tests/knowledge tests/cli/test_doctor.py -q`. **Step 5:** Commit: `feat(manager): M5 knowledge pack lifecycle, plan builder, CLI verbs`.

**Wave 1 exit:** orchestrator merges A/B/C branches into `worktree-manager-mode-pmo` (expect zero conflicts — disjoint files; if conflict, stop and resolve manually), runs `python -m pytest tests/manager tests/cli/test_knowledge_cli.py -q`, Fable review, fixes, milestone commits already in place.

---

## Wave 2 — Parallel consumers (M4, M6, M7). Three Sonnet agents, worktrees from Wave-1 HEAD.

### Task 8 (Agent D): Scope contracts + context bundles + dispatcher kwargs (M4)

**Files:** Create `agent_baton/core/manager/context_bundles.py`, `tests/manager/test_context_bundles.py`, `tests/engine/test_manager_context_prompt.py`. Modify `agent_baton/core/engine/dispatcher.py` (build_delegation_prompt only).

- [ ] **Step 1: Failing tests** (PRD M4): `test_every_nontrivial_step_gets_contract` (nontrivial = has agent_name and `step_type` not in `{"gate"}` and no `command` — confirm against `planning/rules/step_types.py` before finalizing), `test_contract_fields_complete`, `test_bundle_includes_role_card_and_required_packs`, `test_bundle_respects_max_knowledge_docs`, `test_overflow_drops_reference_docs_before_required` (budget 200 tokens → reference_only shrinks, scope_contract_path + required packs + latest handoff survive, `truncation_warnings` non-empty), `test_dispatcher_includes_scope_contract_section` (`build_delegation_prompt(..., scope_contract_section="## Scope Contract\n…", context_bundle_section=…)` → sections appear after knowledge section, before `## Your Task`), `test_dispatcher_unchanged_without_kwargs` (prompt with kwargs omitted == prompt from current signature — snapshot equality).
- [ ] **Step 3: Implement.** `ScopeContractBuilder(config).build(step, workstream, role_card) -> ScopeContract` (mission from step.task_description first sentence; in_scope from workstream deliverables + step deliverables; out_of_scope from scope_map.out_of_scope + other workstreams; allowed_paths step.allowed_paths or workstream.allowed_paths; definition_of_done from deliverables + "handoff summary written" + "no unrelated refactors"; escalation_triggers from role card + standard four from spec §11.4). `contract_to_markdown()` per spec §13.1 template. `ContextBundleBuilder(config).build(step, contract_path, role_card, knowledge_plan, prior_handoff_paths) -> ContextBundle`: must_read = contract + step.context_files; reference_only = knowledge attachments with delivery reference; knowledge_packs capped at `max_knowledge_docs_per_step`; token estimate = chars//4 over file sizes (missing files → 0, note in warnings); overflow order (fixed): drop reference_only lowest-priority-first → then oldest prior_handoffs beyond latest → never drop contract/required packs/latest handoff; every drop appends to `truncation_warnings`. Dispatcher: two keyword-only params `scope_contract_section: str | None = None, context_bundle_section: str | None = None` on `build_delegation_prompt`; insert both (order: contract then bundle) immediately after the knowledge-section block (after `_build_knowledge_section` append, ~dispatcher.py:544-548) gated `if section and section.strip():`. No other dispatcher changes.
- [ ] **Step 4:** Green + `python -m pytest tests/engine -k dispatcher -q`. **Step 5:** Commit: `feat(manager): M4 scope contracts, context bundles, dispatcher sections`.

### Task 9 (Agent E): PhasePolicyApplier (M6)

**Files:** Create `agent_baton/core/manager/phase_policy.py`, `tests/manager/test_phase_policy.py`.

- [ ] **Step 1: Failing tests** (PRD M6): `test_always_injects_review_after_each_phase` (2-phase plan → each phase ends with review step, agent = `review_agents.adversarial_review`, `depends_on` = prior last step, `step_type="review"` if that value exists in step_types rules else `"developing"` with deliverable "review verdict"), `test_risk_based_injects_only_at_threshold` (phase risk_level low → no injection; medium → injected), `test_off_injects_nothing` (plan deep-equal unchanged), `test_project_completion_always_adds_final_review` (last phase gains project-review step by `review_agents.project_review`), `test_handoff_required_recorded_not_mutating` (applier returns `PolicyDecisions(handoff_required=True)`; plan steps unchanged by it), `test_gate_scope_respects_explicit_cli` (`apply(..., cli_gate_scope_explicit=True)` never touches plan gates; explicit False + `gates.mode="project_configured"` → plan.gate_scope-affecting fields set from config `gate_scope`), `test_idempotent` (applying twice injects once — detect via step_id prefix `review-`).
- [ ] **Step 3: Implement.** `PhasePolicyApplier(config).apply(plan, *, cli_gate_scope_explicit: bool) -> PolicyDecisions`. Injected step: `PlanStep(step_id=f"review-{phase.phase_id}", agent_name=cfg.policies.review_agents.adversarial_review, task_description="Adversarial review of phase '<name>': verify deliverables against the project charter and phase handoff; veto with reasons or approve.", depends_on=[last step id], deliverables=["review verdict"], parallel_safe=False)`. Respect `MachinePlan` validator (unique ids, backward deps) — run `plan.to_dict()/from_dict` round-trip in tests to prove validity. `PolicyDecisions` (small pydantic model): handoff_required, gates_mode, injected_review_steps: list[str], final_review_step: str | None. NOTE: bundles for injected steps are built later by ManagerModePlanner ordering (Wave 3) — do not build bundles here; the PRD case "review step context includes phase handoff and rubric" is Wave 3's `test_review_bundle_integration`.
- [ ] **Step 4:** Green + `python -m pytest tests/planning/test_plan_quality_golden.py -q` (must stay green). **Step 5:** Commit: `feat(manager): M6 configurable phase and project policies`.

### Task 10 (Agent F): Reports, decision packets, report/team CLI (M7)

**Files:** Create `agent_baton/core/manager/reports.py`, `decisions.py`, `agent_baton/cli/commands/report_cmd.py`, `agent_baton/cli/commands/team_cmd.py`, `tests/manager/test_manager_reports.py`, `tests/manager/test_decision_packets.py`, `tests/cli/test_report_cli.py`.

- [ ] **Step 1: Failing tests** (PRD M7): `test_brief_includes_required_sections` (objective/workstreams/team/knowledge/policies/risks headers), `test_report_includes_team_and_workstream_status`, `test_scope_expansion_creates_packet_when_queued` (DecisionPacketBuilder + policy queue_for_manager → `decisions/<id>.md` + decision-log.jsonl line + DecisionManager request created), `test_knowledge_gap_creates_recommendation_packet`, `test_report_cli_renders_for_active_task`, `test_report_json_machine_readable` (parses, has status/workstreams/open_decisions keys), `test_no_raw_logs_by_default` (report text contains no delegation-prompt content marker from fixture).
- [ ] **Step 3: Implement.** `ManagerReportBuilder(config, paths)`: `build_brief(artifacts, plan) -> str` (sections: Objective, Assumptions, Workstreams table w/ owners, Team summary, Knowledge Packs w/ missing/stale callouts + truncation warnings, Configured Policies, Director Decision Points, Risks); `build_report(plan, artifacts, execution_state: dict | None, beads: list | None) -> str` (Status, Phase/Workstream progress from `execution_state["completed_steps"]`-style keys — read `core/engine/persistence.py` state shape first, Handoffs completed (glob `handoffs/`), Knowledge gaps, Scope changes (decision-log entries type scope_expansion), Reviews completed/pending (steps prefixed `review-`), Open decisions, Final recommendation when state complete); `save_brief`/`save_report` via artifacts writer. `DecisionPacketBuilder(config, paths, decision_manager=None)`: `create(decision: ManagerDecision) -> Path` — renders spec §15.3 md template, appends decision-log.jsonl, and (when a `DecisionManager` from `core/runtime/decisions.py` is provided) files a `DecisionRequest` so `baton execute decide` sees it; `decision_id = f"dec-{sha1(summary+created_at)[:8]}"` with `created_at` passed in by callers (no clock reads inside — testability). CLI `baton report [--task-id] [--json]`: resolve task id via the execute.py ladder pattern (read `_resolve` helpers, reuse `storage.get_active_task()`), load sidecars, render report (build fresh if `manager-report.md` missing). CLI `baton team status|show`: read team-blueprint.json + execution-state.json + open decision packets → spec §8.3 field list; `show` adds role-card summaries. Register both in `cli/main.py::_COMMAND_GROUPS`.
- [ ] **Step 4:** Green. **Step 5:** Commit: `feat(manager): M7 manager reports, decision packets, report and team CLI`.

**Wave 2 exit:** merge D/E/F, run `python -m pytest tests/manager tests/engine/test_manager_context_prompt.py tests/cli -q`, Fable review, fixes.

---

## Wave 3 — Glue + planning E2E (W3 wiring + M8). One Sonnet agent, sequential on the integration branch.

### Task 11: Full ManagerModePlanner composition + config CLI

**Files:** Modify `agent_baton/core/manager/planner.py`, `agent_baton/cli/commands/execution/plan_cmd.py`. Create `agent_baton/cli/commands/config_cmd.py`, `tests/manager/test_manager_mode_planner.py`, `tests/cli/test_config_cli.py`.

- [ ] **Step 1: Failing tests** — `test_build_produces_all_artifacts` (charter, scope_map, blueprint, role_cards_md, knowledge_plan, contracts+bundles for every nontrivial step INCLUDING injected review steps, brief_md), `test_composition_order` (review steps present in scope_contracts keys — proves policy ran before bundles), `test_review_bundle_integration` (review step bundle includes latest phase handoff ref + `review-rubric` pack when registry has it — the deferred PRD M6 case), `test_dry_run_writes_nothing`, `test_save_writes_all_sidecars`, config CLI: `test_config_init_writes_template` (`.claude/baton.yaml` created with manager profile, valid per ManagerConfig.from_yaml), `test_config_validate_reports_bad_enum` (exit code ≠ 0, names offending key), `test_config_show_renders_effective_config`.
- [ ] **Step 3: Implement** composition exactly in the Task-4 docstring order; thread `cli_gate_scope_explicit` from plan_cmd; `--explain` gains a `## Manager Mode` section (workstreams, team, policies applied); `--dry-run` prints artifact preview list (paths + one-line descriptions) without writing. `config_cmd.py`: `init --profile manager` writes the spec §9.1 example (as template constant) to `.claude/baton.yaml` (refuse overwrite without `--force`), `validate` runs `ManagerConfig.load` and prints warnings/errors, `show` prints effective merged config as YAML. Register in `_COMMAND_GROUPS`.
- [ ] **Step 4:** Green + full `python -m pytest tests/manager tests/planning tests/cli -q`. **Step 5:** Commit: `feat(manager): W3 full manager-mode planning composition and config CLI`.

### Task 12: Fixture repo + planning E2E (M8)

**Files:** Create `tests/fixtures/medium_project_repo/` (pyproject.toml; `app/__init__.py`; `app/reporting/service.py` (~30-line real module); `app/auth/session.py` (stub); `tests_fixture/test_service.py`; `README.md`; `.claude/knowledge/coding-conventions/{knowledge.yaml,conventions.md}`; `.claude/baton.yaml` with `adversarial_review: always`, `handoff_required: true`), `tests/e2e/test_manager_mode_planning.py`.

- [ ] **Step 1: Failing test** — run real `IntelligentPlanner.create_plan` + `ManagerModePlanner.build_and_write` against the fixture (task: "Add a reporting endpoint with tests and docs", complexity medium). Assert every PRD M8 item: project-charter.md, scope-map.json, team-blueprint.json, role-cards/ non-empty, knowledge-plan.json, context-bundles/ non-empty, manager-brief.md all exist; review steps present per config; `missing_packs` includes `repo-architecture`+`testing-strategy` (not in fixture registry); **no network**: `monkeypatch.delenv("ANTHROPIC_API_KEY")` + `monkeypatch.setenv("BATON_MANAGER_ENRICH", "off")` + assert no `anthropic` client constructed (monkeypatch import hook or the planner's LLM-review env unset). Non-manager control: same plan without manager mode → `executions/<task>/` contains plan.* only, none of the PMO filenames.
- [ ] **Step 4:** Green. **Step 5:** Commit: `test(manager): M8 medium-project fixture and manager-mode planning E2E`.

**Wave 3 exit:** Fable review, fixes.

---

## Wave 4 — Execution consumption (M9) + docs + final QA. One Sonnet agent, then Fable final review.

### Task 13: Executor hooks

**Files:** Modify `agent_baton/core/engine/executor.py` (three sites only: `_dispatch_action` ~L6600-6699, `_synthesize_beads_post_phase` L960-1048, `complete()` L4004), create `agent_baton/core/engine/scope_expansion.py`, `tests/e2e/test_manager_mode_execution_dry_run.py`, `tests/engine/test_scope_expansion.py`.

- [ ] **Step 1: Failing tests** (PRD M9): `test_dispatch_prompt_includes_scope_contract` (dry-run engine over fixture plan w/ pre-written sidecars → DISPATCH action's delegation prompt contains `## Scope Contract` + `## Context Bundle` with pack refs), `test_non_manager_dispatch_prompt_unchanged` (manager_mode False → byte-identical to pre-change snapshot), `test_phase_completion_writes_handoff` (record all phase-1 steps + pass gate → `handoffs/phase-1-handoff.md` exists with Completed work/Files changed/Decisions/Unresolved/Knowledge gaps/Scope changes/Next phase sections), `test_adversarial_review_step_dispatches_after_phase` (injected review step is the next DISPATCH), `test_manager_report_updates_after_phase` (mtime/content delta), `test_scope_expansion_signal_routes_by_policy` (step result text containing `SCOPE_EXPANSION: app/auth/session.py — session metadata needed` → queue_for_manager: decision packet exists + log line; allow_with_note: warning bead; block: step recorded failed with amend message), `test_complete_writes_final_report`.
- [ ] **Step 3: Implement.** All hooks fire only when `plan.manager_mode` and sidecars exist; every hook wrapped best-effort (`except Exception: logger.debug(...)`) EXCEPT scope-expansion `block` policy which must fail the step visibly. `scope_expansion.py`: `parse_scope_expansions(text) -> list[ScopeExpansionSignal]` — regex `^SCOPE_EXPANSION:\s*(?P<path>\S+)\s*[—-]\s*(?P<reason>.+)$` multiline, mirror `gate_addition.py` structure. Dispatch: load `scope_contract(step_id, "md")` text and render bundle JSON into a compact `## Context Bundle` section (must_read/reference_only/pack names+paths — not full doc bodies). Post-phase: build handoff md from phase step_results (files_changed, summaries), decision beads, gap records; write via ManagerArtifactPaths; then `ManagerReportBuilder.save_report`. Also add the `SCOPE_EXPANSION` signal line to the dispatcher `_SIGNALS_BLOCK` (additive sentence documenting the format — verify existing block wording first). `complete()`: final report after retrospective.
- [ ] **Step 4:** Green + `python -m pytest tests/engine -q` (full engine suite). **Step 5:** Commit: `feat(manager): M9 execution consumes PMO artifacts`.

### Task 14: Docs matrix

**Files:** Modify `docs/cli-reference.md` (plan --manager-mode; config/report/team groups; knowledge list/scan/show/audit), `docs/architecture/package-layout.md` (`core/manager/`), `docs/design-decisions.md` (post-processor + knowledge.yaml decisions, link design doc), `CLAUDE.md` (env table: `BATON_MANAGER_ENRICH`; reference architecture row for `core/manager/`), `GEMINI.md` (same convention deltas), `docs/agent-roster.md` — no change (no new agents).

- [ ] **Step 1:** Write all doc deltas. **Step 2:** `python -m pytest tests/cli -q` (some CLI tests assert help text — fix drift). **Step 3:** Commit: `docs: manager-mode PMO surfaces across docs matrix`.

### Task 15: Final QA (orchestrator + Fable)

- [ ] Full targeted sweep: `python -m pytest tests/manager tests/engine tests/planning tests/cli tests/e2e tests/knowledge -q`.
- [ ] Fable end-to-end review of the whole diff (`/code-review` high) + fixes.
- [ ] `bd close` milestone beads; PR to `master` with release-notes summary (BLUF/Delta/Tech Debt per WRAP protocol).

---

## Self-review notes (resolved during planning)

- PRD M6 case "review step context includes phase handoff and rubric" deliberately moved to Wave 3 (`test_review_bundle_integration`) — it requires M4+M6 composition, impossible in parallel Wave 2.
- Composition order (policy before bundles) is load-bearing and pinned in two places (Task 4 docstring, Task 11 test).
- `ManagerDecision.created_at` is caller-supplied everywhere → no clock reads inside builders → deterministic tests.
- Non-manager byte-identical prompt guarantees are enforced twice (Task 8 snapshot, Task 13 snapshot).
- `knowledge-scan.json` lands at `.claude/team-context/` root (PRD §12.4), not under `executions/` — encoded in Task 7 test.
