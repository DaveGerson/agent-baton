# Baton Execution Plan Design Patterns

This reference catalogs recurring structural patterns for execution plans. Each
pattern has a name, intent, when to use it, structural signature, and a concrete
example drawn from real plans.

---

## Pattern 1: Prerequisite Gate Pattern

**Intent**: Guard cross-workstream dependencies with an explicit verification
phase before substantive work begins. Fail fast rather than discover missing
prerequisites mid-execution.

**When to use**:
- A feature depends on another team's module, API contract, or schema that may
  not yet exist.
- An agent needs to import or call code from a sibling project before writing
  its own.
- External system access (database, message bus, registry) must be confirmed
  before dispatching expensive agents.
- The task spans multiple codebases or repos and import paths need validation.

**Structural signature**:

```
Phase 0 — Prerequisites (GATE type: import-check | api-check | schema-check)
  Step 0.1 — Verify <dependency> is importable / accessible
  QA Gate — if check fails → STOP; if pass → proceed

Phase 1 — Core implementation
  ...
```

Phase 0 is intentionally cheap: it contains only verification steps, no
implementation. The gate after Phase 0 is mandatory — mark it `fail_on: any`.
If Phase 0 fails, execution stops before any expensive agent is dispatched.

**Example — Knowledge Delivery Subsystem (2026-03-24)**:

The knowledge delivery plan added `KnowledgeRegistry` and `KnowledgeResolver`
into `core/orchestration/` and `core/engine/` respectively. Before implementing
these components, Phase 0 verified that the existing `PatternLearner` and
`RetrospectiveEngine` interfaces matched the expected signatures — because the
new components had to write into retrospective JSON files that `PatternLearner`
reads. A mismatch in field names would only surface at the integration test
phase, after three agent dispatches.

```yaml
phases:
  - id: 0
    name: Prerequisite Check
    gate:
      type: command
      command: "python -c \"from agent_baton.core.learn.pattern_learner import PatternLearner; assert hasattr(PatternLearner, 'knowledge_gaps_for')\""
      description: Verify PatternLearner has knowledge_gaps_for() method
      fail_on: any
    steps:
      - id: "0.1"
        agent: backend-engineer--python
        task: Verify KnowledgeGapRecord fields match PatternLearner expectations
```

**Anti-pattern**: Putting prerequisite checks inside Phase 1 alongside
implementation steps. If the check fails partway through Phase 1, some
implementation files have been created but the phase is incomplete, leaving
the repo in an inconsistent state.

---

## Pattern 2: Parallel Wave Pattern

**Intent**: Dispatch multiple independent implementation steps simultaneously
within a phase to reduce total wall-clock time.

**When to use**:
- Two or more agents work on separate files or subsystems with no shared
  write targets.
- The steps do not consume each other's outputs — they produce deliverables
  that are later assembled by a downstream agent or gate.
- Each step would otherwise sit idle waiting for a sibling step it does not
  depend on.

**Structural signature**:

```
Phase N — Parallel implementation
  Step N.1 — Agent A → writes to subsystem-A/
  Step N.2 — Agent B → writes to subsystem-B/   (depends_on: [])
  Step N.3 — Agent C → writes to subsystem-C/   (depends_on: [])
  QA Gate — runs after ALL steps in the phase complete

Phase N+1 — Integration
  Step (N+1).1 — Agent D → assembles A+B+C (depends_on: [N.1, N.2, N.3])
```

All steps in a phase with empty `depends_on` lists are eligible for parallel
dispatch. Use `baton execute next --all` to retrieve all dispatchable steps
at once. The engine returns them as a list of DISPATCH actions.

**Example — Federated Sync Architecture (2026-03-24)**:

The federated sync implementation split into three independent storage
modules: `SyncEngine`, `CentralStore`, and the `ExternalSourceAdapter`
protocol. None of these modules imports from the others at write time —
`SyncEngine` writes to `central.db`, `CentralStore` reads it, and the
adapter protocol produces `ExternalItem` objects that are persisted
separately. All three were implemented in parallel in Phase 1, then
integrated in Phase 2 by the `__init__.py` factory additions and CLI
commands.

```yaml
phases:
  - id: 1
    name: Storage Layer Implementation
    steps:
      - id: "1.1"
        agent: backend-engineer--python
        task: Implement SyncEngine in core/storage/sync.py
        allowed_paths: [agent_baton/core/storage/sync.py]
        depends_on: []
      - id: "1.2"
        agent: backend-engineer--python
        task: Implement CentralStore in core/storage/central.py
        allowed_paths: [agent_baton/core/storage/central.py]
        depends_on: []
      - id: "1.3"
        agent: backend-engineer--python
        task: Implement ExternalSourceAdapter protocol and AdoAdapter
        allowed_paths: [agent_baton/core/storage/adapters/]
        depends_on: []
    gate:
      type: command
      command: pytest tests/test_storage/ -x -q
      fail_on: any
  - id: 2
    name: Integration
    steps:
      - id: "2.1"
        agent: backend-engineer--python
        task: Wire SyncEngine, CentralStore, AdapterRegistry into storage/__init__.py and add CLI commands
        depends_on: ["1.1", "1.2", "1.3"]
```

**Constraint**: Steps in the same phase should write to non-overlapping file
paths. If two parallel steps write to the same file, one will overwrite the
other's changes. Enforce this via `allowed_paths` in each step definition.

---

## Pattern 3: Gap-Suggested Knowledge Pattern

**Intent**: Carry forward knowledge gaps from previous retrospectives as
candidate attachments in future plans. The planner marks these as
`gap-suggested` so the user can review and confirm them at the plan review
gate before execution begins.

**When to use**:
- A task type has been executed before and the retrospective recorded that
  an agent lacked context on a specific domain.
- The same agent-task combination is being planned again (same agent name +
  similar task type).
- `PatternLearner` has accumulated enough evidence (2+ gap records for the
  same `agent_name + task_type` pair) to suggest the attachment proactively.

**Structural signature**:

```
Plan review gate (before Phase 1):
  Attached knowledge:
    - pack: compliance-audit  [source: gap-suggested, confidence: 0.84]
      "3 prior runs of auditor on data-classification tasks flagged missing
       SOX context. Confirm to attach or remove."
    - doc: schema-v3.md       [source: explicit, user-provided]

Phase 1 — Implementation
  Step 1.1 — Agent receives confirmed knowledge attachments in delegation prompt
```

The `[source: gap-suggested]` tag is produced by `KnowledgeResolver` when it
finds a `KnowledgeGapRecord` from a prior retrospective that matches the current
step's `agent_name + task_type`. The attachment is included in `plan.md` with a
confirmation notice — the user removes it if it is not applicable to this run.

**Example — Knowledge Delivery Retrospective Feedback Loop (2026-03-24)**:

During the design of the knowledge delivery subsystem, the `auditor` agent
repeatedly flagged missing compliance context when reviewing data classification
changes. After three executions, `PatternLearner` indexed these as:

```
agent: auditor
task_type: data-classification-review
gap: "Missing SOX audit trail requirements for financial data"
resolution: unresolved (×3)
```

The fourth plan for a data classification task automatically included the
`compliance-audit` knowledge pack as a `gap-suggested` attachment in
`plan.md`. The user confirmed the attachment. The auditor agent received the
pack at dispatch time and completed the review without a `KNOWLEDGE_GAP`
self-interrupt.

**Implementation note**: `gap-suggested` attachments appear in `plan.md`
with a distinct marker (`[gap-suggested: N prior occurrences]`) so they
are visually distinguishable from `[explicit]` and `[agent-declared]`
attachments. The plan review gate presents them as items requiring explicit
confirmation rather than passive acknowledgement.

---

## Pattern 4: Intervention Level Pattern

**Intent**: Expose a user-controlled escalation threshold that shifts how
aggressively the engine routes ambiguous situations to human review versus
resolving them autonomously.

**When to use**:
- The task involves regulated data, compliance requirements, or sensitive
  business logic where autonomous resolution of ambiguity is unacceptable.
- The task is exploratory and the user wants to minimize interruptions.
- An agent self-interrupts with a `KNOWLEDGE_GAP` signal and the engine
  must decide: auto-resolve, defer, or escalate.
- The user's tolerance for autonomy varies per task, not globally.

**Structural signature**:

```
baton plan "task description" --intervention low|medium|high
```

| Level | Behavior |
|-------|---------|
| `low` (default) | Maximize agent autonomy. Auto-resolve factual gaps from registry. Proceed best-effort if gap cannot be resolved and risk is LOW. Queue gaps only for MEDIUM+ risk tasks. |
| `medium` | Escalate factual gaps at any risk level if the registry cannot resolve them. Auto-resolve only when confidence is high (registry match + prior resolution record). |
| `high` | Escalate all unresolved gaps to the next human gate, regardless of risk level or gap type. Every `KNOWLEDGE_GAP` signal creates a gate. |

The `--intervention` flag is stored in the `MachinePlan` and read by
`KnowledgeGap` handler at runtime when an agent emits a `KNOWLEDGE_GAP`
signal.

**Example — Federated Sync Architecture (2026-03-24)**:

The federated sync plan was created with `--intervention medium` because
the sync engine writes to a shared database that multiple projects depend on.
An error in the sync logic could corrupt cross-project views for all projects,
not just the one being worked on. With `medium` intervention:

- `SyncEngine._sync_table()` implementation questions about conflict
  resolution strategy were escalated to a human gate rather than resolved by
  the agent's best guess.
- The watermark update logic (which determines idempotency guarantees) was
  surfaced at an approval gate with the question: "Should `rowid` or `updated_at`
  be the watermark column?" The user chose `rowid` for tables without
  `updated_at`.

At `--intervention low`, both decisions would have been made autonomously by
the backend engineer agent, and the choice might not have matched the
architectural intent.

**Example — Knowledge Delivery Subsystem (2026-03-24)**:

The knowledge delivery plan used `--intervention low` because the subsystem
is additive (no existing behavior changes, only new behavior is injected) and
the components are independently testable. Low intervention let the backend
engineer agent proceed through all three components without human gates
except for the explicit QA gates after each phase.

**Implementation note**: The `--intervention` flag shifts the thresholds in
the escalation matrix in `core/engine/knowledge_gap.py`. It does not change
which agents are dispatched, which gates are run, or which knowledge is
attached — only how aggressively `KNOWLEDGE_GAP` signals are escalated.
Users can re-run with a different intervention level to get a different
autonomy profile on the same plan.
