# Roadmap: Plan Creation and Coordination

**Capability goal:** Developers should get plans that are well-scoped, explainable, context-aware, and safe to execute. Baton should show why it selected phases, agents, gates, knowledge, and coordination constraints.

**No-structural-refactor constraint:** Keep the existing `IntelligentPlanner` pipeline, `PlanDraft`, `ExecutionEngine`, `ActionResolver`, and PMO route structure. Add wiring, diagnostics, validation, tests, and small behavior improvements only.

---

## Phase 1 — Activate and expose planning intelligence

### Developer outcome

A developer running `baton plan` or using PMO Forge can see what Baton inferred: task type, complexity, archetype, risk, selected agents, selected phases, knowledge attachments, gates, and validation warnings.

### Work items

1. **Wire the default knowledge registry into planner construction.**
   - Build and load `KnowledgeRegistry` in CLI/API planner entry points when no explicit registry is provided.
   - If no packs exist, report `knowledge_registry: loaded=0`, not an error.
   - If packs are degraded, report degraded pack names in diagnostics.

2. **Add a concise plan diagnostics block.**
   - Include task type, complexity, archetype, risk, classification source, selected agents, phase count, gate count, approval count, knowledge attachment count, and validation warning count.
   - Prefer a stable text block plus optional JSON output.

3. **Expose planner explainability in normal workflows.**
   - Add or extend `--explain` / `--explain-json` support for plan creation.
   - PMO Forge should return explainability metadata or make it queryable after plan generation.

4. **Make unavailable dependencies visible.**
   - If LLM classification, knowledge registry, bead store, or policy engine is unavailable, print a clear warning with the fallback path.
   - Avoid noisy stack traces unless `--debug` is set.

### Suggested files

```text
agent_baton/core/engine/planning/planner.py
agent_baton/core/engine/planning/stages/assembly.py
agent_baton/core/orchestration/knowledge_registry.py
agent_baton/cli/commands/**/plan*.py
agent_baton/api/deps.py
agent_baton/api/routes/pmo.py
tests/planning/
tests/api/
```

### Acceptance criteria

- A plan created in a project with `.claude/knowledge/<pack>/knowledge.yaml` attaches or references matching knowledge documents.
- A plan created in a project with no knowledge packs still succeeds and says no knowledge packs were loaded.
- Plan output includes a short diagnostics summary.
- `explain_plan()` or equivalent output is available through CLI and PMO/API path.
- Tests cover both with-knowledge and no-knowledge cases.

### Validation commands

```bash
python -m pytest -q tests/planning tests/api
python -m pytest -q tests/test_api_pmo_beads.py || true
baton plan "Add a small validation helper and test it" --explain
```

### Baton run prompt

```text
Implement Phase 1 of roadmaps/01-plan-creation-and-coordination.md.
Focus on activating KnowledgeRegistry by default and surfacing concise plan diagnostics for developers.
Do not refactor the planner pipeline or ExecutionEngine.
Add focused tests for knowledge-loaded and no-knowledge cases.
```

---

## Phase 2 — Make plan quality failures actionable

### Developer outcome

Developers should not receive malformed or low-quality plans without clear warnings. When Baton detects a bad plan, it should explain exactly what to fix.

### Work items

1. **Make critical plan defects fail by default in non-dev mode.**
   - Preserve an explicit opt-out for local experimentation.
   - Keep `BATON_PLANNER_HARD_GATE` support, but add a clearer default policy such as `BATON_DEV_MODE=1` to allow warnings-only behavior.

2. **Improve defect messages.**
   - For `empty_plan`, `empty_phase`, `agent_phase_mismatch`, and `review_skipped`, include phase/step IDs and a suggested remediation.

3. **Add golden plan tests.**
   - Snapshot representative plans:
     - direct/light task,
     - investigative bug task,
     - compound multi-concern task,
     - high-risk/security task,
     - compliance/audit task,
     - knowledge-heavy task.

4. **Fail clearly on impossible phase/agent assignment.**
   - If reviewer/auditor agents are blocked from implementation phases and no Review/Audit phase is created, return a plan defect.

### Suggested files

```text
agent_baton/core/engine/planning/stages/validation.py
agent_baton/core/engine/planning/utils/phase_builder.py
agent_baton/core/engine/planning/stages/enrichment.py
tests/planning/test_plan_quality_*.py
tests/snapshots/plans/
```

### Acceptance criteria

- Critical defects block plan creation unless dev/warn-only mode is explicitly enabled.
- Every critical defect includes a human-readable remediation.
- Golden plan tests are stable and intentionally updated when planner behavior changes.
- High-risk and compliance plans always contain review/audit coverage or fail validation.

### Validation commands

```bash
python -m pytest -q tests/planning/test_plan_quality_*.py
python -m pytest -q tests/snapshots || true
BATON_PLANNER_HARD_GATE=1 baton plan "Refactor authentication and payment authorization logic"
```

### Baton run prompt

```text
Implement Phase 2 of roadmaps/01-plan-creation-and-coordination.md.
Make critical planner defects actionable and enforceable without restructuring the planner.
Add golden tests for representative plan shapes.
```

---

## Phase 3 — Improve context, handoffs, and coordination loops

### Developer outcome

Agents should receive the right context at the right time, with less repeated knowledge and clearer prior-step handoffs. Developers should see when Baton amends or rounds out a plan.

### Work items

1. **Add context budget reporting.**
   - For each plan, report estimated shared context size, inline knowledge size, reference count, and largest context contributor.

2. **Improve extracted path warnings.**
   - If extracted file paths are outside the project root, mark them read-only in diagnostics and prompt context.

3. **Make goal round-out visible.**
   - When goal evaluation inserts phases, record a concise amendment summary in status output and plan history.

4. **Add handoff quality checks.**
   - Warn when a step depends on a previous step that produced no outcome, no files, and no bead/handoff summary.

### Suggested files

```text
agent_baton/core/engine/planning/utils/context.py
agent_baton/core/engine/dispatcher.py
agent_baton/core/engine/executor.py
agent_baton/cli/commands/execution/execute.py
agent_baton/api/routes/pmo.py
tests/engine/
tests/planning/
```

### Acceptance criteria

- Plan diagnostics include context size estimates.
- Out-of-root paths are visible to the developer and are not presented as write targets.
- Status output shows plan amendments/goal round-out cycles.
- Tests cover out-of-root path rendering and empty-handoff warning behavior.

### Validation commands

```bash
python -m pytest -q tests/engine tests/planning
baton plan "Update src/api.py and review /tmp/example-only-reference.txt" --explain
```

### Baton run prompt

```text
Implement Phase 3 of roadmaps/01-plan-creation-and-coordination.md.
Improve context and handoff visibility for developers without changing core state-machine structure.
Add focused tests for path warnings and context-size diagnostics.
```

---

## Phase 4 — Make planning inspectable in PMO and docs

### Developer outcome

A developer using the PMO UI can preview why Baton selected a plan and can reject/edit the plan before execution with enough context to make a good decision.

### Work items

1. **Add PMO plan preview metadata.**
   - Show classification source, risk, agents, phases, gates, knowledge attachments, and validation warnings.

2. **Add docs for plan interpretation.**
   - Explain what each diagnostic field means and how to fix common warnings.

3. **Add plan smoke examples.**
   - Provide small example tasks and expected plan characteristics.

4. **Add release check for planner smoke.**
   - Include planner smoke tests in CI/release gating.

### Suggested files

```text
pmo-ui/src/**
agent_baton/api/models/responses.py
agent_baton/api/routes/pmo.py
docs/cli-reference.md
docs/orchestrator-usage.md
tests/api/
pmo-ui/src/**/*.test.tsx
```

### Acceptance criteria

- PMO preview displays plan diagnostics before approval/save.
- Docs include a troubleshooting table for plan warnings.
- CI has a planner smoke target or required job.

### Validation commands

```bash
python -m pytest -q tests/api tests/planning
cd pmo-ui && npm run build && npm run test:run
```

### Baton run prompt

```text
Implement Phase 4 of roadmaps/01-plan-creation-and-coordination.md.
Expose plan diagnostics in PMO and documentation. Do not redesign the PMO UI; add a compact preview panel and focused tests.
```
