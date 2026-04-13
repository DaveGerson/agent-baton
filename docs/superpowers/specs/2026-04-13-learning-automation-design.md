# Learning Automation System — Design Spec

**Date:** 2026-04-13
**Status:** Approved
**Approach:** Hybrid — Learning Ledger feeds existing improvement pipeline

## Problem Statement

Agent Baton's learning pipeline collects rich execution data (retrospectives,
scores, patterns, recommendations) but has three gaps:

1. **No structured issue tracking** — problems are scattered across
   retrospective markdown files with no queryable index.
2. **No systematized workflow** — routing corrections, agent drops, and
   knowledge gaps require manual discovery across files.
3. **No auto-application path** — even safe, repeated signals never get
   applied without human intervention.

## Architecture Overview

```
Execution completes
    |
    v
LearningEngine.detect(state)          <-- automatic, every execution
    |-- scans for routing mismatches, agent failures, knowledge gaps, etc.
    |-- writes/updates LearningIssue records in LearningLedger (SQLite)
    |-- if issue crosses auto-apply threshold --> apply() immediately
    |
    v
LearningLedger (baton.db)             <-- queryable issue tracker
    |-- federated: per-project baton.db + central.db aggregation
    |-- deduplication by (issue_type, target)
    |-- evidence accumulation over time
    |
    +---> ImprovementLoop.run_cycle()  <-- periodic analysis
    |         |-- LearningEngine.analyze()
    |         |-- generates Recommendations from open issues
    |         |-- feeds existing proposal/experiment/rollback pipeline
    |
    +---> baton learn interview        <-- human-directed decisions
    |         |-- structured multiple-choice dialogue
    |         |-- surfaces issues needing human judgment
    |         |-- records decisions as resolution directives
    |
    +---> baton learn status/issues    <-- visibility
              |-- dashboard of open issues, recent resolutions
              |-- filterable by type, severity, status
```

## Component 1: Learning Ledger

### LearningIssue Model (`models/learning.py`)

```python
@dataclass
class LearningEvidence:
    timestamp: str          # ISO 8601
    source_task_id: str     # execution that produced this signal
    detail: str             # what was observed
    data: dict              # structured payload (agent names, scores, etc.)

@dataclass
class LearningIssue:
    issue_id: str           # UUID
    issue_type: str         # routing_mismatch | agent_degradation | knowledge_gap |
                            # roster_bloat | gate_mismatch | pattern_drift | prompt_evolution
    severity: str           # low | medium | high | critical
    status: str             # open | investigating | proposed | applied | resolved | wontfix
    title: str              # human-readable summary
    target: str             # what this is about (agent name, flavor, pack, etc.)
    evidence: list[LearningEvidence]
    first_seen: str         # ISO timestamp
    last_seen: str          # ISO timestamp
    occurrence_count: int
    proposed_fix: str | None
    resolution: str | None
    resolution_type: str | None  # auto | human | interview
    experiment_id: str | None    # links to Experiment if auto-applied
```

### LearningLedger (`core/learn/ledger.py`)

SQLite-backed CRUD for `LearningIssue` records.

- **Storage:** `learning_issues` table in project-level `baton.db`.
- **Federation:** Mirrored in `central.db` with `project_id` prefix column,
  synced via existing `SyncEngine`.
- **Deduplication:** Issues keyed by `(issue_type, target)`. Repeated signals
  increment `occurrence_count`, append evidence, update `last_seen`.
- **Schema:** Added to `PROJECT_SCHEMA_DDL` and `CENTRAL_SCHEMA_DDL` in
  `core/storage/schema.py`, with migration entry in `MIGRATIONS`.

Key methods:
- `record_issue(issue_type, target, severity, title, evidence)` — create or update
- `get_open_issues(type=None, severity=None)` — filtered query
- `get_issue(issue_id)` — single lookup
- `update_status(issue_id, status, resolution=None, resolution_type=None)`
- `get_issues_above_threshold(issue_type, min_occurrences)` — for auto-apply checks
- `get_history(limit=50)` — resolved issues with outcomes

## Component 2: Learning Engine

### LearningEngine (`core/learn/engine.py`)

Orchestrates the detect → log → analyze → apply cycle.

**Three operating modes:**

#### `detect(state: ExecutionState)`
Called automatically at execution completion. Scans for:

| Signal | Detection Method | Issue Type |
|--------|-----------------|------------|
| Wrong agent flavor for stack | Compare `--suffix` language vs `detected_stack` | `routing_mismatch` |
| Agent with high retry rate | `retries >= 2` in step results | `agent_degradation` |
| Agent failure | `status == "failed"` in step results | `agent_degradation` |
| Gate command/stack mismatch | Gate command language vs detected stack | `gate_mismatch` |
| Knowledge gap signals | `KNOWLEDGE_GAP` in agent output or high token usage | `knowledge_gap` |
| Classifier fallback event | `classification_source == "keyword-fallback"` | `roster_bloat` |
| Score health drop | `PerformanceScorer` health below threshold | `agent_degradation` |

Each detection writes/updates a `LearningIssue` in the ledger.

#### `analyze()`
Called by `baton learn analyze` or `ImprovementLoop.run_cycle()`.

1. Reads all open issues from ledger.
2. Groups by `issue_type`.
3. For each group, computes confidence: `min(1.0, occurrence_count / threshold)`.
4. Generates `Recommendation` objects that feed into existing proposal pipeline.
5. For issues crossing auto-apply thresholds, marks as `status="proposed"`.

#### `apply(issue_id, resolution_type="auto")`
Executes a fix. Dispatches to type-specific resolvers:

| Issue Type | Threshold | Resolution Action |
|---|---|---|
| `routing_mismatch` | 3 occurrences | Write FLAVOR_MAP override to `learned-overrides.json` |
| `agent_degradation` | 5 occurrences | Add to drop list; generate prompt evolution proposal |
| `knowledge_gap` | 3 occurrences | Create knowledge pack stub in `.claude/knowledge/` |
| `roster_bloat` | 3 occurrences | Adjust classifier scoring via overrides |
| `gate_mismatch` | 2 occurrences | Write gate command override to `learned-overrides.json` |
| `pattern_drift` | N/A | Interview required — cannot auto-apply |
| `prompt_evolution` | N/A | Generate draft prompt, apply after interview or experiment success |

All auto-applications create an `Experiment` for rollback safety.

## Component 3: Learned Overrides

### LearnedOverrides (`core/learn/overrides.py`)

Persists auto-applied corrections to `.claude/team-context/learned-overrides.json`.

```json
{
  "flavor_map": {
    "python/react": {"backend-engineer": "python", "frontend-engineer": "react"}
  },
  "gate_commands": {
    "typescript": {"test": "vitest run", "build": "npx tsc --noEmit"}
  },
  "agent_drops": ["visualization-expert"],
  "classifier_adjustments": {
    "min_keyword_overlap": 3
  },
  "version": 2,
  "last_updated": "2026-04-13T12:00:00Z"
}
```

**Consumers:**
- `AgentRouter.route()` — merges `flavor_map` overrides on top of hardcoded `FLAVOR_MAP`.
- `IntelligentPlanner._default_gate()` — merges `gate_commands` overrides.
- `IntelligentPlanner._apply_retro_feedback()` — includes `agent_drops` in drop set.
- `KeywordClassifier._select_agents()` — reads `classifier_adjustments`.

**Reversibility:** Delete specific keys or the entire file to revert.
`baton learn reset --issue ID` removes the corresponding override entry.

## Component 4: Structured Interview System

### LearningInterviewer (`core/learn/interviewer.py`)

Interactive CLI dialogue for human-directed learning decisions.

**Flow:**
1. Query ledger for issues needing human input (high severity, pattern_drift,
   prompt_evolution, or any issue the user wants to review).
2. Present one issue at a time with evidence summary and multiple-choice options.
3. Record decision as `resolution_type="interview"`.

**Example interaction:**
```
Issue #17: Agent Degradation — backend-engineer--python
Severity: medium | Occurrences: 7 | First seen: 2026-04-01

Evidence summary:
  - 4/7 tasks had retry rate >= 2
  - 2 negative mentions in retrospectives
  - First-pass rate dropped from 0.85 -> 0.52

What would you like to do?
  (a) Evolve agent prompt — generate improved prompt from failure patterns
  (b) Add knowledge pack — create targeted context for failure scenarios
  (c) Reduce routing priority — deprioritize for future plans
  (d) Investigate further — gather more data
  (e) Won't fix — suppress future alerts for this target
  (f) Skip — come back later
```

**Options per issue type:**

| Issue Type | Available Actions |
|---|---|
| `routing_mismatch` | Apply fix, Investigate, Won't fix, Skip |
| `agent_degradation` | Evolve prompt, Add knowledge, Reduce priority, Drop agent, Investigate, Won't fix, Skip |
| `knowledge_gap` | Create knowledge pack, Update agent prompt, Investigate, Won't fix, Skip |
| `pattern_drift` | Accept new pattern, Revert to old, Investigate, Skip |
| `prompt_evolution` | Review & apply draft, Edit draft, Reject, Skip |
| `roster_bloat` | Adjust thresholds, Lock agent list, Investigate, Skip |
| `gate_mismatch` | Apply fix, Custom command, Investigate, Won't fix, Skip |

**Targeting:** `baton learn interview --type routing_mismatch --severity high`

## Component 5: CLI Commands

New `learn` command group in "Improvement" category.

| Command | Purpose |
|---|---|
| `baton learn status` | Dashboard: open issues by type/severity, auto-apply stats, last analysis |
| `baton learn issues [--type T] [--severity S] [--status S]` | List issues with filters |
| `baton learn analyze` | Run analysis: detect patterns, generate recommendations |
| `baton learn apply [--issue ID] [--all-safe]` | Apply specific fix or all auto-applicable |
| `baton learn interview [--type T] [--severity S]` | Interactive structured dialogue |
| `baton learn history [--limit N]` | Resolution history with outcomes |
| `baton learn reset --issue ID` | Reopen issue / rollback applied fix |

## Component 6: Integration Points

### Automatic (no human action):
- `ExecutionEngine.complete()` → calls `LearningEngine.detect(state)`
- Issues crossing auto-apply thresholds → immediate `apply()`
- `ImprovementLoop.run_cycle()` → calls `LearningEngine.analyze()`

### Human-triggered:
- `baton learn interview` for directed decisions
- `baton learn apply --issue ID` for manual application
- `baton execute complete` output flags: "N learning issues need attention"

### Override consumption:
- `AgentRouter.route()` reads `learned-overrides.json` at call time
- `IntelligentPlanner` reads overrides during `create_plan()`
- `KeywordClassifier` reads overrides during `_select_agents()`

## File Layout

```
agent_baton/
  core/learn/
    engine.py          # LearningEngine (orchestrator)
    interviewer.py     # LearningInterviewer (structured dialogue)
    ledger.py          # LearningLedger (SQLite CRUD)
    overrides.py       # LearnedOverrides (read/write JSON)
    resolvers.py       # Type-specific resolution strategies
  models/
    learning.py        # LearningIssue, LearningEvidence dataclasses
  cli/commands/improve/
    learn_cmd.py       # baton learn CLI registration
```

## Testing Strategy

- **Unit tests** for each component: ledger CRUD, engine detection logic,
  override merging, interviewer question generation, resolver actions.
- **Integration tests** for the full loop: execution → detect → ledger →
  analyze → apply → verify override consumed.
- **Federation tests** for multi-project sync of learning_issues table.
- **Rollback tests** for experiment-based safety: apply → degrade → rollback.

## Documentation Updates

After implementation:
- `docs/architecture.md` — add learning automation to package layout
- `docs/design-decisions.md` — ADR entry for hybrid ledger approach
- `README.md` — add `baton learn` to CLI command reference
- `CLAUDE.md` — add learning module to repo structure, cross-layer linkage rules
