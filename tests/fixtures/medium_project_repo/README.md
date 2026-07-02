# Medium Project Fixture

A small, real (not stubbed) Python project used as fixture data for the
manager-mode PMO planning end-to-end test
(`tests/e2e/test_manager_mode_planning.py`, PRD Milestone 8).

## Layout

- `app/reporting/` — a reporting service that aggregates task completion
  metrics.
- `app/auth/` — a session/auth stub, kept minimal so the reporting task's
  scope map doesn't accidentally pull auth into scope.
- `tests_fixture/` — unit tests for `app.reporting.service` (named
  `tests_fixture`, not `tests`, so pytest never collects this fixture repo
  as part of the real `agent-baton` suite).
- `.claude/knowledge/coding-conventions/` — one knowledge pack, deliberately
  the only one present so the planning E2E can assert `repo-architecture`
  and `testing-strategy` show up as `missing_packs`.
- `.claude/baton.yaml` — manager-mode config: adversarial review always on,
  phase handoffs required.

## Usage

Not a runnable project on its own. Copied into a temp directory and used
as `project_root` by `IntelligentPlanner.create_plan()` +
`ManagerModePlanner.build_and_write()` in the planning E2E.
