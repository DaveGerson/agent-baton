# Agent Roster

This project uses a three-tier agent roster:
1. **Packaged agents** (20) — mirrored from `agents/` and distributed to users.
2. **Meta agents** (6) — project-specific agents for developing agent-baton.
3. **GSD framework agents** (18) — agents for project management and autonomous workflows (located in `.claude/knowledge/gsd/agents/`).

| Agent | Role |
|-------|------|
| `orchestrator` | Coordinate multi-step development tasks |
| `backend-engineer` / `--python` / `--node` | Server-side implementation |
| `frontend-engineer` / `--react` / `--dotnet` | Client-side UI |
| `architect` | Design decisions, module boundaries |
| `test-engineer` | Write and organize pytest tests |
| `code-reviewer` | Quality review before commits |
| `auditor` | Safety review for guardrail/hook changes |
| `talent-builder` | Create new distributable agent definitions |
| `system-maintainer` | Post-cycle config tuning via learned-overrides.json |
| `security-reviewer` | Security audit (OWASP, auth, secrets) |
| `devops-engineer` | Infrastructure, CI/CD, Docker |
| `data-engineer` / `data-analyst` / `data-scientist` | Data stack |
| `visualization-expert` | Charts, dashboards |
| `subject-matter-expert` | Domain-specific business operations |

## Meta Agents (Development)

| Agent | Role |
|-------|------|
| `ai-systems-architect` | Multi-agent orchestration design |
| `agent-definition-engineer` | Edit agent .md files, references, knowledge packs |
| `prompt-engineer` | Agent prompt optimization |
| `ai-product-strategist` | Product decisions, value/cost analysis |
| `spec-document-reviewer` | Review and validate specification documents |
| `documentation-architect` | Deep-dive codebase documentation |

## GSD Framework Agents

Located in `.claude/knowledge/gsd/agents/`. These are only loaded when a GSD task is active.
