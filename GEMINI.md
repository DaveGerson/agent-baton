# GEMINI.md — agent runtime config for orchestrator-v2

This file contains mandatory instructions for Gemini CLI agents working in this repository.

## Repository layout (load-bearing)

```
agent_baton/       Python package (the orchestration engine)
agents/            Distributable agent definitions (33 .md)
references/        Distributable reference procedures (18 .md)
templates/         CLAUDE.md + settings.json + skills/ — installed to targets
scripts/           install.sh, install.ps1, record_spec_audit_beads.py
tests/             pytest suite
pmo-ui/            React/Vite frontend served at /pmo/
docs/              Public documentation (Diátaxis quadrants)
docs/internal/     Maintainer-only docs (audit, reviews, working drafts)
.claude/           Project-specific orchestration setup (not committed)
```

## Key files (public APIs)

- `agent_baton/cli/commands/execution/execute.py` — `_print_action()` is the protocol surface.
- `agent_baton/core/engine/state.py` — execution state machine.
- `agent_baton/core/engine/protocols.py` — `ExecutionDriver` interface.
- `agent_baton/models/execution.py` — `ActionType` enum and `ExecutionState`.
- `agent_baton/api/` — FastAPI routers.

## Orchestrator behavior (mandatory)

When invoked as the orchestrator, you MUST drive tasks through the `baton` CLI:

1. **Plan:** `run_shell_command(command='baton plan "<task>" --save --explain')`.
   - The engine handles routing, risk, and sequencing.
   - Plan is saved to `.claude/team-context/plan.json`.
2. **Start:** `run_shell_command(command='baton execute start')`.
   - Returns the first action (e.g., DISPATCH, GATE).
3. **Loop:** Iterate through actions until completion.
   - Record results: `baton execute record --output "..."`
   - Handle gates: `baton execute gate --passed/--failed`
4. **Finalize:** `baton execute complete`.
5. **Resume:** Use `baton execute resume` if a session is interrupted.

## Code navigation & Analysis

- **Symbol Lookup:** Use `run_shell_command(command='cymbal investigate <symbol>')` for source and call graphs.
- **Impact Analysis:** Use `run_shell_command(command='cymbal impact <symbol>')` before making breaking changes.
- **Search:** Use `grep_search` or `glob` for general text searches, but prefer `cymbal` for code semantics.

## Efficiency & Tool Usage (mandatory)

- **Token Economy:** 
  - Do NOT re-read files summarized in `plan.md`.
  - Trust `baton`'s internal state; don't re-verify facts the engine has already recorded.
  - Prefer surgical `replace` calls over `write_file` for existing code.
- **Parallelism:** Utilize Gemini's ability to run tool calls in parallel for independent tasks (e.g., searching and reading multiple files). Use `wait_for_previous=true` ONLY when sequential execution is required by state dependencies.
- **Surgical Edits:** Provide sufficient context in `old_string` to ensure unambiguous replacement.

## Autonomous Incident Handling

1. **Bead it:** If a non-blocking bug or warning is encountered, use `baton beads create --type warning --message "..."`.
2. **Fix in Parallel:** For blocking issues, launch a sub-agent on a separate branch if possible, or address it immediately with a regression test.

## Documentation & Testing

- **Docs:** Update `docs/` (Architecture, API, CLI reference) after any structural change.
- **Tests:** Unit tests are required for every fix or feature. Run them via `pytest`.
- **Commits:** Do NOT commit unless explicitly asked. Propose a commit message that follows project style.

## Environment & Secrets

- Never log or print `ANTHROPIC_API_KEY` or other credentials.
- Adhere to `BATON_APPROVAL_MODE=local` unless instructed otherwise.
