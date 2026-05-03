# scripts/ — install scripts and one-shot utilities

Standalone scripts. Cross-cutting rules: [../CLAUDE.md](../CLAUDE.md).

## Files

| Script | Purpose | When to touch |
|--------|---------|---------------|
| `install.sh`, `install.ps1` | User-facing installers — fetch the wheel and bootstrap a project | Update when install UX changes; keep `.sh` and `.ps1` in sync |
| `build_dist.sh` | Build the distributable wheel | Update when packaging changes |
| `sync_bundled_agents.sh` | Mirror `/agents/` → `agent_baton/_bundled_agents/` | Run after editing any `agents/*.md` |
| `record_spec_audit_beads.py` | One-shot — record spec-audit findings as beads | Maintenance only |
| `file_extreme_scale_beads.py` | One-shot — file beads for extreme-scale findings | Maintenance only |
| `file_strategic_gaps.py` | One-shot — file beads for strategic gaps | Maintenance only |
| `migrate_beads_to_notes.py` | One-shot migration from old bead format to notes | Already executed; keep for replay |
| `update_velocity_beads.py` | One-shot — refresh velocity beads | Maintenance only |
| `baton-notes-merge`, `baton-notes-merge.py` | Merge helper for `.claude/team-context/` notes across worktrees | Update when worktree layout changes |

## Conventions

- One-shot maintenance scripts are not part of the runtime. Don't import them from `agent_baton/`.
- Scripts that must work on a user's machine (`install.*`) avoid Python dependencies beyond the standard library.
- Bash scripts use `set -euo pipefail` and quote all expansions.
- PowerShell scripts use `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'`.

## Adding a script

1. Decide if it's truly a one-shot or if it should be a `baton` subcommand. Recurring or user-facing: it's a CLI command, not a script.
2. Add the script with execute bit set.
3. Document it in this file's table.
