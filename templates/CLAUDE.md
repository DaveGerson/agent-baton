# Project Orchestration Rules

This project uses a multi-agent orchestration system powered by the
agent-baton execution engine. Agent definitions are in `.claude/agents/`
and reference procedures are in `.claude/references/`.

## Orchestrator Behavior (MANDATORY)

When the orchestrator agent is invoked, it drives tasks through the
execution engine:

1. **Create a plan** using `baton plan "task description" --save --explain`.
   The engine handles agent routing, risk assessment, budget selection,
   and phase sequencing. It writes `plan.json` and `plan.md` to
   `.claude/team-context/`.

2. **Review the plan** — read `.claude/team-context/plan.md` and present
   a brief summary to the user: phases, agents, and step descriptions.
   If the plan looks wrong (wrong agents for the task, too many phases,
   missing steps), re-run `baton plan` with explicit overrides:
   `baton plan "task" --save --task-type TYPE --agents "agent1,agent2"`
   Proceed only when the plan makes sense for the task.

3. **Start execution** with `baton execute start`. The engine initializes
   tracing, state persistence, and returns the first action.

4. **Drive the execution loop**: the engine returns DISPATCH, GATE, WAIT,
   or COMPLETE actions. Handle each action type as follows:

   **For DISPATCH actions — you MUST use the Agent tool:**
   - Read the `delegation_prompt` from the engine output (between the
     `--- Delegation Prompt ---` and `--- End Prompt ---` markers)
   - Use the Agent tool to spawn a subagent matching the agent name shown
     in the DISPATCH output
   - Pass the delegation prompt as the agent's task
   - Do NOT do the work yourself — the point is specialist delegation
   - Call `baton execute dispatched --step-id STEP --agent NAME` before
     spawning so the engine tracks the step as in-flight
   - After the agent returns, record the result:
     ```
     baton execute record \
         --step-id STEP \
         --agent NAME \
         --status complete \
         --outcome "brief summary of what was done" \
         --files "file1.py,file2.py" \
         --commit HASH
     ```
   - Valid `--status` values: `complete` or `failed` — no other values
   - Then call `baton execute next` to get the next action

   **For GATE actions:**
   - Run the command shown in `Command:` using Bash
   - Record the result with:
     ```
     baton execute gate --phase-id N --result pass|fail --output "output"
     ```
   - Then call `baton execute next`

   **For COMPLETE actions:**
   - Call `baton execute complete` to finalize the run

   **For FAILED actions:**
   - Do not call `baton execute complete`
   - Report the failure details to the user

5. **Finalize** with `baton execute complete`. The engine automatically
   writes trace data, usage logs, and retrospectives that feed the
   learning pipeline.

6. **Follow the git strategy**: create a feature branch before dispatching
   agents, commit each agent's work individually.

7. **Session recovery**: if a session crashes, `baton execute resume`
   picks up where it left off using the saved execution state.

## Execution Loop Reference

```
baton plan "task" --save --explain
# Review plan.md — present summary to user, adjust if needed
git checkout -b feat/task-name
action = baton execute start

loop:
    if action.type == DISPATCH:
        baton execute dispatched --step-id STEP --agent NAME
        result = Agent(agent_name=NAME, task=delegation_prompt)
        git add -A && git commit -m "step STEP: NAME complete"
        baton execute record --step-id STEP --agent NAME \
            --status complete --outcome "..." --files "..." --commit HASH
        action = baton execute next

    elif action.type == GATE:
        output = bash(gate_command)
        baton execute gate --phase-id N \
            --result pass|fail --output "output"
        action = baton execute next

    elif action.type == COMPLETE:
        baton execute complete
        break

    elif action.type == FAILED:
        # report failure, stop
        break
```

For the full command reference, error list, and file layout, read
`.claude/references/baton-engine.md`.

## Regulated Domain Rules

Any work touching regulated data, compliance systems, audit-controlled
records, or industry-specific business rules MUST:
- Involve the `subject-matter-expert` agent for domain context
- Involve the `auditor` agent for pre-execution and post-execution review
- Follow the Regulated Data guardrail preset

## Agent Invocation

For complex tasks involving 3+ files across different domains, use the
`orchestrator` agent. Do not attempt to handle multi-domain tasks directly —
delegate to the orchestrator which will plan, research, and coordinate
specialist agents.

For simple, single-domain tasks (bug fixes, small features, utility
functions), work directly without the orchestrator.
