# Project Orchestration Rules

This project uses a multi-agent orchestration system. Agent definitions are
in `.claude/agents/` and reference procedures are in `.claude/references/`.

## Orchestrator Behavior (MANDATORY)

When the orchestrator agent is invoked:

1. **Read ALL reference documents** in `.claude/references/` before planning.
   This is not optional. The references contain procedures you execute inline.
   Read them at the start of every orchestrated task.

2. **Write the execution plan to disk** at `.claude/team-context/plan.md`
   before delegating to any agents. This enables session recovery if the
   session is interrupted.

3. **Create the shared context document** at `.claude/team-context/context.md`
   before dispatching agents. Every delegation prompt must include:
   "Read `.claude/team-context/context.md` for shared project context."

4. **Update the mission log** at `.claude/team-context/mission-log.md` after
   every agent completes. Write it to disk, do not hold it only in memory.

5. **Follow the git strategy**: create a feature branch before dispatching
   agents, commit each agent's work individually with the commit message
   convention from `.claude/references/git-strategy.md`.

6. **Log decisions**: include the DECISION LOGGING instruction in every
   delegation prompt (see `.claude/references/comms-protocols.md`).

7. **For document tasks**: use the doc-generation pipeline in
   `.claude/references/doc-generation.md` instead of the standard
   implementation workflow.

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
