# Governance, Knowledge, and Events

How safety, knowledge delivery, and event-driven traceability work
together to make multi-agent orchestration reliable.

---

## Overview

Agent Baton enforces safety through three cooperating subsystems:

1. **Governance** classifies risk, applies policy rules, validates agent
   definitions and output specs, manages escalations, and generates
   audit-ready compliance reports.
2. **Knowledge** resolves domain-specific documents at plan time, delivers
   them into delegation prompts, and handles runtime knowledge gaps when
   agents self-interrupt.
3. **Events** record every state change as an append-only stream that
   powers projections, dashboards, and crash recovery.

These systems compose inside the execution pipeline. The planner calls
the classifier to set the risk level, which selects a guardrail preset
(policy set). The dispatcher injects resolved knowledge into every
delegation prompt. The executor publishes events at each step, gate, and
decision point. Together they ensure that every agent action is
risk-appropriate, domain-informed, and traceable.

```
  Plan time                  Execution time
  ──────────                 ──────────────
  DataClassifier ────┐       EventBus
  PolicyEngine ──────┤       ├── step.dispatched
  KnowledgeResolver ─┤       ├── gate.required / passed / failed
  AgentRouter ───────┘       ├── human.decision_needed
                             ├── task.started / completed / failed
                             └── EventPersistence → .jsonl files
                                 └── Projections → TaskView
```

---

## Governance Subsystem

Source: `agent_baton/core/govern/`

### Risk Classification

**Module:** `classifier.py` — `DataClassifier`

The classifier scans a task description and affected file paths to
determine the risk level and matching guardrail preset. It produces a
`ClassificationResult` containing:

| Field | Type | Description |
|-------|------|-------------|
| `risk_level` | `RiskLevel` enum | LOW, MEDIUM, HIGH, or CRITICAL |
| `guardrail_preset` | string | Name of the preset to apply |
| `signals_found` | list[str] | Keywords/patterns that triggered classification |
| `confidence` | string | "high" (0 or 2+ signals) or "low" (exactly 1 signal) |
| `explanation` | string | Human-readable rationale |

#### Signal categories and their risk mappings

| Category | Example signals | Risk level | Preset |
|----------|----------------|------------|--------|
| Regulated | compliance, regulated, audit, hipaa, gdpr, sox, pci, ferpa, retention, certification | HIGH | Regulated Data |
| PII | pii, personal data, ssn, email address, credit card, patient, employee record, user data | HIGH | Regulated Data |
| Security | authentication, authorization, secrets, credentials, password, token, api key, oauth, jwt, encryption | HIGH | Security-Sensitive |
| Infrastructure | terraform, docker, kubernetes, ci/cd, pipeline, deploy, production, monitoring | HIGH | Infrastructure Changes |
| Database | migration, schema, database, table, column, index, foreign key, alter table, drop | MEDIUM | Standard Development |
| No signals | (none detected) | LOW | Standard Development |

#### File path elevation

Certain file paths elevate the risk level regardless of the task
description:

| Path pattern | Elevated preset |
|-------------|-----------------|
| `.env`, `secrets/`, `credentials`, `auth/` | Security-Sensitive |
| `docker`, `dockerfile`, `terraform`, `deploy`, `infrastructure/`, `.github/workflows` | Infrastructure Changes |
| `migrations/` | Stays MEDIUM (does not elevate to HIGH by itself) |

#### CRITICAL escalation

When three or more regulated + PII signals are found in a single task,
the risk level is promoted to CRITICAL with the Regulated Data preset.

#### Auto-detection from git

`DataClassifier.classify_from_files()` runs `git diff --name-only HEAD`
to discover changed files automatically, then feeds them into the
standard classification pipeline.

#### CLI: `baton classify`

```
baton classify "Add HIPAA audit trail to patient records"
baton classify "Refactor utility functions" --files src/auth/login.py
```

Output fields: Risk Level, Preset, Confidence, Signals, Explanation.

---

### Compliance

**Module:** `compliance.py` — `ComplianceReportGenerator`

Generates audit-ready markdown reports for regulated-data tasks. Reports
are persisted to `.claude/team-context/compliance-reports/`.

#### ComplianceReport structure

| Field | Description |
|-------|-------------|
| `task_id` | Unique task identifier |
| `task_description` | What the task does |
| `risk_level` | Assigned risk level |
| `classification` | Guardrail preset applied |
| `timestamp` | ISO 8601 generation time |
| `entries` | List of `ComplianceEntry` records (one per agent action) |
| `auditor_verdict` | SHIP, SHIP WITH NOTES, REVISE, or BLOCK |
| `auditor_notes` | Free-text auditor commentary |
| `total_gates_passed` | Count of passed QA gates |
| `total_gates_failed` | Count of failed QA gates |

#### ComplianceEntry fields

Each entry records one auditable agent action:

| Field | Description |
|-------|-------------|
| `agent_name` | Which agent performed the action |
| `action` | created, modified, or reviewed |
| `files` | Files touched |
| `business_rules_validated` | Domain rules the agent verified |
| `commit_hash` | Git commit hash (if applicable) |
| `gate_result` | PASS, FAIL, or PASS WITH NOTES |
| `notes` | Free-text agent notes |

The generated markdown includes: change log table, business rules
validated, gate summary, and per-agent notes.

#### API

```python
generator = ComplianceReportGenerator()
report = generator.generate(
    task_id="fix-audit-trail",
    task_description="Add SOX audit trail",
    risk_level="HIGH",
    classification="Regulated Data",
    entries=[entry1, entry2],
    auditor_verdict="SHIP WITH NOTES",
)
path = generator.save(report)          # writes .md file
content = generator.load("fix-audit-trail")  # reads it back
recent = generator.list_recent(5)      # last 5 reports
```

#### CLI: `baton compliance`

```
baton compliance                    # list recent reports
baton compliance --task-id FIX-123  # show specific report
baton compliance --count 10         # list last 10
```

---

### Policy Engine

**Module:** `policy.py` — `PolicyEngine`, `PolicySet`, `PolicyRule`

Encodes guardrail presets as evaluable rule sets. Policy sets can be
built-in (standard presets) or custom (JSON files in `.claude/policies/`).

#### PolicyRule fields

| Field | Values | Description |
|-------|--------|-------------|
| `name` | string | Rule identifier |
| `description` | string | Human-readable purpose |
| `scope` | `"all"`, agent name, or glob pattern | Which agents this rule applies to |
| `rule_type` | `path_block`, `path_allow`, `tool_restrict`, `require_agent`, `require_gate` | What the rule checks |
| `pattern` | file glob, tool name, agent name, or gate type | The pattern to match |
| `severity` | `block` or `warn` | Whether violation blocks or warns |

#### Rule types

| Type | Behavior |
|------|----------|
| `path_block` | Blocks an agent from writing to paths matching the pattern |
| `path_allow` | Allows writes only to paths matching the pattern (advisory) |
| `tool_restrict` | Blocks an agent from using specific tools (comma-separated) |
| `require_agent` | Requires a specific agent to be present in the execution plan |
| `require_gate` | Requires a specific QA gate to be present in the plan |

#### Five standard presets

**1. Standard Development** (`standard_dev`) — LOW risk default

| Rule | Type | Pattern | Severity |
|------|------|---------|----------|
| block_env_files | path_block | `**/.env` | block |
| block_secrets_dir | path_block | `**/secrets/**` | block |
| block_node_modules | path_block | `**/node_modules/**` | block |
| reviewers_read_only | tool_restrict | Write, Bash (scope: `*reviewer*`) | block |

**2. Data Analysis** (`data_analysis`) — LOW risk

| Rule | Type | Pattern | Severity |
|------|------|---------|----------|
| data_agents_write_output_only | path_allow | `**/output/**` (scope: `*data*`) | warn |
| block_source_data_writes | path_block | `**/data/**` | block |
| require_pii_masking | require_gate | pii_masking | warn |

**3. Infrastructure Changes** (`infrastructure`) — HIGH risk

| Rule | Type | Pattern | Severity |
|------|------|---------|----------|
| only_devops_writes_infra | path_block | `**/terraform/**` | block |
| block_dockerfile_writes | path_block | `**/Dockerfile*` | block |
| block_cicd_writes | path_block | `**/.github/workflows/**` | block |
| require_auditor | require_agent | auditor | block |
| require_rollback_plan | require_gate | rollback_plan | block |

**4. Regulated Data** (`regulated`) — HIGH/CRITICAL risk

| Rule | Type | Pattern | Severity |
|------|------|---------|----------|
| require_sme | require_agent | subject-matter-expert | block |
| require_auditor | require_agent | auditor | block |
| no_bash_on_data | tool_restrict | Bash | block |
| append_only_historical | require_gate | append_only | block |
| require_audit_trail | require_gate | audit_trail | block |

**5. Security-Sensitive** (`security`) — HIGH risk

| Rule | Type | Pattern | Severity |
|------|------|---------|----------|
| require_auditor | require_agent | auditor | block |
| require_security_reviewer | require_agent | security-reviewer | block |
| block_auth_writes_non_implementing | path_block | `**/auth/**` | block |
| no_hardcoded_credentials | require_gate | no_hardcoded_credentials | block |

#### Policy evaluation

```python
engine = PolicyEngine()
preset = engine.load_preset("security")
violations = engine.evaluate(
    preset,
    agent_name="code-reviewer",
    allowed_paths=["src/auth/login.py"],
    tools=["Read", "Write"],
)
# violations is a list of PolicyViolation objects
```

Evaluation checks each rule's scope against the agent name (using
fnmatch glob), then applies the rule type logic. `require_agent` and
`require_gate` rules always surface as violations to remind the
orchestrator to include them in the plan.

#### Custom policies

Save custom policy sets as JSON in `.claude/policies/`:

```json
{
  "name": "my_custom_policy",
  "description": "Custom guardrails for my project",
  "rules": [
    {
      "name": "block_legacy_dir",
      "description": "Block all writes to legacy/",
      "scope": "all",
      "rule_type": "path_block",
      "pattern": "**/legacy/**",
      "severity": "block"
    }
  ]
}
```

On-disk policies take precedence over built-in presets with the same
name.

#### CLI: `baton policy`

```
baton policy                                 # list all presets
baton policy --show security                 # show rules in a preset
baton policy --check backend-engineer \
    --preset security \
    --paths "src/auth/login.py" \
    --tools "Read,Write"                     # evaluate an agent
```

---

### Spec Validation

**Module:** `spec_validator.py` — `SpecValidator`

Validates agent output against specifications. Four validation modes:

#### 1. JSON Schema validation

```python
validator = SpecValidator()
result = validator.validate_json_against_schema(
    data_path=Path("output.json"),
    schema_path=Path("schema.json"),
)
```

Supports: type checking (string, number, integer, boolean, array,
object, null), required fields, enum values, nested properties, array
items. Does NOT support: `$ref`, `allOf`/`anyOf`/`oneOf`, `pattern`,
`format`.

#### 2. File structure validation

```python
result = validator.validate_file_structure(
    root=Path("src/"),
    expected_files=["models.py", "views.py", "tests/test_models.py"],
)
```

Checks that expected files exist under a root directory.

#### 3. Python export validation

```python
result = validator.validate_exports(
    module_path=Path("src/models.py"),
    expected_names=["UserModel", "create_user", "DEFAULT_ROLE"],
)
```

Text-based (no import): scans for `def`, `async def`, `class`, and
top-level assignment patterns.

#### 4. API contract validation

```python
result = validator.validate_api_contract(
    implementation_path=Path("src/service.py"),
    contract={
        "functions": ["process_order"],
        "classes": ["OrderService"],
        "methods": {"OrderService": ["validate", "submit"]},
    },
)
```

#### 5. Generic gate runner

```python
result = validator.run_gate([
    ("database_connected", lambda: (True, "Connected")),
    ("schema_valid", lambda: (False, "Missing column 'status'")),
])
```

All modes produce a `SpecValidationResult` with a list of `SpecCheck`
objects (name, passed, expected, actual, message) and a `.summary`
property (e.g., "3/4 checks passed").

#### CLI: `baton spec-check`

```
baton spec-check --json data.json --schema schema.json
baton spec-check --files src/ --expect "models.py,views.py"
baton spec-check --exports src/models.py --expect "UserModel,create_user"
```

---

### Agent Validation

**Module:** `validator.py` — `AgentValidator`

Validates agent definition `.md` files for format correctness. Returns
`ValidationResult` with blocking errors and non-blocking warnings.

#### Error checks (blocking)

| Check | Rule |
|-------|------|
| Frontmatter exists | File must start with `---` and have a closing `---` |
| Valid YAML | Frontmatter must parse as valid YAML |
| `name` required | Must be a non-empty string in kebab-case (with optional `--flavor` suffix) |
| `description` required | Must be a non-empty string |
| `model` valid | If present, must be one of: `opus`, `sonnet`, `haiku` |
| `permissionMode` valid | If present, must be `auto-edit` or `default` |
| `tools` valid | If present, each tool must be a known tool or match the MCP pattern `mcp__<server>__<tool>` |
| Body not empty | Markdown body after frontmatter must not be empty |

Valid tools: Read, Write, Edit, Glob, Grep, Bash, NotebookEdit,
WebFetch, WebSearch, Agent. MCP tools matching `mcp__<server>__<tool>`
are accepted without being in the static list.

#### Warning checks (non-blocking)

| Check | Guidance |
|-------|----------|
| Multi-line description | Should have 2+ lines for better trigger matching |
| Name matches filename | Agent name should match the `.md` file stem |
| Reviewer permissions | Reviewer/auditor agents should use `permissionMode: default`, not `auto-edit` |
| Model present | The `model` field should be included |
| Top-level heading | Markdown body should contain a `# ...` heading |

#### CLI: `baton validate`

```
baton validate agents/                   # validate all .md files in directory
baton validate agents/backend-engineer.md  # validate single file
baton validate agents/ --strict          # treat warnings as errors
```

---

### Escalation

**Module:** `escalation.py` — `EscalationManager`

**Model:** `models/escalation.py` — `Escalation`

Manages agent questions that need human answers. Escalations are
persisted to `.claude/team-context/escalations.md` as a structured
markdown file.

#### Escalation fields

| Field | Type | Description |
|-------|------|-------------|
| `agent_name` | string | Agent that raised the escalation |
| `question` | string | The specific question |
| `context` | string | Background information |
| `options` | list[str] | Suggested answer choices |
| `priority` | string | `"blocking"` (halts execution) or `"normal"` (advisory) |
| `timestamp` | string | ISO 8601 creation time (auto-populated) |
| `resolved` | bool | Whether the human has answered |
| `answer` | string | The human's response |

#### API

```python
manager = EscalationManager()
manager.add(Escalation(
    agent_name="backend-engineer",
    question="Should we use append-only or soft-delete for audit records?",
    context="SOX compliance requires immutable audit trails",
    options=["append-only", "soft-delete"],
    priority="blocking",
))
pending = manager.get_pending()           # unresolved only
all_escs = manager.get_all()              # resolved + unresolved
manager.resolve("backend-engineer", "append-only")
manager.clear_resolved()                  # remove answered escalations
```

#### CLI: `baton escalations`

```
baton escalations                         # show pending
baton escalations --all                   # show all (including resolved)
baton escalations --resolve backend-engineer "append-only"
baton escalations --clear                 # remove resolved entries
```

---

### Guardrail Presets Reference

The reference document `references/guardrail-presets.md` provides the
human-readable specification that the code-level policy presets
implement. Key additions beyond what the `PolicyEngine` enforces:

**Trust Levels** — the auditor assigns one of four trust levels per
agent in a Permission Manifest:

| Level | permissionMode | Meaning |
|-------|---------------|---------|
| Full Autonomy | `auto-edit` | Works freely within path boundaries |
| Supervised | `auto-edit` + checkpoint | Auditor verifies output before handoff |
| Restricted | `default` | Must request approval for each write |
| Plan Only | read-only tools | Reads and proposes but cannot execute |

**Per-Agent Boundary Template** — included in every delegation prompt
when guardrails are active:

```
BOUNDARIES:
- ALLOWED: [file paths/patterns this agent may write]
- BLOCKED: [file paths/patterns off-limits]
- TOOLS: [restricted tool list]
- SPECIAL: [domain-specific requirements]
```

---

### CLI: `baton detect`

Detects the project's technology stack by scanning for package manager
files, framework config, and dependency lists:

```
baton detect              # detect stack in current directory
baton detect --path /path/to/project
```

Output: Language, Framework, and the signal files that triggered
detection.

---

## Agent Orchestration

Source: `agent_baton/core/orchestration/`

### Agent Registry

**Module:** `registry.py` — `AgentRegistry`

Loads agent definitions from markdown files, indexes them by name, and
provides lookup methods for the router and planner.

#### Discovery precedence

1. **Global agents** — `~/.claude/agents/*.md`
2. **Project agents** — `.claude/agents/*.md` (override global on name collision)

#### Key API

| Method | Description |
|--------|-------------|
| `load_default_paths()` | Load from global then project directories |
| `load_directory(dir, override=False)` | Load all `.md` files from a directory |
| `get(name)` | Exact lookup by agent name |
| `get_flavors(base_name)` | All flavored variants (e.g., `backend-engineer--python`) |
| `get_base(name)` | Base agent for a given name or flavored name |
| `find_best_match(base, flavor)` | Exact flavor > base fallback |
| `by_category(category)` | All agents in a functional category |

#### Agent categories

| Category | Base names |
|----------|-----------|
| Engineering | architect, backend-engineer, frontend-engineer, devops-engineer, test-engineer, data-engineer |
| Data & Analytics | data-scientist, data-analyst, visualization-expert |
| Domain | subject-matter-expert |
| Review & Governance | security-reviewer, code-reviewer, auditor |
| Meta | talent-builder, orchestrator |

#### CLI: `baton agents`

```
baton agents    # list all agents grouped by category
```

---

### Agent Router

**Module:** `router.py` — `AgentRouter`

Detects the project's technology stack and routes abstract roles to
concrete agent flavors.

#### Stack detection

Scans up to two directory levels for:

**Package signals** (strongest — define the language):

| File | Language |
|------|----------|
| `package.json` | JavaScript |
| `tsconfig.json` | TypeScript |
| `pyproject.toml`, `requirements.txt`, `setup.py` | Python |
| `go.mod` | Go |
| `Cargo.toml` | Rust |
| `Gemfile` | Ruby |
| `build.gradle`, `pom.xml` | Java |
| `*.csproj`, `*.sln` | C# |

**Framework signals** (refine the stack):

| File | Framework |
|------|-----------|
| `next.config.*` | React (Next.js) |
| `nuxt.config.*` | Vue (Nuxt) |
| `angular.json` | Angular |
| `svelte.config.js` | Svelte |
| `appsettings.json` | .NET (ASP.NET Core) |
| `manage.py`, `wsgi.py` | Django |
| `vite.config.*` + `react` in package.json | React (Vite) |

Root-level signals take priority over subdirectory signals. TypeScript
overrides JavaScript when both are detected.

#### Flavor mapping

| Stack | Flavor assignments |
|-------|--------------------|
| Python (any framework) | `backend-engineer` -> `backend-engineer--python` |
| JavaScript/TypeScript + React | `frontend-engineer` -> `frontend-engineer--react`, `backend-engineer` -> `backend-engineer--node` |
| JavaScript/TypeScript (no framework) | `backend-engineer` -> `backend-engineer--node` |
| C# + .NET | `frontend-engineer` -> `frontend-engineer--dotnet` |

#### Routing logic

1. Detect the stack (or accept a pre-detected `StackProfile`)
2. Look up the flavor map for (language, framework)
3. Check if the flavored agent exists in the registry
4. If yes, return the flavored name; if no, fall back to the base name

#### CLI: `baton route`

```
baton route backend-engineer frontend-engineer
baton route --path /path/to/project backend-engineer
```

---

### Context Manager

**Module:** `context.py` — `ContextManager`

Manages the `.claude/team-context/` directory structure, providing
read/write access to plans, shared context, mission logs, and codebase
profiles.

#### Directory layout

```
.claude/team-context/
  executions/<task-id>/       # task-scoped files
    plan.json                 # machine-readable execution plan
    plan.md                   # human-readable execution plan
    context.md                # shared project context for agents
    mission-log.md            # timestamped record of agent completions
  shared/
    codebase-profile.md       # cached codebase research (shared across tasks)
  active-task-id.txt          # pointer to the default task
```

When `task_id` is `None`, falls back to a legacy flat layout (all files
in the root).

#### Context document sections

The shared context document (`context.md`) contains:

- **Stack** — detected language/framework
- **Architecture** — project structure notes
- **Conventions** — coding style and patterns
- **Domain Context** — business-specific context (optional)
- **Guardrails** — which preset is active
- **Agent Assignments** — who is doing what

#### Key API

| Method | Description |
|--------|-------------|
| `write_plan(plan)` | Write both `.md` and `.json` plan files |
| `write_context(...)` | Write the shared context document |
| `init_mission_log(task, risk_level)` | Start a new mission log |
| `append_to_mission_log(entry)` | Add a timestamped entry |
| `write_profile(content)` | Write the codebase profile cache |
| `recovery_files_exist()` | Check which files exist for session resumption |
| `list_task_ids(root)` | List all task IDs with execution directories |

---

## Knowledge System

Source: `agent_baton/core/orchestration/knowledge_registry.py`,
`agent_baton/core/engine/knowledge_resolver.py`,
`agent_baton/core/engine/knowledge_gap.py`,
`agent_baton/models/knowledge.py`

### Knowledge Data Models

**Module:** `models/knowledge.py`

| Model | Purpose |
|-------|---------|
| `KnowledgeDocument` | A single knowledge document within a pack |
| `KnowledgePack` | A curated collection of related documents |
| `KnowledgeAttachment` | A resolved document attached to a plan step |
| `KnowledgeGapSignal` | Parsed from agent output when they self-interrupt |
| `KnowledgeGapRecord` | Persisted gap record for the feedback loop |
| `ResolvedDecision` | A resolved knowledge gap injected on re-dispatch |

#### KnowledgeDocument fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Document identifier |
| `description` | string | What this document covers |
| `source_path` | Path | Filesystem path to the `.md` file |
| `content` | string | Empty at index time (lazy-loaded on demand) |
| `tags` | list[str] | Searchable keywords |
| `grounding` | string | Agent-facing context string injected in the prompt |
| `priority` | string | `high`, `normal`, or `low` |
| `token_estimate` | int | Estimated tokens (chars / 4 heuristic) |

#### KnowledgePack fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Pack identifier |
| `description` | string | What this pack covers |
| `source_path` | Path | Directory path |
| `tags` | list[str] | Searchable keywords |
| `target_agents` | list[str] | Agents this pack is auto-attached to |
| `default_delivery` | string | `inline` or `reference` |
| `documents` | list[KnowledgeDocument] | The documents in this pack |

#### KnowledgeAttachment fields

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | How it was resolved (see resolution sources below) |
| `pack_name` | string or None | Parent pack name |
| `document_name` | string | Document identifier |
| `path` | string | Filesystem path |
| `delivery` | string | `inline` (embedded in prompt) or `reference` (path for agent to read) |
| `retrieval` | string | `file` or `mcp-rag` |
| `grounding` | string | Agent-facing context string |
| `token_estimate` | int | Estimated tokens |

Attachment sources: `explicit`, `agent-declared`,
`planner-matched:tag`, `planner-matched:relevance`, `gap-suggested`.

---

### Knowledge Registry

**Module:** `knowledge_registry.py` — `KnowledgeRegistry`

Loads, indexes, and queries knowledge packs from directory trees.
Document content is NOT loaded at index time (lazy, on-demand).

#### Discovery precedence

1. **Global packs** — `~/.claude/knowledge/*/`
2. **Project packs** — `.claude/knowledge/*/` (override global by name)

#### Pack directory structure

```
.claude/knowledge/
  <pack-name>/
    knowledge.yaml       # manifest (optional but recommended)
    document-a.md        # knowledge document with frontmatter
    document-b.md        # knowledge document with frontmatter
```

#### Manifest format (`knowledge.yaml`)

```yaml
name: agent-baton
description: Architecture and conventions for the agent-baton project
tags: [orchestration, architecture, development]
target_agents: [backend-engineer--python, architect]
default_delivery: reference
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No (falls back to directory name) | Pack identifier |
| `description` | No | What this pack covers |
| `tags` | No | Keywords for matching |
| `target_agents` | No | Agents that auto-receive this pack |
| `default_delivery` | No (default: `reference`) | `inline` or `reference` |

#### Document frontmatter format

```yaml
---
name: architecture
description: Package layout, key classes, and design principles
tags: [architecture, package-layout, design]
priority: high
grounding: "You are receiving the architecture overview for the agent-baton project."
---

# Document content here...
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No (falls back to filename stem) | Document identifier |
| `description` | No | What this document covers |
| `tags` | No | Searchable keywords |
| `priority` | No (default: `normal`) | `high`, `normal`, or `low` — controls delivery order |
| `grounding` | No | Context string injected into the agent prompt |

#### Querying

| Method | Description |
|--------|-------------|
| `get_pack(name)` | Exact pack lookup |
| `get_document(pack_name, doc_name)` | Exact document lookup |
| `packs_for_agent(agent_name)` | Packs listing the agent in `target_agents` |
| `find_by_tags(tags)` | Documents with overlapping tags (intersection match) |
| `search(query, limit=10)` | TF-IDF relevance search over metadata |

#### TF-IDF search

The registry maintains an in-memory TF-IDF index over the concatenated
metadata of all (pack, doc) pairs: pack name + description + tags + doc
name + description + tags. Uses `collections.Counter` — no external
dependencies. IDF formula: `log(N / df) + 1`. Results above a 0.3
threshold are returned sorted by score.

---

### Knowledge Resolution

**Module:** `knowledge_resolver.py` — `KnowledgeResolver`

The resolver is the orchestration point between the registry and the
dispatcher. Given a plan step's context, it produces
`KnowledgeAttachment` objects with delivery decisions.

#### 4-layer resolution pipeline

The resolver runs four layers in order. Documents seen in earlier layers
are skipped in later layers (deduplication by source path, falling back
to pack name + doc name).

| Layer | Name | Input | Description |
|-------|------|-------|-------------|
| 1 | Explicit | `--knowledge` and `--knowledge-pack` CLI args | User-supplied pack names and document file paths |
| 2 | Agent-declared | Agent's `knowledge_packs` frontmatter field | Packs the agent definition says it always needs |
| 3 | Planner-matched (tag) | Keywords from task description + task type | Strict tag intersection match via `registry.find_by_tags()` |
| 4 | Planner-matched (relevance) | Task description + task type | TF-IDF fallback — only fires when Layer 3 returns nothing |

Within each layer, documents are sorted by priority: high > normal > low.

#### Delivery decisions

Each attachment gets an inline or reference delivery based on token
budget:

| Condition | Delivery | Rationale |
|-----------|----------|-----------|
| `token_estimate <= 0` | reference | Cannot estimate — too risky to inline |
| `token_estimate > doc_token_cap` (default: 8,000) | reference | Too large for inline |
| `token_estimate <= remaining_budget` | inline | Fits within step budget |
| Budget exhausted | reference | Already used the step's inline budget |

Default step token budget: 32,000 tokens.

When `rag_available=True`, reference deliveries get `retrieval="mcp-rag"`
instead of `"file"`, hinting that the agent should use an MCP RAG
server to retrieve content.

#### Usage

```python
resolver = KnowledgeResolver(
    registry=knowledge_registry,
    agent_registry=agent_registry,
    rag_available=False,
    step_token_budget=32_000,
    doc_token_cap=8_000,
)
attachments = resolver.resolve(
    agent_name="backend-engineer--python",
    task_description="Add SOX audit trail to compliance module",
    task_type="feature",
    risk_level="HIGH",
    explicit_packs=["compliance"],
    explicit_docs=["docs/audit-rules.md"],
)
```

---

### Knowledge Gap Detection

**Module:** `knowledge_gap.py` — `parse_knowledge_gap()`,
`determine_escalation()`

Handles the runtime knowledge acquisition protocol. When an agent
encounters a knowledge gap during execution, it self-interrupts by
including a structured signal in its output.

#### Signal format

Agents output this in their outcome text when they lack context:

```
KNOWLEDGE_GAP: Need context on SOX audit trail requirements
CONFIDENCE: none
TYPE: contextual
```

| Field | Values | Default |
|-------|--------|---------|
| `KNOWLEDGE_GAP` | Free-text description | (required) |
| `CONFIDENCE` | `none`, `low`, `partial` | `low` |
| `TYPE` | `factual`, `contextual` | `factual` |

**Factual gaps** are about verifiable information (data schemas, API
endpoints, configuration values) — potentially resolvable from
knowledge packs automatically.

**Contextual gaps** are about business decisions, organizational
preferences, or domain judgment — always require human input.

#### Parsing

```python
from agent_baton.core.engine.knowledge_gap import parse_knowledge_gap

signal = parse_knowledge_gap(
    outcome="I need more info.\nKNOWLEDGE_GAP: SOX requirements\nCONFIDENCE: none\nTYPE: contextual",
    step_id="step-1",
    agent_name="backend-engineer",
)
# Returns KnowledgeGapSignal or None if no KNOWLEDGE_GAP line found
```

#### Escalation matrix

After parsing a gap signal, `determine_escalation()` decides what
action to take based on the gap type, whether matching knowledge was
found, and the plan's risk/intervention levels:

| Gap type | Resolution found? | Risk x Intervention | Action |
|----------|-------------------|---------------------|--------|
| factual | yes | any | **auto-resolve** |
| factual | no | LOW + low intervention | **best-effort** (log and continue) |
| factual | no | LOW + medium/high intervention | **queue-for-gate** (pause for human) |
| factual | no | MEDIUM+ (any intervention) | **queue-for-gate** |
| contextual | (any) | any | **queue-for-gate** |

Actions:
- **auto-resolve** — the matched knowledge is injected into the
  re-dispatch prompt as a `ResolvedDecision`
- **best-effort** — logged but execution continues (LOW risk, low
  intervention only)
- **queue-for-gate** — execution pauses; the gap appears in the
  dashboard or `baton status` for human resolution

---

### Knowledge Delivery

**Module:** `engine/dispatcher.py` — `PromptDispatcher`

The dispatcher injects resolved knowledge into delegation prompts in two
sections:

#### Inline delivery

Documents with `delivery="inline"` are rendered under a
`## Knowledge Context` heading with their full content loaded lazily
from `source_path`:

```
## Knowledge Context

### architecture (agent-baton)
You are receiving the architecture overview for the agent-baton project.

[full document content here]
```

#### Reference delivery

Documents with `delivery="reference"` are listed under
`## Knowledge References` with a retrieval hint:

```
## Knowledge References

- **data-model** (systems): Data model for the operations database
  Read: .claude/knowledge/systems/data-model.md
```

#### Knowledge gap instructions

Every delegation prompt includes a `## Knowledge Gaps` block instructing
agents on the self-interrupt protocol:

```
## Knowledge Gaps

If you lack sufficient context to complete this task correctly:
- Output `KNOWLEDGE_GAP: <description>` with what you need
- Include `CONFIDENCE: none | low | partial` and `TYPE: factual | contextual`
- Stop and report your partial progress

Do not guess through gaps on HIGH/CRITICAL risk tasks.
Resolved decisions (provided above) are final — do not revisit them.
```

---

### Knowledge Architecture

The reference document `references/knowledge-architecture.md` describes
the four-layer knowledge model:

| Layer | Name | Cost | Best for |
|-------|------|------|----------|
| 1 | Baked-in (system prompt) | Zero — always in context | Stable domain knowledge: regulations, terminology, procedures |
| 2 | Reference packs (files) | ~1-5K tokens per file | Data models, API docs, compliance checklists |
| 3 | Skills (procedures + assets) | Variable | Repeatable workflows with templates and scripts |
| 4 | MCP servers (live tools) | Per-call | Real-time data: system status, open records, live dashboards |

Resolution order: project knowledge > global knowledge > baked-in
knowledge.

---

## Event System

Source: `agent_baton/core/events/`

### Event Model

**Module:** `models/events.py` — `Event`

Every event in the system is an `Event` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | UUID hex prefix (12 chars) |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `topic` | string | Dot-separated topic string (e.g., `step.completed`) |
| `task_id` | string | Task this event belongs to |
| `sequence` | int | Monotonic sequence number within a task |
| `payload` | dict | Topic-specific data |

Events are append-only: once published, they are never mutated.

---

### Event Bus

**Module:** `bus.py` — `EventBus`

In-process publish/subscribe with fnmatch-style glob topic routing.

#### Topic routing examples

| Pattern | Matches |
|---------|---------|
| `step.*` | `step.completed`, `step.failed`, `step.dispatched` |
| `gate.*` | `gate.required`, `gate.passed`, `gate.failed` |
| `human.*` | `human.decision_needed`, `human.decision_resolved` |
| `*` | Everything |

#### Behavior

- **Synchronous**: handlers are called immediately during `publish()`.
  No threads, no queues.
- **Auto-sequencing**: if an event's `sequence` is 0, the bus
  auto-assigns the next monotonic number for that `task_id`.
- **In-memory history**: all published events are retained for replay.

#### API

```python
bus = EventBus()

# Subscribe
sub_id = bus.subscribe("step.*", handler_fn)

# Publish
bus.publish(event)

# Replay for a task
events = bus.replay("task-123", from_seq=5, topic_pattern="gate.*")

# History
all_events = bus.history(limit=50)

# Unsubscribe
bus.unsubscribe(sub_id)
```

---

### Domain Events

**Module:** `events.py` — factory functions

Each domain event is created via a factory function that produces a
properly-typed `Event` with the correct topic and payload.

#### Step lifecycle events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `step_dispatched()` | `step.dispatched` | step_id, agent_name, model |
| `step_completed()` | `step.completed` | step_id, agent_name, outcome, files_changed, commit_hash, duration_seconds, estimated_tokens |
| `step_failed()` | `step.failed` | step_id, agent_name, error, duration_seconds |

#### Gate events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `gate_required()` | `gate.required` | phase_id, gate_type, command |
| `gate_passed()` | `gate.passed` | phase_id, gate_type, output |
| `gate_failed()` | `gate.failed` | phase_id, gate_type, output |

#### Human decision events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `human_decision_needed()` | `human.decision_needed` | request_id, decision_type, summary, options, context_files |
| `human_decision_resolved()` | `human.decision_resolved` | request_id, chosen_option, rationale, resolved_by |

#### Task lifecycle events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `task_started()` | `task.started` | task_summary, risk_level, total_steps |
| `task_completed()` | `task.completed` | steps_completed, gates_passed, elapsed_seconds |
| `task_failed()` | `task.failed` | reason, failed_step_id |

#### Phase lifecycle events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `phase_started()` | `phase.started` | phase_id, phase_name, step_count |
| `phase_completed()` | `phase.completed` | phase_id, phase_name |

#### Approval events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `approval_required()` | `approval.required` | phase_id, phase_name, description |
| `approval_resolved()` | `approval.resolved` | phase_id, result, feedback |

#### Plan amendment events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `plan_amended()` | `plan.amended` | amendment_id, description, trigger, phases_added, steps_added |

#### Team step events

| Factory | Topic | Key payload fields |
|---------|-------|-------------------|
| `team_member_completed()` | `team.member_completed` | step_id, member_id, agent_name, outcome |

---

### Event Persistence

**Module:** `persistence.py` — `EventPersistence`

Append-only JSONL event log per task. Each task's events are stored in
a separate `.jsonl` file under `.claude/team-context/events/`.

File naming: task ID is sanitized (non-alphanumeric characters replaced
with `-`) and used as the filename with `.jsonl` extension.

#### Storage format

Each line is a single JSON object representing one `Event`:

```json
{"event_id":"abc123def456","timestamp":"2026-03-24T10:30:00+00:00","topic":"step.completed","task_id":"fix-audit","sequence":3,"payload":{"step_id":"step-1","agent_name":"backend-engineer","outcome":"Added audit trail"}}
```

#### API

```python
persistence = EventPersistence()

# Write
persistence.append(event)               # appends to task's .jsonl file

# Read
events = persistence.read("task-123")   # all events for a task
events = persistence.read("task-123", from_seq=5)  # from sequence 5+
events = persistence.read("task-123", topic_pattern="gate.*")
last_5 = persistence.read_last("task-123", n=5)

# Discovery
task_ids = persistence.list_task_ids()   # all tasks with event logs
count = persistence.event_count("task-123")

# Cleanup
persistence.delete("task-123")          # remove a task's event log
```

#### Wiring as a bus subscriber

The persistence layer is independent of the `EventBus`. It can be wired
as a subscriber for automatic durability:

```python
bus = EventBus()
persistence = EventPersistence()
bus.subscribe("*", persistence.append)   # persist all events
```

---

### Projections

**Module:** `projections.py` — `TaskView`, `PhaseView`, `StepView`,
`project_task_view()`

Materialized views derived from event streams. Projections consume a
list of `Event` objects (from the bus or from disk replay) and produce
summary structures. They are read-only and never mutate events.

#### TaskView

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Task identifier |
| `status` | string | started, running, completed, failed |
| `started_at` / `completed_at` | string | ISO timestamps |
| `risk_level` | string | From task.started payload |
| `total_steps` | int | Planned step count |
| `steps_completed` | int | Derived from step events |
| `steps_failed` | int | Derived from step events |
| `steps_dispatched` | int | Currently in-flight |
| `gates_passed` / `gates_failed` | int | Gate outcome counts |
| `elapsed_seconds` | float | Total execution time |
| `phases` | dict[int, PhaseView] | Phase-level detail |
| `pending_decisions` | list[str] | Unresolved human decision request IDs |
| `last_event_seq` | int | Highest sequence number processed |

#### PhaseView

| Field | Description |
|-------|-------------|
| `phase_id` | Phase index |
| `phase_name` | Human-readable name |
| `status` | pending, running, gate_pending, completed, failed |
| `steps` | dict[str, StepView] — steps within this phase |
| `gate_status` | "", required, passed, failed |
| `gate_output` | Gate command output |

#### StepView

| Field | Description |
|-------|-------------|
| `step_id` | Step identifier |
| `agent_name` | Assigned agent |
| `status` | pending, dispatched, completed, failed |
| `duration_seconds` | Execution time |
| `outcome` | Agent's outcome summary |
| `error` | Error message (if failed) |
| `files_changed` | Files the agent modified |
| `commit_hash` | Git commit hash |

#### Building a projection

```python
from agent_baton.core.events.projections import project_task_view

events = persistence.read("task-123")
view = project_task_view(events, task_id="task-123")

print(view.status)           # "completed"
print(view.steps_completed)  # 5
print(view.gates_passed)     # 2
```

#### CLI: `baton events`

```
baton events --list-tasks                  # list all task IDs with event counts
baton events --task fix-audit              # show events as a table
baton events --task fix-audit --last 10    # last 10 events
baton events --task fix-audit --topic "gate.*"  # filter by topic
baton events --task fix-audit --json       # output as JSON
baton events --task fix-audit --summary    # projected TaskView summary
```

---

## Integration

### How governance plugs into the execution pipeline

```
baton plan "task"
  │
  ├── DataClassifier.classify(description, files)
  │     → ClassificationResult (risk_level, guardrail_preset)
  │
  ├── PolicyEngine.load_preset(preset_name)
  │     → PolicySet (rules for the selected preset)
  │
  ├── KnowledgeResolver.resolve(...)
  │     → KnowledgeAttachment[] (per step)
  │
  └── AgentRouter.route(role, stack)
        → Concrete agent name

baton execute start
  │
  ├── EventBus.publish(task_started)
  │
  └── Loop:
        │
        ├── DISPATCH action
        │     ├── PromptDispatcher builds delegation prompt
        │     │     ├── Injects knowledge (inline/reference)
        │     │     ├── Injects boundaries from PolicySet
        │     │     └── Includes KNOWLEDGE_GAP instructions
        │     ├── EventBus.publish(step_dispatched)
        │     └── On agent completion:
        │           ├── parse_knowledge_gap(outcome) → gap signal?
        │           │     └── determine_escalation() → action
        │           └── EventBus.publish(step_completed | step_failed)
        │
        ├── GATE action
        │     ├── GateRunner.evaluate_output(gate, output, exit_code)
        │     └── EventBus.publish(gate_passed | gate_failed)
        │
        ├── APPROVAL action
        │     ├── EventBus.publish(approval_required)
        │     └── EventBus.publish(approval_resolved)
        │
        └── COMPLETE
              ├── EventBus.publish(task_completed)
              └── ComplianceReportGenerator.generate() [if HIGH/CRITICAL]
```

### How events enable recovery

If a session crashes mid-execution:

1. `baton execute resume` reads the saved execution state from
   `execution-state.json`
2. The event persistence layer replays events from disk via
   `EventPersistence.read(task_id)`
3. `project_task_view()` rebuilds the full `TaskView` from the event
   stream
4. Execution continues from the last completed step

---

## Configuration

### Policy files

Store custom policies as JSON in `.claude/policies/`:

```
.claude/policies/
  my-custom-policy.json      # custom PolicySet
```

On-disk policies override built-in presets with the same name. Built-in
presets are always available as fallbacks.

### Knowledge pack configuration

Knowledge packs live in `.claude/knowledge/` (project-level) or
`~/.claude/knowledge/` (global). Each pack is a directory containing a
`knowledge.yaml` manifest and `.md` document files.

Project packs override global packs with the same name.

### Event log storage

Event logs are stored in `.claude/team-context/events/` as `.jsonl`
files, one per task. No configuration is needed — the persistence layer
creates the directory on first write.

### Compliance reports

Compliance reports are stored in
`.claude/team-context/compliance-reports/` as `.md` files, one per task.

### Escalation file

Escalations are stored in `.claude/team-context/escalations.md` as a
structured markdown file with `---` separators between entries.

---

## Creating Custom Knowledge Packs

### Step 1: Create the pack directory

```bash
mkdir -p .claude/knowledge/my-domain
```

### Step 2: Write the manifest

Create `.claude/knowledge/my-domain/knowledge.yaml`:

```yaml
name: my-domain
description: Domain knowledge for the billing and invoicing system
tags: [billing, invoicing, payments, revenue]
target_agents: [backend-engineer, data-analyst]
default_delivery: reference
```

**Fields:**
- `name`: Pack identifier (defaults to directory name if omitted)
- `description`: What this pack covers (used by TF-IDF search)
- `tags`: Keywords for strict tag matching (Layer 3 resolution)
- `target_agents`: Agents that auto-receive this pack (Layer 2)
- `default_delivery`: `reference` (agent reads the file) or `inline`
  (content embedded in prompt)

### Step 3: Write knowledge documents

Create `.claude/knowledge/my-domain/data-model.md`:

```markdown
---
name: billing-data-model
description: Schema for the billing database — tables, relationships, key fields
tags: [billing, database, schema, invoicing]
priority: high
grounding: "You are receiving the billing database schema. Use these exact table and column names."
---

# Billing Data Model

## INVOICES
| Column | Type | Description |
|--------|------|-------------|
| invoice_id | UUID | Primary key |
| customer_id | UUID | FK -> CUSTOMERS |
| amount | DECIMAL | Invoice total |
| status | VARCHAR | DRAFT, SENT, PAID, OVERDUE, VOID |
...
```

**Frontmatter fields:**
- `name`: Document identifier (defaults to filename stem if omitted)
- `description`: What this document covers (used by TF-IDF and tag search)
- `tags`: Keywords for matching — overlap with task keywords triggers
  attachment
- `priority`: `high` (delivered first), `normal` (default), or `low`
  (delivered last)
- `grounding`: Context string injected into the agent's prompt alongside
  the document content or reference

### Step 4: Verify the pack loads

```bash
# The registry should find your pack:
python -c "
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
kr = KnowledgeRegistry()
count = kr.load_default_paths()
print(f'Loaded {count} packs')
pack = kr.get_pack('my-domain')
if pack:
    print(f'Pack: {pack.name} ({len(pack.documents)} docs)')
    for doc in pack.documents:
        print(f'  - {doc.name} [{doc.priority}] ~{doc.token_estimate} tokens')
"
```

### Step 5: Test resolution

```bash
# Verify that your pack resolves for the intended agent:
python -c "
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver

kr = KnowledgeRegistry()
kr.load_default_paths()
ar = AgentRegistry()
ar.load_default_paths()

resolver = KnowledgeResolver(kr, agent_registry=ar)
attachments = resolver.resolve(
    agent_name='backend-engineer',
    task_description='Fix invoice status transition from DRAFT to SENT',
    task_type='bug-fix',
)
for a in attachments:
    print(f'{a.document_name} [{a.delivery}] via {a.source}')
"
```

### Tips for effective knowledge packs

1. **Keep documents under 200 lines.** Optimize for agent consumption:
   tables, not prose.
2. **Use tags aggressively.** Tags are the primary matching mechanism.
   Include both general and specific terms.
3. **Set priority to `high`** for documents that should always be
   delivered first (schemas, core business rules).
4. **Write grounding strings** that tell the agent why it is receiving
   the document and how to use it.
5. **Use `target_agents`** for packs that a specific role always needs
   (e.g., compliance packs for the auditor).
6. **Prefer `reference` delivery** for large documents. The agent reads
   them from disk, keeping prompt size manageable.
7. **Use `inline` delivery** only for small, critical documents (under
   8,000 estimated tokens) that the agent must have immediately.

### Existing packs in this project

| Pack | Documents | Target agents |
|------|-----------|---------------|
| `agent-baton` | architecture, agent-format, development-workflow | backend-engineer--python, architect, ai-systems-architect |
| `ai-orchestration` | agent-evaluation, context-economics, multi-agent-patterns, prompt-engineering-principles | ai-systems-architect, architect, ai-product-strategist |
| `case-studies` | failure-modes, orchestration-frameworks, scaling-patterns | (none — available via tag/relevance matching) |
