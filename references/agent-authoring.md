---
name: agent-authoring
description: Standard contract for authoring generated Agent Baton agent files.
---

# Agent Authoring

Use this reference when `talent-builder` creates or updates an agent. The
legacy name `talent-manager` is a compatibility alias for `talent-builder`;
prefer `talent-builder` in new docs and prompts, and only mention
`talent-manager` when integrating with older workflows that still use that
name.

## Generated-Agent Contract

Every generated agent is a markdown file with YAML frontmatter and a body
prompt. The frontmatter is the routing surface. The body is the operating
contract the agent follows at dispatch time.

Required frontmatter fields:
- `name`
- `description`
- `model`
- `permissionMode`
- `tools`

Recommended frontmatter fields:
- `owner`
- `status`
- `version`
- `created_by`
- `last_reviewed`
- `knowledge_packs`

Required body sections:
- Mission
- Before Starting
- Knowledge References
- Principles
- Anti-Patterns
- Output Format

## Field Guidance

| Field | Guidance |
|-------|----------|
| `name` | Kebab-case role name. Use `role--flavor` for variants. |
| `description` | Multi-line trigger guidance. Say when to use the agent and when not to. |
| `model` | Use `opus` for high-judgment reasoning, `sonnet` for implementation, `haiku` for narrow procedural tasks. |
| `permissionMode` | Use `default` for reviewers/advisors and `auto-edit` only for trusted implementers. |
| `tools` | Start with the minimum viable set. Reviewers usually need only `Read`, `Glob`, `Grep`. |
| `owner` | Team or person responsible for maintenance. |
| `status` | One of `draft`, `active`, `deprecated`, or `archived`. |
| `version` | Semantic version for prompt contract changes. |
| `created_by` | Usually `talent-builder`, unless another agent or human authored it. |
| `last_reviewed` | ISO date for the last contract review. |
| `knowledge_packs` | List of knowledge-pack paths the agent is expected to read. Use `[]` when none are required. |

## Tool Policy

Avoid broad tools unless the mission requires them. Add `Edit`, `Write`,
`Bash`, or external MCP/server tools only when the agent's responsibilities
cannot be completed with read-only tools. When broad tools are included, state
the reason in the Principles or Before Starting section.

## Reference Validation

Before saving or reporting an agent:
- Read back the final agent file.
- Validate references named in `knowledge_packs` and Knowledge References.
- Remove stale paths, or mark optional references explicitly with why they are
  optional.
- Keep generated-agent prompts concise enough that dispatch context is not
  dominated by static boilerplate.

## Starter Templates

Use these files as copy sources:
- `.claude/templates/agents/base-agent.md`
- `.claude/templates/agents/flavored-agent.md`
- `.claude/templates/agents/reviewer-agent.md`
