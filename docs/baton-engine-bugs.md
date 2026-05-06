# Baton Engine Bugs — Identified During Live Usage

> Captured 2026-03-26 during PMO UX Phase 4 execution and documentation overhaul session.

## BUG-001: `baton execute record` silently fails on SQLite UNIQUE constraint

**Symptom**: After `baton execute dispatched --step 1.1`, calling `baton execute record --step 1.1 --status complete` logs `SQLite save failed, falling back to file persistence: UNIQUE constraint failed: step_results.task_id, step_results.step_id` — but the JSON file fallback still writes `status: "dispatched"` instead of `"complete"`.

**Root cause**: The `step_results` table uses `INSERT OR IGNORE` (fixed to `INSERT OR REPLACE` in migrate.py but possibly not in `sqlite_backend.py`'s live write path). When the dispatched record exists, the complete record is silently dropped. The JSON fallback appears to re-read from SQLite rather than applying the new status.

**Impact**: Execution gets stuck in `ACTION: wait` permanently because the engine sees the step as still "dispatched".

**Fix needed**:
1. `sqlite_backend.py` step_results write path must use `INSERT OR REPLACE` or `ON CONFLICT DO UPDATE`
2. The JSON fallback path should apply the status update independently of SQLite success
3. `baton execute record` should exit non-zero when the record fails to persist

**Severity**: CRITICAL — blocks all orchestrated execution

---

## BUG-002: `baton execute next` stuck in `ACTION: wait` after step completion

**Symptom**: After recording step 1.1 as complete, `baton execute next` repeatedly returns `ACTION: wait — Waiting for in-flight steps to complete before proceeding.` The engine never advances to the next phase.

**Root cause**: Related to BUG-001. The engine reads step status from SQLite (which has "dispatched") rather than the JSON file (which may have "complete"). The two stores are out of sync.

**Impact**: Execution cannot proceed without manual JSON editing.

**Fix needed**:
1. Engine should have a single authoritative state source (SQLite preferred, with verified fallback)
2. `baton execute next` should detect and report state inconsistencies rather than silently waiting

**Severity**: CRITICAL — blocks execution flow

---

## BUG-003: `active_phase_index` set to `None` on start

**Symptom**: After `baton execute start`, `execution-state.json` has `"active_phase_index": null`. The engine's `next_action()` method may not advance properly when the phase index is null.

**Root cause**: `ExecutionEngine.start()` may not initialize the phase index, or it gets cleared during state serialization.

**Fix needed**: `start()` should set `active_phase_index = 0` if there are phases in the plan.

**Severity**: HIGH — may contribute to stuck execution

---

## BUG-004: Agent routing picks wrong stack flavor

**Symptom**: Planning for a Python/FastAPI project with a React frontend routes to `backend-engineer--node` instead of `backend-engineer--python`.

**Root cause**: The `AgentRouter.detect_stack()` may be picking up `package.json` from `pmo-ui/` and weighting it over `pyproject.toml` in the project root. The router's stack detection doesn't distinguish between primary project language and auxiliary tool languages.

**Impact**: Wrong agent dispatched; the node specialist doesn't know Python patterns.

**Fix needed**:
1. Stack detection should prioritize the root `pyproject.toml`/`setup.py` over nested `package.json` in subdirectories
2. Or: when both Python and JS are detected, the planner should create separate steps for backend (Python) and frontend (React) agents

**Severity**: MEDIUM — workaround is to manually specify agent in plan amendment

---

## BUG-005: Plan Phase 1 labeled "[APPROVAL REQUIRED]" but `requires_approval` is `false`

**Symptom**: `plan.md` shows `## Phase 1: Design [APPROVAL REQUIRED]` but the JSON plan has `"requires_approval": false`. The engine never emits an APPROVAL action for this phase.

**Root cause**: The markdown renderer adds the `[APPROVAL REQUIRED]` label based on the phase name containing "Design", but the planner doesn't set `requires_approval: true` in the JSON. The two are out of sync.

**Impact**: User expects to approve the design phase but the engine silently skips it.

**Fix needed**: Either:
1. The planner should set `requires_approval: true` for Design phases, OR
2. The markdown renderer should only show `[APPROVAL REQUIRED]` when the JSON flag is true

**Severity**: MEDIUM — misleading plan presentation

---

## BUG-006: `baton execute dispatched` succeeds but doesn't update `active_phase_index`

**Symptom**: After calling `baton execute dispatched --step 1.1 --agent architect`, the execution state still shows `"active_phase_index": null`. The engine should set the active phase to 0 (phase 1) when the first step is dispatched.

**Root cause**: `mark_dispatched()` may not update `active_phase_index`.

**Fix needed**: When dispatching a step, set `active_phase_index` to the phase containing that step.

**Severity**: MEDIUM — contributes to state machine confusion

---

## Workarounds Used

1. **BUG-001/002**: Manually edited `execution-state.json` via Python script to set step status to "complete" and `active_phase_index` to 0. This didn't fully work because the engine reads from SQLite.
2. **BUG-004**: Bypassed engine routing by dispatching agents directly with explicit `subagent_type` parameter.
3. **BUG-001/002**: Ultimately bypassed the engine entirely and dispatched backend/frontend agents in parallel without the execution loop.

---

## Prompt caching on subagent dispatch path (issue #29966)

**Context**: Anthropic issue #29966 reports that the in-process Agent tool
(where the top-level Claude Code session spawns packaged subagents) does not
set `enablePromptCaching` on the API requests it makes, reducing cache hit
rates for repeated system prompts.

**Does this affect Baton's headless path?** No.  `baton execute run` and
`ForgeSession` / `HeadlessClaude` both dispatch agents by shelling out to
`claude --print` as a top-level CLI invocation via
`asyncio.create_subprocess_exec`.  Each subprocess is its own independent
Claude Code process — not a nested in-process Agent tool call.  Issue #29966
only affects the in-process Agent tool path used by an orchestrator running
inside a Claude Code session.

**What about the session-based orchestrator path?** When the orchestrator
agent runs inside a Claude Code session and uses the Agent tool to dispatch
packaged subagents, issue #29966 does apply.  We have no direct control over
`enablePromptCaching` from within a Claude Code session; that is an internal
Claude Code concern to be fixed upstream.

**Caching behavior on the subprocess path**: Each `claude --print`
subprocess independently builds its API request.  We have no direct handle
on `enablePromptCaching` from a subprocess caller.  However, we can reduce
the dynamic content in the system prompt — which is the main obstacle to
cache reuse — by passing `--exclude-dynamic-system-prompt-sections`.  This
flag moves per-machine sections (cwd, env info, memory paths, git status)
from the system prompt into the first user message, making the system prompt
stable across dispatches from different machines or directories.

**Mitigation implemented** (2026-04-17): Both
`ClaudeCodeLauncher._build_command` and `HeadlessClaude.run` now append
`--exclude-dynamic-system-prompt-sections` when the installed Claude Code CLI
supports the flag.  Support is probed once at process startup via
`_supports_exclude_flag()` in `agent_baton/core/runtime/claude_launcher.py`
and cached for the process lifetime.  The flag is skipped when a custom
`--system-prompt` is injected (it is documented as a no-op in that case).

**Flag availability**: Confirmed present in the installed Claude Code version:

```
--exclude-dynamic-system-prompt-sections   Move per-machine sections (cwd,
  env info, memory paths, git status) from the system prompt into the first
  user message. Improves cross-user prompt-cache reuse. Only applies with
  the default system prompt (ignored with --system-prompt). (default: false)
```
