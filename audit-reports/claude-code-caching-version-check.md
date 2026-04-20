# Claude Code Version + Caching-Bug Verification

**Date:** 2026-04-17
**Author:** worktree agent (branch `worktree-agent-a6b317fa`)
**Purpose:** Verify our installed Claude Code binary against the known
Feb–Mar 2026 prompt-caching bugs before attributing recent token burn
(~$5K session cost) to our own orchestration code.

---

## 1. Installed Version + Install Method

| Field | Value |
|-------|-------|
| `claude --version` | **2.1.112 (Claude Code)** |
| `which claude` | `/home/djiv/.local/bin/claude` |
| Install method | **Anthropic native installer** (self-update channel) |
| Symlink target | `/home/djiv/.local/share/claude/versions/2.1.112` |
| Released | 2026-04-16 (latest stable at time of check) |

Adjacent versions on disk: `2.1.107` (Apr 14), `2.1.110` (Apr 15), `2.1.112`
(Apr 17 local copy). No npm/pnpm/brew — direct Anthropic binary channel,
so upgrades are driven by `claude`'s own update flow (managed by hook
`gsd-check-update.js` + the CLI itself).

## 2. Known Caching Bugs — Status

### Bug A: Subagent prompt caching disabled by default (Issue #29966)

- **Reported:** 2026-03-02
- **Symptom:** Subagents spawned via the Agent tool had
  `enablePromptCaching ?? false` (main REPL uses `?? YWq(model)` → true).
  Every subagent request paid ~7k uncached tokens for tools/system prompt.
  Estimated 378k wasted tokens per session in the reported trace.
- **Changelog search for v2.1.109–2.1.112:** No explicit mention of
  issue #29966, `enablePromptCaching`, or subagent caching fixes.
- **Status at 2.1.112:** **UNCLEAR** — not acknowledged in changelog,
  GitHub issue reportedly still open as of last community check.

### Bug B: Conversation history cache invalidation on resumed sessions (Issue #40524 / #28899)

- **Reported:** 2026-02-26 (#28899, v2.1.59), expanded in #40524.
- **Symptom:** `cache_read` drops to 0 with large `cache_create` values
  on subsequent turns of long/resumed sessions — 10–20× token inflation.
- **Regression introduced:** after v2.1.67.
- **Community workaround repo (`cnighswonger/claude-code-cache-fix`):**
  "Confirmed through v2.1.111."
- **Partial related fixes shipped:**
  - v2.1.89 — "Fixed prompt cache misses in long sessions caused by tool
    schema bytes changing mid-session."
  - v2.1.101 — `DISABLE_TELEMETRY` falling back to 5-min TTL fix, plus
    `--resume`/`--continue` context-loss fix.
  - v2.1.108 — `ENABLE_PROMPT_CACHING_1H` env var exposure.
- **Status at 2.1.112:** **NOT FULLY PATCHED** per community monitoring
  repo. The v2.1.89/101/108 fixes address adjacent failure modes but do
  not clearly resolve the cache-invalidation-on-turn regression tracked
  in #40524.

### Overall verdict

**UNCLEAR / LIKELY NOT FULLY PATCHED.** We are on the very latest
release (2.1.112, one day old), so there is no newer upgrade target.
However the known regression is reported to persist through 2.1.111,
and the 2.1.112 changelog is a one-line fix ("claude-opus-4-7 temporarily
unavailable" auto-mode fix) — it does not address caching.

## 3. Local Config — Caching-Related Env Vars / Flags

### User settings (`~/.claude/settings.json`)
- No `CLAUDE_CODE_CACHE*`, `DISABLE_PROMPT_CACHING*`, `ENABLE_PROMPT_CACHING*`,
  `FORCE_PROMPT_CACHING*`, or `DISABLE_TELEMETRY` env var set.
- Only env var present: `ENABLE_LSP_TOOL=1`.
- Model pinned to `haiku` globally; `effortLevel: high`.
- Relevant non-caching hooks: gsd-context-monitor, gsd-check-update,
  gsd-session-state, gsd-statusline, workflow/prompt/read guards.

### Project settings (`.claude/settings.json`)
- No caching env vars or flags.
- Hook stack mirrors user settings (prompt guard + context monitor).

**Conclusion:** Nothing in our config is disabling prompt caching.
Caching behavior is entirely dependent on the Claude Code binary
internals. Whatever bugs exist in 2.1.112 are in full effect for us.

## 4. CLI Flags Available During Subagent Dispatch

From `claude --print --help` (v2.1.112), the caching-relevant surface is:

| Flag / Env | Effect |
|------------|--------|
| `--exclude-dynamic-system-prompt-sections` | Moves per-machine sections (cwd, env info, memory paths, git status) from system prompt into first user message. **Docs explicitly: "Improves cross-user prompt-cache reuse."** Only applies with default system prompt. |
| `ENABLE_PROMPT_CACHING_1H` (env) | Opt into 1-hour TTL (API key / Bedrock / Vertex / Foundry). Added in 2.1.108. |
| `FORCE_PROMPT_CACHING_5M` (env) | Force 5-minute TTL. |
| `DISABLE_PROMPT_CACHING*` (env) | Turn caching off (we should **not** set these). |

No flag directly addresses the subagent `enablePromptCaching ?? false`
issue from the CLI side — that path is internal to the Agent tool
dispatcher and only an upstream code fix can change its default.

## 5. Recommendation

**FURTHER INVESTIGATION — do not yet attribute the $5K burn to our code.**

Concrete next steps, in priority order:

1. **Instrument one real baton-execute session with verbose API logging**
   to confirm whether we are actually hitting the bug. Run one session
   with:
   ```
   OTEL_LOG_RAW_API_BODIES=1 claude -d api,hooks --debug-file /tmp/claude-api.log
   ```
   (`OTEL_LOG_RAW_API_BODIES` was added in 2.1.111.) Then grep the log
   for `cache_read_input_tokens` vs `cache_creation_input_tokens` on
   subagent (Agent tool) requests. If `cache_read` is 0 across repeated
   subagent dispatches within a single session, we are hitting Bug A.

2. **Add `--exclude-dynamic-system-prompt-sections` to headless runs**
   in `core/runtime/claude_launcher.py` / `headless.py`. Per Anthropic's
   own docs this is a direct cache-reuse improvement and is safe for
   our orchestrator-dispatched subagents.

3. **Subscribe to issue #29966 / #40524 + watch the changelog** —
   upgrade immediately when the fix lands (likely 2.1.113+).

4. **No downgrade.** Rolling back to 2.1.67 (the last "clean" version
   per community reports) would lose the fixes in 2.1.75, 2.1.84,
   2.1.86, 2.1.89, 2.1.97, 2.1.101, 2.1.108, plus Opus 4.7 + xhigh
   effort support. Net-negative.

5. **Keep auto-updates on.** `gsd-check-update.js` handles this today.

**Bottom line:** We are already on the newest build. There is nothing
left to upgrade to. The evidence that our $5K was caused by Claude Code
caching bugs vs. our own orchestration is circumstantial until step 1
is done — but the prior probability is high enough that we should
instrument before we spend engineering cycles optimizing our own plan
shapes.
