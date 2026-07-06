# Roadmap: Talent Builder and Subagent Management

**Capability goal:** Developers should be able to create, validate, review, and reuse specialist subagents without accidentally creating unsafe, bloated, or broken agent definitions.

**Canonical term:** The implemented agent is `talent-builder`. Treat `talent-manager` as an alias only if needed for user-facing compatibility.

**No-structural-refactor constraint:** Keep filesystem-backed agent definitions and `AgentRegistry` override semantics. Add validation, metadata, templates, CLI helpers, and documentation only.

---

## Phase 1 — Standardize generated-agent contract

### Developer outcome

When Talent Builder creates or updates an agent, developers receive predictable files, metadata, references, and validation instructions.

### Work items

1. **Add a generated-agent output contract.**
   - Define required frontmatter fields: `name`, `description`, `model`, `permissionMode`, `tools`.
   - Define recommended fields: `owner`, `status`, `version`, `created_by`, `last_reviewed`, `knowledge_packs`.
   - Define output sections: mission, before starting, knowledge references, principles, anti-patterns, output format.

2. **Update Talent Builder instructions.**
   - Ensure it writes the contract into every generated agent.
   - Add explicit “do not use broad tools unless needed” rule.
   - Add “read back and validate references” as non-optional.

3. **Add `talent-manager` alias documentation.**
   - If docs or prompts use `talent-manager`, point to `talent-builder`.
   - Avoid creating two divergent agents unless there is a strong reason.

4. **Create starter templates.**
   - `templates/agents/base-agent.md`
   - `templates/agents/flavored-agent.md`
   - `templates/agents/reviewer-agent.md`

### Suggested files

```text
agents/talent-builder.md
templates/agents/*.md
references/agent-authoring.md
docs/agent-roster.md
tests/agents/
```

### Acceptance criteria

- Talent Builder instructions include the generated-agent contract.
- Templates exist and match the contract.
- Docs use `talent-builder` consistently or clearly alias `talent-manager`.
- Existing bundled agents still parse.

### Validation commands

```bash
python -m agent_baton.cli.main validate agents
python -m pytest -q tests/agents || true
```

### Baton run prompt

```text
Implement Phase 1 of roadmaps/03-talent-builder-subagent-management.md.
Standardize the generated-agent contract, update Talent Builder instructions, and add templates.
Do not change AgentRegistry architecture.
```

---

## Phase 2 — Add agent doctor validation

### Developer outcome

Developers can run one command to find broken generated agents before Baton dispatches them.

### Work items

1. **Add or extend an agent validation command.**
   - Preferred: `baton agents doctor` or an enhanced `baton validate` report.
   - Validate frontmatter shape, model value, permission mode, tool list, description length, and output-format section.

2. **Verify knowledge references.**
   - Check `knowledge_packs` frontmatter against loaded knowledge registry.
   - Check “Before Starting” file paths exist when they are local paths.

3. **Add safety warnings.**
   - Flag implementer agents with broad tools and no clear need.
   - Flag reviewers/auditors with `Write` or `Edit` unless explicitly justified.
   - Flag very large baked-in knowledge sections.

4. **Add machine-readable report.**
   - Support `--json` output for CI and PMO future use.

### Suggested files

```text
agent_baton/cli/commands/agents/*.py
agent_baton/core/orchestration/registry.py
agent_baton/core/orchestration/knowledge_registry.py
tests/agents/test_agent_doctor.py
```

### Acceptance criteria

- `baton agents doctor` or equivalent exits non-zero on broken required fields.
- Missing knowledge packs are reported with agent name and field.
- Safety warnings are visible but do not block unless `--strict` is passed.
- JSON output is stable enough for tests.

### Validation commands

```bash
python -m pytest -q tests/agents/test_agent_doctor.py
baton agents doctor --strict || true
baton agents doctor --json > /tmp/agent-doctor.json
```

### Baton run prompt

```text
Implement Phase 2 of roadmaps/03-talent-builder-subagent-management.md.
Add a lightweight agent doctor that validates generated agents, knowledge references, and unsafe tool permissions.
Keep changes additive and backward compatible.
```

---

## Phase 3 — Add draft/review/promote workflow using metadata

### Developer outcome

New subagents can be created as drafts, reviewed, then promoted for use. Developers can distinguish experimental agents from approved team assets.

### Work items

1. **Support `status` metadata.**
   - Recognize `status: draft|reviewed|approved|deprecated` in frontmatter.
   - Default missing status to `approved` for backward compatibility, but warn for generated agents missing it.

2. **Add doctor rules for lifecycle.**
   - Draft agents should be visible but optionally excluded from planning unless explicitly requested.
   - Deprecated agents should warn when selected.

3. **Add promote checklist.**
   - `baton agents promote <name>` may be a simple frontmatter edit, or document a manual process if command scope is too much.
   - Require doctor pass before promotion.

4. **Record generation provenance.**
   - Encourage `created_by: talent-builder`, `source_docs`, and `version` fields.

### Suggested files

```text
agent_baton/core/orchestration/registry.py
agent_baton/cli/commands/agents/*.py
agents/talent-builder.md
templates/agents/*.md
tests/agents/
```

### Acceptance criteria

- Draft/deprecated status is visible in agent listing or doctor output.
- Planner behavior remains backward compatible for existing agents.
- A developer can promote a generated agent with a documented checklist.

### Validation commands

```bash
python -m pytest -q tests/agents
baton agents doctor --strict
```

### Baton run prompt

```text
Implement Phase 3 of roadmaps/03-talent-builder-subagent-management.md.
Add lifecycle metadata support and draft/review/promote validation for generated agents.
Do not change the fundamental filesystem-backed registry model.
```

---

## Phase 4 — Make the agent catalog useful

### Developer outcome

Developers can browse available agents, see which are safe/approved, understand when to use them, and know what knowledge packs they depend on.

### Work items

1. **Improve agent listing output.**
   - Show name, category, model, permission mode, status, knowledge packs, and source path.

2. **Add catalog documentation.**
   - Generate or update `docs/agent-roster.md` from registry data where practical.

3. **Expose agent health in PMO/API.**
   - Add status/knowledge metadata to the `/agents` response if low risk.

4. **Add examples.**
   - Include “create a new agent for X” and “validate generated agent” examples.

### Suggested files

```text
agent_baton/api/routes/agents.py
agent_baton/api/models/responses.py
agent_baton/cli/commands/agents/*.py
docs/agent-roster.md
pmo-ui/src/**
tests/api/
tests/agents/
```

### Acceptance criteria

- CLI agent list is useful for a developer deciding which agent to use.
- Agent catalog docs include generated-agent workflow.
- API response remains backward compatible or versioned.

### Validation commands

```bash
python -m pytest -q tests/api tests/agents
baton agents list || true
```

### Baton run prompt

```text
Implement Phase 4 of roadmaps/03-talent-builder-subagent-management.md.
Make the agent catalog more useful in CLI/API/docs with status, dependencies, and examples.
Keep schema changes backward compatible.
```
