"""Engine-facing dry-run launcher.

This module promotes the existing :class:`agent_baton.core.runtime.launcher.DryRunLauncher`
to the ``core.engine`` namespace so that callers wiring the dry-run testing
harness (DX.5) can import from a single, intent-revealing path:

    from agent_baton.core.engine.dry_run_launcher import DryRunLauncher

The runtime path (``core.runtime.launcher``) remains canonical for the
``AgentLauncher`` protocol and the production ``ClaudeCodeLauncher``.
This re-export is purely a discoverability and intent layer — the class
identity is shared so all existing tests and runtime wiring keep working.

The ``TracingDryRunLauncher`` extends the base mock with richer per-launch
metadata (timestamps, prompt size, token estimate) used by the dry-run
report writer.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_baton.core.runtime.launcher import (
    AgentLauncher,
    DryRunLauncher,
    LaunchResult,
)


class TracingDryRunLauncher(DryRunLauncher):
    """Dry-run launcher that records prompt size and timestamps.

    Adds extra fields to each ``launches`` entry so the dry-run report can
    surface per-step metadata without changing the base ``DryRunLauncher``
    contract used elsewhere in the codebase.

    Extra recorded fields per launch:

    - ``prompt_chars`` -- length of the delegation prompt in characters
    - ``estimated_tokens`` -- crude prompt-size-based token estimate
      (``prompt_chars // 4``); a placeholder for proper Haiku-classifier
      integration.
    - ``launched_at`` -- ISO-8601 UTC timestamp of when the mock launch ran.
    """

    async def launch(  # type: ignore[override]
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str = "",
        mcp_servers: list[str] | None = None,
    ) -> LaunchResult:
        result = await super().launch(
            agent_name=agent_name,
            model=model,
            prompt=prompt,
            step_id=step_id,
            mcp_servers=mcp_servers,
        )
        # The base class appended an entry already; enrich it in place.
        if self.launches:
            entry = self.launches[-1]
            entry["prompt_chars"] = len(prompt)
            entry["estimated_tokens"] = max(1, len(prompt) // 4)
            entry["launched_at"] = datetime.now(timezone.utc).isoformat()
        return result


__all__ = [
    "AgentLauncher",
    "DryRunLauncher",
    "LaunchResult",
    "TracingDryRunLauncher",
]
