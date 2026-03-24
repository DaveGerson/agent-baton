# Proposal 004: Parallel Execution Engine with Cross-Project Coordination

**Status**: Draft
**Author**: Architecture Review
**Date**: 2026-03-24
**Risk**: MEDIUM — extends existing runtime/engine modules; critical wiring fix + new components
**Estimated Scope**: ~2,400 LOC new, ~400 LOC modified across 15-20 files
**Depends On**: Proposal 001 (Async Runtime) — hard dependency for daemon mode; Proposal 003 (Learning Loop) — soft dependency for cross-execution learning

---

## Problem Statement

Agent Baton runs one execution at a time per project. The engine writes
to a single `execution-state.json`, the daemon manages a single PID
file, and all observability logs are global singletons. This means:

1. **Context-switching kills state.** Starting a new plan overwrites the
   active execution. If you need to interrupt feature A to fix bug B,
   you lose A's engine state.

2. **No background execution.** You cannot run a long implementation in
   daemon mode while interactively planning the next task. The engine
   is locked to one execution.

3. **The improvement loop cannot run alongside active work.** Proposal
   003's `ImprovementLoop.run_cycle()` needs to execute post-task, but
   it cannot run if another execution already owns the engine.

4. **Cross-project work is manual.** Monorepo changes spanning packages,
   or microservice changes spanning repos, require manually orchestrating
   each project with no coordination, dependency tracking, or shared
   learning.

5. **A critical wiring bug prevents even basic multi-execution.**
   `StatePersistence` already supports namespaced directories
   (`executions/<task-id>/`), the CLI already passes `task_id`, but
   `ExecutionEngine.__init__` does not accept `task_id` — so all
   executions silently write to the same flat file.

---

## Current State Assessment

### What Already Exists (70% of Stage 1)

| Component | Status | Detail |
|-----------|--------|--------|
| `StatePersistence` namespacing | Built, unused | Supports `executions/<task-id>/` directories, `list_executions()`, `load_all()`, `set_active()`, `get_active_task_id()` |
| CLI `--task-id` flag | Built, broken | `execute.py` passes `task_id` to `ExecutionEngine`, but engine ignores it |
| `active-task-id.txt` | Planned in code, not created | `StatePersistence` has the path but nothing writes it |
| PMO Scanner | Built, functional | Already reads multi-project, multi-execution state via `StatePersistence.load_all()` |
| PMO Store | Built, functional | Global config at `~/.baton/pmo-config.json` with project registry |
| Per-execution event persistence | Built, functional | `events/<task-id>.jsonl` — already namespaced |
| Per-execution traces | Built, functional | `traces/<task-id>.json` — already namespaced |
| Per-execution retrospectives | Built, functional | `retrospectives/<task-id>.md` — already namespaced |
| Within-plan parallel dispatch | Built, functional | `engine.next_actions()` returns all dispatchable steps; `StepScheduler` with `asyncio.Semaphore` |
| Atomic state writes | Built, functional | `StatePersistence.save()` uses tmp + rename (POSIX atomic) |

### What Does NOT Exist

| Gap | Impact |
|-----|--------|
| `ExecutionEngine` task_id wiring | CRITICAL — all executions overwrite each other |
| Multiple concurrent daemons | Cannot run executions in background |
| Per-execution usage/telemetry logs | Shared singletons cause interleaved writes |
| File-level locking | No concurrency protection beyond atomic writes |
| Git worktree isolation | Parallel agents modify the same working tree |
| Cross-execution resource governance | No rate limit coordination |
| Cross-project execution registry | No global view of all running executions |
| Cross-project dependency model | Cannot express "task B waits for task A in another repo" |

---

## Proposed Architecture

### Design Philosophy

**Process-per-execution with lightweight coordination.** Each parallel
execution gets its own worker process, PID file, log, and namespaced
state directory. Coordination is file-based with a thin SQLite layer
for cross-project queries. No inter-process communication required —
the file system is the coordination substrate.

### Three-Stage Delivery

Each stage is independently useful and shippable.

---

### Stage 1: Within-Project Multi-Execution

**Goal:** Multiple plans running concurrently in the same project.

#### 1.1 Fix ExecutionEngine Task ID Wiring

```python
# agent_baton/core/engine/executor.py

class ExecutionEngine:
    def __init__(
        self,
        team_context_root: Path | None = None,
        bus: EventBus | None = None,
        task_id: str | None = None,        # NEW
    ) -> None:
        self._root = team_context_root or _default_context_root()
        self._bus = bus or EventBus()
        self._task_id = task_id
        self._persistence = StatePersistence(
            self._root,
            task_id=task_id,               # NOW WIRED
        )
```

This single change activates the entire namespaced persistence layer
that is already built.

#### 1.2 Namespace Daemon PID Files

```python
# agent_baton/core/runtime/supervisor.py — extend WorkerSupervisor

class WorkerSupervisor:
    def __init__(
        self,
        team_context_root: Path | None = None,
        task_id: str | None = None,        # NEW
    ) -> None:
        ...
        if task_id:
            exec_dir = self._root / "executions" / task_id
            self._pid_path = exec_dir / "worker.pid"
            self._log_path = exec_dir / "worker.log"
            self._status_path = exec_dir / "worker-status.json"
        else:
            # Legacy single-daemon paths
            self._pid_path = self._root / "daemon.pid"
            self._log_path = self._root / "daemon.log"
            self._status_path = self._root / "daemon-status.json"
```

Multiple daemons can now coexist — each locks its own PID file via
`fcntl.flock()` (existing pattern).

#### 1.3 Per-Execution Observability Files

```python
# Namespace usage and telemetry logs per execution

# agent_baton/core/observe/usage.py
class UsageLogger:
    def __init__(self, context_root: Path, task_id: str | None = None):
        if task_id:
            self._log_path = context_root / "executions" / task_id / "usage-log.jsonl"
        else:
            self._log_path = context_root / "usage-log.jsonl"  # legacy

# agent_baton/core/observe/telemetry.py — same pattern
```

#### 1.4 File-Level Locking

```python
# agent_baton/core/engine/persistence.py — add advisory locking

class StatePersistence:
    def lock(self) -> None:
        """Acquire exclusive advisory lock on execution state."""
        lock_path = self._state_path.with_suffix(".lock")
        self._lock_fd = open(lock_path, "w")
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(self) -> None:
        """Release advisory lock."""
        if self._lock_fd:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
            self._lock_fd = None
```

#### 1.5 CLI Extensions

```bash
# New commands
baton execute list                    # Show all executions in this project
baton execute switch <task-id>        # Set active execution
baton daemon list                     # Show all running daemons

# Enhanced existing commands
baton execute start                   # Now writes to namespaced directory
baton execute next --task-id <id>     # Target specific execution
baton daemon start --task-id <id>     # Start daemon for specific execution
baton execute resume --task-id <id>   # Resume specific execution
```

#### 1.6 Active Execution Tracking

```python
# agent_baton/core/engine/persistence.py — activate existing planned code

class StatePersistence:
    def set_active(self, task_id: str) -> None:
        """Write active-task-id.txt marker."""
        marker = self._context_root / "active-task-id.txt"
        marker.write_text(task_id)

    def get_active_task_id(self) -> str | None:
        """Read active-task-id.txt marker."""
        marker = self._context_root / "active-task-id.txt"
        if marker.exists():
            return marker.read_text().strip()
        return None
```

When no `--task-id` is specified, CLI commands target the active
execution. `baton execute start` automatically sets the new execution
as active.

#### Storage Layout (Stage 1)

```
.claude/team-context/
  active-task-id.txt                    # points to current execution
  execution-state.json                  # legacy (preserved for compat)
  executions/
    <task-id-A>/
      execution-state.json              # namespaced state
      execution-state.lock              # advisory lock
      worker.pid                        # daemon PID (if running)
      worker.log                        # daemon log
      worker-status.json                # daemon status snapshot
      plan.json                         # copy of the plan
      usage-log.jsonl                   # per-execution usage
      telemetry.jsonl                   # per-execution telemetry
    <task-id-B>/
      ...
  events/                               # existing — already per-task
    <task-id>.jsonl
  traces/                               # existing — already per-task
    <task-id>.json
  retrospectives/                       # existing — already per-task
    <task-id>.md
```

---

### Stage 2: Isolation & Governance

**Goal:** Safe parallel file modifications and resource management.

#### 2.1 Git Worktree Manager

```python
# agent_baton/core/runtime/worktree.py

class WorktreeManager:
    """Manage git worktrees for parallel execution isolation.

    Each execution gets a worktree at:
      .baton-worktrees/<task-id>/

    Created from a new branch:
      baton/<task-id>

    On completion, merged back via user-chosen strategy.
    """

    WORKTREE_ROOT = ".baton-worktrees"

    def __init__(self, repo_root: Path | None = None) -> None: ...

    def create_worktree(
        self, task_id: str, base_branch: str = "HEAD"
    ) -> Path:
        """Create a git worktree for this execution.

        Returns the worktree path. ClaudeCodeLauncher.working_directory
        is set to this path so all subprocesses operate in isolation.
        """
        branch = f"baton/{task_id}"
        worktree_path = self._repo_root / self.WORKTREE_ROOT / task_id
        # git worktree add -b <branch> <path> <base>
        ...
        return worktree_path

    def merge_worktree(
        self, task_id: str, target_branch: str = "main",
        strategy: str = "merge"  # "merge", "rebase", "squash"
    ) -> str:
        """Merge the execution's branch back. Returns merge commit hash."""
        ...

    def cleanup_worktree(self, task_id: str) -> None:
        """Remove the worktree and optionally delete the branch."""
        ...

    def list_worktrees(self) -> list[dict]: ...
```

**CLI:**
```bash
baton execute start --worktree          # create worktree for this execution
baton worktree list                     # show all worktrees
baton worktree merge <task-id>          # merge back
baton worktree cleanup <task-id>        # remove worktree
```

#### 2.2 Execution Registry (SQLite)

```python
# agent_baton/core/engine/registry.py

@dataclass
class ExecutionRecord:
    """Lightweight record in the central registry."""
    execution_id: str
    project_path: str
    status: str              # "running", "complete", "failed", "paused"
    plan_summary: str
    worker_pid: int | None
    started_at: str
    updated_at: str
    risk_level: str
    budget_tier: str
    steps_total: int
    steps_complete: int
    git_branch: str
    tokens_estimated: int

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord": ...


class ExecutionRegistry:
    """Cross-project execution index backed by SQLite.

    - WAL mode for concurrent reads during writes
    - Lazily created on first use
    - Lightweight: metadata only, full state stays in JSON files
    """

    _DEFAULT_PATH = Path("~/.baton/registry.db").expanduser()

    def __init__(self, db_path: Path | None = None) -> None: ...

    def register(self, record: ExecutionRecord) -> None: ...
    def update_status(self, execution_id: str, project_path: str,
                       status: str, **kwargs) -> None: ...
    def deregister(self, execution_id: str, project_path: str) -> None: ...

    # Queries
    def list_running(self) -> list[ExecutionRecord]: ...
    def list_by_project(self, project_path: str) -> list[ExecutionRecord]: ...
    def list_all(self, status: str | None = None) -> list[ExecutionRecord]: ...
    def get(self, execution_id: str, project_path: str) -> ExecutionRecord | None: ...

    # Resource queries
    def total_estimated_tokens(self) -> int: ...
    def running_count(self) -> int: ...
```

**Schema:**
```sql
CREATE TABLE executions (
    execution_id TEXT NOT NULL,
    project_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    plan_summary TEXT DEFAULT '',
    worker_pid INTEGER,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    risk_level TEXT DEFAULT 'LOW',
    budget_tier TEXT DEFAULT 'standard',
    steps_total INTEGER DEFAULT 0,
    steps_complete INTEGER DEFAULT 0,
    git_branch TEXT DEFAULT '',
    tokens_estimated INTEGER DEFAULT 0,
    PRIMARY KEY (execution_id, project_path)
);

CREATE INDEX idx_status ON executions(status);
CREATE INDEX idx_project ON executions(project_path);
```

**Integration:** `ExecutionEngine.start()` and `complete()` call
`registry.register()` and `registry.update_status()` via EventBus
subscribers.

**CLI:**
```bash
baton execute list --all                # all executions, all projects
baton resources                         # current resource usage
```

#### 2.3 Resource Governor

```python
# agent_baton/core/runtime/governor.py

@dataclass
class ResourceLimits:
    max_concurrent_executions: int = 3
    max_concurrent_agents: int = 8
    max_tokens_per_minute: int = 0       # 0 = unlimited
    max_concurrent_per_project: int = 2

class ResourceGovernor:
    """Enforce resource limits across parallel executions.

    Reads from the central registry to determine current load.
    Called before starting a new execution or dispatching an agent.
    """

    def __init__(
        self,
        registry: ExecutionRegistry,
        limits: ResourceLimits | None = None,
    ) -> None: ...

    def can_start_execution(self, project_path: str) -> tuple[bool, str]: ...
    def can_dispatch_agent(self) -> tuple[bool, str]: ...
    def wait_for_capacity(self, timeout: float = 300.0) -> bool: ...
```

**Integration with StepScheduler:**
```python
async def dispatch(self, ...):
    if self._governor and not self._governor.can_dispatch_agent()[0]:
        await self._governor.wait_for_capacity()
    async with self._semaphore:
        # existing dispatch logic
```

---

### Stage 3: Cross-Project Coordination

**Goal:** A single orchestrator managing work across multiple repos.

#### 3.1 Cross-Project Coordinator

```python
# agent_baton/core/runtime/cross_project.py

@dataclass
class SubPlanRef:
    project_path: Path
    task_id: str
    plan: MachinePlan
    worktree_path: Path | None = None

@dataclass
class CrossProjectPlan:
    coordinator_id: str
    sub_plans: list[SubPlanRef]
    dependencies: dict[str, list[str]]   # task_id -> [depends_on_task_ids]
    shared_context: str

class CrossProjectCoordinator:
    """Orchestrate work across multiple projects.

    NOT a daemon. Driven by a Claude Code session that calls CLI
    commands to start and monitor sub-executions.

    1. Decompose cross-project task into per-project sub-plans
    2. Start each as a parallel execution in its project
    3. Monitor via the central registry
    4. Enforce cross-project dependencies
    5. Aggregate results and traces
    """

    def __init__(
        self,
        registry: ExecutionRegistry,
        governor: ResourceGovernor,
    ) -> None: ...

    def start(self, plan: CrossProjectPlan) -> dict[str, str]: ...
    def check_dependencies(self, task_id: str) -> bool: ...
    def aggregate_status(self, coordinator_id: str) -> dict: ...
    def aggregate_traces(self, coordinator_id: str) -> list: ...
```

**CLI:**
```bash
baton cross plan "description" --projects /path/a /path/b
baton cross start --plan cross-plan.json
baton cross status <coordinator-id>
```

---

## Data Model Changes

### New: `agent_baton/models/parallel.py`

```python
@dataclass
class ExecutionRecord:
    """Registry record for a running/completed execution."""
    execution_id: str
    project_path: str
    status: str
    plan_summary: str
    worker_pid: int | None
    started_at: str
    updated_at: str
    risk_level: str
    budget_tier: str
    steps_total: int
    steps_complete: int
    git_branch: str
    tokens_estimated: int

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionRecord": ...

@dataclass
class ResourceLimits:
    max_concurrent_executions: int = 3
    max_concurrent_agents: int = 8
    max_tokens_per_minute: int = 0
    max_concurrent_per_project: int = 2

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "ResourceLimits": ...

@dataclass
class CrossProjectPlan:
    coordinator_id: str
    sub_plans: list[SubPlanRef]
    dependencies: dict[str, list[str]]
    shared_context: str

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "CrossProjectPlan": ...
```

---

## Guardrails & Safety

### Concurrency Control

| Resource | Isolation Mechanism |
|----------|-------------------|
| Execution state (JSON) | Per-execution directory + flock on .lock file |
| Working tree (source files) | Git worktrees (Stage 2) or manual branch management |
| Event logs (JSONL) | Per-execution directory (already append-only) |
| Central registry (SQLite) | WAL mode + SQLite internal locking |
| Claude API rate limits | Per-launcher exponential backoff + ResourceGovernor cap |
| PID files | fcntl.flock exclusive lock (existing pattern) |

### Safety Rules

1. **Backward compatible.** `baton execute` without `--task-id` works
   exactly as today (targets active execution, falls back to legacy).
2. **No auto-merge.** Worktree merges require explicit user action.
   Merge conflicts are reported, not auto-resolved.
3. **Resource caps are enforced.** Default max 3 concurrent executions,
   8 concurrent agents. Cannot be exceeded without explicit config change.
4. **Registry is advisory.** If SQLite is corrupted, the system falls
   back to scanning project directories. No hard dependency.
5. **Crash recovery.** `baton execute recover` scans PID files, detects
   dead workers, marks them as paused. User decides whether to resume
   or discard.
6. **Worktree cleanup.** `baton worktree cleanup --stale` removes
   worktrees whose executions completed more than 7 days ago.

---

## Failure Mode Analysis

| Failure | Probability | Impact | Mitigation |
|---------|------------|--------|------------|
| Two workers write same state file | HIGH if naive | Data corruption | Per-execution `flock()` on lock file. Each worker holds exclusive lock. |
| Worker crashes mid-execution | MEDIUM | Orphaned "running" state | `recover_stale()` scans PIDs with `os.kill(pid, 0)`. Dead workers marked "paused". |
| Two executions modify same source file | HIGH without isolation | Merge conflicts | Git worktrees enforce filesystem isolation. Conflicts caught at merge time. |
| API rate limit collapse | HIGH with 3+ active | All executions throttled | ResourceGovernor caps total concurrent agents. Per-launcher backoff already exists. |
| SQLite registry stale | LOW | Dashboard shows wrong status | Heartbeat every 60s. Verify PIDs alive on status query. |
| Worktree branch conflicts at merge | MEDIUM | Manual resolution needed | Report conflict to user. Do NOT auto-resolve. |
| Disk space from worktrees | LOW-MEDIUM | New worktrees fail | Auto-cleanup on completion. `baton worktree cleanup --stale` for manual cleanup. |
| Cross-project dependency deadlock | LOW | Both tasks stuck | Cycle detection at plan time. Stall detection at runtime (no progress for 10 min). |

---

## Integration Points

### With Proposal 001 (Async Runtime)
- `TaskWorker` and `WorkerSupervisor` are the daemon foundation.
  Stage 1 extends them with task_id namespacing.
- `StepScheduler` semaphore is the within-execution concurrency control.
  Stage 2 adds cross-execution governance.

### With Proposal 003 (Learning Loop)
- `ImprovementLoop.run_cycle()` can run as a background execution
  alongside active development work (enabled by Stage 1).
- Cross-execution learning: experiments in one execution feed data to
  the recommender running in another (enabled by shared usage logs).

### With PMO UI
- PMO Scanner already reads multi-execution state — Stage 1 activates
  the data it already knows how to display.
- Stage 2 registry enables the PMO dashboard to show cross-project
  execution status.
- PMO Kanban cards map to individual executions with pause/cancel actions.

---

## CLI Command Summary

### Stage 1 — New Commands

```bash
baton execute list                       # all executions in this project
baton execute switch <task-id>           # set active execution
baton daemon list                        # all running daemons
```

### Stage 1 — Enhanced Commands

```bash
baton execute start [--task-id <id>]     # start with explicit task-id
baton execute next [--task-id <id>]      # target specific execution
baton execute resume [--task-id <id>]    # resume specific execution
baton daemon start [--task-id <id>]      # daemon for specific execution
```

### Stage 2 — New Commands

```bash
baton execute list --all                 # all executions, all projects
baton execute start --worktree           # create worktree for this execution
baton execute recover                    # find and recover stale executions
baton worktree list                      # show all worktrees
baton worktree merge <task-id>           # merge worktree back
baton worktree cleanup [--stale]         # remove worktrees
baton resources                          # current resource usage
baton resources --limits                 # show/set resource limits
```

### Stage 3 — New Commands

```bash
baton cross plan "desc" --projects ...   # plan across projects
baton cross start --plan <path>          # start cross-project execution
baton cross status <coordinator-id>      # monitor cross-project progress
```

---

## Migration Strategy

### Stage 1: Within-Project Multi-Execution (Focus of this proposal)

1. Fix `ExecutionEngine.__init__` to accept and propagate `task_id`
2. Namespace daemon PID/log files per execution
3. Namespace usage and telemetry logs per execution
4. Add advisory file locking to `StatePersistence`
5. Activate `active-task-id.txt` tracking
6. Add `baton execute list`, `switch`, `baton daemon list` commands
7. Update `ExecutionContext.build()` to pass task_id through
8. Tests: 60+ unit tests, 10+ integration tests
9. Gate: All existing tests pass (backward compatibility)

### Stage 2: Isolation & Governance

1. Implement `WorktreeManager`
2. Implement `ExecutionRegistry` (SQLite)
3. Implement `ResourceGovernor`
4. Wire registry into engine start/complete via EventBus
5. Wire governor into StepScheduler
6. Add CLI commands for worktrees, resources, cross-project listing
7. Tests: 50+ unit tests
8. Gate: All tests pass, multi-execution integration test

### Stage 3: Cross-Project Coordination (Defer until Stages 1+2 validated)

1. Implement `CrossProjectCoordinator`
2. Add `baton cross` CLI commands
3. Wire cross-project dependency checking
4. Tests: 30+ unit tests
5. Gate: Cross-project integration test with 2 test repos

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Increased complexity for single-execution users | Fully backward compatible. No behavioral changes unless `--task-id` or `--worktree` flags are used. |
| File system contention between workers | Per-execution flock + namespaced directories. Shared JSONL files use append + flock. |
| Git worktree merge conflicts | Worktrees isolate during execution. Conflicts caught at merge time and reported to user. |
| SQLite as new dependency | It is stdlib Python (`sqlite3`). No external packages required. Created lazily on first use. |
| Over-engineering Stage 3 | Strict stage gating. Stage 3 does not begin until Stage 1+2 produce real usage data. |
| Token cost from parallel executions | ResourceGovernor enforces caps. Each execution still dispatches the same agents — parallelism does not increase per-task cost. |

---

## Success Criteria

### Stage 1

1. Two executions run concurrently without data corruption (state files
   are isolated, no overwrites).
2. `baton execute switch` changes active execution in < 1 second.
3. Background daemon completes a standard plan without user intervention
   while another execution runs interactively.
4. Usage logs correctly attribute to the right execution.
5. All existing tests pass with zero behavioral changes for users who
   do not use `--task-id`.
6. `baton execute list` shows all executions with correct status.

### Stage 2

7. Two executions with worktrees modify overlapping files without
   conflict during execution; conflicts detected at merge time.
8. ResourceGovernor blocks a 4th concurrent execution when limit is 3.
9. `baton execute list --all` shows executions across all registered
   projects.
10. Stale worktree cleanup removes completed worktrees older than 7 days.

### Stage 3

11. A cross-project plan starts sub-executions in 2 different project
    directories.
12. Cross-project dependency causes task B to wait until task A completes.
13. `baton cross status` shows aggregated progress across all sub-plans.
