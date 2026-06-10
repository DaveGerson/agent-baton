# Hooks & Enforcement Configuration

This document explains the hooks that mechanically enforce orchestrator
behavior — turning prompt-based "please do X" into "you literally cannot
skip X."

---

## What Hooks Solve

| Problem (Prompt-Only) | Fix (With Hooks) |
|----------------------|------------------|
| Orchestrator skips reading references | SubagentStart hook reminds it on launch |
| Mission log not updated | SubagentStop hook auto-appends a timestamp entry |
| Agent writes to .env or secrets/ | PreToolUse hooks block the write |
| team-context directory doesn't exist | SubagentStart hook auto-creates it |
| Policy not enforced at runtime | `baton policy-check` PreToolUse hook evaluates every tool call |
| Tool use not audit-logged | `baton comply-record` PostToolUse hook records every call |
| Session end not recorded | `baton comply-record --event-type session_stop` Stop hook |
| User doesn't know mission log exists | Stop hook prints the path when session ends |

---

## Runtime Policy Enforcement (Phase F)

Phase F adds three CLI commands that form a hook-driven enforcement layer.
This layer operates independently of the full execution engine — it works
during any Claude Code session, whether or not `baton execute` is running.

### Command overview

| Command | Hook event | What it does |
|---------|-----------|--------------|
| `baton classify --activate` | (setup, not a hook) | Writes `.claude/active-policy.json` so hooks know which preset to enforce |
| `baton policy-check` | PreToolUse | Reads the active policy; denies the tool call when a block rule matches |
| `baton comply-record` | PostToolUse / Stop | Appends a hash-chained entry to the compliance audit log |

### Setup: activating the policy

Before starting work on a task, run:

```bash
baton classify "your task description" --activate
```

This writes `.claude/active-policy.json` with the resolved PolicyEngine key
(`standard_dev`, `regulated`, etc.). The hooks read this file on every call —
no process restart required.

**Policy resolution fallback** (when no `active-policy.json` exists):
1. `.claude/team-context/plan.json` → `risk_level` (HIGH/CRITICAL → `regulated`)
2. Default: `standard_dev`

### `baton policy-check` — PreToolUse deny hook

Reads the PreToolUse JSON from stdin, evaluates the tool call against the
active policy, and emits a deny JSON when a blocking rule fires.

**Blocked rule types** (per-call enforcement):
- `path_block` + `severity="block"` → path matched a blocked glob pattern
- `tool_restrict` + `severity="block"` → tool name is in the restricted set

**Not blocked** (structural checks — require plan-level verification):
- `require_agent`, `require_gate` rules
- `path_allow` rules (advisory stderr warning only)

**Fail modes:**
- Default: fail-open. Bad stdin or unreadable policy → stderr warning, exit 0.
- `BATON_POLICY_FAIL_CLOSED=1`: exit 2 on errors (blocks the tool call).

### `baton comply-record` — PostToolUse / Stop record hook

Appends a hash-chained entry to `.claude/team-context/compliance-audit.jsonl`.
The same `ComplianceChainWriter` used by the execution engine is used here —
redaction and hash-chaining come free.

**Fail modes:**
- Default: fail-open. Write errors → stderr warning, exit 0.
- `BATON_COMPLIANCE_FAIL_CLOSED=1`: exit 1 on write errors.
- Malformed stdin: always silently ignored (exit 0).

---

## Claude Code Hooks (settings.json)

The `templates/settings.json` includes all hooks. To install:

```bash
# If .claude/settings.json doesn't exist:
cp templates/settings.json .claude/settings.json

# If it already exists, manually merge the "hooks" arrays — do NOT overwrite.
```

### Hook inventory

**SubagentStart (orchestrator matcher):**
Fires when the orchestrator agent starts. Prints a reminder to use
`baton plan` + `baton execute`. Also ensures `.claude/team-context/`
directory exists.

**SubagentStop (all agents):**
Fires when any subagent completes. Appends a timestamped entry to the
mission log. Safety net — ensures completion is recorded even if the
orchestrator skips it.

**PreToolUse — inline path guard (Write|Edit matcher):**
Fast bash inline check. Blocks writes to `.env`, `secrets/`, `node_modules/`,
`.pem`/`.key` files. Returns exit code 2 to deny the operation immediately
without spawning a Python process.

**PreToolUse — policy-check (Bash|Write|Edit|MultiEdit matcher):**
Calls `baton policy-check` with the full PreToolUse JSON on stdin. Enforces
the active guardrail policy (path_block + tool_restrict rules). Timeout: 10s.

**PostToolUse — comply-record (Bash|Write|Edit|MultiEdit matcher):**
Calls `baton comply-record` to append the tool-use event to the compliance
audit log. Timeout: 5s.

**PostToolUse — pyright filter (Edit|Write|MultiEdit matcher):**
Runs `.claude/hooks/pyright-filter.sh` if present. Filters Pyright diagnostics
to high-signal errors; strips cache-stale false positives. Silent no-op if
the script is absent.

**Stop — mission log:**
Prints the mission log path if it exists.

**Stop — comply-record session_stop:**
Calls `baton comply-record --event-type session_stop` to record the session
end in the compliance audit log.

---

## Adding Custom File Protection

### Claude Code

Add more paths to the inline PreToolUse hook's grep pattern:

```json
{
  "matcher": "Write|Edit",
  "hooks": [{
    "type": "command",
    "command": "bash -c 'FILE=\"$CLAUDE_TOOL_INPUT_FILE_PATH\"; if echo \"$FILE\" | grep -qE \"(\\.env|secrets/|node_modules/|prisma/schema.prisma|.github/workflows/)\"; then echo \"BLOCKED: Protected path: $FILE\" >&2; exit 2; fi'"
  }]
}
```

Alternatively, define custom rules in `.claude/policies/<preset>.json` and
they will be picked up by `baton policy-check` automatically (on-disk presets
take precedence over built-in presets of the same name).

---

## Codex Enforcement

Codex doesn't have the same hook system, but provides equivalent controls:

### sandbox_mode per agent

Already set in the TOML files:
- `sandbox_mode = "read-only"` for reviewers (auditor, SME, security, code-review)
- Implementation agents inherit parent sandbox (default: workspace-write)

### Config-level controls

In your `.codex/config.toml` (project) or `~/.codex/config.toml` (user):

```toml
[agents]
max_threads = 6        # Cap concurrent subagents
max_depth = 1          # Prevent subagents from spawning sub-subagents
```

### AGENTS.md reinforcement

The `AGENTS.md` file is read at session start and persists across the
conversation. It reinforces the same behaviors the Claude Code hooks enforce:
- Read all references before planning
- Write plan to disk before delegating
- Update mission log after each agent

---

## Validating Hooks Are Working

After installing `settings.json`:

1. Start a Claude Code session
2. Ask it to write to a `.env` file: "Create a .env file with DATABASE_URL=test"
3. The PreToolUse hooks should block it with a deny decision
4. If the write goes through, the hook isn't loaded. Check:
   - Is `settings.json` valid JSON? (`python -m json.tool .claude/settings.json`)
   - Did you restart the session after adding hooks?
   - Is the file at `.claude/settings.json` (project) or `~/.claude/settings.json` (user)?

### Manual smoke test

```bash
# Test policy-check deny:
echo '{"tool_name":"Write","tool_input":{"file_path":"/project/.env"},"session_id":"test"}' \
  | baton policy-check
# Expected: {"hookSpecificOutput": {"permissionDecision": "deny", ...}}

# Test comply-record append:
echo '{"tool_name":"Write","tool_input":{"file_path":"/project/src/main.py"},"session_id":"test"}' \
  | baton comply-record
# Expected: exit 0; entry appended to .claude/team-context/compliance-audit.jsonl

# Verify audit log integrity:
baton compliance verify
```
