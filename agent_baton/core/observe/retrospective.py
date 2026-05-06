"""Retrospective engine -- generates and manages task retrospectives.

Retrospectives are the qualitative counterpart to usage records.  While
:class:`~agent_baton.core.observe.usage.UsageLogger` captures numeric
metrics, retrospectives record *why* things went well or poorly: which
agents succeeded, which failed, what knowledge was missing, and what
roster changes are recommended.

Retrospectives bridge the observe and learn layers:

* The :class:`RetrospectiveEngine` merges explicit ``KNOWLEDGE_GAP``
  signals from execution with implicit gap detection (scanning narrative
  text for phrases like "lacked context" or "assumed incorrectly").
* :class:`~agent_baton.core.learn.pattern_learner.PatternLearner` reads
  retrospective JSON sidecars to surface per-agent knowledge gaps.
* :class:`~agent_baton.core.improve.scoring.PerformanceScorer` scans
  retrospective markdown for positive/negative agent mentions to compute
  qualitative scorecard signals.

Note: prompt-improvement proposals are no longer generated in-process.
The ``learning-analyst`` agent (dispatched via ``baton learn run-cycle``)
reads the retrospectives produced here and emits evidence-cited
recommendations -- see L2.1 retirement (bd-362f).

Storage format: each retrospective is persisted as a pair of files in
``<team_context_root>/retrospectives/``:

* ``<task_id>.md`` -- human-readable Markdown narrative.
* ``<task_id>.json`` -- structured JSON sidecar for programmatic access.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.models.feedback import RetrospectiveFeedback
from agent_baton.models.knowledge import KnowledgeGapRecord

if TYPE_CHECKING:
    from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore

logger = logging.getLogger(__name__)
from agent_baton.models.retrospective import (
    AgentOutcome,
    ConflictRecord,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
    SequencingNote,
    TeamCompositionRecord,
)
from agent_baton.models.usage import TaskUsageRecord

# Phrases that indicate an implicit knowledge gap in retrospective narrative text.
# Each entry is a compiled regex pattern for fast scanning.
_IMPLICIT_GAP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"lacked context", re.IGNORECASE),
    re.compile(r"didn['\u2019]t know about", re.IGNORECASE),
    re.compile(r"assumed incorrectly", re.IGNORECASE),
    re.compile(r"no knowledge of", re.IGNORECASE),
    re.compile(r"unaware of", re.IGNORECASE),
    re.compile(r"missing context", re.IGNORECASE),
    re.compile(r"lack(?:ed|ing) information", re.IGNORECASE),
)

# Compiled patterns used by the section-aware parser.
_HEADER_RE: re.Pattern[str] = re.compile(r"^(#{2,3})\s+(.+)", re.MULTILINE)
_BULLET_RE: re.Pattern[str] = re.compile(r"^[ \t]*[-*]\s+(.+)")


def parse_markdown_sections(text: str) -> dict[str, list[str]]:
    """Parse retrospective Markdown into a section-title \u2192 bullets mapping.

    Splits the document on H2 (``## \u2026``) and H3 (``### \u2026``) headers, then
    collects bullet lines (``- \u2026`` or ``* \u2026``, with any leading whitespace)
    within each section.  The result is a dict keyed by the section title
    (stripped of leading/trailing whitespace) whose values are lists of
    stripped bullet texts.

    Tolerances built in:

    - Windows-style line endings (``\\r\\n``) are normalised before parsing.
    - Multiple consecutive blank lines are ignored.
    - Leading indentation before bullet markers is accepted.
    - Both ``-`` and ``*`` list markers are recognised.
    - Nested bullets (extra leading whitespace) are included as flat entries
      in the enclosing section.

    Args:
        text: Raw Markdown content of a retrospective file.

    Returns:
        ``dict[section_title, list[bullet_text]]``.  Headers with no bullets
        are included with an empty list.  Returns an empty dict when the
        document contains no H2/H3 headers.
    """
    # Normalise line endings.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    sections: dict[str, list[str]] = {}
    current_title: str | None = None
    current_bullets: list[str] = []

    for line in text.splitlines():
        header_match = _HEADER_RE.match(line)
        if header_match:
            # Flush the previous section.
            if current_title is not None:
                sections[current_title] = current_bullets
            current_title = header_match.group(2).strip()
            current_bullets = []
            continue

        if current_title is not None:
            bullet_match = _BULLET_RE.match(line)
            if bullet_match:
                current_bullets.append(bullet_match.group(1).strip())

    # Flush the last section.
    if current_title is not None:
        sections[current_title] = current_bullets

    return sections


class RetrospectiveEngine:
    """Generate structured retrospectives and store them on disk.

    Retrospectives are written to .claude/team-context/retrospectives/[task_id].md
    and provide qualitative signal about what went well, what failed, and
    what the system should learn for next time.
    """

    def __init__(
        self,
        retrospectives_dir: Path | None = None,
        *,
        telemetry: KnowledgeTelemetryStore | None = None,
    ) -> None:
        self._dir = (retrospectives_dir or Path(".claude/team-context/retrospectives")).resolve()
        # Optional F0.4 lifecycle telemetry side-channel; failures must not
        # crash retro generation.
        self._telemetry = telemetry

    @property
    def dir(self) -> Path:
        return self._dir

    def generate_from_usage(
        self,
        usage: TaskUsageRecord,
        task_name: str = "",
        what_worked: list[AgentOutcome] | None = None,
        what_didnt: list[AgentOutcome] | None = None,
        knowledge_gaps: list[KnowledgeGapRecord] | None = None,
        roster_recommendations: list[RosterRecommendation] | None = None,
        sequencing_notes: list[SequencingNote] | None = None,
        task_type: str | None = None,
        task_summary: str = "",
        team_compositions: list[TeamCompositionRecord] | None = None,
        conflicts: list[ConflictRecord] | None = None,
        attached_docs: list[tuple[str, str]] | None = None,
    ) -> Retrospective:
        """Generate a retrospective from a usage record plus qualitative input.

        The usage record provides metrics (agent count, retries, gates, tokens).
        The qualitative fields (what_worked, what_didnt, etc.) are provided by
        the orchestrator based on its observations during the task.

        Explicit ``knowledge_gaps`` (from KNOWLEDGE_GAP signals during execution)
        are merged with any implicit gaps detected by scanning the narrative text
        in ``what_didnt``.  Duplicates (same description) are removed; explicit
        gaps take precedence.

        Args:
            usage: Metrics from the completed task execution.
            task_name: Human-readable task name (falls back to task_id).
            what_worked: Agent outcomes that succeeded.
            what_didnt: Agent outcomes that had issues — scanned for implicit gaps.
            knowledge_gaps: Explicit :class:`~agent_baton.models.knowledge.KnowledgeGapRecord`
                entries from KNOWLEDGE_GAP signals captured during execution.
            roster_recommendations: Agent roster change suggestions.
            sequencing_notes: Phase-level observations about gate effectiveness.
            task_type: Inferred task type (e.g. "feature", "bugfix") for gap indexing.
            task_summary: Short summary of the task for gap context.
        """
        total_tokens = sum(a.estimated_tokens for a in usage.agents_used)
        total_retries = sum(a.retries for a in usage.agents_used)

        resolved_task_summary = task_summary or task_name or usage.task_id

        explicit_gaps: list[KnowledgeGapRecord] = list(knowledge_gaps or [])
        implicit_gaps = self._detect_implicit_gaps(
            what_didnt or [],
            task_type=task_type,
            task_summary=resolved_task_summary,
        )

        # Merge: deduplicate by description, explicit entries win over implicit.
        seen_descriptions: set[str] = {g.description for g in explicit_gaps}
        merged_gaps: list[KnowledgeGapRecord] = list(explicit_gaps)
        for gap in implicit_gaps:
            if gap.description not in seen_descriptions:
                seen_descriptions.add(gap.description)
                merged_gaps.append(gap)

        retro = Retrospective(
            task_id=usage.task_id,
            task_name=task_name or usage.task_id,
            timestamp=usage.timestamp,
            agent_count=len(usage.agents_used),
            retry_count=total_retries,
            gates_passed=usage.gates_passed,
            gates_failed=usage.gates_failed,
            risk_level=usage.risk_level,
            estimated_tokens=total_tokens,
            what_worked=what_worked or [],
            what_didnt=what_didnt or [],
            knowledge_gaps=merged_gaps,
            roster_recommendations=roster_recommendations or [],
            sequencing_notes=sequencing_notes or [],
            team_compositions=team_compositions or [],
            conflicts=conflicts or [],
        )

        # F0.4 telemetry: correlate task outcome with each attached knowledge
        # doc.  Outcome score = gates_passed / (gates_passed + gates_failed),
        # defaulting to 1.0 when no gates ran.  Best-effort — never raise.
        if self._telemetry is not None and attached_docs:
            self._emit_outcome_events(
                attached_docs,
                task_id=usage.task_id,
                gates_passed=usage.gates_passed,
                gates_failed=usage.gates_failed,
            )

        return retro

    def _emit_outcome_events(
        self,
        attached_docs: list[tuple[str, str]],
        *,
        task_id: str,
        gates_passed: int,
        gates_failed: int,
    ) -> None:
        """Best-effort emission of KnowledgeOutcome telemetry rows."""
        if self._telemetry is None:
            return
        total_gates = gates_passed + gates_failed
        if total_gates > 0:
            score = gates_passed / total_gates
        else:
            score = 1.0
        for doc_name, pack_name in attached_docs:
            try:
                self._telemetry.record_outcome(
                    doc_name=doc_name,
                    pack_name=pack_name or "",
                    task_id=task_id,
                    outcome_correlation=score,
                )
            except Exception as exc:  # noqa: BLE001 — telemetry must not crash retro
                logger.debug(
                    "KnowledgeTelemetry.record_outcome failed for %s/%s: %s",
                    pack_name, doc_name, exc,
                )

    # ------------------------------------------------------------------
    # Implicit gap detection
    # ------------------------------------------------------------------

    def _detect_implicit_gaps(
        self,
        outcomes: list[AgentOutcome],
        *,
        task_type: str | None = None,
        task_summary: str = "",
    ) -> list[KnowledgeGapRecord]:
        """Scan narrative text in *outcomes* for knowledge-gap indicators.

        Inspects the ``issues`` and ``root_cause`` fields of each
        :class:`~agent_baton.models.retrospective.AgentOutcome`.  Any sentence
        (line) containing a recognised phrase is emitted as a candidate
        :class:`~agent_baton.models.knowledge.KnowledgeGapRecord` with
        ``resolution="unresolved"``.

        Deduplication by description is performed within this method; the caller
        performs a second dedup pass against explicit gaps.

        Returns:
            List of detected gap records, possibly empty.
        """
        gaps: list[KnowledgeGapRecord] = []
        seen: set[str] = set()

        for outcome in outcomes:
            agent = outcome.name
            for text in (outcome.issues, outcome.root_cause):
                if not text:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if any(pat.search(line) for pat in _IMPLICIT_GAP_PATTERNS):
                        if line not in seen:
                            seen.add(line)
                            gaps.append(
                                KnowledgeGapRecord(
                                    description=line,
                                    gap_type="contextual",
                                    resolution="unresolved",
                                    resolution_detail="",
                                    agent_name=agent,
                                    task_summary=task_summary,
                                    task_type=task_type,
                                )
                            )
        return gaps

    def save(self, retro: Retrospective) -> Path:
        """Write a retrospective to disk as Markdown with a JSON sidecar.

        Both files share the same stem (``<sanitized_task_id>``) so
        structured data can be reloaded without Markdown parsing.  The
        JSON sidecar is the preferred machine-readable format for
        downstream consumers such as
        :meth:`~agent_baton.core.learn.pattern_learner.PatternLearner.knowledge_gaps_for`
        and :meth:`load_recent_feedback`.

        Args:
            retro: The retrospective to persist.

        Returns:
            Absolute path to the written Markdown file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        # Sanitize task_id for filename
        safe_id = retro.task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        path.write_text(retro.to_markdown(), encoding="utf-8")

        # JSON sidecar — structured data for programmatic consumption
        json_path = self._dir / f"{safe_id}.json"
        json_path.write_text(
            json.dumps(retro.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        return path

    def load(self, task_id: str) -> str | None:
        """Read a retrospective by task ID. Returns markdown content or None."""
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_retrospectives(self) -> list[Path]:
        """List all retrospective files, sorted by name (most recent last)."""
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.md"))

    def list_recent(self, count: int = 5) -> list[Path]:
        """Return the N most recent retrospective files."""
        all_retros = self.list_retrospectives()
        return all_retros[-count:]

    def search(self, keyword: str) -> list[Path]:
        """Find retrospectives containing a keyword (case-insensitive)."""
        results = []
        keyword_lower = keyword.lower()
        for path in self.list_retrospectives():
            try:
                content = path.read_text(encoding="utf-8")
                if keyword_lower in content.lower():
                    results.append(path)
            except OSError:
                continue
        return results

    def list_json_files(self) -> list[Path]:
        """List all retrospective JSON sidecar files, sorted by name."""
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.json"))

    def load_recent_feedback(self, limit: int = 5) -> RetrospectiveFeedback:
        """Load structured feedback from the most recent retrospective JSON files.

        Reads the last *limit* JSON sidecars (written alongside each markdown
        file by :meth:`save`).  Falls back gracefully to markdown parsing via
        :meth:`extract_recommendations` when no JSON sidecars exist yet.

        Args:
            limit: Maximum number of retrospective files to read.

        Returns:
            A :class:`~agent_baton.models.feedback.RetrospectiveFeedback`
            aggregating roster recommendations, knowledge gaps, and sequencing
            notes across the selected retrospectives.
        """
        json_files = self.list_json_files()[-limit:]

        if json_files:
            return self._feedback_from_json(json_files)

        # Legacy fallback: no JSON sidecars — parse markdown for recommendations only.
        recs = self.extract_recommendations()
        return RetrospectiveFeedback(
            roster_recommendations=recs,
            source_count=len(self.list_recent(limit)),
        )

    def _feedback_from_json(self, json_files: list[Path]) -> RetrospectiveFeedback:
        """Deserialise feedback from JSON sidecar files."""
        roster: list[RosterRecommendation] = []
        gaps: list[KnowledgeGapRecord] = []
        notes: list[SequencingNote] = []
        loaded = 0

        for path in json_files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            try:
                retro = Retrospective.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue

            roster.extend(retro.roster_recommendations)
            gaps.extend(retro.knowledge_gaps)
            notes.extend(retro.sequencing_notes)
            loaded += 1

        return RetrospectiveFeedback(
            roster_recommendations=roster,
            knowledge_gaps=gaps,
            sequencing_notes=notes,
            source_count=loaded,
        )

    def extract_recommendations(self) -> list[RosterRecommendation]:
        """Extract all roster recommendations across all retrospectives.

        Uses :func:`parse_markdown_sections` to locate the
        ``Roster Recommendations`` section (tolerates H2 or H3, varying
        whitespace, and Windows line endings), then parses each bullet for
        the pattern ``**Action:** target-name``.

        This is a legacy fallback used when no JSON sidecars exist; prefer
        :meth:`load_recent_feedback` which reads structured JSON when
        available.

        Returns:
            Aggregated list of roster recommendations extracted from all
            retrospective files.  The list feeds into the orchestrator's
            planning phase and the talent-builder agent for agent roster
            evolution.
        """
        # Pattern: **Create:** target  (bold action followed by colon)
        _rec_re = re.compile(r"\*\*([^*]+):\*\*\s*(.+)")

        recommendations: list[RosterRecommendation] = []
        for path in self.list_retrospectives():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            sections = parse_markdown_sections(content)
            # Find the roster section regardless of exact title casing/spacing.
            for title, bullets in sections.items():
                if "roster recommendation" in title.lower():
                    for bullet in bullets:
                        m = _rec_re.match(bullet)
                        if m:
                            recommendations.append(
                                RosterRecommendation(
                                    action=m.group(1).strip().lower(),
                                    target=m.group(2).strip(),
                                )
                            )
        return recommendations
