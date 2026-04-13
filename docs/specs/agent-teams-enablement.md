# Agent Teams Enablement — Specification

**Date**: 2026-03-28
**Status**: Proposal
**Scope**: 6 phases, from team execution model through maturation

---

## Executive Summary

Agent-baton dispatches agents in parallel, but they work in isolation. A team
of 3 agents produces 3 independent outputs, not one integrated result. This
spec builds true team execution across four phases: a team model with wave-
based dispatch and synthesis (Phase 1), structured context sharing between
agents (Phase 2), reusable multi-perspective collaboration patterns (Phase 3),
and daemon-native runtime integration (Phase 4).

The core insight driving this work: **the value of agent teams is not
parallelism (that is just speed) — it is diverse perspectives on the same
problem.** A business analyst sees user impact, a security reviewer sees
attack surface, an engineer sees implementation complexity. Synthesizing
these perspectives produces better decisions than any single agent could.

---

## Current State

| Capability | Status |
|---|---|
| Parallel dispatch via `StepScheduler` | Production |
| `TeamMember` model (member_id, role, depends_on) | Production |
| `record_team_member_result()` with auto-aggregation | Production |
| `build_team_delegation_prompt()` per member | Production |
| `ContextManager` (context.md, mission-log.md) | Production |
| `EventBus` with JSONL persistence | Production |
| Daemon mode with crash recovery | Production |
| `baton execute amend` (runtime plan mutation) | Production |

**The gap**: Agents in a team step have no awareness of each other. There is
no shared scratchpad, no decision propagation between parallel agents, no
synthesis step, and no reusable patterns for common collaboration shapes.

---

### Phase 1: Integrated Team Execution Model

#### 1.1 Team Definition Model

Today, team steps embed concrete agent names directly in
`TeamMember.agent_name`. This couples plans to a specific agent roster and
prevents the planner from reasoning about _what kind of work_ needs doing
before choosing _who_ does it.

Phase 1 introduces **team profiles**: capability-based descriptions that the
planner resolves to concrete agents at plan time using the existing
`AgentRouter` and `AgentRegistry`.

##### Data model

```python
# agent_baton/models/team.py

@dataclass
class TeamRole:
    """A slot in a team defined by capability, not by agent name.

    The planner fills each slot by querying AgentRegistry.by_category()
    and AgentRouter.route_agent() at plan time.

    Attributes:
        role_id: Unique within the profile (e.g. "lead", "impl-1").
        capability: What this role does -- matching hint for the router.
        category: AgentCategory constraint (ENGINEERING, DOMAIN, etc.).
        preferred_agent: Optional hard pin. When set, the resolver uses
            this agent directly, skipping category search.
        role_type: "lead" | "implementer" | "reviewer" | "synthesizer".
        depends_on: Other role_ids that must complete before this starts.
        model: LLM model override (empty = inherit from plan).
    """
    role_id: str
    capability: str
    category: AgentCategory = AgentCategory.ENGINEERING
    preferred_agent: str = ""
    role_type: str = "implementer"
    depends_on: list[str] = field(default_factory=list)
    model: str = ""


@dataclass
class TeamProfile:
    """A capability-based team template resolved at plan time.

    Profiles are ephemeral -- created by the planner, resolved immediately,
    and discarded. The durable artifact is PlanStep.team (which already
    exists and is persisted in plan.json).

    Attributes:
        profile_id: Identifier for tracing.
        description: What this team shape is good for.
        roles: The capability slots to fill.
        synthesis: Whether a synthesis step runs after all waves complete.
        synthesis_agent: Agent for synthesis (default: lead role's agent).
    """
    profile_id: str
    description: str
    roles: list[TeamRole] = field(default_factory=list)
    synthesis: bool = False
    synthesis_agent: str = ""


@dataclass
class ResolvedTeam:
    """Output of resolving a TeamProfile against the registry."""
    profile_id: str
    slots: list[ResolvedSlot] = field(default_factory=list)
    synthesis_agent: str = ""
    unresolved: list[str] = field(default_factory=list)


@dataclass
class ResolvedSlot:
    """One resolved team member."""
    role_id: str
    agent_name: str
    role_type: str
    capability: str
    depends_on: list[str] = field(default_factory=list)
    model: str = ""
```

##### Resolution logic

A new `TeamResolver` class in the orchestration package resolves profiles
to concrete agents:

```python
# agent_baton/core/orchestration/team_resolver.py

class TeamResolver:
    """Resolve TeamProfile roles to concrete agent names."""

    def __init__(self, registry: AgentRegistry, router: AgentRouter):
        self._registry = registry
        self._router = router

    def resolve(self, profile: TeamProfile, project_root: Path | None = None) -> ResolvedTeam:
        """Resolve each role to a concrete agent.

        Resolution order per role:
        1. If preferred_agent is set, use it (after routing for flavor).
        2. Query registry.by_category(role.category).
        3. Pick the first agent that passes router.route_agent() for
           the detected stack.
        4. If no match, add to unresolved list.
        """
        ...
```

No new persistence is required — profiles are resolved immediately and the
result is written into the existing `PlanStep.team` list of `TeamMember`
objects.

**Files touched:**
- New: `agent_baton/models/team.py`
- New: `agent_baton/core/orchestration/team_resolver.py`
- Modified: `agent_baton/models/execution.py` — add `synthesis_agent` field to `PlanStep`

---

#### 1.2 Dynamic Team Composition

The `IntelligentPlanner` already runs a 13-stage pipeline: task
classification, pattern lookup, agent selection, stack detection, phase
building, etc. Team composition plugs into this pipeline as a new stage
between agent selection (step 5) and phase building (step 9).

##### When does the planner create a team step vs. solo?

```python
# New method on IntelligentPlanner

def _should_use_team(
    self,
    classification: TaskClassification,
    step_description: str,
    phase_name: str,
) -> TeamProfile | None:
    """Decide whether a step warrants a team.

    Returns a TeamProfile if the step benefits from multiple perspectives,
    or None for solo dispatch.

    Heuristics (in order):
    1. REVIEW phases always get a team: implementer + reviewer.
    2. Steps touching 2+ AgentCategory domains get a cross-functional
       team.
    3. "heavy" complexity tasks with 3+ agents get a team with a lead.
    4. Retrospective feedback that flags "needs-review" for this task
       type triggers a reviewer role.
    """
    ...
```

##### Team size bounds

- **Max members per team**: 5 (diminishing returns beyond this with LLM
  agents — more members means more synthesis work and higher token cost).
- **Budget tier scaling**: Lean plans cap at 2 members, standard at 3,
  full at 5.

##### Integration point in create_plan

After phase building (step 9), for each step in each phase, the planner
calls `_should_use_team()`. If a `TeamProfile` is returned, it resolves
via `TeamResolver` and converts `ResolvedSlot` objects into `TeamMember`
objects on `PlanStep.team`.

**Files touched:**
- Modified: `agent_baton/core/engine/planner.py` — add `_should_use_team()`,
  team composition stage

---

#### 1.3 Team Execution Semantics

Today `_team_dispatch_action()` dispatches all ready members at once. Members
with `depends_on` are skipped and picked up on the next `next_action()` call.
This is already wave-like, but lacks two capabilities: (a) earlier members'
outputs are never injected into later members' prompts, and (b) there is no
synthesis step.

##### Wave dispatch with inter-wave context injection

```python
# Modified _team_dispatch_action in ExecutionEngine

def _team_dispatch_action(self, step: PlanStep, state: ExecutionState):
    """Build DISPATCH action for the next wave of team members.

    Changes from current implementation:
    1. Collect completed members' outcomes as 'prior_work' context.
    2. Pass prior_work into build_team_delegation_prompt() so wave N+1
       agents see what wave N produced.
    3. When all members complete AND step.synthesis_agent is set, return
       a DISPATCH for the synthesizer instead of auto-completing.
    """
    dispatcher = PromptDispatcher()
    completed_members = {
        m.member_id: m.outcome
        for m in (parent.member_results if parent else [])
        if m.status == "complete"
    }

    # --- Synthesis dispatch ---
    non_synth = [m for m in step.team if m.role != "synthesizer"]
    all_done = all(m.member_id in completed_members for m in non_synth)

    if all_done and step.synthesis_agent:
        synth_member = next(
            (m for m in step.team if m.role == "synthesizer"), None
        )
        if synth_member and synth_member.member_id not in completed_members:
            prior_work = self._format_prior_work(completed_members, step)
            prompt = dispatcher.build_synthesis_prompt(
                step=step, member=synth_member,
                prior_work=prior_work,
                shared_context=state.plan.shared_context,
                task_summary=state.plan.task_summary,
            )
            return ExecutionAction(
                action_type=ActionType.DISPATCH,
                agent_name=synth_member.agent_name,
                agent_model=synth_member.model,
                delegation_prompt=prompt,
                step_id=synth_member.member_id,
            )

    # --- Wave dispatch with context injection ---
    member_actions = []
    for member in step.team:
        if member.member_id in completed_members:
            continue
        if member.role == "synthesizer":
            continue
        if not all(dep in completed_members for dep in member.depends_on):
            continue

        prior_work = {
            dep_id: completed_members[dep_id]
            for dep_id in member.depends_on
            if dep_id in completed_members
        }
        prompt = dispatcher.build_team_delegation_prompt(
            step=step, member=member,
            shared_context=state.plan.shared_context,
            task_summary=state.plan.task_summary,
            prior_work=prior_work,  # NEW parameter
        )
        member_actions.append(ExecutionAction(...))

    # ... return actions or WAIT
```

##### Auto-complete changes

With synthesis enabled, the step is not complete until the synthesizer
member has run. `record_team_member_result()` checks: if `synthesis_agent`
is set and synthesizer hasn't run, keep the step open.

**Files touched:**
- Modified: `agent_baton/core/engine/executor.py` — `_team_dispatch_action()`,
  `record_team_member_result()`
- Modified: `agent_baton/core/engine/dispatcher.py` — `build_team_delegation_prompt()`
  (new `prior_work` param), new `build_synthesis_prompt()`
- Modified: `agent_baton/core/runtime/worker.py` — member-ID routing in result recording

---

#### 1.4 User Stories

**Story 1: Cross-functional feature analysis.** A product team asks "Should
we add real-time notifications?" The planner detects ENGINEERING + DOMAIN
categories are needed. Wave 1: `subject-matter-expert` analyzes business
value; `architect` evaluates WebSocket vs SSE vs polling. Wave 2:
`backend-engineer` receives both analyses and produces a technical design
that addresses business requirements using the recommended architecture.
Synthesis: `architect` merges all outputs into a go/no-go recommendation.
Without teams, these three agents produce isolated documents with no
cross-referencing.

**Story 2: Implementation with integrated review.** "Add rate limiting to
the API." Wave 1: `backend-engineer` implements; `test-engineer` writes
tests concurrently. Wave 2: `security-reviewer` receives both the
implementation diff AND the test coverage report. Synthesis:
`backend-engineer` reads the security review, applies fixes, confirms
tests pass. Today the security reviewer would be in a separate phase
with no visibility into test coverage decisions.

**Story 3: Data pipeline with business validation.** "Build ETL for churn
analysis." Wave 1: `data-engineer` designs pipeline schema;
`data-analyst` defines churn metrics and expected output format. Wave 2:
`data-scientist` receives both and builds the transformation layer mapping
the engineer's schema to the analyst's metrics. The data scientist doesn't
have to guess what "churn" means or what schema was chosen.

---

### Phase 2: Inter-Agent Context and Communication

#### 2.1 Problem Statement

Context flows forward (step N's output becomes step N+1's handoff) but NOT
laterally (step N.1 and N.2 running in parallel share nothing). Agents in
the same phase can't see each other's decisions. There is no structured way
for an architect to say "I chose JWT auth" and have the backend engineer
pick that up.

Three specific gaps:
1. **No lateral context within a phase.** Parallel steps share nothing.
2. **No structured decision propagation across phases.** The `handoff_from`
   string is the last step's raw outcome. Decisions are buried in prose.
3. **No domain-aware filtering.** Every agent gets the same `shared_context`.
   There is no mechanism to route API decisions to engineers but not to
   test engineers writing unrelated tests.

#### 2.2 Structured Decision Log

A typed, append-only log where agents record decisions during execution.
Stored as `decisions.json` in the task's execution directory.

```python
# agent_baton/models/decision_log.py

class DecisionType(Enum):
    API_CONTRACT = "api-contract"
    IMPLEMENTATION_CHOICE = "implementation-choice"
    ARCHITECTURE_DECISION = "architecture-decision"
    DATA_MODEL = "data-model"
    DEPENDENCY_ADDED = "dependency-added"
    RISK_IDENTIFIED = "risk-identified"

# Which agent roles consume which decision types.
DECISION_RELEVANCE: dict[DecisionType, set[str]] = {
    DecisionType.API_CONTRACT: {
        "backend-engineer", "frontend-engineer", "test-engineer", ...
    },
    DecisionType.ARCHITECTURE_DECISION: {
        "backend-engineer", "frontend-engineer", "architect",
        "devops-engineer", "security-reviewer", ...
    },
    DecisionType.RISK_IDENTIFIED: {
        "architect", "security-reviewer", "code-reviewer", ...
    },
    # ... other mappings
}


@dataclass
class AgentDecision:
    """A single structured decision recorded by an agent."""
    decision_id: str                    # auto-generated
    agent_name: str
    step_id: str
    phase_id: int
    timestamp: str                      # ISO 8601
    decision_type: str                  # DecisionType value
    summary: str                        # 1-3 sentences
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)
    dependencies_created: list[str] = field(default_factory=list)
    dependencies_consumed: list[str] = field(default_factory=list)


@dataclass
class DecisionLog:
    """Append-only log of all decisions for a task execution."""
    task_id: str
    decisions: list[AgentDecision] = field(default_factory=list)

    def append(self, decision: AgentDecision) -> None:
        # Deduplicate by (step_id, decision_type, summary) tuple
        key = (decision.step_id, decision.decision_type, decision.summary)
        for existing in self.decisions:
            if (existing.step_id, existing.decision_type, existing.summary) == key:
                return
        self.decisions.append(decision)

    def relevant_to(self, agent_name: str) -> list[AgentDecision]:
        """Return decisions relevant to a given agent role."""
        result = []
        for d in self.decisions:
            try:
                dtype = DecisionType(d.decision_type)
            except ValueError:
                result.append(d)  # unknown type -> include (fail open)
                continue
            relevant = DECISION_RELEVANCE.get(dtype, set())
            if not relevant or agent_name in relevant:
                result.append(d)
        return result
```

#### 2.3 Agent-Side Protocol

The delegation prompt already instructs agents to log decisions. Phase 2
upgrades the instruction to request structured output:

```markdown
## Decision Logging

When you make a non-obvious decision, document it under a '## Decisions'
heading using this format:

- **Type**: api-contract | implementation-choice | architecture-decision |
  data-model | dependency-added | risk-identified
- **Summary**: 1-3 sentence description
- **Artifacts**: file paths created or modified (if any)
- **Creates dependency**: what downstream agents need from this (if any)
```

A new `decision_parser.py` module extracts `## Decisions` blocks from agent
outcomes into `AgentDecision` objects. It is called from
`ExecutionEngine.record_step_result()` after the existing deviation and
knowledge-gap parsing.

#### 2.4 Context Injection Between Dispatch Waves

The key change is in `_dispatch_action()`: after loading the decision log,
it filters to decisions relevant to the target agent and passes them to
`build_delegation_prompt()`.

```python
# Modified _dispatch_action in ExecutionEngine

def _dispatch_action(self, step, state):
    # ... existing handoff logic ...

    # NEW: load decision log, filter to relevant decisions
    decision_log = self._persistence.load_decision_log(state.task_id)
    relevant = decision_log.relevant_to(step.agent_name)

    prompt = dispatcher.build_delegation_prompt(
        step,
        shared_context=state.plan.shared_context,
        handoff_from=handoff,
        task_summary=state.plan.task_summary,
        team_decisions=relevant,  # NEW parameter
    )
```

`build_delegation_prompt()` renders a new `## Team Decisions` section:

```markdown
## Team Decisions

The following decisions were made by other agents in this execution.
Treat them as authoritative constraints -- do not revisit them.

- [api-contract] (architect, step 1.1): Authentication uses JWT with RS256.
  Tokens issued at /auth/token, validated via middleware.
  Artifacts: src/auth/middleware.py, docs/api-auth.md
  Requires: All API handlers must use @require_auth decorator
```

**The worker loop itself does not change.** The existing sequence —
`next_actions()` returns dispatchable steps, each built via
`_dispatch_action()` which now reads the decision log — means that after
batch N completes and its decisions are parsed, batch N+1 automatically
receives those decisions.

**Token budget guard**: A 2000-token cap on the `## Team Decisions` section.
If truncation triggers, prioritize `architecture-decision` and
`api-contract` types (highest downstream impact).

#### 2.5 Event-Driven Context Propagation

A `TeamContextPropagator` subscribes to `step.completed` events and writes
decisions to the log as a consistency backstop:

```python
# agent_baton/core/engine/context_propagator.py

class TeamContextPropagator:
    """EventBus subscriber that captures decisions from step.completed events."""

    def __init__(self, persistence, bus):
        self._sub_id = bus.subscribe("step.completed", self._on_step_completed)

    def _on_step_completed(self, event):
        decisions = parse_decisions(
            outcome=event.payload.get("outcome", ""),
            agent_name=event.payload.get("agent_name", ""),
            step_id=event.payload.get("step_id", ""),
            phase_id=event.payload.get("phase_id", 0),
        )
        if decisions:
            log = self._persistence.load_decision_log(event.task_id)
            for d in decisions:
                log.append(d)  # dedup built into append()
            self._persistence.save_decision_log(log)
            self._bus.publish(Event.create(
                topic="decision.recorded", task_id=event.task_id,
                payload={"count": len(decisions)},
            ))
```

#### 2.6 Cross-Phase Context Accumulation

When the engine advances from Phase 1 to Phase 2, the first step of Phase 2
receives all decisions from Phase 1 via the decision log (which
`_dispatch_action()` loads in full). A `## Decisions from Previous Phase`
summary is prepended to the handoff when dispatching the first step in a new
phase, replacing the fragile "last outcome string" with a structured digest
preserving every agent's contributions.

#### 2.7 User Stories

**Story 1: Architect and backend engineer align.** Phase 1 has two parallel
steps: architect designs API contract, backend engineer scaffolds the
project. Without context sharing, the frontend engineer in Phase 2 gets
only the backend engineer's outcome and may adopt a conflicting convention.
With Phase 2, the frontend engineer's prompt includes the architect's
`api-contract` decision: "Auth endpoint is POST /auth/token, returns
{ access_token, refresh_token, expires_in }."

**Story 2: Risk propagation.** The architect identifies a path traversal
vulnerability in user-uploaded files. This is recorded as `risk-identified`.
In Phase 2, the `security-reviewer` receives this decision because
`DECISION_RELEVANCE[RISK_IDENTIFIED]` includes `security-reviewer`. The
reviewer focuses its audit on file upload handling rather than spending
tokens on unrelated code.

**Story 3: Data model consistency.** A data-engineer defines the schema:
"UUID primary keys, JSONB preferences column." Both the backend-engineer
(building ORM models) and data-analyst (writing queries) receive this as
a `data-model` decision, preventing schema drift.

#### 2.8 Risks

| Risk | Mitigation |
|---|---|
| Agents produce malformed decision blocks | Parser defaults unknown types, skips entries without summary. Free-text `handoff_from` still propagates as fallback. |
| Decision log grows unbounded | 2000-token cap on injection. Typical execution produces 5-15 decisions. |
| Same-batch parallel agents can't share laterally | Deliberate trade-off. Planner should sequence decision-dependent steps via `depends_on`. |
| File contention on decisions.json | EventBus is synchronous; writes are sequenced. Content-based dedup handles theoretical double-writes. |

**Files to create:**
- `agent_baton/models/decision_log.py`
- `agent_baton/core/engine/decision_parser.py`
- `agent_baton/core/engine/context_propagator.py`

**Files to modify:**
- `agent_baton/core/engine/persistence.py` — `save/load_decision_log()`
- `agent_baton/core/engine/executor.py` — parse decisions in `record_step_result()`, pass in `_dispatch_action()`
- `agent_baton/core/engine/dispatcher.py` — render `## Team Decisions` section

---

### Phase 3: Multi-Perspective Analysis and Synthesis

#### 3.1 Problem Statement

Today's team steps are a parallelism mechanism. Results are aggregated by
concatenating outcomes with semicolons. The `TeamMember.role` field supports
only "lead", "implementer", and "reviewer" — there is no synthesizer,
challenger, or facilitator. `build_team_delegation_prompt` tells each member
"you are part of a team" but does not frame the collaboration shape.

The gap is structural. Agent teams need a **pattern** that defines the
collaboration shape, and a **synthesis step** that integrates diverse
outputs into a single coherent deliverable.

#### 3.2 Team Pattern Data Model

```python
# agent_baton/models/team_pattern.py

@dataclass
class PatternSlot:
    """A capability slot within a team pattern."""
    slot_id: str                    # e.g. "analyst", "challenger"
    capability: str                 # e.g. "security-review"
    description: str = ""
    model: str = ""
    optional: bool = False


class FlowType(Enum):
    DIVERGE_CONVERGE = "diverge-converge"
    RELAY = "relay"
    CHALLENGE = "challenge"
    PANEL = "panel"


@dataclass
class FlowWave:
    """A group of slots that execute together within a pattern flow.

    Waves execute sequentially. Slots within a wave execute in parallel.
    """
    wave_id: int
    slot_ids: list[str]
    prompt_framing: str = ""        # injected into delegation prompts
    receives_prior_output: bool = True


@dataclass
class SynthesisSpec:
    """How the pattern produces a unified output."""
    synthesizer_slot_id: str
    input_format: str = "structured"    # "full" | "structured"
    conflict_handling: str = "surface"   # "surface" | "resolve" | "escalate"
    output_schema: list[str] = field(default_factory=list)


@dataclass
class TeamPattern:
    """A reusable, named template for multi-agent collaboration."""
    pattern_id: str
    name: str
    description: str
    flow_type: FlowType
    slots: list[PatternSlot]
    waves: list[FlowWave]
    synthesis: SynthesisSpec
    tags: list[str] = field(default_factory=list)
    min_slots_required: int = 0
```

Design decision: patterns define `FlowWave` groups rather than arbitrary
`depends_on` DAGs. Waves are simpler, map directly to PMO visualization,
and cover all four collaboration shapes.

#### 3.3 The Four Collaboration Shapes

**Diverge-Converge.** N agents analyze the same problem independently in
wave 0. A synthesizer in wave 1 reads all outputs and produces a merged
analysis. Key framing for wave 0: _"Focus on what your perspective uniquely
reveals. Do not try to be comprehensive."_

**Relay.** Strict sequence across waves. Each wave has one slot. Wave N
receives full output of wave N-1. Framing emphasizes building on prior
work: _"The previous agent produced the following. Build on their work."_

**Challenge.** Three waves: wave 0 (proposer), wave 1 (challengers framed
as critics: _"Find weaknesses, risks, and unstated assumptions"_), wave 2
(proposer revises: _"Address each challenge. Note which you accepted and
which you rejected with justification."_).

**Panel.** Like diverge-converge but with a facilitator who produces
structured output with consensus, dissenting opinions, and open questions.
Panelist framing: _"Brief assessment, under 500 words."_

#### 3.4 Synthesis Agent Design

Synthesis is a **role**, not a separate agent type. Any agent can fill it.
The `PromptDispatcher` gains `build_synthesis_prompt()`:

1. **Role framing.** "You are synthesizing outputs from N agents."
2. **Prior outputs.** Each member's output in labeled sections.
3. **Detected conflicts.** Cases where agents disagreed (keyword heuristic).
4. **Output requirements.** From `SynthesisSpec.output_schema`, e.g.:
   "Your output MUST include: ## Severity, ## Root Cause, ## Recommendation."
5. **Conflict handling.** `"surface"` = note all disagreements. `"resolve"` =
   pick a winner. `"escalate"` = flag for human APPROVAL action.

The synthesis output becomes the canonical `StepResult.outcome`. Individual
member outputs are preserved in `member_results` for auditability.

#### 3.5 Pattern Library (Initial 4 Patterns)

**Bug Triage Panel** (`bug-triage-panel`)
```yaml
flow_type: panel
tags: [bug-fix, triage, incident]
slots:
  - slot_id: researcher     # capability: code-research
  - slot_id: security       # capability: security-review (optional)
  - slot_id: business       # capability: business-analysis
  - slot_id: facilitator    # capability: orchestration
waves:
  - wave_id: 0, slot_ids: [researcher, security, business]
  - wave_id: 1, slot_ids: [facilitator]
synthesis:
  synthesizer_slot_id: facilitator
  output_schema: [severity, root_cause, user_impact, recommendation, dissenting_views]
```

**Feature Design Review** (`feature-design-review`)
```yaml
flow_type: challenge
tags: [new-feature, design, architecture]
slots:
  - slot_id: proposer        # capability: architecture
  - slot_id: eng-challenger   # capability: backend-implementation
  - slot_id: test-challenger  # capability: test-engineering
waves:
  - wave_id: 0, slot_ids: [proposer]        # propose
  - wave_id: 1, slot_ids: [eng-challenger, test-challenger]  # challenge
  - wave_id: 2, slot_ids: [proposer]        # revise
synthesis:
  synthesizer_slot_id: proposer
  output_schema: [revised_design, changes_accepted, changes_rejected]
```

**Full-Stack Implementation** (`fullstack-implementation`)
```yaml
flow_type: diverge-converge
tags: [new-feature, full-stack]
slots:
  - slot_id: designer   # architecture
  - slot_id: backend     # backend-implementation
  - slot_id: frontend    # frontend-implementation (optional)
  - slot_id: tester      # test-engineering
  - slot_id: reviewer    # code-review
waves:
  - wave_id: 0, slot_ids: [designer]
  - wave_id: 1, slot_ids: [backend, frontend, tester]
  - wave_id: 2, slot_ids: [reviewer]
synthesis:
  synthesizer_slot_id: reviewer
  output_schema: [review_summary, issues_found, approval_status]
```

**Risk Assessment Panel** (`risk-assessment`)
```yaml
flow_type: panel
tags: [risk, assessment, deployment]
slots:
  - slot_id: security    # security-review
  - slot_id: business    # business-analysis
  - slot_id: ops         # devops
  - slot_id: facilitator # orchestration
waves:
  - wave_id: 0, slot_ids: [security, business, ops]
  - wave_id: 1, slot_ids: [facilitator]
synthesis:
  synthesizer_slot_id: facilitator
  output_schema: [risk_matrix, top_risks, mitigations, go_no_go_recommendation]
```

#### 3.6 Planner Integration

The `IntelligentPlanner` gains a `PatternRegistry` and a pattern-selection
step between task classification and phase generation:

1. Match `task_type` against `TeamPattern.tags`.
2. Filter to patterns whose required slots can be filled by available agents.
3. If multiple match, prefer highest `LearnedPattern.success_rate`, else
   fewest slots (simpler is better).
4. If none match, fall back to current single-agent behavior.

The selected pattern replaces `_consolidate_team_step`. `MachinePlan` gains
`team_pattern_id: str | None = None` to record which pattern was used.

#### 3.7 User Stories

**Bug triage: before vs. after.** _Before:_ Backend engineer investigates
and fixes a race condition bug. Two days later, security discovers it was
exploitable as a timing attack. A stakeholder asks why affected customer
workflows were not addressed. _After:_ Bug Triage Panel. Three agents
analyze concurrently: engineer traces root cause, security-reviewer
identifies attack vector (HIGH severity), SME maps to 3 customer workflows.
Facilitator synthesizes: severity CRITICAL, fix addresses all three angles.

**Architecture decision: before vs. after.** _Before:_ Architect recommends
microservices. Six months in, 2-person team drowns in operational overhead.
_After:_ Design Review pattern. Architect proposes microservices. Engineer
challenges: "2-person team can't maintain 6 services." Test engineer
challenges: "Integration test harness doesn't exist." Architect revises:
modular monolith with extraction triggers. The `changes_accepted` section
makes the reasoning chain auditable.

#### 3.8 Risks

| Risk | Mitigation |
|---|---|
| Token cost (~4x for panel) | Planner only selects patterns when warranted. BudgetTuner incorporates overhead. Panel framing caps panelist output at 500 words. |
| Synthesis loses sharp edges | Prompt requires "Dissenting Views" section. `conflict_handling` forces naming disagreements. Raw member outputs preserved. |
| Pattern sprawl | Start with 4 patterns. Min 2 slots required. Usage tracking flags unused patterns. |
| Slot resolution failure | Planner checks fillability before selection. Pattern either runs fully or not at all. |

**Files to create:**
- `agent_baton/models/team_pattern.py`
- `agent_baton/core/orchestration/pattern_registry.py`
- `agents/patterns/*.yaml` (4 pattern definitions)

**Files to modify:**
- `agent_baton/core/engine/dispatcher.py` — `build_synthesis_prompt()`
- `agent_baton/core/engine/executor.py` — wave-based dispatch, pattern-aware auto-complete
- `agent_baton/core/engine/planner.py` — pattern selection stage
- `agent_baton/models/execution.py` — `team_pattern_id` on PlanStep

---

### Phase 4: Daemon-Native Team Orchestration

#### 4.1 Team-Aware TaskWorker

When the `TaskWorker` detects team-member dispatches (step_id has letter
suffix like "1.1.a"), it enters a team orchestration sub-loop that processes
waves sequentially while dispatching members within each wave concurrently.

```python
async def _handle_team_dispatch(self, initial_actions):
    """Drive a team step through waves, context injection, and synthesis."""
    task_id = self._engine.status().get("task_id", "")
    parent_step_id = self._resolve_parent_step_id(initial_actions[0].step_id)
    completed_members: dict[str, LaunchResult] = {}
    wave_number = 0

    while True:
        if self._shutdown_event and self._shutdown_event.is_set():
            break

        action = self._engine.next_action()
        if action.action_type in (ActionType.COMPLETE, ActionType.FAILED,
                                   ActionType.GATE, ActionType.APPROVAL):
            return  # delegate back to outer loop

        if action.action_type == ActionType.WAIT:
            await asyncio.sleep(0.5)
            continue

        wave_actions = self._engine.next_actions() or [action]
        wave_number += 1

        # Publish wave start event
        self._bus.publish(evt.team_wave_started(
            task_id=task_id, step_id=parent_step_id,
            wave=wave_number, member_ids=[a.step_id for a in wave_actions],
        ))

        # Inter-wave context injection (Phase 2)
        if wave_number > 1 and completed_members:
            wave_actions = self._inject_wave_context(
                wave_actions, completed_members,
            )

        # Mark dispatched + publish member events
        for a in wave_actions:
            self._engine.mark_dispatched(a.step_id, a.agent_name)
            self._bus.publish(evt.team_member_dispatched(
                task_id=task_id, step_id=parent_step_id,
                member_id=a.step_id, agent_name=a.agent_name,
                wave=wave_number,
            ))

        # Dispatch wave concurrently
        steps = [{"agent_name": a.agent_name, "model": a.agent_model,
                  "prompt": a.delegation_prompt, "step_id": a.step_id}
                 for a in wave_actions]
        results = await self._scheduler.dispatch_batch(steps, self._launcher)

        # Record results per member
        for result in results:
            if isinstance(result, Exception):
                continue
            self._engine.record_team_member_result(
                step_id=parent_step_id, member_id=result.step_id,
                agent_name=result.agent_name, status=result.status,
                outcome=result.outcome, files_changed=result.files_changed,
            )
            if result.status == "complete":
                completed_members[result.step_id] = result
                self._bus.publish(evt.team_member_completed(...))
            else:
                self._bus.publish(evt.step_failed(...))
```

**Graceful shutdown during team steps**: When `_shutdown_event` fires
mid-wave, the current `dispatch_batch()` runs to completion. Completed
member results are recorded before exiting. On resume, recovery re-dispatches
only incomplete members.

#### 4.2 Crash Recovery for Team Steps

Today `recover_dispatched_steps()` removes all `StepResult` entries with
`status == "dispatched"`. For team steps this is destructive — a 5-member
team where 3 completed before crash would lose that work.

**Extended recovery**: Preserve completed `member_results` within the parent
`StepResult` and only reset the parent status to "pending" so
`_team_dispatch_action()` re-evaluates which members still need dispatch.

```python
def recover_dispatched_steps(self) -> int:
    """Clear stale markers with team-member granularity.

    Regular steps: remove the StepResult entirely (existing behavior).
    Team steps: preserve completed member_results, reset parent to
    pending so the engine re-dispatches only incomplete members.
    """
    for r in state.step_results:
        if r.status != "dispatched":
            keep.append(r)
            continue

        plan_step = self._find_step(state, r.step_id)
        is_team = plan_step and bool(plan_step.team)

        if is_team and r.member_results:
            completed = [m for m in r.member_results if m.status == "complete"]
            if completed:
                r.status = "pending"
                r.member_results = completed
                keep.append(r)
                recovered += 1
            else:
                recovered += 1  # no completed members, drop entirely
        else:
            recovered += 1  # regular step, drop
```

**State persistence guarantee**: The engine saves state after every
`record_team_member_result()` call. A crash loses at most one in-flight
member's work, not the entire wave.

#### 4.3 Real-Time Team Monitoring

**New events** (added to `core/events/events.py`):

| Event | Payload | Publisher |
|---|---|---|
| `team.wave_started` | step_id, wave, member_ids | TaskWorker |
| `team.member_dispatched` | step_id, member_id, agent_name, wave | TaskWorker |
| `team.member_completed` | step_id, member_id, agent_name, outcome | TaskWorker |
| `team.synthesis_started` | step_id, agent_name | TaskWorker |
| `team.synthesis_completed` | step_id, agent_name, outcome | TaskWorker |

**New API endpoint**:

```
GET /api/v1/executions/{task_id}/steps/{step_id}/team

Response:
{
  "step_id": "1.1",
  "is_team_step": true,
  "pattern": "diverge-converge",
  "waves": [
    { "wave": 1, "members": [
        {"member_id": "1.1.a", "agent_name": "backend-engineer",
         "status": "complete", "outcome": "..."},
        {"member_id": "1.1.b", "agent_name": "security-reviewer",
         "status": "complete"}
    ]},
    { "wave": 2, "members": [
        {"member_id": "1.1.c", "agent_name": "test-engineer",
         "status": "dispatched"}
    ]}
  ],
  "synthesis": {"agent_name": "architect", "status": "pending"}
}
```

SSE streams all team events alongside existing step events. PMO board
clients filter by `"team."` topic prefix.

#### 4.4 PMO Board Team Visualization

Team steps on the board expand to show:

1. **Wave progress bar** — horizontal segments per wave. Green/blue/gray/red.
2. **Member roster** — compact table: member_id, agent, role, status, duration.
3. **Decision log timeline** — structured entries from Phase 2's decision log.
4. **Synthesis banner** — when complete, becomes the card's primary summary.

#### 4.5 CLI Commands

```bash
# List available team patterns
baton team patterns

# Plan with explicit pattern
baton plan "task" --pattern diverge-converge --save

# Plan-then-execute in one command via daemon
baton daemon run "Implement OAuth2 login" \
  --team-pattern panel --max-parallel 4 --serve

# Status shows team member progress
baton daemon status --task-id abc123
# Task: abc123 | Status: running (step 1.1 — team)
# Pattern: diverge-converge
# Wave 1: 2/2 complete
#   1.1.a  backend-engineer   complete  (0:42)
#   1.1.b  security-reviewer  complete  (1:15)
# Wave 2: 0/1
#   1.1.c  test-engineer      dispatched
# Synthesis: pending (architect)
```

#### 4.6 End-to-End Story: Bug Triage Panel

A production bug arrives via PagerDuty webhook (daemon roadmap Phase 2).

1. **Trigger**: `POST /api/v1/triggers` receives the incident. Auto-triage
   creates a PMO signal and generates a plan using the `bug-triage-panel`
   pattern.

2. **Phase 1 — Triage (team step)**:
   - Wave 1: Three agents dispatch concurrently:
     - `backend-engineer` traces root cause: "Pagination cursor not
       URL-decoded, causing SQL injection → 500s."
     - `data-analyst` correlates: "Error spike at 14:32 UTC with deploy
       v2.4.1. 340 users affected."
     - `security-reviewer` flags: "SQL injection vulnerability. CVE
       assignment recommended."
   - Synthesis: `architect` produces unified diagnosis: "Root cause
     confirmed. Priority P0 (security). Fix: parameterized query."
   - APPROVAL gate: Diagnosis appears on PMO board. On-call reviews,
     clicks "Approve."

3. **Phase 2 — Fix (team step, diverge-converge)**:
   - Wave 1: `backend-engineer` fixes the query; `test-engineer` writes
     regression test. Both receive Phase 1 decisions via cross-phase
     context accumulation.
   - Synthesis: `code-reviewer` reviews combined diff. "Fix correct.
     Parameterized query eliminates injection. Test coverage adequate."
   - Gate: `pytest` auto-runs. Passes.

4. **Completion**: Card moves to "Done." Outbound webhook notifies Slack.
   Total elapsed: ~15 minutes from alert to tested, reviewed fix.

#### 4.7 Risks

| Risk | Mitigation |
|---|---|
| Wave deadlock (all members blocked) | Worker detects prolonged WAIT (10min timeout), fails the step. |
| Synthesis gets stale context after crash | Reads from persisted `member_results`, not in-memory state. |
| Resource exhaustion from large teams | `StepScheduler` semaphore already caps at `max_parallel`. |
| Concurrent `record_team_member_result()` calls | Worker awaits `dispatch_batch()` then records sequentially. |

**Files changed:**
- `core/runtime/worker.py` — `_handle_team_dispatch()`, `_dispatch_synthesis()`, `_inject_wave_context()`
- `core/engine/executor.py` — team-granular `recover_dispatched_steps()`
- `core/events/events.py` — 5 new event factory functions
- `api/routes/executions.py` — team status endpoint
- `cli/commands/execution/daemon.py` — `--team-pattern` flag, `run` subcommand
- `cli/commands/agents/team_cmd.py` — `baton team patterns`
- `pmo-ui/` — team card expansion, wave progress, SSE subscriptions

---

## Phase Summary

| Phase | Delivers | Depends On |
|---|---|---|
| **1: Team Execution Model** | Team profiles, dynamic composition, wave dispatch, synthesis steps | — |
| **2: Inter-Agent Context** | Decision log, context injection, cross-phase accumulation | Phase 1 |
| **3: Multi-Perspective Patterns** | 4 collaboration shapes, pattern library, planner integration | Phase 1 |
| **4: Daemon Integration** | Team-aware worker, crash recovery, monitoring, PMO visualization | Phase 1-3 |

Phases 2 and 3 can be developed in parallel after Phase 1. Phase 4
integrates everything into the production runtime. Phases 5-6 are
maturation phases that make teams smarter and more capable over time.

---

### Phase 5: Team Learning and Adaptation

#### 5.1 Problem Statement

Every team execution is stateless. The system doesn't learn that certain
agent combinations produce better results, that a particular pattern is
overkill for low-severity bugs, or that a specific synthesis agent
consistently loses signal. The `RetroEngine` and `PatternLearner` exist
but operate at the individual agent and task-type level — they don't
track team-level effectiveness.

#### 5.2 Team-Level Retrospectives

After a team step completes, the `RetroEngine` gains a
`record_team_retrospective()` method that captures:

```python
@dataclass
class TeamRetrospective:
    task_id: str
    step_id: str
    pattern_id: str | None          # which pattern was used
    team_composition: list[str]     # agent names in order
    flow_type: str                  # diverge-converge, panel, etc.

    # Quality metrics
    synthesis_quality: float        # 0-1, based on how much of the
                                    # synthesis was used vs. overridden
    conflict_count: int             # number of disagreements surfaced
    conflict_resolution_rate: float # how many were resolved vs. escalated
    human_override_count: int       # how many agent decisions the human changed

    # Efficiency metrics
    total_tokens: int
    total_duration_seconds: float
    tokens_per_member: dict[str, int]
    wave_count: int
    redundancy_score: float         # 0-1, how much output overlapped
                                    # between parallel members

    # Outcome metrics
    task_type: str
    task_complexity: str
    final_status: str               # complete, failed
    files_changed: int
    downstream_issues: int          # bugs found in code this team wrote
                                    # (populated by later retrospectives)
```

**Where data comes from:**
- `synthesis_quality`: When a human overrides a synthesis output (via
  `approve-with-feedback`), the override rate across executions measures
  synthesis reliability.
- `redundancy_score`: Text similarity between parallel members' outputs.
  High redundancy means the team is too homogeneous — the pattern isn't
  adding diverse perspectives.
- `downstream_issues`: Populated retroactively. When a bug-fix execution
  traces to code written by a prior team execution, the prior team's
  retrospective gets an `issues_found` increment.

#### 5.3 Team Composition Optimization

The `PatternLearner` gains team-aware pattern matching:

```python
class TeamPatternLearner:
    def recommend_pattern(
        self, task_type: str, complexity: str, agents_available: list[str],
    ) -> PatternRecommendation:
        """Recommend a pattern based on historical team retrospectives.

        Returns:
            PatternRecommendation with: pattern_id, confidence,
            recommended_agents (ordered), reasoning, historical_stats
        """
```

Recommendations are based on:
- **Success rate by pattern**: "Bug-triage-panel resolves 85% of HIGH
  severity bugs in one pass. Solo dispatch resolves 60%."
- **Agent pairing effectiveness**: "When architect + security-reviewer
  are paired, security issues found per execution is 2.3x higher than
  security-reviewer alone."
- **Redundancy avoidance**: "backend-engineer + backend-engineer--python
  on the same team produces 70% output overlap. Drop one."
- **Synthesis agent ranking**: "architect as synthesizer retains 90% of
  panelist insights. orchestrator retains 75%."

#### 5.4 Team Calibration for New Agents

When a new agent is created via `talent-builder`, the system has no
performance data for it. Team calibration addresses this:

1. **Shadow mode**: The new agent runs alongside an established agent
   on the same task. Both produce output, but only the established
   agent's output is used. The new agent's output is compared for
   quality.
2. **Graduated integration**: After N shadow runs with acceptable quality,
   the agent is promoted to active team participation.
3. **A/B team patterns**: For the same task type, alternate between
   teams with and without the new agent. Compare outcomes over time.

#### 5.5 User Stories

> *"The system recommends the bug-triage-panel for this P0 bug because
> historically, panels catch security issues 2.3x more often than solo
> dispatch for this task type."*

> *"I created a new cloud-cost-expert agent. The system runs it in shadow
> mode alongside the existing SME for 5 executions. Once it proves
> reliable, it replaces the SME in cost estimation teams."*

> *"The retrospective shows our security-reviewer + architect pairing
> has 90% synthesis quality but backend-engineer + architect drops to
> 65%. The planner stops pairing backend-engineer as synthesizer."*

#### 5.6 Key Files

| File | Change |
|---|---|
| `models/retrospective.py` | `TeamRetrospective` model |
| `core/improve/scoring.py` | Team-level scoring, redundancy detection |
| `core/learn/pattern_learner.py` | `TeamPatternLearner`, pairing effectiveness |
| `core/learn/team_calibration.py` | New: shadow mode, graduated integration |
| `core/engine/planner.py` | Consume team pattern recommendations |

---

### Phase 6: Advanced Team Journeys

#### 6.1 Problem Statement

Phases 1-5 build the machinery for team execution, context sharing,
patterns, runtime integration, and learning. Phase 6 addresses the
specific interactive workflows that require capabilities beyond
plan-then-execute: co-collaborative design, real-time analytical deep
dives, and multi-specialist estimation sessions.

These journeys share a characteristic: **the human does not know the full
plan upfront.** Each step's outcome shapes the next question. The system
needs to support conversational, iterative co-creation — not just
structured plan execution.

#### 6.2 Journey Support: Co-Collaborative Design

> A mock user and a developer walk through a browser-based UX test,
> discussing functionalities and feasibility in real time.

**Pattern**: Iterative Challenge (new pattern type)

```yaml
pattern_id: iterative-ux-review
name: Iterative UX Review
flow_type: challenge  # extended with iteration
tags: [ux, design, review]
iteration:
  max_cycles: 5
  continue_condition: "human signals 'done'"
slots:
  - slot_id: mock-user
    capability: business-analysis
    description: Evaluate UI from user persona perspective
  - slot_id: implementer
    capability: frontend-implementation
    description: Apply design changes
  - slot_id: advisor
    capability: architecture
    description: Flag feasibility concerns
    optional: true
waves_per_cycle:
  - wave_id: 0, slot_ids: [mock-user]       # evaluate current state
  - wave_id: 1, slot_ids: [human]           # human reviews + redirects
  - wave_id: 2, slot_ids: [implementer]     # apply changes
```

Each cycle produces a mini-retrospective: what changed, what the mock
user thought, what the human adjusted. After N cycles, the accumulated
decision log serves as the design specification.

**Requires**: `ActionType.INTERACT` from daemon Phase 6, human-as-member
from the same phase.

#### 6.3 Journey Support: Real-Time Analytical Deep Dive

> A business executive works with a data analyst and consultant live
> to explore data and build a dashboard.

**Two-mode execution**: The daemon supports a plan with an exploratory
phase followed by a build phase:

```
Phase 1: Exploration (iterative step, mode: iterative)
  Step 1.1 → data-analyst (iterative, human steers)
  Step 1.2 → data-scientist (iterative, joins when needed)
  Output: findings.md artifact

Phase 2: Dashboard Build (standard plan execution)
  Step 2.1 → visualization-expert (design)
  Step 2.2 → frontend-engineer (implement)
  Step 2.3 → data-analyst (backing queries)
  Gate: test
```

Phase 1 uses iterative steps — the human asks questions, agents
investigate, the human asks follow-ups. When the human signals "done,"
Phase 1 produces `findings.md` which becomes a knowledge attachment for
Phase 2.

**Requires**: Iterative step execution, MCP database access for agents
(daemon Phase 7).

#### 6.4 Journey Support: Multi-Specialist Estimation

> A financial analyst estimates TCO alongside a cloud hosting expert.

**Pattern**: Structured Relay with shared artifact

```yaml
pattern_id: collaborative-estimation
name: Collaborative Estimation
flow_type: relay
tags: [estimation, analysis, cost]
shared_artifact: tco-model.md
slots:
  - slot_id: architect
    capability: architecture
    description: Identify required cloud resources
  - slot_id: cost-expert
    capability: cloud-cost-analysis
    description: Price each resource, model alternatives
  - slot_id: financial-analyst
    capability: financial-modeling
    description: TCO framework, sensitivity analysis
waves:
  - wave_id: 0, slot_ids: [architect]
  - wave_id: 1, slot_ids: [cost-expert, financial-analyst]  # parallel
  - wave_id: 2, slot_ids: [financial-analyst]  # sensitivity analysis
```

The `shared_artifact` field means all agents read and write the same
file. Phase 2's decision log captures structural choices
("UUID primary keys," "3-year time horizon") and Phase 1's wave
dispatch ensures each wave builds on the prior one.

Iteration happens via `approve-with-feedback` at the end:
"Also model a GCP migration path" → amend adds another relay cycle.

**Requires**: Purpose-built agents via `talent-builder`
(`financial-analyst`, `cloud-cost-expert`). Shared artifact protocol
(new `context_files` write coordination).

#### 6.5 Additional Journeys Identified

**Incident response**: An on-call engineer works with agents during a
production outage. Agents pull logs, test hypotheses, and execute
diagnostics concurrently while the human steers investigation. This is
a real-time diverge-converge with sub-minute iteration cycles.

**Executive briefing preparation**: A chief of staff works with agents
to prepare a board presentation. Agents draft slides, the exec refines
messaging, agents update visualizations. Highly iterative, human-voice-
driven creative work.

**Adversarial security review**: Red-team agents try to break what
blue-team agents built. The conflict is the point — synthesis should
surface attack vectors, not resolve disagreements. Requires the conflict
escalation protocol from daemon Phase 7.

**Regulatory compliance review**: Legal agent and engineering agent
disagree about data handling. Resolution requires human judgment with
regulatory citations from both sides. Requires conflict escalation.

#### 6.6 Shared Artifact Protocol

Multiple agents writing to the same file need coordination. New protocol:

1. Each agent reads the artifact at dispatch time (via `context_files`)
2. Agent writes changes to the artifact (via normal file operations)
3. After dispatch, the worker diffs the artifact and records changes
   in the decision log as `type: "artifact-update"`
4. Next wave's agents receive the updated artifact automatically
   (they read it fresh on dispatch)
5. Merge conflicts: if two parallel agents modify the same file, the
   worker detects the conflict and either auto-merges (if changes are
   to different sections) or escalates to human via `CONFLICT_ESCALATION`

This is lighter than a full shared scratchpad — it uses the filesystem
as the coordination medium, which agents already interact with naturally.

#### 6.7 Key Files

| File | Change |
|---|---|
| `agents/patterns/iterative-ux-review.yaml` | New pattern |
| `agents/patterns/collaborative-estimation.yaml` | New pattern |
| `core/runtime/worker.py` | Shared artifact diff/merge, iterative cycles |
| `core/engine/dispatcher.py` | Artifact-aware prompt building |
| `cli/commands/execution/explore.py` | New: `baton explore` command |

---

## Phase Summary

| Phase | Delivers | Depends On |
|---|---|---|
| **1: Team Execution Model** | Team profiles, dynamic composition, wave dispatch, synthesis steps | — |
| **2: Inter-Agent Context** | Decision log, context injection, cross-phase accumulation | Phase 1 |
| **3: Multi-Perspective Patterns** | 4 collaboration shapes, pattern library, planner integration | Phase 1 |
| **4: Daemon Integration** | Team-aware worker, crash recovery, monitoring, PMO visualization | Phase 1-3 |
| **5: Team Learning** | Team retrospectives, composition optimization, agent calibration | Phase 4 |
| **6: Advanced Journeys** | Iterative patterns, analytical sessions, shared artifacts, journey support | Phase 4-5, Daemon Phase 6-7 |

Phases 2 and 3 can be developed in parallel after Phase 1. Phase 4
integrates everything. Phase 5 makes teams learn from experience.
Phase 6 builds on daemon Phase 6-7's iterative execution to support
the interactive journeys.
