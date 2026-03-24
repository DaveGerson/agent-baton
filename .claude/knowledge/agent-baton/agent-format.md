---
name: agent-format
description: Agent definition file format — frontmatter fields, naming conventions, model selection, permission modes, and tool sets
tags: [agent-format, frontmatter, conventions, agent-definition]
priority: high
---

# Agent Definition Format

## File Structure

Agent definitions are markdown files with YAML frontmatter delimited by `---`:

```
---
name: agent-name
description: |
  Multi-line description of when to use this agent.
model: sonnet
permissionMode: auto-edit
color: blue
tools: Read, Write, Edit, Glob, Grep, Bash
---

# Agent Title

Markdown body — the agent's system prompt / instructions.
```

## Frontmatter Fields

| Field | Required | Type | Values | Purpose |
|-------|----------|------|--------|---------|
| `name` | Yes | string | kebab-case | Agent identifier. Flavors use `role--flavor` |
| `description` | Yes | string | Multi-line | Trigger text — Claude Code uses this to decide invocation |
| `model` | No | string | `opus`, `sonnet`, `haiku` | Default: `sonnet` |
| `permissionMode` | No | string | `auto-edit`, `default` | Default: `default` |
| `color` | No | string | `red`, `blue`, `green`, `yellow`, `cyan`, `magenta`, `purple` | UI hint |
| `tools` | No | string | Comma-separated | `Read, Write, Edit, Glob, Grep, Bash` |

## Naming Conventions

| Pattern | Example | Meaning |
|---------|---------|---------|
| `role` | `architect` | Base agent (generic) |
| `role--flavor` | `backend-engineer--python` | Flavored variant (stack-specific) |

The double-dash `--` separates base from flavor. The `AgentDefinition` model
parses this into `.base_name` and `.flavor` properties.

## Parsing

The `AgentRegistry` parses agent files by:
1. Splitting on the first two `---` delimiters
2. Parsing the YAML block between them (PyYAML)
3. Taking everything after the second `---` as the markdown body
4. Constructing an `AgentDefinition` dataclass

## Frontmatter Parsing Code Pattern

```python
import yaml

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split markdown with YAML frontmatter into (metadata, body)."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    metadata = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return metadata, body
```

## Model Selection Guide

| Model | Use For | Examples |
|-------|---------|---------|
| `opus` | Deep reasoning, independence, complex judgment | orchestrator, auditor, architect, SME |
| `sonnet` | Implementation, standard analysis | backend-engineer, test-engineer, data-analyst |
| `haiku` | Simple lookups, formatting, boilerplate | Quick checks, simple transforms |

## Permission Modes

| Mode | Meaning | Use For |
|------|---------|---------|
| `auto-edit` | Agent writes files without prompting | Implementers (engineers, data-engineer) |
| `default` | Agent must request approval for writes | Reviewers (auditor, code-reviewer, security-reviewer) |

## Tool Sets

| Role Type | Tools | Rationale |
|-----------|-------|-----------|
| Implementer | `Read, Write, Edit, Glob, Grep, Bash` | Needs full write + shell access |
| Reviewer | `Read, Glob, Grep` | Read-only analysis |
| Reviewer + verify | `Read, Glob, Grep, Bash` | Read-only + can run tests/builds |

## Reference Document Format

Reference docs may optionally have frontmatter:

```
---
name: reference-name
description: |
  What this reference contains and when to read it.
---

# Reference Title

Content...
```

Most references are plain markdown without frontmatter. The `description`
field is used by the registry to help agents decide which references to read.

## Knowledge Pack Format

Knowledge packs are directories under `.claude/knowledge/` (project-level) or
`~/.claude/knowledge/` (global). Each pack contains a `knowledge.yaml` manifest
and one or more `.md` documents.

### Pack manifest: `knowledge.yaml`

```yaml
# .claude/knowledge/my-pack/knowledge.yaml
name: my-pack                         # required — kebab-case identifier
description: What this pack contains  # required — used by planner for matching
tags: [tag1, tag2]                    # optional — used for strict tag matching
target_agents: [agent-name]           # optional — agents that always receive this pack
default_delivery: reference           # optional — inline | reference (default: reference)
```

| Field | Required | Type | Purpose |
|-------|----------|------|---------|
| `name` | Yes | string | Pack identifier. Must match directory name. |
| `description` | Yes | string | Planner uses this for relevance matching. |
| `tags` | No | list[string] | Strict tag matching by planner and `find_by_tags()`. |
| `target_agents` | No | list[string] | Agent names that auto-receive this pack. Supports base names (`backend-engineer` matches `backend-engineer--python`). Empty list = broadly applicable (not auto-matched by `packs_for_agent()`). |
| `default_delivery` | No | string | `inline` or `reference`. Resolver may override based on token budget. |

### Document frontmatter

Each `.md` file in a pack should have YAML frontmatter. `name` and `description`
are required for the document to be discoverable by the planner:

```yaml
---
name: my-document                     # required — kebab-case identifier
description: What this document covers and when it is useful
tags: [tag1, tag2]                    # optional — for tag-based matching
priority: normal                      # optional — high | normal | low (default: normal)
grounding: |                          # optional — injected before doc content on inline delivery
  You are receiving this because your task involves X.
  Use it to make informed decisions about Y.
---
```

| Field | Required | Type | Purpose |
|-------|----------|------|---------|
| `name` | Yes | string | Document identifier within the pack. |
| `description` | Yes | string | Used by planner TF-IDF search and plan.md rendering. |
| `tags` | No | list[string] | Augments pack-level tags for `find_by_tags()` queries. |
| `priority` | No | string | `high` documents are inlined first when budget allows. |
| `grounding` | No | string | Agent-facing context prepended on inline delivery. Auto-generated from pack+doc description if absent. |

`token_estimate` is computed automatically by `KnowledgeRegistry` at index time
(character count ÷ 4 heuristic). Never set it manually.

### Graceful degradation

- Packs without `knowledge.yaml` still load (name inferred from directory, empty metadata).
  They are only reachable via explicit `--knowledge-pack` or agent-declared bindings.
- Documents without frontmatter still load (name inferred from filename, empty metadata).
  They won't match planner tag or relevance searches.

When `talent-builder` creates a new agent + knowledge pack, it generates both the
`knowledge.yaml` manifest and document frontmatter automatically from the agent definition.
