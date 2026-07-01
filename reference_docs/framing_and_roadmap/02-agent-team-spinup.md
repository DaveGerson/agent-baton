# Roadmap: Agent Team Spin-Up Capabilities

**Capability goal:** Developers should be able to ask Baton for a multi-agent team and receive coordinated, bounded, reviewable work with clear ownership, conflict visibility, and synthesis.

**No-structural-refactor constraint:** Keep the existing `WorktreeTeamBackend`, `ClaudeTeamsBackend`, `TeamMember`, `TeamStepResult`, mailbox, and execution state model. Add validation, diagnostics, prompt improvements, reports, and minimal synthesis behavior only.

---

## Phase 1 — Make team readiness visible

### Developer outcome

Before Baton launches a team, developers should know whether the team is safe to run, which backend is active, which teammates will be spawned, and which limitations apply.

### Work items

1. **Add team readiness diagnostics.**
   - For every team step, report backend, member count, nested team count, shared files/contracts, synthesis strategy, conflict strategy, and warning count.

2. **Strict backend option.**
   - Add `BATON_TEAMS_BACKEND_STRICT=1` or equivalent.
   - In strict mode, unknown team backend names fail instead of falling back to worktree.

3. **Surface Claude Teams caveats to users.**
   - When `claude-teams` is active, print or return warnings for no resume, no nesting, one-team-at-a-time, fixed permissions, and missing skills/MCP frontmatter.

4. **Add team dispatch report artifact.**
   - Write a lightweight `team-report.md` or JSON under the execution/team directory when a team step is dispatched.

### Suggested files

```text
agent_baton/core/engine/team_backends.py
agent_baton/core/engine/planning/utils/phase_builder.py
agent_baton/core/engine/executor.py
agent_baton/cli/commands/execution/execute.py
tests/engine/test_team_*.py
```

### Acceptance criteria

- Developers can see the active team backend before dispatch.
- Unknown backend fails in strict mode and falls back only in default permissive mode.
- A team report exists for each team step.
- Claude Teams warnings are present in CLI/API diagnostics.

### Validation commands

```bash
python -m pytest -q tests/engine/test_team_*.py
BATON_TEAMS_BACKEND=not-real BATON_TEAMS_BACKEND_STRICT=1 python -m pytest -q tests/engine/test_team_*.py
```

### Baton run prompt

```text
Implement Phase 1 of roadmaps/02-agent-team-spinup.md.
Add team readiness diagnostics, strict backend behavior, and a per-team report artifact.
Do not redesign team execution or replace the existing backends.
```

---

## Phase 2 — Improve ownership and conflict quality

### Developer outcome

Team members should receive clearer file-scope contracts, and Baton should catch likely conflicts before they become confusing failures.

### Work items

1. **Add machine-readable file ownership contracts.**
   - Extend team diagnostics/reporting with member-level intended file or path scope.
   - Use existing prompt fields where possible; do not add a new runtime model unless necessary.

2. **Warn on overlapping ownership before dispatch.**
   - If two members have identical or overlapping `allowed_paths` / file-scope text, add a warning.

3. **Add conflict severity.**
   - Current conflict detection is file-overlap based. Keep it, but classify severity:
     - high: same file modified by two implementers,
     - medium: shared config/test files,
     - low: docs or generated artifacts.

4. **Improve conflict messages.**
   - Include affected files, member IDs, agents, and next action.

### Suggested files

```text
agent_baton/core/engine/executor.py
agent_baton/core/engine/team_backends.py
agent_baton/core/engine/planning/utils/phase_builder.py
agent_baton/models/retrospective.py
tests/engine/test_team_conflicts.py
```

### Acceptance criteria

- Team report shows per-member ownership contract.
- Overlap warnings appear before dispatch when ownership is ambiguous.
- Conflict records include severity and actionable details.
- Existing team tests continue to pass.

### Validation commands

```bash
python -m pytest -q tests/engine/test_team_conflicts.py tests/engine/test_team_*.py
```

### Baton run prompt

```text
Implement Phase 2 of roadmaps/02-agent-team-spinup.md.
Improve team member ownership diagnostics and conflict severity using targeted changes only.
Avoid model/schema changes unless a small backward-compatible optional field is clearly necessary.
```

---

## Phase 3 — Add minimum viable team synthesis

### Developer outcome

After a team completes, developers should receive one coherent summary of what the team did, what changed, what remains risky, and whether follow-up review is required.

### Work items

1. **Improve existing synthesis strategies.**
   - Keep `concatenate` and `merge_files`, but format output as a readable team summary instead of a semicolon string.

2. **Implement a minimal `agent_synthesis` fallback.**
   - If true agent dispatch is too much for this phase, create a deterministic synthesis report and mark that agent synthesis was requested but deterministic fallback was used.
   - If small enough, dispatch the configured synthesis agent as a follow-up review step using existing dispatch mechanics.

3. **Add a team summary artifact.**
   - Store final team summary in the execution directory.

4. **Add tests for synthesis output.**
   - Cover concatenate, merge_files, and agent_synthesis fallback.

### Suggested files

```text
agent_baton/core/engine/executor.py
agent_baton/core/engine/dispatcher.py
tests/engine/test_team_synthesis.py
```

### Acceptance criteria

- Team parent outcome is readable Markdown.
- Files changed are deduplicated when strategy requires it.
- Agent synthesis request is not silently ignored.
- Tests cover all synthesis strategies.

### Validation commands

```bash
python -m pytest -q tests/engine/test_team_synthesis.py tests/engine/test_team_*.py
```

### Baton run prompt

```text
Implement Phase 3 of roadmaps/02-agent-team-spinup.md.
Add a minimum viable synthesis report for team steps and tests for all existing synthesis strategies.
Do not introduce a new team runtime.
```

---

## Phase 4 — Make teams inspectable in PMO and docs

### Developer outcome

Developers should be able to inspect team progress, teammate outputs, conflicts, synthesis, and warnings from the PMO UI or CLI without reading raw JSON state.

### Work items

1. **Expose team details in card/execution detail.**
   - Include team members, member status, conflict status, synthesis summary, and team report path.

2. **Add PMO team panel.**
   - Simple read-only panel; no major redesign.

3. **Document team backends.**
   - Explain worktree vs Claude Teams tradeoffs, resumability, nesting, skills/MCP caveats, and strict mode.

4. **Add team smoke test to CI.**
   - At minimum, verify a team plan can be generated and team diagnostics can be rendered.

### Suggested files

```text
agent_baton/api/routes/pmo.py
agent_baton/api/models/responses.py
pmo-ui/src/**
docs/engine-and-runtime.md
docs/orchestrator-usage.md
tests/api/
pmo-ui/src/**/*.test.tsx
```

### Acceptance criteria

- PMO card execution detail displays team members and synthesis summary.
- CLI status exposes team report path.
- Docs clearly state which backend to choose for reliable/resumable execution.

### Validation commands

```bash
python -m pytest -q tests/api tests/engine/test_team_*.py
cd pmo-ui && npm run build && npm run test:run
```

### Baton run prompt

```text
Implement Phase 4 of roadmaps/02-agent-team-spinup.md.
Expose team execution status in API/PMO and document backend tradeoffs.
Keep UI changes small and read-only.
```
