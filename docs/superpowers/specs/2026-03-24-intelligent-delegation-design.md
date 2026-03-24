# Intelligent Delegation — Plan Quality Improvements

**Date:** 2026-03-24
**Status:** Design (rev 1)
**Story:** Baton plan generation produces "stuffy" delegation prompts that teach expert agents what they already know, insert irrelevant method guidance, and ignore agent definition metadata. This reduces plan quality and can guide subagents down wrong paths.

## Context

The `IntelligentPlanner` generates execution plans with delegation prompts assembled from hardcoded `_STEP_TEMPLATES`. These templates are moderately prescriptive — they tell agents HOW to work (e.g., "Define module boundaries, interfaces, and data flow") rather than WHAT to achieve. Expert agents with rich definitions (like `architect.md` which already specifies output format and principles) receive redundant instructions that waste context and can narrow their thinking.

Additionally, the planner never consults agent definitions during plan creation. It doesn't read agent model preferences (architect specifies `model: opus` but gets assigned `sonnet`), doesn't know what expertise agents already encode, and can't tailor prompts to complement rather than duplicate agent knowledge.

## Design Decisions

1. **Outcome-oriented templates** — Rewrite `_STEP_TEMPLATES` to describe achievements, not methods. The agent's own definition carries the expertise for HOW.
2. **Agent definition consultation** — The planner reads agent definitions via the existing registry to inherit model preferences and assess expertise richness.
3. **Prompt weight scaling** — Expert agents (rich definitions) get lighter templates. Agents without definitions get current-level detail as fallback.
4. **Task-specific context injection** — Auto-extract file paths from task summaries, add Intent and Success Criteria sections to delegation prompts.
5. **Structured deviation protocol** — Agents can signal plan misfit via a Deviation section in their outcome, feeding the retrospective learning loop.
6. **Backward compatibility** — No changes to CLI contract (`_print_action` format), execution flow, plan structure, or agent definitions.

## Tier 1: Outcome-Oriented Templates

### Current Problem

`_STEP_TEMPLATES` in `planner.py` (lines 118-258) contains ~50 agent+phase template entries. They are method-prescriptive:

```
# Current architect/design template:
"Design the architecture for: {task}. Define module boundaries, interfaces,
and data flow. Document key design decisions and identify affected components."
```

The architect agent definition already says: "Produce specific schemas, interface definitions, and file structure recommendations." The template duplicates this and constrains the agent to only those methods.

The backend-engineer template says "Focus on API endpoints, business logic, and data access" — but many tasks don't involve API endpoints.

### Change

Rewrite all entries in `_STEP_TEMPLATES` to be outcome-focused:

| Agent + Phase | Current | New |
|---------------|---------|-----|
| architect/design | "Design the architecture for: {task}. Define module boundaries, interfaces, and data flow. Document key design decisions and identify affected components." | "Produce a design for: {task} that the implementation team can build from without further clarification." |
| architect/research | "Research existing patterns and constraints for: {task}. Identify risks, dependencies, and technical trade-offs." | "Assess feasibility and constraints for: {task}. Surface anything that would change the implementation approach." |
| architect/review | "Review the implementation of: {task} for architectural consistency. Verify design adherence, module boundaries, and maintainability." | "Review: {task} for architectural fitness. Approve or flag structural issues." |
| backend-engineer/implement | "Implement the server-side components for: {task}. Write clean, tested code following project conventions. Focus on API endpoints, business logic, and data access." | "Implement: {task}. Deliver working, tested code." |
| backend-engineer/fix | "Diagnose and fix: {task}. Identify root cause, apply a targeted fix, and add a regression test." | "Fix: {task}. Include a regression test." |
| backend-engineer/design | "Design the backend approach for: {task}. Define endpoints, data models, and business logic flow." | "Design the backend approach for: {task}." |
| backend-engineer/investigate | "Investigate: {task}. Trace the issue through the codebase, identify root cause, and document findings with reproduction steps." | "Investigate: {task}. Document root cause and reproduction steps." |
| frontend-engineer/implement | "Implement the UI for: {task}. Create components, wire up state management, and handle user interactions. Ensure accessibility and responsive behavior." | "Implement the UI for: {task}. Deliver working, accessible components." |
| frontend-engineer/design | "Design the frontend approach for: {task}. Plan component hierarchy, state management, and user flow." | "Design the frontend approach for: {task}." |
| test-engineer/test | "Write comprehensive tests for: {task}. Cover happy paths, edge cases, error scenarios, and boundary conditions. Include both unit and integration tests where appropriate." | "Verify: {task}. Deliver tests that would catch regressions." |
| test-engineer/implement | "Build test infrastructure for: {task}. Create fixtures, helpers, and test utilities as needed." | "Build test infrastructure for: {task}." |
| test-engineer/review | "Review test coverage for: {task}. Identify untested code paths and missing edge cases." | "Review test coverage for: {task}. Flag gaps." |
| code-reviewer/review | "Review the implementation of: {task}. Check code quality, error handling, naming consistency, and adherence to project conventions. Flag security concerns." | "Review: {task}. Approve or flag issues blocking merge." |
| security-reviewer/review | "Security audit: {task}. Check for OWASP top 10 vulnerabilities, auth gaps, input validation issues, and secrets exposure." | "Security audit: {task}. Flag vulnerabilities and required fixes." |
| devops-engineer/implement | "Set up infrastructure for: {task}. Configure deployment, CI/CD pipelines, Docker, and environment as needed." | "Set up infrastructure for: {task}." |
| devops-engineer/review | "Review infrastructure changes for: {task}. Verify security, reliability, and operational readiness." | "Review infrastructure for: {task}. Flag operational risks." |
| data-engineer/design | "Design the data architecture for: {task}. Define schemas, migrations, indexes, and data flow." | "Design the data layer for: {task}." |
| data-engineer/implement | "Implement the data layer for: {task}. Create schemas, write migrations, optimize queries, and build ETL as needed." | "Implement the data layer for: {task}." |
| data-analyst/design | "Plan the analysis approach for: {task}. Define metrics, data sources, and query strategy." | "Plan the analysis for: {task}." |
| data-analyst/implement | "Execute the analysis for: {task}. Write queries, compute metrics, and prepare findings for stakeholders." | "Execute the analysis for: {task}. Deliver findings." |
| data-scientist/design | "Design the modeling approach for: {task}. Define features, model selection criteria, and evaluation methodology." | "Design the modeling approach for: {task}." |
| data-scientist/implement | "Build and evaluate models for: {task}. Implement feature engineering, model training, and evaluation pipeline." | "Build and evaluate models for: {task}." |
| auditor/review | "Audit the implementation of: {task}. Verify compliance, safety, and governance requirements. Flag risks and provide pass/fail determination." | "Audit: {task}. Provide pass/fail with findings." |
| visualization-expert/implement | "Create visualizations for: {task}. Design clear, accurate charts or dashboards that communicate insights effectively." | "Create visualizations for: {task}." |
| subject-matter-expert/research | "Provide domain expertise for: {task}. Document applicable business rules, regulatory requirements, and industry standards." | "Provide domain context for: {task}." |
| subject-matter-expert/review | "Validate domain correctness of: {task}. Verify business logic, terminology, and compliance with industry standards." | "Validate domain correctness of: {task}." |

### Principle

Tell agents WHAT to achieve, not HOW to do their job. They are experts — that's why they were selected. The agent definition carries the expertise; the plan carries the mission.

### `_AGENT_DELIVERABLES` Update

Simplify default deliverables to match outcome focus:

| Agent | Current | New |
|-------|---------|-----|
| architect | "Design document with module boundaries and interfaces" | "Design document" |
| backend-engineer | "Implementation source files", "Tests for changed code" | "Working implementation with tests" |
| frontend-engineer | "UI component files", "Tests for changed code" | "Working UI components with tests" |
| test-engineer | "Test files with comprehensive coverage" | "Test suite" |
| code-reviewer | "Review summary with findings and approval status" | "Review verdict with findings" |
| security-reviewer | "Security audit report with findings and risk ratings" | "Security audit report" |
| devops-engineer | "Infrastructure and deployment configuration files" | "Infrastructure configuration" |
| data-engineer | "Schema definitions and migration files" | "Schema and migrations" |
| data-analyst | "Analysis results and supporting queries" | "Analysis results" |
| data-scientist | "Model artifacts and evaluation results" | "Model with evaluation results" |
| auditor | "Audit report with compliance findings" | "Audit verdict" |
| visualization-expert | "Visualization files or dashboard components" | "Visualizations" |
| subject-matter-expert | "Domain requirements document" | "Domain context document" |

## Tier 2: Agent-Aware Planning

### Current Problem

`IntelligentPlanner` has access to the `AgentRegistry` (via `self._registry`) but never calls `registry.get(agent_name)` during plan creation:

1. **Model selection ignores definitions** — The architect definition specifies `model: opus` but the planner assigns `model: sonnet` to all steps via the default in `PlanStep`.
2. **No expertise awareness** — The planner doesn't know what agents already know, so it can't adjust prompt detail.

### Change 1: Model Inheritance

After agent assignment in `_assign_agents_to_phases()`, look up each agent's definition and inherit its model preference:

```python
# In _assign_agents_to_phases, after creating PlanStep:
agent_def = self._registry.get(agent)
if agent_def and agent_def.model:
    step.model = agent_def.model
```

Fallback: if the agent has no definition or no model specified, keep the existing default (`"sonnet"`).

### Change 2: Prompt Weight Scaling

Add a method `_agent_expertise_level()` that checks how rich an agent's definition is:

```python
def _agent_expertise_level(self, agent_name: str) -> str:
    """Assess agent expertise from definition richness.

    Returns: "expert" (rich definition, >200 words),
             "standard" (has definition),
             "minimal" (no definition found)
    """
    agent_def = self._registry.get(agent_name)
    if agent_def is None:
        return "minimal"
    word_count = len(agent_def.instructions.split())
    return "expert" if word_count > 200 else "standard"
```

In `_step_description()`, use this to scale template detail:

- **expert**: Use just the outcome phrase (e.g., "Implement: {task}.")
- **standard**: Use the full outcome template (current new templates)
- **minimal**: Use the full outcome template plus one line of method hints (light version of current templates)

This ensures agents without definitions still get enough guidance while expert agents aren't over-instructed.

### Change 3: Deliverables Deduplication

If an agent definition contains an "Output Format" or "When you finish" section, skip adding `_AGENT_DELIVERABLES` defaults — the agent already knows what to produce.

Detection: check if `agent_def.instructions` contains any of: "output format", "when you finish", "return:", "deliverables".

## Tier 3: Context Richness

### Current Problem

The delegation prompt (`dispatcher.py`) is structurally rigid:
- Every agent gets the same sections regardless of need
- Shared context is metadata-heavy (risk level, budget tier) but intent-light
- File paths mentioned in the task summary aren't auto-added to `context_files`

### Change 1: File Path Extraction

Add `_extract_file_paths()` to the planner that scans the task summary for file-like patterns and adds them to every step's `context_files`:

```python
import re

def _extract_file_paths(self, text: str) -> list[str]:
    """Extract file paths from task summary text."""
    # Match patterns like: path/to/file.ext, docs/spec.md, src/module/
    pattern = r'(?:^|[\s(])([a-zA-Z0-9_./-]+(?:\.[a-zA-Z0-9]+|/))'
    candidates = re.findall(pattern, text)
    # Filter to likely file paths (must have / or extension)
    return [c for c in candidates if '/' in c or '.' in c.split('/')[-1]]
```

Applied in `create_plan()` after building phases — append extracted paths to each step's `context_files` (deduplicated).

### Change 2: Intent Section in Delegation Prompt

Add `## Intent` to the delegation prompt (in `dispatcher.py`), placed before `## Your Task`:

```markdown
## Intent
{task_summary}
```

This forwards the user's original description unmodified — no template wrapping, no method insertion. The agent sees exactly what the user asked for.

### Change 3: Success Criteria Section

Add `## Success Criteria` to the delegation prompt, derived from task type:

```python
_SUCCESS_CRITERIA: dict[str, str] = {
    "bug-fix": "The bug no longer reproduces and a regression test prevents recurrence.",
    "new-feature": "The feature works as specified and has test coverage.",
    "refactor": "Behavior is unchanged, code is cleaner, and tests still pass.",
    "test": "Test coverage meaningfully improved with no false positives.",
    "documentation": "Documentation is accurate, complete, and matches current code.",
    "migration": "Data is migrated correctly with rollback capability verified.",
    "data-analysis": "Analysis answers the stated question with supporting evidence.",
}
```

The task type is already inferred by the planner and stored in shared context. The dispatcher reads it from shared context to select the right criteria.

### Updated Delegation Prompt Structure

```markdown
You are a {role} working on {project}.

## Shared Context
{shared_context}

Read `CLAUDE.md` for project conventions.

## Intent
{task_summary — user's original words, unmodified}

## Your Task (Step {step_id})
{task_description — outcome-oriented template}

## Success Criteria
{derived from task type}

## Files to Read
{context_files — now includes auto-extracted paths}

## Deliverables
{deliverables — skipped if agent definition has output format}

## Boundaries
- Write to: {allowed_paths}
- Do NOT write to: {blocked_paths}

## Previous Step Output
{handoff from prior step}

## Decision Logging
When you make a non-obvious decision, document it in your output
under a 'Decisions' heading explaining why you chose this approach.

## Deviations
If the plan's approach doesn't fit the actual situation, document what
you changed and why under a 'Deviations' heading. This feeds the
learning loop — deviations improve future plans.
```

## Tier 4: Agent Pushback Protocol

### Current Problem

Agents can only report success or failure. There's no structured way to signal "the plan told me to do X but Y was actually needed." Plan quality feedback is lost.

### Change 1: Deviations Field on StepResult

Add to `StepResult` in `models/execution.py`:

```python
@dataclass
class StepResult:
    # ... existing fields ...
    deviations: list[str] = field(default_factory=list)
```

### Change 2: Deviation Extraction in Executor

When the orchestrator records a step outcome via `baton execute record --outcome "..."`, the executor scans the outcome text for a `## Deviations` or `## Deviation` section header and extracts the content into the `deviations` list.

```python
def _extract_deviations(self, outcome: str) -> list[str]:
    """Extract deviation notes from agent outcome text."""
    lines = outcome.split('\n')
    in_deviation = False
    current = []
    deviations = []
    for line in lines:
        if re.match(r'^#{1,3}\s+[Dd]eviation', line):
            if current:
                deviations.append('\n'.join(current).strip())
                current = []
            in_deviation = True
            continue
        if in_deviation:
            if re.match(r'^#{1,3}\s+', line) and not re.match(r'^#{1,3}\s+[Dd]eviation', line):
                deviations.append('\n'.join(current).strip())
                current = []
                in_deviation = False
            else:
                current.append(line)
    if in_deviation and current:
        deviations.append('\n'.join(current).strip())
    return [d for d in deviations if d]
```

### Change 3: Deviation Section in Delegation Prompt

Already shown in the updated prompt structure above. The instruction reads:

```markdown
## Deviations
If the plan's approach doesn't fit the actual situation, document what
you changed and why under a 'Deviations' heading. This feeds the
learning loop — deviations improve future plans.
```

### Change 4: Retrospective Integration

In `RetrospectiveEngine.generate_from_usage()`, when step results contain deviations, include them as `sequencing_notes` in the `RetrospectiveFeedback`:

```python
for result in step_results:
    if result.deviations:
        for dev in result.deviations:
            feedback.sequencing_notes.append(
                SequencingNote(
                    description=f"Agent {result.agent_name} deviated: {dev}",
                    source_task=task_id,
                )
            )
```

This closes the feedback loop: deviations from this execution inform the planner's decisions on future tasks.

## Files Changed

| File | Tier | Change |
|------|------|--------|
| `agent_baton/core/engine/planner.py` | 1, 2, 3 | Rewrite templates, add agent-aware model/prompt scaling, add file path extraction |
| `agent_baton/core/engine/dispatcher.py` | 3, 4 | Add Intent, Success Criteria, Deviations sections to delegation prompt |
| `agent_baton/models/execution.py` | 4 | Add `deviations` field to `StepResult` |
| `agent_baton/core/engine/executor.py` | 4 | Add `_extract_deviations()`, wire into step recording |
| `agent_baton/core/observe/retrospective.py` | 4 | Ingest deviations into retrospective feedback |
| `tests/test_engine_planner.py` | all | Update template assertions, add model inheritance + richness tests |
| `tests/test_dispatcher.py` | 3, 4 | Add tests for new prompt sections |

## What Doesn't Change

- Plan structure (MachinePlan, PlanPhase, PlanStep, PlanGate) — unchanged
- CLI contract (`_print_action` format) — unchanged
- Execution flow (DISPATCH/GATE/COMPLETE cycle) — unchanged
- Agent definitions — unchanged (they're already good)
- `baton execute record` CLI interface — backward compatible (deviations extracted from outcome text, no new flags needed)

## Scope Boundaries

**In scope:**
- Template rewrites in planner.py
- Agent definition consultation for model + richness
- Delegation prompt enhancements (Intent, Success Criteria, Deviations)
- Deviation extraction and retrospective integration
- Test updates

**Out of scope:**
- Changes to agent definitions themselves
- Changes to CLI command interface
- Changes to plan structure (phases, steps, gates)
- Changes to execution flow
- New CLI flags for deviations (extracted from outcome text automatically)
