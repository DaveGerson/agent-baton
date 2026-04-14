# Planning Taxonomy

Universal semantic object model for Agent Baton execution plans. This
document is the authoritative reference for every concept that appears
in a `MachinePlan` and the planning process that creates it.

Use this document when you need to:
- Understand what a plan element means and why it exists
- Classify a new step, phase, or gate correctly
- Build or review plans with consistent terminology
- Extend the planner with new capabilities

---

## 1. Plan Element Hierarchy

Every execution plan is a tree of typed elements:

```
MachinePlan
  |-- PlanPhase (1..N)
  |     |-- PlanStep (1..N)
  |     |     |-- TeamMember (0..N)   # non-empty = team step
  |     |     |-- KnowledgeAttachment (0..N)
  |     |-- PlanGate (0..1)           # QA checkpoint
  |-- ForesightInsight (0..N)         # proactive gap analysis
  |-- PlanAmendment (0..N)            # runtime modifications
```

### Element Definitions

| Element | Kind | Purpose |
|---------|------|---------|
| **MachinePlan** | `plan` | Top-level contract between planner and executor. Contains task metadata, risk/budget/complexity classification, and the ordered list of phases. |
| **PlanPhase** | `phase` | Logical grouping of related steps. Each phase maps to a **Phase Archetype** (see below). Phases execute sequentially; steps within a phase may run in parallel. |
| **PlanStep** | `step` | Atomic unit of agent work. Carries a **Step Intent** (see below), agent assignment, task description, path constraints, and knowledge attachments. |
| **TeamMember** | `member` | Individual contributor within a team step. Has a role (lead, implementer, reviewer) and intra-team dependency ordering. |
| **PlanGate** | `gate` | QA checkpoint between phases. Runs automated checks (build, test, lint, spec) or requests manual review. Gate failure triggers remediation. |
| **ForesightInsight** | `foresight_insight` | A proactive observation from the planner's foresight engine. Describes a predicted gap and the preparatory step inserted to address it. |
| **PlanAmendment** | `amendment` | A runtime modification to the plan during execution, triggered by gate feedback, approval feedback, or manual intervention. |

---

## 2. Step Intents

Every plan step has a semantic **intent** that describes *why* it
exists, independent of which agent executes it.

### Core Work (directly advance the task)

| Intent | Value | Definition | Examples |
|--------|-------|------------|----------|
| **Produce** | `produce` | Create new artifacts | Write new API endpoints, create database schema, author documentation |
| **Transform** | `transform` | Modify existing artifacts | Refactor module structure, migrate from REST to GraphQL |
| **Validate** | `validate` | Verify correctness | Run pytest suite, type-check with mypy, lint with ruff |
| **Integrate** | `integrate` | Connect components | Wire frontend to new API, connect event bus subscribers |

### Support (enable core work)

| Intent | Value | Definition | Examples |
|--------|-------|------------|----------|
| **Scaffold** | `scaffold` | Set up structure, boilerplate, or tooling | Create project boilerplate, set up test infrastructure |
| **Provision** | `provision` | Prepare runtime resources | Create database tables, set up message queue |
| **Configure** | `configure` | Adjust settings or environment | Update environment variables, adjust feature flags |
| **Remediate** | `remediate` | Fix issues found by gates or reviews | Fix failing tests, address review findings |

### Governance (ensure quality and compliance)

| Intent | Value | Definition | Examples |
|--------|-------|------------|----------|
| **Review** | `review` | Human or agent review | Code review before merge, architecture review |
| **Audit** | `audit` | Compliance/security audit | Security audit of auth flow, compliance check |
| **Approve** | `approve` | Human approval checkpoint | Approve design before implementation |
| **Gate** | `gate` | Automated quality gate | Test gate between phases, lint gate |

### Foresight (proactively inserted by the planner)

| Intent | Value | Definition | Examples |
|--------|-------|------------|----------|
| **Foresight** | `foresight` | Proactive step addressing predicted gaps | Add drop-records tool before data quality run, insert schema validation before migration |

---

## 3. Phase Archetypes

Every phase name maps to a canonical **archetype** that determines
default gate selection, agent affinity, and step intent patterns.

| Archetype | Value | Definition | Typical Phase Names |
|-----------|-------|------------|---------------------|
| **Discovery** | `discovery` | Understand the problem | Research, Investigate, Analyze |
| **Design** | `design` | Produce architecture/design | Design |
| **Preparation** | `preparation` | Set up prerequisites (foresight phases are typically this) | Scaffold, Provision, Configure, Prepare: * |
| **Implementation** | `implementation` | Build the solution | Implement, Fix, Draft, Build |
| **Verification** | `verification` | Verify correctness | Test, Verify, Validate |
| **Review** | `review` | Human/agent review | Review, Audit |
| **Remediation** | `remediation` | Fix issues | Remediate, Hotfix |

---

## 4. Task Classification

Before plan creation, tasks are classified along three axes:

### Task Type

| Type | Keywords | Default Phases | Default Agents |
|------|----------|----------------|----------------|
| `new-feature` | add, build, create, implement | Design, Implement, Test, Review | architect, backend-engineer, test-engineer, code-reviewer |
| `bug-fix` | fix, bug, broken, error | Investigate, Fix, Test | backend-engineer, test-engineer |
| `refactor` | refactor, clean, reorganize | Design, Implement, Test, Review | architect, backend-engineer, test-engineer, code-reviewer |
| `data-analysis` | analyze, report, dashboard | Design, Implement, Review | architect, data-analyst |
| `documentation` | doc, readme, spec | Research, Draft, Review | architect, talent-builder, code-reviewer |
| `migration` | migrate, upgrade, move | Design, Implement, Test, Review | architect, backend-engineer, test-engineer, code-reviewer, auditor |
| `test` | test, coverage, e2e | Implement, Review | test-engineer |

### Complexity

| Level | Criteria | Agent Count | Phase Count |
|-------|----------|-------------|-------------|
| `light` | 1-3 files, single domain, simple action | 1 | 1 |
| `medium` | 3-6 files, may cross domains, moderate effort | 2-3 | 2-3 |
| `heavy` | 6+ files, multi-domain, new patterns, high risk | 3-5+ | 3-4 |

### Risk Level

| Level | Triggers | Git Strategy | Approval Gates |
|-------|----------|--------------|----------------|
| `LOW` | No risk signals detected | commit-per-agent | None |
| `MEDIUM` | migration, database, >5 agents, destructive verbs | commit-per-agent | None |
| `HIGH` | production, infrastructure, security, deploy | branch-per-agent | Design/Research phases |
| `CRITICAL` | Critical data, regulated environment | branch-per-agent | All phases |

---

## 5. Foresight System

The planner's **foresight engine** proactively analyzes the plan and
inserts preparatory phases when it detects gaps the user didn't
explicitly request but that are necessary for success.

### How Foresight Works

1. After phases are built and enriched (step 9.6 of plan creation),
   the foresight engine scans every step's description and agent
   assignment against a set of **foresight rules**.
2. When a rule matches, a new **preparation phase** is inserted
   *before* the phase that triggered the match.
3. Each insertion is recorded as a `ForesightInsight` on the plan.

### Foresight Rule Categories

| Category | Description | Example |
|----------|-------------|---------|
| `capability_gap` | Agent's toolkit is missing a needed operation | Data quality agent needs delete/drop capability for duplicates |
| `prerequisite` | A dependency must be established first | Migration needs rollback scripts before it runs |
| `edge_case` | A likely edge case needs handling | Destructive operations need dry-run and confirmation |
| `tooling` | Environment or tooling setup needed | Infrastructure changes need environment validation |

### Built-in Foresight Rules

| Rule | Trigger | Resolution |
|------|---------|------------|
| Data CRUD completeness | Data quality/processing + update keywords | Provision drop/delete operations in agent toolkit |
| Migration rollback safety | Migration keywords + data agents | Set up rollback scripts and pre-migration backups |
| API schema validation | API endpoint keywords + implementation agents | Define request/response schemas before implementation |
| Destructive operation safety | Delete/drop/truncate keywords | Add dry-run mode, confirmation, audit logging |
| Infrastructure environment | Infrastructure/deploy keywords + devops agent | Validate environment configuration and credentials |
| Integration contract | Cross-domain integration keywords | Define interface contracts between components |
| Test infrastructure | Integration/e2e test keywords + test-engineer | Set up fixtures, mocks, test databases |

### Confidence and Risk Interaction

Foresight rules have a confidence score (0.0-1.0). Higher-risk plans
lower the confidence threshold so more rules fire:

- LOW risk: threshold = 0.7
- MEDIUM risk: threshold = 0.65
- HIGH/CRITICAL risk: threshold = 0.55

---

## 6. Agent-Intent Affinity

Maps agents to the step intents they naturally fulfill. Used for
routing and plan validation.

| Agent | Natural Intents |
|-------|-----------------|
| architect | produce, review, foresight |
| backend-engineer | produce, transform, integrate, remediate |
| frontend-engineer | produce, transform, integrate |
| test-engineer | validate, scaffold |
| code-reviewer | review |
| security-reviewer | audit |
| devops-engineer | provision, configure, scaffold |
| data-engineer | produce, transform, provision |
| data-analyst | produce, validate |
| data-scientist | produce, validate |
| auditor | audit |
| visualization-expert | produce |
| subject-matter-expert | review, foresight |
| talent-builder | scaffold |

---

## 7. Budget and Execution

### Budget Tiers

| Tier | Agent Count | Use When |
|------|-------------|----------|
| `lean` | 1-2 | Light complexity, focused tasks |
| `standard` | 3-5 | Medium complexity, most tasks |
| `full` | 6-8 | Heavy complexity, multi-domain |

### Execution Modes

| Mode | Strategy | Use When |
|------|----------|----------|
| `parallel` | Independent steps run simultaneously | Steps touch separate files |
| `sequential` | Each step feeds the next | Linear dependency chain |
| `phased` | Groups of steps with gates between | Most real tasks (default) |

### Gate Types

| Type | Passes When | Blocks On |
|------|-------------|-----------|
| `build` | exit code 0 | Test failure, import error |
| `test` | exit code 0 | Test failure, coverage below threshold |
| `lint` | exit code 0 and no error markers | Lint errors (warnings OK) |
| `spec` | SpecValidator passes | Structural violations |
| `review` | Always (advisory) | Never |

---

## 8. Serialization Contract

Plans serialize to JSON via `MachinePlan.to_dict()` and render to
markdown via `MachinePlan.to_markdown()`. The JSON schema is the
contract between planner and executor.

Key serialization rules:
- Enums serialize to their `.value` string
- Timestamps use ISO 8601 format
- Optional fields omit from JSON when empty/None
- `foresight_insights` is always present (empty list if no insights)
- `PlanAmendment` records are append-only during execution
