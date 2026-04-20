# Agent Baton — Development Guide

This repo contains the source for Agent Baton, a multi-agent orchestration
system for Claude Code.

## Repository Structure

```
agent_baton/       ← Python package (orchestration engine)
docs/              ← Architecture documentation (15 .md files)
agents/            ← Distributable agent definitions (20 .md files)
references/        ← Distributable reference docs (16 .md files)
templates/         ← CLAUDE.md + settings.json + skills/ installed to targets
scripts/           ← Install scripts + record_spec_audit_beads.py
tests/             ← Test suite (~5719 tests, pytest)
pmo-ui/            ← React/Vite PMO frontend (served at /pmo/)
audit-reports/     ← Architecture audit documents (8 reports)
proposals/         ← Design proposals and RFCs (6 documents)
reference_files/   ← Integration questionnaires, roadmaps, analysis docs (ignored)
.claude/           ← Project-specific orchestration setup (ignored)
```

## Key Rules

- `agents/` and `references/` are the **distributable** source of truth.
- `core/engine/` is the execution engine — changes here affect all users.
- `cli/commands/execution/execute.py` contains `_print_action()` — treat as public API.
- All imports use canonical paths (e.g. `from agent_baton.core.govern.classifier import DataClassifier`).

## Agent Roster & Usage

See **[docs/agent-roster.md](docs/agent-roster.md)** for the full roster of 47 agents.
See **[docs/orchestrator-usage.md](docs/orchestrator-usage.md)** for how to use the orchestrator.

## Development

```bash
pip install -e ".[dev]"    # Install in editable mode
pytest                     # Run tests (~5719 tests)
scripts/install.sh         # Re-install globally after editing agents/references
```

### Code Navigation (cymbal)

Use `cymbal investigate <symbol>` to find source, callers, and callees.
Use `cymbal impact <symbol>` before changing high-fanout symbols.

## Token Efficiency (MANDATORY)

- **Prefer file-references over inline tool output.**
- **Trust engine records; don't re-verify.**
- **Default to `baton execute run` for non-INTERACT phases.**
- **Don't re-read files already summarized in plan.md or beads.**

## Autonomous Incident Handling (MANDATORY)

Handle bugs/failures autonomously without pausing:
1. **Bead it.** `baton beads create --type warning ...`
2. **Fix in parallel.** Launch a background subagent on a separate branch.
3. **Require a regression test.**
4. **Continue the main flow.**
