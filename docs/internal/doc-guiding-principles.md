# Documentation Guiding Principles for Agent Baton

> Authority document. Every writer agent that produces or revises Baton
> documentation MUST follow these rules. Reviewers MUST apply the
> checklist in section 9 before merging. When in doubt, the explicit
> calls in this document override personal preference.

This file is the foundational research output for the Baton documentation
overhaul. It cites authoritative sources inline, makes Baton-specific
calls where the source material is silent, and provides ready-to-copy
templates so writer agents do not have to re-derive structure.

Sources cited throughout:

- [Diátaxis](https://diataxis.fr/) — the four-quadrant documentation
  framework (tutorials, how-to, reference, explanation).
- [llms.txt](https://llmstxt.org/) — proposal for a machine-readable
  index file at `/llms.txt`.
- [Google developer documentation style guide](https://developers.google.com/style)
  — voice, tone, terminology, code samples.
- [Anthropic / Claude Code docs](https://docs.anthropic.com/en/docs/claude-code)
  — patterns for tools that themselves wrap an LLM.
- [Stripe API reference](https://docs.stripe.com/api) — three-column
  reference layout, runnable samples, deep linking.
- [Twilio docs](https://www.twilio.com/docs) — task-oriented quickstarts,
  language pickers, persistent code samples.
- [Vercel docs](https://vercel.com/docs) — terse landing pages, sidebar
  hierarchy, side-by-side conceptual + practical pairs.

---

## 1. Audience Model

Baton docs serve **two audiences with non-overlapping reading patterns**.
Every page must be written with both in mind, but it is rarely both at
the same time. Decide which audience a section serves before you write
it. If you cannot decide, split the section.

### 1.1 The two audiences

| Audience | Who they are | What they want | How they read |
|----------|-------------|----------------|---------------|
| **AI agents** (orchestrator, subagents, Claude Code itself) | Claude reading the repo at runtime, looking up the right CLI flag, the right reference procedure, or the right H2 anchor to cite | Unambiguous structure, stable headings, file-path:line refs, machine-parseable indexes, no buried lede | `grep`, `Read`, anchor lookup, `cymbal investigate`, scrolling to a known anchor |
| **End-user developers** | Humans installing or adopting Baton; humans extending Baton; humans reviewing what Baton did | Progressive disclosure: what is it → install → first task → orchestrator → power use → internals | Linear scrolling, table-of-contents jumps, web search, GitHub Pages reading |

A third group exists implicitly: **Baton maintainers**. They read the
same docs but tolerate denser material and prefer file-path:line
references over prose. Treat them as a power-user variant of the
end-user developer.

### 1.2 Recognising the audience signal

When drafting, ask:

1. *Will this page be read in a session by Claude when it is dispatched
   on a task?* If yes, it is **agent-facing** — every H2 must be a noun
   phrase that an LLM can grep. Examples: `agents/orchestrator.md`,
   `references/baton-engine.md`, `CLAUDE.md`, anything under
   `templates/`.
2. *Will this page be read by a human evaluating whether to adopt
   Baton?* If yes, it is **adopter-facing** — the lede must answer
   "should I keep reading?" in two sentences. Examples: `README.md`,
   `docs/index.md`, `docs/examples/first-run.md`.
3. *Will this page be read by a developer extending Baton (writing a
   new agent, adding a CLI command, integrating a new storage
   backend)?* If yes, it is **maintainer-facing** — link to source files
   and tests, not just prose. Examples: `docs/architecture.md`,
   `docs/invariants.md`, `docs/design-decisions.md`, `CLAUDE.md`
   (project root).

### 1.3 What this means in practice

- Pages addressed to agents and pages addressed to humans **can share a
  file** but must not share a paragraph. Use a clearly labelled section
  ("For agents" / "For humans") if you must combine them.
- The orchestrator agent definition (`agents/orchestrator.md`) and the
  reference procedures under `references/` are agent-facing first,
  human-readable second. Do not write to flatter humans; write to be
  parsed by a model that has 200 lines of context budget left.
- The `README.md` and `docs/index.md` are adopter-facing first.
  Maintainers tolerate marketing-adjacent framing here; agents do not
  read these in the dispatch hot path.

### 1.4 Call: target both, but never simultaneously

Do not write "this section is for agents and humans". Pick one. If both
need the same content, write it once and link from each entry point.

---

## 2. Diátaxis Mapping for Agent Baton

The [Diátaxis framework](https://diataxis.fr/) sorts technical writing
along two axes: action vs cognition, acquisition vs application. The
result is four quadrants. Every Baton doc must land in exactly one
quadrant. Pages that "explain a bit, then walk through, then list flags"
are the canonical Diátaxis anti-pattern; split them.

### 2.1 The four quadrants, applied to Baton

| Quadrant | User stance | Question answered | Baton example | Anti-example |
|----------|-------------|-------------------|---------------|--------------|
| **Tutorial** (learning-oriented) | Studying | "Teach me Baton from zero" | `docs/examples/first-run.md` (a guaranteed-to-work walkthrough that ends with a passing test) | Anything labelled "Quickstart" that lists alternatives ("you can also …") |
| **How-to guide** (task-oriented) | Working | "How do I dispatch a team to a single step?" | `docs/orchestrator-usage.md` task recipes; one focused page per task | A how-to that drifts into "this works because …" |
| **Reference** (information-oriented) | Looking up | "What does `baton execute amend` accept?" | `docs/cli-reference.md`, `docs/api-reference.md`, `docs/agent-roster.md`, `references/baton-engine.md` | A reference page that opens with marketing copy or a tutorial intro |
| **Explanation** (understanding-oriented) | Studying away from the keyboard | "Why does Baton use a state machine instead of …?" | `docs/architecture.md`, `docs/design-decisions.md`, `docs/invariants.md`, `docs/engine-and-runtime.md` | An explanation page that turns into a how-to halfway through |

> *"A tutorial is a lesson. A how-to guide is a recipe. A reference is
> a map. An explanation is a discussion."* — paraphrased from
> [Diátaxis](https://diataxis.fr/).

### 2.2 Concrete mapping of existing files

The following table is the **authoritative classification** for every
file currently in `docs/`. Writers MUST keep each file in its assigned
quadrant. If a file violates its quadrant today (because of accumulated
drift), the next edit to that file should remove the off-quadrant
material into a new file rather than leaving it mixed.

| File | Quadrant | Status | Action |
|------|----------|--------|--------|
| `docs/index.md` | Landing (cross-quadrant index) | OK | Keep terse; do not promote to tutorial. |
| `docs/examples/first-run.md` | **Tutorial** | OK | Must always run end-to-end on a fresh checkout. Run it in CI. |
| `docs/examples/knowledge-packs/*` | **Tutorial** (auxiliary) | OK | Keep each pack example self-contained. |
| `docs/orchestrator-usage.md` | **How-to** | OK | Already task-oriented; do not let explanation creep in. |
| `docs/cli-reference.md` | **Reference** | OK | Must mirror `agent_baton/cli/commands/` 1:1. |
| `docs/api-reference.md` | **Reference** | OK | Must mirror `agent_baton/api/` route modules 1:1. |
| `docs/agent-roster.md` | **Reference** | OK | Mirror `agents/*.md` frontmatter. |
| `docs/terminology.md` | **Reference** (glossary) | OK | One term, one definition, alphabetical. |
| `docs/architecture.md` | **Explanation** | Drift risk (1979 lines) | Split: keep `architecture.md` as the high-level discussion; move package layout to a new `architecture/package-layout.md` reference. |
| `docs/engine-and-runtime.md` | **Explanation** | OK | Pair with `architecture.md`; do not duplicate. |
| `docs/governance-knowledge-and-events.md` | **Explanation** | OK | Same. |
| `docs/observe-learn-and-improve.md` | **Explanation** | OK | Same. |
| `docs/storage-sync-and-pmo.md` | **Explanation** | OK | Same. |
| `docs/design-decisions.md` | **Explanation** (ADR log) | OK | One decision per H2; date-stamped; never edit history. |
| `docs/invariants.md` | **Explanation** + **Reference** | Mixed | Keep the three invariants as **reference**; move the rationale prose to `architecture.md`. |
| `docs/troubleshooting.md` | **How-to** (symptom-keyed) | OK | Symptom → Cause → Fix; never narrative. |
| `docs/finops-chargeback.md` | **Explanation** | OK | Pair with a future how-to "How to attribute costs to a project". |
| `docs/daemon-mode-evaluation.md` | **Explanation** | OK | Mark as historical evaluation; do not let users mistake it for usage docs. |
| `docs/baton-engine-bugs.md` | **Reference** (known-issues list) | OK | Auto-generate from issue tracker if possible. |
| `docs/PRODUCTION_READINESS.md` | **Explanation** (status report) | Drift risk | Date-stamp every entry; archive when superseded. |
| `docs/pyright-diagnostics-triage.md` | **Internal** (working doc) | Move | Relocate to `docs/internal/`; not user-facing. |
| `docs/specs/*` | **Explanation** (RFC log) | OK | Treat as design history. |
| `docs/audit/*` | **Internal** | Move | Relocate to `audit-reports/` or `docs/internal/`. |
| `docs/reviews/*` | **Internal** | Move | Same. |
| `docs/competitive-audit/*` | **Internal** | Move | Same. |
| `docs/superpowers/*` | **Reference** (skill catalogue) | OK | One skill per file. |
| `docs/internal/*` | **Internal** | OK | Not published; not indexed for users. |
| `docs/8fe40a58a84f43c8ad4b7fb082e2b995.txt` | **Garbage** | Delete | Stray hash-named file. |

### 2.3 Calls

- **Yes**, split `docs/architecture.md` (1979 lines) into a high-level
  discussion (Explanation) and a package-layout map (Reference). The
  current file violates the quadrant boundary by including both in one
  document, which Diátaxis explicitly warns against.
- **Yes**, treat `docs/cli-reference.md` and `docs/api-reference.md` as
  generated artefacts even if they are hand-written today. Their
  structure must mirror code structure 1:1, and they must be regenerated
  (or at least diffed) on every CLI/API change.
- **No**, do not merge `docs/orchestrator-usage.md` and
  `docs/examples/first-run.md`. The first is how-to (task recipes); the
  second is tutorial (a single guaranteed-success path). They are
  different quadrants.
- **No**, do not create a "guides" or "concepts" top-level folder.
  Diátaxis maps onto the four-quadrant naming directly; introducing a
  fifth bucket dilutes the framework.

### 2.4 Connecting the quadrants

Diátaxis pages cross-link only at well-defined hand-offs:

- A **tutorial** ends by linking to the relevant **how-tos** ("Now that
  you've run the first task, here are recipes for ...") and to the
  **reference** for the commands it used.
- A **how-to** opens by linking to the **reference** for any flag it
  uses, and closes by linking to the **explanation** for the *why*.
- A **reference** page does not link out to tutorials or how-tos in the
  body. It may link in the front-matter ("See also").
- An **explanation** links to the reference for any term it introduces,
  and to the how-to for any task it implies.

---

## 3. Style Rules

These rules apply to every Baton doc unless explicitly overridden in a
specific template. They are derived from the
[Google developer documentation style guide](https://developers.google.com/style)
and adapted for Baton's mixed audience.

### 3.1 Voice and person

- **Use second person.** Address the reader as *you*. Use *we* only
  when describing what the Baton team did or decided in an explanation
  page (e.g., ADRs). Never use *we* in a reference or how-to.
- **Use active voice.** "The planner classifies risk" beats "Risk is
  classified by the planner".
- **Use present tense for behaviour.** "`baton plan` writes
  `plan.json`" — not *will write* or *has written*.
- **Past tense is allowed for design history** (ADRs and explanation
  pages). "We chose a state machine because …" is fine in an ADR;
  remove it from a reference page.

### 3.2 Tone

- Conversational but professional. *Friendly, not chummy.* No emoji in
  prose. No exclamation marks. No marketing adjectives ("powerful",
  "seamless", "lightning-fast" — all banned).
- No hedging about the future. Do not write "in the future Baton will
  …". Either it works today or it is not in the doc.
- No apologies. "Note that this is currently a bit awkward …" — cut.

### 3.3 Naming and terminology

This is the most-violated rule today. Fix it permanently:

- **The product is "Agent Baton".** Use the full name once at the top
  of every adopter-facing page. After that, "Baton" is acceptable. In
  agent-facing pages and reference pages, "Baton" alone is fine.
- **The CLI is `baton`** (lowercase, monospace). Never "the Baton CLI"
  in code samples; just `baton`.
- **The Python package is `agent_baton`** (underscore, monospace) when
  referring to the import path. The distribution name is
  `agent-baton` (hyphen) on PyPI.
- **"Orchestrator"** refers specifically to the orchestrator *agent
  definition* (`agents/orchestrator.md`). Do not use it as a synonym
  for "Baton" or "the engine".
- **"Engine"** refers to the Python state machine in
  `agent_baton/core/engine/`. Do not use it as a synonym for "Baton" or
  "the planner".
- **"Plan"** is the noun for a saved `plan.json`. **"Planner"** is the
  subsystem that produces a plan. Do not write "the plan classifies
  risk"; the planner does.
- **"Agent"** vs **"subagent"**: an *agent* is a definition file in
  `agents/`. A *subagent* is a Claude Code Agent-tool invocation
  spawned at runtime. Reserve "subagent" for runtime; use "agent" for
  the definition.
- **"Task"** vs **"phase"** vs **"step"**: a task is the user-visible
  unit ("add input validation"); a phase is a stage of a plan
  (implement, test, review); a step is the smallest unit of dispatch
  inside a phase. Never use them interchangeably.
- **"Bead"** is the structured-memory record type. Always lowercase
  unless starting a sentence.

Maintain `docs/terminology.md` as the **canonical glossary**. Every term
above MUST appear there, defined once. Other pages link to it. Do not
redefine.

### 3.4 Headings

- **Sentence case for H1 and H2** (`## How the planner classifies risk`,
  not `## How The Planner Classifies Risk`). Code identifiers in a
  heading keep their case (`## baton execute resume`).
- **H2s are noun phrases or imperative verb phrases.** Both are
  greppable; both are stable. Prefer the form Claude is most likely to
  search for.
- **Each H2 has a unique slug across the page.** GitHub Markdown
  auto-generates slugs from the heading text; if two H2s share a slug,
  rename the second.
- **Do not skip levels.** H1 → H2 → H3 only. No H4 unless absolutely
  required (and it usually isn't).
- **Stable IDs.** When a heading is referenced from another doc or from
  CLI output, its slug is part of the contract. Do not rename casually.
  If you must rename, leave a stub with the old anchor and a redirect
  note for one release cycle.

### 3.5 Code samples

- **Every sample must run.** No pseudo-code unless it is fenced as
  ```` ```text ```` and clearly labelled as illustrative.
- **Show the command and one line of expected output** for any CLI
  example used in a tutorial or how-to. Reference pages need only the
  invocation.
- **Prefer real arguments** to placeholders. `baton plan "Add JWT
  auth" --save` beats `baton plan "<your task>" --save`. Real strings
  are scannable; placeholders force a re-read.
- **Fence languages explicitly.** Use ```` ```bash ````, ```` ```python
  ````, ```` ```json ````, ```` ```text ````. Never an unfenced block.
- **No prompts in shell samples.** Write `pip install agent-baton`, not
  `$ pip install agent-baton`. The dollar sign defeats copy-paste.
- **No `&&` chains in tutorials.** A tutorial's reader runs commands
  one at a time; chained commands hide which step failed. Chains are
  fine in how-tos for the already-competent reader.

### 3.6 Links

- **Use descriptive link text.** *"See the [orchestrator usage guide]"*
  beats *"see [here]"*. The target of the link must be obvious from the
  link text alone (LLMs and screen readers both depend on this).
- **Relative paths for in-repo links** (`../cli-reference.md#baton-plan`),
  absolute URLs only for external resources.
- **Cite the source when claiming a fact about an external system.**
  E.g., when describing Diátaxis, link to the relevant
  [Diátaxis](https://diataxis.fr/) page.

### 3.7 Lists and tables

- **Lists are for unordered or sequential enumerations.** Three or more
  items. Two items belong in prose.
- **Tables are for two-or-more-column lookup data.** If every row has
  the same shape, use a table. If rows have variable shape, use H3
  subsections.
- **Sort tables by something.** Alphabetical for glossaries and CLI
  references. Logical workflow order for tutorials and how-tos.
  Random ordering is a smell.

---

## 4. Agent Navigation Aids

Agents reading Baton docs at runtime are constrained by context window
and tool budget. Every doc must be designed so an LLM can locate the
exact section it needs in O(1) — not by reading the whole file.

### 4.1 `llms.txt` for Baton

**Call: yes, ship a `/llms.txt`.** The
[llms.txt convention](https://llmstxt.org/) is a low-cost machine-
readable index that lets external Claude instances (and other LLMs)
discover the Baton docs at inference time. Cost to maintain is small;
benefit is real.

Place at the **repo root** (`./llms.txt`) and at the **GitHub Pages
root** when published. The file conforms to the spec:

```text
# Agent Baton

> Multi-agent orchestration system for Claude Code. Plans tasks,
> dispatches specialist agents, enforces QA gates, persists state
> for crash recovery. Python engine + CLI + agent definitions.

Baton runs locally. The CLI is the contract between Claude and the
Python engine. Agents read reference procedures inline; the engine
owns persistence, gating, and tracing.

## Docs

- [README](https://github.com/DaveGerson/agent-baton/blob/master/README.md): Project overview and 5-minute install
- [docs/index.md](https://davegerson.github.io/agent-baton/): Documentation landing page
- [docs/examples/first-run.md](https://davegerson.github.io/agent-baton/examples/first-run/): Tutorial — first task end-to-end
- [docs/orchestrator-usage.md](https://davegerson.github.io/agent-baton/orchestrator-usage/): How-to recipes for the orchestrator
- [docs/cli-reference.md](https://davegerson.github.io/agent-baton/cli-reference/): Full CLI reference
- [docs/api-reference.md](https://davegerson.github.io/agent-baton/api-reference/): REST API reference
- [docs/agent-roster.md](https://davegerson.github.io/agent-baton/agent-roster/): All 47 agent definitions
- [docs/architecture.md](https://davegerson.github.io/agent-baton/architecture/): System design and engine internals
- [docs/terminology.md](https://davegerson.github.io/agent-baton/terminology/): Canonical glossary
- [docs/invariants.md](https://davegerson.github.io/agent-baton/invariants/): Three load-bearing system invariants

## Reference procedures (agent-facing)

- [references/baton-engine.md](https://github.com/DaveGerson/agent-baton/blob/master/references/baton-engine.md): CLI + protocol contract
- [references/agent-routing.md](https://github.com/DaveGerson/agent-baton/blob/master/references/agent-routing.md): Router selection logic
- [references/guardrail-presets.md](https://github.com/DaveGerson/agent-baton/blob/master/references/guardrail-presets.md): Risk-tier guardrails
- [references/baton-patterns.md](https://github.com/DaveGerson/agent-baton/blob/master/references/baton-patterns.md): Reusable orchestration patterns

## Optional

- [docs/design-decisions.md](https://davegerson.github.io/agent-baton/design-decisions/): ADR log
- [docs/troubleshooting.md](https://davegerson.github.io/agent-baton/troubleshooting/): Symptom-keyed fix list
```

Rules:

1. **One H1**, the project name. Required by the spec.
2. **One blockquote** with a 2–3 sentence summary. Required.
3. **H2 sections** named for the kind of link they hold (`Docs`,
   `Reference procedures`, `Optional`).
4. **Every link is a stable URL**, not a relative path. The file is
   meant to be fetched cold by an external LLM.
5. **Mark the "Optional" section** for content the agent can skip when
   context is tight.

We do **not** ship `llms-full.txt` or `llms-ctx.txt` until there is a
demonstrated consumer that needs it. Adding them later is cheap; getting
them stale immediately is worse than not having them.

### 4.2 `CLAUDE.md` as agent index

`CLAUDE.md` (project root) and the templated `templates/CLAUDE.md` (the
one installed into target projects) are **not** general docs. They are
agent indexes. Rules:

- **Top of file: one paragraph** stating what this repo is and what
  Claude is expected to do here. This is the equivalent of the llms.txt
  blockquote, scoped to the agent's session.
- **One MANDATORY section per behavioural rule.** The current file
  already does this well (`## Token Efficiency (MANDATORY)`,
  `## Autonomous Incident Handling (MANDATORY)`). Keep going.
- **Every rule is imperative.** *"Use file references."* Not *"file
  references can be useful."*
- **Link out for everything else.** CLAUDE.md does not duplicate
  agent-roster.md or cli-reference.md; it points to them.
- **Cap the length.** Target ≤200 lines for the project root file,
  ≤100 lines for the templated file. Past that, the agent stops
  reading.
- **Keep environment-variable tables.** They are high-value lookups
  Claude makes repeatedly.

### 4.3 H2/H3 patterns LLMs can grep

LLMs locate sections by keyword match against headings. Optimise for
that.

- **Lead with the noun the user will search for.** `## baton execute
  resume` beats `## Resuming after a crash`. The former matches a
  literal command lookup; the latter requires synonym inference.
- **Echo the exact phrase the agent would query.** If users routinely
  ask "how do I dispatch a team?", an H2 must literally read `## How to
  dispatch a team` (in a how-to doc) or `## Team dispatch` (in
  reference). Verb phrase in how-to, noun phrase in reference.
- **Avoid clever titles.** "Letting the planner do the work" is unsearchable. "How the planner selects agents" is searchable.
- **Avoid duplicate H2s on the same page.** They produce ambiguous
  anchors. Use H3 for sub-cases.
- **Avoid H2s under 5 characters or over 80 characters.** Both extremes
  fail keyword match.

### 4.4 Stable anchor IDs

GitHub auto-generates anchors by lowercasing the heading and replacing
spaces with hyphens (`## baton execute resume` →
`#baton-execute-resume`). This means:

1. **The heading text is the anchor contract.** Renaming a heading is
   a breaking change for any cross-link.
2. **Quote anchors when citing them in CLI output.** When `baton`
   prints a doc reference, use the full URL with anchor:
   `https://davegerson.github.io/agent-baton/cli-reference/#baton-execute-resume`.
3. **Add an explicit anchor only when the heading must change.** Use
   the HTML form `<a id="legacy-anchor"></a>` directly above the new
   heading. Document the legacy anchor with a comment.

### 4.5 File-path:line references

For maintainer-facing and agent-facing docs, **cite source by
file-path:line**. The `cymbal` tool resolves these instantly; LLMs can
quote them; reviewers can click through.

- Cite a function as `agent_baton/core/engine/planner.py:142`.
- Cite a class as `agent_baton/core/engine/state.py:ExecutionState`
  (no line number when the symbol name is unique).
- Cite a CLI command as `agent_baton/cli/commands/execution/execute.py`
  + the H2 in `cli-reference.md` that documents it.
- **Do not cite by symbol name alone** when there are multiple
  definitions in the codebase. `cymbal investigate <symbol>` will
  surface ambiguity; resolve it before citing.

When line numbers drift, fix them. Stale line numbers are worse than no
line numbers — they look authoritative.

### 4.6 Machine-parseable indexes

Two indexes must be regenerated mechanically and are part of the
**contract** for agent navigation:

| Index | Generated from | Consumer |
|-------|---------------|----------|
| `docs/agent-roster.md` | `agents/*.md` frontmatter | Orchestrator agent looking up flavours |
| `docs/cli-reference.md` H2 list | `agent_baton/cli/commands/` modules | Claude looking up a command |
| `references/baton-engine.md` action list | `agent_baton/core/engine/state.py` action enum | Orchestrator parsing engine output |

The list of indexes is closed for now. Adding a new one requires a
rule update here.

---

## 5. Progressive Disclosure for Humans

Humans adopting Baton are not learning a SaaS product; they are bolting
a CLI + Python package + agent definitions onto an existing Claude Code
workflow. The reading path must respect that. The order below is the
**canonical adopter journey**. Every adopter-facing page must link
forward to the next stop and backward to the previous one.

### 5.1 The reading path

1. **`README.md`** — One-screen pitch. Answers "what is this?", "what
   does it cost me?", "is the install reversible?". Closes with the
   five-minute install and a single forward link to the tutorial.
2. **`docs/index.md`** — One-screen landing page on GitHub Pages.
   Mirrors the README structure but for the published-docs reader.
   Forward link to first-run; sidebar links to reference and
   explanation.
3. **Install** (in `README.md` and reproduced in
   `docs/examples/first-run.md`) — `pip install agent-baton` plus
   `scripts/install.sh`. Verify with `baton agents` and `/agents`.
4. **`docs/examples/first-run.md`** (Tutorial) — One guaranteed-success
   walk-through ending in a passing test. No alternatives, no
   "you could also". This is the only place in the docs that violates
   "show alternatives" — by design.
5. **`docs/orchestrator-usage.md`** (How-to) — Recipes for common
   tasks: dispatch a team, amend a plan, resume after crash. Each
   recipe stands alone.
6. **`docs/cli-reference.md`** (Reference) — Full surface, looked up
   on demand. Read once linearly when you want to know what's
   available; afterwards, used as a lookup.
7. **`docs/architecture.md`** (Explanation) — Read when "why does this
   work this way?" becomes a blocker. Optional for users; required
   for contributors.
8. **`CONTRIBUTING.md`** (Reference + How-to) — Reading list for
   first-time contributors. Links into `docs/internal/`.

### 5.2 The two-link rule

Every adopter-facing page has at most two forward links and at most two
backward links **in the body**. The sidebar can list everything; the
narrative cannot. More than two forward links and the reader chooses
none.

### 5.3 What goes on the README

- The pitch (≤2 sentences).
- A 5-line "what Baton does" code block.
- The without/with comparison table.
- The 5-minute install.
- One forward link to the tutorial.
- A feature list (collapsed `<details>` is acceptable for browsing).
- A status section with the one-line "what's stable, what's in
  progress" answer.

The README does NOT contain:

- The full CLI reference. (It lives in `docs/cli-reference.md`.)
- A roadmap of unshipped features.
- Implementation notes.
- Style guidance for contributors.

### 5.4 What goes on `docs/index.md`

Tighter than the README. The published landing page is for visitors who
already clicked through; they want the four-quadrant menu, not the
pitch.

- One paragraph reaffirming the pitch.
- Four cards (or table rows): Tutorial / How-to / Reference /
  Explanation, each with one link to the canonical entry point in that
  quadrant.
- Search hint (Sphinx/MkDocs auto-search if present).

---

## 6. Accuracy Discipline

Stale docs are worse than missing docs. A reader who follows a wrong
instruction loses trust permanently. The rules below are not optional.

### 6.1 Every code sample must be runnable

- Every fenced ```` ```bash ```` block in a tutorial or how-to must
  succeed when copy-pasted into a fresh shell on a fresh checkout. CI
  runs the tutorial end-to-end.
- Every fenced ```` ```python ```` block must import successfully
  against the current `agent_baton` package. Use `>>> ` doctest format
  for executable Python in explanation pages; CI runs `pytest --doctest-glob='*.md'` against `docs/`.
- Pseudo-code is fenced as ```` ```text ```` and explicitly labelled
  as illustrative.

### 6.2 Every CLI flag must be verified

When you document a CLI flag, verify it with the source. Two acceptable
methods:

1. `baton <subcommand> --help` and copy the exact flag form.
2. Open `agent_baton/cli/commands/<group>/<command>.py` and read the
   argparse `add_argument` calls.

If a flag is documented but not in the source, the doc is broken; remove
the doc, do not add the flag.

### 6.3 Every claimed file path must exist

When you write `agent_baton/core/engine/planner.py:142`, that file must
exist and that line must be approximately what you describe. CI runs a
link-checker that resolves every `path:line` reference in `docs/` and
fails on broken ones.

### 6.4 Use `cymbal` to verify symbols before citing them

`cymbal investigate <symbol>` returns the source location, callers, and
callees of any Python symbol in the repo. Before writing
"`ExecutionState.advance()` returns the next action", run:

```bash
cymbal investigate ExecutionState.advance
```

and confirm the signature and behaviour match. Do not paraphrase from
memory.

For high-fanout symbols, run `cymbal impact <symbol>` before claiming
"changes here are local". Symbols touched by 50+ call sites are
load-bearing; the doc must say so.

### 6.5 Date-stamp anything time-sensitive

Status pages, roadmaps, and ADRs include the date in ISO format on the
first line:

```markdown
> Last updated: 2026-04-28
```

When a status entry becomes false, archive the page (move to
`docs/internal/archive/`) rather than letting it rot.

### 6.6 No "in the future"

Do not document features that do not exist. The exception is
`docs/specs/` and `docs/design-decisions.md`, which are explicitly
historical/forward-looking and clearly labelled as such.

If a feature is partially implemented, say so explicitly with the
exact gap: *"`baton execute amend` accepts new phases today; new steps
within an existing phase are tracked in
[issue #N](https://...)."*

### 6.7 Structured tests for docs

Where tooling allows:

- **Doctests** for Python snippets in explanation pages.
- **Tutorial test** in CI that runs `docs/examples/first-run.md`
  end-to-end (extract the `bash` blocks with a small script; pipe
  through `bash -e`).
- **Link checker** for every relative link and every `path:line`
  reference.
- **CLI surface diff** that compares `docs/cli-reference.md` H2 list
  to the auto-discovered command modules. Drift fails CI.

---

## 7. What to Cut

These are anti-patterns currently present in `docs/`. The next pass
through each file should remove them. **Be aggressive.** A shorter,
correct doc beats a longer, partially-stale one.

### 7.1 Files to delete outright

- `docs/8fe40a58a84f43c8ad4b7fb082e2b995.txt` — stray hash-named file,
  no clear purpose. Delete.
- `docs/internal/INSTALL-PROMPT.md` — install instructions for an
  outdated copy-paste workflow. Replaced by `scripts/install.sh` and
  `baton install`. Delete; do not relocate.
- `docs/PRODUCTION_READINESS.md` if it is older than 90 days. If
  current, keep but date-stamp.

### 7.2 Files to relocate (out of public docs)

- `docs/audit/*` — audit reports. Move to `audit-reports/` (already
  exists at repo root) or `docs/internal/`.
- `docs/reviews/*` — internal reviews. Move to `docs/internal/`.
- `docs/competitive-audit/*` — internal competitive analysis. Move
  to `docs/internal/`.
- `docs/pyright-diagnostics-triage.md` — internal triage doc. Move
  to `docs/internal/`.
- `docs/internal/CODEBASE_REVIEW.md` and
  `docs/internal/REVIEW-consulting-delivery-platform.md` — already
  internal; confirm not linked from public navigation.

### 7.3 Files to split (Diátaxis violations)

- `docs/architecture.md` (1979 lines) — split into:
  - `docs/architecture.md` (Explanation, target ≤500 lines): the
    interaction chain, design philosophy, the *why*.
  - `docs/architecture/package-layout.md` (Reference): the
    1:1 map of `agent_baton/` packages.
  - `docs/architecture/state-machine.md` (Reference + small
    Explanation): the action enum, the transition table.
- `docs/cli-reference.md` (2185 lines) — keep as one file *only* if
  the page renders with a working table-of-contents in GitHub Pages.
  If not, split per command group: `cli-reference/execution.md`,
  `cli-reference/observe.md`, etc.
- `docs/invariants.md` — split the three invariant statements
  (Reference) from the rationale (Explanation, fold into
  `architecture.md`).

### 7.4 Patterns to delete from any page

- **Marketing adjectives.** "Powerful", "seamless", "lightning-fast",
  "robust", "production-grade", "world-class". Delete on sight.
- **Hedging.** "It is recommended that you …" → "Run …". "You can …"
  in a tutorial → "Run …".
- **Aspirational tense.** "Baton will support …", "in a future
  release …", "we plan to …". Delete or move to `docs/specs/`.
- **Apologies.** "Note: this is currently a bit awkward, but …".
  Delete the apology; either the doc is correct or the feature is
  broken.
- **Duplicated overviews.** Every doc currently opens with a
  re-explanation of what Baton is. The README and `docs/index.md`
  do that work. Other pages open with one sentence on what *that
  specific page* covers.
- **Alternative paths in tutorials.** "You can also use `--json`
  here." Delete from tutorial; move to how-to.
- **"Of course"** and **"obviously"**. Always delete; if it's
  obvious, the sentence is unnecessary.
- **"Simply"**. Always delete; if it's simple, it doesn't need the
  word.
- **Diagrams that don't pay rent.** ASCII art that just lists package
  names → table. ASCII art of an actual control flow → keep, with
  caption.

### 7.5 Stale architecture sketches

The current `docs/architecture.md` includes:

- Three different ASCII boxes-and-arrows diagrams of the same flow.
  Pick one; delete the others.
- A "Three Interfaces" diagram that omits the daemon. Update or
  delete.
- A sentence count of "49 commands" that disagrees with the README's
  "50+ commands". Make it generated; replace prose count with a
  generated-from-source line.

### 7.6 The big rule

If you cannot find a reader who needs a paragraph, delete the
paragraph. Do not retain "for completeness". Reference docs achieve
completeness; explanation docs do not.

---

## 8. Document Templates

Copy the relevant template into a new file. Fill in the placeholders.
Do not vary the structure.

### 8.1 Tutorial template

```markdown
# Tutorial: <one concrete outcome the reader will achieve>

> By the end of this tutorial you will <single concrete deliverable —
> e.g., "have run a Baton plan that adds a health-check endpoint and
> see the test pass">. Estimated time: <X> minutes.

## Before you begin

You need:

- <prerequisite 1, with version>
- <prerequisite 2, with version>
- <prerequisite 3>

This tutorial assumes a fresh checkout of agent-baton at <commit/tag>.
It does **not** assume prior Baton experience.

## Step 1 — <imperative verb phrase>

<One sentence saying why this step exists.>

```bash
<exact command>
```

You should see:

```text
<exact expected output, trimmed to the load-bearing lines>
```

> Notice the `<specific token>` in the output — that confirms <fact>.

## Step 2 — <imperative verb phrase>

<...same shape...>

## Step N — <imperative verb phrase>

<...>

## What just happened

<2–4 sentences. Not a re-explanation; a *recap* tied to what the
reader saw on screen. No new concepts.>

## Where to go next

- For the recipe form of what you just did, see
  [<how-to title>](<link>).
- For why Baton works this way, see
  [<explanation title>](<link>).
- For the full surface, see [CLI reference](<link>).
```

### 8.2 How-to guide template

```markdown
# How to <accomplish a specific task>

This guide is for users who already understand <baseline competence>.
For an end-to-end introduction, see
[the first-run tutorial](<link>).

## When to use this

<2–3 sentences scoping the situation. When does this recipe apply?
When does it NOT apply?>

## Steps

1. <Imperative step.>

   ```bash
   <command>
   ```

2. <Imperative step.>

   ```bash
   <command>
   ```

3. <...>

## Verifying

<One way to confirm the task succeeded. Usually a `baton query` or
inspection of `plan.json`.>

```bash
<verification command>
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| <symptom> | <cause> | <fix> |

## See also

- [<related how-to>](<link>)
- [<reference page for the commands used>](<link>)
```

### 8.3 Reference template (CLI command)

```markdown
### `baton <command> <subcommand>`

<One sentence: what this command does. No preamble.>

**Synopsis**

```bash
baton <command> <subcommand> [OPTIONS] <ARGS>
```

**Arguments**

| Name | Required | Description |
|------|----------|-------------|
| `<ARG1>` | yes | <what it is> |
| `<ARG2>` | no | <what it is, default> |

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--flag1 VALUE` | <default> | <what it does> |
| `--flag2` | off | <what it does> |

**Output**

<What is printed on success. Cite the structure if it is parsed by
agents.>

**Exit codes**

| Code | Meaning |
|------|---------|
| 0 | success |
| 1 | <specific failure> |
| 2 | <specific failure> |

**Examples**

```bash
# Common case
baton <command> <subcommand> <example args>

# Edge case
baton <command> <subcommand> --flag1 X <example args>
```

**Source**: `agent_baton/cli/commands/<group>/<file>.py`
```

### 8.4 Reference template (concept / API)

```markdown
## <Symbol or concept name>

<One sentence definition.>

**Type**: <Class / Function / Protocol / Endpoint / Event / ...>

**Defined in**: `<file path>:<line>`

**Signature**

```python
<exact signature>
```

**Fields / Parameters**

| Name | Type | Description |
|------|------|-------------|
| `<name>` | `<type>` | <description> |

**Behaviour**

<3–6 sentences. Neutral. No opinions. Cross-link to explanation page
for the *why*.>

**Examples**

```python
<minimal usage example>
```

**See also**

- [<reference page for related symbol>](<link>)
- [<explanation page for the subsystem>](<link>)
```

### 8.5 Explanation template

```markdown
# <Topic phrased as a noun phrase>

<2–3 sentence opening. State the question this page answers.>

## Background

<What did Baton inherit, what problem domain are we in, what context
does the reader need? 1–3 paragraphs. Link out for terminology.>

## How <subsystem> works

<Discussion. Multiple paragraphs allowed. Diagrams welcome (one per
load-bearing flow). This is where you can use *we* and discuss
trade-offs.>

## Why this design

<Discussion of alternatives considered. Link to the relevant ADR in
`docs/design-decisions.md`. Admit the limitations.>

## Implications

<What this design buys, what it costs. Concrete bullets are fine.>

## See also

- ADR: [<title>](../design-decisions.md#<anchor>)
- Reference: [<symbol>](../api-reference.md#<anchor>)
- How-to: [<task>](../orchestrator-usage.md#<anchor>)
```

### 8.6 Troubleshooting entry template

A single entry within `docs/troubleshooting.md`. The page is a flat
H3-keyed list grouped by H2 area.

```markdown
### <Verbatim error message OR symptom phrased as the user sees it>

**Cause**: <One sentence root cause.>

**Fix**: <One sentence imperative fix.>

```bash
<exact command if applicable>
```

<Optional: one paragraph explaining when this happens, if the cause is
non-obvious. No more.>
```

---

## 9. Review Checklist

Apply this checklist to any doc page before approving the PR. The page
PASSES if and only if **every** item is checked. If any item is N/A,
note it explicitly; do not silently skip.

1. **Quadrant**: the page is exactly one of {tutorial, how-to,
   reference, explanation}. The quadrant is named in the file's H1 or
   frontmatter, or unambiguous from the table in section 2.2.
2. **Audience**: the page targets exactly one audience (agent /
   adopter / maintainer). If two, they are in clearly labelled
   sub-sections.
3. **Lede**: the first paragraph answers "what does this page do for
   me?" in ≤3 sentences. No marketing adjectives.
4. **Voice**: second person, active voice, present tense throughout.
   *We* appears only in explanation pages.
5. **Terminology**: every term from `docs/terminology.md` is used
   consistently. No re-definition.
6. **Headings**: sentence case; H2/H3 are noun phrases or imperative
   verb phrases that match the queries readers will use; no skipped
   levels; unique slugs.
7. **Code samples**: every fenced block has a language; every
   bash/python sample is runnable as written; expected output shown
   for tutorials and how-tos.
8. **CLI flags**: every flag mentioned has been verified against
   `--help` or against `agent_baton/cli/commands/`.
9. **File paths**: every `path:line` reference resolves on the
   current commit; every relative link resolves.
10. **No future tense**: no "will", "plans to", "in a future
    release". Aspirational content is in `docs/specs/` only.
11. **No duplicated overview**: the page does not re-explain Baton.
    It opens with what *this page* covers.
12. **Cross-links**: tutorial → how-to + reference at the bottom;
    how-to → reference inline; reference → no outbound body links;
    explanation → reference + ADR.
13. **Length**: the page is as short as possible to be correct.
    Reference pages can be long; explanation pages cap around 500
    lines; how-tos cap around 200; tutorials cap around 300.
14. **Date stamp**: present on any time-sensitive page (status,
    roadmap, ADR).
15. **Removable**: every paragraph has a reader. No "for completeness"
    text. If unsure, cut.

---

## Appendix A — Why these calls

Brief rationale for the load-bearing decisions in this document, so
future writers understand the constraints they are working within.

- **Diátaxis is the spine.** Other frameworks exist (DITA, Microsoft's
  pattern library, Write the Docs heuristics). Diátaxis wins because
  its boundaries are sharp: action vs cognition, acquisition vs
  application. Sharp boundaries beat richer vocabularies for an
  AI-edited corpus, where drift is the dominant failure mode.
- **`llms.txt` is cheap insurance.** The spec is two pages. The cost
  of maintaining one file at the repo root is bounded. The benefit —
  external Claude instances finding the right docs without
  hallucinating — is real and growing as more users invoke Baton via
  Claude Code from outside this repo.
- **CLAUDE.md ≠ documentation.** It is an agent runtime configuration
  file that happens to be Markdown. Treat it like `.editorconfig`,
  not like `README.md`. Drift here causes silent agent regressions,
  not user-visible bugs.
- **Aggressive cuts beat thoughtful additions.** The current docs
  total ~6000 lines across the eight headline files alone. A reader
  (human or LLM) cannot consume that. Halving the corpus while
  keeping the working content is the explicit goal of any pass.

## Appendix B — When to break these rules

Diátaxis itself notes that the framework "prescribes approaches" but
remains "lightweight". The same applies here. Break a rule when:

- A specific reader's needs are unambiguous and the rule blocks them.
- Breaking the rule is consistent with the surrounding doc.
- You leave a one-line comment in the file explaining the break.

Do **not** break a rule because the rule feels arbitrary or because
the page "flows better". Those are the two most common bad reasons.
