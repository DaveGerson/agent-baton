# tests/ — pytest suite

Cross-cutting rules: [../CLAUDE.md](../CLAUDE.md).

## Discipline (mandatory)

Agents do **not** run the full suite. The engine emits a `GATE` action when a
test sweep is required, and the orchestrator runs it then. Outside of GATE
steps, only run the specific tests that exercise the file you're editing.

## Layout

| Path | Scope |
|------|-------|
| `engine/` | Engine state machine, dispatcher, planner |
| `planning/` | Planner-specific tests |
| `orchestration/` | Routing, registry, agent dispatch |
| `govern/` | Risk classifier, guardrails |
| `improve/` | Retrospectives and learning loops |
| `knowledge/` | Knowledge pack resolution |
| `models/` | Pydantic model behavior |
| `observability/` | Telemetry, OTEL export |
| `release/` | Release-readiness scoring |
| `runtime/` | Subprocess + runtime helpers |
| `specs/` | Spec parsing and lifecycle |
| `storage/` | SQLite persistence, migrations |
| `tenancy/` | Multi-tenant isolation |
| `api/` | FastAPI integration tests (route-level) |
| `cli/` | Click command-level tests |
| `integration/` | End-to-end flows that span multiple subsystems |
| Top-level `test_*.py` | Older tests — keep adding new tests under the matching subdirectory instead |

## Conventions

- Mirror the source path: `agent_baton/core/govern/classifier.py` → `tests/govern/test_classifier.py`.
- Use `pytest` fixtures from `conftest.py`. Don't reinvent app/engine setup per test.
- Tests must be hermetic: no real network, no real Anthropic API calls, no host filesystem assumptions outside `tmp_path`.
- For new bugs: add a regression test (this is mandatory under autonomous incident handling).

## Running

```bash
pytest tests/govern/test_classifier.py        # one file
pytest tests/govern -k classifier             # one area
pytest -x                                     # stop at first failure
```

The full suite is gated to maintainers + CI.
