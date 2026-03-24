# Implementation Plan: ClaudeCodeLauncher + Daemonization

**Date**: 2026-03-22
**Source**: Independent analysis by 3 architecture agents reading the codebase from scratch
**Confidence**: HIGH — all 3 agents converged on the same architecture and scope

---

## Executive Summary

The async runtime infrastructure is complete and tested. The **only
production gap** is a real `AgentLauncher` implementation that invokes
the `claude` CLI. Everything else — engine, worker, scheduler, event bus,
persistence, decisions, supervisor — is wired and working.

Daemonization is a secondary concern that has two sub-gaps: (1) the
supervisor doesn't fork/detach, and (2) `SignalHandler` and
`DecisionManager` are defined but not wired. These are important for
the "walk away" use case but the system becomes useful the moment a
real launcher exists.

**Total new code**: ~450 LOC (launcher) + ~150 LOC (daemon improvements)
**Files modified**: 3 (daemon.py, 2x __init__.py)
**Files created**: 2 (claude_launcher.py, test_claude_launcher.py)
**New dependencies**: None (stdlib only)
**Existing tests affected**: Zero

---

## Part 1: ClaudeCodeLauncher

### 1.1 Why This Is the Only Blocker

Three agents independently identified the same TODO at `daemon.py:53`:

```python
launcher = DryRunLauncher() if args.dry_run else DryRunLauncher()
# TODO: Replace second DryRunLauncher with real ClaudeCodeLauncher
```

Both branches use `DryRunLauncher`. The entire pipeline from
`baton daemon start` through `WorkerSupervisor` → `TaskWorker` →
`StepScheduler` → `AgentLauncher.launch()` works correctly with
any launcher satisfying the protocol. The protocol is:

```python
class AgentLauncher(Protocol):
    async def launch(
        self, agent_name: str, model: str, prompt: str, step_id: str = ""
    ) -> LaunchResult: ...
```

### 1.2 What the Launcher Must Do

```
Input:  agent_name="backend-engineer--python", model="sonnet",
        prompt="<delegation prompt text>", step_id="1.1"

1. Locate `claude` binary (shutil.which or env var)
2. Build CLI command with non-interactive flags
3. Run via asyncio.create_subprocess_exec with timeout
4. Parse structured output (JSON if available, raw text fallback)
5. Detect git changes (pre/post commit hash, changed files)
6. Return LaunchResult with all fields populated

Output: LaunchResult(step_id="1.1", agent_name="backend-engineer--python",
        status="complete", outcome="...", files_changed=["a.py"],
        commit_hash="abc123", estimated_tokens=5000, duration_seconds=45.0)
```

### 1.3 Claude Code CLI Integration

The launcher invokes `claude` in non-interactive mode:

```bash
claude --print \
  --model <mapped_model_id> \
  --output-format json \
  -p "<prompt>"
```

Or for large prompts (>128KB), pipe via stdin:

```bash
echo "$PROMPT" | claude --print --model <model> --output-format json -
```

**Model mapping** (configurable, not hardcoded):

| Plan value | CLI model flag |
|-----------|---------------|
| `"sonnet"` | `sonnet` (Claude Code resolves to latest) |
| `"opus"` | `opus` |
| `"haiku"` | `haiku` |

### 1.4 LaunchResult Field Mapping

| Field | Source |
|-------|--------|
| `status` | Exit code 0 + no `is_error` → `"complete"`. Otherwise → `"failed"` |
| `outcome` | `result` field from JSON output, truncated to 4000 chars |
| `files_changed` | Post-hoc `git diff --name-only <pre_hash> HEAD` |
| `commit_hash` | `git rev-parse HEAD` if changed from pre-launch snapshot |
| `estimated_tokens` | `usage.input_tokens + usage.output_tokens` from JSON |
| `duration_seconds` | `duration_ms / 1000` from JSON, or wall-clock fallback |
| `error` | stderr content, or `result` when `is_error=true` |

### 1.5 Error Handling

| Failure Mode | Detection | Action |
|-------------|-----------|--------|
| `claude` not installed | `shutil.which()` returns `None` at construction | `RuntimeError` — fail fast |
| Authentication failure | stderr contains "auth" or "API key" | `status="failed"`, descriptive error |
| Agent timeout | `asyncio.wait_for` exceeds timeout | Kill process, `status="failed"` |
| Rate limiting | stderr contains "rate limit" or "429" | Retry with exponential backoff (max 3 retries, 5s base) |
| Malformed JSON output | `json.JSONDecodeError` | Fall back to raw text parsing |
| Context overflow | stderr contains "context" + "exceeded" | `status="failed"`, diagnostic error |
| Subprocess crash | `OSError` or `SubprocessError` | Catch, wrap in LaunchResult |

Only rate limits trigger internal retry. All other failures return
immediately — the engine has its own retry logic.

### 1.6 Configuration

```python
@dataclass
class ClaudeCodeConfig:
    claude_path: str = "claude"
    working_directory: Path | None = None    # defaults to cwd
    default_timeout_seconds: float = 600.0   # 10 min
    model_timeouts: dict[str, float] = field(default_factory=lambda: {
        "opus": 900.0, "sonnet": 600.0, "haiku": 300.0,
    })
    max_retries: int = 3                     # rate limits only
    base_retry_delay: float = 5.0
    max_outcome_length: int = 4000
    prompt_file_threshold: int = 131072      # 128KB
    env_passthrough: list[str] = field(default_factory=lambda: [
        "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX", "AWS_PROFILE", "AWS_REGION",
    ])
```

### 1.7 Async Strategy

Use `asyncio.create_subprocess_exec` (native async), NOT `subprocess.run`
in `asyncio.to_thread`. Reasons:

- True async cancellation on timeout (kill process + await wait)
- Cooperates with `StepScheduler`'s semaphore correctly
- Future: streaming stdout for progress events

### 1.8 Git State Tracking

Before launch:
```python
pre_commit = await _git("rev-parse", "HEAD")
```

After launch:
```python
post_commit = await _git("rev-parse", "HEAD")
if post_commit != pre_commit:
    files_changed = (await _git("diff", "--name-only", pre_commit, post_commit)).splitlines()
    commit_hash = post_commit
```

**Race condition note**: Parallel agents committing simultaneously could
produce incorrect attribution. This is acceptable for v1 — the engine's
commit-per-agent git strategy is prompt-enforced, not mechanically
enforced.

### 1.9 File Location

```
agent_baton/core/runtime/claude_launcher.py    # NEW — ~350-450 LOC
tests/test_claude_launcher.py                  # NEW — ~250-350 LOC
```

### 1.10 Testing Strategy

**Layer 1 — Unit tests (mock subprocess)**:
Mock `asyncio.create_subprocess_exec` to return controlled stdout/stderr/exit codes.

| Test Case | Asserts |
|-----------|---------|
| Happy path (JSON output) | `status="complete"`, tokens populated |
| Agent failure | `status="failed"`, error populated |
| Malformed JSON | Falls back to raw parsing |
| Timeout | `status="failed"`, error contains "timed out" |
| Rate limit + retry | Internal retry, then result |
| Rate limit exhausted | `status="failed"` after max retries |
| `claude` not installed | `RuntimeError` at construction |
| Git changes detected | `files_changed` and `commit_hash` populated |
| Large prompt | Uses temp file delivery |

**Layer 2 — Protocol conformance**:
Run the same test suite as DryRunLauncher against a mocked ClaudeCodeLauncher.

**Layer 3 — Integration (opt-in)**:
`@pytest.mark.skipif(not shutil.which("claude"))` — single trivial-prompt test.

### 1.11 Known Gap: path_enforcement

The `ExecutionAction.path_enforcement` field (a bash command for
PreToolUse hooks) is available on the action but the worker discards
it when building step dicts for the scheduler. The launcher never
receives it.

**v1 approach**: Rely on the global `PreToolUse` hook in
`settings.json` for protected paths (.env, secrets). Per-step path
enforcement is prompt-only, not mechanically enforced.

**Future**: Extend the worker→scheduler→launcher chain to pass
path_enforcement, and have the launcher inject it as a temporary
settings overlay.

---

## Part 2: Daemonization

### 2.1 Current State (verified by all 3 agents)

| Component | Status |
|-----------|--------|
| `supervisor.start()` | **Blocks** — calls `asyncio.run()`, never returns until plan completes |
| `SignalHandler` | **Dead code** — defined in `signals.py`, never imported or used |
| `DecisionManager` ↔ Worker | **Not wired** — decisions work standalone via CLI, worker never creates/checks them |
| Crash recovery | **Engine-level only** — `engine.resume()` works but no CLI `--resume` flag. Dispatched-step zombie problem exists |

### 2.2 Daemonization Strategy: Double-fork (manual)

Do NOT use `python-daemon` library:
- Breaks asyncio (closes event loop FDs)
- Conflicts with existing PID management
- Adds external dependency

Instead, ~40 lines of standard UNIX double-fork in a new file:

```
agent_baton/core/runtime/daemon.py
```

1. First fork → parent exits (detaches from terminal)
2. `os.setsid()` → child becomes session leader
3. Second fork → session leader exits (can't reacquire terminal)
4. Redirect stdin/stdout/stderr to `/dev/null` (after logging setup)
5. Preserve working directory (agent-baton uses relative paths)

Must be called BEFORE `asyncio.run()`.

### 2.3 Signal Handler Wiring

Wire `SignalHandler` into supervisor by replacing:

```python
# Current:
summary = asyncio.run(worker.run())
```

With:

```python
# New:
summary = asyncio.run(self._run_with_signals(worker))

async def _run_with_signals(self, worker):
    handler = SignalHandler()
    handler.install()
    worker_task = asyncio.create_task(worker.run())
    signal_task = asyncio.create_task(handler.wait())
    done, _ = await asyncio.wait(
        {worker_task, signal_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if signal_task in done:
        worker_task.cancel()
        try:
            await asyncio.wait_for(worker_task, timeout=30.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        return "Daemon stopped by signal."
    signal_task.cancel()
    return worker_task.result()
```

### 2.4 Decision Integration

Wire `DecisionManager` into the worker's `_handle_gate()`:

| Gate type | Behavior |
|-----------|----------|
| `test`, `build`, `lint` | Auto-approve (run command, check exit code) |
| `review`, `approval` | Create `DecisionRequest`, poll filesystem for resolution |

The worker polls `decisions_dir` every 2 seconds for the resolution file
(written by `baton decide --resolve`). This is cross-process
communication via the filesystem — matches the project's file-based
philosophy.

The existing `_wait_event` and `notify_resolution()` on the worker
are scaffolding for future IPC (Unix domain socket). For v1, filesystem
polling is simpler and more reliable.

### 2.5 Crash Recovery

**Add `--resume` flag** to `baton daemon start`:

```bash
baton daemon start --resume    # loads from execution-state.json
```

**Fix dispatched-step zombie problem**: Add
`engine.recover_dispatched_steps()` that clears stale `dispatched`
markers on resume. Steps that were in-flight when the daemon crashed
become eligible for re-dispatch.

**Atomic state writes**: Change `_save_state()` to write-then-rename
to prevent corrupted JSON from mid-write crashes.

### 2.6 Process Management

**Single-instance enforcement**: Use `fcntl.flock()` on the PID file
instead of just writing/reading it. The OS releases the lock on crash,
preventing stale-PID-file problems.

**Stop with wait**: `baton daemon stop` should send SIGTERM, then poll
for process exit (up to 30s), then report success/timeout.

**Log rotation**: Replace `FileHandler` with `RotatingFileHandler`
(10MB limit, 3 backups).

### 2.7 CLI Changes

```
baton daemon start --plan FILE [--dry-run] [--max-parallel N]
                   [--foreground] [--resume] [--project-dir DIR]
baton daemon status
baton daemon stop
```

New flags:
- `--foreground` — skip daemonization, run in foreground (for debugging)
- `--resume` — resume from saved execution state
- `--project-dir` — working directory for Claude Code (default: cwd)

---

## Part 3: Dependency Order and Build Sequence

### 3.1 Critical Path

```
ClaudeCodeLauncher (NEW, independent)
    ├── can be built and tested in complete isolation
    ├── zero dependencies on daemonization
    └── makes the system USEFUL immediately
         │
         ▼
daemon.py CLI wiring (MODIFY, 5 lines)
    └── depends on ClaudeCodeLauncher existing
         │
         ▼
__init__.py exports (MODIFY, 4 lines)
    └── depends on ClaudeCodeLauncher existing
```

```
Daemonization (MODIFY supervisor + NEW daemon.py)
    ├── can be built independently of ClaudeCodeLauncher
    ├── signal wiring, decision integration, crash recovery
    └── makes the system AUTONOMOUS
```

### 3.2 Recommended Build Order

| Step | What | Files | Depends On | Risk |
|------|------|-------|------------|------|
| **1** | `ClaudeCodeLauncher` + config | `core/runtime/claude_launcher.py` | Nothing | MEDIUM |
| **2** | Launcher tests | `tests/test_claude_launcher.py` | Step 1 | LOW |
| **3** | CLI wiring | `cli/commands/daemon.py` | Step 1 | LOW |
| **4** | Exports | `core/runtime/__init__.py`, `core/__init__.py` | Step 1 | LOW |
| **5** | Daemonize function | `core/runtime/daemon.py` | Nothing | MEDIUM |
| **6** | Signal wiring | `core/runtime/supervisor.py` | Step 5 | MEDIUM |
| **7** | Decision integration | `core/runtime/worker.py` | Nothing | MEDIUM |
| **8** | Crash recovery | `core/engine/executor.py`, `cli/commands/daemon.py` | Nothing | MEDIUM |
| **9** | Process management | `core/runtime/supervisor.py` | Steps 5-6 | LOW |
| **10** | Daemon tests | `tests/test_daemon.py` (expanded) | Steps 5-9 | LOW |

Steps 1-4 are the **minimum viable delivery**. Steps 5-10 complete the
"walk away" use case.

Steps 1-4 and 5-10 can be built **in parallel** (different branches).

### 3.3 What Must NOT Change

| File | Why |
|------|-----|
| `launcher.py` | Protocol is the integration boundary — changing it breaks all launchers |
| `worker.py` (for Part 1) | Worker is launcher-agnostic by design |
| `scheduler.py` | Delegates through protocol, no launcher knowledge |
| `executor.py` (for Part 1) | Engine doesn't know how agents are launched |
| `cli/main.py` | Auto-discovers commands, never edit |
| All 1901 existing tests | Zero regressions. New launcher has its own test file |

---

## Part 4: Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Claude Code CLI flags change between versions | HIGH | Isolate all flag construction in `_build_command()`. Version-detect at startup. Fallback parser for non-JSON output. |
| Parallel subprocess resource exhaustion (3 Claude processes = 1.5GB+ RAM) | MEDIUM | `SchedulerConfig.max_concurrent` already caps parallelism. Add model-specific timeouts. |
| path_enforcement not mechanically enforced | MEDIUM | Global `PreToolUse` hook covers protected paths. Per-step enforcement is prompt-only in v1. |
| Zombie processes from daemon crash | HIGH | `fcntl.flock()` on PID file. `status()` verifies PID liveness. |
| Corrupted execution-state.json from mid-write crash | HIGH | Atomic write (tmp + rename). |
| Dispatched-step zombies on crash recovery | HIGH | `recover_dispatched_steps()` clears stale markers. |
| Terminal close kills foreground daemon | LOW | Double-fork daemonization solves this. `--foreground` available for debugging. |
| API key exposure in subprocess environment | MEDIUM | `env_passthrough` whitelist — only listed variables are forwarded. |

---

## Part 5: Success Criteria

1. `baton daemon start --plan plan.json` launches real Claude Code agents
   that modify files, commit code, and produce non-synthetic output.

2. `baton daemon start --plan plan.json --dry-run` continues to work
   exactly as before (DryRunLauncher, synthetic results).

3. A 3-step plan with parallel steps dispatches 2+ agents concurrently
   (verified via launcher.launches or event timestamps showing overlap).

4. Rate-limited API responses trigger automatic retry with backoff,
   not immediate failure.

5. `baton daemon start --plan plan.json` (without `--foreground`) returns
   the terminal immediately, with the daemon running in the background.

6. `baton daemon stop` sends SIGTERM, the daemon drains in-flight agents
   (up to 30s), persists state, and exits cleanly.

7. After a daemon crash, `baton daemon start --resume` picks up from the
   last persisted state and re-dispatches any steps that were in-flight.

8. A plan with a `review` gate pauses execution, writes a decision
   request file, and resumes when resolved via `baton decide --resolve`.

9. All 1901 existing tests continue to pass with zero modifications.

---

## Execution Instructions for Claude Code

Read this plan, then the proposal at:
`git show origin/claude/review-delivery-platform-T1JcT:proposals/001-async-execution-runtime.md`

Follow the build order in Part 3. For each step:

1. Read the files listed in the "Files" column
2. Read `.claude/team-context/context.md` for conventions
3. Implement following project patterns:
   - `from __future__ import annotations` at top of all files
   - `@dataclass` with `to_dict()`/`from_dict()` for models
   - `Path` parameters with defaults for directories
   - pytest with `tmp_path`, class-based grouping
4. Run `python3 -m pytest tests/ -x -q` after each step
5. Update exports as needed

**Branch**: `feat/001-claude-launcher`
**Commit convention**: `001: <step description>`
