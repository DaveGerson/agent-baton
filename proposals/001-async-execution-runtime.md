# Proposal 001: Async Execution Runtime & Event-Driven Architecture

**Status**: Draft
**Author**: Architecture Review
**Date**: 2026-03-21
**Risk**: HIGH — touches core/engine, core/distribute, models, CLI
**Estimated Scope**: ~2,500 LOC new, ~400 LOC modified across 15-20 files

---

## Problem Statement

Agent Baton's execution engine is **synchronous and CLI-driven**. The
orchestrator calls `engine.next_action()`, spawns one agent, waits for
completion, calls `engine.record_step_result()`, and repeats. This works
for interactive sessions where a human is watching, but breaks down for
the primary use case: **autonomous agent development with asynchronous
human interaction**.

Current limitations:

1. **No background execution** — AsyncDispatcher writes task JSON files
   but has no worker daemon; the caller must poll and spawn subprocesses
   manually.
2. **No event propagation** — state changes (step complete, gate failed,
   escalation triggered) are written to files but never broadcast.
   Nothing can subscribe to "agent X finished" or "human approval needed."
3. **No human decision points** — the Escalation model exists but there
   is no delivery mechanism. When the engine needs a human decision, it
   has nowhere to send the request and no way to block-then-resume.
4. **Single-threaded state machine** — `ExecutionEngine.next_action()`
   returns one action at a time. Parallel steps are modeled in MachinePlan
   but dispatched serially by the caller.
5. **No session continuity** — crash recovery reloads from disk, but
   there is no concept of a persistent daemon that outlives a terminal
   session.

Without an async runtime, autonomous agents cannot run overnight, pause
for human review at gates, or execute parallel workstreams.

---

## Proposed Architecture

### Layer 1: Event Bus (In-Process + File-Backed)

```
agent_baton/core/events/
├── __init__.py
├── bus.py            # EventBus: publish/subscribe with topic routing
├── events.py         # Event dataclasses (StepCompleted, GateRequired, ...)
├── persistence.py    # Append-only event log (.jsonl) for recovery
└── projections.py    # Materialized views from event stream
```

**EventBus** is the central nervous system:

```python
@dataclass
class Event:
    event_id: str           # uuid4
    timestamp: str          # ISO 8601
    topic: str              # e.g. "step.completed", "gate.required", "human.decision_needed"
    task_id: str
    payload: dict           # event-specific data

class EventBus:
    def publish(self, event: Event) -> None: ...
    def subscribe(self, topic_pattern: str, handler: Callable) -> str: ...
    def unsubscribe(self, subscription_id: str) -> None: ...
    def replay(self, task_id: str, from_seq: int = 0) -> list[Event]: ...
```

**Design decisions:**

- **In-process first**: Subscribers are Python callables. No external
  message broker required (Redis, RabbitMQ, etc.). This matches the
  file-based philosophy and keeps deployment simple.
- **File-backed**: Every event appends to `.claude/team-context/events/<task_id>.jsonl`.
  On recovery, replay the event log to rebuild state — event sourcing lite.
- **Topic routing**: Glob-style patterns (`step.*`, `human.*`, `gate.required`).
  Keeps coupling low; new consumers don't require bus changes.

### Layer 2: Async Worker Runtime

```
agent_baton/core/runtime/
├── __init__.py
├── worker.py         # TaskWorker: asyncio event loop driving execution
├── scheduler.py      # StepScheduler: parallel dispatch with concurrency limits
├── supervisor.py     # WorkerSupervisor: lifecycle, health checks, restart
└── signals.py        # POSIX signal handling for graceful shutdown
```

**TaskWorker** replaces the synchronous next_action() loop:

```python
class TaskWorker:
    """Drives a single task's execution asynchronously."""

    def __init__(self, engine: ExecutionEngine, bus: EventBus,
                 agent_launcher: AgentLauncher, max_parallel: int = 3):
        ...

    async def run(self) -> None:
        """Main loop: pull next actions, dispatch agents, record results."""
        while True:
            actions = self.engine.next_actions()  # plural — parallel support
            if not actions:
                break

            if any(a.action_type == ActionType.WAIT for a in actions):
                await self._wait_for_human(actions)
                continue

            tasks = [self._dispatch(a) for a in actions
                     if a.action_type == ActionType.DISPATCH]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for action, result in zip(actions, results):
                self.engine.record_step_result(...)
                self.bus.publish(StepCompleted(...))

    async def _wait_for_human(self, actions: list[ExecutionAction]) -> None:
        """Publish decision request, then await resolution event."""
        for action in actions:
            self.bus.publish(HumanDecisionNeeded(
                task_id=self.engine.state.task_id,
                decision_type=action.metadata.get("decision_type"),
                context=action.metadata.get("context"),
                options=action.metadata.get("options"),
            ))
        # Block until human.decision_resolved event for this task
        await self._wait_for_event("human.decision_resolved")
```

**StepScheduler** manages concurrency:

```python
class StepScheduler:
    """Dispatch parallel steps with bounded concurrency."""

    def __init__(self, max_concurrent: int = 3, rate_limit: RateLimit = None):
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def dispatch(self, step: PlanStep, prompt: str,
                       launcher: AgentLauncher) -> StepResult:
        async with self._semaphore:
            return await launcher.launch(step.agent, step.model, prompt)
```

**Key design decisions:**

- **asyncio, not threads**: Matches Claude Code's Node.js-style
  concurrency model. Agents are I/O-bound (waiting for LLM responses),
  not CPU-bound.
- **AgentLauncher protocol**: Abstract interface so the worker doesn't
  care whether agents run as Claude Code subagents, subprocess calls,
  or API requests. Testable via mock launcher.
- **Concurrency limit**: Default 3 parallel agents. Configurable to
  respect rate limits (cost-budget.md warns about 5+ parallel Opus).
- **Graceful shutdown**: SIGTERM/SIGINT handlers drain in-flight agents
  before persisting state.

### Layer 3: Human Decision Protocol

```
agent_baton/core/runtime/
├── decisions.py      # DecisionManager: request, resolve, timeout
└── notifiers/
    ├── __init__.py
    ├── file.py       # Write decision request to .md file (default)
    ├── webhook.py    # POST to configured URL
    └── stdout.py     # Print to terminal (interactive fallback)
```

**DecisionManager** bridges async execution with human review:

```python
@dataclass
class DecisionRequest:
    request_id: str
    task_id: str
    decision_type: str       # "gate_approval", "escalation", "plan_review"
    summary: str             # human-readable context
    options: list[str]       # e.g. ["approve", "reject", "modify"]
    deadline: str | None     # ISO 8601 timeout (optional)
    context_files: list[str] # paths to relevant artifacts

@dataclass
class DecisionResolution:
    request_id: str
    chosen_option: str
    rationale: str | None
    resolved_by: str         # "human", "timeout_default", "auto_policy"
    resolved_at: str

class DecisionManager:
    def request(self, req: DecisionRequest) -> None:
        """Persist request + notify via configured notifiers."""
        ...

    def resolve(self, request_id: str, option: str,
                rationale: str = None) -> None:
        """Record resolution + publish event to unblock worker."""
        ...

    def pending(self) -> list[DecisionRequest]:
        """List all unresolved decision requests."""
        ...
```

**Notifier chain** (configurable in settings.json):

```json
{
  "baton": {
    "notifiers": [
      {"type": "file", "path": ".claude/team-context/decisions/"},
      {"type": "webhook", "url": "https://hooks.example.com/baton"}
    ],
    "decision_timeout_minutes": 480,
    "timeout_action": "block"
  }
}
```

**Human resolves decisions via CLI:**

```bash
baton decide --list                          # show pending decisions
baton decide --resolve REQ_ID --option approve --rationale "LGTM"
```

### Layer 4: Daemon Mode

```bash
# Start background execution
baton daemon start --plan plan.json --detach
# → writes PID to .claude/team-context/daemon.pid
# → logs to .claude/team-context/daemon.log

# Check status
baton daemon status
# → "Running task abc123, phase 2/3, 4 steps complete, 1 pending decision"

# Stop gracefully
baton daemon stop
# → SIGTERM → drain in-flight → persist state → exit
```

**WorkerSupervisor** manages the daemon lifecycle:

```python
class WorkerSupervisor:
    def start(self, plan: MachinePlan, detach: bool = False) -> None:
        """Start TaskWorker, optionally daemonize."""
        ...

    def status(self) -> dict:
        """Read execution state + event log for live status."""
        ...

    def stop(self, timeout: int = 30) -> None:
        """Graceful shutdown with timeout."""
        ...
```

---

## Data Model Changes

### New model: `agent_baton/models/events.py`

```python
@dataclass
class Event:
    event_id: str
    timestamp: str
    topic: str
    task_id: str
    sequence: int          # monotonic within task
    payload: dict

    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, data: dict) -> "Event": ...
```

### New model: `agent_baton/models/decision.py`

```python
@dataclass
class DecisionRequest:
    request_id: str
    task_id: str
    decision_type: str
    summary: str
    options: list[str]
    deadline: str | None
    context_files: list[str]
    created_at: str
    status: str             # "pending", "resolved", "expired"

@dataclass
class DecisionResolution:
    request_id: str
    chosen_option: str
    rationale: str | None
    resolved_by: str
    resolved_at: str
```

### ExecutionEngine extension: `next_actions()` (plural)

```python
# executor.py — add method
def next_actions(self) -> list[ExecutionAction]:
    """Return all dispatchable actions for current state.

    If current phase has parallel steps that are all ready,
    return all of them. If a gate or human decision is needed,
    return a single WAIT action.
    """
```

This is a non-breaking addition. The existing `next_action()` (singular)
remains and returns `next_actions()[0]` for backward compatibility.

---

## Integration Points

### With Existing Execution Engine

The async runtime **wraps** the existing `ExecutionEngine` — it does not
replace it. The engine remains the source of truth for plan state.

```
┌──────────────────────────────────────┐
│          WorkerSupervisor            │
│  ┌────────────────────────────────┐  │
│  │         TaskWorker             │  │
│  │  ┌──────────┐  ┌───────────┐  │  │
│  │  │ Scheduler │  │ EventBus  │  │  │
│  │  └─────┬────┘  └─────┬─────┘  │  │
│  │        │              │        │  │
│  │  ┌─────▼──────────────▼─────┐  │  │
│  │  │   ExecutionEngine        │  │  │
│  │  │   (existing, unchanged)  │  │  │
│  │  └──────────────────────────┘  │  │
│  └────────────────────────────────┘  │
│                                      │
│  ┌────────────────────────────────┐  │
│  │     DecisionManager           │  │
│  │  ┌──────┐ ┌────────┐ ┌─────┐ │  │
│  │  │ File │ │Webhook │ │Stdout│ │  │
│  │  └──────┘ └────────┘ └─────┘ │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
```

### With Existing Observability

EventBus subscribers automatically feed:
- **TraceRecorder**: `step.*` events → trace events
- **UsageLogger**: `step.completed` events → usage records
- **Telemetry**: all events → telemetry log
- **Dashboard**: aggregated from usage + trace (unchanged)

### With Existing CLI

New commands added alongside existing ones:

| Command | Purpose |
|---------|---------|
| `baton daemon start` | Start background execution |
| `baton daemon status` | Show live execution status |
| `baton daemon stop` | Graceful shutdown |
| `baton decide --list` | List pending human decisions |
| `baton decide --resolve` | Resolve a decision |
| `baton events --task` | Query event log for a task |

Existing `baton execute *` commands continue to work for synchronous
interactive use.

---

## Migration Strategy

### Phase 1: Event Bus (Week 1-2)
1. Implement `core/events/` module (bus, events, persistence)
2. Wire ExecutionEngine to publish events on state changes
3. Wire TraceRecorder and UsageLogger as subscribers
4. Tests: 80+ unit tests for bus, persistence, replay

### Phase 2: Async Worker (Week 3-4)
1. Implement `core/runtime/` module (worker, scheduler)
2. Implement `AgentLauncher` protocol + Claude Code adapter
3. Add `next_actions()` to ExecutionEngine
4. Tests: 60+ tests for worker loop, parallel dispatch, error handling

### Phase 3: Human Decisions (Week 5-6)
1. Implement DecisionManager + file notifier
2. Add `baton decide` CLI commands
3. Wire ExecutionEngine WAIT actions to DecisionManager
4. Tests: 40+ tests for decision lifecycle, timeout, resolution

### Phase 4: Daemon Mode (Week 7-8)
1. Implement WorkerSupervisor with daemonize support
2. Add `baton daemon` CLI commands
3. Signal handling, PID management, log rotation
4. Integration tests: full plan execution in daemon mode
5. Webhook notifier (optional, for teams with external tooling)

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Event bus adds complexity to a simple system | In-process only; no external broker. File-backed for recovery. Can be disabled via config. |
| Daemon mode is hard to debug | Structured logging to daemon.log. `baton daemon status` for live inspection. Fallback to synchronous mode always available. |
| Parallel agent dispatch hits rate limits | StepScheduler with configurable concurrency limit. Default conservative (3). |
| Human decision timeout blocks indefinitely | Configurable timeout with default action (block or auto-approve-low-risk). |
| Breaking changes to ExecutionEngine | Additive only. `next_actions()` is new; `next_action()` unchanged. EventBus is opt-in subscriber pattern. |

---

## Success Criteria

1. A MachinePlan with 3 phases, 8 steps, 2 gates executes to completion
   in daemon mode without human presence (for LOW-risk, no escalations).
2. A MEDIUM-risk plan pauses at the auditor gate, writes a decision
   request, and resumes when resolved via `baton decide`.
3. Parallel steps within a phase execute concurrently (observed via
   trace timestamps showing overlap).
4. Crash recovery: kill daemon mid-execution, restart, execution resumes
   from last persisted state.
5. Event log replay produces identical execution state as live execution.
