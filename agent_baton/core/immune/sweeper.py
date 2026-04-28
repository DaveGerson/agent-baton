"""Wave 6.2 Part B — Sweeper: dispatch sweep agents for immune findings.

For each sweep kind, a dedicated sweep-agent (``agents/immune-*.md``) is
dispatched via ``ClaudeCodeLauncher.launch()`` with the cached project context
injected as a prompt-cache prefix.

The sweeper returns a :class:`SweepFinding` when an issue is detected, or
``None`` when the target is clean.

Agent–kind mapping:

=========================  =================================
Kind                       Agent
=========================  =================================
``deprecated-api``         ``immune-deprecated-api``
``untested-edges``         ``immune-untested-edges``
``stale-comment``          ``immune-stale-comment``
``todo-rot``               ``immune-todo-rot``
``doc-drift``              ``immune-doc-drift``
=========================  =================================
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.immune.cache import ContextCache
    from agent_baton.core.immune.scheduler import SweepTarget

_log = logging.getLogger(__name__)

__all__ = ["SweepFinding", "Sweeper"]

# Mapping from sweep kind to agent name (matches agents/*.md frontmatter).
_KIND_AGENT: dict[str, str] = {
    "deprecated-api": "immune-deprecated-api",
    "untested-edges": "immune-untested-edges",
    "stale-comment": "immune-stale-comment",
    "todo-rot": "immune-todo-rot",
    "doc-drift": "immune-doc-drift",
}

# Sweep kinds that are eligible for the auto-fix allowlist in FindingTriage.
AUTO_FIX_KINDS: frozenset[str] = frozenset({
    "deprecated-api-trivial",
    "doc-drift-signature",
    "stale-comment",
})

# Minimum confidence for an auto_fix_directive to be populated.
_AUTO_FIX_CONFIDENCE_THRESHOLD = 0.85


@dataclass
class SweepFinding:
    """A finding produced by a sweep agent.

    Attributes:
        target: The :class:`~agent_baton.core.immune.scheduler.SweepTarget`
            that was swept.
        confidence: Sweep agent confidence score 0.0–1.0.
        description: Human-readable summary, ≤120 characters.
        affected_lines: List of line numbers flagged in the target file.
        auto_fix_directive: Populated when *confidence* ≥ 0.85 and the kind
            is in the auto-fix allowlist; contains a directive string for the
            self-heal micro-agent.
        kind: Sweep kind (mirrors ``target.kind`` but may be more specific,
            e.g. ``"deprecated-api-trivial"``).
    """

    target: "SweepTarget"
    confidence: float
    description: str
    affected_lines: list[int] = field(default_factory=list)
    auto_fix_directive: str = ""
    kind: str = ""

    def __post_init__(self) -> None:
        if not self.kind:
            self.kind = self.target.kind


# ---------------------------------------------------------------------------
# Sweeper
# ---------------------------------------------------------------------------


class Sweeper:
    """Dispatches immune sweep agents and parses their findings.

    Args:
        cache: :class:`~agent_baton.core.immune.cache.ContextCache` instance
            for injecting the project context prefix.
        launcher: ``ClaudeCodeLauncher`` instance.  Typed as ``Any`` here to
            avoid a hard import of the launcher (which requires the Claude
            Code CLI to be installed).
    """

    # Expected JSON schema keys in the agent's output.
    _REQUIRED_KEYS = ("found", "confidence", "description")

    def __init__(self, cache: "ContextCache", launcher: object) -> None:
        self._cache = cache
        self._launcher = launcher

    def sweep(self, target: "SweepTarget") -> SweepFinding | None:
        """Dispatch the appropriate sweep agent for *target*.

        Uses ``cache.get_or_build()`` to inject the project context as the
        prompt-cache prefix (Anthropic prompt caching breakpoint).

        Args:
            target: The :class:`~agent_baton.core.immune.scheduler.SweepTarget`
                to sweep.

        Returns:
            A :class:`SweepFinding` when an issue is detected, or ``None``
            when the target is clean or the agent returns an unparseble result.
        """
        agent_name = _KIND_AGENT.get(target.kind)
        if agent_name is None:
            _log.warning("Sweeper: unknown sweep kind %r — skipping", target.kind)
            return None

        context_snapshot = self._cache.get_or_build()
        prompt = self._build_prompt(target, context_snapshot)

        try:
            result = self._launcher.launch(  # type: ignore[union-attr]
                agent_name=agent_name,
                prompt=prompt,
                cwd_override=str(target.path.parent)
                if target.path.is_file()
                else str(target.path),
            )
        except Exception as exc:
            _log.warning(
                "Sweeper: launch failed for %s/%s: %s",
                target.kind, target.path, exc,
            )
            return None

        return self._parse_result(target, result)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(target: "SweepTarget", context_snapshot: str) -> str:
        """Build the sweep prompt with the cached context prefix."""
        return (
            f"<project_context cache_control='ephemeral'>\n"
            f"{context_snapshot}\n"
            f"</project_context>\n\n"
            f"Sweep the file at the path below for issues of kind '{target.kind}'.\n"
            f"Target path: {target.path}\n\n"
            f"Respond with a JSON object matching this exact schema:\n"
            f'{{"found": <bool>, "confidence": <0.0-1.0>, "description": "<≤120 chars>", '
            f'"affected_lines": [<int>, ...], "kind": "<specific-kind>", '
            f'"auto_fix_directive": "<directive or empty string>"}}\n\n'
            f"Output ONLY the JSON object. No prose, no code fences."
        )

    @classmethod
    def _parse_result(
        cls, target: "SweepTarget", raw: object
    ) -> SweepFinding | None:
        """Parse the agent's output into a :class:`SweepFinding`.

        Accepts either a string (JSON-encoded result) or a dict.  Returns
        ``None`` on parse failure or when ``found=false``.
        """
        if raw is None:
            return None

        # LaunchResult → try .output attribute first.
        text: str = ""
        if isinstance(raw, str):
            text = raw
        elif hasattr(raw, "output") and isinstance(raw.output, str):
            text = raw.output
        elif isinstance(raw, dict):
            data = raw
        else:
            text = str(raw)

        if text:
            # Strip leading/trailing whitespace and optional markdown fences.
            stripped = text.strip()
            if stripped.startswith("```"):
                lines = stripped.splitlines()
                inner = [l for l in lines if not l.startswith("```")]
                stripped = "\n".join(inner).strip()
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                _log.debug("Sweeper: could not parse agent output: %s | raw=%r", exc, text[:200])
                return None

        if not isinstance(data, dict):
            return None

        found = data.get("found")
        if not found:
            return None

        confidence = float(data.get("confidence", 0.0))
        description = str(data.get("description", ""))[:120]
        affected_lines = [int(l) for l in data.get("affected_lines", []) if isinstance(l, (int, float))]
        kind = str(data.get("kind", target.kind))
        auto_fix = str(data.get("auto_fix_directive", ""))

        # Only populate auto_fix_directive when confidence + kind qualify.
        if confidence < _AUTO_FIX_CONFIDENCE_THRESHOLD or kind not in AUTO_FIX_KINDS:
            auto_fix = ""

        return SweepFinding(
            target=target,
            confidence=confidence,
            description=description,
            affected_lines=affected_lines,
            auto_fix_directive=auto_fix,
            kind=kind,
        )
