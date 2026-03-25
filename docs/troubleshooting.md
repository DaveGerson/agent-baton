# Troubleshooting

Quick-fix index for common Agent Baton issues. Find your symptom, follow the fix.

---

## Installation & Setup

### "command not found: baton"
**Cause**: Python package not installed or not on PATH.
**Fix**: `pip install -e ".[dev]"` from the agent-baton repo root.

### Agents don't appear in `/agents`
**Cause**: Agent `.md` files are not in `.claude/agents/`.
**Fix**: Run `scripts/install.sh` from the repo root, or manually copy `agents/*.md` into `~/.claude/agents/`.

### "install.sh: No such file or directory"
**Cause**: Running the install script from the wrong directory.
**Fix**: `cd /path/to/agent-baton && scripts/install.sh`

### Stack detection returns "unknown"
**Cause**: `baton plan` or `baton detect` cannot find config files (`pyproject.toml`, `package.json`, `go.mod`, etc.) in the current directory.
**Fix**: Pass `--project PATH` pointing to the directory that contains the config files. Subdirectory config files (e.g. a monorepo `backend/`) are not auto-detected.

```
baton plan "..." --save --project /path/to/project/root
```

---

## Planning

### Plan selects wrong agents
**Cause**: Stack detection returned `unknown` or a wrong framework.
**Fix**: Use `--agents agent1,agent2` to override, or diagnose with `baton detect --path .` and pass `--project PATH`.

### Plan has generic descriptions ("Implement feature", "Add tests")
**Cause**: Task summary passed to `baton plan` was too vague.
**Fix**: Provide a richer description — the planner uses it verbatim in delegation prompts.

```
# Too vague
baton plan "auth" --save

# Better
baton plan "Add JWT authentication middleware with login/logout endpoints and integration tests" --save
```

### "plan.json does not exist" on `baton execute start`
**Cause**: `baton plan` was not run with `--save`, or was run from a different directory.
**Fix**: `baton plan --save "task description"` then retry `baton execute start`.

---

## Execution

### "No active execution state found"
**Cause**: `baton execute next` or `baton execute record` was called before `baton execute start`, or `execution-state.json` was deleted.
**Fix**:
1. `baton execute resume` — recovers from saved state if it exists
2. Otherwise: `baton plan --save "..." && baton execute start`

### "invalid choice" for --status (expected 'complete' or 'failed')
**Cause**: `baton execute record --status pass` (or `done`, `success`, `ok`, etc.).
**Fix**: Use only `complete` or `failed`. To mark a step as in-flight, use `baton execute dispatched` instead.

```
# Wrong
baton execute record --step-id 1.1 --agent foo --status pass

# Correct
baton execute record --step-id 1.1 --agent foo --status complete
```

### Agent produced wrong output or failed
**Cause**: Scope too broad, missing context, or hallucination.
**Fix**: See `references/failure-handling.md` for the retry/replan protocol. Hard rule: max 1 retry per failure — if the retry fails, report to the user and stop.

### Gate fails with "No active execution state"
**Cause**: `baton execute gate` was called without a preceding `baton execute start`, or state was cleared.
**Fix**: Check `baton execute status`. If no active execution, start fresh.

### `baton execute complete` reports "No active execution state found"
**Cause**: `complete` was called before the COMPLETE action was returned, or the run was already finalised.
**Fix**: Verify `baton execute next` returned COMPLETE before calling `complete`. If the run was already finalised, data is already in `usage-log.jsonl` and `retrospectives/`.

### Permission prompts on every agent action
**Cause**: Agent frontmatter is missing `permissionMode: auto-edit`.
**Fix**: Add `permissionMode: auto-edit` to the agent definition's YAML frontmatter.

### Rate limited mid-execution
**Cause**: Too many API calls in a short window.
**Fix**: Start a new session, then `baton execute resume` — state is saved to disk and picks up where it left off.

---

## Concurrent Execution

### Wrong execution targeted in multi-terminal setup
**Cause**: Task ID resolved via `active-task-id.txt` (repo-wide marker) instead of the intended per-session binding.
**Fix**: Export the correct task ID in each terminal after `baton execute start`:

```bash
export BATON_TASK_ID=<task-id-from-start-output>
```

Check the current binding with `baton execute status` — the `Bound:` field shows which resolution path is active.

### Agentic callers lose BATON_TASK_ID between Bash calls
**Cause**: Env vars do not persist across independent `Bash` tool calls in an agent context.
**Fix**: Pass `--task-id <id>` explicitly on every CLI call when driving concurrent executions from an agent.

---

## Storage & Sync

### "no such table: external_sources"
**Cause**: Database schema is outdated.
**Fix**: `baton migrate-storage`

### "UNIQUE constraint failed"
**Cause**: Trying to add a source or record that already exists.
**Fix**: Remove first (`baton source remove <id>`), then re-add.

### Sync to central.db silently failing
**Cause**: Database locked, disk full, or permission denied on `~/.baton/central.db`.
**Fix**: Check file permissions, then retry with `baton sync`.

---

## Session Recovery

### Session crashed mid-execution
**Fix**:
1. `baton execute resume` — picks up from saved `execution-state.json`
2. If resume fails: `baton execute status` to see where it stopped
3. Check `git log --oneline` — commits per agent show what completed before the crash
4. Manually record remaining steps and call `baton execute complete`

### Orchestrator skips planning and goes straight to coding
**Fix**: Say explicitly: "Stop. Run `baton plan` first, then present the plan before doing any work."

---

## Still Stuck?

- `references/baton-engine.md` — full CLI reference, all flags, action types
- `references/failure-handling.md` — failure classification and recovery protocols
- `baton execute status` — always safe to run; shows current state without mutating it
