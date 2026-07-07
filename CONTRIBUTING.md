# Contributing to Agent Baton

Thanks for your interest in contributing. This guide covers how to set up
the project, make changes, and submit them for review.

## Development Setup

```bash
git clone https://github.com/DaveGerson/agent-baton.git
cd agent-baton
pip install -e ".[dev]"
pytest                     # Confirm tests pass
```

Python 3.10+ is required.

## Project Layout

| Directory | What it contains |
|-----------|-----------------|
| `agent_baton/` | Python package (orchestration engine, CLI, API) |
| `agents/` | Distributable agent definitions (markdown + YAML frontmatter) |
| `references/` | Shared knowledge documents read by agents at runtime |
| `templates/` | Files installed into target projects |
| `tests/` | pytest test suite |
| `docs/` | Architecture documentation |
| `pmo-ui/` | React/Vite PMO frontend |
| `scripts/` | Install scripts (bash + PowerShell) |

## Making Changes

### Code changes (`agent_baton/`)

1. Create a feature branch from `master`.
2. Write or update tests for your changes.
3. Run `pytest` and confirm all tests pass.
4. If you change CLI command names, `_print_action()` output format, or
   the execution state schema, read `docs/invariants.md` first -- these
   are the protocol contract between Claude and the engine.

### Agent or reference changes (`agents/`, `references/`)

These files are distributed to every user who installs Agent Baton.
Changes here have broad impact:

1. Validate definitions: `baton validate agents/`
2. Test with a real orchestration run if possible.
3. Keep agent definitions focused -- broad agents are less effective than
   specialists with clear scope.

### Documentation changes

Update the relevant docs when your change affects:

- `docs/architecture.md` -- package layout, dependency graph
- `docs/design-decisions.md` -- add an ADR for non-obvious decisions
- `docs/invariants.md` -- CLI surface, output format, state schema
- `README.md` -- user-facing overview
- `CLAUDE.md` -- developer guide

## Commit Messages

Write clear, concise commit messages. Use the imperative mood:

```
Add request logging middleware to FastAPI app
Fix phase-skipping bug in gate result recording
Remove deprecated --summary flag from execute record
```

## Pull Requests

- Keep PRs focused on a single concern.
- Include a short description of what changed and why.
- Reference any related issues.
- Ensure all tests pass before requesting review.

## Code Style

- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_CASE`
- Imports use canonical sub-package paths
  (e.g., `from agent_baton.core.govern.classifier import DataClassifier`)
- No backward-compatibility shims -- if something is unused, remove it.

## Testing

```bash
pytest                     # Run all tests
pytest tests/test_engine.py  # Run a specific test file
pytest -x --tb=short      # Stop on first failure, short traceback
```

`pytest` (no arguments) excludes the slow packaging smoke test
(`tests/packaging/`, marked `packaging`) via `addopts` in `pyproject.toml`.
Run it explicitly when touching packaging (`pyproject.toml`, bundled
resources, entry points):

```bash
pip install build
pytest -q -m packaging tests/packaging/
```

### `make` targets

The `Makefile` wraps the common local checks (each auto-installs into a
`.venv/` on first use):

```bash
make test            # pytest tests/ -q
make test-packaging  # wheel build + fresh-venv install smoke (see above)
make lint            # ruff, if installed
make typecheck        # mypy, if installed
make doctor           # baton doctor
make ci-local         # lint + typecheck + test + doctor
make validate         # baton validate agents/
```

CI (`.github/workflows/tests.yml`) mirrors this split: the full pytest
suite, packaging smoke, and the PMO UI build/tests run as separate jobs.

## Reporting Issues

Open an issue on GitHub with:

1. What you expected to happen
2. What actually happened
3. Steps to reproduce
4. Your Python version and OS

## License

License terms are pending. Contact the maintainers for details.
