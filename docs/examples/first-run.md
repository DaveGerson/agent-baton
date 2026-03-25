# First Run — End-to-End Example

A complete walkthrough of planning and executing a task with Agent Baton.

## Prerequisites

- Agent Baton installed (`scripts/install.sh` or `baton install`)
- Python 3.10+, git
- Claude Code active in your terminal

## Step 1: Create a Plan

```bash
$ baton plan "Add request logging middleware to the FastAPI app" --save --explain
```

The planner detects your stack, selects agents, and writes `plan.json` and
`plan.md` to `.claude/team-context/`:

```
Task:    Add request logging middleware to the FastAPI app
Task ID: task-logging-a3f9
Risk:    LOW  |  Budget: standard  |  Mode: phased

Phase 1 — Implementation
  1.1  backend-engineer--python  [sonnet]  Add middleware, register with app
  Gate: pytest tests/

Phase 2 — Review
  2.1  code-reviewer  [sonnet]  Review for correctness and style

Explanation: LOW-risk scoped feature. Test gate after Phase 1 catches
regressions before the review phase.
```

Review `plan.md` and adjust if needed, then move on.

## Step 2: Start Execution

```bash
$ git checkout -b feat/request-logging
$ baton execute start
```

The engine initialises state and prints the first action, followed by the
session binding line:

```
ACTION: DISPATCH
  Agent: backend-engineer--python
  Model: sonnet
  Step:  1.1
  Message: Dispatch agent 'backend-engineer--python' for step 1.1.

--- Delegation Prompt ---
# Agent Task: 1.1 — Add request logging middleware
Read .claude/team-context/context.md for shared project context.
Add structured request logging middleware to the FastAPI application.
Capture method, path, status, and duration (ms). Register in app/main.py.
Write tests in tests/test_middleware.py.
--- End Prompt ---

Session binding: export BATON_TASK_ID=task-logging-a3f9
```

The `Session binding:` line appears after every `baton execute start`. Run
it to bind this shell to the task — required when running two tasks in
parallel across separate terminals:

```bash
$ export BATON_TASK_ID=task-logging-a3f9
```

## Step 3: Dispatch the Agent

Mark the step in-flight before spawning the agent:

```bash
$ baton execute dispatched --step-id 1.1 --agent backend-engineer--python
{"status": "dispatched", "step_id": "1.1"}
```

Use the Agent tool (inside Claude Code) to spawn the subagent with the
delegation prompt. The orchestrator does not do the work inline — the subagent does.

## Step 4: Record the Result

After the agent finishes, commit its work and record the outcome:

```bash
$ git add app/middleware.py tests/test_middleware.py
$ git commit -m "step 1.1: backend-engineer--python complete"

$ baton execute record \
    --step-id 1.1 --agent backend-engineer--python \
    --status complete \
    --outcome "Added LoggingMiddleware; logs method, path, status, duration" \
    --files "app/middleware.py,app/main.py,tests/test_middleware.py" \
    --commit a4d82cf
Recorded: step 1.1 (backend-engineer--python) — complete
```

`--status` only accepts `complete` or `failed`. Values like `pass` or `done`
raise an error.

## Step 5: Continue the Loop

```bash
$ baton execute next
```

All Phase 1 steps are done, so the engine returns a gate action:

```
ACTION: GATE
  Type:    test
  Phase:   1
  Command: pytest tests/
  Message: Run gate 'test' for phase 1.
```

Run the gate command and record the result:

```bash
$ pytest tests/
5 passed in 0.43s

$ baton execute gate --phase-id 1 --result pass
Gate recorded: phase 1 — PASS

$ baton execute next
ACTION: DISPATCH
  Agent: code-reviewer  |  Step: 2.1
  ...
```

Dispatch and record Phase 2 the same way as Steps 3–4:

```bash
$ baton execute dispatched --step-id 2.1 --agent code-reviewer
$ baton execute record --step-id 2.1 --agent code-reviewer \
    --status complete --outcome "Middleware looks correct; minor docstring note"
Recorded: step 2.1 (code-reviewer) — complete
```

## Step 6: Complete Execution

```bash
$ baton execute next
ACTION: COMPLETE
  Summary: Task task-logging-a3f9 completed successfully.

$ baton execute complete
Task task-logging-a3f9 completed.
Steps: 2/2  |  Gates passed: 1  |  Elapsed: 287s
Trace:         .claude/team-context/traces/task-logging-a3f9.json
Retrospective: .claude/team-context/retrospectives/task-logging-a3f9.md
```

## What Just Happened?

The engine planned the task, selected specialist agents, routed each step
with a scoped delegation prompt, ran a test gate between phases, and
recorded a full trace and retrospective. Every decision feeds the learning
pipeline for future runs.

## Next Steps

- `baton execute status` — check state at any time
- `baton execute list` — see all past executions
- `baton dashboard` — view metrics across executions
- `references/baton-engine.md` — full CLI reference

## Common Variations

**Crash recovery** — reload state after a session interruption:

```bash
$ baton execute resume
```

**Parallel dispatch** — for phases with independent steps:

```bash
$ baton execute next --all   # returns a JSON array of dispatchable actions
```

**Plan amendment** — add a phase when an agent uncovers unexpected work:

```bash
$ baton execute amend \
    --description "Need migration step for new config table" \
    --add-phase "Migration:backend-engineer--python"
```

**Concurrent tasks** — bind each shell to its own task after `start`:

```bash
$ export BATON_TASK_ID=<task-id>      # persists for the shell session
$ baton execute next --task-id <id>   # single-command override
```
