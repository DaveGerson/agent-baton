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

---

<a id="executable-beads-trust-boundary"></a>

## Pattern: Executable Beads — Trust Boundary

**Reference**: bd-18f6 ship-ready audit lineage (end-user readiness #10).

**Intent**: Set explicit expectations about what the executable-bead sandbox
does and does not protect against, before the feature is exposed to any
shareable / federated / pull-request code path.

### What is an executable bead?

An *executable bead* is a `Bead` with `bead_type="executable"` (modelled by
`agent_baton.models.bead.ExecutableBead`) that carries a script body
(referenced by `script_sha` / `script_ref`), an interpreter
(`bash` / `python`), and runtime limits (`timeout_s`, `mem_mb`, `net`).
It is created via `baton beads create-exec` and run via `baton beads exec`.
Executable beads exist so that an agent's reproducer steps, validation
scripts, or smoke checks can be committed to the bead graph alongside the
discoveries they validate, and re-run on demand.

### What sandboxing is in place

The runner (`agent_baton/core/exec/runner.py`) and sandbox
(`agent_baton/core/exec/sandbox.py`) provide:

- **Process-level isolation only.** The script runs in a child process with
  a wall-clock timeout (default `30s`), a memory limit (default `256 MB`),
  and a captured stdout/stderr stream that spills to disk when large.
- **Static lint pre-flight.** `ScriptLinter` (`script_lint.py`) refuses to
  store scripts that match a denylist of obviously dangerous patterns
  (e.g. `cat /etc/passwd`, `curl | sh`, fork bombs, `dd` overwrites,
  writes into `.claude/souls/`, writes into `baton.db` / `central.db`).
- **Auditor gate.** `auditor_gate.py` quarantines newly-stored beads;
  `baton beads exec` refuses to run a bead until an auditor approves it.
- **Operator confirmation.** `baton beads exec <bead-id>` prints the
  interpreter, script SHA, runtime limits, and content preview, then
  requires a `y` to proceed (skip with `--no-confirm`).
- **Optional soul signing.** When `BATON_SOULS_ENABLED=1`, the bead must be
  signed by a soul before storage; tampering invalidates the signature.
- **Feature flag.** The whole subsystem is gated behind
  `BATON_EXEC_BEADS_ENABLED=1`; the default install cannot run scripts at
  all.

The runner does NOT provide:

- Filesystem namespacing / chroot / mount isolation.
- Network namespacing (`net=False` is enforced only at the linter level).
- A syscall filter (no seccomp, no AppArmor / SELinux profile).
- User-namespace / UID remapping.
- A read-only working tree — the script can write anywhere the parent
  process can write, modulo what `ScriptLinter` rejects up front.

### What scripts are SAFE to run as executable beads

The trust model assumes the script comes from the same operator who is
running `baton`. Specifically:

- **Locally-authored** by the operator or a teammate working in the same
  repository.
- **Version-controlled** in the same git project the bead lives in
  (the `anchor_commit` field on the bead pins it to a tree the operator
  can audit before approving).
- **Reviewed-by-team** as part of the auditor-gate quarantine — i.e. the
  reviewer has read the script and decided it is appropriate to run with
  the same privileges as the operator's shell.

Under those assumptions the sandbox is sufficient: it stops the script
from running away with the machine, and it surfaces obvious mistakes.

### What scripts are NOT SAFE

Any executable bead whose script body did not originate with the local team
must be treated as untrusted code on a trusted machine. In particular:

- **Downloaded beads** — anything pulled in from another operator's bead
  store, a published bead pack, or a curated catalogue.
- **Beads from untrusted sources** — third-party agents, scraped issues,
  customer-supplied repros.
- **Beads received over federation** — anything the (future) federation
  pipeline syncs in from `central.db` of a project the operator does not
  own end-to-end.
- **Beads from pull requests on public repos** — including bot-generated
  beads, fork-PR beads, or beads that arrived as part of an attached
  patch.

For all of these, do not run `baton beads exec`. Read the script body
(`baton beads show --script`), reproduce the intent manually if it is
worth keeping, then re-create the bead locally so its `anchor_commit`
points at code the operator vouches for.

### Threat model in scope

The sandbox is designed to catch:

- **Accidents** — a teammate's reproducer script that loops forever, fills
  the disk, or wedges the terminal.
- **Broken builds** — a regression-check script that needs to fail
  cleanly and surface logs without taking down the host.

The sandbox is explicitly NOT designed to defend against:

- **Supply-chain attacks** — a script body that was modified between the
  author and the operator.
- **Malicious actors** — a script that was authored with the intent of
  exfiltrating data, persisting access, or escalating privilege.

If the threat model needs to expand to include either of those, the
sandbox needs to grow first.

### Future-state

If executable beads ever need to handle untrusted input — federated bead
sync, public bead packs, fork-PR beads, customer-uploaded reproducers, or
any other "shareable bead" use case — the sandbox MUST be upgraded to
namespacing (mount + network + user) and a syscall filter (seccomp or
equivalent) BEFORE that use case ships. The CLI warning emitted by
`baton beads exec` for non-local origins (see
`agent_baton/cli/commands/bead_cmd.py::_handle_exec`) is the tripwire
that surfaces the gap; it is not a substitute for the upgrade.

**Operational rule**: do not extend the runner to consume any bead source
other than `agent-signal | planning-capture | retrospective | manual`
without first delivering the sandbox upgrade described above.
