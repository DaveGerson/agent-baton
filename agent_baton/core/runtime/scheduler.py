"""StepScheduler — bounded-concurrency dispatcher for parallel plan steps.

Uses asyncio.Semaphore to cap the number of simultaneously running agent
launches.  The caller passes in a list of step dicts; all steps are started
concurrently, but at most *max_concurrent* run at the same time.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agent_baton.core.runtime.launcher import AgentLauncher, LaunchResult


@dataclass
class SchedulerConfig:
    """Configuration for StepScheduler.

    Attributes:
        max_concurrent: Maximum number of agent launches that can run
            simultaneously.  This controls the ``asyncio.Semaphore``
            inside ``StepScheduler``.  Set lower on resource-constrained
            machines or when agents are competing for shared resources
            (e.g., same git repository).
    """
    max_concurrent: int = 3


class StepScheduler:
    """Dispatch parallel steps with bounded concurrency.

    Uses ``asyncio.Semaphore`` to cap the number of simultaneously running
    agent launches.  All steps in a batch are started concurrently as
    asyncio tasks, but at most ``max_concurrent`` actually run at the same
    time.

    The scheduler is stateless between batches and can be reused across
    multiple dispatch rounds within the same ``TaskWorker`` session.

    Example usage::

        scheduler = StepScheduler(SchedulerConfig(max_concurrent=2))
        results = await scheduler.dispatch_batch(steps, launcher)

    Attributes:
        _config: Scheduler configuration (concurrency limit).
        _semaphore: Asyncio semaphore enforcing the concurrency cap.
        _active: Current count of in-flight launches (for observability).
    """

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self._config = config or SchedulerConfig()
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
        self._active: int = 0

    @property
    def max_concurrent(self) -> int:
        """Maximum number of concurrent agent launches."""
        return self._config.max_concurrent

    @property
    def active_count(self) -> int:
        """Number of currently active (in-flight) launches."""
        return self._active

    async def dispatch(
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str,
        launcher: AgentLauncher,
        mcp_servers: list[str] | None = None,
        cwd_override: str | None = None,
        task_id: str = "",
    ) -> LaunchResult:
        """Dispatch a single step, respecting the concurrency limit.

        Args:
            cwd_override: When set, the agent subprocess is launched with
                this directory as its working directory.  Used for Wave 1.3
                worktree isolation to run each agent inside its isolated
                worktree.  Forwarded to the launcher via kwargs so
                non-worktree launchers (e.g. ``DryRunLauncher``) can safely
                ignore it.
            task_id: Optional task identifier propagated to the subprocess as
                ``BATON_TASK_ID`` when ``cwd_override`` is set.
        """
        async with self._semaphore:
            self._active += 1
            try:
                # Forward cwd_override and task_id as keyword arguments.
                # Launchers that do not support these kwargs (e.g. DryRunLauncher)
                # simply ignore them because they accept **kwargs or have no such
                # parameter — the Protocol does not declare them, but concrete
                # implementations (ClaudeCodeLauncher) do, so we pass via kwargs
                # and rely on duck-typing.
                launch_kwargs: dict = {}
                if cwd_override:
                    launch_kwargs["cwd_override"] = cwd_override
                if task_id:
                    launch_kwargs["task_id"] = task_id
                return await launcher.launch(
                    agent_name, model, prompt, step_id,
                    mcp_servers=mcp_servers,
                    **launch_kwargs,
                )
            finally:
                self._active -= 1

    async def dispatch_batch(
        self,
        steps: list[dict],
        launcher: AgentLauncher,
    ) -> list[LaunchResult]:
        """Dispatch multiple steps in parallel, bounded by *max_concurrent*.

        Each step dict must contain: ``agent_name``, ``model``, ``prompt``,
        ``step_id``.  Optional keys:

        - ``mcp_servers`` (list of strings): enable selective MCP pass-through.
        - ``cwd_override`` (str): working directory for the agent subprocess
          (Wave 1.3 worktree isolation).
        - ``task_id`` (str): task identifier forwarded as ``BATON_TASK_ID``.

        Returns results in the same order as *steps*.
        """
        tasks = [
            self.dispatch(
                agent_name=s["agent_name"],
                model=s["model"],
                prompt=s["prompt"],
                step_id=s["step_id"],
                launcher=launcher,
                mcp_servers=s.get("mcp_servers") or None,
                cwd_override=s.get("cwd_override") or None,
                task_id=s.get("task_id") or "",
            )
            for s in steps
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)
