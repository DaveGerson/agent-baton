# Observe, Learn, and Improve Subsystems

Agent Baton includes a closed-loop learning pipeline that automatically
collects execution data, discovers patterns in that data, and proposes
(or auto-applies) improvements to future plans. The three subsystems --
Observe, Learn, and Improve -- form a feedback cycle that makes the
orchestration system better with every task it runs.

```
                         THE LEARNING LOOP

  +-----------+       +----------+       +-----------+
  |  OBSERVE  | ----> |  LEARN   | ----> |  IMPROVE  |
  |           |       |          |       |           |
  | traces    |       | patterns |       | scores    |
  | usage     |       | budget   |       | evolution |
  | retros    |       | recs     |       | VCS       |
  | telemetry |       |          |       | loop      |
  | profiles  |       |          |       | rollback  |
  +-----------+       +----------+       +-----------+
       ^                                       |
       |                                       |
       +--------- better next execution -------+

  execution --> trace --> usage --> retro --> pattern --> score
      --> recommendation --> experiment --> (auto-apply | escalate)
          --> rollback if degraded --> circuit breaker
```

---

## 1. Observe Subsystem

**Package:** `agent_baton.core.observe`

The observe layer captures everything that happens during an orchestrated
task execution. It produces the raw data that the learn and improve layers
consume.

### 1.1 Trace Recorder

**Module:** `agent_baton/core/observe/trace.py`
**Classes:** `TraceRecorder`, `TraceRenderer`
**Models:** `agent_baton/models/trace.py` -- `TaskTrace`, `TraceEvent`

The trace recorder captures a DAG of timestamped events for each
orchestrated task. Each event records what happened, which agent was
involved, and at which phase/step of the plan.

#### What Gets Traced

Every significant execution event is recorded as a `TraceEvent`:

| `event_type` | Description |
|--------------|-------------|
| `agent_start` | Agent dispatch begins |
| `agent_complete` | Agent finishes its work |
| `gate_check` | Gate check initiated |
| `gate_result` | Gate check outcome (PASS/FAIL) |
| `escalation` | Issue escalated to human |
| `replan` | Plan amended during execution |
| `file_read` | File read by an agent |
| `file_write` | File written by an agent |
| `decision` | Decision point reached |

#### TraceEvent Model

```python
@dataclass
class TraceEvent:
    timestamp: str          # ISO 8601
    event_type: str         # see table above
    agent_name: str | None  # which agent, if applicable
    phase: int              # plan phase number
    step: int               # step within phase
    details: dict           # event-specific metadata
    duration_seconds: float | None  # wall-clock duration
```

#### TaskTrace Model

```python
@dataclass
class TaskTrace:
    task_id: str
    plan_snapshot: dict         # the plan as it existed at start
    events: list[TraceEvent]    # ordered event list
    started_at: str             # ISO 8601
    completed_at: str | None    # ISO 8601, set on completion
    outcome: str | None         # "SHIP", "REVISE", etc.
```

#### Storage

Traces are persisted as JSON files at:
```
.claude/team-context/traces/<task_id>.json
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `start_trace(task_id, plan_snapshot)` | Create a new in-memory trace |
| `record_event(trace, event_type, ...)` | Append an event to a trace |
| `complete_trace(trace, outcome)` | Finalize and write trace to disk |
| `load_trace(task_id)` | Load a trace from disk by task ID |
| `list_traces(count)` | List the N most recent trace files |
| `get_last_trace()` | Load the most recently modified trace |

#### TraceRenderer

The `TraceRenderer` class formats traces for human consumption:

- **`render_timeline(trace)`** -- Full timeline grouped by phase, showing
  time, event type, agent name, duration, and primary detail.
- **`render_summary(trace)`** -- Compact one-screen summary with task ID,
  outcome, duration, event count, agents involved, gate results, and event
  type breakdown.

---

### 1.2 Usage Logger

**Module:** `agent_baton/core/observe/usage.py`
**Class:** `UsageLogger`
**Models:** `agent_baton/models/usage.py` -- `TaskUsageRecord`, `AgentUsageRecord`

The usage logger tracks token consumption, agent utilization, retry
counts, gate results, and outcomes for every orchestrated task.

#### AgentUsageRecord

```python
@dataclass
class AgentUsageRecord:
    name: str                          # agent name
    model: str = "sonnet"              # LLM model used
    steps: int = 1                     # steps executed
    retries: int = 0                   # retry count
    gate_results: list[str] = []       # "PASS" / "FAIL" per gate
    estimated_tokens: int = 0          # approximate token usage
    duration_seconds: float = 0.0      # wall-clock time
```

#### TaskUsageRecord

```python
@dataclass
class TaskUsageRecord:
    task_id: str
    timestamp: str                     # ISO 8601
    agents_used: list[AgentUsageRecord]
    total_agents: int = 0
    risk_level: str = "LOW"            # LOW / MEDIUM / HIGH
    sequencing_mode: str = "phased_delivery"
    gates_passed: int = 0
    gates_failed: int = 0
    outcome: str = ""                  # "SHIP", "SHIP WITH NOTES", "REVISE", "BLOCK"
    notes: str = ""
```

#### Storage

Usage records are appended as JSONL (newline-delimited JSON) to:
```
.claude/team-context/usage-log.jsonl
```

JSONL was chosen so records can be appended without loading the entire
file into memory.

#### Key Methods

| Method | Purpose |
|--------|---------|
| `log(record)` | Append a `TaskUsageRecord` to the log |
| `read_all()` | Read all records from the log file |
| `read_recent(count)` | Read the N most recent records |
| `summary()` | Aggregate stats: total tasks, tokens, agents, outcomes, risk distribution |
| `agent_stats(agent_name)` | Per-agent stats: times used, retries, gate pass rate, models |

#### Summary Aggregation

`summary()` returns:

| Field | Description |
|-------|-------------|
| `total_tasks` | Number of tasks recorded |
| `total_agents_used` | Sum of agents across all tasks |
| `total_estimated_tokens` | Total token consumption |
| `avg_agents_per_task` | Mean agents per task |
| `avg_retries_per_task` | Mean retries per task |
| `outcome_counts` | Dict of outcome -> count |
| `risk_level_counts` | Dict of risk level -> count |
| `agent_frequency` | Dict of agent name -> usage count |

---

### 1.3 Dashboard Generator

**Module:** `agent_baton/core/observe/dashboard.py`
**Class:** `DashboardGenerator`

The dashboard generator produces a comprehensive markdown document from
usage logs and telemetry data. It integrates data from both `UsageLogger`
and `AgentTelemetry` into a single report.

#### Dashboard Sections

1. **Overview** -- Total tasks, agent uses, estimated tokens, avg
   agents/task, avg retries/agent, gate pass rate.
2. **Outcomes** -- Outcome distribution table (SHIP, REVISE, BLOCK, etc.).
3. **Risk Distribution** -- Tasks by risk level (LOW, MEDIUM, HIGH,
   CRITICAL).
4. **Model Mix** -- Which LLM models were used and how often.
5. **Agent Utilization** -- Per-agent usage count and average retry rate.
6. **Sequencing Modes** -- Distribution of sequencing modes across tasks.
7. **Telemetry** (if data exists) -- Event counts by agent and by type,
   files read/written counts.

#### Storage

Dashboards are written to:
```
.claude/team-context/usage-dashboard.md
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `generate()` | Return the full dashboard as a markdown string |
| `write(path)` | Write the dashboard to disk (default path above) |

---

### 1.4 Retrospective Engine

**Module:** `agent_baton/core/observe/retrospective.py`
**Class:** `RetrospectiveEngine`
**Models:** `agent_baton/models/retrospective.py` -- `Retrospective`,
`AgentOutcome`, `KnowledgeGap`, `RosterRecommendation`, `SequencingNote`
**Models:** `agent_baton/models/feedback.py` -- `RetrospectiveFeedback`

The retrospective engine generates structured post-task analysis that
captures both quantitative metrics and qualitative signals. It is the
primary bridge between observation and learning.

#### Retrospective Model

```python
@dataclass
class Retrospective:
    task_id: str
    task_name: str
    timestamp: str

    # Quantitative metrics
    agent_count: int
    retry_count: int
    gates_passed: int
    gates_failed: int
    risk_level: str
    estimated_tokens: int

    # Qualitative signals
    what_worked: list[AgentOutcome]         # agents that performed well
    what_didnt: list[AgentOutcome]          # agents that had issues
    knowledge_gaps: list[KnowledgeGapRecord] # missing knowledge identified
    roster_recommendations: list[RosterRecommendation]
    sequencing_notes: list[SequencingNote]
```

#### Supporting Models

- **`AgentOutcome`** -- Per-agent result with `name`, `worked_well`,
  `issues`, and `root_cause` fields.
- **`KnowledgeGapRecord`** -- Structured record of missing knowledge with
  `description`, `gap_type`, `resolution`, `resolution_detail`,
  `agent_name`, `task_summary`, `task_type`.
- **`RosterRecommendation`** -- Suggested agent roster change:
  `action` (create/improve/remove), `target` (agent or pack name),
  `reason`.
- **`SequencingNote`** -- Phase-level observation: `phase`, `observation`,
  `keep` (whether the phase/gate should be retained).

#### Implicit Knowledge Gap Detection

The engine scans narrative text in `what_didnt` outcomes for phrases that
indicate implicit knowledge gaps. Detected phrases include:

- "lacked context"
- "didn't know about"
- "assumed incorrectly"
- "no knowledge of"
- "unaware of"
- "missing context"
- "lacked/lacking information"

Implicit gaps are merged with explicit gaps (from `KNOWLEDGE_GAP` signals
during execution), with explicit gaps taking precedence when descriptions
match.

#### Storage

Retrospectives are persisted as two files:
```
.claude/team-context/retrospectives/<task_id>.md    # human-readable
.claude/team-context/retrospectives/<task_id>.json   # machine-readable sidecar
```

#### RetrospectiveFeedback

The `RetrospectiveFeedback` model aggregates actionable signals from
recent retrospectives for consumption by the planner:

```python
@dataclass
class RetrospectiveFeedback:
    roster_recommendations: list[RosterRecommendation]
    knowledge_gaps: list[KnowledgeGapRecord]
    sequencing_notes: list[SequencingNote]
    source_count: int

    def agents_to_drop() -> set[str]    # agents recommended for removal
    def agents_to_prefer() -> set[str]  # agents recommended for preferring
    def has_feedback() -> bool           # any actionable feedback exists?
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `generate_from_usage(usage, ...)` | Generate a retrospective from a usage record plus qualitative input |
| `save(retro)` | Write retrospective to disk (markdown + JSON sidecar) |
| `load(task_id)` | Read a retrospective markdown by task ID |
| `list_retrospectives()` | List all retrospective files |
| `list_recent(count)` | Return the N most recent retrospective files |
| `search(keyword)` | Find retrospectives containing a keyword |
| `load_recent_feedback(limit)` | Aggregate structured feedback from recent JSON sidecars |
| `extract_recommendations()` | Extract roster recommendations from all retrospective markdown |

---

### 1.5 Context Profiler

**Module:** `agent_baton/core/observe/context_profiler.py`
**Class:** `ContextProfiler`
**Models:** `agent_baton/models/context_profile.py` -- `TaskContextProfile`,
`AgentContextProfile`

The context profiler analyses trace data to compute per-agent context
efficiency metrics. It helps identify agents that are reading too broadly
(wasting context window tokens) or not reading enough (missing relevant
files).

#### Efficiency Score

The core metric is the efficiency score:

```
efficiency_score = len(files_written) / max(len(files_read), 1)
```

An agent that reads 10 files but writes 2 has an efficiency of 0.2. An
agent that reads 5 files and writes 5 has an efficiency of 1.0. Agents
with scores below 0.3 are flagged as "broad readers."

#### Redundancy Rate

At the task level, redundancy measures how many file reads were duplicated
across agents:

```
redundancy_rate = (total_reads - unique_reads) / total_reads
```

A redundancy rate of 0.4 means 40% of file reads were reading files
already read by another agent in the same task.

#### Models

```python
@dataclass
class AgentContextProfile:
    agent_name: str
    files_read: list[str]
    files_written: list[str]
    files_referenced: list[str]       # files both read AND written
    context_tokens_estimate: int      # approx input tokens (reads * 500)
    output_tokens_estimate: int       # approx output tokens (writes * 200)
    efficiency_score: float           # writes / max(reads, 1)

@dataclass
class TaskContextProfile:
    task_id: str
    agent_profiles: list[AgentContextProfile]
    total_files_read: int
    unique_files_read: int
    redundant_reads: int
    redundancy_rate: float
    created_at: str
```

#### Storage

Context profiles are persisted as JSON files at:
```
.claude/team-context/context-profiles/<task_id>.json
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `profile_task(task_id)` | Build a profile from a task's trace data |
| `save_profile(profile)` | Persist a profile to disk |
| `load_profile(task_id)` | Load a profile from disk |
| `list_profiles(count)` | List the N most recent profile files |
| `agent_summary(agent_name)` | Aggregate stats for an agent across all profiles |
| `generate_report()` | Produce a markdown context efficiency report |

---

### 1.6 Telemetry

**Module:** `agent_baton/core/observe/telemetry.py`
**Class:** `AgentTelemetry`
**Data class:** `TelemetryEvent` (defined in same module)

Telemetry captures real-time agent tool-call events at a finer granularity
than traces. Where traces capture high-level orchestration events,
telemetry captures individual tool invocations.

#### TelemetryEvent

```python
@dataclass
class TelemetryEvent:
    timestamp: str
    agent_name: str
    event_type: str     # "tool_call", "file_read", "file_write", "bash_exec", "error"
    tool_name: str      # which tool was called
    file_path: str      # file involved, if applicable
    duration_ms: int    # wall-clock milliseconds
    details: str        # additional context
```

#### Storage

Telemetry events are appended as JSONL to:
```
.claude/team-context/telemetry.jsonl
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `log_event(event)` | Append a telemetry event to the log |
| `read_events(agent_name)` | Read all events, optionally filtered by agent |
| `read_recent(count)` | Read the N most recent events |
| `summary()` | Aggregate: total events, by agent, by type, files read/written |
| `clear()` | Clear the telemetry log (between tasks) |

---

### 1.7 Data Archiver

**Module:** `agent_baton/core/observe/archiver.py`
**Class:** `DataArchiver`

The data archiver provides retention-based cleanup for all execution
artifacts. It keeps the `.claude/team-context/` directory manageable as
task volume grows.

#### What Gets Cleaned

| Category | Path | File Pattern |
|----------|------|-------------|
| Executions | `executions/` | Subdirectories |
| Traces | `traces/` | `*.json` |
| Events | `events/` | `*.jsonl` |
| Retrospectives | `retrospectives/` | All files (`.md` + `.json`) |
| Context Profiles | `context-profiles/` | `*.json` |

Additionally, JSONL logs (`telemetry.jsonl`) are rotated by keeping only
the last 10,000 lines.

#### Key Methods

| Method | Purpose |
|--------|---------|
| `scan(retention_days)` | Read-only scan for files older than retention period |
| `cleanup(retention_days, dry_run)` | Remove old files (with dry-run option) |
| `summary(retention_days)` | Human-readable report of what would be cleaned |

Default retention is 90 days.

---

### 1.8 Observe CLI Commands

| Command | Description |
|---------|-------------|
| `baton trace` | List recent traces |
| `baton trace TASK_ID` | Show timeline for a specific task |
| `baton trace --last` | Show timeline for the most recent task |
| `baton trace --summary TASK_ID` | Show compact summary |
| `baton usage` | Show aggregate usage summary |
| `baton usage --recent N` | Show the N most recent usage records |
| `baton usage --agent NAME` | Show stats for a specific agent |
| `baton dashboard` | Print the usage dashboard |
| `baton dashboard --write` | Write the dashboard to disk |
| `baton telemetry` | Show telemetry summary |
| `baton telemetry --agent NAME` | Show events for a specific agent |
| `baton telemetry --recent N` | Show the N most recent events |
| `baton telemetry --clear` | Clear the telemetry log |
| `baton context-profile` | List recent context profiles |
| `baton context-profile TASK_ID` | Show profile for a specific task |
| `baton context-profile --generate TASK_ID` | Generate a profile from trace data |
| `baton context-profile --agent NAME` | Show aggregate stats for an agent |
| `baton context-profile --report` | Print a full context efficiency report |
| `baton retro` | List recent retrospectives |
| `baton retro --task-id ID` | Show a specific retrospective |
| `baton retro --search KEYWORD` | Search retrospectives by keyword |
| `baton retro --recommendations` | Extract roster recommendations |
| `baton cleanup` | Remove old execution artifacts |
| `baton cleanup --retention-days N` | Set retention period (default 90 days) |
| `baton cleanup --dry-run` | Preview what would be removed |

### 1.9 Observe API Endpoints

**Module:** `agent_baton/api/routes/observe.py`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard` | GET | Pre-rendered usage dashboard as markdown |
| `/traces/{task_id}` | GET | Structured trace JSON for a completed task |
| `/usage` | GET | Usage records with optional `since` and `agent` filters |

---

## 2. Learn Subsystem

**Package:** `agent_baton.core.learn`

The learn layer analyses the data collected by the observe layer and
discovers actionable patterns. It turns raw execution history into
knowledge the system can use for better planning.

### 2.1 Pattern Learner

**Module:** `agent_baton/core/learn/pattern_learner.py`
**Class:** `PatternLearner`
**Model:** `agent_baton/models/pattern.py` -- `LearnedPattern`

**Status:** Experimental -- built and tested but not yet validated with
real usage data.

The pattern learner analyses completed task records from the usage log,
groups them by `sequencing_mode` (used as a proxy for task type), and
computes per-group statistics. Groups that meet minimum thresholds are
emitted as `LearnedPattern` objects.

#### How Patterns Are Discovered

1. Read all `TaskUsageRecord` entries from the usage log.
2. Group records by `sequencing_mode`.
3. For each group with >= `min_sample_size` (default 5) records:
   - Compute `success_rate` = count(outcome == "SHIP") / total.
   - Find the most common agent combination (sorted tuple of agent names).
   - Compute `avg_token_cost` from successful tasks (falls back to all if
     none succeeded).
   - Compute `avg_retries` and `avg_gate_rate`.
   - Apply the confidence formula.
4. Filter by `min_confidence` (default 0.7).
5. Return patterns sorted by confidence descending.

#### Confidence Formula

```
confidence = min(1.0, (sample_size / 15) * success_rate)
```

This ramps linearly from 0 at 0 samples to 1.0 at 15+ samples with
perfect success. The divisor 15 is a calibration constant.

#### LearnedPattern Model

```python
@dataclass
class LearnedPattern:
    pattern_id: str                  # e.g. "phased_delivery-001"
    task_type: str                   # derived from sequencing_mode
    stack: str | None                # tech stack qualifier, None = any
    recommended_template: str        # human-readable workflow description
    recommended_agents: list[str]    # best agent combination observed
    confidence: float                # [0.0, 1.0]
    sample_size: int                 # records that contributed
    success_rate: float              # fraction with outcome=="SHIP"
    avg_token_cost: int              # mean tokens across successful tasks
    evidence: list[str]              # contributing task_ids
    created_at: str                  # ISO 8601
    updated_at: str                  # ISO 8601
```

#### Storage

Patterns are persisted to:
```
.claude/team-context/learned-patterns.json
```

#### Knowledge Gap Querying

The pattern learner also provides `knowledge_gaps_for(agent_name,
task_type)` which reads retrospective JSON sidecars and returns prior
knowledge gap records matching a given agent. This enables the planner
to attach relevant knowledge packs to agents that previously lacked
context.

#### Key Methods

| Method | Purpose |
|--------|---------|
| `analyze(min_sample_size, min_confidence)` | Read usage log and return qualifying patterns |
| `refresh(min_sample_size, min_confidence)` | Re-analyse and write to `learned-patterns.json` |
| `load_patterns()` | Read patterns from disk |
| `get_patterns_for_task(task_type, stack)` | Filter stored patterns by task type and stack |
| `recommend_sequencing(task_type)` | Return the optimal agent sequence for a task type |
| `knowledge_gaps_for(agent_name, task_type)` | Query prior knowledge gaps for an agent |
| `generate_report()` | Generate a markdown report of all patterns |

---

### 2.2 Budget Tuner

**Module:** `agent_baton/core/learn/budget_tuner.py`
**Class:** `BudgetTuner`
**Model:** `agent_baton/models/budget.py` -- `BudgetRecommendation`

**Status:** Experimental -- built and tested but not yet validated with
real usage data.

The budget tuner analyses historical token usage and recommends budget
tier changes. It ensures tasks are not over-provisioned (wasting tokens)
or under-provisioned (running out of budget).

#### Budget Tiers

| Tier | Token Range | Midpoint |
|------|-------------|----------|
| **Lean** | 0 -- 50,000 | 25,000 |
| **Standard** | 50,001 -- 500,000 | 275,000 |
| **Full** | 500,001+ | 750,000 |

#### Recommendation Rules

For each group of tasks (grouped by `sequencing_mode`) with >= 3 records:

- **Upgrade:** median usage > 80% of current tier's upper bound.
- **Downgrade:** 95th-percentile usage < current tier's lower bound.

#### Confidence Formula

```
confidence = min(1.0, sample_size / 10)
```

#### Auto-Apply Guardrail

Only **downgrade** recommendations are eligible for auto-application.
Budget upgrades (to more expensive tiers) always require human approval.

#### BudgetRecommendation Model

```python
@dataclass
class BudgetRecommendation:
    task_type: str              # grouping key
    current_tier: str           # inferred from median usage
    recommended_tier: str       # after applying rules
    reason: str                 # human-readable explanation
    avg_tokens_used: int
    median_tokens_used: int
    p95_tokens_used: int
    sample_size: int
    confidence: float
    potential_savings: int      # estimated tokens saved per task (downgrades only)
```

#### Storage

Recommendations are persisted to:
```
.claude/team-context/budget-recommendations.json
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `analyze()` | Read usage log and return budget recommendations |
| `recommend()` | Generate a human-readable markdown report |
| `save_recommendations()` | Analyse and write to disk |
| `auto_apply_recommendations(threshold)` | Return only downgrade recommendations above confidence threshold |
| `load_recommendations()` | Read previously saved recommendations |

---

### 2.3 Unified Recommender

**Module:** `agent_baton/core/learn/recommender.py`
**Class:** `Recommender`
**Model:** `agent_baton/models/improvement.py` -- `Recommendation`

The recommender aggregates analysis from all engines (scorer, pattern
learner, budget tuner, evolution engine) into a unified, deduplicated,
ranked list of recommendations.

#### Recommendation Categories

| Category | Description |
|----------|-------------|
| `agent_prompt` | Changes to agent prompt definitions |
| `budget_tier` | Budget tier adjustments |
| `routing` | Agent routing weight changes |
| `sequencing` | Task sequencing pattern changes |
| `gate_config` | Gate configuration changes |
| `roster` | Agent roster changes |

#### Guardrail Enforcement

The recommender enforces strict safety rules on what can be auto-applied:

| Category | Auto-Apply Rule |
|----------|-----------------|
| `agent_prompt` | **NEVER** auto-apply. Always escalate to human. |
| `budget_tier` | Only **downgrades** (cheaper tier). Never auto-upgrade. |
| `routing` | Only if confidence >= 0.9 **and** additive change. |
| `sequencing` | Only if confidence >= 0.8 **and** success rate >= 0.9. |
| Other | Only LOW-risk recommendations auto-apply. |

#### Recommendation Model

```python
@dataclass
class Recommendation:
    rec_id: str                 # unique identifier
    category: str               # see categories above
    target: str                 # agent name, task type, etc.
    action: str                 # "downgrade budget", "evolve prompt", etc.
    description: str            # human-readable explanation
    evidence: list[str]         # supporting data references
    confidence: float           # [0.0, 1.0]
    risk: str                   # "low", "medium", "high"
    auto_applicable: bool       # whether auto-apply is permitted
    proposed_change: dict       # machine-readable change specification
    rollback_spec: dict         # how to undo the change
    created_at: str             # ISO 8601
    status: str = "proposed"    # proposed -> applied -> rolled_back | rejected
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `analyze()` | Run all engines and return deduplicated, ranked recommendations |

---

### 2.4 How Learn Feeds Back Into Planning

The learn subsystem's outputs are consumed by the engine's planner:

1. **Learned patterns** inform agent selection and sequencing. When the
   planner creates a new plan, it queries `PatternLearner.recommend_sequencing()`
   to find the best agent sequence for the task type.

2. **Budget recommendations** inform token allocation. The planner can
   query `BudgetTuner.load_recommendations()` to right-size budget tiers.

3. **Knowledge gaps** (from `PatternLearner.knowledge_gaps_for()`) tell
   the planner which knowledge packs to attach to agents that previously
   lacked context.

4. **Retrospective feedback** (via `RetrospectiveFeedback`) provides
   `agents_to_drop()` and `agents_to_prefer()` sets that the planner
   uses to adjust agent routing.

---

### 2.5 Honest Current State and Learning Cycle Pipeline

**Last updated:** 2026-04-17

This section documents what each learn-subsystem component actually does
today, which parts are deprecated, and how the replacement pipeline works.

#### Component Status

**PerformanceScorer** — Working. Computes per-agent `first_pass_rate`,
`retry_rate`, `gate_pass_rate`, `negative_mentions`, and `knowledge_gaps_cited`
by reading `usage-log.jsonl` or the SQLite backend. These are rate calculations
across all historical records with no statistical significance test: an agent
with 3 uses and 2 failures shows a 67% failure rate with the same weight as
an agent with 50 uses and 33 failures.

**PatternLearner** — Working. Groups completed tasks by `sequencing_mode` and
identifies the most common agent combination per group. The `evidence_strength`
score (formerly called "confidence") is a heuristic ramp, not a statistically
validated confidence interval. Minimum sample requirement: `min_sample_size`
records (default 3) before a group is considered.

**BudgetTuner** — Working. Reads usage records and compares actual token
consumption to the nominal midpoint of the current budget tier. Suggestions
are output to `budget-recommendations.json` — they are proposals, not
decisions.

**ImprovementLoop** — Working. Calls `TriggerEvaluator`, then `Recommender`
(which aggregates PerformanceScorer, PatternLearner, BudgetTuner outputs),
classifies each recommendation as auto-applicable or escalated, and persists
an `ImprovementReport`. Auto-apply guardrails: prompt changes never auto-apply;
budget upgrades never auto-apply; routing reductions never auto-apply; all
others only if `risk == "low"` and `evidence_strength >= threshold`.

**LearnedOverrides** — Working. Persists operational corrections (agent flavor
mismatches, gate command adjustments) to `learned-overrides.json`. Applied
automatically on the next execution without going through the full pipeline.

**ExperimentManager** (`agent_baton/core/improve/experiments.py`) —
**DEPRECATED.** The module recorded before/after metrics but never ran
concurrent A/B groups. Do not add new callers. The replacement is
before/after scorecard comparison across learning cycles (see Pipeline
below).

**PromptEvolutionEngine** (`agent_baton/core/improve/evolution.py`) —
**DEPRECATED.** Was a template-based rule engine that matched scorecard
thresholds to canned suggestion strings. Suggestions were generic, not
derived from actual retrospective content. Do not add new callers. The
replacement is the `learning-analyst` agent (D2).

#### Evidence Strength vs. Confidence

Across the codebase, fields previously named `confidence` have been
relabeled to `evidence_strength` (or equivalent) to avoid implying
statistical validation. The formula is a heuristic:

```
evidence_strength = min(1.0, (sample_size / 15) * success_rate)
```

The default threshold for acting on a pattern is 0.35 (reduced from 0.7).
In the CLI, patterns are displayed as "Seen N times, X% success rate"
rather than as a numeric confidence score.

#### The Learning Cycle Pipeline

The learning system is being refactored from inline analysis code into a
repeatable baton execution plan template at:

```
templates/learning-cycle-plan.json
```

| Phase | Agent | What It Does |
|-------|-------|-------------|
| COLLECT | test-engineer | SQL queries gather scorecards, pattern data, retrospective summaries, and knowledge gaps from the last N executions. Writes a data bundle to the team-context directory. |
| ANALYZE | learning-analyst | Reads the collected data bundle. Identifies patterns and failures with specific evidence citations. |
| PROPOSE | learning-analyst | Outputs actionable recommendations. Each proposal names the specific agent, the specific failure mode, and the specific change. |
| REVIEW | (APPROVAL gate) | Human reviews proposals before anything is applied. |
| APPLY | backend-engineer | Writes approved changes to `learned-overrides.json`, agent definition files, or knowledge packs. |
| DOCUMENT | documentation-architect | Records what changed, why, and what outcome is expected as a retrospective entry. |

Operational corrections (routing mismatches, gate command fixes) bypass
the pipeline and go directly to the `LearnedOverrides` auto-apply path.

#### Triggering a Learning Cycle

**Manual trigger:**

```bash
baton learn run-cycle             # create the plan and print it
baton learn run-cycle --run       # create and execute immediately
baton learn run-cycle --dry-run   # print the baton execute run command without executing
```

**Counter-based trigger:**

`TriggerEvaluator` tracks completed executions since the last learning
cycle. When the count reaches the threshold (configurable via
`LEARNING_TRIGGER_COUNT` env var, default 10), `baton execute status`
reports:

```
Learning cycle recommended (N executions since last cycle)
```

The cycle does NOT run automatically — this is a flag, not an auto-trigger.
The operator or daemon runs it explicitly.

#### Capabilities Summary

| Capability | Status | Notes |
|-----------|--------|-------|
| Track per-agent success/failure rates | Working | PerformanceScorer |
| Identify common agent sequences | Working | PatternLearner |
| Suggest budget tier adjustments | Working | BudgetTuner |
| Auto-apply safe operational corrections | Working | LearnedOverrides |
| Human-reviewed proposal pipeline | Working | ProposalManager |
| Statistical significance tests | Not present | Not planned |
| Concurrent A/B experiments | Not present | ExperimentManager deprecated |
| Template-based prompt suggestions | Deprecated | PromptEvolutionEngine deprecated |
| Agent-driven retrospective analysis | In progress | learning-analyst agent (D2) |
| Repeatable learning cycle template | In progress | templates/learning-cycle-plan.json (D2) |

---

## 3. Improve Subsystem

**Package:** `agent_baton.core.improve`

The improve layer acts on the patterns and recommendations produced by
the learn layer. It scores agents, proposes prompt changes, manages agent
version control, runs experiments, and handles rollbacks.

### 3.1 Performance Scorer

**Module:** `agent_baton/core/improve/scoring.py`
**Classes:** `PerformanceScorer`, `AgentScorecard`

The performance scorer computes per-agent health assessments by combining
quantitative metrics from usage logs with qualitative signals from
retrospectives.

#### AgentScorecard

```python
@dataclass
class AgentScorecard:
    agent_name: str
    times_used: int
    first_pass_rate: float       # % of uses with 0 retries
    retry_rate: float            # average retries per use
    gate_pass_rate: float | None # % of gates passed
    total_estimated_tokens: int
    avg_tokens_per_use: int
    models_used: dict[str, int]

    # Qualitative signals from retrospectives
    positive_mentions: int
    negative_mentions: int
    knowledge_gaps_cited: int
```

#### Health Rating

The `health` property applies a simple decision tree:

| Condition | Health |
|-----------|--------|
| `times_used == 0` | `"unused"` |
| `first_pass_rate >= 0.8` and `negative_mentions == 0` | `"strong"` |
| `first_pass_rate >= 0.5` | `"adequate"` |
| Otherwise | `"needs-improvement"` |

#### Qualitative Signal Extraction

The scorer scans retrospective markdown for agent name mentions within
specific sections:

- Mentions in "What Worked" sections count as positive.
- Mentions in "What Didn't" sections count as negative.
- Mentions in "Knowledge Gaps" sections count as knowledge gaps cited.

When a `StorageBackend` is provided, retrospectives are read from the
database rather than the filesystem, ensuring SQLite-mode projects return
current data.

#### Trend Detection

`detect_trends(agent_name, window)` uses linear regression on the
agent's binary first-pass success (1 if zero retries, 0 otherwise) over
the last N tasks:

| Slope | Trend |
|-------|-------|
| > 0.02 | `"improving"` |
| < -0.02 | `"degrading"` |
| Otherwise | `"stable"` |

#### Key Methods

| Method | Purpose |
|--------|---------|
| `score_agent(agent_name)` | Compute a scorecard for a single agent |
| `score_all()` | Compute scorecards for all agents in usage logs |
| `generate_report()` | Generate a full markdown scorecard report |
| `detect_trends(agent_name, window)` | Detect performance trend over recent tasks |
| `write_report(path)` | Write the scorecard report to disk |

#### Storage

Reports are written to:
```
.claude/team-context/agent-scorecards.md
```

---

### 3.2 Prompt Evolution Engine

**Module:** `agent_baton/core/improve/evolution.py`
**Classes:** `PromptEvolutionEngine`, `EvolutionProposal`

**Status:** Experimental -- built and tested but not yet validated with
real usage data.

The prompt evolution engine analyses agent performance and generates
structured proposals for prompt improvements. It identifies
underperforming agents and suggests specific changes based on their
failure patterns.

#### EvolutionProposal

```python
@dataclass
class EvolutionProposal:
    agent_name: str
    scorecard: AgentScorecard
    issues: list[str]        # problems identified
    suggestions: list[str]   # proposed changes
    priority: str            # "high" (needs-improvement) or "normal" (adequate)
    timestamp: str
```

#### Issue Detection Rules

| Condition | Issue | Suggestion |
|-----------|-------|------------|
| `first_pass_rate < 0.5` | Low first-pass rate | Add specific failure mode instructions; include negative examples |
| `first_pass_rate < 0.8` | Moderate first-pass rate | Review retry patterns in retrospectives |
| `retry_rate > 1.0` | High retry rate | Tighten acceptance criteria |
| `gate_pass_rate < 0.7` | Low gate pass rate | Add quality checklist to prompt |
| `negative_mentions > 0` | Negative retrospective mentions | Address specific failures from "What Didn't Work" |
| `knowledge_gaps_cited > 0` | Knowledge gaps | Create/update knowledge packs; add "Before Starting" section |

#### Workflow

1. **`analyze()`** -- Score all agents, identify underperformers, generate
   proposals sorted by priority (high first, then by first-pass rate
   ascending).
2. **`propose_for_agent(name)`** -- Generate a proposal for a specific agent.
3. **`save_proposals(proposals)`** -- Write proposals as markdown files.
4. **`generate_report()`** / **`write_report()`** -- Summary report.

#### Storage

Proposals are written to:
```
.claude/team-context/evolution-proposals/<agent_name>.md
```

Reports are written to:
```
.claude/team-context/evolution-report.md
```

---

### 3.3 Agent Version Control

**Module:** `agent_baton/core/improve/vcs.py`
**Classes:** `AgentVersionControl`, `ChangelogEntry`

The VCS system tracks changes to agent definition files with timestamped
backups and a changelog. It enables safe prompt evolution by ensuring
every change is reversible.

#### ChangelogEntry

```python
@dataclass
class ChangelogEntry:
    timestamp: str       # ISO 8601
    agent_name: str
    action: str          # "created", "modified", "archived"
    summary: str         # what changed and why
    backup_path: str     # path to .bak file if modified
```

#### Backup Strategy

Backups are stored at:
```
agents/.backups/<agent-name>.<YYYYMMDD-HHMMSS>.md
```

The changelog is stored at:
```
agents/changelog.md
```

New entries are prepended after the header so the most recent change
appears first.

#### Key Methods

| Method | Purpose |
|--------|---------|
| `backup_agent(agent_path)` | Create a timestamped backup before modification |
| `log_change(entry)` | Append a changelog entry |
| `read_changelog()` | Parse and return all changelog entries |
| `get_agent_history(agent_name)` | Return changelog entries for a specific agent |
| `list_backups(agent_name)` | List backup files, optionally filtered by agent |
| `restore_backup(backup_path, target_path)` | Restore a backup (creates safety backup first) |
| `track_modification(agent_path, summary)` | Back up + log change (primary API) |
| `track_creation(agent_path, summary)` | Log creation of a new agent |

---

### 3.4 Improvement Loop

**Module:** `agent_baton/core/improve/loop.py`
**Class:** `ImprovementLoop`
**Model:** `agent_baton/models/improvement.py` -- `ImprovementReport`,
`ImprovementConfig`

The improvement loop is the closed-loop orchestrator that wires together
all the subsystems. It checks triggers, generates recommendations,
classifies them, auto-applies safe ones, creates experiments to track
impact, and escalates risky ones to humans.

#### ImprovementLoop Cycle

```
run_cycle(force=False)
    |
    +-- Check circuit breaker (3+ rollbacks in 7 days -> skip)
    |
    +-- Check triggers (enough new data since last analysis?)
    |
    +-- Detect anomalies (failure rates, gate failures, budget overruns)
    |
    +-- Generate recommendations (via Recommender.analyze())
    |
    +-- Persist all recommendations (via ProposalManager)
    |
    +-- Classify each recommendation:
    |       |
    |       +-- Auto-apply? (low risk + high confidence + permitted category)
    |       |     -> Apply and create an experiment to track impact
    |       |
    |       +-- Escalate? (high risk, or prompt change, or low confidence)
    |             -> Record for human review
    |
    +-- Evaluate running experiments (auto-rollback degraded ones)
    |
    +-- Mark analysis as done (update trigger watermark)
    |
    +-- Save and return ImprovementReport
```

#### Auto-Apply Classification

A recommendation is auto-applied only when ALL of these conditions are met:

1. `rec.category != "agent_prompt"` (prompt changes NEVER auto-apply)
2. `rec.auto_applicable == True` (set by the Recommender's guardrails)
3. `rec.risk == "low"`
4. `rec.confidence >= config.auto_apply_threshold` (default 0.8)

#### ImprovementReport Model

```python
@dataclass
class ImprovementReport:
    report_id: str
    timestamp: str
    skipped: bool                       # True if cycle was skipped
    reason: str                         # why skipped, if applicable
    anomalies: list[dict]               # detected anomalies
    recommendations: list[dict]         # all recommendations generated
    auto_applied: list[str]             # rec_ids that were auto-applied
    escalated: list[str]                # rec_ids escalated for human review
    active_experiments: list[str]       # experiment_ids created this cycle
```

#### ImprovementConfig Model

```python
@dataclass
class ImprovementConfig:
    auto_apply_threshold: float = 0.8   # minimum confidence for auto-apply
    paused: bool = False                # manual pause switch
```

#### Storage

Reports are stored at:
```
.claude/team-context/improvements/reports/<report_id>.json
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `run_cycle(force)` | Run a complete improvement cycle |
| `evaluate_experiments()` | Evaluate all running experiments |
| `load_reports()` | Load all improvement reports |

---

### 3.5 Trigger Evaluator

**Module:** `agent_baton/core/improve/triggers.py`
**Class:** `TriggerEvaluator`
**Model:** `agent_baton/models/improvement.py` -- `TriggerConfig`, `Anomaly`

The trigger evaluator decides when to run the improvement pipeline and
detects system anomalies.

#### Analysis Triggers

Analysis runs when both conditions are met:

1. At least `min_tasks_before_analysis` (default 10) total tasks exist.
2. At least `analysis_interval_tasks` (default 5) new tasks have been
   recorded since the last analysis.

#### TriggerConfig

```python
@dataclass
class TriggerConfig:
    min_tasks_before_analysis: int = 10
    analysis_interval_tasks: int = 5
    agent_failure_threshold: float = 0.3
    gate_failure_threshold: float = 0.2
    budget_deviation_threshold: float = 0.5
    confidence_threshold: float = 0.7
```

#### Anomaly Detection

`detect_anomalies()` scans usage data for four types of anomalies:

| Anomaly Type | Condition | Severity |
|-------------|-----------|----------|
| `high_failure_rate` | Agent failure rate > 30% | high if > 50%, else medium |
| `retry_spike` | Average retries > 2.0 per agent | medium |
| `high_gate_failure_rate` | Gate failure rate > 20% | high if > 40%, else medium |
| `budget_overrun` | Token usage > 50% above tier midpoint | medium |

#### Anomaly Model

```python
@dataclass
class Anomaly:
    anomaly_type: str       # see table above
    severity: str           # "low", "medium", "high"
    agent_name: str         # which agent, if applicable
    metric: str             # metric that triggered the anomaly
    current_value: float    # actual value observed
    threshold: float        # threshold that was exceeded
    sample_size: int        # data points that contributed
    evidence: list[str]     # human-readable evidence strings
```

#### Trigger State

The trigger evaluator persists its watermark (the task count at the time
of the last analysis) to:
```
.claude/team-context/improvement-trigger-state.json
```

---

### 3.6 Experiment Manager

**Module:** `agent_baton/core/improve/experiments.py`
**Class:** `ExperimentManager`
**Model:** `agent_baton/models/improvement.py` -- `Experiment`

The experiment manager tracks the impact of applied recommendations by
creating experiments that compare post-change performance against a
baseline.

#### Experiment Constraints

- Maximum 2 active experiments per agent.
- Minimum 5 samples before evaluation.
- Default maximum duration: 14 days.

#### Experiment Model

```python
@dataclass
class Experiment:
    experiment_id: str
    recommendation_id: str
    hypothesis: str          # what we expect to happen
    metric: str              # "first_pass_rate", "gate_pass_rate", etc.
    baseline_value: float    # pre-change metric value
    target_value: float      # expected improvement (baseline * 1.05)
    agent_name: str
    started_at: str
    min_samples: int = 5
    max_duration_days: int = 14
    status: str = "running"  # running -> concluded | rolled_back
    samples: list[float]     # observed metric values
    result: str = ""         # "improved", "degraded", "inconclusive"
```

#### Evaluation Thresholds

| Condition | Result |
|-----------|--------|
| Average sample > baseline + 5% | `"improved"` |
| Average sample < baseline - 5% | `"degraded"` |
| Otherwise | `"inconclusive"` |

Experiments that degrade are automatically rolled back by the improvement
loop without human approval.

#### Storage

Experiments are stored as individual JSON files at:
```
.claude/team-context/improvements/experiments/<experiment_id>.json
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `create_experiment(recommendation, metric, ...)` | Create a new experiment |
| `record_sample(experiment_id, value)` | Add a new observation |
| `evaluate(experiment_id)` | Evaluate against baseline |
| `conclude(experiment_id, result)` | Manually conclude |
| `mark_rolled_back(experiment_id)` | Mark as rolled back |
| `active()` | List all running experiments |
| `active_for_agent(agent_name)` | Running experiments for a specific agent |

---

### 3.7 Proposal Manager

**Module:** `agent_baton/core/improve/proposals.py`
**Class:** `ProposalManager`

The proposal manager persists the lifecycle of improvement recommendations
as an append-only JSONL log.

#### Status Transitions

```
proposed --> applied --> rolled_back
                    |
                    +--> (stable, no further transition)

proposed --> rejected
```

#### Storage

Recommendations are stored at:
```
.claude/team-context/improvements/recommendations.jsonl
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `record(rec)` | Append a recommendation to the log |
| `record_many(recs)` | Append multiple recommendations |
| `update_status(rec_id, new_status)` | Transition a recommendation's status |
| `load_all()` | Load all recommendations from disk |
| `get(rec_id)` | Get a specific recommendation |
| `get_by_status(status)` | Filter by status |
| `get_applied()` | Get all applied recommendations |
| `get_proposed()` | Get all proposed (pending) recommendations |

---

### 3.8 Rollback Manager

**Module:** `agent_baton/core/improve/rollback.py`
**Class:** `RollbackManager`

The rollback manager restores agents to their pre-experiment state and
implements a circuit breaker to prevent cascading auto-apply failures.

#### Rollback Behavior

- For `agent_prompt` changes: restores the latest backup via
  `AgentVersionControl`.
- For other categories (`budget_tier`, `routing`, `sequencing`): logs the
  `rollback_spec` for the caller to act on.
- Rollback is **always automatic** on experiment degradation -- no human
  approval needed.

#### Circuit Breaker

If **3 or more rollbacks** occur within a **7-day window**, the circuit
breaker trips and all auto-apply is paused. This prevents the system
from repeatedly applying and rolling back changes when there are
systemic issues.

#### Storage

Rollback audit entries are stored at:
```
.claude/team-context/improvements/rollbacks.jsonl
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `rollback(recommendation, reason)` | Execute a rollback and log it |
| `circuit_breaker_tripped()` | Check if 3+ rollbacks in 7 days |
| `recent_rollbacks(days)` | Return rollbacks from the last N days |
| `load_all()` | Load all rollback entries |

---

### 3.9 Improve CLI Commands

| Command | Description |
|---------|-------------|
| `baton scores` | Show all agent performance scorecards |
| `baton scores --agent NAME` | Show scorecard for a specific agent |
| `baton scores --trends` | Show performance trends for all agents |
| `baton scores --write` | Write scorecard report to disk |
| `baton learn run-cycle` | Instantiate the learning-cycle plan template (proposes prompt evolutions and pattern updates as part of the cycle) |
| `baton learn run-cycle --run` | Execute the learning cycle immediately after instantiating the plan |
| `baton learn analyze` | Run analysis: compute confidence, mark auto-apply candidates |
| `baton learn apply --all-safe` | Apply all proposed fixes that meet auto-apply thresholds |
| `baton patterns` | Show all learned patterns |
| `baton patterns --refresh` | Re-analyse usage log and update patterns |
| `baton patterns --task-type TYPE` | Show patterns for a specific task type |
| `baton patterns --recommendations` | Show sequencing recommendations |
| `baton patterns --min-confidence N` | Filter by minimum confidence |
| `baton budget` | Show saved budget recommendations |
| `baton budget --recommend` | Analyse usage log and display recommendations |
| `baton budget --save` | Save recommendations to disk |
| `baton budget --auto-apply` | Show only auto-applicable downgrades |
| `baton changelog` | Show agent changelog entries |
| `baton changelog --agent NAME` | Show history for a specific agent |
| `baton changelog --backups [NAME]` | List backup files |
| `baton learn improve` | Show latest improvement report (formerly `baton improve`) |
| `baton learn improve --run` | Run a full improvement cycle |
| `baton learn improve --force` | Force-run cycle (skip trigger check) |
| `baton learn improve --experiments` | Show active experiments |
| `baton learn improve --history` | Show all improvement reports |
| `baton anomalies` | Detect and display system anomalies |
| `baton anomalies --watch` | Show trigger readiness and anomaly status |

---

## 4. The Learning Loop (End-to-End)

The complete learning loop flows through all three subsystems:

```
EXECUTION                 OBSERVE              LEARN                 IMPROVE
---------                 -------              -----                 -------
Orchestrator              TraceRecorder        PatternLearner        PerformanceScorer
dispatches agents ------> records events       groups by task type   scores each agent
                          |                    |                     |
                          v                    v                     v
                          UsageLogger          computes confidence   PromptEvolutionEngine
                          logs tokens,         and success rate      proposes changes
                          retries, gates       |                     |
                          |                    v                     v
                          v                    BudgetTuner           AgentVersionControl
                          Retrospective        recommends tier       backs up + tracks
                          Engine writes        adjustments           changes
                          what worked,         |                     |
                          what didn't,         v                     v
                          knowledge gaps       Recommender           ImprovementLoop
                          |                    unifies all           classifies:
                          v                    recommendations       auto-apply or escalate
                          ContextProfiler      |                     |
                          measures read/       |                     v
                          write efficiency     |                     ExperimentManager
                          |                    |                     tracks impact
                          v                    |                     |
                          AgentTelemetry       |                     v
                          logs tool calls      +-------------------> RollbackManager
                                                                     auto-rollback if
                                                                     degraded; circuit
                                                                     breaker if 3+ in 7d
```

### Step-by-Step Flow

1. **Execution:** The orchestrator dispatches agents to complete a task.
   Each dispatch, gate check, and completion is recorded.

2. **Trace:** `TraceRecorder` captures a timestamped event DAG for the
   entire task lifecycle.

3. **Usage:** `UsageLogger` appends a `TaskUsageRecord` with per-agent
   token counts, retry counts, gate results, and the final outcome.

4. **Retrospective:** `RetrospectiveEngine` generates a structured
   retrospective with what worked, what didn't, knowledge gaps, roster
   recommendations, and sequencing notes.

5. **Context Profile:** `ContextProfiler` analyses the trace to compute
   per-agent context efficiency (read/write ratio, redundancy).

6. **Telemetry:** `AgentTelemetry` captures fine-grained tool-call events.

7. **Pattern Learning:** `PatternLearner` reads the usage log, discovers
   recurring success patterns, and persists them. The planner uses these
   patterns when creating future plans.

8. **Budget Tuning:** `BudgetTuner` analyses token usage distributions
   and recommends tier adjustments.

9. **Scoring:** `PerformanceScorer` computes per-agent scorecards
   combining quantitative metrics with qualitative retrospective signals.

10. **Evolution:** `PromptEvolutionEngine` identifies underperforming
    agents and proposes specific prompt improvements.

11. **Recommendation:** `Recommender` unifies all recommendations,
    deduplicates, and ranks by confidence and risk.

12. **Improvement Loop:** `ImprovementLoop.run_cycle()` classifies each
    recommendation as auto-apply or escalate, creates experiments for
    auto-applied changes, and evaluates running experiments.

13. **Rollback:** If an experiment shows degradation (>5% loss from
    baseline), `RollbackManager` automatically restores the previous
    state. If 3+ rollbacks occur in 7 days, the circuit breaker trips
    and all auto-apply is paused.

14. **Better Next Execution:** The planner queries learned patterns,
    budget recommendations, knowledge gaps, and retrospective feedback
    when creating the next plan, completing the loop.

---

## 5. Data Models Summary

### Observe Models

| Model | Module | Purpose |
|-------|--------|---------|
| `TraceEvent` | `models/trace.py` | Single timestamped execution event |
| `TaskTrace` | `models/trace.py` | Complete trace for an orchestrated task |
| `AgentUsageRecord` | `models/usage.py` | Single agent's usage within a task |
| `TaskUsageRecord` | `models/usage.py` | Full task usage record |
| `TelemetryEvent` | `core/observe/telemetry.py` | Single agent tool-call event |
| `AgentContextProfile` | `models/context_profile.py` | Per-agent context efficiency |
| `TaskContextProfile` | `models/context_profile.py` | Task-level context efficiency |
| `Retrospective` | `models/retrospective.py` | Structured post-task analysis |
| `AgentOutcome` | `models/retrospective.py` | Per-agent result in a retrospective |
| `KnowledgeGap` | `models/retrospective.py` | Knowledge gap (legacy schema) |
| `RosterRecommendation` | `models/retrospective.py` | Agent roster change suggestion |
| `SequencingNote` | `models/retrospective.py` | Phase-level sequencing observation |
| `RetrospectiveFeedback` | `models/feedback.py` | Aggregated actionable feedback for the planner |

### Learn Models

| Model | Module | Purpose |
|-------|--------|---------|
| `LearnedPattern` | `models/pattern.py` | Recurring orchestration pattern |
| `BudgetRecommendation` | `models/budget.py` | Budget tier adjustment recommendation |

### Improve Models

| Model | Module | Purpose |
|-------|--------|---------|
| `AgentScorecard` | `core/improve/scoring.py` | Per-agent performance scorecard |
| `EvolutionProposal` | `core/improve/evolution.py` | Proposed prompt change |
| `ChangelogEntry` | `core/improve/vcs.py` | Agent change audit record |
| `Recommendation` | `models/improvement.py` | Unified improvement recommendation |
| `Experiment` | `models/improvement.py` | Tracks impact of an applied recommendation |
| `Anomaly` | `models/improvement.py` | Detected system anomaly |
| `TriggerConfig` | `models/improvement.py` | When to run improvement analysis |
| `ImprovementReport` | `models/improvement.py` | Summary of an improvement cycle |
| `ImprovementConfig` | `models/improvement.py` | Top-level improvement loop config |
| `RollbackEntry` | `core/improve/rollback.py` | Rollback audit record |

---

## 6. Configuration

### TriggerConfig (when to analyze)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_tasks_before_analysis` | 10 | Minimum total tasks before first analysis |
| `analysis_interval_tasks` | 5 | New tasks needed to trigger re-analysis |
| `agent_failure_threshold` | 0.3 | Failure rate above which an anomaly is flagged |
| `gate_failure_threshold` | 0.2 | Gate failure rate above which an anomaly is flagged |
| `budget_deviation_threshold` | 0.5 | Token deviation above which a budget overrun is flagged |
| `confidence_threshold` | 0.7 | Minimum confidence for recommendations |

### ImprovementConfig (how to apply)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `auto_apply_threshold` | 0.8 | Minimum confidence for auto-application |
| `paused` | `False` | Manual pause switch for auto-apply |

### PatternLearner Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_sample_size` | 5 | Minimum records per group to generate a pattern |
| `min_confidence` | 0.7 | Minimum confidence to include a pattern |
| Confidence calibration | 15 | Sample size divisor in confidence formula |

### BudgetTuner Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| Minimum sample size | 3 | Records per group before generating recommendations |
| Upgrade threshold | 80% | Median > 80% of tier ceiling triggers upgrade |
| Auto-apply confidence | 0.8 | Minimum confidence for auto-apply (downgrades only) |

### ExperimentManager Constraints

| Parameter | Default | Description |
|-----------|---------|-------------|
| Max active per agent | 2 | Maximum concurrent experiments per agent |
| Min samples | 5 | Minimum observations before evaluation |
| Max duration | 14 days | Maximum experiment runtime |
| Improvement threshold | +5% | Gain needed to declare "improved" |
| Degradation threshold | -5% | Loss needed to declare "degraded" |

### Circuit Breaker (RollbackManager)

| Parameter | Default | Description |
|-----------|---------|-------------|
| Rollback count | 3 | Rollbacks within the window that trip the breaker |
| Window | 7 days | Time window for counting rollbacks |

### Data Archiver Retention

| Parameter | Default | Description |
|-----------|---------|-------------|
| Retention period | 90 days | Files older than this are eligible for cleanup |
| JSONL rotation | 10,000 lines | Maximum lines kept in rotated JSONL files |

---

## 7. File Layout Summary

```
.claude/team-context/
  traces/                           # TraceRecorder output
    <task_id>.json
  usage-log.jsonl                   # UsageLogger output
  usage-dashboard.md                # DashboardGenerator output
  telemetry.jsonl                   # AgentTelemetry output
  retrospectives/                   # RetrospectiveEngine output
    <task_id>.md                    #   human-readable
    <task_id>.json                  #   machine-readable sidecar
  context-profiles/                 # ContextProfiler output
    <task_id>.json
  learned-patterns.json             # PatternLearner output
  budget-recommendations.json       # BudgetTuner output
  agent-scorecards.md               # PerformanceScorer output
  evolution-report.md               # PromptEvolutionEngine output
  evolution-proposals/              # PromptEvolutionEngine proposals
    <agent_name>.md
  improvement-trigger-state.json    # TriggerEvaluator watermark
  improvements/                     # ImprovementLoop output
    recommendations.jsonl           #   ProposalManager log
    rollbacks.jsonl                 #   RollbackManager log
    reports/                        #   ImprovementLoop reports
      <report_id>.json
    experiments/                    #   ExperimentManager data
      <experiment_id>.json

agents/
  .backups/                         # AgentVersionControl backups
    <agent>.<timestamp>.md
  changelog.md                      # AgentVersionControl changelog
```

---

## 8. Practical Examples

### View the current state of your system

```bash
# How are my agents performing?
baton scores

# Any anomalies?
baton anomalies

# What happened in the last execution?
baton trace --last

# How efficient was the context usage?
baton context-profile --generate my-task-id
baton context-profile my-task-id
```

### Run the learning pipeline

```bash
# Discover patterns from execution history
baton patterns --refresh

# Check if budget tiers need adjustment
baton budget --recommend

# See what the system would auto-apply vs. escalate
baton improve --force
```

### Investigate a specific agent

```bash
# Full scorecard
baton scores --agent architect

# Usage stats
baton usage --agent architect

# Performance trend
baton scores --trends

# Context efficiency
baton context-profile --agent architect

# Any evolution proposals? (folded into the learning loop)
baton learn run-cycle

# Changelog history
baton changelog --agent architect
```

### Review and manage experiments

Experiment tracking is folded into `baton learn`:

```bash
# What experiments are running?
baton learn improve --experiments

# Run the analysis loop (auto-applies safe fixes, escalates the rest)
baton learn analyze
baton learn apply --all-safe

# Drive a full improvement cycle (auto-rollback runs if a change degrades)
baton learn improve --run

# Reopen an issue / rollback its applied override
baton learn reset --issue ISSUE_ID
```

### Clean up old data

```bash
# Preview what would be removed without deleting anything
baton cleanup --retention-days 60 --dry-run

# Actually clean up files older than 60 days
baton cleanup --retention-days 60
```

### Read retrospective insights

```bash
# List recent retrospectives
baton retro

# Search for patterns in failures
baton retro --search "knowledge gap"

# See what roster changes are recommended
baton retro --recommendations
```
