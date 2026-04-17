# Team Audit: Maya (Solo Power User) + Agent-Baton Expert

Structured dialogue audit of agent-baton against the workflow needs of
a senior full-stack developer (8-12 years, Python + React, Claude Max
subscriber). Each item follows the dialogue method: Maya asks, Expert
investigates with file:line evidence, Maya probes, Expert discovers
second-pass findings, joint verdict, and delta from the solo audit.

Compared against: `docs/competitive-audit/persona-maya-carlos.md`

---

## Item 1: First-Run Friction

**Maya asks:** I just discovered agent-baton. Walk me through every step
from zero to my first multi-agent task succeeding. Where will I hit
walls? My patience budget is 10 minutes.

**Expert investigates:**

The steps in order:

1. `pip install git+https://github.com/DaveGerson/agent-baton.git`
   (not on PyPI -- `pyproject.toml` line 5 confirms name=agent-baton,
   version=0.1.0)
2. `baton install --scope project --source <path>` (or `scripts/install.sh`)
   -- installs 20 agent .md files, 16 references, settings.json, CLAUDE.md
   template. This is required before planning.
3. `baton plan "my task" --save --explain` -- auto-detects stack, picks
   agents, writes plan.json + plan.md
4. `git checkout -b feat/my-task`
5. `baton execute start` -- first DISPATCH action
6. Drive the loop with agent dispatches and gate checks

The install step (`baton install`) requires `--scope` (mandatory) and
`--source` (defaults to `.`). If the user runs `baton plan` without
installing first, `main.py:170-176` detects the missing `.claude/agents`
directory and prints an install hint -- not an error, but a manual
redirection.

The `docs/examples/first-run.md` exists and walks through a complete
FastAPI task (plan, start, dispatch, gate, complete). But `baton --help`
references it as "Full walkthrough: docs/examples/first-run.md" -- a
relative path that means nothing outside the repo.

**Maya probes:** So I need to know the path to the agent-baton source
repo twice -- once for pip install and again for `--source`? That's
friction. And what happens if I'm working in a project that isn't the
agent-baton repo itself?

**Expert second-pass findings:**

Correct. If agent-baton is `pip install`-ed from git, the source tree
ends up somewhere in site-packages, not an obvious path to pass to
`--source`. The `--source` default is `.` (current directory) which
only works if you're standing in the agent-baton repo. For a remote
install, you'd need to know where the agents/ directory was extracted.

However, `baton install` (at `install.py:155-231`) looks for `agents/`
under the `--source` path. If installed as a pip package, the agents/
directory ships inside the package wheel (confirmed by `pyproject.toml`
build config) -- but install.py doesn't resolve from installed package
metadata; it does raw `Path(args.source) / "agents"` lookup.

This means: pip install gives you the `baton` CLI, but `baton install`
can't find its own agent definitions unless you point `--source` at the
cloned repo. That's a **broken first-run path** for anyone who didn't
clone the repo manually.

**Joint verdict: BLOCKED**

The pip-to-install pipeline is broken for non-repo-local installs. Maya
would need to clone the repo *and* pip install it, then run
`baton install --scope project --source ~/agent-baton`. Three separate
steps before she can plan anything. Exceeds 10-minute budget if she has
to troubleshoot.

**Delta from solo audit:** The solo audit rated "pip install" as PARTIAL
(noting it's not on PyPI) but didn't trace the `baton install --source`
dependency. The team dialogue reveals a *deeper* problem: even after pip
install, `baton install` can't self-discover its own bundled agents. This
is a true first-run blocker, not just a packaging gap.

---

## Item 2: CLI Responsiveness

**Maya asks:** How fast is `baton --help`? Is there a noticeable lag
when I run commands? I'll notice anything over 300ms.

**Expert investigates:**

Measured: `time baton --help` = **188ms** real. Python import of
`agent_baton` = **116ms**. The import chain shows the heaviest modules:

- `agent_baton.models.execution` = 10.3ms (largest model)
- `logging` = 5.3ms
- `agent_baton.models.decision` = 4.7ms
- `agent_baton.core.engine.classifier` = 1.6ms

All command modules are discovered eagerly at startup via
`pkgutil.iter_modules` (`main.py:38-62`). Each discovered module is
imported and has its `register()` called to build the argparse tree.
This means every CLI invocation imports all ~49 command modules
regardless of which command runs.

No lazy-import strategy exists. However, dependencies like `anthropic`,
`fastapi`, `uvicorn` are imported lazily within handlers, not at module
level.

**Maya probes:** 188ms is fine. But what about `baton plan`? It calls
the classifier which might hit the Anthropic API (Haiku). How much does
that add?

**Expert second-pass findings:**

The `HaikuClassifier` (`classifier.py:338`) makes a synchronous
Anthropic API call with a 5-second timeout (`_HAIKU_TIMEOUT = 5.0`).
If the API is unreachable or slow, this adds up to 5 seconds to plan
creation. There IS a fallback: `FallbackClassifier` wraps
`HaikuClassifier` and catches all exceptions, falling back to
`KeywordClassifier` on failure (`classifier.py` -- FallbackClassifier
class). So the worst case for a cold API is 5 seconds of waiting, then
a keyword-based fallback.

With `--complexity light|medium|heavy`, the Haiku classifier is bypassed
entirely (`planner.py:759` -- the condition requires `complexity is None`
to invoke the classifier). This means Maya can skip the API call.

**Joint verdict: WORKS**

188ms cold start is well within Maya's tolerance. The Haiku classifier
adds 0.5-2s normally, up to 5s on timeout, but `--complexity` bypasses
it entirely. No blocking issues.

**Delta from solo audit:** The solo audit reported 190ms and "PASS" but
didn't investigate the plan-time Haiku classifier latency. The team
dialogue reveals that `baton plan` without `--complexity` incurs an API
call that could add seconds. The `--complexity` escape hatch is
important for Maya's speed-sensitive workflow.

---

## Item 3: Plan Quality for Python+React Stack

**Maya asks:** I say: `baton plan "add input validation to /users
endpoint with tests" --save --explain`. My project has FastAPI + React.
What phases get generated? Are they sensible?

**Expert investigates:**

Stack detection (`router.py:33-44`): FastAPI is NOT in
`FRAMEWORK_SIGNALS` -- there's no `fastapi.cfg` or marker file to
detect. However, `manage.py` triggers Django, and `vite.config.ts`
+ `package.json` with react triggers React. For FastAPI, detection
depends on finding `pyproject.toml` or `requirements.txt` at the root
(which gives `language=python`, `framework=None`).

The `FLAVOR_MAP` at `router.py:48-50` maps `("python", "fastapi")` to
`backend-engineer--python`, but the framework detection never sets
`framework="fastapi"` because there's no FastAPI-specific signal file.
So the actual mapping path is `("python", None)` which also maps to
`backend-engineer--python`. End result: the **correct agent** gets
selected, just through the wrong key.

For the React frontend, `vite.config.ts` + `package.json` with "react"
dependency triggers `framework="react"`. With Python at root +
React in subdirectory, the profile becomes `("python", "react")` which
maps to both `backend-engineer--python` and `frontend-engineer--react`.

Task type classification: "add input validation to /users endpoint with
tests" scores highest on "new-feature" (keywords: "add"). The keyword
classifier would produce `complexity=medium` (no heavy/light signals).

Plan phases for medium new-feature: `["Design", "Implement", "Test"]`
(Review dropped for medium complexity per `classifier.py:329`). Agents:
`["architect", "backend-engineer", "test-engineer"]` (3-agent cap for
medium).

Gate for Python: `pytest --cov` (from `_STACK_GATE_COMMANDS` at
`planner.py:130`).

**Maya probes:** Wait -- no frontend-engineer? I said /users endpoint
but the validation also needs client-side form validation. Does
cross-concern expansion catch that?

**Expert second-pass findings:**

The `_CROSS_CONCERN_SIGNALS` at `planner.py:150-166` lists
`frontend-engineer` keywords: "ux", "ui", "navigate", "browser",
"visual", "layout", "css", "component", "react", "frontend". The task
description "add input validation to /users endpoint with tests" contains
none of these keywords. So no -- frontend-engineer is NOT added.

This is a correct omission for this specific task description (backend
endpoint validation). But if Maya meant both client and server validation,
she'd need to say so explicitly: "add input validation to /users endpoint
with React form validation and tests". The word "react" would then trigger
frontend-engineer inclusion.

The architect would also be included but may be unnecessary for a
validation task. For `--complexity light`, only `backend-engineer--python`
(1 agent, 1 phase: "Implement") would be selected, which is arguably
the right answer.

**Joint verdict: WORKS**

Plan quality is sensible. The agent selection and phase structure are
reasonable for the task description. Cross-concern expansion is keyword-
driven and won't hallucinate agents you don't need. The `--complexity
light` path produces the minimal plan Maya would want for small tasks.

**Delta from solo audit:** The solo audit confirmed plan/execute works
but didn't test a specific task description against the actual planner
logic. The team dialogue reveals: (a) FastAPI is detected as generic
Python, not FastAPI-specific (cosmetic -- still routes correctly), (b)
cross-concern agent expansion is purely keyword-based and misses
implicit frontend needs, (c) `--complexity light` is the real power-user
shortcut.

---

## Item 4: Gate Behavior on Failure

**Maya asks:** My pytest gate fails after Phase 1. What EXACTLY happens?
Can I see the error? Can I retry? Does the failure message help me
fix the problem?

**Expert investigates:**

Gate execution in the orchestrator-driven loop (`execute.py:1061-1090`):

1. The engine returns `ACTION: GATE` with a command (e.g. `pytest --cov`)
2. The orchestrator (Claude Code) or headless runner runs the command
3. `subprocess.run(gate_cmd, shell=True, capture_output=True, timeout=300)`
4. `passed = proc.returncode == 0`
5. **stdout** is captured: `output = proc.stdout[-2000:]`
6. **stderr** appended on failure: `output += "\n--- stderr ---\n{proc.stderr[-1000:]}"`
7. `engine.record_gate_result(phase_id=..., passed=False, output=output)`

In the engine (`executor.py:1382-1389`):
- Gate failure sets `state.status = "failed"`
- Emits `gate_failed` event with output
- Publishes compliance audit record

The `_print_action` for GATE only shows type, phase, command, message --
NOT the output. The output is only stored internally via
`record_gate_result`. In `baton execute status` (`execute.py:774-783`),
gate results are displayed including pass/fail marker and gate type.

**Maya probes:** So if I'm driving the loop interactively (not headless),
the GATE action just tells me the command. I run it, see the output in
my terminal, then record the result. But what if the gate fails and the
engine marks the whole execution as failed? Can I fix the code and retry?

**Expert second-pass findings:**

This is a critical UX gap. When `record_gate_result(passed=False)` is
called, `executor.py:1389` sets `state.status = "failed"` -- **terminal
state**. The next `next_action()` call returns `ActionType.FAILED`.
There is NO built-in retry mechanism for gates.

Recovery options:
- `baton execute resume` -- but it checks status and returns FAILED
  action because state.status is already "failed"
- Manual state edit -- not supported via CLI
- Start over with `baton execute start` -- creates new execution,
  loses all completed step results
- The headless runner (`_handle_run`) doesn't implement gate retry either

The gate output IS captured (last 2000 chars of stdout + 1000 chars of
stderr), viewable via `baton execute status` under the "Gates:" section.
But the user can't act on it within the execution -- the execution is
dead.

**Joint verdict: BLOCKED**

Gate failure is terminal with no retry path. Maya would lose all prior
step completions on a test failure. This is a dealbreaker for any
iterative development workflow. She'd need to fix the code, then start a
completely new execution.

**Delta from solo audit:** The solo audit didn't test gate failure
behavior at all -- it only confirmed gates exist and work. The team
dialogue reveals that gate failure is a hard stop with no recovery,
which is a significant UX gap that solo testing missed entirely.

---

## Item 5: Context Handoff Fidelity

**Maya asks:** When Phase 1 (architect) finishes and Phase 2
(backend-engineer) starts, what does the backend-engineer actually
receive? Does it get the architect's output? Or does it start cold?

**Expert investigates:**

At dispatch time (`executor.py:2979-2988`):

```python
# Find the most recent completed step (different step_id) for handoff.
handoff = ""
for result in reversed(state.step_results):
    if result.step_id != step.step_id and result.status == "complete" and result.outcome:
        handoff = result.outcome
        break
```

This grabs the **most recent completed step's outcome** (the text
string recorded via `baton execute record --outcome "..."`) as the
handoff. It's then appended with resolved decisions
(`_append_resolved_decisions`).

The `PromptDispatcher.build_delegation_prompt` (`dispatcher.py:211-367`)
then constructs:

1. **Shared Context** -- plan-level context string (usually project overview)
2. **Intent** -- the user's original task summary (verbatim)
3. **Knowledge Context** -- attached knowledge packs/docs
4. **Prior Discoveries** -- bead memory (if bead_store is available)
5. **Your Task** -- the step's specific task description
6. **Success Criteria** -- per task-type ("The feature works as specified...")
7. **Previous Step Output** -- the handoff text

The handoff is the previous agent's `--outcome` parameter, typically
1-3 sentences set by the orchestrator when recording the step result.

**Maya probes:** Hold on -- the handoff is only ONE step's outcome?
If I have a 3-phase plan (Design, Implement, Test), the Test phase only
sees the Implement outcome, not the Design output? And the quality of
handoff depends entirely on the orchestrator summarizing the agent's
output well?

**Expert second-pass findings:**

Correct on both counts.

1. **Only the most recent step** -- the handoff loop at `executor.py:2981`
   breaks on the first match. So step 3.1 (Test) only sees step 2.1
   (Implement)'s outcome. The architect's design decisions from step 1.1
   are lost unless they were captured as beads.

2. **Bead relay partially compensates** -- `BeadSelector.select()` at
   `executor.py:2996-3003` selects up to 5 relevant beads (4096 token
   budget) from all prior steps. So discoveries and decisions from Phase
   1 CAN survive if they were emitted as `BEAD_DISCOVERY` or
   `BEAD_DECISION` signals. But this is opt-in behavior -- agents must
   actively emit bead signals.

3. **Shared context is static** -- it's the plan's `shared_context`
   string, set at planning time. It doesn't accumulate during execution.

4. **Outcome quality varies** -- in orchestrator mode, Claude Code
   summarizes the agent's output. In headless mode (`_handle_run`),
   the outcome is taken from the Claude subprocess output (first 500
   chars of stdout). Neither path guarantees a high-quality summary.

**Joint verdict: PARTIAL**

Phase N+1 gets Phase N's outcome text plus up to 5 beads from all prior
phases. The bead relay is the real preservation mechanism, but it's
opt-in and depends on agents emitting structured signals. Without beads,
context degrades across phases.

**Delta from solo audit:** The solo audit didn't examine handoff at all.
The team dialogue reveals a significant finding: the handoff is a single
step's outcome string, not an accumulating context. The bead system
compensates but is not guaranteed. This would directly affect Maya's
multi-phase tasks.

---

## Item 6: Crash Recovery UX

**Maya asks:** My laptop sleeps mid-task. Next morning I open the
terminal. What commands do I run? How much progress is lost?

**Expert investigates:**

`baton execute resume` (`executor.py:1675-1736`):

1. Loads state from disk -- tries file-based load first, then SQLite
   fallback (`executor.py:1692-1714`)
2. State is persisted after every mutation (`_save_execution` called
   after every `record_step_result`, `record_gate_result`, etc.)
3. Reconnects the trace recorder (`executor.py:1726-1734`)
4. Returns the next action via `_determine_action`

Additional recovery features:
- `baton execute list` -- shows all executions with status
- `BATON_TASK_ID` env var binds a session to a specific execution
- `baton execute switch <task-id>` -- changes active execution
- `recover_dispatched_steps()` (`executor.py:1738-1760`) -- clears
  stale "dispatched" markers so steps can be re-dispatched

**Maya probes:** What if the crash happened mid-agent-dispatch? The agent
was running when my laptop slept. Is that step lost or can it be
re-dispatched?

**Expert second-pass findings:**

If a step was in "dispatched" status when the crash happened:

1. `resume()` calls `_determine_action(state)` which walks the phases
2. `_determine_action` at `executor.py:877-887` treats dispatched steps
   as in-flight and skips them, waiting for completion
3. But the agent process is dead -- it will never complete
4. The step is stuck in "dispatched" forever

**However**, `recover_dispatched_steps()` at `executor.py:1738-1760`
exists specifically for this case -- it removes all "dispatched" results
so they get re-dispatched. But this must be called explicitly (it's used
by the daemon's startup path at `worker.py`). The interactive `resume`
subcommand at `execute.py:786-792` does NOT call
`recover_dispatched_steps()`.

So for Maya's scenario: `baton execute resume` would see the stuck
dispatched step and return... nothing actionable. She'd need to know to
use the daemon's recovery path or manually hack the state.

**Joint verdict: PARTIAL**

Crash recovery for completed steps is excellent (persisted after every
mutation). But recovery for in-flight dispatched steps is broken in the
interactive path. The fix exists (`recover_dispatched_steps`) but isn't
wired into `baton execute resume`.

**Delta from solo audit:** The solo audit rated crash recovery as "WORKS"
based on the existence of `resume()` and state persistence. The team
dialogue reveals a critical gap: in-flight steps at crash time become
zombies in the interactive path. The daemon path handles it; the
interactive path doesn't.

---

## Item 7: Complexity Override Depth

**Maya asks:** When I use `--complexity light`, how much ceremony does
it actually skip? Does it still run gates? Still classify risk? Still
run the Haiku API call?

**Expert investigates:**

Tracing `--complexity light` through the planner (`planner.py:672-1120`):

1. **Haiku classifier SKIPPED** -- `planner.py:759` condition:
   `if task_type is None and agents is None and phases is None and
   complexity is None:` -- since complexity is provided, the classifier
   branch is skipped entirely. No API call.

2. **Agent count**: `_MAX_AGENTS_BY_COMPLEXITY["light"] = 1` at
   `classifier.py:31`. The `KeywordClassifier._select_agents` for light
   returns only the primary implementer (e.g. `backend-engineer` for
   new-feature).

3. **Phases**: `KeywordClassifier._select_phases` for light returns
   `["Implement"]` only (`classifier.py:327`). No Design, Test, or
   Review phases.

4. **Risk classification STILL RUNS** -- `planner.py:924-938` runs
   keyword risk assessment regardless of complexity. If the task
   description contains "production" or "security", risk is elevated.

5. **Gates STILL APPLIED** -- `planner.py:1104-1107` adds gates to ALL
   phases that don't already have one. The Implement phase gets a build
   gate (`pytest --cov` for Python).

6. **Git strategy STILL DETERMINED** -- derived from risk level.

7. **Policy validation, knowledge resolution, foresight analysis** all
   still run (though with 1 phase and 1 step, they produce minimal
   output).

**Maya probes:** So `--complexity light` gives me 1 agent, 1 phase, but
still a gate? I thought light meant "just do the thing." Can I skip the
gate entirely?

**Expert second-pass findings:**

There's no `--no-gates` or `--skip-gates` flag. The gate is always added
for code-producing phases (`planner.py:2058-2078` -- only
"investigate", "research", "review", "design", "feedback" skip gates).
An "Implement" phase always gets a gate.

In practice for Maya's interactive workflow this is fine -- when the
GATE action comes, the orchestrator (Claude Code) runs the test command
and records the result. If she wanted to skip it, she could just
`baton execute gate --phase-id N --result pass` without running the
command. But there's no formal way to opt out.

For the headless runner (`baton execute run --dry-run`), gates are
auto-passed (`execute.py:1069-1070`).

Summary of what `--complexity light` skips vs keeps:

| Concern | Skipped? |
|---------|----------|
| Haiku API call | YES |
| Multi-agent roster | YES (1 agent) |
| Design/Test/Review phases | YES |
| Risk classification | NO |
| QA gates | NO |
| Git strategy | NO |
| Policy validation | NO |
| Knowledge resolution | NO |

**Joint verdict: WORKS**

`--complexity light` meaningfully reduces ceremony: 1 agent, 1 phase,
no API call. Gates remain but are low-friction in practice. Risk
classification is cheap (keyword-only). The result is a plan that feels
"just do the thing" while still having a test checkpoint.

**Delta from solo audit:** The solo audit confirmed `--complexity` exists
and "bypasses automatic classification" but didn't trace what stays
active. The team dialogue reveals the full picture: gates survive light
mode (the solo audit missed this), risk classification still runs
(cheap, so fine), and there's no way to opt out of gates formally.

---

## Item 8: Agent Routing Accuracy

**Maya asks:** My project uses FastAPI + HTMX (not React). Does the
router pick the right agents? Or does it only know about the big
frameworks?

**Expert investigates:**

The `FRAMEWORK_SIGNALS` at `router.py:33-44` maps specific config files
to frameworks:
- `next.config.js` -> react
- `nuxt.config.js` -> vue
- `angular.json` -> angular
- `svelte.config.js` -> svelte
- `appsettings.json` -> dotnet
- `manage.py` -> django

FastAPI has NO framework signal file -- it's a pip package with no
required config file. HTMX has NO signal file either. So for
Maya's stack:

Detection result: `language=python, framework=None` (from pyproject.toml
or requirements.txt). FLAVOR_MAP: `("python", None)` maps to
`backend-engineer--python`.

No `frontend-engineer` would be selected because HTMX is not detected.
The `frontend-engineer--react` variant requires React framework
detection, and there's no generic `frontend-engineer` base (checking
`agents/` directory shows `frontend-engineer.md` base +
`frontend-engineer--react.md` + `frontend-engineer--dotnet.md`).

**Maya probes:** So I'd need to manually add `--agents frontend-engineer`
for HTMX work? Is there a way to teach the router about my stack?

**Expert second-pass findings:**

Two paths:

1. **Manual override**: `baton plan "..." --agents backend-engineer--python,frontend-engineer`
   works. The `--agents` flag at `plan_cmd.py:49-51` overrides auto-
   selection entirely.

2. **Learned overrides**: The router checks `LearnedOverrides` at
   `router.py:257-283` for project-specific flavor corrections. The
   `system-maintainer` agent writes `learned-overrides.json` based on
   retrospective analysis. So after a few successful runs with manual
   `--agents`, the system could learn the preference.

3. **Custom agent**: Use `talent-builder` to create a
   `frontend-engineer--htmx` variant. The talent-builder agent
   (`agents/talent-builder.md`) would research HTMX idioms and create
   an agent definition. The router would then pick it up if a
   `("python", "htmx")` entry were added to FLAVOR_MAP.

However: none of these happen automatically. The router will silently
under-staff HTMX projects.

**Joint verdict: PARTIAL**

The router correctly identifies Python backend but misses HTMX (and
any non-standard frontend framework). Manual `--agents` and learned
overrides provide workarounds, but the first run will be under-staffed.
Maya would notice and fix it, but it's a "why didn't you know this?"
moment.

**Delta from solo audit:** The solo audit confirmed `baton detect` works
for Python+React but didn't test unusual frameworks. The team dialogue
reveals a systematic gap: any framework without a config file signal
(FastAPI, HTMX, Flask, Starlette, Litestar) gets detected as generic
Python. The learned-overrides path is a good escape hatch but isn't
discoverable.

---

## Item 9: Bead Usefulness for Debugging

**Maya asks:** Agent 2.1 made a bad implementation decision. Can I trace
WHY through beads? Is the bead system actually useful for debugging, or
is it just metadata noise?

**Expert investigates:**

The bead system (`cli/commands/bead_cmd.py`) provides:

- `baton beads list --task TASK_ID` -- all beads for a task
- `baton beads list --type decision` -- filter by type
- `baton beads show <bead-id>` -- full bead content as JSON
- `baton beads graph TASK_ID` -- dependency graph between beads
- Types: discovery, decision, warning, outcome, planning

Beads are created when agents emit structured signals in their output:
- `BEAD_DISCOVERY: <what they found>`
- `BEAD_DECISION: <what> CHOSE: <choice> BECAUSE: <rationale>`
- `BEAD_WARNING: <what might cause problems>`

The signal parsing happens in `executor.py` during `record_step_result`.
Bead signals are injected into delegation prompts via `_BEAD_SIGNALS_LINE`
(`dispatcher.py:46-52`).

**Maya probes:** OK but do agents actually EMIT these signals reliably?
If the agent doesn't write `BEAD_DECISION: ...`, there's nothing to
trace. What's the actual hit rate?

**Expert second-pass findings:**

The bead signals are REQUESTED in the delegation prompt -- every agent
gets the instruction at `dispatcher.py:46-52`:

```
Report discoveries and decisions using structured signals:
  BEAD_DISCOVERY: <what you found>
  BEAD_DECISION: <what you decided> CHOSE: <choice> BECAUSE: <rationale>
  BEAD_WARNING: <what might cause problems>
```

But this is a prompt instruction, not enforcement. Agent compliance
depends on:
1. The LLM following the instruction (Sonnet/Opus generally do)
2. The agent having non-trivial decisions to report
3. The output being captured and parsed

For debugging Maya's scenario: if the agent DID emit
`BEAD_DECISION: chose X BECAUSE: Y`, she could find it with:
`baton beads list --task <id> --type decision` and trace the rationale.
If the agent didn't emit signals, there's nothing to find.

The `baton beads graph` command shows dependency relationships between
beads (which bead informed which), which IS useful for understanding
decision chains. And `baton beads list --type warning` could surface
ignored warnings.

**Joint verdict: PARTIAL**

The bead system provides the right primitives for decision tracing. The
`graph` and `list --type decision` commands would answer Maya's "why"
question IF agents emit signals. The gap is reliability: signal emission
is prompt-instructed, not enforced. In practice, Claude models follow
these instructions reasonably well, but there's no guarantee.

**Delta from solo audit:** The solo audit didn't examine beads at all
(it focused on traces). The team dialogue reveals that beads are the
primary decision-tracing mechanism, but their usefulness depends on
agent compliance with signal emission instructions. The `graph` command
is a genuinely useful debugging tool that solo testing wouldn't discover
without a populated bead store.

---

## Item 10: Talent-Builder Quality

**Maya asks:** I want a `backend-engineer--fastapi` variant that knows
FastAPI idioms (dependency injection, Pydantic v2 models, async
middleware). How good is the talent-builder output?

**Expert investigates:**

The `backend-engineer--python` agent (`agents/backend-engineer--python.md`)
already knows FastAPI -- lines 5, 21, 25, 28, 38 reference FastAPI
explicitly (dependency injection, Pydantic v2, async patterns). So Maya
might not need a separate variant.

But if she wants one, the talent-builder (`agents/talent-builder.md`,
385 lines) follows a structured workflow:

1. **Interview** (step 1): "What capability is needed?"
2. **Research** (step 2): reads docs, codebase, schemas
3. **Decision framework** (step 3): applies 5 tests from
   `decision-framework.md`
4. **Create** (step 4): builds agent .md with frontmatter, knowledge
   section, principles, success criteria

The talent-builder creates agent files using the same frontmatter
format as existing agents. It references `knowledge-architecture.md`
for deciding between agent-embedded knowledge vs knowledge packs.

**Maya probes:** But does the talent-builder actually KNOW FastAPI
idioms? Or does it just scaffold a generic template that I'd have to
fill in with my own knowledge?

**Expert second-pass findings:**

The talent-builder doesn't have baked-in framework knowledge -- it
researches the codebase. If Maya's project uses FastAPI, it would:

1. Scan for FastAPI patterns in the codebase (`Depends()`, router
   definitions, middleware, Pydantic models)
2. Extract conventions from existing code
3. Bake those into the agent definition

The quality depends on what's already in the codebase. For a mature
FastAPI project, the output would capture project-specific conventions
well. For a greenfield project, it would produce a generic template.

Notably, the talent-builder runs as an Opus model (frontmatter line 13:
`model: opus`), so it has strong reasoning and research capabilities.
The 385-line definition includes enterprise patterns (domain onboarding,
regulatory domain, documentation ingestion) and quality checklists.

The key limitation: the created agent is a static .md file. It captures
idioms at creation time but doesn't evolve. The `system-maintainer`
agent handles post-cycle tuning but that's a separate workflow.

**Joint verdict: WORKS**

The talent-builder produces high-quality agent definitions by researching
the actual codebase. For Maya's FastAPI variant, it would extract
patterns from her project and create a usable specialist agent. The
existing `backend-engineer--python` already covers FastAPI basics, so
the variant would add project-specific conventions.

**Delta from solo audit:** The solo audit confirmed talent-builder exists
(385 lines, comprehensive) but didn't assess whether it can produce
framework-specific output. The team dialogue reveals that it researches
the codebase rather than relying on baked-in knowledge, which means
quality scales with project maturity. Also revealed: the existing
`backend-engineer--python` already has FastAPI coverage, making a
separate variant optional.

---

## Item 11: Query Power

**Maya asks:** I want to know "which agent fails most on my project."
One command? Two? Five?

**Expert investigates:**

One command: `baton query agent-reliability`

Output (`query.py:212-226`):
```
agent | steps | success_rate | successes | failures | retries | tokens | avg_duration_s
```

This directly answers "which agent fails most" -- sort by failures or
success_rate. Additional options:
- `--format json` for programmatic consumption
- `--days N` to scope the time window
- `--format csv` for spreadsheet import

For deeper analysis:
- `baton query agent-history <agent-name>` -- recent step results
- `baton query gate-stats` -- gate pass rates by type
- `baton query cost-by-agent` -- token costs per agent
- `baton query --sql "SELECT ..."` -- arbitrary SQL against baton.db

**Maya probes:** What about cross-project? If I use agent-baton on 3
projects, can I see which agent fails most across all of them?

**Expert second-pass findings:**

Yes: `baton cquery` queries the central `~/.baton/central.db` which
aggregates data from all projects via the sync engine. The central
database includes analytics views (`schema.py`):

- `v_agent_reliability` -- cross-project agent stats
- `v_cost_by_task_type` -- costs across projects
- `v_recurring_knowledge_gaps` -- gaps that appear in multiple projects
- `v_project_failure_rate` -- per-project failure rates

Maya could run:
`baton cquery --sql "SELECT * FROM v_agent_reliability ORDER BY success_rate ASC LIMIT 5"`

The sync must be running (`baton sync --all` or configured in hooks) for
central.db to have data. This isn't automatic -- it requires either
manual sync or a configured post-execution hook.

**Joint verdict: WORKS**

One command for the basic question. SQL escape hatch for complex queries.
Cross-project analytics available via central.db. The only gotcha is
that cross-project data requires sync to be configured.

**Delta from solo audit:** The solo audit confirmed 16 predefined
queries and SQL escape hatch. The team dialogue adds: cross-project
analytics via central.db with pre-built views, which significantly
extends query power. Also reveals the sync dependency (data isn't
automatic).

---

## Item 12: Upgrade Friction

**Maya asks:** A new version of agent-baton ships with better agent
definitions and a schema change. What does upgrade look like? Does it
break my existing execution history?

**Expert investigates:**

Upgrade paths:

1. **Code upgrade**: `pip install --upgrade git+...` or `git pull` +
   `pip install -e ".[dev]"` -- updates the Python package

2. **Agent/reference upgrade**: `baton install --scope project --source <path> --upgrade`
   (`install.py:155-231`):
   - Overwrites agents + references (they improve between versions)
   - **Merges** settings.json hooks (preserves user keys, adds new hooks)
   - Preserves CLAUDE.md, knowledge/, team-context/
   - Does NOT touch baton.db or execution state

3. **Schema migration**: `schema.py:43` defines `SCHEMA_VERSION = 9`.
   `MIGRATIONS` dict (`schema.py:46+`) maps version numbers to DDL
   scripts (ALTER TABLE statements). `ConnectionManager._run_migrations`
   applies them sequentially when a database is behind current version.

   Current migrations: v2 (knowledge columns), v4 (beads), v5
   (learning_issues), v6 (interactions), v7 (interaction_turns), v8
   (plan/execution enhancements), v9 (latest).

4. **Migration is automatic** -- `ConnectionManager` checks schema
   version on connection and applies pending migrations. No manual
   step required.

**Maya probes:** What if a migration fails? Does it corrupt my
database? And the settings merge -- does it handle the case where I've
customized hook commands?

**Expert second-pass findings:**

Migration safety:
- Each migration uses `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD
  COLUMN` -- both are idempotent-safe. If the column already exists,
  ALTER TABLE fails but the migration runner likely catches this (needs
  verification).
- There's no explicit transaction wrapping in the migration runner shown
  in the schema. A partial migration failure could leave the database in
  an inconsistent state.
- No rollback mechanism for failed migrations.

Settings merge safety (`install.py:26-73`):
- Merge is additive: for each hook event in source, adds entries not
  already present (deduped by "command" string)
- User hooks for events not in the source template are preserved
- User-specific top-level keys (permissions, mcpServers, env) are
  preserved untouched
- This is well-designed -- Maya's customizations survive upgrade

Agent file upgrade:
- Old agent files are overwritten (`agent_force = force or upgrade` at
  `install.py:198`)
- If Maya has customized agent definitions, those customizations are lost
- Knowledge packs in `knowledge/` are preserved

**Joint verdict: WORKS**

Upgrade is well-designed: agents/refs overwrite (intended), settings
merge preserves user customizations, schema migration is automatic.
The main risk is customized agent files being overwritten, but that's
documented behavior and custom agents should live in `knowledge/` or
have different filenames.

**Delta from solo audit:** The solo audit mentioned install script
friction but didn't examine upgrade behavior. The team dialogue reveals:
(a) automatic schema migration with 9 versions of incremental DDL,
(b) smart settings merge that preserves user hooks, (c) risk of losing
customized agent files on upgrade. The merge behavior is genuinely
impressive -- solo testing wouldn't discover the hook-level dedup logic.

---

## Summary Table

| # | Item | Verdict | Solo Rating | Delta |
|---|------|---------|-------------|-------|
| 1 | First-run friction | BLOCKED | PARTIAL | Deeper: `baton install` can't self-discover bundled agents |
| 2 | CLI responsiveness | WORKS | PASS | New: Haiku classifier adds latency, `--complexity` bypasses it |
| 3 | Plan quality (Python+React) | WORKS | WORKS | New: FastAPI detected as generic Python; cross-concern is keyword-only |
| 4 | Gate failure behavior | BLOCKED | Not tested | NEW FINDING: gate failure is terminal, no retry path |
| 5 | Context handoff fidelity | PARTIAL | Not tested | NEW FINDING: only most recent step's outcome, beads compensate partially |
| 6 | Crash recovery UX | PARTIAL | WORKS | Downgrade: in-flight steps become zombies in interactive mode |
| 7 | Complexity override depth | WORKS | WORKS | New: gates survive light mode, risk still runs (cheap) |
| 8 | Agent routing accuracy | PARTIAL | WORKS (detect) | Downgrade: any framework without a config file is invisible |
| 9 | Bead usefulness | PARTIAL | Not tested | NEW FINDING: good primitives, reliability depends on agent compliance |
| 10 | Talent-builder quality | WORKS | WORKS | New: researches codebase, existing Python agent already covers FastAPI |
| 11 | Query power | WORKS | WORKS | New: cross-project analytics via central.db views |
| 12 | Upgrade friction | WORKS | PARTIAL (install) | Upgrade: smart settings merge, auto schema migration |

## New Findings vs Solo Audit

The team dialogue method revealed 6 findings that the solo audit missed
entirely:

1. **Gate failure is terminal** (Item 4) -- the most critical gap. No
   retry, no recovery. Solo audit only confirmed gates exist.

2. **Context handoff is single-step** (Item 5) -- only the most recent
   step's outcome text is forwarded. Bead relay partially compensates.
   Solo audit didn't examine this at all.

3. **Crash recovery has a zombie-step bug** (Item 6) --
   `recover_dispatched_steps()` exists but isn't wired into the
   interactive `resume` path. Solo audit rated this WORKS.

4. **`baton install` can't self-discover** (Item 1) -- even after pip
   install, the install command can't find its own agents. Solo audit
   saw the PyPI gap but not this deeper issue.

5. **Framework detection has systematic gaps** (Item 8) -- any
   framework without a required config file (FastAPI, HTMX, Flask) is
   invisible to the router. Solo audit only tested the happy path.

6. **Bead reliability is prompt-dependent** (Item 9) -- the debugging
   value of beads depends on agents following prompt instructions. Solo
   audit didn't examine beads.

## Recommended Priority Fixes

1. **Gate retry mechanism** -- allow `baton execute gate --phase-id N
   --result fail` followed by code fixes and `baton execute gate
   --phase-id N --result pass` without killing the execution. Change
   `record_gate_result(passed=False)` from terminal to retriable.

2. **Wire `recover_dispatched_steps` into resume** -- one-line fix in
   `execute.py` resume handler to call `engine.recover_dispatched_steps()`
   before `engine.resume()`.

3. **Self-discovery for `baton install`** -- resolve agent definitions
   from the installed package's data files when `--source` is not
   provided. Use `importlib.resources` to find bundled agents/.

4. **Multi-step handoff accumulation** -- instead of only the most
   recent step's outcome, accumulate outcomes from all completed steps
   (with a sliding window to manage context size).

5. **Framework detection for pip-based frameworks** -- scan
   `requirements.txt` / `pyproject.toml` dependencies for FastAPI,
   Flask, Starlette, HTMX, etc. Detection by dependency, not by config
   file presence.
