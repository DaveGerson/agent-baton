# GSD Framework

**GSD (Get Stuff Done)** is a standalone productivity framework that shares this repository for development convenience. It is a separate product from `agent_baton/`.

## Non-integration disclaimer

GSD and `agent_baton/` are co-located but **intentionally decoupled**:

- GSD has zero runtime coupling with `agent_baton/`. No imports, no shared state, no shared schemas.
- Do NOT add imports of `agent_baton.*` from GSD code, or imports of GSD from `agent_baton.*`.
- GSD's hooks, references, templates, and workflows stand alone. They are installed independently.
- A repo split (proposal 007 action A8) is planned but not yet scheduled. Until then, treat the boundary as hard: a change in `agent_baton/` must not require a change in `framework/gsd/`, and vice versa.

## Contents

| Path | Purpose |
|------|---------|
| `hooks/` | Shell/Python hooks for GSD workflow stages |
| `references/` | GSD-specific reference procedures |
| `templates/` | Project templates for GSD-managed work |
| `workflows/` | Workflow definitions (YAML/JSON) |
| `gsd-file-manifest.json` | Install manifest listing files copied to target projects |

## Installation

GSD assets are installed separately from agent-baton. See the GSD workflow definitions in `workflows/` for the intended install path.

## Repo split

Proposal 007 (A8) calls for splitting GSD into its own repository. Until that happens:
- All GSD development happens in `framework/gsd/`.
- The `agent_baton/` test suite does not cover GSD code.
- GSD CI (if any) is configured independently of `agent_baton/`'s `tests/` directory.
