# bd-rm-ux-p1 Implementation Report

Status: DONE

Commit: `a3f83eb2cb08beb6e3f5808e6e8d06c3c9e0d924`

## Summary

Implemented top-level `baton doctor` diagnostics using a uniquely named command module, `agent_baton/cli/commands/diagnostics_cmd.py`, to avoid basename collision with `agent_baton/cli/commands/knowledge/doctor_cmd.py`.

The command supports:

- Human-readable report: `python -m agent_baton.cli.main doctor`
- JSON report: `python -m agent_baton.cli.main doctor --json`

Doctor checks now cover:

- Python version
- Package version
- Bundled agents, including `talent-builder`
- Project agents, with lazy `AgentValidator` diagnostics
- Knowledge packs, using `KnowledgeRegistry` details and canonical `knowledge.yaml`
- Assurance packs, with lazy assurance pack validator details
- PMO UI static asset availability via `pmo-ui/dist/index.html`
- Package-resource audit for bundled agents, references, templates, and PMO static assets
- Optional `bd` CLI availability
- Git repo status
- Optional Claude CLI availability
- Writable `.claude/team-context`
- Canonical terminology report

Optional/missing features report as warnings, not crashes.

## Changed Files

- `agent_baton/cli/commands/diagnostics_cmd.py`
- `tests/cli/test_doctor.py`
- `Makefile`
- `docs/terminology.md`

## Risk Review Adjustments

- Did not create a top-level `doctor_cmd.py`; used `diagnostics_cmd.py`.
- Added discovery coverage proving top-level `doctor` and `knowledge doctor` both register.
- Verified `baton --help` works with the new command present.
- Kept optional checks guarded with lazy imports, `shutil.which`, and bounded subprocess calls.
- Added warning-path coverage for missing PMO dist, `bd`, Claude CLI, knowledge packs, assurance packs, and `.claude/team-context` write failures.
- Added terminology/doc assertions for `talent-builder`, `talent-manager` alias wording, `knowledge.yaml`, and knowledge pack vs assurance pack distinction.

## Verification

All commands were run with:

`C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe`

Results:

- `python -m pytest -q tests\cli\test_doctor.py`
  - Exit 0
  - `6 passed in 0.99s`
- `python -m agent_baton.cli.main doctor`
  - Exit 0
  - Report summary: `ok=8 warnings=5 errors=0`
- `python -m agent_baton.cli.main doctor --json`
  - Exit 0
  - JSON parsed successfully with `ConvertFrom-Json`
- `git diff --check`
  - Exit 0
  - Only Git CRLF conversion notices for touched text files; no whitespace errors

## Expected Warnings In This Worktree

- No assurance packs at `.claude/packs`
- PMO UI source exists but `pmo-ui/dist/index.html` is not built
- References, templates, and PMO static assets are not bundled as importlib package resources
- Git work tree was dirty during doctor verification because implementation files were staged/unstaged before commit
- `.claude/team-context` does not exist in this worktree

## Notes

No structural refactoring was performed. `ExecutionEngine`, PMO routes, storage, planning pipeline, MachinePlan schema, PMO UI architecture, and agent runtime were not modified.

## Fix Review Findings

RED first:

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m pytest -q tests\cli\test_doctor.py`
- Exit: `1`
- Outcome:
  - `6 failed, 4 passed in 0.97s`
  - Failures:
    - missing `planner_validation` check in JSON output
    - `knowledge_packs` stayed `ok` for a pack directory without `knowledge.yaml`
    - `assurance_packs` stayed `ok` with `invalid_count == 1`
    - `project_agents` stayed `ok` when agent validation surfaced `validation_error`

GREEN verification after the fix:

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m pytest -q tests\cli\test_doctor.py`
  - Exit: `0`
  - Outcome: `10 passed in 0.68s`

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m agent_baton.cli.main doctor`
  - Exit: `0`
  - Outcome:
    - `Summary: ok=8 warnings=6 errors=0`
    - Includes `[WARNING] Planner validation: No saved plan is available to validate`

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m agent_baton.cli.main doctor --json`
  - Exit: `0`
  - Outcome:
    - JSON emitted successfully
    - Includes `"id": "planner_validation"` with `"status": "warning"`
    - Details show `"plan_path": null`, `"machine_plan_importable": true`, and `"validator_importable": true`

- Command: `git diff --check`
  - Exit: `0`
  - Outcome:
    - No whitespace errors
    - Git printed line-ending warnings for touched files:
      - `agent_baton/cli/commands/diagnostics_cmd.py`
      - `tests/cli/test_doctor.py`

## Second Fix Review Findings

RED first:

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m pytest -q tests\cli\test_doctor.py -k "task_scoped_saved_plan or malformed_saved_plan_json_shape"`
  - Exit: `1`
  - Outcome:
    - `FF [100%]`
    - `test_doctor_discovers_task_scoped_saved_plan_for_planner_validation`
      failed because planner validation still reported `No saved plan is available to validate`
      when only `.claude/team-context/executions/task-002/plan.json` existed.
    - `test_doctor_reports_structured_error_for_malformed_saved_plan_json_shape`
      failed because `diagnostics_cmd.build_report()` raised `AttributeError: 'str' object has no attribute 'get'`
      out of `_validate_plan()` instead of returning a structured doctor error.

GREEN verification after the fix:

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m pytest -q tests\cli\test_doctor.py`
  - Exit: `0`
  - Outcome: `12 passed in 0.73s`

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m agent_baton.cli.main doctor`
  - Exit: `0`
  - Outcome:
    - `Summary: ok=8 warnings=6 errors=0`
    - Planner validation remained a warning in this worktree because no saved plan exists here.

- Command: `C:\Users\gerso\PycharmProjects\agent-baton-framing-roadmap-beads\.venv\Scripts\python.exe -m agent_baton.cli.main doctor --json`
  - Exit: `0`
  - Outcome:
    - JSON emitted successfully.
    - `planner_validation.details.plan_candidates` now lists the deterministic candidate set from the current worktree scan.

- Command: `git diff --check`
  - Exit: `0`
  - Outcome:
    - No whitespace errors.
    - Git printed LF-to-CRLF working-copy warnings for:
      - `agent_baton/cli/commands/diagnostics_cmd.py`
      - `tests/cli/test_doctor.py`
