---
quadrant: reference
audience: agents, maintainers
see-also:
  - [orchestrator-usage.md](orchestrator-usage.md)
  - [../references/agent-routing.md](../references/agent-routing.md)
  - [cli-reference.md](cli-reference.md#baton-agents)
---

# Agent roster

This page mirrors `agents/*.md` — the distributable agent definitions installed by `scripts/install.sh`. There are **33** agents. The orchestrator picks among them based on task domain, risk tier, and budget. To dispatch one directly inside Claude Code, name it in the `Agent` tool with `subagent_type`.

To inspect runtime registration: `baton agents`.

For routing rules: [`references/agent-routing.md`](../references/agent-routing.md).

## How agents are invoked

Agents do not run on their own. They run when:

1. The **orchestrator agent** dispatches them as part of a baton-driven plan, OR
2. A user (or another agent) names them via Claude Code's `Agent` tool with a `subagent_type` parameter.

Each agent file in `agents/` contains a YAML frontmatter block (`name`, `description`, `model`, `tools`) and a body prompt. The frontmatter is what the runtime registers; the body is what the agent reads when dispatched.

## Orchestration & routing

| Agent | Model | Use when |
|-------|-------|----------|
| `orchestrator` | opus | Complex cross-cutting tasks, multi-domain refactors, batches of related tasks. Drives `baton plan` → `baton execute`. |
| `team-lead` | sonnet | Coordinator for parallel sub-teams within a phase. |
| `task-runner` | haiku | Procedural execution of pre-scripted shell/HTTP/data-format tasks. No architectural judgment. |

## Implementation specialists

| Agent | Model | Use when |
|-------|-------|----------|
| `backend-engineer` | sonnet | Server-side: APIs, business logic, ORM, middleware, server config. |
| `backend-engineer--node` | sonnet | Node.js/TypeScript backend specifics (Express, Fastify, NestJS, Prisma, Drizzle). |
| `backend-engineer--python` | sonnet | Python backend specifics (FastAPI, Django, Flask, SQLAlchemy, Alembic, Pydantic, Poetry/uv). |
| `frontend-engineer` | sonnet | Client-side: components, styling, state, routing, forms, accessibility. |
| `frontend-engineer--react` | sonnet | React/Next.js: Server Components, Suspense, App Router, Zustand/Redux Toolkit/Jotai. |
| `frontend-engineer--dotnet` | sonnet | Blazor, Razor Pages, ASP.NET MVC views; SignalR. |
| `data-engineer` | sonnet | Schemas, migrations, query optimization, ETL, data modeling. |
| `devops-engineer` | sonnet | CI/CD, Docker, deployments, environment config, build optimization. |
| `architect` | opus | Data-model design, API contracts, technology selection, module boundaries, second-opinion reviews. |

## Review & quality

| Agent | Model | Use when |
|-------|-------|----------|
| `code-reviewer` | sonnet | Final pass for readability, performance, error handling, project conventions. |
| `security-reviewer` | opus | Auth flows, input validation, secrets management, OWASP top 10, dependency vulns. |
| `auditor` | opus | Independent safety/compliance/governance review. Has veto authority. Required for MEDIUM+ risk plans. |
| `test-engineer` | sonnet | Unit, integration, and E2E tests. Test infrastructure. |

## Data & analysis

| Agent | Model | Use when |
|-------|-------|----------|
| `data-analyst` | sonnet | Business intelligence, reporting, SQL queries, KPI definition, dashboard design. |
| `data-scientist` | sonnet | Statistical analysis, ML modeling, experiment design, model evaluation. |
| `visualization-expert` | sonnet | Chart design, dashboard layout, visual storytelling. |

## Domain & governance

| Agent | Model | Use when |
|-------|-------|----------|
| `subject-matter-expert` | opus | Industry-specific business rules, regulatory compliance (SOX, GDPR, HIPAA), business processes. |
| `learning-analyst` | sonnet | Reads execution history + scorecards; proposes evidence-backed agent/config improvements. |
| `system-maintainer` | sonnet | Post-cycle config tuning. Mutates `learned-overrides.json` only — never source code. |
| `talent-builder` | opus | Researches a domain, creates a new specialist agent + knowledge pack + skills. |

## Resilience subsystem (self-heal)

| Agent | Model | Use when |
|-------|-------|----------|
| `self-heal-haiku` | haiku | Fast triage of trivial test failures, lint errors, formatting drift. |
| `self-heal-sonnet` | sonnet | Mid-tier auto-fix when haiku declines. |
| `self-heal-opus` | opus | Deepest fix tier; invoked only when sonnet escalates. |

## Resilience subsystem (immune)

| Agent | Model | Use when |
|-------|-------|----------|
| `immune-deprecated-api` | sonnet | Sweeps for usage of deprecated APIs flagged by upstream. |
| `immune-doc-drift` | sonnet | Detects when docs disagree with source. |
| `immune-stale-comment` | haiku | Finds stale comments and TODOs that no longer match the code. |
| `immune-todo-rot` | haiku | TODO/FIXME age detection and triage. |
| `immune-untested-edges` | sonnet | Identifies code paths missing test coverage. |

## Speculation & swarm

| Agent | Model | Use when |
|-------|-------|----------|
| `speculative-drafter` | haiku | Pre-computes likely next-step drafts in the background while the user is reading. |
| `swarm-reconciler` | sonnet | Merges parallel work from independent swarm agents into a coherent change. |

---

For the routing logic that picks among these agents, see [`references/agent-routing.md`](../references/agent-routing.md). For risk-tier guardrails, see [`references/guardrail-presets.md`](../references/guardrail-presets.md). For each agent's full prompt, read the matching file in `agents/<name>.md`.
