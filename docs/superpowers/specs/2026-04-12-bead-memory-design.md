# Beads-Inspired Agent Memory — Design Specification

**Status:** Draft
**Date:** 2026-04-12

---

## Problem

Agents dispatched by Agent Baton start each session with zero memory of prior work. The existing feedback loop — execution, retrospective, PatternLearner, next plan — is lossy. It captures outcomes (pass/fail, duration, files changed) but not the decision process: why an agent chose approach A over B, what constraints it discovered mid-task, or what implicit assumptions proved wrong.

This creates three concrete failures:

1. **Intra-execution amnesia.** When the orchestrator dispatches Agent A (architect) then Agent B (backend-engineer) in the same execution, Agent B receives only the previous step's outcome text. It has no access to design trade-offs Agent A evaluated and rejected. Agent B may re-explore dead ends or violate constraints Agent A already identified.

2. **Cross-session regression.** An agent that solved a subtle integration issue in execution N will re-discover the same issue from scratch in execution N+1. Retrospective feedback captures "the task succeeded" but not "the ORM requires explicit flush before reading back generated IDs."

3. **Manual context bridging.** Users compensate by pasting prior outputs into prompts, attaching knowledge documents, or verbally summarizing history. This is fragile, scales poorly, and defeats the purpose of autonomous orchestration.

There is no structured mechanism for agents to leave durable, queryable traces of their reasoning for consumption by other agents or future sessions.

---

## Architecture Decision: Native SQLite, Not Beads Backend

### Decision

Implement Beads-inspired memory concepts natively on Agent Baton's existing SQLite storage layer. Do not adopt the Beads Go CLI (`bd`), Dolt database engine, or `.beads/` file format as runtime dependencies.

### Context

The Beads project (Steve Yegge, 18.7k GitHub stars) solves a similar problem — persistent, hash-linked memory across tool invocations — and has iterated through several storage backends. Their concepts are sound and battle-tested. Their infrastructure choices solve problems we do not have.

### Rationale

**1. The concurrent-write problem does not exist here.**

Beads migrated from SQLite to Dolt because N concurrent agents writing to a shared SQLite database caused write contention and lock timeouts. Agent Baton's architecture is fundamentally different. The `ExecutionDriver` serializes all state mutations through a single call path: `record_step_result()` (and its variant `record_team_member_result()`). Even when multiple agents are dispatched, their results are recorded sequentially by the orchestrating Claude session. There is no concurrent writer scenario. SQLite's single-writer model is not a limitation — it is a match.

**2. SyncEngine already provides replication.**

`SyncEngine` in `core/storage/sync.py` performs row-level incremental replication from per-project `baton.db` to `central.db`. This is functionally equivalent to Dolt's push/pull for our use case: local-first operation with optional centralized aggregation. Adding bead tables to the existing sync pipeline requires only schema additions, not new infrastructure.

**3. Installation simplicity is a hard constraint.**

Agent Baton installs via `pip install agent-baton`. Introducing a Go binary dependency (`bd`) would require platform-specific binaries, a separate install step, and PATH configuration. This breaks the single-command install story and adds a failure mode to every CI pipeline that uses the tool. Each installation must remain semi-local and self-contained for developers.

**4. Upstream stability risk.**

The Beads project underwent three storage migrations in four months: JSONL to SQLite, SQLite to Dolt, Dolt removal, then Dolt restoration (~120 commits for the reversal). Each migration changed the on-disk format and CLI behavior. Coupling Agent Baton's memory layer to this moving target would import churn into a system where storage reliability is a core invariant.

**5. Locality principle.**

Each Agent Baton installation is semi-local. A per-project `baton.db` is self-contained, requires no external services, and syncs to `central.db` only when explicitly requested via `baton sync`. Beads' Dolt server mode — which assumes a running database process — contradicts this principle and adds operational overhead inappropriate for a developer tool.

### What We Adopt from Beads (Concepts, Not Code)

- **Hash-based bead IDs.** SHA-256 of content plus metadata, truncated to 4-6 hex chars with progressive scaling. Collision-free across parallel agents without coordination.
- **Typed dependency graph.** Edges between beads carry semantics: `blocks`, `blocked_by`, `relates_to`, `discovered_from`, `validates`, `contradicts`, `extends`.
- **Ready queries.** Surface unblocked items whose dependencies are all resolved — applicable to knowledge gaps and pending decisions.
- **Memory decay.** Auto-summarize closed or stale beads to free context budget. Old beads are compressed, not deleted.
- **Agent signal protocol.** Structured output parsing to extract beads from agent responses, following the existing `KNOWLEDGE_GAP` pattern.

### What We Explicitly Do Not Adopt

- **Dolt database engine.** Solves concurrent-write contention we do not have; adds a server process we do not want.
- **Git-backed JSONL storage.** Beads' original format. Poor query performance, merge conflicts on concurrent access.
- **`bd` CLI dependency.** Go binary; breaks pip-only install; version coupling risk.
- **Flat task model.** Beads models work items as a flat graph. Agent Baton's hierarchical plan structure (phases, steps, team members) is load-bearing for gate logic, approval flow, and progress tracking.
- **Pull-based readiness (`bd ready`).** Agent Baton uses push-based dispatch: the `ExecutionDriver` determines the next step and constructs a delegation prompt. Beads inform what context to include, not which step to run.

### Pull-Forward Hedge

The `StorageBackend` protocol in `core/storage/` and the `ExternalSourceAdapter` protocol in `core/storage/adapters/` preserve the option to integrate with the Beads ecosystem if/when the `.beads/` on-disk format stabilizes. A future `BeadsAdapter` implementing `ExternalSourceAdapter` (~300 LOC) could read/write `.beads/` directories for cross-tool interoperability without changing the core engine or storage layer.

### Alternatives Considered

| Alternative | Disposition | Reason |
|---|---|---|
| **Full Beads backend** — adopt Dolt + `bd` CLI | Rejected | Go binary dependency, Dolt server process, upstream churn. Solves concurrent-write problem we don't have. Breaks pip-only install and locality. |
| **Hybrid** — `bd` CLI for beads, SQLite for engine state | Rejected | Two storage systems with split-brain risk. Subprocess calls on critical dispatch path. Still requires Go binary. |
| **Python reimplementation with `.beads/` interop** | Deferred | `.beads/` format not yet stable (3 format changes in 4 months). `ExternalSourceAdapter` hedge preserves this option at low cost once format stabilizes. |
| **Full independence** — no Beads influence | Rejected | Beads' core abstractions (content-addressed IDs, typed edges, decay) are well-designed and proven. Ignoring prior art would produce a worse design. We adopt concepts, not code. |

---

## Relationship to Existing Subsystems

Bead memory supplements existing subsystems. It does not replace any of them.

| Subsystem | Current Role | Impact of Beads |
|---|---|---|
| **KnowledgePack / KnowledgeDocument** | Static, human-curated knowledge attached at plan time | **Unchanged.** Beads are dynamic, agent-produced memory — a different concern. |
| **KnowledgeGapSignal / KnowledgeGapRecord** | Detects missing knowledge during execution | **Enhanced.** Bead queries can auto-resolve gaps from prior discoveries (F8). |
| **PatternLearner / LearnedPattern** | Aggregates statistical patterns across executions | **Enhanced.** Bead signals become an additional input source (F7). |
| **PerformanceScorer** | Quantitative agent assessment | **Enhanced.** Bead quality metrics feed into scoring (F12). |
| **RetrospectiveFeedback** | Post-execution narrative analysis | **Enhanced.** Retrospectives generate summary beads that persist findings (F6). |
| **EventPersistence** | Append-only event log for audit | **Unchanged.** Events and beads serve different purposes. Bead creation emits events, but the event store is not a bead store. |

---

## Data Model

### Bead Dataclass (`agent_baton/models/bead.py`)

```python
"""Data models for Beads-inspired structured memory.

Beads capture discrete units of insight -- discoveries, decisions, warnings,
outcomes, and planning notes -- produced by agents during execution.  They
persist across steps and phases, enabling downstream agents to inherit
upstream context without re-reading raw output.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _generate_bead_id(
    task_id: str, step_id: str, content: str, timestamp: str, bead_count: int
) -> str:
    """Generate a short hash ID using progressive scaling.

    Uses SHA-256 of ``task_id:step_id:content:timestamp`` truncated to
    a length that scales with the number of beads in the project:

    - < 500 beads:   4 hex chars  (~65k namespace)
    - < 1500 beads:  5 hex chars  (~1M namespace)
    - >= 1500 beads: 6 hex chars  (~16M namespace)

    Returns the ID with a ``bd-`` prefix for visual identification.
    """
    digest = hashlib.sha256(
        f"{task_id}:{step_id}:{content}:{timestamp}".encode()
    ).hexdigest()
    if bead_count >= 1500:
        length = 6
    elif bead_count >= 500:
        length = 5
    else:
        length = 4
    return f"bd-{digest[:length]}"


@dataclass
class BeadLink:
    """A typed dependency link between two beads.

    Attributes:
        target_bead_id: The bead this link points to.
        link_type: Relationship kind -- ``"blocks"``, ``"blocked_by"``,
            ``"relates_to"``, ``"discovered_from"``, ``"validates"``,
            ``"contradicts"``, or ``"extends"``.
        created_at: ISO 8601 timestamp.
    """

    target_bead_id: str
    link_type: str        # "blocks" | "blocked_by" | "relates_to" |
                          # "discovered_from" | "validates" | "contradicts" |
                          # "extends"
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "target_bead_id": self.target_bead_id,
            "link_type": self.link_type,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BeadLink:
        return cls(
            target_bead_id=data["target_bead_id"],
            link_type=data.get("link_type", "relates_to"),
            created_at=data.get("created_at", ""),
        )


@dataclass
class Bead:
    """A discrete unit of structured memory produced during execution.

    Attributes:
        bead_id: Short hash ID (e.g. ``"bd-a1b2"``).
        task_id: Execution that produced this bead.
        step_id: Step within the execution, or ``"planning"`` for
            beads created during plan generation.
        agent_name: Agent that generated this bead.
        bead_type: ``"discovery"`` | ``"decision"`` | ``"warning"``
            | ``"outcome"`` | ``"planning"``.
        content: The actual insight, discovery, or decision text.
        confidence: ``"high"`` | ``"medium"`` | ``"low"``.
        scope: ``"step"`` | ``"phase"`` | ``"task"`` | ``"project"``.
        tags: Semantic tags for retrieval matching.
        affected_files: Files this bead is about.
        status: ``"open"`` | ``"closed"`` | ``"archived"``.
        created_at: ISO 8601 creation timestamp.
        closed_at: ISO 8601 close timestamp, empty if open.
        summary: Compacted description (populated on close/decay).
        links: Typed dependency links to other beads.
        source: ``"agent-signal"`` | ``"planning-capture"``
            | ``"retrospective"`` | ``"manual"``.
        token_estimate: Approximate token count for budget management.
    """

    bead_id: str
    task_id: str
    step_id: str
    agent_name: str
    bead_type: str
    content: str
    confidence: str = "medium"
    scope: str = "step"
    tags: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    status: str = "open"
    created_at: str = ""
    closed_at: str = ""
    summary: str = ""
    links: list[BeadLink] = field(default_factory=list)
    source: str = "agent-signal"
    token_estimate: int = 0

    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, data: dict) -> Bead: ...
```

`to_dict()` / `from_dict()` follow the identical pattern in `models/knowledge.py`: flat dict output, `.get(key, default)` for every field, `[BeadLink.from_dict(d) for d in data.get("links", [])]` for nested lists.


### BeadStore (`agent_baton/core/engine/bead_store.py`)

```python
class BeadStore:
    """SQLite-backed bead persistence and query engine."""

    def __init__(self, db_path: Path) -> None: ...

    def write(self, bead: Bead) -> str:
        """Persist bead, write normalized bead_tags rows. Return bead_id."""

    def read(self, bead_id: str) -> Bead | None:
        """Fetch a single bead by ID."""

    def query(self, *, task_id=None, agent_name=None, bead_type=None,
              status=None, tags=None, limit=100) -> list[Bead]:
        """Filtered search with AND semantics. Ordered by created_at DESC."""

    def ready(self, task_id: str) -> list[Bead]:
        """Open beads with all blocked_by dependencies satisfied."""

    def close(self, bead_id: str, summary: str) -> None:
        """Close bead with compacted summary."""

    def link(self, source_id: str, target_id: str, link_type: str) -> None:
        """Add typed link between two beads."""

    def decay(self, max_age_days: int, task_id: str | None = None) -> int:
        """Archive old closed beads. Return count archived."""
```


### Schema Changes (`agent_baton/core/storage/schema.py`)

#### PROJECT_SCHEMA_DDL additions

```sql
-- BEADS
CREATE TABLE IF NOT EXISTS beads (
    bead_id          TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    bead_type        TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    confidence       TEXT NOT NULL DEFAULT 'medium',
    scope            TEXT NOT NULL DEFAULT 'step',
    tags             TEXT NOT NULL DEFAULT '[]',
    affected_files   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    closed_at        TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    links            TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_beads_task ON beads(task_id);
CREATE INDEX IF NOT EXISTS idx_beads_agent ON beads(agent_name);
CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);

-- BEAD_TAGS (normalized for efficient tag-based retrieval)
CREATE TABLE IF NOT EXISTS bead_tags (
    bead_id  TEXT NOT NULL,
    tag      TEXT NOT NULL,
    PRIMARY KEY (bead_id, tag),
    FOREIGN KEY (bead_id) REFERENCES beads(bead_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bead_tags_tag ON bead_tags(tag);
```

#### CENTRAL_SCHEMA_DDL additions

Mirror tables with `project_id TEXT NOT NULL` prepended to primary keys:

```sql
CREATE TABLE IF NOT EXISTS beads (
    project_id       TEXT NOT NULL,
    bead_id          TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    bead_type        TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    confidence       TEXT NOT NULL DEFAULT 'medium',
    scope            TEXT NOT NULL DEFAULT 'step',
    tags             TEXT NOT NULL DEFAULT '[]',
    affected_files   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    closed_at        TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    links            TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, bead_id)
);

CREATE TABLE IF NOT EXISTS bead_tags (
    project_id  TEXT NOT NULL,
    bead_id     TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (project_id, bead_id, tag)
);
```

#### MIGRATIONS[4]

```python
SCHEMA_VERSION = 4

MIGRATIONS: dict[int, str] = {
    # ... existing entries for 2 and 3 ...
    4: """
-- v4: add bead memory tables.
CREATE TABLE IF NOT EXISTS beads (
    bead_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL,
    agent_name TEXT NOT NULL, bead_type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '', confidence TEXT NOT NULL DEFAULT 'medium',
    scope TEXT NOT NULL DEFAULT 'step', tags TEXT NOT NULL DEFAULT '[]',
    affected_files TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL,
    closed_at TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
    links TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_beads_task ON beads(task_id);
CREATE INDEX IF NOT EXISTS idx_beads_agent ON beads(agent_name);
CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);
CREATE TABLE IF NOT EXISTS bead_tags (
    bead_id TEXT NOT NULL, tag TEXT NOT NULL,
    PRIMARY KEY (bead_id, tag),
    FOREIGN KEY (bead_id) REFERENCES beads(bead_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bead_tags_tag ON bead_tags(tag);
""",
}
```

#### SyncEngine additions

```python
# In SYNCABLE_TABLES, after shared_context:
SyncTableSpec("beads", ["bead_id"]),
SyncTableSpec("bead_tags", ["bead_id", "tag"]),
```

### Cross-Layer Linkage Checklist

| # | Sync Point | Status |
|---|-----------|--------|
| 1 | **PROJECT_SCHEMA_DDL** | `beads` and `bead_tags` tables added |
| 2 | **CENTRAL_SCHEMA_DDL** | Mirror tables with `project_id` prefix |
| 3 | **MIGRATIONS[4]** | `CREATE TABLE IF NOT EXISTS` for both tables |
| 4 | **sqlite_backend.py** | `BeadStore` encapsulates all INSERT/SELECT |
| 5 | **sync.py** | Two `SyncTableSpec` entries in `SYNCABLE_TABLES` |

---

## Feature Catalog

### F1. Bead Data Model and Store

**Value:** Foundation for all memory features. No existing data disturbed.
**Scope:** M | **Tier:** 1

**Integration points:**
- New: `agent_baton/models/bead.py`, `agent_baton/core/engine/bead_store.py`
- Modified: `core/storage/schema.py` (both DDLs + migration), `core/storage/sync.py`, `models/__init__.py`

**Depends on:** nothing

---

### F2. Agent Signal Protocol

**Value:** Agents emit structured discoveries, decisions, and warnings. These populate the bead store, turning ephemeral output into durable memory.
**Scope:** M | **Tier:** 1

**Integration points:**
- New: `core/engine/bead_signal.py` — parsers for `BEAD_DISCOVERY`, `BEAD_DECISION`, `BEAD_WARNING`
- Modified: `core/engine/executor.py` — `record_step_result()` calls `parse_bead_signals()` after knowledge gap parsing (~line 739)
- Modified: `core/engine/dispatcher.py` — append signal instructions after `_KNOWLEDGE_GAPS_LINE` (~line 264)
- Modified: `core/events/events.py` — `bead.created` event factory

**Depends on:** F1

---

### F3. Forward Relay — Inter-Agent Bead Injection

**Value:** The single highest-impact feature. Agents receive prior discoveries in their prompts without manual context bridging.
**Scope:** M | **Tier:** 2

**Integration points:**
- New: `core/engine/bead_selector.py` — filter, rank, budget-trim beads for dispatch
- Modified: `core/engine/executor.py` — `_dispatch_action()` (~line 2124) calls BeadSelector
- Modified: `core/engine/dispatcher.py` — `build_delegation_prompt()` gains `prior_beads` parameter, renders `## Prior Discoveries` section between Knowledge Context and Your Task

**Depends on:** F2

---

### F4. Planning Decision Capture

**Value:** Records planner's decision process as durable beads. Replaces ephemeral `_last_*` variables. Makes `explain_plan()` persistent.
**Scope:** S | **Tier:** 2

**Integration points:**
- Modified: `core/engine/planner.py` — `create_plan()` writes beads at 14 decision points; `explain_plan()` reads from BeadStore

**Depends on:** F1

---

### F5. `baton beads` CLI Commands

**Value:** User visibility into the bead store. `ready` surfaces actionable items. `list`/`show` enable debugging.
**Scope:** S | **Tier:** 1

**Integration points:**
- New: `cli/commands/bead_cmd.py` — subcommands: `list`, `show`, `ready`, `close`, `link`
- Modified: `cli/main.py` — register bead commands

**Depends on:** F1

---

### F6. Memory Decay and Summarization

**Value:** Prevents unbounded growth. Archived beads retain structure (id, type, summary, links) while dropping verbose content.
**Scope:** S | **Tier:** 2

**Integration points:**
- New: `core/engine/bead_decay.py`
- Modified: `core/engine/executor.py` — `complete()` (~line 890) triggers decay
- Modified: `cli/commands/bead_cmd.py` — `cleanup` subcommand

**Depends on:** F1

---

### F7. Bead-Informed Plan Enrichment — BeadAnalyzer

**Value:** Mines historical beads to improve future plans. Recurring warnings → review phases. Discovery clusters → context files. Decision reversals → approval gates.
**Scope:** L | **Tier:** 3

**Integration points:**
- New: `core/learn/bead_analyzer.py`
- Modified: `models/pattern.py` — `PlanStructureHint` dataclass
- Modified: `core/engine/planner.py` — consults analyzer after pattern lookup

**Depends on:** F4

---

### F8. Knowledge Gap Auto-Resolution from Beads

**Value:** Reduces human interruptions. Checks bead store for matching discoveries before escalating a knowledge gap.
**Scope:** M | **Tier:** 3

**Integration points:**
- Modified: `core/engine/knowledge_gap.py` — `determine_escalation()` gains `bead_store` parameter
- Modified: `core/engine/executor.py` — passes BeadStore to escalation

**Depends on:** F1, F2

---

### F9. Bead-to-Knowledge Promotion

**Value:** Graduates high-value beads into permanent knowledge documents.
**Scope:** S | **Tier:** 3

**Integration points:**
- Modified: `cli/commands/bead_cmd.py` — `promote` subcommand
- Uses: `models/knowledge.py`, KnowledgeRegistry

**Depends on:** F5

---

### F10. Central Store Sync

**Value:** Cross-project analytics via `baton query`. Teams query patterns across all projects.
**Scope:** M | **Tier:** 4

**Integration points:**
- Modified: `core/storage/schema.py` — central DDL, analytics view `v_cross_project_discoveries`
- Modified: `core/storage/sync.py`, `cli/commands/query_cmd.py`

**Depends on:** F1

---

### F11. Bead Dependency Graph and Conflict Detection

**Value:** Detects contradictory beads before they cause agent confusion. Surfaces conflicts as approval gates.
**Scope:** L | **Tier:** 4

**Integration points:**
- Modified: `core/engine/bead_store.py` — conflict detection on `contradicts`/`supersedes` links
- Modified: `core/engine/executor.py` — `_determine_action()` checks conflicts before advancing
- Modified: `core/events/events.py` — `bead.conflict` event
- Modified: `cli/commands/bead_cmd.py` — `graph` subcommand

**Depends on:** F2, F3

---

### F12. Quality Scoring and Agent Feedback

**Value:** Self-curating memory. High-quality beads surface more; noise decays.
**Scope:** M | **Tier:** 4

**Integration points:**
- Modified: `models/bead.py` — `quality_score`, `retrieval_count` fields
- Modified: `core/engine/bead_selector.py` — ranking incorporates quality_score
- Modified: `core/engine/bead_signal.py` — parse `BEAD_FEEDBACK` signals
- Modified: `core/improve/scoring.py` — PerformanceScorer gains bead quality metric

**Depends on:** F2, F3

---

## Pull-Forward Features (Beads Ecosystem Hedge)

These activate only if the `.beads/` format stabilizes. Not scheduled — designs-on-the-shelf.

### PF1. Beads Read Adapter

`ExternalSourceAdapter` implementation that reads `.beads/issues.jsonl` into central.db. ~200 LOC.
- New: `core/storage/adapters/beads.py`
- Activated via: `baton source add beads --config '{"beads_dir": ".beads"}'`

### PF2. Bead Export to `.beads/` Format

After `baton execute complete`, optionally emit `.beads/`-compatible summary per step.
- Modified: `core/engine/executor.py` — `complete()` hook
- CLI: `baton execute complete --emit-beads`

### PF3. Cross-Tool Bead Import in Knowledge Resolver

5th resolution layer: query `.beads/` for historical discoveries from other tools.
- Modified: `core/engine/knowledge_resolver.py`

---

## Phased Implementation Roadmap

### Tier 1: Foundation (F1, F2, F5)

**Goal:** Agents produce structured memory. Users can inspect it. No workflow changes.

**Parallelization:** F1 first. F2 and F5 in parallel after F1 (no file overlap).

**Exit criteria:** Every step can produce beads via signals. `baton beads list` and `baton beads ready` work. No impact on existing behavior.

**Scope:** 3-4 weeks.

### Tier 2: Value Multipliers (F3, F4, F6)

**Goal:** Inter-agent knowledge transfer. Auditable planning. Sustainable memory growth.

**Parallelization:** F3, F4, F6 are fully independent — build simultaneously.

**Exit criteria:** Agents receive prior discoveries in prompts. Planning decisions are persisted. Closed beads auto-summarize.

**Important:** Let Tier 1 run 2-4 weeks before starting Tier 2 so the bead store has content.

**Scope:** 3-5 weeks.

### Tier 3: Intelligence Layer (F7, F8, F9)

**Goal:** Self-improving plans. Fewer knowledge gap escalations. Organic knowledge growth.

**Parallelization:** F7, F8, F9 are independent.

**Exit criteria:** Plans cite bead evidence. Known-answer gaps auto-resolve. Beads promote to knowledge packs.

**Scope:** 4-6 weeks.

### Tier 4: Scale and Safety (F10, F11, F12)

**Goal:** Cross-project learning. Conflict safety net. Self-curating memory.

**Parallelization:** F10 independent. F11 and F12 depend on F3 but not each other.

**Exit criteria:** Beads sync to central.db. Contradictions surface before advancement. Low-value beads decay.

**Scope:** 4-6 weeks.

### Pull-Forward Features (PF1, PF2, PF3)

**Trigger:** `.beads/` format stability (post-Beads v1.0). Not scheduled. 1-2 weeks each when ecosystem signal is right.

---

## Integration Contract

### Prompt Structure (after F3)

```
You are a {role} working on {project_line}.

## Shared Context
{shared_context_block}

Read `CLAUDE.md` for project conventions.

## Intent
{task_summary}

## Knowledge Context                    <- unchanged
{knowledge_section}

## Prior Discoveries                    <- NEW (F3)
The following were discovered by prior agents in this execution.
Treat as established context unless you find contrary evidence.

### Discovery (step 1.1, backend-engineer, confidence: high)
The auth module uses JWT with RS256, not HS256 as documented.
Files: src/auth/token.py
Tags: auth, jwt

### Warning (step 1.2, test-engineer, confidence: medium)
Test DB fixture uses hardcoded port 5433 — may conflict.
Files: tests/conftest.py
[N additional discoveries omitted — run `baton beads list` to review]

## Your Task (Step {step_id})
{task_description}

{success_criteria}
{files_to_read}
{deliverables}
{boundaries}

If you lack critical context, output `KNOWLEDGE_GAP: <description>` ...

Report discoveries and decisions using structured signals:
  BEAD_DISCOVERY: <what you found>
  BEAD_DECISION: <what you decided> CHOSE: <choice> BECAUSE: <rationale>
  BEAD_WARNING: <what might cause problems>

## Previous Step Output                 <- preserved for backward compat
{previous_output}

Log non-obvious decisions under a **Decisions** heading...
```

### Signal Protocol

```python
_BEAD_DISCOVERY_PATTERN = re.compile(r"BEAD_DISCOVERY:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_BEAD_DECISION_PATTERN = re.compile(r"BEAD_DECISION:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_BEAD_CHOSE_PATTERN = re.compile(r"CHOSE:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_BEAD_BECAUSE_PATTERN = re.compile(r"BECAUSE:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_BEAD_WARNING_PATTERN = re.compile(r"BEAD_WARNING:\s*(.+?)(?:\n|$)", re.IGNORECASE)
```

Malformed signals silently dropped. Extraction never fatal.

### Invariant Compliance

This spec does NOT change any of the three invariants in `docs/invariants.md`:
- **CLI command surface:** `baton beads` commands are purely additive
- **`_print_action()` output format:** Unchanged
- **`execution-state.json` schema:** Beads stored in SQLite, NOT in execution-state.json

---

## Risk Assessment

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| 1 | **Token budget inflation** — beads compete for context window | High | Separate 4k budget (not shared with knowledge's 32k). Hard cap of 5 beads per dispatch. `--no-beads` flag. |
| 2 | **Signal extraction noise** — malformed `BEAD_` signals | Medium | Strict regex. Malformed signals silently dropped. Never fatal. |
| 3 | **Stale/misleading memory** — old beads from different context | High | Memory decay (F6). Project-scoped by default. Cross-project opt-in. Advisory framing in prompt. |
| 4 | **Storage growth** — every step can produce beads | Low | ~200-500 bytes per bead. `baton beads cleanup` prunes. Configurable retention. 1000 executions < 5 MB. |
| 5 | **Cold-start** — no beads until agents emit signals | Medium | Tier 1 is capture-only. F4 captures planning beads immediately. Backfill command for history. |
| 6 | **"Claude owns intelligence" violation** — injected memory overrides reasoning | Medium | Advisory framing ("unless you find contrary evidence"). 5-bead cap. Mirrors KnowledgeAttachment pattern. |

---

## Dependency Graph

```
F1 (Model + Store)
 ├──→ F2 (Signal Protocol)
 │     ├──→ F3 (Forward Relay)
 │     │     ├──→ F11 (Conflict Detection)
 │     │     └──→ F12 (Quality Scoring)
 │     └──→ F8 (Gap Auto-Resolution)
 ├──→ F4 (Planning Capture)
 │     └──→ F7 (BeadAnalyzer)
 ├──→ F5 (CLI)
 │     └──→ F9 (Promotion)
 ├──→ F6 (Decay)
 └──→ F10 (Central Sync)

Pull-forward (when .beads/ stabilizes):
 └──→ PF1 (Read Adapter)
 └──→ PF2 (Export)
 └──→ PF3 (Cross-Tool Import)
```

---

## Summary Table

| # | Feature | Size | Tier | Depends On | Gastown Lesson |
|---|---------|------|------|------------|----------------|
| F1 | Bead Model + Store | M | 1 | — | Hash IDs prevent collisions |
| F2 | Agent Signal Protocol | M | 1 | F1 | Structured signals = inspectability |
| F5 | CLI Commands | S | 1 | F1 | Operator visibility |
| F3 | Forward Relay | M | 2 | F2 | Shared context cuts token burn 60-80% |
| F4 | Planning Decision Capture | S | 2 | F1 | Design bottleneck becomes visible |
| F6 | Memory Decay | S | 2 | F1 | Prevents unbounded context growth |
| F7 | BeadAnalyzer | L | 3 | F4 | Historical data improves decomposition |
| F8 | Gap Auto-Resolution | M | 3 | F1, F2 | Reduces human intervention |
| F9 | Bead-to-Knowledge Promotion | S | 3 | F5 | Discoveries become permanent knowledge |
| F10 | Central Store Sync | M | 4 | F1 | Cross-project learning |
| F11 | Conflict Detection | L | 4 | F2, F3 | Semantic conflicts are the hard problem |
| F12 | Quality Scoring | M | 4 | F2, F3 | Self-curating memory |
| PF1 | Beads Read Adapter | S | — | F10 | Ecosystem interop |
| PF2 | Bead Export | S | — | F1 | Ecosystem interop |
| PF3 | Cross-Tool Import | S | — | PF1 | Network effect |

**Totals:** 12 core features (3S + 6M + 3L), 3 pull-forward features (3S). 4 tiers spanning 14-21 weeks.
