# CLAUDE.md architecture — what, why, and what we changed

Status: Accepted (2026-05-06)

This is an ELI5 explainer of how `CLAUDE.md` files work in this repo, what
makes a good one, and the specific decisions made when reorganizing them.
Maintainer-only — see [CLAUDE.md](CLAUDE.md) for the rules of this directory.

## ELI5: what is `CLAUDE.md`?

`CLAUDE.md` is a Markdown file that Claude Code loads automatically into the
agent's working context. It's the place to put **rules and warnings** that the
agent needs to follow while working in this codebase — not documentation, not
marketing, not architecture explainers. Think of it as a co-located "house
rules" sign for whoever is editing the code right now (where "whoever" is an
LLM with no memory of your last session).

## How Claude Code loads it

This is the load model based on Anthropic's official docs:

1. **At session start**, Claude Code walks **upward** from the current working
   directory to the repo root, loading every `CLAUDE.md` it finds along the
   way. It loads them in order from root to leaf, so a child file's rules
   override the parent's.
2. **On demand**, when Claude reads a file inside a subdirectory for the first
   time, it loads any `CLAUDE.md` in that subtree it hasn't already loaded.
3. **Sibling files are not loaded.** `agent_baton/api/CLAUDE.md` is invisible
   to a session working in `agent_baton/cli/`.

The practical consequence: every `CLAUDE.md` you add costs context tokens for
sessions that touch that subtree. Add one only when the rules are different
enough from the parent to be worth the cost.

## What makes a good `CLAUDE.md`

These rules come from Anthropic's published guidance, the Claude Code memory
docs, and observed failure modes in real repositories.

### The four-rule short version

1. **Under 200 lines.** Every line you add costs tokens on every relevant
   session. If you can't justify the line, delete it.
2. **Rules and warnings, not documentation.** If the content is "this is how
   our system works," it goes in `docs/`. If it's "agents must do X, must not
   do Y," it goes here.
3. **Don't repeat the parent.** Cross-cutting rules live at the root. A nested
   file should only contain what's *different* about that subtree.
4. **One file per coherent boundary.** A directory either has its own rules or
   it inherits its parent's. Don't split coherent rules across three CLAUDE.md
   files just because the code happens to be in three directories.

### Section structure that works

For a top-level component file, in this order:

- **Component identity** — one sentence on what this directory is for.
- **Layout** — a short table of subdirectories with a one-line role each.
- **Conventions** — the rules. Bullet list, imperative voice.
- **Critical files / public-API surfaces** — call out the things that, if
  broken, break the contract with users or other components.
- **When you change X** — numbered checklist of co-update obligations.
- **Don'ts** — a short list of common mistakes.

For a nested file, drop the redundant sections. Often it's just `Layout` +
`Conventions` + `Don'ts`.

### Anti-patterns to avoid

- **Marketing prose.** "Agent Baton revolutionizes orchestration..." — belongs
  in `README.md`, not here.
- **Architecture explainers.** "The state machine works by..." — belongs in
  `docs/`, not here.
- **Duplicating cross-cutting rules in every nested file.** The root says it.
  Trust inheritance.
- **Going three levels deep.** `pkg/foo/bar/baz/CLAUDE.md` splits guidance
  into fragments nobody can keep in sync.
- **Mixing scopes.** Don't put repo-wide policy in a leaf directory or
  component-specific rules at the root.
- **Stale content.** A file that disagrees with the code is worse than no file
  — agents follow the wrong rule confidently.

## What we did in this repo

Before: a single 130-line root `CLAUDE.md` plus a distributable `templates/CLAUDE.md`.

After: a layered set of files where each level is loaded only when relevant.

```
CLAUDE.md                                 ← cross-cutting rules (root, ~150 lines)
├── agent_baton/CLAUDE.md                 ← package-level: imports, layout
│   ├── api/CLAUDE.md                     ← FastAPI conventions
│   ├── cli/CLAUDE.md                     ← CLI surface
│   │   └── commands/execution/CLAUDE.md  ← protocol surface (don't break the wire)
│   ├── core/CLAUDE.md                    ← engine-internals nav
│   │   ├── engine/CLAUDE.md              ← protocol contract, state machine
│   │   │   └── planning/CLAUDE.md        ← plan-pipeline architecture
│   │   ├── orchestration/CLAUDE.md       ← registry/router/runner
│   │   ├── govern/CLAUDE.md              ← regulated-data, guardrails
│   │   └── storage/CLAUDE.md             ← migrations, no raw SQL elsewhere
│   └── models/CLAUDE.md                  ← Pydantic, persistence rules
├── agents/CLAUDE.md                      ← agent definition format
├── references/CLAUDE.md                  ← reference procedure conventions
├── pmo-ui/CLAUDE.md                      ← React/Vite + backend coupling
├── tests/CLAUDE.md                       ← test layout, GATE-only full suite
├── docs/CLAUDE.md                        ← public-doc Diátaxis quadrants
│   └── internal/CLAUDE.md                ← maintainer-only directory rules
├── scripts/CLAUDE.md                     ← one-shot vs CLI command boundary
└── templates/CLAUDE.md                   ← UNTOUCHED (distributable artifact)
```

### Specific changes

**Root `CLAUDE.md`.** Cleaned up the guiding-principles block (was prefixed
with stray "Can you" + trailing whitespace from a previous edit). Replaced the
flat repository-layout block with a navigation table linking into each
component's file. Fixed a stale reference (`engine/state.py` → `states.py`).
Cross-cutting rules — orchestrator behavior, concurrent agent isolation,
autonomous incident handling, regulated-domain rules, testing discipline, env
vars, doc maintenance — stay at the root because they apply everywhere.

**Top-level component files (11 new).** One per major directory. Each focuses
on what's *different* about that directory: import discipline for
`agent_baton/`, route conventions for `api/`, the `_print_action()` protocol
warning for `cli/`, etc. None duplicate root content; all link back to the
root for cross-cutting rules.

**Second-level files (5 new).** Added only where the sub-component has
substantially different rules from its parent:

| File | Why it earned its own file |
|------|----------------------------|
| `agent_baton/core/engine/CLAUDE.md` | Hosts the protocol surface (`protocols.py`, `states.py`) — protocol-change discipline is too important to leave implicit |
| `agent_baton/core/orchestration/CLAUDE.md` | Distinct rules around registry-as-source-of-truth and deterministic routing |
| `agent_baton/core/govern/CLAUDE.md` | Regulated-data handling rules, override-log invariant, classifier-only-Anthropic boundary |
| `agent_baton/core/storage/CLAUDE.md` | Migration discipline, no-raw-SQL elsewhere, adapter conventions |
| `docs/internal/CLAUDE.md` | Different publication rules — never link from public docs |

**Third-level files (2 new).** Added only where one specific subdirectory has
its own architecture or hosts a public-API surface:

| File | Why it earned its own file |
|------|----------------------------|
| `agent_baton/core/engine/planning/CLAUDE.md` | A multi-stage pipeline with its own `stages/`, `rules/`, `utils/` — the plan-generation architecture is too much to fit inside `engine/CLAUDE.md` |
| `agent_baton/cli/commands/execution/CLAUDE.md` | Hosts `_print_action()` — the protocol-contract warning is co-located with the code that, if changed, breaks the contract |

### What we deliberately did **not** add

- **Per-route or per-middleware files in `api/`** — single-purpose subdirectories adequately covered by the parent.
- **Per-command-group files in `cli/commands/`** beyond `execution/` — most groups follow the parent CLI conventions; adding files would just split a coherent table.
- **Per-engine-subsystem files** for `beads`, `souls`, `speculation`, etc. — these are file-level concepts already grouped in a table inside `engine/CLAUDE.md`.
- **`engine/planning/{rules,stages,utils}/CLAUDE.md`** — three levels deep is the anti-pattern; covered inside `engine/planning/CLAUDE.md` instead.
- **`templates/CLAUDE.md` content changes** — that file is the artifact installed into user projects, not guidance for editing this repo.

## Decision rule for future additions

Before adding a new `CLAUDE.md`, ask:

1. Does this subdirectory have rules that **differ** from the parent's?
2. Are there **public-API surfaces** here that, if broken, break the contract?
3. Is there a **distinct architecture** in this subdirectory worth documenting?
4. Will the rule **catch a real mistake** an agent would otherwise make?

If you can't answer "yes" to at least one, don't add the file.

## Structural finding flagged during this work

While writing the per-component files, one code-organization smell surfaced:

- **`agent_baton/core/observe/` vs `agent_baton/core/observability/`** — both
  exist with overlapping naming. The actual split is sensible (observability
  emits/exports; observe consumes/dashboards), but the names are confusable
  enough that contributors are likely to file code in the wrong one. The
  current `core/CLAUDE.md` calls out the split explicitly to mitigate. A
  rename (e.g., `observability/` → `telemetry_export/`, `observe/` →
  `telemetry_consume/`) would remove the trap, but it's a cross-cutting change
  with import implications and should go through a normal refactor task —
  not silently as part of a docs change.

No other structural smells justified action at this time.

## Related references

- [doc-guiding-principles.md](doc-guiding-principles.md) — writer's contract for public docs.
- [doc-ia.md](doc-ia.md) — public-docs information architecture.
- [doc-audit.md](doc-audit.md) — running audit trail of doc decisions.
