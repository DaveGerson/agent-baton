# Roadmap: General Code Layout, Developer UX, and Release Polish

**Capability goal:** Developers should be able to install, inspect, validate, and operate Baton with fewer surprises. This roadmap deliberately avoids structural refactoring and focuses on quick wins around diagnostics, terminology, packaging, CI smoke coverage, and documentation.

**No-structural-refactor constraint:** Do not split large modules or reorganize package directories in this roadmap. Improve around the edges: commands, docs, tests, packaging manifests, and diagnostics.

---

## Phase 1 — Add a developer-facing doctor and terminology cleanup

### Developer outcome

A developer can run one command and learn whether Baton is installed correctly, whether agents/knowledge/packs are discoverable, whether the PMO UI assets exist, and which optional features are degraded.

### Work items

1. **Add `baton doctor`.**
   - Check Python version, package version, bundled agents, project agents, knowledge packs, assurance packs, PMO UI assets, `bd` availability, git repo status, Claude CLI availability, and writable `.claude/team-context`.
   - Support `--json`.

2. **Clean up terminology.**
   - Standardize `talent-builder` vs `talent-manager`.
   - Standardize `knowledge.yaml` naming.
   - Distinguish knowledge packs vs assurance packs in docs.

3. **Audit package resources.**
   - Report whether bundled agents, references, templates, and PMO static assets are available in a wheel install.
   - Do not change packaging yet unless the fix is trivial.

4. **Add Makefile targets if missing.**
   - Add `lint`, `typecheck`, `doctor`, and `ci-local` as wrappers if tooling is available.

### Suggested files

```text
agent_baton/cli/commands/**/doctor*.py
agent_baton/cli/main.py
pyproject.toml
Makefile
docs/*.md
README.md
tests/cli/test_doctor.py
```

### Acceptance criteria

- `baton doctor` produces human-readable and JSON reports.
- Terminology inconsistencies are cleaned or explicitly aliased.
- Doctor reports missing optional features as warnings, not crashes.

### Validation commands

```bash
python -m pytest -q tests/cli/test_doctor.py
baton doctor
baton doctor --json > /tmp/baton-doctor.json
```

### Baton run prompt

```text
Implement Phase 1 of roadmaps/05-general-developer-ux-and-layout.md.
Add a developer-facing doctor command, terminology cleanup, and package-resource checks.
Do not reorganize directories or split large modules.
```

---

## Phase 2 — Add quick CI and smoke coverage for developer outcomes

### Developer outcome

Developers can trust that core workflows still work after changes: help output, plan creation, agent validation, knowledge loading, PMO UI build, and package build.

### Work items

1. **Add CLI smoke tests.**
   - `baton --help`
   - `baton doctor --json`
   - `baton validate agents`
   - `baton plan` smoke with deterministic fallback path.

2. **Add package build smoke.**
   - Build wheel, install into clean venv, run `baton --help` and `baton doctor`.

3. **Add UI build smoke.**
   - Ensure PMO UI build/test runs in a separate job or release check.

4. **Add planner golden smoke target.**
   - Not exhaustive; just enough to catch import/runtime asset breakage.

### Suggested files

```text
.github/workflows/tests.yml
.github/workflows/release-pypi.yml
Makefile
tests/cli/
tests/packaging/
pmo-ui/package.json
```

### Acceptance criteria

- CI catches package import failures and missing runtime resources.
- UI build is exercised at least on PR or release branches.
- Wheel install smoke proves source checkout is not required for basic use.

### Validation commands

```bash
python -m pytest -q tests/cli tests/packaging
python -m build
cd pmo-ui && npm run build && npm run test:run
```

### Baton run prompt

```text
Implement Phase 2 of roadmaps/05-general-developer-ux-and-layout.md.
Add CI and smoke tests that protect end-user workflows, including wheel install and PMO UI build.
Do not broaden into full module refactoring.
```

---

## Phase 3 — Improve PMO/client operational polish

### Developer outcome

Developers using the PMO UI get clearer errors and a working path when API auth or capability diagnostics are enabled.

### Work items

1. **Centralize PMO API client behavior.**
   - Ensure auth/header injection is consistent.
   - Ensure request timeout and error handling are consistent.
   - Avoid raw `fetch()` calls where the shared request wrapper should be used.

2. **Show capability health in PMO.**
   - Display doctor summary or a subset: agents loaded, knowledge packs loaded, planner hard-gate mode, team backend, PMO API version.

3. **Improve API error display.**
   - Show stable error messages in UI instead of raw exception blobs.

4. **Add tests for API client behavior.**
   - Mock 401/403/500 and timeout.

### Suggested files

```text
pmo-ui/src/api/client.ts
pmo-ui/src/api/types.ts
pmo-ui/src/**
agent_baton/api/routes/**
agent_baton/api/models/responses.py
pmo-ui/src/**/*.test.tsx
tests/api/
```

### Acceptance criteria

- PMO client uses a central request/auth/error path.
- Capability health is visible in UI or an API response consumed by UI.
- UI tests cover auth and error states.

### Validation commands

```bash
python -m pytest -q tests/api
cd pmo-ui && npm run build && npm run test:run
```

### Baton run prompt

```text
Implement Phase 3 of roadmaps/05-general-developer-ux-and-layout.md.
Improve PMO client consistency and capability health visibility.
Do not redesign the UI or API route layout.
```

---

## Phase 4 — Release and documentation hardening

### Developer outcome

Developers can install Baton, follow docs, validate their project, and run the improved capabilities without guessing which assets or commands are available.

### Work items

1. **Add release checklist.**
   - Build wheel, install wheel, run doctor, run plan smoke, run agent doctor, run knowledge doctor, run PMO UI build.

2. **Update CLI reference.**
   - Include `doctor`, `agents doctor`, `knowledge doctor/search`, team backend diagnostics, and plan diagnostics.

3. **Add “first 15 minutes” developer guide.**
   - Install, run doctor, validate agents, create a plan, inspect diagnostics, execute dry-run, add knowledge pack.

4. **Add examples.**
   - Example plan with knowledge pack.
   - Example generated agent lifecycle.
   - Example team execution report.

### Suggested files

```text
docs/cli-reference.md
docs/getting-started.md
docs/knowledge-packs.md
docs/agent-roster.md
docs/engine-and-runtime.md
.github/workflows/release-pypi.yml
README.md
```

### Acceptance criteria

- Docs cover every new command added by these roadmaps.
- Release workflow or checklist includes package and capability smoke tests.
- A new developer can follow the getting-started guide without using source-only assumptions.

### Validation commands

```bash
mkdocs build --strict || true
python -m build
baton doctor
```

### Baton run prompt

```text
Implement Phase 4 of roadmaps/05-general-developer-ux-and-layout.md.
Harden docs and release checks for the newly improved developer-facing capabilities.
Do not perform structural refactoring.
```
