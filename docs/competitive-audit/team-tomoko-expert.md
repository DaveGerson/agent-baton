# Team Audit: Tomoko (Workflow Designer) + Agent-Baton Expert

Structured dialogue audit against the `feat/actiontype-interact` branch.
Each item follows the pattern: Tomoko asks, Expert investigates with
file:line evidence, Tomoko probes depth, Expert discovers second-pass
findings, joint verdict.

---

## Item 1: Scoring Formula Transparency

**Tomoko asks:** "Walk me through exactly how an agent score is computed.
I want to see every weight, threshold, and magic number. If I wanted to
reproduce a scorecard from raw data, could I?"

**Expert investigates:**

The scoring algorithm lives in `agent_baton/core/improve/scoring.py`.
It is completely deterministic with zero hidden weights.

Step-by-step for `score_agent()` (line 209):

1. **times_used**: Read from `UsageLogger.agent_stats()` -- count of
   task participations.

2. **first_pass_rate** (line 249): For every `TaskUsageRecord`, for every
   `AgentUsageRecord` matching the agent name, count those with
   `retries == 0`. Divide by `times_used`.

3. **retry_rate** (line 339): `stats["avg_retries"]` from
   `UsageLogger.agent_stats()` -- straight arithmetic mean of retry
   counts across all participations.

4. **gate_pass_rate** (line 340): `stats["gate_pass_rate"]` -- fraction
   of gate results with value `"PASS"` across all the agent's
   participations.

5. **Qualitative signals** (lines 259-315): Scans retrospective Markdown
   for section headers `## What Worked`, `## What Didn't`, `## Knowledge
   Gaps`. For each line in the appropriate section that contains the
   agent name (case-sensitive substring match), increments
   `positive_mentions`, `negative_mentions`, or `knowledge_gaps_cited`.

6. **health** (lines 100-115): A categorical rating with explicit thresholds:
   - `"strong"`: `first_pass_rate >= 0.8` AND `negative_mentions == 0`
   - `"adequate"`: `first_pass_rate >= 0.5`
   - `"needs-improvement"`: `first_pass_rate < 0.5`
   - `"unused"`: `times_used == 0`

7. **Bead quality** (lines 320-333): When a `bead_store` is provided,
   queries beads by `agent_name`, computes mean `quality_score` excluding
   zeros, stores as `avg_bead_quality`.

8. **Trend detection** (lines 400-458): `detect_trends()` collects a
   binary success vector (1.0 = zero retries, 0.0 = had retries) for
   each participation, takes the last `window` values (default 10),
   and runs ordinary least squares linear regression. Thresholds:
   - slope > 0.02: `"improving"`
   - slope < -0.02: `"degrading"`
   - otherwise: `"stable"`
   - Minimum 3 data points; below that, always `"stable"`.

**Tomoko probes:** "So there's no composite score? No weighted average
of these metrics into a single number? And the qualitative signals --
a substring match on the agent name is fragile. What if 'backend-engineer'
matches inside 'backend-engineer--python'?"

**Expert second pass:**

Correct -- there is no composite numeric score. The output is a
dataclass (`AgentScorecard`) with individual fields. The `health` property
is the only aggregation, and it is a simple threshold cascade on
`first_pass_rate` plus a binary check on `negative_mentions`.

On the substring match concern: line 284 does `if agent_name in line`,
so searching for `"backend-engineer"` WILL match a line mentioning
`"backend-engineer--python"`. This is a genuine precision issue --
the base name agent would get credit/blame for the flavored agent's
mentions. However, since scoring always passes the exact agent name
from usage records (which includes the flavor suffix), this only becomes
an issue if retrospective text uses base names while usage records
use flavored names.

No confidence intervals. No error bars. The trend regression is OLS
without significance testing -- a 3-sample "trend" is not statistically
meaningful. The 0.02 slope threshold is a hardcoded magic number
(line 454-456) with no documented calibration basis.

**Joint verdict: WORKS**

The formula is fully transparent -- every number can be traced to source
data. But there are quality gaps: no composite score, crude substring
matching for qualitative signals, and no statistical rigor in trend
detection. A data scientist like Tomoko would want confidence intervals
and proper significance testing.

**Delta from solo audit:** Solo audit described scoring at a summary
level ("computes per-agent scorecards from usage logs"). This dialogue
exposed three NEW findings: (1) the substring match precision issue
for flavored agents, (2) the absence of confidence intervals on trends,
(3) the undocumented 0.02 slope threshold is a magic number.

---

## Item 2: Pattern Learner Accuracy

**Tomoko asks:** "The pattern learner claims to surface recurring
patterns. Walk me through the statistical basis. What's the confidence
formula? Is it prone to false positives with small samples?"

**Expert investigates:**

`agent_baton/core/learn/pattern_learner.py`, class `PatternLearner`.

Algorithm (method `analyze`, line 104):

1. Read all `TaskUsageRecord` from `usage-log.jsonl`.
2. Group by `sequencing_mode` (a string proxy for task type).
3. For each group with `len(group) >= min_sample_size` (default 5):
   - `success_rate = count(outcome == "SHIP") / total`
   - `confidence = min(1.0, (sample_size / 15) * success_rate)`
4. Filter: `confidence >= min_confidence` (default 0.7).
5. For qualifying groups, find the most common agent combination
   (`Counter` on sorted agent name tuples), compute avg token cost
   from successful tasks, avg retries, avg gate pass rate.

The confidence formula (line 150):
```
confidence = min(1.0, (len(group) / 15) * success_rate)
```

The divisor `15` is a class constant (`_CONFIDENCE_CALIBRATION`, line 93)
documented as a "calibration constant."

**Tomoko probes:** "This confidence formula is concerning. With 5
samples and 100% success rate, confidence = 5/15 = 0.33. That won't
pass the 0.7 threshold. So what's the minimum sample to get through?
And is this confidence in any statistical sense, or just a ramp?"

**Expert second pass:**

Solving for the minimum: `confidence >= 0.7` requires
`(n / 15) * success_rate >= 0.7`. At 100% success rate, `n >= 10.5`,
so minimum 11 samples at perfect success. At 80% success:
`n / 15 * 0.8 >= 0.7` => `n >= 13.125` => 14 samples.

This is NOT statistical confidence (no hypothesis test, no p-value,
no bootstrap). It's a heuristic ramp that combines sample size and
success rate into a single number. The docstring (line 86-93) is
honest about this: "This ramps confidence linearly from 0 at 0 samples
to a maximum of 1.0 at 15+ samples (assuming perfect success rate)."

False positive risk: With 11 samples and 100% success, confidence hits
0.73. But 11 is a small sample -- there could easily be a 20% failure
rate that hasn't manifested yet. There's no variance estimation, no
binomial confidence interval. The pattern learner has no concept of
prediction uncertainty.

The team pattern variant (`analyze_team_patterns`, line 437) uses the
same formula but with lower defaults: `min_sample_size=3`,
`min_confidence=0.5`. At 3 samples and 100% success: confidence =
3/15 = 0.2 -- still below 0.5 threshold. Minimum is 8 samples at
perfect success for teams.

**Joint verdict: PARTIAL**

The pattern learner works mechanically and its formula is transparent.
But the "confidence" metric is a misnomer -- it's a heuristic ramp,
not statistical confidence. With the default thresholds (min 5 samples,
min 0.7 confidence), in practice you need 11+ samples at perfect success
to surface a pattern. This is conservative enough to avoid the worst
false positives, but the system lacks true statistical rigor: no variance
estimation, no prediction intervals, no false discovery rate control.

**Delta from solo audit:** Solo audit said "Surfaces patterns meeting
minimum sample size (5+) and confidence threshold (0.7)." This dialogue
reveals that the 5-sample minimum is misleading -- the confidence formula
means you actually need 11+ samples at perfect success to qualify. Also
newly exposed: the confidence is a heuristic ramp, not a statistical
measure, and the team pattern variant uses weaker thresholds.

---

## Item 3: Experiment Rigor

**Tomoko asks:** "I want to A/B test a prompt change. Walk me through
the full experiment lifecycle: create, traffic split, measure, conclude.
What statistical tests are applied? Minimum sample size?"

**Expert investigates:**

`agent_baton/core/improve/experiments.py`, class `ExperimentManager`.

Lifecycle:

1. **Create** (`create_experiment`, line 64): Takes a `Recommendation`,
   a `metric` name (e.g. `"first_pass_rate"`), a `baseline_value`, and
   a `target_value`. Returns an `Experiment` object with status `"running"`.
   Safety: max 2 active experiments per agent (line 96-98, constant
   `_MAX_ACTIVE_PER_AGENT = 2`).

2. **Record samples** (`record_sample`, line 120): Appends a float value
   to `experiment.samples` list. Called after each agent dispatch where
   the experiment's target metric is observed.

3. **Evaluate** (`evaluate`, line 147): Requires `_MIN_SAMPLES = 5`
   (line 39). Computes `avg_sample = sum(samples) / len(samples)`, then:
   - `change_pct = (avg_sample - baseline) / |baseline|`
   - `"improved"`: change_pct > +5% (`_IMPROVEMENT_THRESHOLD = 0.05`)
   - `"degraded"`: change_pct < -5% (`_DEGRADATION_THRESHOLD = -0.05`)
   - `"inconclusive"`: within +/-5% band
   - Special case: baseline = 0 uses absolute thresholds.

4. **Conclude/rollback**: `conclude()` manually sets result,
   `mark_rolled_back()` flags the experiment. Degraded experiments
   trigger auto-rollback in `ImprovementLoop._evaluate_running_experiments`
   (loop.py:315-333).

**Tomoko probes:** "Wait -- there's NO traffic split? No A/B split?
It just measures before vs. after? And the statistical test is... a
comparison of means with a fixed 5% threshold? No t-test? No power
analysis? What about variance?"

**Expert second pass:**

Correct. This is NOT an A/B test. It is a simple before/after comparison:

- **No traffic split**: There is no mechanism to route 50% of dispatches
  to variant A and 50% to variant B. All dispatches go to the modified
  agent. The "experiment" is purely sequential: measure baseline, apply
  change, measure samples, compare.

- **No statistical test**: The evaluation (line 175-197) is a simple
  comparison of the mean against a fixed +/-5% threshold. No t-test,
  no Mann-Whitney, no Bayesian inference. No variance estimation. No
  power analysis. No correction for multiple comparisons.

- **Sample independence not guaranteed**: Samples are recorded sequentially.
  There's no check for autocorrelation, no randomization, no blocking.

- **5 samples minimum**: This is dramatically underpowered for detecting
  a 5% effect. A proper power analysis for a two-sided t-test at
  alpha=0.05, beta=0.80, detecting a 5% change would require 50-100+
  samples depending on variance.

The `ImprovementLoop` does have a circuit breaker (3+ rollbacks in 7 days
pauses all auto-apply -- `rollback.py:29-30`), which provides a
meta-safety net. But this catches systemic problems, not statistical noise.

**Joint verdict: PARTIAL**

The experiment infrastructure exists and functions mechanically: create,
record, evaluate, rollback. The safety guardrails are solid (max 2
experiments, auto-rollback, circuit breaker). But the experimental
methodology is scientifically weak: no A/B split, no statistical testing,
underpowered sample size, no variance estimation. Calling this "A/B
testing" would be misleading. It is before/after monitoring with fixed
thresholds.

**Delta from solo audit:** Solo audit said "Requires 5+ samples before
evaluation. Improvement/degradation thresholds at +/- 5%." This dialogue
exposes critical NEW findings: (1) there is NO traffic split -- this is
NOT A/B testing, it is before/after, (2) there are NO statistical tests
at all, (3) 5 samples is dramatically underpowered, (4) no variance
estimation or power analysis.

---

## Item 4: Evolve Proposal Quality

**Tomoko asks:** "Show me an actual evolution proposal. Is it specific
enough to be actionable, or generic advice like 'add more context'?"

**Expert investigates:**

`agent_baton/core/improve/evolution.py`, class `PromptEvolutionEngine`.

The `analyze()` method (line 139) runs a cascade of signal checks on
each agent's scorecard and maps each to specific suggestions. Here is
the complete cascade:

| Signal | Threshold | Issue text | Suggestion text |
|--------|-----------|-----------|-----------------|
| first_pass_rate < 0.5 | needs-improvement | "Low first-pass rate (X%) -- agent frequently needs retries" | "Add more specific instructions for common failure modes" |
| first_pass_rate < 0.5 | needs-improvement | (same) | "Include negative examples (what NOT to do)" |
| first_pass_rate 0.5-0.8 | adequate | "Moderate first-pass rate (X%)" | "Review retry patterns in retrospectives for recurring issues" |
| retry_rate > 1.0 | -- | "High retry rate (X)" | "Tighten acceptance criteria in the agent's output format section" |
| gate_pass_rate < 0.7 | -- | "Low gate pass rate (X%)" | "Add quality checklist to the agent's prompt" |
| negative_mentions > 0 | -- | "N negative mention(s)" | "Read retrospective 'What Didn't Work' entries for this agent and address specific failures" |
| knowledge_gaps_cited > 0 | -- | "N knowledge gap(s) cited" | "Create or update knowledge pack to fill cited gaps" |
| knowledge_gaps_cited > 0 | (same) | (same) | "Add 'Before Starting' section pointing to relevant knowledge packs" |

**Tomoko probes:** "These suggestions are all at the meta level. 'Add
more specific instructions for common failure modes' -- which failure
modes? 'Review retry patterns' -- which patterns? Can it actually tell
me WHAT the agent got wrong, or just that it got wrong?"

**Expert second pass:**

The proposals contain the scorecard (with exact numbers) and the issue
descriptions (with metric values). But the suggestions are static
strings -- they do NOT read the actual retrospective content, do NOT
identify specific failure modes, and do NOT extract concrete retry
patterns.

For example, a proposal for `backend-engineer--python` would say:
```
## Issues Identified
- Low first-pass rate (40%) -- agent frequently needs retries
- 3 negative mention(s) in retrospectives

## Suggested Changes
1. Add more specific instructions for common failure modes
2. Include negative examples (what NOT to do)
3. Read retrospective 'What Didn't Work' entries and address failures
```

It tells you "there's a problem" and "go read your retrospectives."
It does NOT tell you "the agent failed on Django model migrations 3
times because it forgot to generate the migration file."

The proposals are classified as `risk="high"` and `auto_applicable=False`
in the recommendation pipeline (recommender.py:191-192), so they NEVER
auto-apply. This is a deliberate safety choice, but it also means the
system relies on a human to translate generic suggestions into specific
prompt edits.

**Joint verdict: PARTIAL**

Proposals correctly identify underperformers with exact metrics and
route the operator to the right data sources. But the suggestions
themselves are templates, not data-driven insights. The system can
tell you "agent X is struggling" but cannot tell you "agent X fails
specifically at Y because of Z." A workflow designer would need to
manually read retrospectives and correlate failure patterns -- the
system does not do this analysis for you.

**Delta from solo audit:** Solo audit said proposals are "specific enough
to be actionable" based on "a cascade of quantitative and qualitative
signals." This dialogue reveals the suggestions are STATIC TEMPLATES
keyed to metric thresholds, not data-driven recommendations. The
cascade is correctly identified, but the output is generic. The solo
audit overstated actionability.

---

## Item 5: Knowledge Gap to Resolution Pipeline

**Tomoko asks:** "A knowledge gap is detected mid-execution. Walk me
through the FULL pipeline: detection, interview, resolution, and
verification that it actually helped. Is this loop actually closed?"

**Expert investigates:**

The pipeline spans four modules:

**1. Detection** (`core/engine/knowledge_gap.py`):
- Agent outputs `KNOWLEDGE_GAP: <description>` + optional `CONFIDENCE:` / `TYPE:` lines.
- `parse_knowledge_gap()` (line 48) extracts a `KnowledgeGapSignal`.
- Called from `executor.py:_handle_knowledge_gap()` during `record_step_result()`.

**2. Escalation** (`knowledge_gap.py:determine_escalation()`, line 121):
- Applies an escalation matrix:
  - Factual + match found -> `auto-resolve` (re-dispatch with knowledge attached)
  - Factual + bead match (F8) -> `auto-resolve` (bead keyword overlap >= 2 words)
  - Factual + no match + LOW risk + low intervention -> `best-effort`
  - Factual + no match + elevated risk/intervention -> `queue-for-gate`
  - Contextual -> always `queue-for-gate`

**3. Resolution paths** (in executor.py lines 3595-3773):
- **auto-resolve**: `KnowledgeResolver` resolves via 4-layer pipeline
  (explicit, agent-declared, tag-matched, relevance fallback). Creates
  a `ResolvedDecision`, amends the plan to re-dispatch the step with
  the resolved knowledge injected. Records a `PlanAmendment` for audit.
- **best-effort**: Logs the gap, continues execution.
- **queue-for-gate**: Appends to `state.pending_gaps`, surfaces at
  next human review gate. Human answers via `baton execute decide`.

**4. Learning capture** (`core/learn/engine.py:detect()`, line 71):
- Called at execution completion.
- Scans `state.pending_gaps` and records `LearningIssue` records
  (type `"knowledge_gap"`) in the ledger.
- At threshold (3 occurrences), auto-applies: creates a knowledge
  pack stub via `resolvers.resolve_knowledge_gap()` (resolvers.py:97).

**5. Retrospective capture** (executor.py lines 2532-2551):
- Unresolved gaps -> `KnowledgeGapRecord` with `resolution="unresolved"`.
- Human-answered gaps -> `KnowledgeGapRecord` with `resolution="human-answered"`.
- Both written to retrospective and synced to central.db.

**Tomoko probes:** "OK but is the loop actually CLOSED? After you create
a knowledge pack stub, does the system verify it helped on the next
execution? Does the gap recurrence rate actually drop?"

**Expert second pass:**

The loop is NOT fully closed. Here is the verification gap:

- The knowledge pack stub created by `resolve_knowledge_gap()` is an
  EMPTY TEMPLATE (resolvers.py:130-141). It contains placeholders like
  `"> Fill in relevant context here."` A human must fill it in.

- There is no automated verification that the filled-in pack actually
  reduces gap recurrence. The learning system tracks gap occurrence
  counts in the ledger (via `record_issue` deduplication on
  `(issue_type, target)`), and if the same gap recurs, the count
  increments. But there is no automated comparison of gap rates before
  vs. after the pack was created.

- The `PatternLearner.knowledge_gaps_for()` (pattern_learner.py:306)
  reads retrospective sidecars to surface per-agent gaps for the
  planner, enabling knowledge attachment on future dispatches. This is
  the closest thing to closure: if a gap was recorded, the planner
  will try harder to attach relevant packs next time.

- But the scorer does track `knowledge_gaps_cited` (scoring.py:83,95),
  which feeds into `PromptEvolutionEngine`. An agent with persistent
  gaps gets flagged for evolution. This is indirect closure.

So the pipeline is: detect -> escalate -> resolve/queue -> learn ->
stub -> (human fills) -> planner attaches on future runs -> scorer
tracks if gaps persist. The last step ("did it actually help?") requires
manual inspection of gap recurrence rates via `baton cquery` or
`baton query --sql`.

**Joint verdict: PARTIAL**

Detection through resolution is well-engineered with a proper
escalation matrix, auto-resolve from knowledge packs and beads,
and human escalation for contextual gaps. The learning capture is
solid. But the loop has a verification gap: the system creates a
knowledge pack stub that requires human completion, and there is no
automated measurement of whether gap recurrence decreased after the
pack was populated. The loop is open at the "did this actually help?"
step.

**Delta from solo audit:** Solo audit did not evaluate the knowledge
gap pipeline at all (it was not in Tomoko's original 17 items). This
is entirely NEW coverage from the dialogue format. Key findings: the
auto-resolve path through beads (F8), the empty stub problem in the
learning resolver, and the missing verification step.

---

## Item 6: Custom Agent Iteration Cycle

**Tomoko asks:** "I create a new agent via talent-builder, deploy it,
measure performance, and want to iterate. What's the actual feedback
loop? How quickly can I measure whether the new agent is better?"

**Expert investigates:**

The iteration cycle spans several modules:

**1. Creation** via `agents/talent-builder.md`: Builds the agent
definition (.md file), knowledge packs, and skills. The agent is
immediately available after creation (no deployment step -- it's a
file in `agents/`).

**2. Performance appears in scores** after the first execution that
uses the agent. `PerformanceScorer.score_agent()` (scoring.py:209)
reads `UsageLogger.agent_stats()` which scans the JSONL usage log.
The log is written at execution completion by
`ExecutionEngine.complete()`.

**3. Version control** via `AgentVersionControl` (vcs.py): Before
modifying the agent, `track_modification()` (line 279) creates a
timestamped backup and a changelog entry. The backup directory is
`agents/.backups/`.

**4. Experiment tracking**: After modifying the prompt, the
`ImprovementLoop` can create an experiment to track the impact
(loop.py:300-313). The experiment needs 5 post-change observations
before evaluation.

**5. Trend detection**: After enough data (minimum 3 data points),
`detect_trends()` (scoring.py:400) reports improving/degrading/stable.

**Tomoko probes:** "So the minimum cycle time to measure a change is
5 executions that use my agent? And there's no way to run the experiment
faster -- like replaying historical tasks against the new prompt?"

**Expert second pass:**

Correct. The minimum measurement cycle requires 5 real executions where
the agent is dispatched. There is no replay mechanism -- no way to
re-run historical tasks against a modified agent definition. The system
is purely forward-looking.

The actual cycle time depends on how frequently the agent is dispatched:
- If the agent is dispatched once per execution: 5 executions minimum.
- If the agent handles multiple steps per execution: could be fewer
  executions but the experiment still needs 5 sample recordings.

There's also no A/B comparison (as covered in Item 3). Tomoko cannot
run old-version and new-version in parallel. She must commit to the
change, observe, and rollback if it degrades.

The VCS backup system (vcs.py) is the safety net: `restore_backup()`
(line 260) reverts to the pre-change version, and it takes a safety
backup before restoring so even rollbacks are reversible.

For the reporting cycle: `baton scores --agent my-agent` gives
immediate results from available data. `baton scores --trends` adds
trajectory. `baton experiment show <id>` shows experiment progress.

**Joint verdict: PARTIAL**

The components for agent iteration exist: create (talent-builder),
version (VCS with timestamped backups), measure (scorer, experiments),
and rollback (automatic on degradation). But the feedback loop is
slow -- minimum 5 real dispatches with no replay capability. The
absence of A/B testing means Tomoko must sequentially test each
variant. A power user would want replay testing against historical
tasks and parallel variant comparison.

**Delta from solo audit:** Solo audit covered talent-builder creation
and VCS existence. This dialogue exposes NEW findings: (1) minimum 5
real dispatches for measurement, (2) no historical replay capability,
(3) no parallel variant comparison, (4) the VCS safety-backup-before-
restore pattern.

---

## Item 7: Plan Template Expressiveness

**Tomoko asks:** "I want to create a template for 'add API endpoint'
that includes: backend route, tests, OpenAPI update, frontend client
generation. Can the template system express this?"

**Expert investigates:**

The planner lives in `agent_baton/core/engine/planner.py`.

Built-in templates are Python dicts (lines 112-123):
```python
_PHASE_NAMES: dict[str, list[str]] = {
    "new-feature": ["Design", "Implement", "Test", "Review"],
    "bug-fix": ["Investigate", "Fix", "Test"],
    ...
}

_DEFAULT_AGENTS: dict[str, list[str]] = {
    "new-feature": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    ...
}
```

Phase names are strings. Agent assignment is done dynamically by
the planner based on the task type and the phase name. Steps within
a phase are generated programmatically.

There is NO user-facing template save/load mechanism. The `_PHASE_NAMES`
and `_DEFAULT_AGENTS` dicts are compile-time constants. The planner
does read learned patterns from `PatternLearner.get_patterns_for_task()`
(which stores recommended agents per task type), but these are derived
from historical data, not user-defined templates.

**Tomoko probes:** "So I can't define a custom template that says:
Phase 1 = backend-engineer creates the route, Phase 2 = test-engineer
writes endpoint tests, Phase 3 = architect updates OpenAPI spec,
Phase 4 = frontend-engineer regenerates client? The phases and agents
are determined by the planner, not by me?"

**Expert second pass:**

Correct. The planner generates phases and assigns agents algorithmically.
A user cannot define a specific multi-step template and say "use this
whenever the task description matches 'add API endpoint'."

The workarounds available:

1. **Plan amendment**: `baton execute amend` (in executor.py) allows
   adding phases or steps during execution. But this is reactive, not
   template-based.

2. **Learned patterns**: If you consistently run "add API endpoint"
   tasks using a specific agent sequence, the `PatternLearner` will
   eventually surface it as a pattern. But you can't define it upfront.

3. **Task type keywords**: The planner selects task type via the
   `FallbackClassifier` or Haiku classifier. The task type maps to
   `_PHASE_NAMES` and `_DEFAULT_AGENTS`. To get a custom template,
   you would need to add a new task type to these compile-time dicts.

4. **Knowledge packs**: You could attach a knowledge pack that instructs
   the planner about the desired sequence, but the planner doesn't
   read knowledge packs -- agents do. The planner operates
   independently of the knowledge system.

The `LearnedOverrides` file (overrides.py) supports flavor overrides,
gate overrides, and agent drops, but NOT custom phase templates.

**Joint verdict: PARTIAL**

The plan template system cannot express Tomoko's "add API endpoint"
workflow as a user-defined template. Phases and agents are determined
algorithmically by the planner, with no `baton plan save-template`
or `baton plan from-template` mechanism. The closest substitute is
the implicit learning from `PatternLearner`, which requires 11+
successful executions of the same task type before it surfaces as
a reusable pattern. Compile-time constants can be extended but that
requires code changes.

**Delta from solo audit:** Solo audit identified this as PARTIAL with
the same gap. This dialogue adds NEW depth: (1) the specific mechanism
by which patterns become implicit templates (11+ samples), (2) the four
workaround paths (amend, learned patterns, task type keywords, knowledge
packs), (3) confirmation that `LearnedOverrides` explicitly lacks
template support.

---

## Item 8: Cross-Project Pattern Sharing

**Tomoko asks:** "I discover a successful pattern on Project A and want
to share it with Project B. Walk me through the actual mechanism. What
gets shared? What's lost?"

**Expert investigates:**

Cross-project sharing uses the federated sync architecture:

**1. Sync to central** (`core/storage/sync.py`):
- `SyncEngine` pushes `learned_patterns` from per-project `baton.db`
  to `~/.baton/central.db` (line 68: `SyncTableSpec("learned_patterns",
  ["pattern_id"])`).
- Also syncs: `usage_records`, `retrospectives`, `step_results`,
  `knowledge_gaps`, `budget_recommendations`, and 15+ other tables.

**2. Central analytics views** (`core/storage/schema.py`):
- `v_agent_reliability`: aggregates step results across projects.
- `v_cost_by_task_type`: token usage across projects.
- `v_recurring_knowledge_gaps`: common gaps across projects.
- `v_cross_project_discoveries`: bead discoveries shared across projects.

**3. Central-to-local enrichment** (`core/improve/loop.py:_apply_central_signals()`, line 416):
- During the improvement cycle, `ImprovementLoop` queries `CentralStore`
  for:
  - `agent_reliability()` -> merged into local `learned-patterns.json`
    via `PatternLearner.merge_cross_project_signals()` (pattern_learner.py:592).
  - `cost_by_task_type()` -> merged into local
    `budget-recommendations.json` via
    `BudgetTuner.merge_cross_project_cost_signals()` (budget_tuner.py:324).
  - Recurring knowledge gaps (informational only, logged).
  - Project failure rates (informational only, logged).

**4. Package distribution** (`core/distribute/sharing.py`):
- `baton package` creates `.tar.gz` bundles with agents, references,
  and knowledge packs.
- `baton install` extracts to target project.

**Tomoko probes:** "So the central-to-local merge -- what exactly gets
shared? Full patterns with agent sequences and confidence? Or just
agent reliability signals?"

**Expert second pass:**

The `merge_cross_project_signals()` method (pattern_learner.py:592-658)
receives `agent_reliability` rows (containing `agent_name`,
`success_rate`, `total_steps`, `avg_tokens`). These are converted
to `LearnedPattern` objects with:
- `pattern_id = f"central-agent-{agent_name}"`
- `task_type = "agent_reliability"` (not the original task type)
- `recommended_agents = [agent_name]` (single agent, not the full team)
- `source = "central"`

What gets LOST in cross-project sharing:

1. **Team compositions**: Only individual agent reliability is shared,
   not which agent COMBINATIONS work well together. The team patterns
   (`team-patterns.json`) are NOT synced to central.

2. **Task type specificity**: Central patterns are tagged
   `task_type="agent_reliability"`, losing the original task type
   context. The planner won't match these to "new-feature" or "bug-fix"
   queries.

3. **Phase/step detail**: The execution plan structure (which phases,
   which steps) is not encoded in the pattern. Only the success rate
   and token cost survive.

4. **Qualitative signals**: Retrospective "What Worked"/"What Didn't"
   text is synced to central but is NOT read by the enrichment pipeline.
   Only numeric aggregates are used.

5. **Knowledge packs**: Shared via `baton package` (explicit export/import),
   not via the federated sync. Automatic cross-project knowledge
   propagation does not exist.

**Joint verdict: PARTIAL**

Cross-project sharing exists through two complementary mechanisms:
federated sync (automatic, numeric signals) and package distribution
(manual, full artifacts). But the federated path loses significant
context: team compositions, task type specificity, phase structure,
and qualitative insights. The package path preserves full fidelity
but requires manual export/import. A workflow designer would want
the federated path to carry team patterns and task-type-specific
insights, not just per-agent reliability numbers.

**Delta from solo audit:** Solo audit described cross-project learning
at the feature level ("SyncEngine pushes to central.db, analytics views
exist"). This dialogue exposes FIVE new findings about what gets LOST:
(1) team compositions not shared, (2) task type context lost, (3) phase
structure lost, (4) qualitative signals not read by enrichment,
(5) knowledge packs require manual packaging.

---

## Item 9: Bead Quality Signals

**Tomoko asks:** "Beads have a quality_score. How is it computed? Can
I influence it? Does it actually predict whether a bead is useful?"

**Expert investigates:**

Bead quality scoring spans three modules:

**1. Initial quality** (`core/engine/bead_store.py`):
- `quality_score` defaults to 0.0 at creation (models/bead.py field).
- No initial score computation -- all beads start at zero.

**2. Agent feedback** (`core/engine/bead_signal.py`, lines 76-131):
- Agents can output `BEAD_FEEDBACK: <bead-id> useful|misleading|outdated`.
- Parsed by `parse_bead_feedback()`, returns `(bead_id, delta)` tuples.
- Deltas (line 83-87):
  - `useful`: +0.5
  - `misleading`: -0.5
  - `outdated`: -0.3

**3. Score update** (`core/engine/bead_store.py:update_quality_score()`, line 488):
- `quality_score = MAX(-1.0, MIN(1.0, quality_score + delta))`
- Clamped to [-1.0, 1.0].

**4. Retrieval count** (`bead_store.py:increment_retrieval_count()`, line 465):
- Incremented each time a bead is selected by `BeadSelector`.
- Tracked separately from quality_score.

**5. Selection influence** (`core/engine/bead_selector.py`, line 54-57):
- Within a selection tier, beads are sorted by type priority first,
  then by `quality_score` as a tiebreaker (higher score = selected first).
- Quality score does NOT gate selection -- even negative-scored beads
  can be selected if they're in a high-priority tier and have the
  right type.

**6. Scorecard integration** (`core/improve/scoring.py`, lines 320-333):
- `avg_bead_quality` is computed per agent and included in the
  `AgentScorecard`. But it is NOT used in the `health` calculation
  or any downstream decision (evolution proposals, recommendations).

**Tomoko probes:** "So the quality score is purely based on agent
self-reporting? An agent says 'this bead was useful' and the score
goes up? That's circular -- the agent that CREATED the bead judges
whether it was useful?"

**Expert second pass:**

The BEAD_FEEDBACK protocol is designed for the RECEIVING agent to
judge beads it was given, not the producing agent. The `BeadSelector`
injects beads into delegation prompts for downstream agents. Those
downstream agents then output `BEAD_FEEDBACK: <id> useful` if the
bead helped them.

However, there is no enforcement of this separation. Nothing prevents
the producing agent from self-judging. And since agent output is
parsed mechanically, the quality signal depends entirely on whether
agents consistently output BEAD_FEEDBACK lines -- which depends on
their prompt instructions.

The score is NOT predictive in any validated sense. There is no
ground truth: no human-labeled dataset of "useful" vs. "not useful"
beads. The score is a popularity/recency signal, not a validated
quality metric. The retrieval count is a complementary engagement
signal but similarly unvalidated.

The `bead_decay` mechanism (`bead_store.py:decay()`, line 518) archives
closed beads older than N days, but this is time-based, not quality-based.
Low-quality beads persist until they age out.

**Joint verdict: PARTIAL**

The quality scoring infrastructure exists: feedback protocol, score
update, clamping, selection tiebreaker, per-agent aggregation. But
the system is purely agent-self-reported with no ground truth validation.
Quality scores are unvalidated popularity signals. A workflow designer
would want: (1) quality-based gating in selection (not just tiebreaker),
(2) quality-based decay (prune low-quality beads faster), (3) human
validation of the signal.

**Delta from solo audit:** Solo audit did not evaluate bead quality in
depth. This dialogue is entirely NEW: the +0.5/-0.5/-0.3 delta table,
the circular self-reporting risk, the absence of ground truth validation,
the fact that quality is only a tiebreaker not a gate, and the time-based
(not quality-based) decay.

---

## Item 10: Routing Override Granularity

**Tomoko asks:** "I want to override routing for a specific task type --
'always use my custom test-engineer--property-based for tasks tagged
testing.' How granular is the override system?"

**Expert investigates:**

The override system lives in `core/learn/overrides.py` (`LearnedOverrides`)
and is consumed by `core/orchestration/router.py` (`AgentRouter.route()`).

**Current override capabilities:**

1. **Flavor overrides** (overrides.py:110-123): Keyed by
   `(stack_key, agent_base)`. Example: set
   `flavor_map["python/react"]["backend-engineer"] = "python"`.
   Consumed at router.py:257-283 where learned overrides take
   precedence over the hardcoded `FLAVOR_MAP`.

2. **Agent drops** (overrides.py:140-153): A list of agent names
   excluded from ALL plans. Example: `agent_drops: ["visualization-expert"]`.

3. **Gate command overrides** (overrides.py:125-137): Keyed by
   `(language, gate_type)`. Example: set
   `gate_commands["typescript"]["test"] = "vitest run"`.

4. **Classifier adjustments** (overrides.py:data structure): A
   `min_keyword_overlap` threshold for the keyword-based classifier.

**Tomoko probes:** "But none of these are task-type-specific! I can
override by stack (Python projects) or agent role (backend-engineer),
but I can't say 'for task_type=testing, use test-engineer--property-based.'
The override system has no task-type dimension?"

**Expert second pass:**

Correct. The override system has TWO dimensions:
- Stack (language/framework) -> flavor
- Agent name -> drop

It does NOT have:
- Task type -> agent override
- Task tag -> agent override
- Phase -> agent override
- Conditional routing (if X then use Y)

The `AgentRouter.route()` method (router.py:212) takes `base_name`
and `stack`, not task type. The planner decides which base roles to
use for a task type (via `_DEFAULT_AGENTS` dict), then the router
flavors each role. There's no override point between "planner selects
roles" and "router flavors roles."

To achieve Tomoko's goal ("always use test-engineer--property-based
for testing tasks"), she would need to either:

1. **Modify `_DEFAULT_AGENTS`**: Add `"test": ["test-engineer--property-based"]`
   to the compile-time dict. This requires code changes.

2. **Create a learned pattern**: Run enough testing tasks with her
   preferred agent and let the `PatternLearner` surface it. But this
   requires 11+ successful runs (per Item 2).

3. **Write a hook**: Use Claude Code's `settings.json` hook system
   to pre-process the plan and swap agent names. This is outside baton
   proper.

The `LearnedOverrides` format is extensible (it's a JSON dict), so
adding a `"task_type_routing"` section is architecturally feasible but
not implemented.

**Joint verdict: PARTIAL**

Routing overrides exist for stack-based flavor selection and agent
exclusion. But the override system lacks a task-type dimension entirely.
A workflow designer cannot say "for this kind of task, always use this
specific agent variant." The granularity stops at stack x role, not
task-type x role.

**Delta from solo audit:** Solo audit did not evaluate routing override
granularity at this depth. NEW findings: (1) the override system has
exactly two dimensions (stack, agent name), (2) no task-type dimension
exists, (3) the override point is between planner role selection and
router flavoring, with no hook between "planner selects roles" and
"router flavors them", (4) three workarounds identified.

---

## Item 11: Learning System Convergence

**Tomoko asks:** "After 100 executions, has the learning system
actually improved outcomes? Can I measure before/after? What metrics
show convergence?"

**Expert investigates:**

The system provides several convergence signals:

**1. Pattern confidence growth** (`PatternLearner`):
- Confidence formula `min(1.0, n/15 * success_rate)` saturates at
  n=15 with perfect success. At 100 executions, you'd have well-saturated
  patterns. Can be measured via `baton patterns --refresh` output.

**2. Agent scores over time** (`PerformanceScorer.detect_trends()`):
- OLS linear regression over last N tasks, reported as
  "improving"/"degrading"/"stable". Available via `baton scores --trends`.

**3. Anomaly detection** (`TriggerEvaluator.detect_anomalies()`):
- Failure rate, retry spikes, gate failures, budget overruns.
  If the learning system is converging, anomaly counts should decrease.

**4. Budget tuner convergence** (`BudgetTuner`):
- After enough data, budget recommendations stabilize (no more
  upgrade/downgrade suggestions).

**5. Improvement reports** (`ImprovementLoop.run_cycle()`):
- Each cycle produces an `ImprovementReport` with counts of
  auto-applied, escalated, and experiment results. Stored at
  `.claude/team-context/improvements/reports/<id>.json`.

**6. Cross-project analytics** (`CentralStore` views):
- `v_agent_reliability`, `v_cost_by_task_type`,
  `v_project_failure_rate`. Queryable via `baton cquery`.

**Tomoko probes:** "But is there a single convergence dashboard? Can
I see 'week 1 failure rate was 40%, week 10 failure rate is 15%'?
Or do I need to run 6 different commands and manually correlate?"

**Expert second pass:**

There is NO single convergence dashboard or convergence metric.
The data exists in multiple locations:

- Agent trends: `baton scores --trends` (per-agent, no timeline).
- Patterns: `baton patterns` (confidence values but no temporal view).
- Anomalies: `baton anomalies` (current snapshot, no history).
- Experiments: `baton experiment list` (individual results, no aggregate).
- Reports: improvement report JSON files (need manual parsing).
- Cross-project: `baton cquery` with custom SQL.

To answer "did outcomes improve after 100 executions?", Tomoko would
need to:

1. Run `baton cquery` with custom SQL against central.db:
   ```sql
   SELECT
     STRFTIME('%Y-W%W', timestamp) AS week,
     AVG(CASE WHEN status='complete' THEN 1.0 ELSE 0.0 END) AS success_rate,
     AVG(estimated_tokens) AS avg_tokens
   FROM step_results
   WHERE project_id = 'my-project'
   GROUP BY week
   ORDER BY week
   ```

2. Or parse the JSONL usage log programmatically and compute time-series
   metrics herself.

The `baton dashboard` command (observe subsystem) exists but renders
a current-state summary, not a convergence timeline.

The system does NOT track or visualize:
- Metrics over time (time-series view).
- Before/after comparisons for applied recommendations.
- Aggregate learning velocity (how quickly patterns stabilize).
- Diminishing returns (when the learning system has extracted all
  available signal).

**Joint verdict: PARTIAL**

The raw data for convergence analysis exists and is queryable. Individual
metrics (trends, patterns, anomalies, experiments) are accessible
through CLI commands. But there is no convergence dashboard, no
time-series visualization, no aggregate convergence metric. Measuring
whether the learning system has improved outcomes requires manual SQL
queries and data analysis. For a data-driven workflow designer, this
is a significant gap -- the system that claims to learn cannot easily
demonstrate that it has learned.

**Delta from solo audit:** Solo audit mentioned cross-project learning
and queryability. This dialogue exposes NEW findings: (1) no single
convergence metric exists, (2) no time-series view of improvement,
(3) measuring convergence requires manual SQL, (4) the dashboard is
current-state only, (5) no tracking of learning velocity or diminishing
returns.

---

## Item 12: Contribution Path

**Tomoko asks:** "I want to contribute a custom gate type and a pattern
back to the project. What's the actual mechanism? Is the codebase
structured for external contribution?"

**Expert investigates:**

**CONTRIBUTING.md** exists at the repo root. It covers:
- Development setup: `pip install -e ".[dev]"`, `pytest`.
- Project layout with a directory table.
- Code change workflow: feature branch, tests, invariants.
- Agent/reference change protocol: validate, test with real run.
- Documentation update requirements.
- Commit message style (imperative mood).
- PR guidelines (single concern, tests pass).
- Code style (snake_case, PascalCase, UPPER_CASE, canonical imports).

**Custom gate type contribution path:**

1. Gate types are defined in `models/execution.py:PlanGate` (field
   `gate_type: str`). Adding a new type requires:
   - Adding evaluation logic to `core/engine/gates.py:GateRunner`.
   - The existing types (build, test, lint, spec, review) are
     evaluated via a dispatch pattern, not a plugin registry.
   - No plugin/extension mechanism exists -- adding a gate type
     requires core code changes.

2. The `PlanGate.command` field accepts arbitrary shell commands, so
   a CUSTOM gate command works without code changes. But a custom
   gate TYPE (with custom evaluation semantics) requires modifying
   `gates.py`.

**Pattern contribution path:**

1. Patterns are per-project (stored in `learned-patterns.json` under
   `.claude/team-context/`). There's no global pattern repository.

2. Cross-project sharing happens via the federated sync (as covered
   in Item 8), but this is automatic and numeric, not a contribution
   mechanism.

3. To share a pattern as a community contribution, you would need to
   contribute it as code: modify `_PHASE_NAMES` / `_DEFAULT_AGENTS`
   in `planner.py`, or add a new task type classification.

**Tomoko probes:** "So there's no plugin system? No way to register
a custom gate evaluator or a pattern template without forking the
core?"

**Expert second pass:**

Correct. The codebase has no plugin/extension mechanism for:
- Custom gate evaluators (no `register_gate_type()` API).
- Custom pattern templates (no `register_template()` API).
- Custom recommendation strategies (no `register_recommender()` API).
- Custom scoring algorithms (no `register_scorer()` API).

The closest extensibility points are:
- **Gate commands**: `PlanGate.command` accepts any shell command, so
  you can run arbitrary validation scripts as gate checks.
- **Knowledge packs**: Fully extensible -- create `.md` files in
  `.claude/knowledge/`.
- **Agent definitions**: Create `.md` files in `agents/`.
- **Learned overrides**: JSON file with flavor/gate/drop overrides.
- **External source adapters**: Protocol-based (`ExternalSourceAdapter`
  in `core/storage/adapters/`) -- this IS a proper extension point
  with a protocol/interface.

The `ExternalSourceAdapter` protocol (referenced in storage adapters)
is the only real plugin architecture in the codebase. Gate types,
scoring, learning, and recommendations are all monolithic.

The `CONTRIBUTING.md` is well-structured for PR-based contribution
to the core, but there's no pathway for "add my custom gate type
as a plugin that doesn't require core changes."

**Joint verdict: PARTIAL**

The project has a clear contribution guide (`CONTRIBUTING.md`) and
a well-organized codebase that accepts PRs. But there is no plugin
architecture for custom gate types, scoring algorithms, or pattern
templates. Contributing these requires core code changes via PRs,
not runtime extensions. The only proper extension points are: agent
definitions (file-based), knowledge packs (file-based), learned
overrides (JSON), and external source adapters (protocol-based).

**Delta from solo audit:** Solo audit did not evaluate contribution
paths. This dialogue is entirely NEW: (1) the gate evaluation dispatch
pattern has no plugin registry, (2) no extensibility for scoring/
learning/recommendations, (3) the `ExternalSourceAdapter` protocol
is the only true extension point, (4) the CONTRIBUTING.md exists but
addresses PR-based contribution, not plugin-based extension.

---

## Summary Scorecard

| Item | Verdict | Key Finding |
|------|---------|-------------|
| 1. Scoring formula transparency | WORKS | Fully transparent, but no composite score, crude substring matching, magic-number slope threshold |
| 2. Pattern learner accuracy | PARTIAL | "Confidence" is a heuristic ramp (not statistical), needs 11+ samples at perfect success |
| 3. Experiment rigor | PARTIAL | No A/B split, no statistical tests, 5 samples is underpowered, before/after only |
| 4. Evolve proposal quality | PARTIAL | Suggestions are static templates keyed to metric thresholds, not data-driven |
| 5. Knowledge gap pipeline | PARTIAL | Detection through resolution is solid; verification step ("did it help?") is missing |
| 6. Custom agent iteration | PARTIAL | Components exist but minimum 5 real dispatches, no replay, no parallel variants |
| 7. Plan template expressiveness | PARTIAL | No user-defined templates; phases/agents determined algorithmically |
| 8. Cross-project pattern sharing | PARTIAL | Numeric signals shared; team compositions, task types, and qualitative data lost |
| 9. Bead quality signals | PARTIAL | Agent self-reported, no ground truth, quality is tiebreaker not gate |
| 10. Routing override granularity | PARTIAL | Stack x role only; no task-type dimension in overrides |
| 11. Learning system convergence | PARTIAL | Raw data exists but no convergence dashboard or time-series view |
| 12. Contribution path | PARTIAL | CONTRIBUTING.md exists; no plugin architecture for gates, scoring, or patterns |

**Overall: 1 WORKS, 11 PARTIAL, 0 BLOCKED**

## New Findings vs. Solo Audit

The solo audit (persona-priya-tomoko.md) rated Tomoko's journey as
13 WORKS, 4 PARTIAL, 0 BLOCKED across 17 checks. This team dialogue
investigated 12 items at deeper technical depth and found substantially
more limitations:

| Area | Solo Audit | Team Dialogue | Delta |
|------|-----------|---------------|-------|
| Scoring | WORKS (surface) | WORKS (with caveats) | Exposed: substring precision bug, magic-number slope, no confidence intervals |
| Patterns | WORKS | PARTIAL | Exposed: 11-sample minimum (not 5), heuristic-not-statistical confidence |
| Experiments | WORKS | PARTIAL | Exposed: NOT A/B testing, no statistical tests, underpowered samples |
| Evolution proposals | WORKS | PARTIAL | Exposed: suggestions are static templates, not data-driven |
| Knowledge gap pipeline | Not evaluated | PARTIAL | Entirely new: auto-resolve from beads, empty stub problem, missing verification |
| Agent iteration | WORKS | PARTIAL | Exposed: 5-dispatch minimum, no replay, no parallel variants |
| Plan templates | PARTIAL | PARTIAL | Added: four workaround paths, 11-sample pattern threshold |
| Cross-project sharing | WORKS | PARTIAL | Exposed: five categories of data loss in federated sync |
| Bead quality | Not evaluated | PARTIAL | Entirely new: circular self-reporting, quality as tiebreaker only |
| Routing overrides | Not evaluated | PARTIAL | Entirely new: two-dimension limit, no task-type routing |
| Convergence measurement | Not evaluated | PARTIAL | Entirely new: no convergence dashboard, manual SQL required |
| Contribution path | Not evaluated | PARTIAL | Entirely new: no plugin architecture, PR-only contribution |

**Total new findings: 29 specific issues not identified in the solo audit.**

## Priority Recommendations for Tomoko

1. **Statistical testing in experiments** -- Add a proper two-sample
   test (Welch's t-test or Mann-Whitney U) and report p-values.
   Calculate minimum sample size via power analysis.

2. **Convergence dashboard** -- Add a `baton convergence` command that
   renders time-series of key metrics (success rate, token cost,
   anomaly count) over execution windows.

3. **Task-type routing overrides** -- Extend `LearnedOverrides` with
   a `task_type_routing` section so users can map task types to
   specific agent variants.

4. **Plugin architecture for gates** -- Introduce a `GateEvaluator`
   protocol and a registration mechanism so custom gate types can be
   added without core code changes.

5. **Data-driven evolution proposals** -- Read actual retrospective
   failure text and surface the top-3 concrete failure patterns
   instead of static suggestion templates.
