---
quadrant: explanation
audience: users, maintainers
see-also:
  - [../pillars.md](../pillars.md)
  - [../agent-roster.md](../agent-roster.md)
---

# Pillar 2 — Compose the Right Team

!!! abstract "Pillar context"
    One of [the four pillars](../pillars.md) — the differentiator: bespoke specialists over overloaded generalists.

> **In one line:** the right specialists for *this* problem, each with a clean, focused context window.

---

## The vision

The ideal is a system that auto-composes a purpose-built fleet for every task:
zero context rot, a specialist for every domain, no one agent dragging the
weight of earlier phases into a new one.

A single generalist agent on a complex codebase suffers **context rot**: by the
time it reaches step four it is carrying the full history of the previous three
steps plus however much of the codebase it was given on entry. Reasoning quality
degrades, token cost compounds, and errors from earlier steps bleed forward
unchecked.

The answer is narrowly-scoped specialists. Instead of one agent asked to plan,
implement backend logic, write tests, and review security in a single session,
Baton assembles an ad-hoc fleet where each member:

- Receives a **single, bounded task** — one domain, one phase.
- Starts from a **clean context window** loaded only with the knowledge
  relevant to that task.
- Is **purpose-built for its stack**: a Python-FastAPI project gets
  `backend-engineer--python`, not a generic backend agent that must infer
  Python idioms from scratch.

When a gap in the roster is discovered — no specialist exists for the
combination of role and domain needed — `talent-builder` fills it: it researches
the domain, creates the agent file, builds a knowledge pack, and optionally
scaffolds a repeatable skill, so the next time the same gap arises the fleet is
already equipped.

---

## How it works today

### The 30 shipping agents

`scripts/install.sh` installs **30 agent definitions** from `agents/` into
`.claude/agents/` (project scope) or `~/.claude/agents/` (user scope). The
`agents/CLAUDE.md` confirms the count; `baton agents` shows them at runtime,
grouped by category.

The roster covers the full delivery lifecycle:

| Category | Agents |
|----------|--------|
| Orchestration | `orchestrator`, `team-lead`, `task-runner` |
| Backend | `backend-engineer`, `backend-engineer--python`, `backend-engineer--node` |
| Frontend | `frontend-engineer`, `frontend-engineer--react`, `frontend-engineer--dotnet` |
| Architecture | `architect` |
| Quality | `test-engineer`, `code-reviewer`, `security-reviewer` |
| Governance | `auditor` |
| Data | `data-engineer`, `data-analyst`, `data-scientist` |
| Visualization | `visualization-expert` |
| Operations | `devops-engineer` |
| Domain | `subject-matter-expert`, `learning-analyst`, `system-maintainer` |
| Meta | `talent-builder` |
| Archetype | `archetype-james-engineering-manager` |
| Resilience | `immune-autofix`, `immune-deprecated-api`, `immune-doc-drift`, `immune-stale-comment`, `immune-todo-rot`, `immune-untested-edges` |

Each file is a Markdown document with YAML frontmatter (`name`, `description`,
`model`, `tools`, optional `permissionMode`, `color`) and a body system prompt.
The frontmatter is what the runtime registers; the body is what the agent reads
when dispatched. See `agents/CLAUDE.md` for format rules.

### The `role--flavor` naming scheme

Stack-specific variants follow the convention `<role>--<flavor>`. Four flavors
ship today:

| Flavored agent | Use when |
|----------------|----------|
| `backend-engineer--python` | `pyproject.toml`, `requirements.txt`, or `setup.py` at root |
| `backend-engineer--node` | `package.json` or `tsconfig.json` at root (JS/TS project) |
| `frontend-engineer--react` | `next.config.*`, `nuxt.config.*`, `angular.json`, or `vite.config.*` with `"react"` in `package.json` |
| `frontend-engineer--dotnet` | `appsettings.json` or `.csproj`/`.sln` at root |

The base agents (`backend-engineer`, `frontend-engineer`) remain in the registry
as fallbacks when no matching flavor exists.

### Routing: stack detection and flavor selection

`baton route [ROLES]` and `baton agents` are the CLI entry points
(`agent_baton/cli/commands/agents/route.py`, `agents.py`).

The `AgentRouter` in `agent_baton/core/orchestration/router.py` runs a two-pass
scan of the project tree (root + visible children + visible grandchildren,
skipping `node_modules`, `__pycache__`, `dist`, `build`, `.git`):

1. **Framework signals** (more specific) — `FRAMEWORK_SIGNALS` maps filenames
   like `next.config.js` or `appsettings.json` to `(language, framework)` pairs.
   Root-level signals are authoritative; subdir-level signals provide framework
   hints but do not override a root-level language.

2. **Package manager signals** (broader) — `PACKAGE_SIGNALS` maps `pyproject.toml`,
   `go.mod`, `Cargo.toml`, etc. to a language. Python wins over Node/TS when both
   appear at the root (monorepo convention: Python backend + JS frontend).

The result is a `StackProfile(language, framework, detected_files, languages,
frameworks)`. The router then consults `FLAVOR_MAP` to find the right flavor
suffix for each requested role, verifies the flavored agent exists in the
`AgentRegistry`, and returns either the flavored name or the base name as
fallback.

```bash
baton route backend-engineer frontend-engineer
#   Stack: python/fastapi
#   backend-engineer   → backend-engineer--python  *
#   frontend-engineer  → frontend-engineer            (no flavor match)
```

### Learned overrides

Routing corrections that persist across sessions are stored in
`.claude/team-context/learned-overrides.json` and managed by
`LearnedOverrides` in `agent_baton/core/learn/overrides.py`. Before consulting
the hardcoded `FLAVOR_MAP`, the router reads `flavor_map` from this file. A
project-specific entry wins:

```json
{
  "flavor_map": {
    "python/react": {
      "backend-engineer": "python",
      "frontend-engineer": "react"
    }
  }
}
```

The `system-maintainer` agent is the designated writer of
`learned-overrides.json` — it never touches source code.

### The `talent-builder` agent

When the roster has a gap, `talent-builder` (`agents/talent-builder.md`, runs on
`opus`) fills it. It builds the full knowledge stack, not just an agent file:

| Artifact | Location | When created |
|----------|----------|--------------|
| Agent definition (`.md`) | `.claude/agents/` or `~/.claude/agents/` | A new role is needed |
| Knowledge pack | `.claude/knowledge/<domain>/` | Domain facts too large to bake into the prompt (100–500 lines) |
| Skill | `.claude/skills/<name>/SKILL.md` + scripts + templates | A workflow is done repeatedly |
| Reference doc | `.claude/references/` | Multiple agents share the same knowledge |

`talent-builder` follows a structured workflow: understand the need, research
the domain (light or deep, 5–30 minutes), apply a five-test decision framework
(agent vs. knowledge pack vs. skill vs. reference doc), and report a token-cost
estimate before writing anything. The naming convention it uses matches the
fleet's existing scheme: `backend-engineer--go`, `data-analyst--salesforce`, etc.

### Context economics

Every subagent costs a full context-window load, startup latency, and
information-loss at handoff. Baton manages this cost at four points:

1. **Inline research and routing** — stack detection and knowledge resolution
   run inside the orchestrator's own session; no new agent window is opened
   until actual implementation work begins (noted in `README.md` and
   `agent_baton/core/orchestration/CLAUDE.md`).

2. **Per-step MCP pass-through** — each `PlanStep` declares `mcp_servers`:
   only the listed servers are forwarded into the agent's tool environment.
   Steps that need no external tools carry an empty list, keeping unused tool
   schemas out of the context window (source: `README.md` §"Selective MCP
   pass-through"; `docs/design-decisions.md` §ADR-21).

3. **CHECKPOINT action** — `ActionType.CHECKPOINT` (`agent_baton/models/
   execution.py`, value `"checkpoint"`) tells the orchestrator to save state
   and start a fresh session. Its docstring reads: *"save state + suggest fresh
   session to prevent context rot"*. State persists to `baton.db`; `baton
   execute resume` reconstructs from the last checkpoint without re-dispatching
   any step.

4. **Worktree isolation** — when two or more steps in a wave are parallel-safe,
   the executor provisions a linked git worktree at `.claude/worktrees/
   <task_id>/<step_id>/` before dispatch. Parallel agents write to isolated
   working copies and no uncommitted change leaks between steps
   (`BATON_WORKTREE_ENABLED`, default `1`; stale worktrees reclaimed after
   4 hours by `WorktreeManager.gc_stale()`).

---

## The gap today

### 1. Limited shipping flavor coverage

Four flavors ship (`--python`, `--node`, `--react`, `--dotnet`). The stack
detector knows how to detect Go (`go.mod`), Rust (`Cargo.toml`), Ruby
(`Gemfile`), Java (`build.gradle`, `pom.xml`), and Kotlin — but none of these
languages appear in `FLAVOR_MAP`, so the router always falls back to the
unflavored `backend-engineer` for Go, Rust, Ruby, and Java projects. The
detection data is there; the specialist agent is not.

Source: `agent_baton/core/orchestration/router.py` — `PACKAGE_SIGNALS` lists
all six non-covered languages; `FLAVOR_MAP` has no entry for any of them.

### 2. On-demand creation friction

When a gap exists, `talent-builder` can fill it — but it does not do so
automatically mid-plan. It is a dispatched agent step, not a background service.
The flow is: planner emits a DISPATCH action → orchestrator spawns
`talent-builder` → `talent-builder` conducts its research interview (Step 1 of
its workflow asks for domain, documentation, scope, and intended usage) → writes
the files → returns. This adds at least one full agent round-trip before the
missing specialist is available, and the orchestrator must then restart or
reconfigure the affected steps.

There is no auto-initiation path that detects a missing flavor at `baton plan`
time and automatically invokes `talent-builder` to fill the gap before execution
begins (the `has_project_agents()` check in `AgentRegistry` triggers a
talent-builder suggestion in `baton plan`, but it is advisory, not automatic).

### 3. Heuristic routing and mis-routes

The router is deterministic for a given `(task, registry)` pair — `orchestration/
CLAUDE.md` calls this out explicitly as a design goal. However, the signals it
uses are purely file-system heuristics. Two known failure modes:

- **Root vs. subdir priority**: a Python monorepo with `pyproject.toml` at the
  root and a React app in `pmo-ui/` correctly reports `language=python`. But a
  project with only a subdir-level framework signal (e.g., `pmo-ui/next.config.js`
  in an otherwise empty parent) triggers the subdir-framework fallback path,
  which can classify the whole repo as TypeScript/React. This is explicitly
  called out in the router's inline comments (bd-75e8 fix).

- **Language-not-in-FLAVOR_MAP**: Go, Rust, Ruby, Java, and Kotlin are detected
  but produce no flavor. The router silently falls back to the base agent and
  logs at `DEBUG` level — no warning surfaces to the operator that the detected
  stack is uncovered.

`learned-overrides.json` is the correction mechanism: once a mis-route is
identified, `system-maintainer` writes the correct mapping, and subsequent runs
use it. But the initial mis-route still happens once before the correction
exists.

### 4. Context rot mitigated, not eliminated

CHECKPOINT, worktrees, and per-step MCP pass-through reduce context
accumulation, but they do not eliminate it:

- **CHECKPOINT** suggests a fresh session; it does not force one. Whether a
  new session is started depends on the orchestrator agent acting on the
  instruction.
- **Worktree isolation** prevents uncommitted file bleed between parallel
  agents but does not reduce context within a single long-running specialist
  session.
- **Knowledge packs** front-load domain knowledge as structured files instead
  of re-deriving it from the codebase, but the agent still loads and processes
  them, contributing to context size.
- **Per-step MCP pass-through** keeps tool schemas small, not the
  accumulated conversation history.

There is no mechanism to measure context pressure within a running specialist
session and proactively checkpoint before quality degrades. The CHECKPOINT
action is inserted at plan time based on step count or phase boundaries, not
dynamically based on observed context size.

---

## Where this lives

- Docs: [../agent-roster.md](../agent-roster.md), [../orchestrator-usage.md](../orchestrator-usage.md)
- Code: `agents/talent-builder.md`, `agent_baton/core/orchestration/router.py`,
  `agent_baton/core/orchestration/registry.py`,
  `agent_baton/core/learn/overrides.py`
- Commands: `baton agents`, `baton route`
