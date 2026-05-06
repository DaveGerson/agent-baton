# Target Information Architecture for Agent Baton Documentation

> Authority: this document is the writer's contract. It implements the rules in `/home/djiv/PycharmProjects/orchestrator-v2/docs/internal/doc-guiding-principles.md`. Where the principles doc made a call, this IA carries it through verbatim.

## 1. Target tree

```
docs/
├── index.md                              # Landing (4-quadrant menu)
├── terminology.md                        # Reference: glossary
├── invariants.md                         # Reference: 3 invariants only
├── agent-roster.md                       # Reference: agents/*.md mirror
├── cli-reference.md                      # Reference: CLI surface (or split)
├── api-reference.md                      # Reference: REST routes mirror
├── baton-engine-bugs.md                  # Reference: known issues
├── architecture.md                       # Explanation: top-level
├── engine-and-runtime.md                 # Explanation: engine subsystem
├── governance-knowledge-and-events.md    # Explanation: govern/knowledge/events
├── observe-learn-and-improve.md          # Explanation: observability + learning
├── storage-sync-and-pmo.md               # Explanation: storage/sync/PMO
├── design-decisions.md                   # Explanation: ADR log
├── finops-chargeback.md                  # Explanation: cost attribution
├── daemon-mode-evaluation.md             # Explanation: historical eval
├── orchestrator-usage.md                 # How-to: orchestrator recipes
├── troubleshooting.md                    # How-to: symptom-keyed
├── architecture/
│   ├── package-layout.md                 # Reference: agent_baton/ map
│   └── state-machine.md                  # Reference: action enum + transitions
├── examples/
│   ├── first-run.md                      # Tutorial: canonical walkthrough
│   └── knowledge-packs/
│       ├── agent-baton.md
│       ├── ai-orchestration.md
│       └── case-studies.md
├── superpowers/                          # (one skill per file, unchanged)
├── specs/                                # (RFC log, unchanged)
└── internal/
    ├── doc-guiding-principles.md         # Authority doc
    ├── doc-ia.md                         # This document
    ├── archive/                          # Date-stamped retired status pages
    ├── pyright-diagnostics-triage.md     # Moved from docs/
    ├── audit/                            # Moved from docs/audit/
    ├── reviews/                          # Moved from docs/reviews/
    └── competitive-audit/                # Moved from docs/competitive-audit/
```

Repo-root files that interact with the IA:

```
README.md                # Adopter pitch + 5-min install (forward link to first-run)
CLAUDE.md                # Agent runtime config (project root)
QUICKSTART.md            # Folded into first-run.md; deleted
llms.txt                 # NEW: agent index (per llmstxt.org)
CONTRIBUTING.md          # Reference + how-to for contributors
SECURITY.md              # Reference: vuln reporting
```

### Per-file table

| Path | Quadrant | Audience | Purpose | Cap |
|------|----------|----------|---------|-----|
| `docs/index.md` | Landing | both | 4-card quadrant menu | 60 |
| `docs/terminology.md` | Reference (glossary) | both | One term per row, alphabetical | 250 |
| `docs/invariants.md` | Reference | both | The three load-bearing invariants, terse | 80 |
| `docs/agent-roster.md` | Reference | agents | Mirror of `agents/*.md` frontmatter | 400 |
| `docs/cli-reference.md` | Reference | both | 1:1 with `agent_baton/cli/commands/` | 2200 |
| `docs/api-reference.md` | Reference | agents | 1:1 with `agent_baton/api/` route modules | 1200 |
| `docs/baton-engine-bugs.md` | Reference | maintainers | Known-issues list | 300 |
| `docs/architecture.md` | Explanation | maintainers | High-level *why*, design philosophy | 500 |
| `docs/engine-and-runtime.md` | Explanation | maintainers | Engine state machine, planner, executor | 500 |
| `docs/governance-knowledge-and-events.md` | Explanation | maintainers | Classifier, policy, knowledge resolver | 500 |
| `docs/observe-learn-and-improve.md` | Explanation | maintainers | Tracing, telemetry, scoring, evolution | 500 |
| `docs/storage-sync-and-pmo.md` | Explanation | maintainers | SQLite, federated sync, PMO, Smart Forge | 500 |
| `docs/design-decisions.md` | Explanation (ADR) | maintainers | One decision per H2 | unbounded |
| `docs/finops-chargeback.md` | Explanation | adopters | Cost attribution model | 300 |
| `docs/daemon-mode-evaluation.md` | Explanation (historical) | maintainers | Historical eval; banner | 400 |
| `docs/orchestrator-usage.md` | How-to | adopters | Task recipes; one task per H2 | 600 |
| `docs/troubleshooting.md` | How-to | adopters | Symptom → Cause → Fix; flat list | 500 |
| `docs/architecture/package-layout.md` | Reference | maintainers | Map of `agent_baton/` packages | 600 |
| `docs/architecture/state-machine.md` | Reference | both | Action enum + transition table | 250 |
| `docs/examples/first-run.md` | Tutorial | adopters | Guaranteed-success walkthrough | 300 |
| `README.md` | Landing | adopters | Pitch + 5-min install + forward link | 250 |
| `CLAUDE.md` | Agent runtime | agents | Project-root agent index | 200 |
| `llms.txt` | Agent index | agents | llmstxt.org-conformant root index | 60 |
| `CONTRIBUTING.md` | Reference + How-to | maintainers | Contributor onboarding | 250 |
| `SECURITY.md` | Reference | adopters | Vuln reporting policy | 60 |

Files explicitly absent (deleted in migration):
- `docs/8fe40a58a84f43c8ad4b7fb082e2b995.txt`
- `docs/internal/INSTALL-PROMPT.md`
- `QUICKSTART.md` (folded into `docs/examples/first-run.md`)

---

## 2. Reading paths

### Path A — First-time human user → first successful baton command

1. `README.md` — pitch + comparison + install commands.
2. `docs/examples/first-run.md` — tutorial top to bottom.
3. Tutorial closes with `baton execute complete` succeeding; trace + retro written.
4. Forward link to `docs/orchestrator-usage.md` for next tasks.

### Path B — Returning human looking up a thing

1. `docs/index.md` — pick the quadrant.
2. Quadrant entry point:
   - "How do I X?" → `docs/orchestrator-usage.md` or `docs/troubleshooting.md`.
   - "What does flag/command Y do?" → `docs/cli-reference.md` (anchor).
   - "Why does Baton work this way?" → `docs/architecture.md` → drilldown.
3. Resolve at the H2 anchor. Stop.

### Path C — AI agent dispatched into the codebase

1. `CLAUDE.md` (project root) — mandatory rules + env-var table.
2. `llms.txt` — pick the topic.
3. Target page: `references/baton-engine.md`, `references/agent-routing.md`, `references/baton-patterns.md`, `references/guardrail-presets.md`, `docs/cli-reference.md#<command>`, `docs/agent-roster.md`.
4. If invoking a specialist: `agents/<name>.md`.

The agent path **never** routes through `docs/index.md`, `README.md`, or any explanation page.

---

## 3. Cross-linking rules

### Required links

| From | To | Where |
|------|-----|-------|
| `README.md` | `docs/examples/first-run.md` | "Get started" section |
| `docs/index.md` | One canonical page per quadrant | The 4 cards |
| `docs/examples/first-run.md` | `orchestrator-usage.md`, `cli-reference.md`, `architecture.md` | Footer "Where to go next" |
| `docs/orchestrator-usage.md` (each recipe) | `cli-reference.md` anchor | Inline at first command mention |
| `docs/troubleshooting.md` (each entry) | Relevant CLI anchor | Inside Fix line |
| `docs/architecture.md` | Subsystem explanation pages | One link per subsystem H2 |
| Each explanation page | `design-decisions.md#<adr-anchor>` | "Why this design" section |
| `docs/cli-reference.md` | (no body outbound links) | — |
| `CLAUDE.md` | `agent-roster.md`, `orchestrator-usage.md`, `references/baton-engine.md` | Inline at first mention |
| `llms.txt` | All canonical entry points (stable URLs) | Body |

### Anchor naming convention

- GitHub auto-slug from heading text. Heading = anchor contract.
- CLI commands: `## baton <command> <subcommand>` → `#baton-command-subcommand`. Lowercase. Match `--help`.
- Symbols: `## ExecutionState.advance` → `#executionstateadvance`. Use literal Python name.
- Concepts: noun-phrase, sentence-case (`## How the planner classifies risk`).

### "See also" footer policy

- **Tutorials**: required footer with (1) how-to recipe, (2) reference, (3) explanation. Exactly three.
- **How-to guides**: footer with related how-tos + reference. Up to four.
- **Reference pages**: NO body "See also". Front-matter "See also" allowed (≤3).
- **Explanation pages**: footer to (1) ADR, (2) reference, (3) how-to. Up to four.

---

## 4. `llms.txt` spec

File: `/llms.txt` (repo root). Also at GitHub Pages root.

```text
# Agent Baton

> Multi-agent orchestration system for Claude Code. Plans tasks,
> dispatches specialist agents, enforces QA gates, persists state for
> crash recovery. Python engine + CLI + agent definitions. Local-only;
> the only required external dependency is Claude Code itself.

Baton's contract with Claude is the CLI: `baton plan` produces a saved
plan, `baton execute` drives the loop, agents read reference procedures
inline, the engine owns persistence, gating, tracing, and learning.

## Docs

- [README](https://github.com/DaveGerson/agent-baton/blob/master/README.md): Project pitch and 5-minute install
- [Documentation home](https://davegerson.github.io/agent-baton/): Landing page with 4-quadrant menu
- [First-run tutorial](https://davegerson.github.io/agent-baton/examples/first-run/): Guaranteed-success end-to-end walkthrough
- [Orchestrator usage](https://davegerson.github.io/agent-baton/orchestrator-usage/): Task recipes (how-to)
- [CLI reference](https://davegerson.github.io/agent-baton/cli-reference/): Every `baton` subcommand
- [REST API reference](https://davegerson.github.io/agent-baton/api-reference/): FastAPI route modules
- [Agent roster](https://davegerson.github.io/agent-baton/agent-roster/): All specialist agent definitions
- [Architecture](https://davegerson.github.io/agent-baton/architecture/): Top-level design
- [Terminology](https://davegerson.github.io/agent-baton/terminology/): Canonical glossary
- [Invariants](https://davegerson.github.io/agent-baton/invariants/): The three load-bearing invariants
- [Troubleshooting](https://davegerson.github.io/agent-baton/troubleshooting/): Symptom-keyed fix list

## Reference procedures (agent-facing)

- [references/baton-engine.md](https://github.com/DaveGerson/agent-baton/blob/master/references/baton-engine.md): CLI + engine protocol contract
- [references/agent-routing.md](https://github.com/DaveGerson/agent-baton/blob/master/references/agent-routing.md): Router selection logic
- [references/guardrail-presets.md](https://github.com/DaveGerson/agent-baton/blob/master/references/guardrail-presets.md): Risk-tier guardrails
- [references/baton-patterns.md](https://github.com/DaveGerson/agent-baton/blob/master/references/baton-patterns.md): Reusable orchestration patterns
- [references/task-sequencing.md](https://github.com/DaveGerson/agent-baton/blob/master/references/task-sequencing.md): Phase ordering and dependencies
- [references/knowledge-architecture.md](https://github.com/DaveGerson/agent-baton/blob/master/references/knowledge-architecture.md): Knowledge pack design

## Optional

- [Design decisions (ADR log)](https://davegerson.github.io/agent-baton/design-decisions/): History of architectural calls
- [Engine and runtime](https://davegerson.github.io/agent-baton/engine-and-runtime/): Engine internals
- [Governance, knowledge, and events](https://davegerson.github.io/agent-baton/governance-knowledge-and-events/): Classifier, policy, knowledge resolver
- [Observe, learn, improve](https://davegerson.github.io/agent-baton/observe-learn-and-improve/): Tracing, scoring, learning automation
- [Storage, sync, and PMO](https://davegerson.github.io/agent-baton/storage-sync-and-pmo/): SQLite, federated sync, PMO
- [Architecture package layout](https://davegerson.github.io/agent-baton/architecture/package-layout/): `agent_baton/` package map
```

Rules:
1. Exactly one H1: project name.
2. Exactly one blockquote, 2–3 sentences.
3. H2s named `Docs`, `Reference procedures (agent-facing)`, `Optional`.
4. Every link is a stable absolute URL.
5. The `Optional` H2 is the contract for "skip when context is tight".
6. Do **not** ship `llms-full.txt` until a consumer demands it.

---

## 5. CLAUDE.md vs llms.txt vs index.md

| File | Consumer | Read when | Contains | Does NOT contain |
|------|----------|-----------|----------|------------------|
| `llms.txt` | External LLM that has never seen this repo | At first cold lookup | Project name, 2–3 sentence summary, sorted list of stable URLs | Behavioural rules, env vars, code snippets |
| `CLAUDE.md` | Claude Code agent inside this repo | Every session start | Mandatory rules, env-var table, key-file pointers, autonomous-incident protocol | Marketing pitch, install instructions, full CLI reference |
| `docs/index.md` | Human visiting Pages | After landing on docs site | One paragraph + 4 cards (Tutorial / How-to / Reference / Explanation) | Code samples, env-var tables, full pitch |

Caps: `CLAUDE.md` 200 lines (project root); `templates/CLAUDE.md` 100 lines; `llms.txt` 60; `docs/index.md` 60.

If a fact appears in two of these, delete it from one and link.

---

## 6. Migration mapping

| Current path | Action | New location |
|--------------|--------|--------------|
| `docs/index.md` | Trim to 4-card menu | `docs/index.md` |
| `docs/examples/first-run.md` | Keep; CI-test | unchanged |
| `docs/orchestrator-usage.md` | Keep; remove explanation creep | unchanged |
| `docs/cli-reference.md` | Keep as one file if TOC works; else split | `docs/cli-reference.md` |
| `docs/api-reference.md` | Keep | unchanged |
| `docs/agent-roster.md` | Auto-generate from `agents/*.md` frontmatter | unchanged |
| `docs/terminology.md` | Keep; alphabetise | unchanged |
| `docs/architecture.md` (1979 lines) | Split | `architecture.md` + `architecture/package-layout.md` + `architecture/state-machine.md` |
| `docs/engine-and-runtime.md` | Keep | unchanged |
| `docs/governance-knowledge-and-events.md` | Keep | unchanged |
| `docs/observe-learn-and-improve.md` | Keep | unchanged |
| `docs/storage-sync-and-pmo.md` | Keep | unchanged |
| `docs/design-decisions.md` | Keep; date-stamped H2s | unchanged |
| `docs/invariants.md` | Trim — three invariants only; rationale → `architecture.md` | unchanged |
| `docs/troubleshooting.md` | Keep; enforce Symptom/Cause/Fix table | unchanged |
| `docs/finops-chargeback.md` | Keep | unchanged |
| `docs/daemon-mode-evaluation.md` | Keep with "historical evaluation" banner | unchanged |
| `docs/baton-engine-bugs.md` | Keep | unchanged |
| `docs/PRODUCTION_READINESS.md` | Date-stamp & keep, or archive | possibly `docs/internal/archive/` |
| `docs/pyright-diagnostics-triage.md` | Move | `docs/internal/pyright-diagnostics-triage.md` |
| `docs/audit/*` | Move | `docs/internal/audit/*` |
| `docs/reviews/*` | Move | `docs/internal/reviews/*` |
| `docs/competitive-audit/*` | Move | `docs/internal/competitive-audit/*` |
| `docs/superpowers/*` | Keep | unchanged |
| `docs/specs/*` | Keep | unchanged |
| `docs/internal/INSTALL-PROMPT.md` | **Delete** | — |
| `docs/8fe40a58a84f43c8ad4b7fb082e2b995.txt` | **Delete** | — |
| `README.md` | Trim; collapse install to 5 lines + 1 forward link | unchanged |
| `QUICKSTART.md` | **Delete**; content lives in `docs/examples/first-run.md` | — |
| `CLAUDE.md` (root) | Cap at 200 lines | unchanged |
| `llms.txt` (root) | **New** | new |

---

## 7. Open questions

1. `cli-reference.md` split threshold — default to single file unless TOC fails.
2. PMO UI documentation — fold into `orchestrator-usage.md` recipe block; no dedicated page.
3. `llms.txt` URL strategy — Pages URLs for `docs/`, `github.com/.../blob/master/` for `references/`.
4. CI tutorial-extractor script — bead it; not a blocker for this rewrite.
