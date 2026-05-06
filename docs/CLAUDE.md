# docs/ — public documentation

Human-facing pages. Cross-cutting rules: [../CLAUDE.md](../CLAUDE.md).

## Audience separation

- `docs/` (this directory) — **public** docs for users and integrators. Published via MkDocs.
- `docs/internal/` — **maintainer-only** working drafts, audit trails, design notes. Not published.

If you're writing for end users, stay in `docs/`. If you're recording a decision, audit, or in-progress design, use `docs/internal/`.

## Diátaxis quadrants

Public docs follow [Diátaxis](https://diataxis.fr/):

- **Tutorials** — guided learning (rare here).
- **How-to** — task-oriented (`orchestrator-usage.md`, `troubleshooting.md`).
- **Reference** — lookup tables (`agent-roster.md`, `cli-reference.md`, `api-reference.md`, `terminology.md`, `invariants.md`).
- **Explanation** — conceptual background (`architecture.md`, `engine-and-runtime.md`, `design-decisions.md`, `governance-knowledge-and-events.md`, `observe-learn-and-improve.md`, `storage-sync-and-pmo.md`, `finops-chargeback.md`).

The full matrix is in [internal/doc-guiding-principles.md](internal/doc-guiding-principles.md).

## Layout

| Path | Contents |
|------|----------|
| `architecture.md` + `architecture/` | High-level + technical design, package layout, state machine |
| `agent-roster.md` | Roster of all agents in `/agents/` with capabilities |
| `cli-reference.md` | Reference for every `baton <command>` |
| `api-reference.md` | Reference for every REST route |
| `examples/` | Runnable examples |
| `specs/` | Public spec documents |
| `reviews/` | Published reviews |
| `competitive-audit/` | Comparative analyses |
| `superpowers/` | (gitignored content area, see `.claudeignore`) |
| `internal/` | Maintainer-only — audit trails, working drafts, doc IA |

## When you change code

Update the matching doc **before** merging. The map:

| Code change | Doc to update |
|-------------|---------------|
| New CLI command or flag | `cli-reference.md` |
| New REST route | `api-reference.md` |
| New or removed agent | `agent-roster.md` |
| State machine / protocol | `architecture/state-machine.md` and `engine-and-runtime.md` |
| Module reorg | `architecture/package-layout.md` |
| New design decision | `design-decisions.md` (and a note in `internal/doc-audit.md`) |
| User-visible UI/PMO change | `storage-sync-and-pmo.md` |

## Writer's contract

See [internal/doc-guiding-principles.md](internal/doc-guiding-principles.md) and [internal/doc-ia.md](internal/doc-ia.md). Audit trail of doc decisions: [internal/doc-audit.md](internal/doc-audit.md).
