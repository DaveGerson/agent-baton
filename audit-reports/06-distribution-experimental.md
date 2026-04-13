# Audit Report: Distribution, Packaging & Experimental Subsystems

**Scope:** `core/distribute/`, `core/engine/experimental/`, `cli/commands/distribute/`, `cli/commands/agents/`, `core/orchestration/`
**Date:** 2026-04-13

---

## Findings

### 1. Distribution Pipeline (package → publish → pull → install) — HALF-BUILT

The pipeline is technically functional end-to-end but has a structural split:

- `sharing.py:114-411` provides `PackageBuilder` with build/extract/install.
- `packager.py:1-400+` provides `PackageVerifier` with checksums, dependency tracking, and validation via a parallel `EnhancedManifest` dataclass.
- **The two are not integrated.** `PackageBuilder.build()` produces archives with `PackageManifest`. `PackageVerifier.validate_package()` reads archives with `EnhancedManifest`. The builder never computes checksums or records dependencies. The verifier never calls the builder.

| File | Issue |
|------|-------|
| `core/distribute/packager.py:59-121` | `EnhancedManifest` duplicates `PackageManifest` fields, adds `checksums`/`dependencies` that nothing populates |
| `core/distribute/sharing.py:60-111` | `PackageManifest` — the version actually used by build/install |

### 2. Registry Client — ASPIRATIONAL (remote); FUNCTIONAL (local)

`RegistryClient` (`registry_client.py:76-81`) accepts a `registry_url` parameter that is stored but **never used anywhere in the codebase**. All operations (`publish`, `pull`, `list_packages`, `search`) require a `registry_path: Path` argument pointing to a local filesystem directory. There is no HTTP client, no remote fetch, no authentication.

| File | Issue |
|------|-------|
| `core/distribute/registry_client.py:78-82` | `registry_url` and `local_cache` are stored but never read |

### 3. Experimental Features — EXPERIMENTAL-LEAK

Despite `experimental/__init__.py:1-2` stating "not yet validated with real usage data," every experimental module is:

- Re-exported from `core/distribute/__init__.py:28-30` into the public API
- Directly imported by production CLI commands:
  - `cli/commands/distribute/transfer.py:16` imports `ProjectTransfer`
  - `cli/commands/agents/incident.py:13` imports `IncidentManager`
  - `cli/commands/execution/async_cmd.py:14` imports `AsyncDispatcher`

These are not experimental — they are production features with an incorrect label.

**AsyncDispatcher** (`experimental/async_dispatch.py`) — Records task intent to disk but never executes anything. The `baton async --dispatch` CLI writes a JSON file and returns. No subprocess launch, no polling, no integration with the execution engine.

**IncidentManager** (`experimental/incident.py`) — Standalone markdown template generator. Creates incident documents but has zero integration with the execution engine. Incidents do not become execution plans; incident phases do not map to engine phases.

### 4. Agent Routing + Learning — FUNCTIONAL ✓

The router at `core/orchestration/router.py:232-252` imports `LearnedOverrides` inside a `try/except` block and consults learned flavor overrides before falling back to the hardcoded `FLAVOR_MAP`. Learning data improves routing without creating a hard dependency.

### 5. Knowledge Registry — FUNCTIONAL ✓

Consumed by:
- `core/engine/planner.py:421-443` (plan-time knowledge attachment)
- `core/engine/knowledge_resolver.py:105` (step-level resolution during execution)
- CLI commands for knowledge management

Well-integrated.

### 6. Transfer Functionality — REDUNDANT

`ProjectTransfer` (`experimental/transfer.py`) duplicates `PackageBuilder` (`sharing.py`) at a lower level of abstraction. Both copy agents, knowledge packs, and references between `.claude/` directories. Both have identical `_copy_file()` static methods (compare `sharing.py:396-401` with `transfer.py:275-280`). Transfer is "package-without-archive."

### 7. Incident Management — UNUSED-INFRA

`IncidentManager` generates markdown files but has no connection to the execution engine. It cannot:
- Create execution plans from incident templates
- Track incident phases through the engine's gate/dispatch lifecycle
- Report incident status to the PMO dashboard

---

## Summary Table

| Category | Component | Files | Recommended Action |
|----------|-----------|-------|--------------------|
| ASPIRATIONAL | Remote registry | `registry_client.py:78-82` | Remove `registry_url`/`local_cache` or implement remote fetch |
| HALF-BUILT | Checksum/dependency pipeline | `packager.py:59-121` | Integrate into `PackageBuilder.build()` or remove |
| EXPERIMENTAL-LEAK | All 3 experimental modules | `distribute/__init__.py:28-30`, CLI imports | Drop the "experimental" label — these are production |
| UNUSED-INFRA | AsyncDispatcher | `experimental/async_dispatch.py` | Wire into execution engine or document as record-only |
| UNUSED-INFRA | IncidentManager | `experimental/incident.py` | Integrate with planner or extract to a separate tool |
| REDUNDANT | `_copy_file()` duplication | `sharing.py:396` vs `transfer.py:275` | Extract to a shared utility |
| REDUNDANT | Transfer vs Package pipeline | `experimental/transfer.py` vs `sharing.py` | Consolidate — transfer is package-without-archive |
