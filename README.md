# Orchestrator Skill v2 — Multi-Agent Orchestration for Claude Code

A multi-agent orchestration system that intelligently decides what should be a
subagent (its own context window) vs a skill (inline procedure) vs a reference
document (shared knowledge). The result: fewer agents, less token waste, better
information fidelity, and safety governance built into the architecture.

---

## Design Philosophy

> **Core principle: Pay for context only when you need isolation.**

Every subagent costs a full 200K-token context window, startup latency, and
information loss when summarizing results back. These costs are justified only
when the work is substantial, independence matters, or the output would
overwhelm the caller's context.

Everything else — research procedures, stack detection, communication
templates, low-risk guardrails — runs inline as skills the orchestrator
executes in its own context. This keeps the orchestrator's planning detail at
full fidelity instead of reading summaries-of-summaries.

See `references/decision-framework.md` for the full framework, including the
five-test decision flowchart.

---

## Architecture

```
                    ┌─────────────────────────────┐
                    │        ORCHESTRATOR          │
                    │                              │
                    │  Inline skills:              │
                    │  • Research procedures       │
                    │  • Agent routing/mapping     │
                    │  • Comms protocols           │
                    │  • Low-risk guardrails       │
                    └──────────┬──────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │   AUDITOR    │ │     SME      │ │   TALENT     │
     │ (independent │ │  (domain     │ │   BUILDER    │
     │  veto power) │ │  judgment)   │ │ (creates new │
     └──────────────┘ └──────────────┘ │  capabilities│
                                        └──────────────┘
              ┌─────────────────────────────────┐
              │        SPECIALIST AGENTS         │
              │  Backend · Frontend · Architect  │
              │  DevOps · Testing · Data Eng     │
              │  Data Sci · Analyst · Viz Expert │
              │  Security · Code Review          │
              └─────────────────────────────────┘

     ┌─────────────────────────────────────────────┐
     │           REFERENCE DOCUMENTS                │
     │  (shared knowledge — read by multiple agents)│
     │                                              │
     │  decision-framework · research-procedures    │
     │  agent-routing · guardrail-presets            │
     │  comms-protocols                              │
     └─────────────────────────────────────────────┘
```

### What Changed from v1 (and Why)

**Removed as subagents, now inline skills:**

| Was | Now | Why |
|-----|-----|-----|
| `researcher` agent | Research procedures (inline) | Orchestrator is the consumer of research. Subagent meant findings were summarized and lost detail the orchestrator needs for planning. Fails Test 3 (caller needs full detail). |
| `talent-mapper` agent | Agent routing (inline) | Stack detection is a lookup procedure, not judgment. Reading `package.json` and matching a table doesn't need its own context window. Fails Test 1 (not substantial) and Test 4 (procedure, not judgment). |
| `comms-controller` agent | Comms protocols (inline) | Templates, mission log entries, and handoff briefs are fill-in-the-blank procedures. Adding a subagent created coordination overhead with zero reasoning benefit. Fails Test 1 and Test 4. |

**Restructured:**

| Agent | Change | Why |
|-------|--------|-----|
| `auditor` | Now dual: inline process (LOW risk) + subagent (MEDIUM+ risk) | Prevents the auditor from being either a bottleneck (reviewing trivial changes) or absent (skipped because "too heavy"). Low-risk tasks use guardrail presets inline; high-risk tasks get independent review. |
| `talent-builder` | Now applies decision framework before creating anything | Prevents subagent sprawl. New capabilities get triaged: subagent, skill, or reference doc — whichever tier fits. |

**Net result:** 19 agent files (down from 22), 5 reference documents added.
Token savings of ~3 context windows per task that previously spawned
researcher + mapper + comms-controller.

---

## File Structure

```
orchestrator-v2/
├── agents/                              ← Subagents (own context window)
│   ├── orchestrator.md                  ← PM — plans, coordinates, runs inline skills
│   ├── auditor.md                       ← Independent safety reviewer (veto power)
│   ├── talent-builder.md                ← Creates new agents/skills/references
│   ├── architect.md                     ← System design
│   ├── backend-engineer.md              ← Server-side (generic)
│   │   ├── backend-engineer--node.md
│   │   └── backend-engineer--python.md
│   ├── frontend-engineer.md             ← Client-side (generic)
│   │   ├── frontend-engineer--react.md
│   │   └── frontend-engineer--dotnet.md
│   ├── devops-engineer.md               ← Infrastructure
│   ├── test-engineer.md                 ← Testing
│   ├── data-engineer.md                 ← Schemas, pipelines, migrations
│   ├── data-scientist.md                ← ML, statistics, modeling
│   ├── data-analyst.md                  ← SQL, KPIs, business questions
│   ├── visualization-expert.md          ← Charts, dashboards
│   ├── subject-matter-expert.md         ← Business domain expertise
│   ├── security-reviewer.md             ← Security audit
│   └── code-reviewer.md                 ← Quality review
│
├── references/                          ← Skills & shared knowledge
│   ├── decision-framework.md            ← When to use subagent vs skill vs reference
│   ├── research-procedures.md           ← 4 research modes (orchestrator runs inline)
│   ├── agent-routing.md                 ← Stack detection + flavor matching
│   ├── guardrail-presets.md             ← Risk triage + standard guardrail configs
│   └── comms-protocols.md               ← Delegation, handoff, logging templates
│
├── install.sh                           ← Setup script
└── README.md
```

---

## The Decision Framework (Summary)

Full version in `references/decision-framework.md`.

**Five tests, applied in order:**

1. **Substantial independent work product?** Yes → subagent. No → next.
2. **Independence from caller needed?** Yes → subagent. No → next.
3. **Caller needs full detail?** Yes → skill (inline). No → subagent.
4. **Procedure or judgment?** Procedure → skill. Judgment → subagent.
5. **Used by multiple agents?** Yes → reference document. No → embed.

**Three tiers:**

| Tier | What | Cost | When |
|------|------|------|------|
| Subagent | Own context window, own prompt, own tools | High (200K tokens, latency, summary loss) | Substantial work, independence, or large output |
| Skill | Procedure the orchestrator runs inline | Low (stays in orchestrator's context) | Lookup, detection, templates, checklists |
| Reference | Shared doc multiple agents read | Minimal | Knowledge used by >1 agent |

---

## The Auditor's Dual Nature

The auditor is both a **process** and an **agent**:

**As a process** (LOW risk): The orchestrator applies guardrail presets from
`references/guardrail-presets.md` inline. Quick risk triage, standard
boundaries, no subagent overhead.

**As a subagent** (MEDIUM+ risk): Independent review with veto authority.
The auditor is a separate context window specifically so it can disagree with
the orchestrator's plan without being influenced by the planner's reasoning.

**Risk triage signals:**
- LOW: Simple code changes, no regulated data, read-only analysis
- MEDIUM: Multiple agents writing, Bash access, database changes
- HIGH: Infrastructure, production systems, security-sensitive
- CRITICAL: Regulatory-reportable data, schema migrations on production

---

## Agent Flavoring

Specialists have base (generic) and flavored (stack-specific) variants:

```
backend-engineer.md           ← Any backend
backend-engineer--node.md     ← Node.js / TypeScript patterns
backend-engineer--python.md   ← Python / FastAPI / Django patterns
```

The orchestrator detects the stack inline (using `references/agent-routing.md`)
and routes to the best flavor. If a needed flavor doesn't exist, the
`talent-builder` creates it — after verifying it justifies a full context
window per the decision framework.

---

## Setup

```bash
# Extract
tar xzf orchestrator-v2.tar.gz
cd orchestrator-v2

# Install (choose user-level or project-level)
chmod +x install.sh
./install.sh

# Verify
claude agents   # or /agents in a session
```

---

## Usage

### Automatic

Describe a complex task naturally:

> "Build a compliance tracking system — needs API endpoints, a dashboard,
> data validation against regulatory requirements, and tests."

The orchestrator activates, researches inline, consults the SME, triages
risk (CRITICAL → invokes auditor), maps agent flavors, and delegates.

### Direct Specialist Invocation

> "Use the data-analyst to investigate our resource utilization trends."

> "Use the security-reviewer to audit our authentication flow."

---

## Tips

- **The system self-expands.** First time on a Go project? The talent-builder
  creates `backend-engineer--go`. Next time, the routing table finds it.
- **Watch for subagent sprawl.** If you keep creating agents, periodically
  review the roster against the decision framework. Can any be downgraded?
- **The decision framework is the north star.** When in doubt about how to
  implement something, read `references/decision-framework.md`.
- **3-5 specialists per task.** More than that and coordination overhead
  outweighs benefits.
- **Token cost: ~4-7x** per subagent over single-agent work. Inline skills
  cost nothing extra. Design accordingly.
