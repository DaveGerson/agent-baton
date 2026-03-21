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

2. **Start execution** with `baton execute start`. The engine initializes
   tracing, state persistence, and returns the first action.

3. **Drive the execution loop**: the engine returns DISPATCH, GATE, or
   COMPLETE actions. For DISPATCH, spawn the agent with the provided
   prompt. For GATE, run the specified check. Record results with
   `baton execute record` and `baton execute gate`.

4. **Finalize** with `baton execute complete`. The engine automatically
   writes trace data, usage logs, and retrospectives that feed the
   learning pipeline.

5. **Follow the git strategy**: create a feature branch before dispatching
   agents, commit each agent's work individually.

6. **Session recovery**: if a session crashes, `baton execute resume`
   picks up where it left off using the saved execution state.

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
