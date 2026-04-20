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

**Headless execution (default):** Use `baton execute run` — it drives the full loop to completion automatically. Switch to the manual `baton execute next` loop only for INTERACT gates, APPROVAL checkpoints, or debugging.

**Depth limit:** The orchestrator MUST run at the top level of a conversation. It cannot be dispatched as a subagent due to Claude Code's nesting limits.

**Plan amendments:** Use `baton execute amend` to add phases/steps mid-flight.
**Team steps:** Use `baton execute team-record` for parallel completions.

See `references/baton-engine.md` for the full CLI reference.

---

## Token Reduction — Standard Operating Procedures

Apply these in every baton session to control cost:

**Rule 1: Headless by default**
```bash
baton execute run   # ← default (not baton execute next)
```

**Rule 2: Real token tracking on every `record` call**
```bash
baton execute record --step 1.1 --agent backend-engineer \
  --status complete --outcome "..." \
  --session-id "$CLAUDE_SESSION_ID" \
  --step-started-at "2026-04-19T13:00:00Z"
```

**Rule 3: Terse dispatch output**
```bash
baton execute next --terse   # full prompt written to current-dispatch.prompt.md
```

**Rule 4: Compact plan summary only**
`baton plan --save` emits a 4-line summary. Never add `--verbose` unless you need to inspect the full plan in context.

**Rule 5: Trust knowledge dedup — don't re-inject manually**
The dispatcher tracks `delivered_knowledge` across dispatches. Docs already inlined in step 1.x are downgraded to reference in step 1.y automatically.

**Rule 6: File-references over inline output**
Pass file paths rather than re-reading and inlining content. Engine-recorded results don't need re-verification.

**Rule 7: Check real spend after significant sessions**
```bash
baton usage   # shows Real tokens vs Estimated
```
