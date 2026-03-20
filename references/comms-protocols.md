# Communication Protocols & Templates

The orchestrator manages cross-agent communication as part of its workflow.
No subagent needed — these are templates and procedures, not reasoning tasks.

---

## Shared Context Document

Create at `.claude/team-context/context.md` before dispatching agents.
Every delegation prompt should include: "Read `.claude/team-context/context.md`
for shared project context before starting."

**Template:**
```markdown
# Team Context — [Task Description]

## Project
[One paragraph from research phase]

## Stack
[Language, framework, ORM, key dependencies with versions]

## Architecture
[Directory structure, patterns, data flow]

## Conventions
[Naming, file org, coding style — extracted during research]

## Domain Context
[Business rules from SME, if applicable. Skip if not a domain task.]

## Guardrails
[From auditor review or inline risk triage. Per-agent boundaries.]

## Agent Assignments
| Agent | Scope | Writes To | Blocked From | Depends On |
|-------|-------|-----------|--------------|------------|
| ... | ... | ... | ... | ... |
```

## Mission Log

Create at `.claude/team-context/mission-log.md`. Update after each agent
completes.

**Template:**
```markdown
# Mission Log — [Task Description]

Started: [timestamp]
Risk level: [from audit]

---

### [Agent Name] — [COMPLETE | FAILED | PARTIAL]
Assignment: [one-line summary of delegation]
Result: [2-3 sentence summary of what was done]
Files: [paths created/modified]
Decisions: [key choices made]
Issues: [problems, blockers, questions raised]
Handoff: [what the next agent needs from this output]

---
```

## Delegation Prompt Template

Use this structure for every agent delegation:

```
You are a [role] working on [project description].

SHARED CONTEXT: Read .claude/team-context/context.md

TASK: [Specific, concrete assignment]

CONTEXT:
- [Relevant files to read]
- [Patterns to follow]
- [Output from prior agents this one depends on]

DOMAIN CONTEXT: [If applicable — key business rules from SME]

BOUNDARIES:
- ALLOWED: [file paths this agent may write]
- BLOCKED: [file paths off-limits]

ACCEPTANCE CRITERIA:
- [How to know the work is done correctly]

RETURN: [Summary of files changed, decisions, integration notes, open questions]

DECISION LOGGING: When you make a non-obvious decision (choosing between
approaches, deviating from patterns, making assumptions), document it in a
"Decisions" section of your output: Chose / Over / Because / Risk.

CAPABILITIES CHECK: Before starting work, assess whether you can complete
the full task. If any part is outside your expertise or requires tools/access
you don't have, report it immediately in a "Capabilities Assessment" section:
  Can do: [list what you can handle]
  Cannot do: [list what's outside your scope and why]
  Suggestion: [which agent or approach would handle the gap]
Do NOT attempt work you've flagged as outside your capabilities.
```

## Handoff Briefs

When Agent A's output feeds into Agent B, prepare a summary — don't pass
raw output. This keeps Agent B's context clean.

**Template:**
```
HANDOFF FROM [Agent A]:
What was done: [2-3 sentences]
What you need: [specific details relevant to your task]
Files to reference: [paths for full detail if needed]
Constraints from this work: [e.g., "API returns this shape, consume these fields"]
```

## Information Classification

| Level | Description | Handling |
|-------|-------------|----------|
| SHARED | Stack, conventions, architecture | Shared context doc |
| DIRECTED | Agent A's output for Agent B | Included in delegation prompt as handoff |
| RESTRICTED | Credentials, PII, security findings | Reference by env var or file path only — never in prompts |
| INTERNAL | Agent working notes, intermediate output | Stays in agent's context — only summary returned |

**Hard rules:**
- Credentials and secrets are NEVER included in delegation prompts or shared
  context. Reference by environment variable name only.
- Security findings from the security-reviewer go only to the orchestrator
  and auditor — never broadcast to all agents.
- Agent outputs are always summarized before handoff. Raw Bash output, full
  file contents, and verbose logs stay INTERNAL.

## Completion Report Template

Produce after all agents finish and auditor signs off:

```markdown
## Completion Report — [Task]

### Summary
[What was built/changed in 2-3 sentences]

### Files Modified
| File | Action | Agent | Purpose |
|------|--------|-------|---------|
| ... | Created/Modified | ... | ... |

### Key Decisions
1. [Decision]: [rationale] — by [agent]

### Audit Status
[Summary from auditor — SHIP / SHIP WITH NOTES / REVISE]

### Known Limitations
[What wasn't done, needs follow-up]
```

## Decision Journal

Agents should document non-obvious decisions in their output. This creates
an audit trail of *why* choices were made, not just *what* was built.

### When to Log a Decision

- Choosing between two valid approaches
- Deviating from a common pattern for a specific reason
- Selecting a library, tool, or algorithm over alternatives
- Making assumptions about ambiguous requirements
- Choosing NOT to do something (e.g., skipping a feature, not adding a test)

### Decision Entry Format

Include a "Decisions" section in agent output:

```
## Decisions

### [Decision Title]
**Chose:** [What was chosen]
**Over:** [What was rejected]
**Because:** [Concrete reason — not "it seemed better"]
**Risk:** [What could go wrong with this choice]
```

### Orchestrator Logging

The orchestrator captures agent decisions in the mission log entry under
the `Decisions` field. Over time, this creates a searchable history of
technical choices and their rationale across tasks.

## Adaptive Task Decomposition

When an agent reports it cannot complete part of its assignment, the
orchestrator dynamically adjusts the plan rather than accepting a failure.

### Capabilities Assessment Protocol

Every delegation prompt includes a CAPABILITIES CHECK instruction. Agents
that can't complete the full task return a structured assessment:

```
## Capabilities Assessment

### Can Do
- [specific deliverables this agent can produce]

### Cannot Do
- [specific part]: [reason — missing tool, wrong domain, needs access]

### Suggestion
- [which agent should handle the gap, or what knowledge pack is needed]
```

### Orchestrator Response to Partial Capability

When the orchestrator receives a capabilities assessment:

1. **Accept the capable portion.** Let the agent proceed with what it can do.
2. **Route the gap.** Check the agent roster for a better match:
   - Another existing specialist → delegate the gap portion
   - No specialist exists → call talent-builder to create one (if justified
     by the decision framework), or handle inline if the gap is small
3. **Update the plan.** Add a new step for the routed gap. Update
   dependencies so the gap-filler's output feeds back into the workflow.
4. **Log in mission log.** Record the decomposition: what was split, why,
   and to whom.

### When to Expect Capabilities Assessments

Most common triggers:
- Agent encounters a different tech stack than expected
- Task requires domain knowledge the agent doesn't have
- Task requires tools the agent wasn't granted (e.g., Bash, Write)
- Task spans two domains (e.g., backend + frontend in one step)
