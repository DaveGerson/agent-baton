# Knowledge Delivery During Plan Execution — Design

**Status:** Complete
**Date:** 2026-03-24

## Problem

Knowledge packs exist on disk (`.claude/knowledge/`) and are distributed
via the packaging system, but the execution engine never consumes them.
Agents receive generic shared context but no targeted domain knowledge.
This wastes the key value of SMEs and specialist agents — their ability
to be distinctively good at specific tasks through curated knowledge,
without polluting other agents' context windows.

Additionally, agents have no mechanism to recognize and signal knowledge
gaps at runtime. When an agent lacks sufficient context, it guesses rather
than requesting help — leading to low-quality outputs that require human
rework.

## Architecture

Layered pipeline with three components that compose in sequence:

```
KnowledgeRegistry (curated packs)  ─┐
                                     ├──→ KnowledgeResolver ──→ Dispatcher injection
MCP RAG Server (broad org knowledge) ─┘     (match + budget)      (prompt assembly)
```

- **KnowledgeRegistry** lives in `core/orchestration/` — parallel to AgentRegistry
- **KnowledgeResolver** lives in `core/engine/` — interacts with planner state, risk, budgets
- **Runtime acquisition protocol** lives in `core/engine/knowledge_gap.py` — defines gap signals and executor handling
- **Feedback loop** flows through existing `core/observe/` and `core/learn/` subsystems

## Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Binding model | Static (agent-declared) + Dynamic (planner-matched + user-explicit) | Task-specific context like PRDs and specs must be attachable dynamically |
| 2 | Granularity | Packs as standard unit; individual docs also attachable | Packs are the natural grouping, but precision matters |
| 3 | Discovery | 4-layer: explicit → agent-declared → planner-matched → plan review | All resolved at plan time, visible in plan.md for user review |
| 4 | Delivery | Tiered: small/critical inlined, larger referenced | Prevents context rot while ensuring critical knowledge is seen |
| 5 | Metadata | Pack manifest required + doc frontmatter semi-mandatory | Frontmatter serves dual purpose: planner discovery AND agent grounding |
| 6 | Schema minimalism | name + description required; everything else optional or auto-computed | Must be agentically manageable — talent-builder handles creation |
| 7 | Architecture | Layered pipeline: Registry → Resolver → Dispatcher | Mirrors existing patterns, each component independently testable |
| 8 | Planner matching | Hybrid: strict tag/keyword first, scored relevance fallback | Tags when we have them, TF-IDF/RAG when we don't |
| 9 | Token budgeting | Per-step budget (32k) + per-doc cap (8k) | Generous for Sonnet's 200k context; agents may carry detailed component-specific knowledge |
| 10 | Reference delivery | Path + summary + grounding; retrieval hint (file or mcp-rag) | Enough signal for agent to decide whether to read; extensible to RAG |
| 11 | Retro feedback | Auto-suggest matches, flag as gap-suggested in plan.md | Human reviews during plan review gate |
| 12 | Runtime acquisition | Agents self-interrupt via KNOWLEDGE_GAP signal, terminate + re-dispatch | Fits existing stateless agent model; uses amend flow |
| 13 | Escalation policy | Risk × confidence × gap-type matrix, shifted by intervention level | Balances autonomy with accuracy based on stakes |
| 14 | Intervention expectation | Plan-level --intervention low\|medium\|high, default low | User controls how much they're pulled into the loop |
| 15 | Decision finality | Resolved decisions logged in execution state, never revisited | Prevents agents from re-litigating settled questions |
| 16 | RAG integration | Gated behind runtime detection of MCP RAG server | Lights up when available, falls back to file paths and TF-IDF when not |

## Discovery Layers (resolved at plan time)

1. **Explicit** — user passes `--knowledge path/to/file.md` or `--knowledge-pack pack-name` at `baton plan` time
2. **Agent-declared** — agent frontmatter lists baseline packs via `knowledge_packs` field
3. **Planner-matched (strict)** — extract keywords from task description + type, match against registry tags
4. **Planner-matched (relevance fallback)** — if strict returned nothing, TF-IDF over metadata corpus (or RAG if available)
5. **Plan review** — plan.md shows each step's knowledge attachments with source tags; user can add/remove before execution

## Knowledge Pack Schema

### Pack manifest: `knowledge.yaml`

```yaml
# .claude/knowledge/agent-baton/knowledge.yaml
name: agent-baton
description: Architecture, conventions, and development workflow for the agent-baton project
tags: [orchestration, architecture, development]
target_agents: [backend-engineer--python, architect, ai-systems-architect]
default_delivery: reference   # inline | reference
```

### Document frontmatter (semi-mandatory — name + description required)

```yaml
---
name: context-economics
description: Token cost model and context window budgeting for multi-agent orchestration
tags: [context-window, tokens, cost, budgeting]          # optional, auto-derivable
grounding: |                                               # optional, auto-generated from pack+doc description
  You are receiving this because your task involves multi-agent coordination
  where context window costs compound. Use this to make informed decisions
  about when to spawn subagents vs. work inline.
priority: high          # optional, default: normal (high | normal | low)
---
```

- `token_estimate` — auto-computed by registry on index, never hand-maintained
- `tags` — auto-derived from content if absent
- `grounding` — if absent, resolver generates default: "You are receiving `{doc.name}` from the `{pack.name}` pack: {doc.description}"

## Data Models

### New: `agent_baton/models/knowledge.py`

```python
@dataclass
class KnowledgeDocument:
    name: str
    description: str
    source_path: Path | None = None
    content: str = ""              # loaded on demand, not at index time
    tags: list[str] = field(default_factory=list)
    grounding: str = ""            # agent-facing context
    priority: str = "normal"       # high | normal | low
    token_estimate: int = 0        # auto-computed by registry

@dataclass
class KnowledgePack:
    name: str
    description: str
    source_path: Path | None = None
    tags: list[str] = field(default_factory=list)
    target_agents: list[str] = field(default_factory=list)
    default_delivery: str = "reference"
    documents: list[KnowledgeDocument] = field(default_factory=list)

@dataclass
class KnowledgeAttachment:
    """A resolved knowledge item attached to a plan step."""
    source: str              # "explicit" | "agent-declared" | "planner-matched:tag" | "planner-matched:relevance" | "gap-suggested"
    pack_name: str | None    # None for standalone docs
    document_name: str
    path: str                # filesystem path
    delivery: str            # "inline" | "reference"
    retrieval: str = "file"  # "file" | "mcp-rag" — set by resolver based on environment
    grounding: str = ""      # agent-facing context string
    token_estimate: int = 0

@dataclass
class KnowledgeGapSignal:
    """Parsed from agent output when they self-interrupt for knowledge."""
    description: str
    confidence: str          # none | low | partial
    gap_type: str            # factual | contextual
    step_id: str
    agent_name: str
    partial_outcome: str     # work completed before the gap

@dataclass
class KnowledgeGapRecord:
    """Persisted in retrospective data for the feedback loop."""
    description: str
    gap_type: str            # factual | contextual
    resolution: str          # auto-resolved | human-answered | best-effort | unresolved
    resolution_detail: str   # pack/doc that resolved it, or the human's answer
    agent_name: str
    task_type: str | None
    task_summary: str

@dataclass
class ResolvedDecision:
    """A knowledge gap that has been answered — injected on re-dispatch as final."""
    gap_description: str
    resolution: str          # human answer or "auto-resolved via {pack_name}"
    step_id: str
    timestamp: str
```

### Relationship to existing `KnowledgeGap` model

`agent_baton/models/retrospective.py` already defines a `KnowledgeGap` dataclass with
`description`, `affected_agent`, and `suggested_fix`. The new `KnowledgeGapRecord` extends
this concept with resolution tracking, gap typing, and task context needed for the feedback
loop. During implementation, `KnowledgeGap` is **replaced** by `KnowledgeGapRecord` — the
existing model is a subset. The `Retrospective` model's `knowledge_gaps` field type changes
from `list[KnowledgeGap]` to `list[KnowledgeGapRecord]`. The `to_dict()`/`from_dict()` on
`Retrospective` must be updated accordingly. Old retrospective JSON files with the prior
schema are handled by `from_dict()` defaulting the new fields.

### Extensions to existing models

```python
# PlanStep — new field:
knowledge: list[KnowledgeAttachment] = field(default_factory=list)
# NOTE: PlanStep.to_dict() and from_dict() are hand-written — they must be
# updated to serialize/deserialize knowledge attachments. KnowledgeAttachment
# needs its own to_dict()/from_dict() pair. This is load-bearing: plan.json
# is the user-editable artifact for plan review.

# AgentDefinition — new field:
knowledge_packs: list[str] = field(default_factory=list)
# Parsed from frontmatter in registry.py alongside existing fields.

# MachinePlan — new fields:
task_type: str | None = None               # inferred task type (currently a local in create_plan, promoted to model)
explicit_knowledge_packs: list[str] = field(default_factory=list)  # from --knowledge-pack CLI arg
explicit_knowledge_docs: list[str] = field(default_factory=list)   # from --knowledge CLI arg (file paths)
intervention_level: str = "low"            # low | medium | high
# NOTE: MachinePlan.to_dict()/from_dict() are hand-written — all new fields
# must be added to both methods.

# StepStatus enum — new member:
INTERRUPTED = "interrupted"
# The executor's computed properties (dispatched_step_ids, completed_step_ids,
# failed_step_ids) on ExecutionState need a corresponding interrupted_step_ids
# property for state-machine progress tracking.
# IMPORTANT: ExecutionEngine._VALID_STEP_STATUSES whitelist in record_step_result()
# must be updated to include "interrupted" or it will raise ValueError.

# ExecutionState — new fields:
pending_gaps: list[KnowledgeGapSignal] = field(default_factory=list)
resolved_decisions: list[ResolvedDecision] = field(default_factory=list)
```

## KnowledgeRegistry

**Location:** `agent_baton/core/orchestration/knowledge_registry.py`

Parallel to AgentRegistry — stateless directory loader, in-memory index, lookup/query API.
Uses `get_pack()`/`get_document()` rather than AgentRegistry's `get()` because knowledge
has two-level addressing (pack + doc) vs. agents' flat namespace. This is a conscious
divergence from the agent pattern.

### Loading

- Scans `.claude/knowledge/` (project) and `~/.claude/knowledge/` (global)
- Project packs override global packs by name (same precedence as agents)
- Each pack directory must contain a `knowledge.yaml` manifest
- Documents are any `.md` files in the pack directory
- Document frontmatter (`name`, `description`, `tags`, `priority`) is parsed at index time; content is NOT loaded (lazy, on-demand)
- `token_estimate` is computed at index time via a fast character-based heuristic (~4 chars/token)
- Packs without `knowledge.yaml` still load with warnings (name from directory, empty metadata)
- Docs without frontmatter still load (name from filename, empty metadata)

### Public API

```python
class KnowledgeRegistry:
    def load_directory(self, directory: Path, *, override: bool = False) -> int
    def load_default_paths(self) -> int

    # Exact lookups
    def get_pack(self, name: str) -> KnowledgePack | None
    def get_document(self, pack_name: str, doc_name: str) -> KnowledgeDocument | None

    # Query — strict matching
    def packs_for_agent(self, agent_name: str) -> list[KnowledgePack]
    def find_by_tags(self, tags: set[str]) -> list[KnowledgeDocument]

    # Query — relevance fallback
    def search(self, query: str, *, limit: int = 10) -> list[tuple[KnowledgeDocument, float]]

    # Index metadata
    @property
    def all_packs(self) -> dict[str, KnowledgePack]
```

### Search implementation

The `search()` method uses TF-IDF over pack names + descriptions + tags + doc names + descriptions. Built at index time using `collections.Counter` for term frequencies. No external dependencies. Returns `(doc, score)` tuples above a 0.3 threshold. Only called when strict matching returns nothing — the resolver controls this fallback.

When an MCP RAG server is detected at runtime, the resolver bypasses `search()` and queries the RAG server instead. The built-in TF-IDF serves as the offline fallback.

## KnowledgeResolver

**Location:** `agent_baton/core/engine/knowledge_resolver.py`

The orchestration point — takes a plan step's context and produces `KnowledgeAttachment` objects with delivery decisions.

### Public API

```python
class KnowledgeResolver:
    def __init__(
        self,
        registry: KnowledgeRegistry,
        *,
        rag_available: bool = False,
        step_token_budget: int = 32_000,
        doc_token_cap: int = 8_000,
    )

    def resolve(
        self,
        *,
        agent_name: str,
        task_description: str,
        task_type: str | None = None,
        risk_level: str = "LOW",
        explicit_packs: list[str] | None = None,
        explicit_docs: list[str] | None = None,
    ) -> list[KnowledgeAttachment]
```

### Resolution pipeline

Layers execute in order. Documents seen in earlier layers are skipped (deduplication):

1. **Explicit** — resolve `explicit_packs` and `explicit_docs` by name/path. Source: `"explicit"`.
2. **Agent-declared** — look up agent's `knowledge_packs` from AgentDefinition. Source: `"agent-declared"`.
3. **Planner-matched (strict)** — extract keywords from `task_description` + `task_type`, match against registry tags. Source: `"planner-matched:tag"`.
4. **Planner-matched (relevance fallback)** — if strict returned nothing, call `registry.search()` (or RAG server if available). Only results above score threshold. Source: `"planner-matched:relevance"`.

### Delivery decision

Documents are processed in priority order (high → normal → low) within each layer, consuming the step budget:

```python
remaining_budget = 32_000

for doc in sorted_documents:
    if doc.token_estimate <= 0:
        doc.delivery = "reference"       # unestimated — don't inline unknown sizes
    elif doc.token_estimate > 8_000:
        doc.delivery = "reference"       # over per-doc cap
    elif doc.token_estimate <= remaining_budget:
        doc.delivery = "inline"          # fits budget
        remaining_budget -= doc.token_estimate
    else:
        doc.delivery = "reference"       # budget exhausted
```

### Retrieval hint

Defaults to `"file"`. When resolver is constructed with `rag_available=True`, reference deliveries get `retrieval: "mcp-rag"`. RAG availability is detected at startup by checking for a registered MCP RAG server in the install configuration.

### Runtime usage

The same `resolve()` method handles runtime gap resolution. The executor calls it with the `KNOWLEDGE_GAP` description as `task_description` and the originating agent as `agent_name`.

## Dispatcher Changes

**Location:** `agent_baton/core/engine/dispatcher.py` (modify existing)

### Prompt template additions

**Inline knowledge** — inserted between "Shared Context" and "Your Task":

```markdown
## Knowledge Context

### {doc.name} ({pack_name})
{doc.grounding}

{doc.content}
```

**Referenced knowledge** — inserted after inline knowledge:

```markdown
## Knowledge References

- **{doc.name}** ({pack_name}): {doc.description}
  Retrieve via: `Read {doc.path}`
```

When `retrieval: "mcp-rag"`:
```
  Retrieve via: query RAG server for "{doc.name}: {doc.description}"
```

RAG retrieval instructions are only rendered when an MCP RAG server is detected in the environment.

### Implementation

One new method on `PromptDispatcher`:

```python
def _build_knowledge_section(self, attachments: list[KnowledgeAttachment]) -> str
```

Called from `build_delegation_prompt()` and `build_team_delegation_prompt()`. Loads inline
doc content lazily. Returns empty string if no attachments.

**Team step knowledge:** For team steps, knowledge is resolved at the step level (shared
across all team members in the step). Each `TeamMember` receives the same knowledge
attachments. This is correct because team steps group members working on the same phase
goal — they share context. If a team member has a distinct knowledge need, it should be
a separate step, not a team member within a shared step.

### Agent metacognition block

Added to every delegation prompt, at the end of the Boundaries section:

```markdown
## Knowledge Gaps

If you lack sufficient context to complete this task correctly:
- Output `KNOWLEDGE_GAP: <description>` with what you need
- Include `CONFIDENCE: none | low | partial` and `TYPE: factual | contextual`
- Stop and report your partial progress

Do not guess through gaps on HIGH/CRITICAL risk tasks.
Resolved decisions (provided above) are final — do not revisit them.
```

## Planner Integration

**Location:** `agent_baton/core/engine/planner.py` (modify existing)

### Pipeline insertion

After step 7 (agent routing), before step 8 (data classification) — new step 7.5.

**Registry injection:** `KnowledgeRegistry` is passed to `IntelligentPlanner.__init__()` as
a new optional parameter (parallel to how `classifier` and `policy_engine` are already
injected). If `None`, knowledge resolution is skipped entirely — graceful no-op.

**RAG detection:** `_detect_rag()` checks `settings.json` for MCP server entries matching
a well-known naming convention (e.g., server name containing `rag`). Returns `bool`.
Exact detection logic is an implementation detail — the spec only requires that the
resolver receives a boolean.

```python
# Step 7.5: Resolve knowledge attachments
if self.knowledge_registry is not None:
    resolver = KnowledgeResolver(
        self.knowledge_registry,
        rag_available=self._detect_rag(),
        step_token_budget=32_000,
        doc_token_cap=8_000,
    )

    for phase in plan.phases:
        for step in phase.steps:
            step.knowledge = resolver.resolve(
                agent_name=step.agent_name,
                task_description=step.task_description,
                task_type=inferred_type,  # local variable from step 3
                risk_level=plan.risk_level,
                explicit_packs=plan.explicit_knowledge_packs,
                explicit_docs=plan.explicit_knowledge_docs,
            )
```

Note: `inferred_type` is currently a local variable in `create_plan()`. This step also
writes it to `plan.task_type` so it persists on the plan object for downstream use
(gap-suggested resolution, retrospective indexing).

### plan.md rendering

Each step shows its knowledge attachments with source tags:

```markdown
### Step 1.1 — backend-engineer--python
...
**Knowledge:**
- architecture.md (agent-baton) — inline (agent-declared)
- context-economics.md (ai-orchestration) — reference (planner-matched:tag)
- audit-checklist.md (compliance) — reference (gap-suggested)
```

This is the plan review gate (discovery layer 5) — the user can add or remove attachments
in plan.json before execution.

**Implementation note:** `MachinePlan.to_markdown()` in `models/execution.py` generates
plan.md. Update it to render `step.knowledge` attachments after existing step metadata.

### Gap-suggested attachments

After knowledge resolution, the planner queries the pattern learner for prior gap records
matching the agent + task type. This requires a **new method** on `PatternLearner`:

```python
# New method on PatternLearner (core/learn/pattern_learner.py):
def knowledge_gaps_for(
    self, agent_name: str, task_type: str | None = None
) -> list[KnowledgeGapRecord]:
    """Return prior knowledge gap records matching agent + task type.

    Reads from retrospective JSON files in .claude/team-context/retrospectives/.
    Each retro contains a knowledge_gaps list (KnowledgeGapRecord entries).
    Filters by agent_name match, and optionally task_type match.
    Returns deduplicated gaps (by description) sorted by frequency.
    """
```

**Storage path:** `KnowledgeGapRecord` entries are written as part of the `Retrospective`
model (replacing the existing `KnowledgeGap` — see Data Models section). They persist in
the same retrospective JSON files that `RetrospectiveEngine` already writes to
`.claude/team-context/retrospectives/`. The pattern learner reads these files to build its
gap index — no new storage mechanism needed.

```python
# In planner, after step 7.5:
if self.pattern_learner is not None:
    for phase in plan.phases:
        for step in phase.steps:
            prior_gaps = self.pattern_learner.knowledge_gaps_for(
                step.agent_name, plan.task_type
            )
            for gap in prior_gaps:
                matches = resolver.resolve(
                    agent_name=step.agent_name,
                    task_description=gap.description,
                )
                for match in matches:
                    match.source = "gap-suggested"
                    step.knowledge.append(match)
```

These appear in plan.md with a distinct tag so the user knows they're recommendations from prior executions.

## Runtime Knowledge Acquisition Protocol

**Location:** `agent_baton/core/engine/knowledge_gap.py` (new)

Note: `core/engine/protocols.py` is a single file (not a package), so placing this in a
`protocols/` subdirectory would shadow the existing module. Using a standalone file avoids
the conflict.

### Signal format

Agents output structured text in their outcome:

```
KNOWLEDGE_GAP: Need context on SOX audit trail requirements for financial data
CONFIDENCE: none
TYPE: contextual
```

### Executor handling

**Parsing location:** The `KNOWLEDGE_GAP` signal is parsed in `ExecutionEngine.record_step_result()`
(`core/engine/executor.py`), not in the CLI command layer. The CLI passes the raw outcome
string; the executor inspects it before recording the step result. This keeps the protocol
logic in the engine, not the CLI. Note: `ExecutionDriver` in `core/engine/protocols.py` is
the Protocol interface — its signature must also be updated if `record_step_result` gains
new return types for gap handling.

When `record_step()` receives an outcome containing `KNOWLEDGE_GAP:`, the executor:

1. Parses the signal into a `KnowledgeGapSignal`
2. Consults the escalation matrix:

| Gap type | Resolution | Risk × Intervention | Action |
|----------|-----------|---------------------|--------|
| factual | registry/RAG found match | any | Auto-resolve, amend re-dispatch step |
| factual | no match | LOW + low intervention | Proceed best-effort, log gap in trace |
| factual | no match | LOW + medium/high | Queue for next human gate |
| factual | no match | MEDIUM+ any | Queue for next human gate |
| contextual | — | any | Queue for next human gate |

3. **Auto-resolve:** calls `resolver.resolve()` with gap description, amends a re-dispatch step for the same agent with original task + partial outcome as handoff + resolved knowledge
4. **Queue for gate:** stores gap in `execution_state.pending_gaps`, surfaces at next human review gate

### Decision finality

When a gap is resolved (by auto-resolution or human answer), a `ResolvedDecision` is recorded in `execution_state.resolved_decisions`. On re-dispatch, all resolved decisions are injected into the handoff:

```markdown
## Resolved Decisions (final — do not revisit)
- "SOX audit trail requirements": Use 90-day retention with immutable append-only logs per CFO guidance
- "Event bus architecture": Auto-resolved via ai-orchestration/architecture.md
```

### Re-dispatch mechanics

The executor calls `baton execute amend` to insert a new step immediately after the interrupted one:

- Same `agent_name` as the interrupted step
- Original `task_description` + "Continue from partial progress"
- `knowledge` field populated with resolved attachments
- Handoff context: partial outcome + resolved decisions
- Interrupted step recorded as `status: "interrupted"` (new status value)

### Intervention expectation

Set via `--intervention low|medium|high` on `baton plan`, stored as `MachinePlan.intervention_level`. Shifts escalation thresholds:

- **low** (default): agents push through most gaps autonomously, only escalate on CRITICAL risk + contextual gaps
- **medium**: agents escalate on MEDIUM+ risk or when confidence is `none`
- **high**: agents escalate on any unresolved gap — closer to pair-programming mode

## CLI Changes

**Location:** `agent_baton/cli/commands/execution/plan.py` (modify existing)

### New flags on `baton plan`

```
baton plan "task description" \
    --knowledge path/to/doc.md \
    --knowledge-pack compliance \
    --intervention low
```

- **`--knowledge`** (repeatable) — explicit document file paths. Stored in `MachinePlan.explicit_knowledge_docs`. Attached globally; resolver distributes to all steps (user decided it matters).
- **`--knowledge-pack`** (repeatable) — explicit pack names. Stored in `MachinePlan.explicit_knowledge_packs`. Resolver distributes based on `target_agents`; if no target restriction, goes to all steps.
- **`--intervention`** — `low` (default), `medium`, `high`. Stored on `MachinePlan.intervention_level`.

### No new command families

Knowledge registry loading happens automatically at planner startup. No `baton knowledge` commands — the registry is an internal subsystem. Additive CLI commands (e.g., `baton knowledge list`) can be added later if needed.

## Retrospective Feedback Loop

**Location:** Integrates with existing `core/observe/retrospective.py` and `core/learn/`

### Three feedback channels

**1. Explicit gaps (from KNOWLEDGE_GAP signals):**

Every gap signaled during execution is recorded as a `KnowledgeGapRecord` in the retrospective, regardless of resolution method. This flows through the existing `core/observe/` pipeline.

**2. Implicit gaps (from retro analysis):**

The retrospective engine already captures "what went wrong" narratives. A heuristic scans retro text for knowledge-gap signals — phrases like "lacked context," "didn't know about," "assumed incorrectly." These are flagged as candidate `KnowledgeGapRecord` entries with `resolution: "unresolved"`.

**3. Gap-to-pack resolution (via pattern learner):**

The `core/learn/` pattern learner indexes gap records by `agent_name` + `task_type`. When the planner builds a future plan with a matching combo, it queries the learner and runs the matches through the resolver as `gap-suggested` attachments (see Planner Integration section).

### Self-healing cycle

Over time, early executions have more gaps. The system learns which agents need what knowledge for which task types. Later executions auto-attach the right knowledge from the start, with the user confirming via plan review.

### What it does NOT do

- Does not auto-create new knowledge packs from gaps — that's a human/talent-builder decision
- Does not modify existing packs — packs are curated artifacts
- Does not persist suggestions across projects — gap records live in the project's retro data

## Talent-Builder Scope

No new code — this is about expanding what `talent-builder` already does when creating knowledge packs.

When `talent-builder` creates a new agent + knowledge pack, it now also:

1. **Generates `knowledge.yaml` manifest** — infers `name`, `description`, `tags`, and `target_agents` from the agent definition
2. **Generates document frontmatter** — each doc gets `name`, `description`, and `tags`. `priority` defaults to `normal`. `grounding` auto-generated if not provided.

The instructions for this live in the `agent-baton/agent-format.md` knowledge doc (which talent-builder already consumes) — a "Knowledge Pack Format" section is added to that doc.

### Graceful degradation

The registry logs warnings for packs missing `knowledge.yaml` or docs missing frontmatter. It still loads them (name inferred from directory/filename, description left empty), but they won't match on planner searches. Only discoverable via explicit `--knowledge-pack` or agent-declared bindings.

## Migration

**Existing state:** 3 packs in `.claude/knowledge/` (agent-baton, ai-orchestration, case-studies), 10 docs total. No manifests, no frontmatter.

**Migration plan:**
1. Add `knowledge.yaml` to each of the 3 existing pack directories
2. Add frontmatter to each of the 10 existing docs
3. One-time manual task during implementation — small enough to do inline, no migration script

**Backwards compatibility:** Packs without manifests and docs without frontmatter still load with degraded discoverability (see Talent-Builder Scope: Graceful degradation). No breakage.

## Plan / Task / Step / Gate Taxonomy

For reference — the hierarchy used throughout this document:

```
MachinePlan (the whole job)
├── task_summary, risk_level, budget_tier, intervention_level
├── explicit_knowledge_packs: [...]   ← --knowledge-pack goes here
├── explicit_knowledge_docs: [...]    ← --knowledge goes here
│
├── Phase 1: "Backend implementation"
│   ├── Step 1.1: agent — "task"
│   │   └── knowledge: [...]         ← resolved per-agent
│   ├── Step 1.2: agent — "task"
│   └── Gate 1: "pytest passes"
│
├── Phase 2: "Review"
│   ├── Step 2.1: code-reviewer
│   └── Gate 2: "Human approval"     ← pending gaps surface here
│
└── Phase 3: ...
```

| Term | What it is | Knowledge scope |
|------|-----------|----------------|
| **Plan** | The whole job. One per `baton plan` invocation. | `explicit_knowledge_packs` + `explicit_knowledge_docs` — user's global inputs |
| **Phase** | A sequential stage. Phases run in order. | Groups steps + a gate |
| **Step** | One agent dispatch. The atomic unit of work. | `knowledge` attachments — resolved per-agent |
| **Gate** | A checkpoint between phases. Tests, approvals, human review. | Where pending knowledge gaps surface |
