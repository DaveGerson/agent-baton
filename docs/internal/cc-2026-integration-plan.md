# Claude Code 2026 Integration Plan

**Branch**: `claude/research-release-notes-sFoUg`
**Status**: Plan recorded; Tier-1 implementation paused for per-commit approval
**Date opened**: 2026-05-12
**Sources**:
- Claude Code changelog (Feb–May 2026, v2.1.92 → v2.1.139): <https://code.claude.com/docs/en/changelog.md>
- Hooks reference: <https://code.claude.com/docs/en/hooks.md>
- Skills reference: <https://code.claude.com/docs/en/skills.md>
- Sub-agents reference: <https://code.claude.com/docs/en/sub-agents.md>
- MCP reference: <https://code.claude.com/docs/en/mcp.md>

This is a living document. Each commit on `claude/research-release-notes-sFoUg` should update the **Status** column of the affected items.

---

## Tier-1 changes (do now, with per-commit approval)

| # | Change | Files | Complexity | Status |
|---|---|---|---|---|
| 1 | Fix `ActionType` doc drift (9 → 10; CHECKPOINT documented) | `CLAUDE.md`, `agent_baton/models/CLAUDE.md` | S | done (b4924a5) |
| 2 | Add shipped-agents validator regression test (baseline) | `tests/agents/test_shipped_agents_validate.py` (new) | S | pending |
| 3 | Expand agent frontmatter validator for CC 2.1.x fields | `agent_baton/core/govern/validator.py`, `agents/CLAUDE.md`, `tests/govern/test_validator_new_fields.py` (new) | M | pending |
| 4 | Document confirmed `permissionMode` allow-list | `agents/CLAUDE.md`, `docs/agent-roster.md` | S | pending |
| 5 | Pin `worktree.baseRef="head"` in templates + drift detection | `templates/settings.json`, `agent_baton/core/engine/worktree_manager.py`, `scripts/migrate_worktree_baseref.sh` (new) | S | pending |
| 6 | Wire `SessionStart` hook via `CLAUDE_ENV_FILE` for baton env seeding | `templates/settings.json`, `agent_baton/cli/commands/session_env.py` (new) | M | pending |
| 7 | CHECKPOINT plumbing + `PreCompact` hook (full end-to-end) | `agent_baton/cli/commands/execution/checkpoint.py` (new), `cli/commands/execution/execute.py` (`_print_action`), `core/engine/executor.py`, `templates/settings.json` | M-L | pending |
| 8 | Regression test asserting `--dangerously-skip-permissions` stays absent | `tests/runtime/test_no_dangerous_skip.py` (new) | S | pending |
| 9 | Upgrade notes doc | `docs/upgrade-notes.md` (new or extend) | S | pending |

Commit order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Each commit is atomic and reviewable; later items can be deferred without blocking earlier ones.

### Per-item caveats and execution notes

**#1 — ActionType doc drift.** Lowest risk. Code at `agent_baton/models/execution.py:62-74` has 10 values: DISPATCH, GATE, COMPLETE, FAILED, WAIT, APPROVAL, FEEDBACK, INTERACT, SWARM_DISPATCH, CHECKPOINT. Root `CLAUDE.md` and `models/CLAUDE.md` say 9. Pure doc fix.

**#2 — Shipped-agents regression test.** Captures the *current* validator behavior against the 33 shipped agents in `agents/`. Must go green before #3. If it fails today, that's a pre-existing bug we surface (not introduce). **Caveat**: if it fails, we have to decide between fixing the agents or relaxing the validator — likely a separate decision point.

**#3 — Expand validator.** Accept new CC 2.1.x optional fields: `isolation`, `background`, `skills`, `mcpServers`, `maxTurns`, `effort`, `disallowedTools`, `initialPrompt`. Expand `permissionMode` allow-list to `{default, acceptEdits, auto, dontAsk, bypassPermissions, plan}`. Keep `auto-edit` as legacy alias. **Policy rule (D4)**: reject `permissionMode: bypassPermissions` on `auditor`, `subject-matter-expert`, and any regulated-domain agent. Unknown top-level keys → warning, not error.

**#5 — Worktree baseRef pin.** CC v2.1.133 flipped the default. We pin `"head"` (matches our `expected_sha` invariant). The `worktree_manager.py` change is a non-fatal bead warning when user settings disagree. **Caveat**: the migration script must be idempotent and safe to run multiple times against user `.claude/settings.json` files.

**#6 — SessionStart env seeding.** Replaces ad-hoc per-CLI rediscovery. New `baton session-env --emit-env-file` is read-only and pure. **Caveat**: when no execution is active, write zero env lines (silent no-op) rather than erroring.

**#7 — CHECKPOINT plumbing (expanded scope).** Per audit findings, CHECKPOINT is currently enum-only. Full implementation requires:
- `checkpoint` CLI verb in `agent_baton/cli/commands/execution/`
- CHECKPOINT case in `cli/commands/execution/execute.py::_print_action`
- Executor in `core/engine/executor.py` yields CHECKPOINT (on `PreCompact` hook trigger, on long-running steps, or on demand)
- Persistence semantics: extend existing `StatePersistence` in `core/engine/persistence.py` to write a checkpoint marker (state snapshot + resume token)
- `PreCompact` hook in `templates/settings.json` calls `baton execute checkpoint --reason precompact || true`

**Caveats for #7:**
- This is the largest item and worth a brief design pass (or splitting) before implementation.
- Must not interfere with existing `resume` semantics.
- Tests need to cover: checkpoint during DISPATCH wait, checkpoint during GATE, checkpoint when no execution active.

---

## Tier-2 changes (planned, deferred — open as beads)

| # | Change | Files | Notes |
|---|---|---|---|
| B1 | `claude agents` CLI bridge → REST `/api/sessions` | `agent_baton/api/routes/sessions.py` (new), PMO UI | Gate behind `BATON_AGENTS_BRIDGE=1` |
| B2 | Expand baton skills frontmatter (model, effort, arguments, agent, allowed-tools, disable-model-invocation) | `templates/skills/baton-{beads,help,learn}/SKILL.md` | Empirical test needed for skill `agent` field nesting limit |
| B3 | `PostToolUse continueOnBlock` for secret-write blocker | `templates/settings.json` | Feed rejection reason back instead of silent block |
| B4 | `/goal` injection into DISPATCH delegation prompts | `agent_baton/core/engine/dispatcher.py::build_delegation_prompt` | Gate behind `BATON_GOAL_HINT=1` — at odds with "trust scope extensions" rule |
| B5 | Bash wrapper-stripping parity in `DataClassifier` | `agent_baton/core/govern/classifier.py` | Strip `env`, `sudo`, `watch`, `ionice`, `setsid` before classification |
| B6 | MCP foundation (`.mcp.json` scaffold + `enabledMcpjsonServers`) | `templates/.mcp.json` (new), `templates/settings.json` | Include `alwaysLoad` example in docs (confirmed v2.1.121+) |

---

## Tier-3 / WATCH (defer; revisit on next release-notes pass)

- **`context: fork`** — env-var only (`CLAUDE_CODE_FORK_SUBAGENT=1`) or `/fork` slash command. No frontmatter, no Agent SDK param. Orchestrator cannot programmatically drive forks. Revisit when an SDK release exposes it as a dispatch param.
- **New hook events** (`TaskCreated`, `TaskCompleted`, `TeammateIdle`, `WorktreeCreate`, `WorktreeRemove`, `Elicitation`, `ElicitationResult`) — would duplicate baton's existing trace events. Revisit only if mirroring into Claude Code's native observability stream becomes a goal.
- **Migrating from `SubagentStart`/`SubagentStop` to `TaskCreated`/`TaskCompleted`** — both event families still fire; no-op migration not worth the risk.
- **`UserPromptSubmit` / `UserPromptExpansion`** — covered by `SessionStart` + `CLAUDE_ENV_FILE` (Tier-1 #6) at lower surface area.
- **`templates/commands/` directory** — Skills are the recommended path; don't add a parallel command tree.
- **Agent SDK new dispatch params** (`background`, `skills`, `mcpServers` at call time) — baton uses `claude_launcher.py` + headless invocation, not the SDK directly. Revisit on SDK migration.
- **Opus 4.7 `xhigh` effort, Bedrock Mantle, Vertex AI wizard, Windows PowerShell tool, custom themes, `/buddy`, `/team-onboarding`, OTEL agent_id fields, plugin marketplace improvements** — track for awareness; no integration action.

---

## Risks and migration

### D1 — Worktree base ref default change (HIGH)
Existing baton deployments rely on the old default. Symptom: agents edit files based on `origin/<default>` and `expected_sha` assertion fires "Worktree HEAD does not match expected SHA — aborting" (loud but disruptive). **Mitigation**: Tier-1 #5 + migration script + upgrade notes (Tier-1 #9).

### D2 — Validator regression (BLOCKING)
Tier-1 #3 changes validator accept-set logic. Must run baseline against shipped 33 agents (Tier-1 #2) before changing the validator. Any pre-existing failures must be fixed or grandfathered in the same commit cycle.

### D3 — `--dangerously-skip-permissions` audit (CLEAN)
Audit confirmed zero usages anywhere in repo. Tier-1 #8 adds a regression test asserting the flag stays absent.

### D4 — `permissionMode: bypassPermissions` on regulated agents (POLICY)
Tier-1 #3 must include a validator rule blocking `bypassPermissions` for `auditor`, `subject-matter-expert`, and any agent flagged as regulated-domain. Existing `auto-edit` warning at `validator.py:217–223` is the precedent — escalate to error for `bypassPermissions`.

### D5 — PreCompact hook ordering
Compaction during a subagent tool call: parent's `PreCompact` runs but subagent state is on a separate transcript. **Document that CHECKPOINT covers parent state only** — subagents fold up on `SubagentStop` regardless.

---

## Resolved open questions (from research/audit passes)

| Question | Resolution | Source |
|---|---|---|
| Does `baton execute checkpoint` CLI verb exist? | **No.** CHECKPOINT is enum-only; never yielded, no `_print_action` case, no CLI registration. Referenced only in `tests/test_archetype_execution_models.py`. | Codebase audit (Explore agent) |
| Is `--dangerously-skip-permissions` used in baton? | **No.** Zero hits across repo. | Codebase audit (Explore agent) |
| Does MCP `alwaysLoad` field exist? | **Yes** (v2.1.121+). Boolean on all server types. Per-tool variant: `"anthropic/alwaysLoad": true` in `_meta`. | <https://code.claude.com/docs/en/mcp.md> |
| Is `context: fork` invocable programmatically? | **No.** Only `CLAUDE_CODE_FORK_SUBAGENT=1` env var or `/fork` command. No frontmatter, no SDK param. | <https://code.claude.com/docs/en/sub-agents.md> |

---

## Outstanding open questions

1. **Skills `agent` field semantics** — when a skill spawns a subagent, does the depth-1 nesting limit (per `templates/CLAUDE.md`) still apply, blocking the orchestrator from invoking skill-spawned agents? Needs empirical test before Tier-2 B2 ships.
2. **`isolation: worktree` precedence** — both agent frontmatter and dispatch-time can set it. If they disagree, baton currently authoritatively decides at dispatch time (`engine/executor.py`). Document the precedence in `agents/CLAUDE.md` when Tier-1 #3 lands.
3. **CHECKPOINT scope** (Tier-1 #7) — what state exactly should CHECKPOINT persist that the existing `StatePersistence` doesn't already? Worth a brief design note before implementation.

---

## Reference: notable items NOT acted on

These are tracked from the release-notes pass but explicitly skipped:
- `/buddy`, custom session colors, vim visual mode, fullscreen renderer, focus mode — UI-only.
- Remote control + mobile notifications — human workflow features.
- `/team-onboarding` — distributable usage patterns; nice-to-have, not load-bearing for baton.
- `/ultrareview` — external tool; reference but don't depend.

---

## Living-document update protocol

When a Tier-1 commit lands:
1. Update the **Status** column in the table (pending → in_progress → done, with the commit SHA).
2. Append a one-line "Decisions made" entry below this section if any of the per-item caveats were resolved differently than planned.
3. If new questions surface, add to "Outstanding open questions".
4. When Tier-1 is complete, open Tier-2 items as beads (`baton beads create --type task`) and link them from the table.

### Decisions made

- **Tier-1 #1 (ActionType doc drift)** — Scope reduced from 4 files to 2.
  - `agents/orchestrator.md` does not enumerate ActionTypes; skipped to avoid introducing a one-off mention.
  - `references/baton-engine.md` enumerates only 6 of the 9 pre-CHECKPOINT action types (already missing `FEEDBACK`, `INTERACT`, `SWARM_DISPATCH`). Adding only `CHECKPOINT` would extend that inconsistency. **Deferred** to a separate effort: document all missing action types in `references/baton-engine.md` (new follow-up below).
- New follow-up: **document missing action types in `references/baton-engine.md`** — FEEDBACK, INTERACT, SWARM_DISPATCH (and CHECKPOINT once it lands as more than enum-only). Not strictly part of the 2026 release-notes integration but surfaced during Tier-1 #1. Track as its own item; could go before, with, or after Tier-1 #7.
