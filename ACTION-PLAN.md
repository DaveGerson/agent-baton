# Action Plan: Red-Team Feedback Response

## Feedback Assessment

### Where the evaluator is right

**1. "Massive overengineering relative to actual runtime behavior"** — Agree.
The 19 agents and 12 references are the product. The Python package is
developer tooling that grew ahead of validated need. The README now leads
with what users get (agents + references) and positions the Python package
as optional.

**2. "Guardrails are prompt-level, not mechanically enforced"** — Agree.
`allowed_paths` / `blocked_paths` are prompt instructions, not filesystem
ACLs. The only mechanical enforcement is the `PreToolUse` hook. This is
a real limitation. Action required.

**3. "Risk assessment is keyword-based"** — Agree. Scanning for "production"
and "database" in task descriptions is brittle. Needs improvement.

**4. "No evidence of empirical validation"** — Agree. No usage data, no
A/B comparisons, no evidence the learning pipeline improves outcomes. The
learning infrastructure was built before the system ran at volume.

**5. "Test-to-code ratio is suspiciously high"** — Partially agree. The
ratio itself is fine (high coverage is good), but the concern that tests
validate internal consistency of speculative infrastructure rather than
real-world outcomes is valid.

**6. "Single real dependency, fragile install"** — Agree. The Python
environment setup should be more robust.

### Where we disagree

**"Ship the markdown, simplify the Python to ~2,000 lines"** — Too
aggressive. The execution engine (planner, executor, gates, dispatcher)
is ~1,800 lines and provides genuine value: crash recovery, phased
execution with QA gates, parallel dispatch with dependency tracking. These
capabilities don't exist in Claude Code natively. The state machine that
survives session crashes is not duplicating Claude Code functionality.

**"The Python layer duplicates what Claude Code already does natively"** —
Partially wrong. Claude Code can spawn agents and run tools, yes. But it
cannot: persist execution state across crashes, enforce phase ordering with
gates, track step dependencies for parallel dispatch, or generate structured
traces. The engine adds capabilities Claude Code doesn't have.

**"Governance/compliance modules are low value"** — Too early to say.
The guardrail-presets reference doc (prompt-level) is validated and useful.
The Python governance modules (classifier, policy engine, compliance
reporter) are not yet validated. Rather than delete them, we should flag
them as experimental and defer investment until there's demand.

---

## Action Items

### P0: Must do now

#### A1. Add mechanical path enforcement to the execution engine
**Problem**: `allowed_paths`/`blocked_paths` are prompt annotations, not
enforced. A confused agent can write anywhere.
**Action**: Add a `PreToolUse` hook generator that the execution engine
writes per-step, scoping Write/Edit permissions to the step's `allowed_paths`.
The hook blocks writes outside the boundary with `exit 2`. This turns prompt
annotations into mechanical enforcement.
**Files**: `agent_baton/core/engine/dispatcher.py`, `templates/settings.json`
**Effort**: Small

#### A2. Improve risk assessment beyond keyword matching
**Problem**: Keyword scanning for "production"/"database" is brittle.
**Action**: Augment keyword matching with structural signals:
- Check `git diff --name-only HEAD` for files being modified (migrations/,
  infra/, .env → higher risk)
- Check if task involves agents with Bash tool access (higher risk)
- Check if task touches paths matching guardrail-preset patterns
The classifier already has `_detect_file_risk()` — wire it into the planner.
**Files**: `agent_baton/core/engine/planner.py`, `agent_baton/core/govern/classifier.py`
**Effort**: Medium

#### A3. Validate with real usage — instrument the first 20 tasks
**Problem**: No empirical evidence the system improves outcomes.
**Action**: Use agent-baton on 3 real projects for 20+ orchestrated tasks.
Collect:
- Task completion rate (did the orchestrator finish successfully?)
- Gate pass rate (how often do QA gates catch real issues?)
- Agent dispatch count (are we using 3-5 per task as intended?)
- Crash recovery usage (did state persistence actually save anyone?)
- Compare wall-clock time vs single-agent approach for similar tasks
Write results to `reference_files/validation-results.md`.
**Files**: None (operational, not code)
**Effort**: Ongoing

### P1: Should do soon

#### B1. Flag speculative modules as experimental
**Problem**: Governance, learning, and distribution modules are built but
unvalidated. They create an impression of complexity.
**Action**: Add `_EXPERIMENTAL = True` flag to module docstrings for:
- `core/learn/` (pattern_learner, budget_tuner)
- `core/govern/` (classifier, compliance, escalation, policy)
- `core/distribute/` (registry_client, sharing, transfer, incident)
- `core/improve/` (evolution)
Do NOT delete them — they're tested and working. But make their status clear
in code and docs.
**Files**: Module docstrings
**Effort**: Small

#### B2. Harden the install path for Python environments
**Problem**: Tests fail out of the box due to Python path mismatches.
**Action**:
- Add a `Makefile` with `make install`, `make test`, `make lint` targets
  that handle venv creation and dependency installation
- Add a `pyproject.toml` `[tool.pytest.ini_options]` section with explicit
  Python path
- Test install from clean venv on Linux, macOS, and Windows
**Files**: `Makefile` (new), `pyproject.toml`
**Effort**: Small

#### B3. Separate "what users install" from "developer tooling" in project structure
**Problem**: The repo mixes the distributable product (agents, references,
templates) with the developer tooling (Python package, tests).
**Action**: The README already separates these conceptually. Reinforce in
the project structure docs. The key message: `agents/` + `references/` +
`templates/` + `scripts/` is the product. `agent_baton/` + `tests/` is
optional tooling.
**Files**: README.md (already done), CLAUDE.md
**Effort**: Done

### P2: Track for later

#### C1. Build mechanical enforcement for trust levels
**Problem**: `trust_level` (FULL_AUTONOMY, SUPERVISED, RESTRICTED, PLAN_ONLY)
is a prompt annotation with no backing.
**Action**: Map trust levels to Claude Code permission modes in the agent
frontmatter. RESTRICTED agents get `permissionMode: plan` (ask before every
tool use). SUPERVISED get `permissionMode: auto-edit` (auto-approve edits,
ask for Bash). This IS mechanical — Claude Code enforces permission modes.
**When**: After P0 validation confirms the system is used.

#### C2. A/B comparison: agent-baton vs vanilla Claude Code
**Problem**: No evidence agent-baton beats well-written CLAUDE.md instructions.
**Action**: After 20 task validation (A3), run 10 comparable tasks with and
without agent-baton. Measure: completion rate, code quality (test pass rate),
time, token usage. If agent-baton doesn't measurably improve outcomes,
reassess the project.
**When**: After A3 completes.

#### C3. Prune speculative modules based on usage data
**Problem**: Learning, governance, and distribution modules may never be
exercised.
**Action**: After 6 months of usage data, check which `baton` CLI commands
are actually used. Modules with zero real-world usage get deprecated and
moved to a `contrib/` directory.
**When**: 6 months after first real deployment.

---

## Execution Priority

```
Now         P0-A1  Mechanical path enforcement (small, high impact)
            P0-A2  Improve risk assessment (medium, high impact)
            P0-A3  Start 20-task validation (ongoing)
            P1-B1  Flag experimental modules (small)
            P1-B2  Harden Python install (small)

After       P2-C1  Mechanical trust levels
validation  P2-C2  A/B comparison
            P2-C3  Prune unused modules
```
