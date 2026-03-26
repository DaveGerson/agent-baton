# Adaptive Execution & Activity Chaining

How the orchestrator classifies task complexity and selects the right
engagement level — from single-agent direct execution to full multi-phase
orchestration. Also defines how multiple activities chain together so that
small tasks benefit from orchestration without paying its full overhead
individually.

---

## Why Adaptive Execution

Not every task needs the full orchestration pipeline. A 3-file CSS change
doesn't need a written plan, shared context document, mission log, auditor
review, and code reviewer pass. But that same change, when part of a larger
initiative (e.g., "implement Phase A2"), benefits from being orchestrated
alongside its siblings — shared context is loaded once, QA gates run once
over the combined output, and the orchestrator catches cross-cutting
concerns between tasks that look independent.

The orchestrator classifies every incoming task (or batch of tasks) and
selects the minimum engagement level that produces reliable results.

---

## Engagement Levels

### Level 1: Direct (single agent, no ceremony)

**Profile:** Small, well-scoped, single-domain, low risk.

**Pipeline:**
1. Identify the right specialist agent
2. Dispatch with a focused prompt (no shared context doc, no plan on disk)
3. Verify the output yourself (quick read of changed files)
4. Commit

**What's skipped:** Plan on disk, shared context document, mission log,
QA gates, code review agent, auditor.

**Overhead:** ~1 agent dispatch. Minimal orchestrator reasoning.

**Example tasks:**
- "Persist plot status to sessionLog" (3 files, small effort)
- "Add a 'Send to Notes' button on DM Coach results" (2 files, small effort)
- "Fix a typo in the README"
- "Add a missing type field to an interface"

### Level 2: Coordinated (1-2 agents, light ceremony)

**Profile:** Medium scope, single domain, may introduce new components or
touch shared utilities. Benefits from a brief plan but doesn't need full
orchestration.

**Pipeline:**
1. Write a brief inline plan (in your response, not to disk)
2. Dispatch the primary specialist agent with clear boundaries
3. Run a build/test gate after completion
4. Dispatch code-reviewer if the change is substantial
5. Commit

**What's skipped:** Plan on disk, shared context document, mission log,
auditor review. QA gates are lightweight (build check only).

**Overhead:** ~1-2 agent dispatches. Brief planning.

**Example tasks:**
- "Add sidebar search/filter" (1 file, medium effort, new behavior)
- "Add loading skeletons to dashboards" (new component + integration)
- "Refactor a service method that's gotten too long"

### Level 3: Full Orchestration (multi-agent, full ceremony)

**Profile:** Large scope, multi-domain, introduces new architecture, or
high risk. This is the existing orchestration pipeline.

**Pipeline:** The complete workflow — research, decompose, plan (on disk),
risk triage, auditor (if MEDIUM+), shared context, mission log, git branch,
multi-agent dispatch with handoffs, QA gates between phases, integration
review, completion report.

**What's skipped:** Nothing. Full pipeline.

**Overhead:** 3-8 agent dispatches. Full planning and coordination.

**Example tasks:**
- "Build the Smart Context Window service" (new service + integration + tests)
- "Implement the Session Prep Wizard" (new component, AI service, domain logic)
- "Add cloud authentication and sync" (multi-domain architecture)

---

## Classification Procedure

When the orchestrator receives a task (or batch of tasks), classify each
one before execution.

### Signal Matrix

| Signal | Level 1 | Level 2 | Level 3 |
|--------|---------|---------|---------|
| Files touched | 1-3 | 3-6 | 6+ |
| Domains involved | 1 | 1 | 2+ |
| New files created | 0 | 0-1 | 1+ |
| Effort (if stated) | Small | Medium | Large |
| New architectural patterns | No | No | Yes |
| Cross-cutting concerns | No | Minor | Yes |
| Risk level | LOW | LOW | MEDIUM+ |
| Depends on other tasks | No | Maybe | Often |

### Classification Rules

1. **Count the signals.** For each task, evaluate the signal matrix.
2. **Highest level wins.** If any signal points to Level 3, it's Level 3.
   Exception: a single Level 3 signal can be overridden if the rest are
   clearly Level 1 (use judgment).
3. **New architecture = Level 3.** If the task introduces a pattern the
   codebase doesn't have yet (new service layer, new state management
   approach, new build pipeline), it's Level 3 regardless of file count.
4. **Risk trumps scope.** A small change to auth logic is Level 3 (high
   risk), not Level 1 (small scope).
5. **Uncertainty bumps up.** If you can't confidently classify a task,
   bump it one level. The cost of over-orchestrating is ceremony; the cost
   of under-orchestrating is quality failures.

### Quick Classification Shortcut

Ask yourself two questions:

1. **Could a single well-prompted agent complete this in one pass without
   needing handoffs or architectural decisions?**
   - Yes → Level 1 or 2 (based on scope)
   - No → Level 3

2. **If this task fails, what breaks?**
   - Just this feature → Level 1 or 2
   - Other features, data integrity, or user trust → Level 3

---

## Activity Chaining

Activity chaining lets the orchestrator bundle multiple tasks into a single
orchestrated run. The chain is the unit of orchestration — individual
activities within the chain get the engagement level they need, but share
the overhead of context loading, QA, and review.

### Why Chain

- **Amortized overhead.** Context is loaded once. The codebase profile,
  project conventions, and shared context serve the entire chain.
- **Combined QA.** Build and test gates run once over the combined output,
  not per-task. Code review covers the full diff.
- **Cross-cutting detection.** The orchestrator sees all activities together
  and can detect that three "independent" tasks all touch the same file,
  requiring sequencing that wouldn't be obvious if run separately.
- **One branch, one PR.** The chain produces a single logical unit of work
  with individually committed activities.
- **Context accumulation.** Later activities benefit from the context and
  output of earlier ones — file paths discovered, patterns established,
  utilities created.

### Chain Structure

```
Chain: "[Initiative Name]"
│
├── Chain Setup (once)
│   ├── Load/verify codebase profile
│   ├── Create chain context (lightweight shared context)
│   ├── Create git branch
│   └── Classify all activities
│
├── Activity 1: [Task] [Level 1: Direct]
│   └── Single agent dispatch → commit
│
├── Activity 2: [Task] [Level 2: Coordinated]
│   ├── Agent dispatch with boundaries
│   ├── Build gate
│   └── Commit
│
├── Activity 3: [Task] [Level 3: Full]
│   ├── Full planning for this activity
│   ├── Multi-agent dispatch with handoffs
│   ├── Per-phase QA gates
│   └── Commit per agent
│
├── Chain Gate: Build + Test + Integration
│   └── Covers all activities in the chain
│
└── Chain Review: code-reviewer (one pass, full diff)
```

---

## Engine Integration

As of v0.2, the engagement level classification is implemented in the
engine's planner via the `TaskClassifier` protocol. This means the
engine itself right-sizes plans — it is no longer solely the
orchestrator's responsibility to classify before calling `baton plan`.

### How It Works

When `baton plan "task description"` is called:

1. The planner invokes a `TaskClassifier` (default: `FallbackClassifier`)
2. `FallbackClassifier` tries `HaikuClassifier` first (Claude Haiku via
   the Anthropic SDK, ~500 tokens, <1s)
3. If Haiku is unavailable (no API key, network error, SDK not
   installed), falls back to `KeywordClassifier` (deterministic
   heuristics)
4. The classifier returns: task type, complexity (light/medium/heavy),
   recommended agents, and phase names
5. The planner builds a plan using the classification result

### Engagement Level to Complexity Mapping

| Engagement Level | Engine Complexity | Plan Shape |
|-----------------|-------------------|------------|
| Level 1: Direct | `light` | 1 agent, 1 phase (Implement only) |
| Level 2: Coordinated | `medium` | 2-3 agents, 2-3 phases (no Review) |
| Level 3: Full Orchestration | `heavy` | 3-5 agents, 3-4 phases (full) |

### Orchestrator Override

The orchestrator can pass `--complexity light|medium|heavy` to `baton
plan` to explicitly set the complexity level, bypassing the classifier.
This is useful when the orchestrator has already classified the task and
wants to ensure the engine uses that classification.

### What the Orchestrator Still Does

The engine handles plan sizing automatically, but the orchestrator still
decides whether to use the engine at all:

- **Level 1 tasks**: The orchestrator MAY skip `baton plan` entirely and
  dispatch a single agent directly. Or it can call `baton plan
  --complexity light` for traceability.
- **Level 2+ tasks**: The orchestrator calls `baton plan` and the engine
  handles the rest.

### Chain Setup Procedure

1. **List all activities.** Extract individual tasks from the user's request
   or implementation plan phase.

2. **Classify each activity.** Apply the classification procedure to each
   task independently.

3. **Detect cross-cutting concerns.** Check whether multiple activities
   touch the same files. If so:
   - Sequence them (don't run in parallel)
   - Consider merging them into a single activity if the overlap is high
   - Note the shared files in the chain context

4. **Order the chain.** Apply these ordering rules:
   - Dependencies first (if Activity 3 needs Activity 1's output, 1 goes first)
   - Shared-file activities in sequence (avoid merge conflicts)
   - Level 1 tasks before Level 2 before Level 3 (when no dependencies
     exist — small wins build momentum and establish patterns)
   - Type/utility work before component work (new types feed into components)

5. **Write chain context.** A lightweight shared context document covering:
   - The initiative (what the chain accomplishes as a whole)
   - Chain-level guardrails (write boundaries for the whole chain)
   - Activity manifest (ordered list with engagement levels)
   - Cross-cutting notes (shared files, sequencing constraints)

6. **Create git branch.** One branch for the entire chain.

### Executing a Chain

For each activity in order:

**Level 1 activities:**
- Dispatch single agent with chain context reference
- Verify output (quick file read)
- Commit with descriptive message
- Update chain context with any new patterns or utilities created

**Level 2 activities:**
- Brief inline plan
- Dispatch agent with chain context + boundaries
- Run build gate
- Commit
- Update chain context

**Level 3 activities:**
- Write activity-level plan to disk (within the chain context)
- Full orchestration pipeline for this activity only
- Per-phase QA gates
- Commit per agent
- Update chain context with architectural decisions

**After all activities:**
- Run chain-level QA gate (build + test + integration)
- Dispatch code-reviewer for one pass over the full chain diff
- Write chain completion report

### Chain Failure Handling

- **Level 1 activity fails:** Fix inline or retry once. If still failing,
  skip and continue the chain. Log the skip — it becomes a follow-up.
- **Level 2 activity fails:** Retry once with error context. If still
  failing, pause the chain and report. The user decides whether to skip
  or fix.
- **Level 3 activity fails:** Follow standard failure-handling.md. The
  chain pauses at this activity until it's resolved.
- **Chain gate fails:** Diagnose which activity caused the failure. Fix
  that activity only (don't re-run the whole chain). Re-run the gate.

### Context Accumulation

The chain context grows as activities complete. Each activity may add:

- **New files created** — later activities can reference or extend them
- **Patterns established** — "Activity 2 created a SkeletonCard component;
  Activity 5 should use the same pattern for loading states"
- **Utilities discovered** — "Activity 1 extracted a color mapping utility;
  Activities 3 and 4 should import it rather than duplicating"
- **Conventions confirmed** — "Activity 1 used border-l-4 for entity
  colors; maintain this across the chain"

The orchestrator updates the chain context after each activity with a
brief "Chain Update" note. This is NOT a full context rewrite — it's an
append-only log of what changed.

---

## Upgrading Engagement Mid-Task

Sometimes a task that looked like Level 1 turns out to be Level 2 or 3
once the agent starts working. Signals:

- Agent reports a capabilities gap (needs architectural decisions)
- Agent discovers the change touches more files than expected
- Agent introduces a new pattern that needs review
- Build gate fails in a way that suggests architectural issues

**When this happens:** Stop, reclassify, and re-engage at the higher level.
Don't try to force a Level 3 task through a Level 1 pipeline.

In a chain: reclassify the current activity and continue the chain.
Already-completed activities don't need re-running unless the upgrade
reveals that earlier work needs revision.

---

## Engagement Level Selection for Common Task Patterns

| Pattern | Level | Rationale |
|---------|-------|-----------|
| Bug fix in one component | 1 | Single file, known behavior |
| Add field to existing type + update service | 1 | Mechanical, well-patterned |
| New UI component (follows existing pattern) | 2 | New file, but established pattern |
| New UI component (new pattern) | 3 | Architectural decision needed |
| CSS/styling changes across multiple files | 1-2 | Mechanical but broad |
| New service module | 3 | Architecture + implementation + tests |
| Refactor touching 2-3 related files | 2 | Contained scope, benefits from review |
| Refactor touching 6+ files | 3 | Broad impact, needs coordination |
| Anything touching auth/security | 3 | Risk-driven, regardless of scope |
| Implementation plan phase (3+ tasks) | Chain | Bundle the phase as a chain |
| "Build feature X" (vague, large) | 3 | Needs decomposition |
| "Fix these 5 small issues" | Chain | Bundle as chain of Level 1-2 |

---

## Rules

- **Classify before executing.** Never start work without knowing the
  engagement level. Even a quick classification saves time if it prevents
  under-orchestrating a complex task.
- **The chain is the natural unit for implementation plan phases.** When a
  user says "implement Phase B," that's a chain — not 6 separate
  orchestrator invocations and not one monolithic Level 3 task.
- **Don't over-classify.** Level 1 tasks are the majority of real work.
  If you're classifying everything as Level 3, you're being too cautious.
- **Context accumulation is mandatory in chains.** Later activities must
  benefit from earlier ones. If you're not updating chain context after
  each activity, you're running independent tasks, not a chain.
- **One branch per chain.** Chains produce atomic, reviewable units of
  work. Don't create branches per activity within a chain.
- **Chain gates catch what activity gates miss.** Individual build gates
  verify each activity in isolation. The chain gate verifies integration.
  Both are necessary.
