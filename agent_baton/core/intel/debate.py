"""D4 — Multi-Agent Debate (Tier-4 research feature).

For high-stakes design decisions (architecture choices, security tradeoffs,
risky refactors), run a structured debate between 2-3 specialist agents
with different viewpoints, then have a moderator agent synthesize a final
recommendation.

The motivating insight: a single LLM agent's confidence is unreliable;
adversarial dialogue surfaces hidden assumptions.

This module is opt-in — invoked exclusively via ``baton debate`` from the
CLI. Nothing in the engine auto-invokes it.

Design constraints:
- Sequential dispatch (no async / parallel viewpoint dispatch in v1).
- Maximum 5 viewpoints.
- The Claude runner is pluggable; tests inject a mock.
- Runner failures degrade gracefully — a failed viewpoint records its
  error in the transcript and the debate continues.

The debate transcript is persisted to the ``debates`` table (schema v30).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


MAX_VIEWPOINTS = 5
DEFAULT_ROUNDS = 2
DEFAULT_MODERATOR = "architect"


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ViewpointSpec:
    """A single debate participant.

    ``agent_name`` is the specialist agent (e.g. ``security-reviewer``);
    ``framing`` is the bias / lens they should argue from (e.g.
    ``prioritize defense-in-depth``).
    """

    agent_name: str
    framing: str


@dataclass
class DebateTurn:
    """One agent contribution in the debate transcript."""

    agent_name: str
    round_number: int
    content: str
    timestamp: str = field(default_factory=_utcnow)


@dataclass
class DebateResult:
    """Output of a completed debate."""

    question: str
    transcript: list[DebateTurn]
    recommendation: str
    unresolved: list[str]
    debate_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "debate_id": self.debate_id,
            "question": self.question,
            "transcript": [asdict(t) for t in self.transcript],
            "recommendation": self.recommendation,
            "unresolved": list(self.unresolved),
        }


# ---------------------------------------------------------------------------
# Runner protocol
# ---------------------------------------------------------------------------


class DebateRunner(Protocol):
    """Async callable contract for dispatching a single agent prompt.

    Receives ``(agent_name, prompt)`` and returns the agent's text reply.
    On failure, MUST raise — the orchestrator records the exception in
    the transcript and continues the debate.
    """

    async def __call__(self, agent_name: str, prompt: str) -> str: ...  # noqa: D401,E704


def stub_runner(agent_name: str, prompt: str) -> str:
    """Deterministic dry-run runner used when no real Claude runner is wired.

    Echoes a short notice naming the agent. Useful for CLI smoke tests
    and for projects where the ``claude`` CLI is not installed.
    """
    return (
        f"[stub-runner] {agent_name} would respond here. "
        "No real Claude dispatch was performed (debate runner unavailable)."
    )


def make_headless_runner(headless_cls: type | None = None) -> DebateRunner | None:
    """Build a ``DebateRunner`` backed by ``HeadlessClaude`` if available.

    Returns ``None`` if the headless module / claude binary cannot be used,
    so callers can fall back to the stub runner.
    """
    try:  # local import — headless is optional
        if headless_cls is None:
            from agent_baton.core.runtime.headless import HeadlessClaude  # type: ignore
            headless_cls = HeadlessClaude
    except Exception as exc:  # noqa: BLE001
        logger.info("HeadlessClaude unavailable for debate runner: %s", exc)
        return None

    try:
        client = headless_cls()
    except Exception as exc:  # noqa: BLE001
        logger.info("HeadlessClaude construction failed: %s", exc)
        return None

    if not getattr(client, "is_available", False):
        logger.info("claude CLI not on PATH — debate will use stub runner")
        return None

    async def _runner(agent_name: str, prompt: str) -> str:
        # Prefix with the agent persona; HeadlessClaude doesn't know about
        # subagent delegation directly, so we frame the prompt textually.
        framed = f"You are the {agent_name} agent.\n\n{prompt}"
        result = await client.run(framed)
        if not result.success:
            raise RuntimeError(result.error or "headless dispatch failed")
        return result.output

    return _runner


# ---------------------------------------------------------------------------
# DebateOrchestrator
# ---------------------------------------------------------------------------


class DebateOrchestrator:
    """Coordinate a multi-round, multi-agent debate and synthesize a result."""

    def __init__(
        self,
        runner: DebateRunner | Callable[[str, str], Awaitable[str]] | None = None,
        *,
        db_path: str | None = None,
    ) -> None:
        # Default: try real headless runner; fall back to stub.
        if runner is None:
            runner = make_headless_runner() or _wrap_sync(stub_runner)
        self._runner = runner
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_debate(
        self,
        question: str,
        viewpoints: list[ViewpointSpec],
        rounds: int = DEFAULT_ROUNDS,
        moderator_agent: str = DEFAULT_MODERATOR,
    ) -> DebateResult:
        """Run a synchronous debate. Wraps the async loop with ``asyncio.run``.

        Constraints:
            * 2 <= len(viewpoints) <= MAX_VIEWPOINTS
            * rounds >= 1
        """
        if not question or not question.strip():
            raise ValueError("question must be non-empty")
        if len(viewpoints) < 2:
            raise ValueError("need at least 2 viewpoints for a debate")
        if len(viewpoints) > MAX_VIEWPOINTS:
            raise ValueError(f"max {MAX_VIEWPOINTS} viewpoints supported")
        if rounds < 1:
            raise ValueError("rounds must be >= 1")

        return asyncio.run(
            self._run_debate_async(question, viewpoints, rounds, moderator_agent)
        )

    async def _run_debate_async(
        self,
        question: str,
        viewpoints: list[ViewpointSpec],
        rounds: int,
        moderator_agent: str,
    ) -> DebateResult:
        transcript: list[DebateTurn] = []

        # ------- Rounds -------
        for round_number in range(1, rounds + 1):
            for vp in viewpoints:
                prompt = self._build_viewpoint_prompt(
                    question=question,
                    viewpoint=vp,
                    round_number=round_number,
                    transcript=transcript,
                )
                content = await self._safe_dispatch(vp.agent_name, prompt)
                transcript.append(
                    DebateTurn(
                        agent_name=vp.agent_name,
                        round_number=round_number,
                        content=content,
                    )
                )

        # ------- Moderator synthesis -------
        moderator_prompt = self._build_moderator_prompt(question, transcript)
        moderator_text = await self._safe_dispatch(moderator_agent, moderator_prompt)
        recommendation, unresolved = self._parse_moderator_output(moderator_text)

        result = DebateResult(
            question=question,
            transcript=transcript,
            recommendation=recommendation,
            unresolved=unresolved,
            debate_id=f"db-{uuid.uuid4().hex[:12]}",
        )

        # Best-effort persistence; never block the result on a DB error.
        if self._db_path:
            try:
                persist_debate(self._db_path, result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("debate persistence failed: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    async def _safe_dispatch(self, agent_name: str, prompt: str) -> str:
        """Invoke the runner; on failure, return a tagged error string."""
        try:
            out = self._runner(agent_name, prompt)
            if asyncio.iscoroutine(out):
                out = await out
            return str(out).strip() or "[empty response]"
        except Exception as exc:  # noqa: BLE001
            logger.warning("debate dispatch failed for %s: %s", agent_name, exc)
            return f"[dispatch failed: {exc}]"

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_viewpoint_prompt(
        *,
        question: str,
        viewpoint: ViewpointSpec,
        round_number: int,
        transcript: list[DebateTurn],
    ) -> str:
        parts = [
            "You are participating in a structured multi-agent debate.",
            "",
            f"## Question\n{question}",
            "",
            f"## Your Framing\n{viewpoint.framing}",
            "",
            f"## Round {round_number}",
        ]
        if transcript:
            parts.append("\n## Transcript so far")
            for t in transcript:
                parts.append(
                    f"\n### {t.agent_name} (round {t.round_number})\n{t.content}"
                )
        if round_number == 1 and not transcript:
            parts.append(
                "\nGive your opening position in roughly 200 words. State your "
                "core recommendation, the strongest argument for it, and one "
                "counterargument you anticipate."
            )
        else:
            parts.append(
                "\nRespond to the other viewpoints above in roughly 200 words. "
                "Do NOT merely restate your earlier position — explicitly "
                "engage with the strongest opposing argument and revise your "
                "stance if warranted."
            )
        return "\n".join(parts)

    @staticmethod
    def _build_moderator_prompt(question: str, transcript: list[DebateTurn]) -> str:
        parts = [
            "You are the moderator of a multi-agent debate. Read the full "
            "transcript and produce a synthesis.",
            "",
            f"## Question\n{question}",
            "",
            "## Transcript",
        ]
        for t in transcript:
            parts.append(
                f"\n### {t.agent_name} (round {t.round_number})\n{t.content}"
            )
        parts.extend([
            "",
            "## Output format",
            "Return your synthesis using EXACTLY these two sections:",
            "",
            "## Recommendation",
            "<your concrete final recommendation in 1-3 paragraphs>",
            "",
            "## Unresolved",
            "- <bullet 1: a disagreement that the debate did not settle>",
            "- <bullet 2>",
            "",
            "If there are no unresolved disagreements, write '- none' under "
            "the Unresolved heading.",
        ])
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_moderator_output(text: str) -> tuple[str, list[str]]:
        """Split the moderator's reply into recommendation + unresolved list.

        Tolerant of missing headings — falls back to the whole text as the
        recommendation and an empty unresolved list.
        """
        if not text:
            return "", []

        rec = text.strip()
        unresolved: list[str] = []

        # Extract the Recommendation section
        rec_match = re.search(
            r"##\s*Recommendation\s*\n(.+?)(?=\n##\s|\Z)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if rec_match:
            rec = rec_match.group(1).strip()

        # Extract the Unresolved section
        unr_match = re.search(
            r"##\s*Unresolved\s*\n(.+?)(?=\n##\s|\Z)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if unr_match:
            block = unr_match.group(1).strip()
            for line in block.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                # Strip leading bullet markers
                bullet = re.sub(r"^[-*•]\s*", "", stripped).strip()
                if not bullet:
                    continue
                if bullet.lower() == "none":
                    continue
                unresolved.append(bullet)

        return rec, unresolved


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_debate(db_path: str, result: DebateResult) -> None:
    """Insert a debate row into the ``debates`` table.

    Idempotent: uses INSERT OR REPLACE keyed on ``debate_id``.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO debates "
            "(debate_id, question, transcript_json, recommendation, "
            " unresolved_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                result.debate_id,
                result.question,
                json.dumps([asdict(t) for t in result.transcript]),
                result.recommendation,
                json.dumps(list(result.unresolved)),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _wrap_sync(fn: Callable[[str, str], str]) -> DebateRunner:
    """Adapt a synchronous ``(agent, prompt) -> str`` into an async runner."""

    async def _async(agent_name: str, prompt: str) -> str:
        return fn(agent_name, prompt)

    return _async
