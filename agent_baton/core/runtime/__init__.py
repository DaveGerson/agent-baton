"""Runtime sub-package -- async execution layer wrapping the synchronous engine.

The runtime provides the async infrastructure that drives the engine for
headless/daemon execution:

- ``TaskWorker`` -- async event loop that calls ``engine.next_actions()``
  and dispatches agents concurrently via ``StepScheduler``.
- ``StepScheduler`` -- bounded-concurrency dispatcher using ``asyncio.Semaphore``
  to cap the number of simultaneously running agent launches.
- ``AgentLauncher`` / ``DryRunLauncher`` -- protocol and test mock for
  launching agents.  ``ClaudeCodeLauncher`` is the production implementation
  that invokes the ``claude`` CLI as an async subprocess.
- ``WorkerSupervisor`` -- lifecycle management (PID file, logging, signal
  handling) for daemon-mode execution.
- ``ExecutionContext`` -- factory that correctly wires EventBus,
  ExecutionEngine, and EventPersistence to the same bus instance.
- ``DecisionManager`` -- persists human decision requests to disk and
  publishes events to unblock waiting workers.
- ``SignalHandler`` -- POSIX signal handling for graceful daemon shutdown.
"""
from __future__ import annotations

from agent_baton.core.runtime.launcher import AgentLauncher, DryRunLauncher, LaunchResult
from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher, ClaudeCodeConfig
from agent_baton.core.runtime.context import ExecutionContext
from agent_baton.core.runtime.scheduler import StepScheduler, SchedulerConfig
from agent_baton.core.runtime.signals import SignalHandler
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.core.runtime.supervisor import WorkerSupervisor

__all__ = [
    "AgentLauncher",
    "DryRunLauncher",
    "LaunchResult",
    "ClaudeCodeLauncher",
    "ClaudeCodeConfig",
    "ExecutionContext",
    "StepScheduler",
    "SchedulerConfig",
    "SignalHandler",
    "TaskWorker",
    "DecisionManager",
    "WorkerSupervisor",
]
