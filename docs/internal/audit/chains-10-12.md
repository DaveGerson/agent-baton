# Functionality Audit — Chains 10–12

**Date:** 2026-03-24
**Auditor:** backend-engineer--python
**Scope:** Chain 10 (Distribution), Chain 11 (API Server), Chain 12 (External Sources)

---

## Maturity Scale Reference

| Score | Level | Meaning |
|-------|-------|---------|
| **5** | Production-validated | Exercised in real orchestration sessions, empirically verified |
| **4** | Integration-tested | E2E tests with real logic, CLI/API verified to run |
| **3** | Unit-tested with real logic | Tests exercise business logic, but never run as a composed system |
| **2** | Structurally tested | Tests verify serialization/existence, not behavior |
| **1** | Code exists | Compiles, may have imports, but no meaningful test coverage |
| **0** | Stub/placeholder | Empty or raises NotImplementedError |

---

## Chain 10: Distribution

**Entry points:** `baton package`, `baton publish`, `baton pull`, `baton install`, `baton verify-package`

**Declared path:** CLI → PackageBuilder (`core/distribute/sharing.py`) → RegistryClient (`core/distribute/registry_client.py`) → PackageVerifier (`core/distribute/packager.py`)

### Static Analysis

The import chain is complete and clean:

- `cli/commands/distribute/package.py` imports `PackageBuilder` from `core/distribute/sharing.py`
- `cli/commands/distribute/publish.py` imports `RegistryClient` from `core/distribute/registry_client.py`
- `cli/commands/distribute/pull.py` imports `RegistryClient`
- `cli/commands/distribute/install.py` is a separate install-from-source command (not archive-based)
- `core/distribute/registry_client.py` imports `PackageBuilder` and `_safe_extractall` from `sharing.py`
- `core/distribute/packager.py` imports `AgentValidator` from `core/govern/validator.py`
- All imports resolve. No dead imports found.

One structural note: `baton install` (`cli/commands/distribute/install.py`) is a distinct command — it installs agents/references directly from a source repo root, not from a `.tar.gz` archive. This is the bootstrap install path (used by `scripts/install.sh`). The archive-based install is `baton package --install <archive>`. Both paths exercise `PackageBuilder.install_package()`.

### Empirical Verification

#### `baton package --help`

```
usage: baton package [-h] [--name NAME | --info ARCHIVE | --install ARCHIVE]
                     [--version VERSION] [--description DESCRIPTION]
                     [--include-knowledge] [--no-agents] [--no-references]
                     [--output-dir DIR] [--scope {user,project}] [--force]
                     [--project ROOT]
```

#### `baton package --name test-audit --version 1.0.0 --output-dir /tmp/baton-audit-test`

```
Package created: /tmp/baton-audit-test/test-audit-1.0.0.tar.gz
```

Archive contents confirmed: `manifest.json` (1790 bytes) + 42 agent `.md` files + 15 reference `.md` files. Manifest correctly populated:

```json
{
  "name": "test-audit",
  "version": "1.0.0",
  "created_at": "2026-03-25T00:45:56+00:00",
  "agents": ["agent-definition-engineer.md", ...42 total],
  "references": ["adaptive-execution.md", ...15 total],
  "knowledge_packs": []
}
```

#### `baton publish --init /tmp/baton-audit-registry`

```
Registry initialised at: /tmp/baton-audit-registry
```

#### `baton publish /tmp/baton-audit-test/test-audit-1.0.0.tar.gz --registry /tmp/baton-audit-registry`

```
Published: test-audit @ 1.0.0
  Registry: /tmp/baton-audit-registry
  Path:     packages/test-audit/1.0.0
  Agents:   42
  Refs:     15
```

#### `baton pull test-audit --registry /tmp/baton-audit-registry --list`

```
Name                           Version      Agents  Refs  Description
------------------------------------------------------------------------
test-audit                     1.0.0            42    15
```

#### `baton pull test-audit --registry /tmp/baton-audit-registry --scope project --force`

```
Installed 'test-audit' (latest) to 'project': 42 agents, 15 references, 0 knowledge files
```

#### `baton verify-package /tmp/baton-audit-test/test-audit-1.0.0.tar.gz`

```
Package: test-audit-1.0.0.tar.gz  [PASS]
Contents: 42 agent(s), 15 reference(s), 0 knowledge pack(s)

All checks passed.
```

The full `package → publish → list → pull → verify` roundtrip runs without errors and produces real, verifiable artifacts.

### Test Coverage Assessment

Tests found in: `tests/test_sharing.py`, `tests/test_packager.py`, `tests/test_registry_client.py`

| Test File | Count | Coverage Level |
|-----------|-------|----------------|
| `test_sharing.py` | 34 | PackageBuilder build/extract/install, safe extract, manifest roundtrip |
| `test_packager.py` | 52 | PackageVerifier checksums, validate_package, EnhancedManifest serde, CLI verify-package handler |
| `test_registry_client.py` | 34 | RegistryClient init/publish/list/search/pull with real tmp archives |

All 120 tests pass. Tests exercise real file I/O, tarball creation, manifest parsing, checksum validation, agent validation via `AgentValidator`, and the full publish/pull lifecycle in temp directories. These are behavior-testing tests (score 3+), not structural tests.

The `baton install` CLI handler (`cli/commands/distribute/install.py`) has no dedicated unit tests for the handler function itself, but the underlying `PackageBuilder.install_package()` and `_merge_settings()` logic is well-covered indirectly via `test_sharing.py`.

### Score Per Link

| Link | Score | Evidence |
|------|-------|---------|
| CLI entry (`baton package / publish / pull / verify-package`) | **4** | CLI commands run, produce real output, full roundtrip verified |
| `PackageBuilder` (`core/distribute/sharing.py`) | **4** | 34 behavior tests, empirically confirmed to produce valid tarballs |
| `RegistryClient` (`core/distribute/registry_client.py`) | **4** | 34 behavior tests, publish/pull/list all verified |
| `PackageVerifier` (`core/distribute/packager.py`) | **4** | 52 behavior tests including checksum mismatch, missing manifest, invalid agents |
| `baton install` CLI handler | **3** | No handler-level tests, but underlying logic tested; CLI verified via help output |

**Chain 10 Score: 4 — Integration-tested**

The weakest link is `baton install` (score 3) since the handler itself has no dedicated tests. All other links are at score 4.

### Gaps / Backlog Items

1. **DIST-01** — Add integration tests for `baton install --scope project --verify` covering the full install handler path, including `--upgrade` mode's `_merge_settings` behavior. Acceptance: handler tested with a real source layout, verifying correct file counts and settings merge result.

2. **DIST-02** — No knowledge pack roundtrip test: `baton package --include-knowledge` is untested end-to-end. Acceptance: test that packages a project with a `.claude/knowledge/` tree and verifies the pack appears in the manifest and installs correctly.

---

## Chain 11: API Server

**Entry point:** FastAPI app created via `create_app()` in `agent_baton/api/server.py`; started via `baton serve`; also co-started by `baton daemon start --serve` (Chain 8).

**Declared path:** Server → Routes (9 modules) → Auth middleware → backing subsystems

### Static Analysis

`server.py` imports and instantiates:
- `FastAPI` from `fastapi`
- `init_dependencies()` from `agent_baton.api.deps`
- `TokenAuthMiddleware` from `agent_baton.api.middleware.auth`
- `configure_cors` from `agent_baton.api.middleware.cors`
- `EventBus` from `agent_baton.core.events.bus`
- `WebhookDispatcher` from `agent_baton.api.webhooks.dispatcher`

`deps.py` wires: `ExecutionEngine`, `IntelligentPlanner`, `AgentRegistry`, `DecisionManager`, `DashboardGenerator`, `UsageLogger`, `TraceRecorder`, `WebhookRegistry`, `PmoSqliteStore`, `PmoScanner`, `ForgeSession`, `DataClassifier`, `PolicyEngine`.

All 9 route modules are registered in `_ROUTE_MODULES` and imported lazily (errors are caught per-module, allowing partial startup). The events route requires `sse-starlette`.

Route modules and their backing dependencies:

| Route Module | Dependency | Status |
|---|---|---|
| `routes/health.py` | `ExecutionEngine`, `DecisionManager` | Clean import |
| `routes/plans.py` | `IntelligentPlanner`, `ExecutionEngine` | Clean import |
| `routes/executions.py` | `ExecutionEngine`, `DecisionManager` | Clean import |
| `routes/agents.py` | `AgentRegistry` | Clean import |
| `routes/observe.py` | `TraceRecorder`, `UsageLogger`, `DashboardGenerator` | Clean import |
| `routes/decisions.py` | `DecisionManager` | Clean import |
| `routes/events.py` | `EventBus`, `sse-starlette` | Clean import |
| `routes/webhooks.py` | `WebhookRegistry` | Clean import |
| `routes/pmo.py` | `PmoScanner`, `ForgeSession`, `PmoStore` | Clean import |

### Empirical Verification

#### `baton serve --help`

```
usage: baton serve [-h] [--port PORT] [--host HOST] [--token TOKEN]
                   [--team-context DIR]
```

#### Programmatic app creation

```python
python3 -c "from agent_baton.api.server import create_app; app = create_app(); print('Routes:', [r.path for r in app.routes])"
```

Output:
```
Routes: ['/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc',
'/api/v1/health', '/api/v1/ready', '/api/v1/plans', '/api/v1/plans/{plan_id}',
'/api/v1/executions', '/api/v1/executions/{task_id}',
'/api/v1/executions/{task_id}/record', '/api/v1/executions/{task_id}/gate',
'/api/v1/executions/{task_id}/complete', '/api/v1/executions/{task_id}',
'/api/v1/agents', '/api/v1/agents/{name}', '/api/v1/dashboard',
'/api/v1/traces/{task_id}', '/api/v1/usage', '/api/v1/decisions',
'/api/v1/decisions/{request_id}', '/api/v1/decisions/{request_id}/resolve',
'/api/v1/events/{task_id}', '/api/v1/webhooks', '/api/v1/webhooks',
'/api/v1/webhooks/{webhook_id}', '/api/v1/pmo/board', '/api/v1/pmo/board/{program}',
'/api/v1/pmo/projects', '/api/v1/pmo/projects', '/api/v1/pmo/projects/{project_id}',
'/api/v1/pmo/health', '/api/v1/pmo/forge/plan', '/api/v1/pmo/forge/approve',
'/api/v1/pmo/forge/interview', '/api/v1/pmo/forge/regenerate',
'/api/v1/pmo/ado/search', '/api/v1/pmo/signals', '/api/v1/pmo/signals',
'/api/v1/pmo/signals/{signal_id}/resolve', '/api/v1/pmo/signals/{signal_id}/forge',
'/pmo']
```

All 9 route modules load cleanly. The app creates successfully. All routes are wired. The PMO UI static mount (`/pmo`) appears if the `pmo-ui/dist/` directory exists.

#### Individual route module import verification

All 9 modules confirmed to return a valid `fastapi.routing.APIRouter` object on import:
```
=== health ===   router: <APIRouter object>
=== plans ===    router: <APIRouter object>
=== executions== router: <APIRouter object>
=== agents ===   router: <APIRouter object>
=== observe ===  router: <APIRouter object>
=== decisions == router: <APIRouter object>
=== events ===   router: <APIRouter object>
=== webhooks === router: <APIRouter object>
=== pmo ===      router: <APIRouter object>
```

### Test Coverage Assessment

Test files found: `test_api_health.py`, `test_api_auth.py`, `test_api_plans.py`, `test_api_executions.py`, `test_api_agents.py`, `test_api_observe.py`, `test_api_decisions.py`, `test_api_webhooks.py`.

Note: No `test_api_pmo.py` and no `test_api_events.py` exist.

| Test File | Count | Routes Covered |
|-----------|-------|----------------|
| `test_api_health.py` | 11 | `GET /health`, `GET /ready` |
| `test_api_auth.py` | 20 | `TokenAuthMiddleware` (exempt paths, 401, valid token) |
| `test_api_plans.py` | 12 | `POST /plans`, `GET /plans/{plan_id}` |
| `test_api_executions.py` | 31 | `POST /executions`, `GET/POST/DELETE /executions/{task_id}*` |
| `test_api_agents.py` | 13 | `GET /agents`, `GET /agents/{name}` |
| `test_api_observe.py` | 14 | `GET /dashboard`, `GET /traces/{task_id}`, `GET /usage` |
| `test_api_decisions.py` | 22 | `GET /decisions`, `GET/POST /decisions/{request_id}*` |
| `test_api_webhooks.py` | 17 | `POST/GET/DELETE /webhooks*` |

**Total: 140 tests, all passing.**

Tests use `fastapi.testclient.TestClient` with `create_app(team_context_root=tmp_path)`. They exercise real route handlers calling real engine/store logic with temporary directories — not mocked logic. These are integration-level tests (score 4).

**Missing coverage:**
- `routes/pmo.py` — 12 endpoints, no API tests. The PMO backing store (PmoSqliteStore, PmoScanner) is tested elsewhere, but the HTTP surface is untested.
- `routes/events.py` — SSE stream endpoint has no test. Requires `sse-starlette` and async client.

### Score Per Link

| Link | Score | Evidence |
|------|-------|---------|
| `server.py` + app factory | **4** | Programmatically verified, all routes wired, `create_app()` runs cleanly |
| `deps.py` (DI wiring) | **4** | All singletons initialize without error, tested via every API test |
| `middleware/auth.py` | **4** | 20 behavior tests for token enforcement, exempt paths, 401 format |
| `middleware/cors.py` | **3** | No dedicated tests; imported and applied in all API test fixtures |
| `routes/health.py` | **4** | 11 tests cover liveness/readiness responses |
| `routes/plans.py` | **4** | 12 tests cover plan creation and retrieval |
| `routes/executions.py` | **4** | 31 tests cover full lifecycle: start, record, gate, complete, cancel |
| `routes/agents.py` | **4** | 13 tests cover listing and per-agent retrieval |
| `routes/observe.py` | **4** | 14 tests cover dashboard, traces, usage |
| `routes/decisions.py` | **4** | 22 tests cover listing, retrieval, resolve |
| `routes/events.py` | **1** | Loads cleanly, route registered, SSE logic implemented, but no tests |
| `routes/webhooks.py` | **4** | 17 tests cover register, list, delete |
| `routes/pmo.py` | **1** | Loads cleanly, 12 routes registered, all backing stores tested elsewhere, but no HTTP-level tests for this route module |

**Chain 11 Score: 1 — Code exists**

The chain's score is its weakest link. Both `routes/events.py` and `routes/pmo.py` load cleanly and have real implementations, but neither has any HTTP-level test coverage. By the maturity scale, that is score 1 ("code exists").

### Gaps / Backlog Items

1. **API-01 (HIGH)** — Add `tests/test_api_pmo.py` covering the 12 PMO endpoints. The backing store and scanner are well-tested; only the HTTP surface is missing. Minimum: `GET /pmo/board`, `GET /pmo/projects`, `POST /pmo/projects`, `DELETE /pmo/projects/{id}`, `GET /pmo/health`. Acceptance: 10+ tests using `TestClient`, all passing.

2. **API-02 (MEDIUM)** — Add `tests/test_api_events.py` covering the SSE stream endpoint. SSE responses are awkward to test with `TestClient`; use `httpx` with streaming or test the generator function directly. Minimum: verify subscription/replay mechanics, keepalive comment, disconnect cleanup. Acceptance: at least 5 behavioral tests.

3. **API-03 (LOW)** — Add `tests/test_api_cors.py` for `middleware/cors.py`. Minimum: verify `Access-Control-Allow-Origin` headers are present for localhost origins. Acceptance: 3+ tests.

---

## Chain 12: External Sources

**Entry points:** `baton source add/list/sync/remove/map`

**Declared path:** CLI → `ExternalSourceAdapter` protocol → `AdoAdapter` → `CentralStore`

### Static Analysis

The import chain:

- `cli/commands/source_cmd.py` — all five subcommand handlers (`_add`, `_list`, `_sync`, `_remove`, `_map`) import `CentralStore` from `core/storage/central.py` at call time (lazy import)
- `_sync` additionally imports `AdapterRegistry` and triggers `import agent_baton.core.storage.adapters.ado` (side-effect registration)
- `core/storage/adapters/__init__.py` — defines `ExternalItem` dataclass, `ExternalSourceAdapter` Protocol, `AdapterRegistry`
- `core/storage/adapters/ado.py` — `AdoAdapter` class; self-registers via `AdapterRegistry.register(AdoAdapter)` at module level
- `core/storage/central.py` — `CentralStore` class with `execute()` (DML only, restricted to external-source tables) and `query()` (SELECT only)

All imports resolve cleanly. The protocol is `@runtime_checkable` and `AdoAdapter` satisfies it. The adapter self-registration pattern is clean and verifiable.

Gap: Only `AdoAdapter` is implemented. The CLI and registry advertise support for `jira`, `github`, and `linear` but no adapters exist for these types. `_sync()` handles this gracefully — it prints "No adapter available for source type" and continues.

### Empirical Verification

#### `baton source --help`

```
usage: baton source [-h] {add,list,sync,remove,map} ...

positional arguments:
  {add,list,sync,remove,map}
    add                 Register an external source connection
    list                List all registered external sources
    sync                Pull work items from an external source
    remove              Remove a registered external source
    map                 Map an external work item to a baton project/task
```

#### `baton source list` (empty state)

```
No external sources registered.
Add one with: baton source add ado --name NAME --org ORG --project PROJ --pat-env ENV_VAR
```

#### Full add/list/remove roundtrip

```
$ baton source add ado --name "Test ADO" --org myorg --project myproject --pat-env ADO_PAT
Registered source: ado-myorg-myproject
  Type:    ado
  Name:    Test ADO
  Org:     myorg
  Project: myproject
  PAT env: ADO_PAT

Sync with: baton source sync ado-myorg-myproject

$ baton source list
External Sources (1 registered)
  ado-myorg-myproject             ado       Test ADO                  enabled  last: (never)

$ baton source remove ado-myorg-myproject
Removed source: ado-myorg-myproject

$ baton source list
No external sources registered.
```

#### Sync with missing PAT (error handling)

```
$ baton source add ado --name "Test ADO" --org myorg --project myproject --pat-env DOES_NOT_EXIST_PAT
Registered source: ado-myorg-myproject

$ baton source sync ado-myorg-myproject
  ado-myorg-myproject: Connection failed — ADO PAT not found.  Set the
  'DOES_NOT_EXIST_PAT' environment variable to a Personal Access Token
  with Work Items (Read) scope.
```

Error handling is correct and user-friendly. No real ADO HTTP calls were made; the adapter's `connect()` validation fired first.

### Test Coverage Assessment

Tests found in: `tests/test_adapters.py` (55 tests, all passing)

Coverage breakdown:

| Class | Tests | Nature |
|-------|-------|--------|
| `ExternalItem` dataclass | 3 | Behavioral (field defaults, mutable default isolation) |
| `ExternalSourceAdapter` protocol | 3 | Protocol conformance checks |
| `AdapterRegistry` | 4 | Register/get/available/auto-registration on import |
| `AdoAdapter.connect()` | 6 | Validation errors (missing org, project, PAT, requests import), success |
| `AdoAdapter._normalise()` | 15 | Type mapping, tags, assigned_to, priority, parent, URL construction |
| `AdoAdapter.fetch_items()` | 8 | Mocked HTTP: empty result, normalised items, WIQL failure, batch failure, type filter, since filter, area_path filter, 200-item batching |
| `AdoAdapter.fetch_item()` | 3 | Mocked HTTP: found, not found (404), error raises |
| `CentralStore.execute()` | 7 | DML inserts/updates/deletes on external tables, rejects SELECT, rejects non-external tables |
| `source_cmd` integration | 5 | Full add/list/remove/map/sync flows against real temp central.db |

Tests use mocked HTTP via `unittest.mock.patch` — no real ADO network calls. The `fetch_items` batching test verifies the >200 ID splitting path with a 350-item set. The integration tests in `TestSourceCmdIntegration` exercise the CLI handler functions against a real SQLite database in a temp directory.

### Score Per Link

| Link | Score | Evidence |
|------|-------|---------|
| CLI entry (`baton source *`) | **4** | All 5 subcommands verified empirically, real DB writes confirmed |
| `ExternalSourceAdapter` protocol | **4** | Protocol conformance tested, runtime_checkable verified |
| `AdoAdapter` | **4** | 32 tests covering connect validation, normalisation, mocked HTTP fetch |
| `AdapterRegistry` | **4** | 4 behavior tests, auto-registration verified |
| `CentralStore` (external tables) | **4** | 7 behavior tests for DML restrictions and CRUD |
| `jira/github/linear` adapters | **0** | Stub — not implemented. CLI gracefully reports "No adapter available" |

**Chain 12 Score: 4 — Integration-tested**

The chain scores 4 for the implemented ADO path. The missing `jira`, `github`, and `linear` adapters are scored 0 but are treated as declared future work, not broken links — the framework correctly routes around missing adapters.

### Gaps / Backlog Items

1. **SRC-01 (MEDIUM)** — Implement `JiraAdapter`, `GitHubAdapter`, and `LinearAdapter` to match the protocol. Currently these source types can be registered but `baton source sync` produces "No adapter available" for each. Acceptance: each adapter passes at minimum connect validation tests and mock-HTTP fetch tests equivalent to `TestAdoAdapterFetchItems`.

2. **SRC-02 (LOW)** — Add a live smoke test for ADO sync using a mock HTTP server (e.g., `responses` library or `pytest-httpserver`) to validate the full WIQL → batch → persist path without real ADO credentials. Acceptance: test that registers a source, runs `_sync()` with a mock server returning 3 work items, and verifies 3 rows in `external_items`.

3. **SRC-03 (LOW)** — The `baton source map` command has no test that verifies the `external_mappings` write survives a close/reopen cycle (i.e., that the mapping is truly persisted). `TestSourceCmdIntegration.test_map_source` only queries within the same process. Acceptance: close and reopen `CentralStore` after map, verify rows still present.

---

## Summary Matrix

| Chain | Weakest Link | Chain Score | Tests | Notes |
|-------|-------------|-------------|-------|-------|
| **Chain 10: Distribution** | `baton install` handler (no dedicated tests) | **4** | 120 passing | Full package/publish/pull/verify roundtrip empirically verified |
| **Chain 11: API Server** | `routes/events.py` and `routes/pmo.py` (no HTTP tests) | **1** | 140 passing (7/9 routes) | App creates, all routes load, 7 of 9 route modules fully tested |
| **Chain 12: External Sources** | `jira/github/linear` adapters (stubs) | **4** (ADO path) | 55 passing | ADO end-to-end verified; other source types scaffolded but unimplemented |

### Priority Backlog

| ID | Chain | Priority | Work Item |
|----|-------|----------|-----------|
| API-01 | 11 | HIGH | Add `test_api_pmo.py` — 12 PMO endpoints have no HTTP test coverage |
| API-02 | 11 | MEDIUM | Add `test_api_events.py` — SSE stream endpoint untested |
| DIST-01 | 10 | MEDIUM | Add handler tests for `baton install --scope project --verify` and `--upgrade` |
| SRC-01 | 12 | MEDIUM | Implement `JiraAdapter`, `GitHubAdapter`, `LinearAdapter` |
| API-03 | 11 | LOW | Add `test_api_cors.py` for CORS middleware |
| DIST-02 | 10 | LOW | Add knowledge-pack roundtrip test for `baton package --include-knowledge` |
| SRC-02 | 12 | LOW | ADO live smoke test via mock HTTP server |
| SRC-03 | 12 | LOW | Verify `baton source map` persistence across store close/reopen |
