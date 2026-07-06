# Roadmap: Knowledge Pack Management

**Capability goal:** Developers should be able to create, validate, attach, search, and maintain knowledge packs so agents have the right project/domain context without bloated prompts or missing references.

**No-structural-refactor constraint:** Keep `KnowledgeRegistry`, `KnowledgeResolver`, `KnowledgePack`, and filesystem-backed `.claude/knowledge/` layout. Add default wiring, commands, validation, docs, and focused improvements only.

---

## Phase 1 — Make knowledge packs active by default

### Developer outcome

When a project has knowledge packs, Baton uses them automatically and reports what was loaded. Developers do not need to know hidden constructor details.

### Work items

1. **Load `KnowledgeRegistry` in default planner paths.**
   - CLI plan creation should load global and project knowledge packs.
   - API/PMO planner construction should do the same.

2. **Normalize manifest naming.**
   - Pick `knowledge.yaml` as canonical.
   - Update docs/model comments that mention `pack.yaml`.
   - Optionally tolerate `pack.yaml` with a warning and migration hint.

3. **Report loaded packs.**
   - Plan diagnostics should include `knowledge_packs_loaded`, `degraded_packs`, `docs_indexed`, and `attachments_selected`.

4. **Add a tiny sample pack.**
   - Create a small example under docs or templates that users can copy.

### Suggested files

```text
agent_baton/core/orchestration/knowledge_registry.py
agent_baton/core/engine/planning/stages/risk.py
agent_baton/api/deps.py
agent_baton/models/knowledge.py
docs/orchestrator-usage.md
templates/knowledge/example-pack/
tests/knowledge/
```

### Acceptance criteria

- A project-level pack in `.claude/knowledge/<name>/knowledge.yaml` is loaded by default.
- Manifest naming is consistent in code comments and docs.
- Missing manifests are reported as degraded but do not break planning.
- Tests cover global/project override and degraded pack reporting.

### Validation commands

```bash
python -m pytest -q tests/knowledge tests/planning
baton plan "Use the sample domain rules to update validation" --explain
```

### Baton run prompt

```text
Implement Phase 1 of roadmaps/04-knowledge-pack-management.md.
Wire KnowledgeRegistry into default planning paths, normalize manifest naming, and report loaded/degraded knowledge packs.
Do not redesign the knowledge model.
```

---

## Phase 2 — Add knowledge doctor and search

### Developer outcome

Developers can validate knowledge packs before running agents and can search what Baton would know about a task.

### Work items

1. **Add `baton knowledge doctor`.**
   - Validate manifests, document frontmatter, token estimates, duplicate doc names, missing files, empty descriptions, and oversized inline candidates.
   - Support `--strict` and `--json`.

2. **Add `baton knowledge search <query>`.**
   - Search the registry using current metadata TF-IDF.
   - Show pack, doc, score, path, tags, priority, and token estimate.

3. **Add attach simulation.**
   - `baton knowledge resolve --agent <name> --task <text>` should show which docs would attach and whether inline/reference.
   - If full command is too much, add this as a doctor sub-mode.

4. **Improve validation messages.**
   - Every warning should tell the developer what to edit.

### Suggested files

```text
agent_baton/cli/commands/knowledge/*.py
agent_baton/core/orchestration/knowledge_registry.py
agent_baton/core/engine/knowledge_resolver.py
tests/knowledge/test_knowledge_doctor.py
tests/knowledge/test_knowledge_search.py
```

### Acceptance criteria

- Doctor catches missing `knowledge.yaml`, empty doc metadata, and large inline candidates.
- Search returns useful metadata and paths.
- Resolve simulation matches actual resolver output for a fixture pack.

### Validation commands

```bash
python -m pytest -q tests/knowledge
baton knowledge doctor --strict || true
baton knowledge search "authentication token renewal"
```

### Baton run prompt

```text
Implement Phase 2 of roadmaps/04-knowledge-pack-management.md.
Add knowledge doctor/search/resolve-simulation commands using existing KnowledgeRegistry and KnowledgeResolver.
Keep implementation additive and testable.
```

---

## Phase 3 — Turn knowledge gaps into improvement suggestions

### Developer outcome

When agents report missing knowledge, developers get concrete suggestions for what pack/doc to create or update.

### Work items

1. **Summarize knowledge gaps.**
   - Add CLI/API output listing recent `KnowledgeGapRecord` items by agent, task type, and frequency.

2. **Suggest pack updates.**
   - For recurring gaps, suggest target pack name, doc name, tags, and draft grounding.

3. **Link gaps to Talent Builder.**
   - Add a suggested prompt: “Use talent-builder to create/update knowledge pack X with these gaps.”

4. **Track knowledge usage/freshness where already supported.**
   - Surface last-used and usage count if lifecycle telemetry exists.
   - Do not make telemetry required for planning.

### Suggested files

```text
agent_baton/models/knowledge.py
agent_baton/core/engine/knowledge_gap.py
agent_baton/core/learn/**
agent_baton/cli/commands/knowledge/*.py
agents/talent-builder.md
tests/knowledge/
```

### Acceptance criteria

- Developers can list recent knowledge gaps.
- Recurring gaps produce actionable pack/doc suggestions.
- Talent Builder instructions include the gap-to-pack workflow.
- Planning still succeeds when telemetry tables are unavailable.

### Validation commands

```bash
python -m pytest -q tests/knowledge tests/learn
baton knowledge gaps || true
```

### Baton run prompt

```text
Implement Phase 3 of roadmaps/04-knowledge-pack-management.md.
Surface knowledge gaps as actionable pack-update suggestions and connect the flow to Talent Builder.
Do not require new storage migrations unless absolutely necessary.
```

---

## Phase 4 — Make knowledge usable in UI and docs

### Developer outcome

Developers can understand and maintain project knowledge without reading implementation code or inspecting raw files manually.

### Work items

1. **Add knowledge docs.**
   - Document pack structure, frontmatter, manifest fields, tags, priorities, grounding, delivery behavior, and token budgeting.

2. **Add PMO/API read-only knowledge metadata.**
   - Return pack/doc metadata for UI display.
   - Avoid exposing full document content unless explicitly requested and safe.

3. **Add PMO knowledge panel.**
   - Small read-only list of packs, docs, degraded status, and token estimates.

4. **Add examples.**
   - Include at least one example pack and one task showing automatic attachment.

### Suggested files

```text
agent_baton/api/routes/**
agent_baton/api/models/responses.py
pmo-ui/src/**
docs/knowledge-packs.md
docs/orchestrator-usage.md
templates/knowledge/example-pack/
tests/api/
pmo-ui/src/**/*.test.tsx
```

### Acceptance criteria

- Docs explain how knowledge gets from pack to agent prompt.
- PMO/API can list knowledge metadata.
- Example pack works with `baton plan --explain`.

### Validation commands

```bash
python -m pytest -q tests/api tests/knowledge
cd pmo-ui && npm run build && npm run test:run
```

### Baton run prompt

```text
Implement Phase 4 of roadmaps/04-knowledge-pack-management.md.
Expose read-only knowledge metadata in docs/API/PMO and add example packs.
Do not expose arbitrary file contents through the API.
```
