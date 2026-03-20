# Document Generation Pipeline

A phased workflow for generating complex documents: specifications, ADRs,
runbooks, compliance docs, and reports. The orchestrator runs this inline
when the task is "write a document" rather than "build a feature."

---

## When to Use

- User asks for a spec, ADR, runbook, compliance doc, or technical document
- Task output is a document, not code
- Document requires research, domain context, or multi-source synthesis

## Execution Phases

### Phase 1: Research & Context Gathering
**Mode:** Orchestrator inline (no subagent)
**Steps:**
1. Identify document type and audience
2. Run codebase research (research-procedures.md)
3. Identify domain context needs — delegate to SME if regulated
4. Gather source material (code, configs, existing docs, git history)

**Gate:** Research is complete — sources identified, audience clear

### Phase 2: Structure & Outline
**Mode:** Orchestrator inline or architect subagent (for complex docs)
**Steps:**
1. Select document template (see templates below)
2. Create outline with section headings
3. Map source material to sections
4. Identify gaps — what's missing?

**Gate:** Outline reviewed, gaps addressed or flagged

### Phase 3: Draft
**Mode:** Specialist subagent based on document type
**Agent selection:**

| Document Type | Agent | Why |
|---------------|-------|-----|
| Technical spec / ADR | architect | Design reasoning |
| API documentation | backend-engineer (flavored) | Knows the endpoints |
| Runbook / operations | devops-engineer | Operational knowledge |
| Compliance document | subject-matter-expert | Domain rules |
| Data analysis report | data-analyst | Analytical narrative |
| Test plan | test-engineer | Testing strategy |
| General / mixed | orchestrator inline | No specialist needed |

**Delegation includes:**
- Document outline from Phase 2
- Source material references
- Audience and tone guidance
- Decision logging instruction

**Gate:** Draft complete, all sections filled, no TODOs

### Phase 4: Review & Polish
**Mode:** code-reviewer subagent (or SME for regulated docs)
**Steps:**
1. Review for accuracy (facts match source material)
2. Review for completeness (all sections addressed)
3. Review for audience fit (right level of detail)
4. Review for consistency (terminology, formatting)

**Gate:** Reviewer verdict: SHIP / SHIP WITH NOTES / REVISE

### Phase 5: Finalize
**Mode:** Orchestrator inline
**Steps:**
1. Apply reviewer feedback
2. Add metadata (author, date, version, status)
3. Write to target path
4. Log in mission log

---

## Document Templates

### ADR (Architecture Decision Record)

```
# ADR-[NUMBER]: [TITLE]

**Status:** [Proposed | Accepted | Deprecated | Superseded]
**Date:** [YYYY-MM-DD]
**Decision makers:** [who was involved]

## Context
[What is the issue that motivates this decision?]

## Decision
[What is the change that we're proposing and/or doing?]

## Consequences
[What becomes easier or harder because of this change?]

## Alternatives Considered
[What other options were evaluated and why they were rejected?]
```

### Technical Specification

```
# [Feature/System Name] — Technical Specification

**Version:** [X.Y]
**Author:** [agent or user]
**Status:** [Draft | Review | Approved]
**Date:** [YYYY-MM-DD]

## Overview
[What this is and why it exists — 2-3 sentences]

## Requirements
[Numbered list of requirements]

## Design
[Architecture, data model, API contracts, sequence diagrams]

## Implementation Plan
[Phased approach with milestones]

## Testing Strategy
[How this will be tested]

## Risks & Mitigations
[What could go wrong]

## Open Questions
[Unresolved items]
```

### Runbook

```
# Runbook: [Process Name]

**Last updated:** [YYYY-MM-DD]
**Owner:** [team/person]
**Trigger:** [When to use this runbook]

## Prerequisites
[What must be true before starting]

## Steps
### Step 1: [Name]
[Detailed instructions with exact commands]
**Expected output:** [what you should see]
**If it fails:** [troubleshooting]

### Step 2: [Name]
...

## Rollback
[How to undo if things go wrong]

## Verification
[How to confirm the process completed successfully]
```

---

## Budget & Token Considerations

Document generation is typically **Lean** (1-2 agents):
- Most docs: orchestrator inline research + 1 specialist for draft
- Complex docs: + architect for outline + reviewer for polish (Standard tier)
- Regulated docs: + SME + auditor (Standard tier)

The orchestrator should not default to Full tier for documents —
documents are sequential by nature, not parallelizable.
