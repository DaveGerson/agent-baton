# Worktree Isolation Fix for Concurrent Agent Dispatch

**Severity:** HIGH
**Recommendation:** halt parallel `isolation:"worktree"` dispatch until prompt-level fix lands.

The current state silently produces commits on the wrong branch and cross-agent index contamination. There is no detection layer. Every multi-agent parallel run on a non-trivial branch is at risk of fabricating an apparently-successful execution trace whose commits are anchored to a branch the orchestrator did not intend.

---

## 1. Root Cause Analysis

### Where worktree isolation lives (and doesn't)

Scan results across the agent-baton repo for handling of `isolation: "worktree"`:

- `agent_baton/core/orchestration/__init__.py` exposes `AgentRegistry`, `AgentRouter`, `ContextManager`, `KnowledgeRegistry` — none touch worktrees.
- `agent_baton/core/engine/dispatcher.py` `PromptDispatcher.build_delegation_prompt` builds the agent prompt from `shared_context`, `step.task_description`, `context_files`, `allowed/blocked_paths`, and signals. **No worktree awareness, no cwd directive, no path-anchoring guidance.**
- `references/baton-engine.md` (~2200 lines) **never mentions** "worktree", "isolation", "cwd", or anything analogous.
- `templates/CLAUDE.md` tells the orchestrator to call `Agent(agent_name=..., task=delegation_prompt)`. It says nothing about parallelism, isolation, or worktrees.
- `agents/orchestrator.md` and `agents/backend-engineer.md` contain zero worktree guidance.
- `feedback_concurrent_agent_isolation.md` (user memory) confirms the user discovered this needs `isolation:"worktree"` — but **this knowledge has never been promoted into the distributable templates or the dispatcher's prompt builder.**

**Conclusion:** `isolation:"worktree"` is a Claude Code SDK feature on the Agent tool. The agent-baton repo's responsibility is (a) to instruct the orchestrator to set it, and (b) to NOT undermine it through the prompts the dispatcher generates. Today the repo fails on both counts.

### Hypothesis ranking (likeliest first)

**H1 — Prompt-level project-root leakage (HIGH).** Dominant cause.
- `ContextManager.__init__` (`agent_baton/core/orchestration/context.py:73`) does `self._root = (...).resolve()`. Every path embedded in `context.md`, `plan.md`, the dispatch prompt's "Files to Read" section, `step.context_files`, and `step.allowed_paths` becomes an **absolute path anchored at the project root** — e.g. `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/...`.
- An agent in a worktree at `.claude/worktrees/agent-XXX/` will dutifully `Read` and `Write` the absolute paths — i.e. **edit the project root files**, not its worktree copies. This explains commits landing on `feat/` and the report of writing into project root.
- It also explains O1.4 sweeping in R3.3's pre-staged work: both agents were editing the same physical files at `/home/djiv/.../core/release/*`. When O1.4 ran `git add -A && git commit` from project root cwd, it staged everything there — including R3.3's in-flight files.

**H2 — Orchestrator did not actually pass `isolation:"worktree"` on every Agent call (MEDIUM).** Templates contain zero language about it. Even after the user's memory captured the rule, the orchestrator agent definition was never updated.

**H3 — Agent's `Bash` cwd defaults to parent (LOW-MEDIUM).** Without explicit `cd "$WORKTREE_PATH"` in every Bash invocation, `git commit` lands wherever the spawn's initial cwd happens to be.

**H4 — Shared `.git/index` across worktrees (LOW).** Standard `git worktree add` creates a per-worktree `index`. This works correctly unless the agent runs git from the wrong cwd.

**H5 — Worktree branch fast-forward to feat/ (LOW).** Less likely than H1; symptoms more parsimoniously explained by H1+H3.

**Compound cause: H1 + H2 + H3.** Even if the orchestrator passes `isolation:"worktree"` correctly (fixing H2), the prompt body contains absolute paths that pull the agent's writes back to project root (H1), and Bash invocations don't reliably cd into the worktree (H3).

---

## 2. Required Fixes

### Fix A — Prompt-level worktree contract (XS, blocks halt-removal)

Modify `agent_baton/core/engine/dispatcher.py::PromptDispatcher.build_delegation_prompt`. New optional parameter `isolation: str | None = None`. When `isolation == "worktree"`, prepend:

```
## Worktree Discipline (MANDATORY)
You are running in an isolated git worktree. Your cwd at spawn is your
worktree root. ALL file operations must be relative to your cwd.
- Before EVERY Bash call: prepend `cd "$PWD" &&` or use absolute paths
  rooted at your worktree, NOT the project root.
- For Read/Write/Edit: convert any absolute path you see in this prompt
  that begins with the project root to a path relative to your worktree.
- Run `git rev-parse --show-toplevel` once. That is your root. All git
  commands MUST report this path. If they don't, STOP and report.
- Never `cd` out of your worktree. Never reference `/home/.../<project>/`
  paths outside your worktree even if they appear in this prompt.
```

Then **rewrite all absolute paths in the prompt body to worktree-relative form** before emission. `context_files`, `allowed_paths`, `blocked_paths`, "Files to Read" — all relativized against the project root.

### Fix B — Orchestrator template directive (XS)

Add a "Concurrent Dispatch (MANDATORY)" section to `templates/CLAUDE.md` and `agents/orchestrator.md`:

> When dispatching ≥2 Agent subagents in the same message that modify code in this repo, **every** Agent call MUST include `isolation:"worktree"`. No exceptions. Single-agent or strictly sequential dispatch may omit it.

Promote `feedback_concurrent_agent_isolation.md` user memory into the distributed orchestrator prompt.

### Fix C — Engine signals worktree isolation when emitting parallel DISPATCH (S)

When `baton execute next --all` returns ≥2 dispatchable steps, mark each `ExecutionAction` with `isolation: "worktree"` (new field). Orchestrator template tells the orchestrator to honor this field on Agent invocation. Makes isolation **engine-driven, not orchestrator-judgment-driven**.

### Fix D — Pre-commit guard inside the worktree (S)

Install a pre-commit hook in worktrees that aborts the commit if `git rev-parse --show-toplevel` does not match a `BATON_WORKTREE_ROOT` env var injected at dispatch time. Catches H3 at the moment of damage.

### Fix E — Post-dispatch verification (M)

Add `baton execute verify-dispatch --step-id ID --commit HASH --expected-branch BRANCH` to the record path. Verifies:
- `git branch --contains HASH` includes `BRANCH` and **only** the expected branch
- The commit's tree differs from the parent only in files within `step.allowed_paths`
- No files in the commit appear in any other in-flight step's `allowed_paths`

### Fix F — Detection job (S)

Add `baton execute audit-isolation` that scans recent commits on the parent branch and flags commits whose author matches a dispatched agent but whose timestamp falls inside another in-flight step's window.

---

## 3. Test Plan

1. **Synthetic stress test** in `tests/integration/test_worktree_isolation.py`: 5 parallel steps, mock `claude --print`, assert each commit lands on its worktree branch, none on parent.
2. **Adversarial path test**: include in one step's `context_files` an absolute path to a file owned by another step. Fix A relativizes; assert agent's recorded files restricted to its `allowed_paths`.
3. **cwd drift test**: simulate `cd /tmp && git -C $WORKTREE commit`. Pre-commit hook (Fix D) rejects.
4. **Retrospective scan**: re-run failing 13-agent scenario. Expect 13 distinct branches, 13 distinct commits, zero contamination.
5. **CI gate**: `baton execute audit-isolation` returns zero violations.

---

## 4. Effort Summary

| Fix | Description | Effort |
|-----|-------------|--------|
| A | Prompt-level Worktree Discipline + path relativization | XS (1-2h) |
| B | Orchestrator template + agent definition updates | XS (30m) |
| C | Engine emits `isolation` field on parallel actions | S (3h) |
| D | Pre-commit guard in worktrees | S (3h) |
| E | `verify-dispatch` post-commit verification | M (1d) |
| F | `audit-isolation` retrospective detector | S (4h) |

**MVP to lift the halt:** A + B + C. ~6h. D+E+F are defense in depth.

---

## 5. Mitigation for Existing Contamination

For `feat/g1-governance-redaction-overrides`:
1. Identify intermixed commits via `git log --author=Claude --format="%H %s %ad" feat/g1-governance-redaction-overrides`.
2. For each contaminated commit, identify the intended branch from the dispatch's step ID prefix in the commit message (e.g. `O1.4` → `worktree-agent-XXX` of the O1 step).
3. Use `git rebase -i` with `edit` markers, or cherry-pick the wanted hunks onto a clean replacement branch.
4. For O1.4 specifically: `git show <commit> -- core/release/* tests/release/*` then `git reset HEAD~ -- core/release tests/release` to unstage R3.3's content from O1.4.

---

## 6. Alternatives Considered and Rejected

- **Tell agents not to write absolute paths.** The prompts themselves contain absolute paths.
- **Switch from worktree isolation to container isolation.** Heavier; incompatible with `BATON_DB_PATH` upward-walk + `BATON_TASK_ID` session binding.
- **Force sequential dispatch only.** Defeats the multi-team architecture and would significantly slow strategic-roadmap workflows.
- **Make the orchestrator commit on behalf of agents.** Recreates the contamination problem.

---

Source: ai-systems-architect investigation, 2026-04-25.
