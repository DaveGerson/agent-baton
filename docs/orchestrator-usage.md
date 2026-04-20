# Orchestrator Usage

For complex tasks involving 3+ files across different layers, use the `baton` engine:

```bash
baton plan "task description" --save --explain \
    [--knowledge path/to/doc.md] \       # attach explicit knowledge
    [--knowledge-pack pack-name] \       # attach explicit knowledge pack
    [--intervention low|medium|high]     # escalation threshold
# Review plan.md — present summary to user, adjust if needed
baton execute start

loop:
  baton execute next
  if DISPATCH:
    baton execute dispatched --step ID --agent NAME
    → spawn Agent tool with the delegation_prompt ←
    baton execute record --step ID --agent NAME --status complete \
      --outcome "summary" --files "changed.py"
  if GATE:
    run gate command
    baton execute gate --phase-id N --result pass
  if APPROVAL:
    → present context to user, get decision ←
    baton execute approve --phase-id N --result approve
  if COMPLETE:
    baton execute complete
    break
```

**Headless execution:** For autonomous execution, use `baton execute run`.

**Depth limit:** The orchestrator MUST run at the top level of a conversation. It cannot be dispatched as a subagent due to Claude Code's nesting limits.

**Plan amendments:** Use `baton execute amend` to add phases/steps mid-flight.
**Team steps:** Use `baton execute team-record` for parallel completions.

See `references/baton-engine.md` for the full CLI reference.
