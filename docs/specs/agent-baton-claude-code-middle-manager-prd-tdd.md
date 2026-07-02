# Agent Baton PMO Team-Manager Roadmap

**Document type:** Combined PRD + TDD handoff for Codex  
**Target repo:** `DaveGerson/agent-baton`  
**Primary platform:** Claude Code CLI  
**Target environment:** Local developer repositories  
**Audience:** Codex / implementation agent  
**Status:** Second-pass implementation spec  
**Date:** 2026-07-02  
**Supersedes:** Reliability-first draft `agent-baton-claude-code-middle-manager-prd-tdd.md`

---

## 0. Codex Handoff Summary

Implement the next increment of Agent Baton as a **PMO-style team manager for Claude Code**, optimized for medium-sized local repo projects executed by narrowly scoped, knowledgeable, ad-hoc subagent teams.

The human user is not primarily a gate operator. The user is a **director**. The primary Claude Code/orchestrator session is the **team manager**. Baton supplies the PMO structure: project intake, scope decomposition, team design, context discipline, knowledge-pack assignment, handoffs, phase policies, and manager-grade reporting.

The first-pass roadmap over-indexed on verification gates and administrative controls. Verification remains necessary, but in this second-pass roadmap it is treated as a **configurable policy layer**. The priority is manager leverage:

1. Turn a medium-sized project brief into a project charter, scope map, and execution plan.
2. Instantiate an ad-hoc team of specialist agents with clear role contracts.
3. Prevent context rot through narrow scoped context bundles per role/step.
4. Attach and evolve knowledge packs that encode repo, design, testing, review, and domain best practices.
5. Give the human director concise project/status/decision surfaces.
6. Apply phase/project review and gates through config, not hard-coded over-prescription.

### North-star user story

```text
As a technical director using Claude Code on a local repository,
I want to assign Baton a medium-sized project,
so that Baton can form and manage a scoped agent team,
feed each agent only the context and knowledge it needs,
coordinate phase handoffs and reviews,
and surface concise director-level decisions instead of raw agent logs.
```

### Core product thesis

> Agent Baton should provide PMO structure to ad-hoc Claude Code agent teams: narrow scope, curated context, knowledge-pack discipline, handoff hygiene, and configurable phase governance.

---

## 1. Problem Statement

Claude Code can do substantial local repo work, but medium-sized projects degrade when run as one long conversation or as loosely coordinated subagent calls.

Observed failure modes:

1. **Context rot:** too much repo/project context accumulates in the primary conversation or every subagent prompt.
2. **Poor scoping:** agents receive broad tasks and modify files outside the intended workstream.
3. **Weak team structure:** agents are summoned by role name but not managed as a coherent temporary team.
4. **Inconsistent handoffs:** work from one phase is not distilled into the next phase’s context.
5. **Knowledge drift:** project conventions, design rules, testing practices, and domain constraints are scattered across docs and chats.
6. **Manager overload:** the human has to inspect raw logs, remember state, enforce review discipline, and decide what should happen next.
7. **Overly rigid gate design:** safety and review are important, but hard-coding all validation logic into the execution roadmap risks obscuring the higher-leverage PMO/team-management work.

Agent Baton already has many relevant primitives: planning, role assignment, agent registry/routing, knowledge registry, context management, execution state, team records, handoffs, gates, approval, retrospective, memory/beads, and Claude Code launching. This roadmap should connect those primitives into a coherent **director → team manager → agent team** workflow.

---

## 2. Product Goal

Enable a user to run a manager-mode planning/execution flow for a local repo project:

```bash
baton plan "Build a small reporting feature with tests and docs" --manager-mode --save
baton execute run
baton report
```

or eventually:

```bash
baton run "Build a small reporting feature with tests and docs" --manager-mode
```

The expected outputs are not just `plan.json` and gate results. The expected outputs are a complete PMO packet:

```text
.claude/team-context/executions/<task_id>/
  project-charter.md
  scope-map.json
  team-blueprint.json
  role-cards/
    architect.md
    backend-engineer.md
    test-engineer.md
    adversarial-reviewer.md
  knowledge-plan.json
  context-bundles/
    1.1-architect.json
    2.1-backend-engineer.json
    3.1-test-engineer.json
  handoffs/
    phase-1-handoff.md
    phase-2-handoff.md
  manager-brief.md
  manager-report.md
  decision-log.jsonl
```

`plan.json` remains the machine-execution plan, but the PMO packet is what makes the system trustworthy and useful to a human director.

---

## 3. Target User and Operating Model

## 3.1 Human user: director

The human director:

- Provides project intent and constraints.
- Reviews the project charter and major assumptions.
- Approves high-impact scope changes.
- Reviews concise status/decision packets.
- Does not manually babysit every subagent.
- Does not inspect raw logs unless debugging.

## 3.2 Primary Claude Code/orchestrator instance: team manager

The orchestrator session:

- Reads Baton’s plan and manager packets.
- Dispatches specialist subagents.
- Maintains project cadence.
- Enforces scope, context budgets, and handoff protocol.
- Applies configured phase policies.
- Escalates to the human director only when needed.

## 3.3 Specialist subagents: ad-hoc project team

Subagents:

- Receive narrow role contracts.
- Receive scoped context bundles.
- Receive knowledge-pack references relevant to their task.
- Work only within assigned scope.
- Produce structured handoff material.
- Emit knowledge-gap and scope-expansion signals when appropriate.

## 3.4 Baton: PMO/control plane

Baton provides:

- Project chartering.
- Scope decomposition.
- Team blueprinting.
- Knowledge-pack selection and lifecycle management.
- Context-bundle construction.
- Phase policy injection.
- Handoff capture.
- Manager reporting.
- State, execution, and recovery.

---

## 4. Design Priorities

## 4.1 Priority 1 — Project charter and scope map

A medium-sized project should begin with a project charter, not only a sequence of steps.

The charter should answer:

```text
- What are we trying to accomplish?
- What is explicitly out of scope?
- What assumptions are being made?
- What repo areas are likely affected?
- What are the workstreams?
- What are the delivery artifacts?
- What are the manager decision points?
- What risks or unknowns should be tracked?
```

The scope map should bind workstreams to repo areas, agents, knowledge packs, and deliverables.

## 4.2 Priority 2 — Ad-hoc team instantiation

Baton should not simply select agents. It should instantiate a temporary team.

A team blueprint should include:

```text
- team purpose
- selected roles
- role responsibilities
- workstream ownership
- dependency relationships
- collaboration/handoff rules
- escalation triggers
- review/adversarial roles from config
```

## 4.3 Priority 3 — Context and scoping discipline

Every agent step should have a scope contract and a context bundle.

The agent should know:

```text
- mission
- in-scope files/areas
- out-of-scope files/areas
- allowed paths
- relevant prior decisions
- required knowledge packs
- handoff inputs
- expected outputs
- escalation triggers
```

This is the main anti-context-rot mechanism.

## 4.4 Priority 4 — Knowledge-pack management

Knowledge packs should encode reusable best practices and repo/domain conventions.

Baton should:

- discover existing project knowledge,
- attach relevant packs per role/step,
- detect missing knowledge packs,
- propose new packs when gaps repeat,
- track source/confidence/staleness,
- update or retire stale packs through manager review.

## 4.5 Priority 5 — Configurable phase/project policies

Reviews and gates are important, but should be configured as project policy.

Example:

```yaml
policies:
  phase_completion:
    adversarial_review: always
    gate_scope: project_configured
  project_completion:
    adversarial_review: always
    manager_report: required
```

Baton should read configuration and inject review/gate behavior accordingly. The roadmap should not hard-code one universal review/gate model.

## 4.6 Priority 6 — Director-level status and decision surfaces

The human should receive:

```text
- project brief
- team status
- open decisions
- phase handoffs
- knowledge gaps
- scope changes
- risks
- final report
```

not raw execution trace by default.

---

## 5. Non-Goals

Do not prioritize:

- Non-Claude CLI agent adapters.
- Remote/cloud execution.
- Full enterprise PMO UI.
- Heavy CI/governance expansion.
- Hard-coding all possible gates.
- Speculative execution.
- Multi-repo portfolio management.
- Rewriting the existing execution engine.
- Breaking the existing `_print_action()` protocol.

Gates remain in scope only as **configurable phase/project policy** and as part of project setup. They are not the center of this roadmap.

---

## 6. Success Metrics

## 6.1 Manager leverage metrics

| Metric | Target |
|---|---:|
| Medium project produces project charter | 100% |
| Medium project produces scope map | 100% |
| Medium project produces team blueprint | 100% |
| Each nontrivial agent step has scope contract | 100% |
| Each nontrivial agent step has context bundle | 100% |
| Final report identifies workstreams and ownership | 100% |
| Human interventions are surfaced as decision packets | 100% |

## 6.2 Context discipline metrics

| Metric | Target |
|---|---:|
| Agent prompts include in-scope/out-of-scope boundaries | 100% |
| Context bundle stays under configured token budget | 100% |
| Duplicate large knowledge docs are not repeatedly inlined | 100% |
| Prior phase output is summarized before inclusion | 100% |
| Scope-expansion requests are captured explicitly | 100% |

## 6.3 Knowledge-pack metrics

| Metric | Target |
|---|---:|
| Required default knowledge packs are resolved or reported missing | 100% |
| Step-level knowledge attachments include source and confidence | 100% |
| Missing knowledge-pack recommendations are emitted when repeated gaps occur | 100% |
| Knowledge packs can be listed/audited by CLI | 100% |

## 6.4 Verification/policy metrics

| Metric | Target |
|---|---:|
| Configured phase review policy is applied | 100% |
| Configured project completion policy is applied | 100% |
| Existing manual execution commands remain compatible | 100% |
| Existing public text action protocol remains compatible | 100% |

---

## 7. Existing System Touchpoints

Codex should inspect these paths before implementation:

```text
agent_baton/cli/main.py
agent_baton/cli/commands/execution/plan_cmd.py
agent_baton/cli/commands/execution/execute.py
agent_baton/core/config.py or agent_baton/core/config/
agent_baton/core/engine/planning/
agent_baton/core/engine/planner.py
agent_baton/core/engine/dispatcher.py
agent_baton/core/engine/executor.py
agent_baton/core/engine/team_board.py
agent_baton/core/engine/team_registry.py
agent_baton/core/engine/team_tools.py
agent_baton/core/engine/mailbox.py
agent_baton/core/orchestration/registry.py
agent_baton/core/orchestration/router.py
agent_baton/core/orchestration/context.py
agent_baton/core/orchestration/knowledge_registry.py
agent_baton/core/engine/knowledge_resolver.py
agent_baton/core/intel/knowledge_ranker.py
agent_baton/core/intel/handoff_synthesizer.py
agent_baton/core/intel/context_harvester.py
agent_baton/core/observe/retrospective.py
agent_baton/core/learn/pattern_learner.py
agent_baton/models/execution.py
agent_baton/models/knowledge.py
agent_baton/models/pmo.py
tests/
```

Important existing behavior to preserve:

- `baton execute next` text output is a public protocol for Claude Code orchestration.
- `_print_action()` labels and delimiters must not be changed incompatibly.
- Existing `baton plan`, `baton execute start`, `baton execute run`, and manual execution commands remain supported.
- Existing dry-run, import, template, and plan-save behavior remains supported.
- Existing knowledge registry and context manager should be reused before inventing parallel systems.

---

## 8. Proposed Product Surface

## 8.1 Manager-mode planning

Extend existing planning command:

```bash
baton plan "<project brief>" --manager-mode --save
baton plan "<project brief>" --manager-mode --dry-run
baton plan "<project brief>" --manager-mode --explain
```

### Expected behavior

With `--manager-mode`, planning produces the existing execution plan plus PMO artifacts:

```text
- project-charter.md
- scope-map.json
- team-blueprint.json
- role-cards/*.md
- knowledge-plan.json
- manager-brief.md
```

The existing `MachinePlan` should remain compatible. Prefer adding PMO artifacts as sidecar files first. Only add optional plan fields if needed for execution.

## 8.2 Manager config

Add a project config surface. Preferred path:

```text
.claude/baton.yaml
```

Optional future fallback:

```text
~/.baton/config.yaml
```

Add command:

```bash
baton config init --profile manager
baton config validate
baton config show
```

If `baton config` is too much for one increment, implement config loading plus tests first, and expose `config` commands in a later milestone.

## 8.3 Team status

Add or standardize:

```bash
baton team status [--task-id TASK_ID]
baton team show [--task-id TASK_ID]
```

This should show:

```text
- team purpose
- roles
- current phase/workstream ownership
- completed handoffs
- open knowledge gaps
- open scope changes
- manager decisions needed
```

## 8.4 Knowledge-pack commands

Add or extend:

```bash
baton knowledge list
baton knowledge scan
baton knowledge propose
baton knowledge audit
baton knowledge show PACK_NAME
```

If the repo already has a knowledge CLI under another command name, reuse that surface rather than creating a duplicate. The product requirement is the capability, not the exact command name.

## 8.5 Director report

Add or standardize:

```bash
baton report [--task-id TASK_ID]
baton report --json [--task-id TASK_ID]
```

The report should summarize:

```text
- project charter
- team blueprint
- current status
- workstream status
- context/knowledge packs used
- phase handoffs
- scope changes
- open decisions
- configured reviews/gates run or pending
- final recommendation
```

---

## 9. Manager Configuration Specification

## 9.1 Example `.claude/baton.yaml`

```yaml
version: 1

manager_mode:
  enabled_by_default: false
  project_size_default: medium
  manager_decision_threshold: medium
  assumptions_policy: record_and_continue
  ambiguity_policy: ask_when_high_impact

team:
  max_agents_by_complexity:
    light: 2
    medium: 5
    heavy: 8
  require_role_cards: true
  require_workstream_owners: true
  prefer_specialists_over_generalists: true
  allow_talent_builder: true
  default_roles:
    - architect
    - backend-engineer
    - test-engineer

scoping:
  require_scope_contracts: true
  require_allowed_paths: true
  allow_cross_scope_edits: manager_approval
  scope_expansion_policy: queue_for_manager
  out_of_scope_policy: block_or_escalate

context:
  default_step_token_budget: 12000
  max_knowledge_docs_per_step: 6
  include_prior_phase_handoff: true
  include_full_prior_outputs: false
  summarize_prior_outputs: true
  dedupe_knowledge_across_session: true
  context_bundle_format: json

knowledge_packs:
  discovery_paths:
    - .claude/knowledge
    - docs
    - .
  default_packs:
    - repo-architecture
    - coding-conventions
    - testing-strategy
  required_for_code_steps:
    - coding-conventions
    - testing-strategy
  stale_after_days: 90
  missing_pack_policy: propose

policies:
  phase_completion:
    adversarial_review: always
    handoff_required: true
    gates: project_configured
  project_completion:
    adversarial_review: always
    manager_report: required
    retrospective: required
  review_agents:
    adversarial_review: code-reviewer
    project_review: auditor

gates:
  mode: project_configured
  gate_scope: focused
  allow_smoke_fallback: true
  missing_gate_policy: warn_and_request_manager_decision

reporting:
  write_manager_brief: true
  write_manager_report: true
  decision_log: true
  include_raw_logs_by_default: false
```

## 9.2 Config loading rules

Resolution order:

1. CLI flags.
2. Project config `.claude/baton.yaml`.
3. Optional user config `~/.baton/config.yaml`.
4. Built-in defaults.

Rules:

- Invalid config should fail early with actionable errors.
- Unknown top-level keys should warn, not crash, unless strict mode is enabled.
- Unknown nested policy values should fail validation.
- Missing config should use safe defaults.
- Existing non-manager workflows should behave as before unless `--manager-mode` or config default enables manager mode.

## 9.3 Config schema model

Add typed models, preferably dataclasses or Pydantic consistent with the repo style:

```text
ManagerConfig
TeamConfig
ScopingConfig
ContextConfig
KnowledgePackConfig
PhasePolicyConfig
GateConfig
ReportingConfig
```

Location recommendation:

```text
agent_baton/core/config/manager.py
```

If `agent_baton/core/config.py` already exists as a module, avoid package/module naming conflicts. Use an appropriate path based on the existing layout.

---

## 10. Data Models

Prefer sidecar models initially to avoid destabilizing `MachinePlan`.

## 10.1 ProjectCharter

```python
@dataclass
class ProjectCharter:
    task_id: str
    title: str
    objective: str
    background: str
    in_scope: list[str]
    out_of_scope: list[str]
    assumptions: list[str]
    constraints: list[str]
    risks: list[str]
    manager_decision_points: list[str]
    success_criteria: list[str]
    likely_repo_areas: list[str]
```

## 10.2 ScopeMap

```python
@dataclass
class ScopeMap:
    task_id: str
    workstreams: list[Workstream]
    cross_cutting_concerns: list[str]
    out_of_scope: list[str]
    scope_expansion_policy: str
```

```python
@dataclass
class Workstream:
    id: str
    name: str
    objective: str
    likely_paths: list[str]
    allowed_paths: list[str]
    owner_role: str
    dependencies: list[str]
    deliverables: list[str]
    risks: list[str]
```

## 10.3 TeamBlueprint

```python
@dataclass
class TeamBlueprint:
    task_id: str
    team_name: str
    mission: str
    roles: list[RoleCard]
    workstream_assignments: dict[str, str]
    collaboration_rules: list[str]
    escalation_triggers: list[str]
    phase_policies: dict[str, object]
```

## 10.4 RoleCard

```python
@dataclass
class RoleCard:
    role: str
    agent_name: str
    mission: str
    owns: list[str]
    does_not_own: list[str]
    required_knowledge_packs: list[str]
    default_context_budget: int
    expected_handoffs: list[str]
    escalation_triggers: list[str]
```

## 10.5 ScopeContract

```python
@dataclass
class ScopeContract:
    step_id: str
    agent_name: str
    workstream_id: str
    mission: str
    in_scope: list[str]
    out_of_scope: list[str]
    allowed_paths: list[str]
    expected_artifacts: list[str]
    definition_of_done: list[str]
    escalation_triggers: list[str]
```

## 10.6 ContextBundle

```python
@dataclass
class ContextBundle:
    task_id: str
    step_id: str
    agent_name: str
    scope_contract_path: str
    must_read: list[ContextReference]
    reference_only: list[ContextReference]
    knowledge_packs: list[KnowledgePackReference]
    prior_handoffs: list[str]
    decisions: list[str]
    constraints: list[str]
    token_budget: int
    estimated_tokens: int
```

## 10.7 KnowledgePlan

```python
@dataclass
class KnowledgePlan:
    task_id: str
    selected_packs: list[KnowledgePackReference]
    missing_packs: list[MissingKnowledgePack]
    stale_packs: list[str]
    per_role_packs: dict[str, list[str]]
    per_step_packs: dict[str, list[str]]
```

## 10.8 ManagerDecision

```python
@dataclass
class ManagerDecision:
    decision_id: str
    task_id: str
    decision_type: str
    summary: str
    context: str
    options: list[str]
    recommended_option: str
    created_at: str
    resolved_at: str | None
    resolution: str | None
```

---

## 11. Technical Architecture

## 11.1 New/extended services

Implement with small cohesive services. Avoid adding more responsibility directly into `ExecutionEngine` unless absolutely necessary.

Recommended modules:

```text
agent_baton/core/manager/
  __init__.py
  config.py
  charter.py
  scope.py
  team_blueprint.py
  role_cards.py
  context_bundles.py
  knowledge_plan.py
  phase_policy.py
  reports.py
  decisions.py
```

If the repo already has a more appropriate package for some functions, reuse it.

### Service responsibilities

| Service | Responsibility |
|---|---|
| `ManagerConfigLoader` | Load/validate `.claude/baton.yaml`. |
| `ProjectCharterBuilder` | Convert task summary + repo signals into charter. |
| `ScopeMapBuilder` | Convert charter into workstreams and scope contracts. |
| `TeamBlueprintBuilder` | Convert scope map + agent registry into role cards and assignments. |
| `ContextBundleBuilder` | Build per-step scoped context bundles. |
| `KnowledgePlanBuilder` | Resolve default/required/relevant knowledge packs and missing pack proposals. |
| `PhasePolicyApplier` | Inject configured review/gate/handoff policies into the plan. |
| `ManagerReportBuilder` | Write manager brief/report/status artifacts. |
| `DecisionPacketBuilder` | Write manager decision packets for ambiguity, scope expansion, approval, or failure. |

## 11.2 Planning pipeline integration

Integrate manager-mode after basic classification and before final assembly.

Preferred flow:

```text
1. Existing ClassificationStage
2. Existing RosterStage
3. Existing RiskStage
4. ManagerModeStage:
   - build project charter
   - build scope map
   - build team blueprint
   - build role cards
   - build knowledge plan
5. Existing DecompositionStage, but influenced by scope map/team blueprint
6. ContextBundleStage:
   - attach scope contracts and context bundle refs to steps
7. PhasePolicyStage:
   - apply configured adversarial review, handoff, and gate policies
8. Existing ValidationStage
9. Existing AssemblyStage
10. Write PMO sidecar artifacts
```

If adding pipeline stages is too invasive, implement a manager-mode post-processor around the current `IntelligentPlanner.create_plan()` output:

```text
plan = planner.create_plan(...)
manager_artifacts = ManagerModePlanner(...).build(plan, task_summary, project_root)
plan = PhasePolicyApplier(...).apply(plan, manager_artifacts)
ContextManager.write_manager_artifacts(...)
```

The post-processor approach is preferred for the first increment because it reduces risk.

## 11.3 Execution integration

Execution should consume manager-mode artifacts without requiring a rewrite.

Minimal execution additions:

1. When dispatching a step, include its `ScopeContract` and `ContextBundle` in the delegation prompt.
2. When a step completes, require a handoff summary for phase transitions if configured.
3. When an agent emits scope expansion or knowledge gap signals, write a manager decision packet or knowledge recommendation.
4. When phase completes, apply configured phase policy, such as adversarial review.
5. When project completes, generate manager report.

## 11.4 Prompt/dispatch integration

Modify prompt assembly, preferably in `PromptDispatcher`, to include manager-mode sections only when artifacts exist.

Suggested prompt structure:

```text
# Role
<agent role from role card>

# Mission
<step mission>

# Scope Contract
In scope:
- ...

Out of scope:
- ...

Allowed paths:
- ...

# Knowledge Packs
Must use:
- pack name, purpose, path

# Context Bundle
Must read:
- ...

Reference only:
- ...

# Prior Handoff
<phase or step handoff summary>

# Deliverables
- ...

# Escalation Triggers
Escalate if:
- scope expansion needed
- knowledge gap blocks work
- assigned paths are insufficient
- design assumption appears invalid
```

The goal is not longer prompts. The goal is **better scoped prompts**.

---

## 12. Knowledge Pack Specification

## 12.1 Knowledge pack directory layout

Recommended project-level layout:

```text
.claude/knowledge/
  repo-architecture/
    pack.yaml
    overview.md
    boundaries.md
  coding-conventions/
    pack.yaml
    conventions.md
    examples.md
  testing-strategy/
    pack.yaml
    test-commands.md
    test-patterns.md
  review-rubric/
    pack.yaml
    rubric.md
  domain-glossary/
    pack.yaml
    glossary.md
```

## 12.2 `pack.yaml` schema

```yaml
name: testing-strategy
version: 1
status: active
summary: How tests are organized and run in this repository.
owners:
  - manager
applies_to:
  roles:
    - test-engineer
    - backend-engineer
  task_types:
    - new-feature
    - bug-fix
    - refactor
source_files:
  - pyproject.toml
  - tests/README.md
confidence: medium
last_reviewed: 2026-07-02
stale_after_days: 90
documents:
  - test-commands.md
  - test-patterns.md
```

## 12.3 Pack lifecycle states

| State | Meaning |
|---|---|
| `active` | Can be attached automatically. |
| `draft` | Proposed but not manager-approved. |
| `stale` | Can be attached with warning or excluded by config. |
| `deprecated` | Not attached unless explicitly requested. |
| `missing` | Required by policy but not available. |

## 12.4 Knowledge pack operations

### `baton knowledge scan`

Discovers:

```text
- existing .claude/knowledge packs
- README/CONTRIBUTING/ARCHITECTURE docs
- package/test config files
- coding convention docs
- API/domain docs
```

Writes:

```text
.claude/team-context/knowledge-scan.json
```

### `baton knowledge propose`

Generates missing-pack proposals from:

```text
- repeated knowledge gaps
- project config required packs
- repo signals
- retrospective findings
```

Writes:

```text
.claude/team-context/knowledge-proposals/*.md
```

### `baton knowledge audit`

Checks:

```text
- stale packs
- missing source files
- conflicting guidance
- unused packs
- packs without confidence/source metadata
```

### `baton knowledge show PACK`

Displays pack summary, status, source files, attached roles, and freshness.

## 12.5 Pack attachment rules

Knowledge packs should be attached by relevance and budget:

1. Required packs from config.
2. Role-specific packs from role card.
3. Workstream-specific packs from scope map.
4. Task-type packs from knowledge registry.
5. Prior successful packs from retrospective/learning data.

Each attachment should include:

```text
- pack name
- path
- reason attached
- confidence
- status
- token estimate
```

---

## 13. Scoping and Context Discipline

## 13.1 Scope contracts

Every nontrivial step should get a scope contract. It should be written as both JSON and prompt-ready Markdown.

Example Markdown:

```markdown
# Scope Contract: Step 2.1

## Mission
Implement the service-layer change required for the reporting endpoint.

## In Scope
- `app/reporting/**`
- `app/services/reporting_service.py`
- unit tests for changed service behavior

## Out of Scope
- authentication changes
- database migrations
- frontend UI
- global refactors

## Allowed Paths
- `app/reporting/**`
- `app/services/reporting_service.py`
- `tests/reporting/**`

## Definition of Done
- service behavior implemented
- tests added or updated
- no unrelated refactors
- handoff summary written

## Escalate If
- schema changes are required
- reporting data model is insufficient
- changes outside allowed paths are necessary
```

## 13.2 Scope expansion

When an agent needs to go out of scope, it should not silently proceed.

Config:

```yaml
scoping:
  scope_expansion_policy: queue_for_manager
```

Options:

| Policy | Behavior |
|---|---|
| `allow_with_note` | Proceed and record scope expansion. |
| `queue_for_manager` | Create manager decision packet. |
| `block` | Fail step until plan amended. |

Default for manager mode: `queue_for_manager`.

## 13.3 Context bundle budgets

Context bundle builder should enforce budgets:

```yaml
context:
  default_step_token_budget: 12000
  max_knowledge_docs_per_step: 6
  include_full_prior_outputs: false
  summarize_prior_outputs: true
```

If budget is exceeded:

1. Keep scope contract.
2. Keep required knowledge packs.
3. Keep most recent phase handoff.
4. Drop lower-ranked reference docs.
5. Emit a context-truncation warning in the manager brief.

## 13.4 Handoff summaries

Phase handoff should be a first-class artifact:

```text
handoffs/phase-1-handoff.md
```

Required contents:

```text
- completed work
- files changed
- decisions made
- unresolved questions
- knowledge gaps
- scope changes
- next phase recommendations
```

If configured:

```yaml
policies:
  phase_completion:
    handoff_required: true
```

then the next phase should not dispatch without a handoff or a manager-visible warning.

---

## 14. Team Blueprint and Role Cards

## 14.1 Team blueprint behavior

For manager-mode medium projects, Baton should build a team blueprint that includes:

```text
- team mission
- role list
- workstream ownership
- phase participation
- handoff expectations
- conflict/escalation rules
- configured review roles
```

## 14.2 Role card template

```markdown
# Role Card: backend-engineer

## Mission
Own implementation for the reporting service workstream.

## Owns
- service implementation
- local unit tests related to service behavior
- implementation handoff

## Does Not Own
- product requirements
- unrelated refactors
- security policy review
- final adversarial review

## Required Knowledge Packs
- repo-architecture
- coding-conventions
- testing-strategy

## Context Budget
12,000 tokens

## Escalation Triggers
- required change crosses assigned path boundary
- test strategy is missing
- API contract ambiguity blocks implementation
- design assumption appears wrong

## Handoff Requirements
- changed files
- decisions made
- tests added/updated
- remaining risks
```

## 14.3 Adversarial review as team policy

Adversarial review should be injected from config, not hard-coded everywhere.

Example:

```yaml
policies:
  phase_completion:
    adversarial_review: always
  project_completion:
    adversarial_review: always
  review_agents:
    adversarial_review: code-reviewer
    project_review: auditor
```

Behavior:

- `always`: add review step after every completed phase.
- `risk_based`: add review step for medium/high risk or configured workstreams.
- `off`: do not inject.

The review agent should receive:

```text
- project charter
- phase handoff
- scope contracts
- changed files
- relevant knowledge packs
- review rubric pack if available
```

---

## 15. Manager Reports and Decision Packets

## 15.1 Manager brief

Written after planning:

```text
manager-brief.md
```

Contents:

```text
- project objective
- assumptions
- workstreams
- team blueprint summary
- key knowledge packs
- configured policies
- expected manager decision points
- risks
```

## 15.2 Manager report

Written during/after execution:

```text
manager-report.md
```

Contents:

```text
- status
- phase/workstream progress
- team activity
- handoffs completed
- knowledge gaps
- scope changes
- reviews completed/pending
- gates completed/pending if configured
- open decisions
- final recommendation
```

## 15.3 Decision packets

Decision packets should be written to:

```text
.claude/team-context/executions/<task_id>/decisions/<decision_id>.md
.claude/team-context/executions/<task_id>/decision-log.jsonl
```

Example:

```markdown
# Manager Decision Required: Scope Expansion

## Summary
Backend engineer needs to modify `app/auth/session.py`, which is outside the assigned reporting scope.

## Context
The reporting endpoint currently depends on user session metadata not exposed by the reporting service.

## Options
1. Approve scope expansion for `app/auth/session.py`.
2. Amend plan to add an auth/session workstream.
3. Reject and ask agent to find an alternative within current scope.

## Recommendation
Option 2. The change affects auth/session behavior and should have a dedicated owner and review.
```

## 15.4 Director inbox

Optional later CLI:

```bash
baton inbox
```

Shows only items requiring director attention:

```text
- high-impact assumptions
- scope expansion decisions
- blocked knowledge gaps
- review vetoes
- unresolved phase handoffs
- project completion review
```

---

## 16. Test-Driven Development Plan

Codex must write failing tests before implementation for each milestone. Use `DryRunLauncher` and deterministic fixtures. Do not require live Claude Code in tests.

## Milestone 1 — Manager config foundation

### Product outcome

Baton can load and validate `.claude/baton.yaml` and expose manager-mode settings to planning.

### Implementation tasks

1. Add `ManagerConfig` models.
2. Add config loader.
3. Add default config.
4. Add validation errors for invalid enum values.
5. Wire config into `baton plan --manager-mode`.
6. Preserve non-manager behavior when config absent.

### Tests

Create tests such as:

```text
tests/manager/test_manager_config.py
```

Required test cases:

```text
- loads built-in defaults when no config file exists
- loads .claude/baton.yaml when present
- CLI flags override project config
- invalid phase policy value raises validation error
- unknown top-level key warns but does not crash
- manager_mode.enabled_by_default enables manager-mode planning
- non-manager baton plan output is unchanged when manager mode is off
```

### Acceptance criteria

- `ManagerConfig.load(project_root)` works.
- Config validation is deterministic.
- Existing tests continue to pass.

---

## Milestone 2 — Project charter and scope map

### Product outcome

`baton plan --manager-mode --save` writes `project-charter.md` and `scope-map.json`.

### Implementation tasks

1. Add `ProjectCharterBuilder`.
2. Add `ScopeMapBuilder`.
3. Use task summary, classifier output, stack profile, project root, and agent roster as inputs.
4. Generate workstreams for medium/heavy projects.
5. Identify likely paths using repo signals where possible; otherwise record assumptions.
6. Write sidecar artifacts under the task execution directory.

### Tests

Create:

```text
tests/manager/test_project_charter.py
tests/manager/test_scope_map.py
```

Required test cases:

```text
- medium project creates non-empty charter with objective/in_scope/out_of_scope/assumptions/success_criteria
- medium multi-part task creates at least two workstreams
- workstream has owner_role, deliverables, likely_paths, and risks
- ambiguous task records assumptions instead of inventing certainty
- high-impact ambiguity creates manager decision point when config says ask_when_high_impact
- saved manager-mode plan writes charter and scope map to execution directory
```

### Acceptance criteria

- Every manager-mode plan has a charter and scope map.
- Scope map is JSON round-trippable.
- Charter is readable Markdown.

---

## Milestone 3 — Team blueprint and role cards

### Product outcome

Manager-mode planning writes `team-blueprint.json` and one role card per selected agent/role.

### Implementation tasks

1. Add `TeamBlueprintBuilder`.
2. Add `RoleCard` model.
3. Map workstreams to owner roles.
4. Encode collaboration/handoff rules.
5. Inject configured review/adversarial roles as team roles when policy requires them.
6. Write role cards as Markdown.

### Tests

Create:

```text
tests/manager/test_team_blueprint.py
```

Required test cases:

```text
- manager-mode plan writes team-blueprint.json
- every workstream has an owner role
- every selected implementation/review role has a role card
- role card includes owns/does_not_own/required_knowledge_packs/escalation_triggers
- prefer_specialists_over_generalists avoids single broad role for multi-workstream medium task
- configured adversarial_review: always adds review role to team blueprint
- configured adversarial_review: off does not add review role solely due to policy
```

### Acceptance criteria

- Team blueprint is deterministic and JSON round-trippable.
- Role cards are human-readable and prompt-ready.

---

## Milestone 4 — Context bundles and scope contracts

### Product outcome

Every nontrivial manager-mode plan step has a scope contract and context bundle.

### Implementation tasks

1. Add `ScopeContract` model and writer.
2. Add `ContextBundle` model and writer.
3. Build bundles from charter, scope map, role card, knowledge plan, and prior handoffs.
4. Add token-budget estimation.
5. Add budget overflow behavior.
6. Attach context bundle references to step metadata or a sidecar index.
7. Update prompt dispatch to include scope contract/context bundle when present.

### Tests

Create:

```text
tests/manager/test_context_bundles.py
tests/engine/test_manager_context_prompt.py
```

Required test cases:

```text
- every nontrivial step gets a scope contract
- scope contract includes in_scope/out_of_scope/allowed_paths/definition_of_done/escalation_triggers
- context bundle includes role card and required knowledge packs
- context bundle respects max_knowledge_docs_per_step
- context budget overflow drops lower-priority reference docs before required packs
- prompt dispatcher includes Scope Contract section when bundle exists
- prompt dispatcher does not include manager-mode sections for non-manager plans
```

### Acceptance criteria

- Agent dispatch prompts are visibly narrower and more structured in manager mode.
- Existing non-manager prompt behavior remains compatible.

---

## Milestone 5 — Knowledge-pack lifecycle

### Product outcome

Baton can scan, select, attach, and audit knowledge packs for manager-mode projects.

### Implementation tasks

1. Define `pack.yaml` parser/validator.
2. Add `KnowledgePlanBuilder`.
3. Integrate with existing `KnowledgeRegistry` where possible.
4. Add `baton knowledge list/scan/propose/audit/show` or extend existing equivalent CLI.
5. Attach default/required packs from config.
6. Emit missing-pack proposals when required packs are absent.
7. Emit stale-pack warnings.

### Tests

Create:

```text
tests/manager/test_knowledge_packs.py
tests/cli/test_knowledge_cli.py
```

Required test cases:

```text
- valid pack.yaml parses successfully
- invalid pack status fails validation
- knowledge scan discovers .claude/knowledge packs
- required missing pack appears in knowledge-plan.json missing_packs
- stale pack appears in knowledge-plan.json stale_packs
- role-specific pack attaches to matching role
- required_for_code_steps packs attach to implementation steps
- knowledge audit reports missing source files
- knowledge propose writes a draft proposal for repeated knowledge gap signal fixture
```

### Acceptance criteria

- Manager-mode plans produce `knowledge-plan.json`.
- Missing/stale knowledge is surfaced to the manager.
- Knowledge pack attachment is explainable.

---

## Milestone 6 — Configurable phase and project policies

### Product outcome

Adversarial reviews, handoffs, and gates are applied from config.

### Implementation tasks

1. Add `PhasePolicyApplier`.
2. Support `adversarial_review: always|risk_based|off`.
3. Support `handoff_required: true|false`.
4. Support `gates: project_configured|focused|full|smoke|off`.
5. Inject review steps/phase gates only according to config.
6. Ensure injected review steps receive review-specific context bundles.

### Tests

Create:

```text
tests/manager/test_phase_policy.py
```

Required test cases:

```text
- adversarial_review: always injects review after each phase
- adversarial_review: risk_based injects review only for configured risk threshold
- adversarial_review: off does not inject review
- project_completion.adversarial_review: always injects final review
- handoff_required true causes phase handoff artifact requirement
- gates policy reads configured gate_scope without overriding explicit CLI gate_scope
- review step context includes phase handoff and review rubric pack when available
```

### Acceptance criteria

- Review/gate behavior is project-configurable.
- Gates are not hard-coded as the dominant design mechanism.
- Existing gate behavior remains usable.

---

## Milestone 7 — Manager reports and decision packets

### Product outcome

Baton writes manager-facing status and decision artifacts.

### Implementation tasks

1. Add `ManagerReportBuilder`.
2. Add `DecisionPacketBuilder`.
3. Write `manager-brief.md` after manager-mode planning.
4. Write/update `manager-report.md` during and after execution.
5. Write decision packets for high-impact ambiguity, scope expansion, blocked knowledge gaps, and review vetoes.
6. Add `baton report` CLI or extend existing dashboard/status commands.

### Tests

Create:

```text
tests/manager/test_manager_reports.py
tests/manager/test_decision_packets.py
tests/cli/test_report_cli.py
```

Required test cases:

```text
- manager brief includes objective/workstreams/team/knowledge/policies/risks
- manager report includes team status and workstream status
- scope expansion signal creates decision packet when policy is queue_for_manager
- knowledge gap signal creates missing knowledge recommendation or decision packet
- report CLI renders manager report for active task
- report --json returns machine-readable status
- raw logs are not included by default
```

### Acceptance criteria

- Human director can understand project state from manager report.
- Open decisions are explicit and actionable.

---

## Milestone 8 — Manager-mode dry-run fixture

### Product outcome

A medium-sized local repo fixture can produce the full PMO packet without live Claude calls.

### Implementation tasks

1. Add fixture repo with simple Python or Node project.
2. Add manager config fixture.
3. Run `baton plan --manager-mode --save --dry-run` or equivalent test helper.
4. Assert all PMO artifacts are generated.
5. Assert non-manager plan remains smaller/no PMO artifacts.

### Tests

Create:

```text
tests/fixtures/medium_project_repo/
tests/e2e/test_manager_mode_planning.py
```

Required assertions:

```text
- project-charter.md exists
- scope-map.json exists
- team-blueprint.json exists
- role-cards directory exists
- knowledge-plan.json exists
- context-bundles directory exists
- manager-brief.md exists
- configured adversarial review is represented in plan/team/policies
- no live Claude invocation occurs
```

### Acceptance criteria

- Manager-mode planning is demonstrable end-to-end without network/model dependence.

---

## Milestone 9 — Execution consumption of PMO artifacts

### Product outcome

Execution dispatch uses the PMO artifacts generated during planning.

### Implementation tasks

1. Teach execution/dispatch layer to locate context bundle for each step.
2. Include scope contract and knowledge-pack summary in prompts.
3. Write phase handoff artifacts after phase completion when configured.
4. Update manager report after phase transitions.
5. Ensure adversarial review steps receive correct context.

### Tests

Create:

```text
tests/e2e/test_manager_mode_execution_dry_run.py
```

Required test cases:

```text
- dry-run execution dispatch prompt includes scope contract
- dry-run execution dispatch prompt includes relevant knowledge pack refs
- phase completion writes handoff when configured
- adversarial review step dispatches after phase when configured
- manager report updates after phase completion
```

### Acceptance criteria

- PMO artifacts are not dead files; they actively guide execution.

---

## 17. Acceptance Criteria by Priority

## 17.1 Team management

- [ ] `team-blueprint.json` exists for every manager-mode plan.
- [ ] Every workstream has an owner role.
- [ ] Every selected role has a role card.
- [ ] Review/adversarial roles are injected according to config.
- [ ] Manager report shows team and workstream status.

## 17.2 Context and scoping

- [ ] Every nontrivial step has a scope contract.
- [ ] Every nontrivial step has a context bundle.
- [ ] Prompt dispatcher includes scope/context sections in manager mode.
- [ ] Scope expansion creates decision packet or follows configured policy.
- [ ] Context budget overflow is deterministic and visible.

## 17.3 Knowledge pack management

- [ ] Knowledge packs have parseable metadata.
- [ ] Manager-mode plan writes `knowledge-plan.json`.
- [ ] Required missing packs are reported.
- [ ] Stale packs are reported.
- [ ] Pack attachment reason is explainable.
- [ ] Knowledge CLI can list/scan/audit/show packs.

## 17.4 Configurable policies

- [ ] `.claude/baton.yaml` loads and validates.
- [ ] Phase adversarial review policy works.
- [ ] Project adversarial review policy works.
- [ ] Handoff-required policy works.
- [ ] Gate policy remains configurable, not hard-coded.

## 17.5 Manager/director UX

- [ ] Planning writes `manager-brief.md`.
- [ ] Execution writes/updates `manager-report.md`.
- [ ] Decision packets are concise and actionable.
- [ ] Raw logs are not required for ordinary supervision.

---

## 18. Implementation Guidance for Codex

## 18.1 Work test-first

For each milestone:

1. Write failing tests.
2. Implement the smallest cohesive change.
3. Keep existing behavior compatible.
4. Add or update docs/examples.
5. Run targeted tests and then broader test suite.

## 18.2 Minimize execution-engine churn

Do not keep adding large blocks to `executor.py` unless there is no reasonable alternative. Prefer new manager services and narrow hooks:

```text
- planning post-processor
- context bundle lookup in dispatcher
- manager report builder called from CLI/execution boundary
- phase policy applier before execution
```

## 18.3 Preserve public protocol

Do not change `_print_action()` labels/delimiters incompatibly. Add fields only in an additive way.

## 18.4 Prefer sidecar artifacts first

Before adding many new fields to `MachinePlan`, write PMO artifacts as sidecar files and reference them via task ID/step ID.

## 18.5 Avoid model dependence in tests

Use deterministic builders and fixture repos. Do not call Claude Code in tests.

## 18.6 Do not over-index on gates

Gates belong in `policies` and `gates` config. Implement the config hooks and a basic application path, but do not make gate expansion the central milestone.

---

## 19. Suggested PR Sequence

## PR 1 — Manager config and defaults

Scope:

```text
- ManagerConfig models
- loader/validator
- default config
- tests
```

## PR 2 — Manager-mode planning sidecars: charter + scope

Scope:

```text
- ProjectCharterBuilder
- ScopeMapBuilder
- artifact writer
- plan_cmd --manager-mode wiring
- tests
```

## PR 3 — Team blueprint and role cards

Scope:

```text
- TeamBlueprintBuilder
- RoleCard writer
- workstream-owner assignment
- config review roles represented in team
- tests
```

## PR 4 — Knowledge pack metadata and knowledge plan

Scope:

```text
- pack.yaml parser
- KnowledgePlanBuilder
- missing/stale detection
- basic knowledge CLI or integration with existing command
- tests
```

## PR 5 — Context bundles and prompt injection

Scope:

```text
- ScopeContract model/writer
- ContextBundle model/writer
- PromptDispatcher manager-mode sections
- tests
```

## PR 6 — Phase/project policy applier

Scope:

```text
- adversarial review config
- handoff policy
- gate policy integration
- review-step context
- tests
```

## PR 7 — Manager reports and decision packets

Scope:

```text
- manager brief/report builder
- decision packet builder
- report CLI
- tests
```

## PR 8 — Manager-mode dry-run E2E fixture

Scope:

```text
- fixture repo
- end-to-end manager-mode planning test
- dry-run execution consumption test
```

---

## 20. Example End State

Command:

```bash
baton plan "Add a project-level reporting endpoint with tests and docs" --manager-mode --save
```

Output:

```text
Planning manager-mode project...
  Loaded .claude/baton.yaml
  Built project charter
  Built scope map: 3 workstreams
  Built team blueprint: architect, backend-engineer, test-engineer, code-reviewer
  Selected knowledge packs: repo-architecture, coding-conventions, testing-strategy, review-rubric
  Applied phase policy: adversarial_review=always, handoff_required=true, gates=project_configured
  Wrote manager brief

Artifacts:
  .claude/team-context/executions/2026-07-02-reporting-endpoint/project-charter.md
  .claude/team-context/executions/2026-07-02-reporting-endpoint/scope-map.json
  .claude/team-context/executions/2026-07-02-reporting-endpoint/team-blueprint.json
  .claude/team-context/executions/2026-07-02-reporting-endpoint/manager-brief.md

Next:
  baton execute start --task-id 2026-07-02-reporting-endpoint
  baton execute run --task-id 2026-07-02-reporting-endpoint
```

Manager brief excerpt:

```markdown
# Manager Brief

## Objective
Add a project-level reporting endpoint with tests and documentation.

## Workstreams
1. API design and route integration — owner: architect
2. Service implementation — owner: backend-engineer
3. Test and documentation coverage — owner: test-engineer

## Team
- architect: owns design, boundaries, and API contract
- backend-engineer: owns implementation within reporting/service paths
- test-engineer: owns test strategy and coverage
- code-reviewer: adversarial phase review per config

## Knowledge Packs
- repo-architecture — required
- coding-conventions — required
- testing-strategy — required
- review-rubric — required for adversarial review

## Configured Policies
- phase handoff required
- adversarial review after every phase
- project adversarial review before final completion
- gates use project-configured focused scope

## Director Decision Points
- Approve scope expansion if auth/session files are required.
- Review final manager report before merge.
```

---

## 21. Final Product Framing

This roadmap should make Agent Baton feel less like a gate runner and more like a **local AI PMO for Claude Code**.

The win condition is that a human director can delegate a medium-sized project to Baton and receive:

```text
- a scoped plan,
- a designed temporary team,
- role-specific context,
- curated knowledge packs,
- disciplined handoffs,
- configurable reviews/gates,
- and concise decisions/reports.
```

That is the support lift that makes higher-autonomy Claude Code agent teams viable.
