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

**Headless execution (default):** Use `baton execute run` — drives the loop to completion automatically. Switch to the manual `next` loop only for INTERACT gates, APPROVAL checkpoints, or debugging.

**Depth limit:** The orchestrator MUST run at the top level of a conversation. It cannot be dispatched as a subagent due to Claude Code's nesting limits.

**Plan amendments:** Use `baton execute amend` to add phases/steps mid-flight.
**Team steps:** Use `baton execute team-record` for parallel completions.

See `references/baton-engine.md` for the full CLI reference.

---

## Token Reduction — Standard Operating Procedures

Mandatory defaults in every baton session. Derived from a live audit (~$5K/session spend) and shipped across two release batches.

### 1. Pass real token tracking on every `record` call

```bash
baton execute record --step 1.1 --agent backend-engineer \
  --status complete --outcome "..." \
  --session-id "$CLAUDE_SESSION_ID" \
  --step-started-at "2026-04-19T13:00:00Z"
```

Activates `core/observe/jsonl_scanner.py`, which reads `~/.claude/projects/<slug>/<sid>.jsonl` and sums real token usage for the step's time window. Without it, the char/4 heuristic is used (off by ~3 orders of magnitude historically).

### 2. Use `--terse` on `execute next`

```bash
baton execute next --terse
```

Writes the full delegation prompt to `.claude/team-context/current-dispatch.prompt.md` and emits only a pointer — prevents multi-KB prompts from landing in the orchestrator's context window on every dispatch.

### 3. Plan saves as compact summary only

`baton plan --save` emits a 4-line compact summary by default. Use `--verbose` only when you need the full plan inline. Read `.claude/team-context/plan.md` from disk instead.

### 4. Trust session-level knowledge dedup

The dispatcher tracks `delivered_knowledge` across all dispatches in a session. Docs inlined in step 1.x are automatically downgraded to a reference pointer in step 1.y. Don't manually re-attach knowledge docs already delivered.

### 5. Check real token spend after sessions

```bash
baton usage
```

Shows **Real tokens: X (N steps with real data)** vs **Estimated tokens: Y**. If "none yet" appears, check that `--session-id` was passed to every `baton execute record` call.
