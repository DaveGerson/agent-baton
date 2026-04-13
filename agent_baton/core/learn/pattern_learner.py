"""PatternLearner -- derive recurring orchestration patterns from usage logs.

The pattern learner is the core learning engine in Agent Baton's
closed-loop pipeline.  It reads the historical record of completed tasks
from :class:`~agent_baton.core.observe.usage.UsageLogger`, identifies
recurring success patterns (agent combinations, sequencing modes, retry
rates), and persists them to ``learned-patterns.json`` so the planner can
reuse proven workflows for future tasks of the same type.

Pattern derivation algorithm:

1. Group :class:`~agent_baton.models.usage.TaskUsageRecord` objects by
   ``sequencing_mode`` (proxy for task type).
2. For each group with >= ``min_sample_size`` records, compute:

   - ``success_rate`` = fraction of tasks with outcome ``"SHIP"``.
   - ``confidence`` = min(1.0, (sample_size / 15) * success_rate).
   - Most common agent combination (canonical sorted tuple).
   - Average token cost (from successful tasks, falling back to all).
   - Average retry rate and gate pass rate.

3. Groups whose confidence exceeds ``min_confidence`` are emitted as
   :class:`~agent_baton.models.pattern.LearnedPattern` objects.

The learner also provides :meth:`knowledge_gaps_for`, which reads
retrospective JSON sidecars to surface per-agent knowledge gaps --
enabling the planner to attach relevant knowledge packs before dispatching
agents that have historically lacked context.

"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.knowledge import KnowledgeGapRecord
from agent_baton.models.pattern import LearnedPattern, TeamPattern
from agent_baton.models.usage import TaskUsageRecord

_PATTERNS_FILE = "learned-patterns.json"
_TEAM_PATTERNS_FILE = "team-patterns.json"
_DEFAULT_TEAM_CONTEXT = Path(".claude/team-context")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _agent_combo_key(record: TaskUsageRecord) -> tuple[str, ...]:
    """Canonical, sorted tuple of agent names used in a task."""
    return tuple(sorted(a.name for a in record.agents_used))


def _total_tokens(record: TaskUsageRecord) -> int:
    return sum(a.estimated_tokens for a in record.agents_used)


def _total_retries(record: TaskUsageRecord) -> int:
    return sum(a.retries for a in record.agents_used)


def _gate_pass_rate(record: TaskUsageRecord) -> float | None:
    """Return fraction of gate results that are PASS, or None if no gates."""
    all_results: list[str] = []
    for agent in record.agents_used:
        all_results.extend(agent.gate_results)
    if not all_results:
        return None
    return sum(1 for r in all_results if r == "PASS") / len(all_results)


class PatternLearner:
    """Analyse completed task records and surface recurring success patterns.

    The learner reads TaskUsageRecords from a JSONL usage log, groups them by
    *sequencing_mode* (used as a proxy for task type until explicit tagging is
    added), and computes per-group statistics.  Groups that meet the minimum
    sample size and confidence thresholds are returned as :class:`LearnedPattern`
    objects.

    Confidence formula::

        confidence = min(1.0, (sample_size / 15) * success_rate)

    This ramps confidence linearly from 0 at 0 samples to a maximum of 1.0 at
    15+ samples (assuming perfect success rate).  The divisor 15 is a
    calibration constant; adjust via the formula if the dataset is larger.
    """

    _CONFIDENCE_CALIBRATION = 15

    def __init__(self, team_context_root: Path | None = None) -> None:
        self._root = (team_context_root or _DEFAULT_TEAM_CONTEXT).resolve()
        self._log_path = self._root / "usage-log.jsonl"
        self._patterns_path = self._root / _PATTERNS_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        min_sample_size: int = 5,
        min_confidence: float = 0.7,
    ) -> list[LearnedPattern]:
        """Read usage records and return patterns that pass the thresholds.

        Groups records by sequencing_mode.  For each qualifying group:

        - Computes success_rate = count(outcome=="SHIP") / total
        - Finds the most common agent combination (sorted tuple)
        - Computes avg_token_cost from successful tasks only (falls back to all
          tasks if none succeeded)
        - Applies confidence formula and filters by min_confidence

        Args:
            min_sample_size: Groups with fewer records are excluded entirely.
            min_confidence: Patterns whose confidence falls below this value
                are excluded from the result.

        Returns:
            List of :class:`LearnedPattern` sorted by confidence descending.
        """
        logger = UsageLogger(self._log_path)
        records = logger.read_all()

        if not records:
            return []

        # Group records by sequencing_mode
        groups: dict[str, list[TaskUsageRecord]] = {}
        for rec in records:
            mode = rec.sequencing_mode or "unknown"
            groups.setdefault(mode, []).append(rec)

        now = _now_iso()
        patterns: list[LearnedPattern] = []
        counter = 0

        for mode, group in groups.items():
            if len(group) < min_sample_size:
                continue

            success_tasks = [r for r in group if r.outcome == "SHIP"]
            success_rate = len(success_tasks) / len(group)

            confidence = min(
                1.0,
                (len(group) / self._CONFIDENCE_CALIBRATION) * success_rate,
            )

            if confidence < min_confidence:
                continue

            # Most common agent combination
            combo_counter: Counter[tuple[str, ...]] = Counter(
                _agent_combo_key(r) for r in group
            )
            best_combo, _ = combo_counter.most_common(1)[0]

            # Avg tokens: prefer successful tasks, fall back to whole group
            token_source = success_tasks if success_tasks else group
            avg_tokens = (
                sum(_total_tokens(r) for r in token_source) // len(token_source)
                if token_source
                else 0
            )

            # Retry quality signal: lower is better — embed in template desc
            avg_retries = (
                sum(_total_retries(r) for r in group) / len(group)
            )

            # Gate quality signal
            gate_rates = [
                gpr
                for r in group
                if (gpr := _gate_pass_rate(r)) is not None
            ]
            avg_gate_rate = (
                sum(gate_rates) / len(gate_rates) if gate_rates else None
            )

            template_desc = _build_template_description(
                mode, best_combo, avg_retries, avg_gate_rate
            )

            counter += 1
            pattern_id = f"{mode}-{counter:03d}"

            patterns.append(
                LearnedPattern(
                    pattern_id=pattern_id,
                    task_type=mode,
                    stack=None,
                    recommended_template=template_desc,
                    recommended_agents=list(best_combo),
                    confidence=round(confidence, 4),
                    sample_size=len(group),
                    success_rate=round(success_rate, 4),
                    avg_token_cost=avg_tokens,
                    evidence=[r.task_id for r in group],
                    created_at=now,
                    updated_at=now,
                )
            )

        patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns

    def refresh(
        self,
        min_sample_size: int = 5,
        min_confidence: float = 0.7,
    ) -> list[LearnedPattern]:
        """Re-analyse the usage log and write results to ``learned-patterns.json``.

        Accepts the same threshold parameters as :meth:`analyze` so callers can
        control which patterns are persisted.  Patterns are written even when
        the result list is empty (the file will contain ``[]``), which clears
        stale data from a previous run.

        Returns the freshly computed list of patterns.
        """
        patterns = self.analyze(
            min_sample_size=min_sample_size,
            min_confidence=min_confidence,
        )
        self._write_patterns(patterns)
        return patterns

    def load_patterns(self) -> list[LearnedPattern]:
        """Read patterns from ``learned-patterns.json``.

        Returns an empty list if the file does not exist or contains no
        recognisable records.
        """
        if not self._patterns_path.exists():
            return []

        try:
            raw = json.loads(self._patterns_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(raw, list):
            return []

        results: list[LearnedPattern] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                results.append(LearnedPattern.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def get_patterns_for_task(
        self,
        task_type: str,
        stack: str | None = None,
    ) -> list[LearnedPattern]:
        """Return stored patterns matching *task_type* and optionally *stack*.

        Matching rules:
        - task_type must equal the pattern's task_type (exact, case-sensitive).
        - If *stack* is provided, only patterns whose stack is ``None`` (any
          stack) or exactly equal to *stack* are included.
        - If *stack* is ``None``, all patterns for the task_type are returned.

        Returns patterns sorted by confidence descending.
        """
        patterns = self.load_patterns()
        matches = [
            p
            for p in patterns
            if p.task_type == task_type
            and (stack is None or p.stack is None or p.stack == stack)
        ]
        matches.sort(key=lambda p: p.confidence, reverse=True)
        return matches

    def recommend_sequencing(
        self,
        task_type: str,
    ) -> tuple[list[str], float] | None:
        """Return the optimal agent sequence and confidence for a task type.

        Examines stored patterns matching *task_type* and returns the
        recommended agent list from the highest-confidence pattern, along
        with the pattern's confidence score.

        Returns ``None`` if no matching pattern is found.
        """
        patterns = self.get_patterns_for_task(task_type)
        if not patterns:
            return None

        best = patterns[0]  # already sorted by confidence desc
        return best.recommended_agents, best.confidence

    def knowledge_gaps_for(
        self,
        agent_name: str,
        task_type: str | None = None,
    ) -> list[KnowledgeGapRecord]:
        """Return prior knowledge gap records matching *agent_name* and optionally *task_type*.

        Reads all retrospective JSON sidecar files from
        ``<team_context_root>/retrospectives/``.  Each file contains a
        ``knowledge_gaps`` list whose entries are deserialized as
        :class:`~agent_baton.models.knowledge.KnowledgeGapRecord`.

        Filtering rules:

        - Only records whose ``agent_name`` matches *agent_name* are included.
        - If *task_type* is provided, only records whose ``task_type`` matches
          are included (records with ``task_type=None`` are excluded when a
          filter is given).

        Deduplication is performed on ``description`` — the first occurrence
        wins (preserving the most recent file's record, since files are
        iterated in sorted — i.e. oldest-first — order, so later duplicates
        are dropped).  The returned list is sorted by frequency (most-seen
        descriptions first), then alphabetically on description for stability.

        Returns:
            Deduplicated list of gap records, possibly empty.
        """
        retros_dir = self._root / "retrospectives"
        if not retros_dir.is_dir():
            return []

        description_counts: Counter[str] = Counter()
        # Map description → first-seen record (from most recent file, so
        # we iterate newest-first and keep the first occurrence).
        gap_by_description: dict[str, KnowledgeGapRecord] = {}

        json_files = sorted(retros_dir.glob("*.json"), reverse=True)  # newest first
        for path in json_files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            raw_gaps = raw.get("knowledge_gaps", [])
            if not isinstance(raw_gaps, list):
                continue

            for entry in raw_gaps:
                if not isinstance(entry, dict):
                    continue
                try:
                    record = KnowledgeGapRecord.from_dict(entry)
                except (KeyError, TypeError, ValueError):
                    continue

                # Filter by agent_name
                if record.agent_name != agent_name:
                    continue

                # Filter by task_type when requested
                if task_type is not None and record.task_type != task_type:
                    continue

                description_counts[record.description] += 1
                # Keep the first occurrence (newest file, since we iterate newest-first).
                if record.description not in gap_by_description:
                    gap_by_description[record.description] = record

        # Sort by frequency descending, then description for determinism.
        sorted_descriptions = sorted(
            gap_by_description.keys(),
            key=lambda d: (-description_counts[d], d),
        )
        return [gap_by_description[d] for d in sorted_descriptions]

    def generate_report(self) -> str:
        """Return a Markdown report summarising all stored patterns.

        Each pattern section includes task type, stack affinity, confidence
        bar, success rate, sample size, average token cost, recommended
        template description, recommended agent list, and evidence task IDs.

        Returns:
            A complete Markdown document.  Returns a placeholder message
            directing the user to run ``baton patterns --refresh`` if no
            patterns have been computed yet.
        """
        patterns = self.load_patterns()

        if not patterns:
            return "# Learned Patterns\n\nNo patterns found. Run `baton patterns --refresh` to analyse the usage log.\n"

        lines: list[str] = [
            "# Learned Patterns",
            "",
            f"*{len(patterns)} pattern(s) derived from usage log.*",
            "",
        ]

        for p in patterns:
            confidence_bar = _confidence_bar(p.confidence)
            lines += [
                f"## {p.pattern_id}",
                "",
                f"**Task type:** {p.task_type}",
                f"**Stack:** {p.stack or 'any'}",
                f"**Confidence:** {p.confidence:.0%} {confidence_bar}",
                f"**Success rate:** {p.success_rate:.0%}  |  "
                f"**Sample size:** {p.sample_size}  |  "
                f"**Avg tokens:** {p.avg_token_cost:,}",
                "",
                f"**Template:** {p.recommended_template}",
                "",
                f"**Recommended agents:** {', '.join(p.recommended_agents) or '(none)'}",
                "",
                f"**Evidence tasks ({len(p.evidence)}):** "
                + (", ".join(p.evidence[:5]) + (" …" if len(p.evidence) > 5 else "")),
                "",
                f"*Updated: {p.updated_at}*",
                "",
                "---",
                "",
            ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Team pattern analysis
    # ------------------------------------------------------------------

    def analyze_team_patterns(
        self,
        min_sample_size: int = 3,
        min_confidence: float = 0.5,
    ) -> list[TeamPattern]:
        """Analyse usage records grouped by team composition.

        Groups records by canonical agent combination (sorted tuple of
        agent names) rather than sequencing_mode.  This identifies which
        agent *teams* are most effective, enabling team-level cost
        prediction and composition recommendations.

        Uses the same confidence formula as solo pattern analysis::

            confidence = min(1.0, (sample_size / 15) * success_rate)

        Args:
            min_sample_size: Groups with fewer records are excluded.
            min_confidence: Patterns below this threshold are excluded.

        Returns:
            List of :class:`TeamPattern` sorted by confidence descending.
        """
        logger = UsageLogger(self._log_path)
        records = logger.read_all()

        if not records:
            return []

        # Group records by canonical agent combination
        groups: dict[tuple[str, ...], list[TaskUsageRecord]] = {}
        for rec in records:
            combo = _agent_combo_key(rec)
            if len(combo) < 2:
                # Solo agent — not a team pattern
                continue
            groups.setdefault(combo, []).append(rec)

        now = _now_iso()
        patterns: list[TeamPattern] = []
        counter = 0

        for combo, group in groups.items():
            if len(group) < min_sample_size:
                continue

            success_tasks = [r for r in group if r.outcome == "SHIP"]
            success_rate = len(success_tasks) / len(group)

            confidence = min(
                1.0,
                (len(group) / self._CONFIDENCE_CALIBRATION) * success_rate,
            )

            if confidence < min_confidence:
                continue

            # Collect task types where this team was used
            task_types = sorted({
                r.sequencing_mode or "unknown"
                for r in group
            })

            # Avg tokens from successful tasks, falling back to all
            token_source = success_tasks if success_tasks else group
            avg_tokens = (
                sum(_total_tokens(r) for r in token_source) // len(token_source)
                if token_source
                else 0
            )

            counter += 1
            combo_slug = "-".join(combo)[:40]
            pattern_id = f"team-{combo_slug}-{counter:03d}"

            patterns.append(
                TeamPattern(
                    pattern_id=pattern_id,
                    agents=list(combo),
                    task_types=task_types,
                    success_rate=round(success_rate, 4),
                    sample_size=len(group),
                    avg_token_cost=avg_tokens,
                    confidence=round(confidence, 4),
                    created_at=now,
                    updated_at=now,
                )
            )

        patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns

    def refresh_team_patterns(
        self,
        min_sample_size: int = 3,
        min_confidence: float = 0.5,
    ) -> list[TeamPattern]:
        """Re-analyse usage log and write team patterns to ``team-patterns.json``.

        Returns the freshly computed list of team patterns.
        """
        patterns = self.analyze_team_patterns(
            min_sample_size=min_sample_size,
            min_confidence=min_confidence,
        )
        self._write_team_patterns(patterns)
        return patterns

    def load_team_patterns(self) -> list[TeamPattern]:
        """Read team patterns from ``team-patterns.json``."""
        path = self._root / _TEAM_PATTERNS_FILE
        if not path.exists():
            return []

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(raw, list):
            return []

        results: list[TeamPattern] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                results.append(TeamPattern.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return results

    def get_team_cost_estimate(
        self,
        agents: list[str],
    ) -> int | None:
        """Return estimated token cost for a given team composition.

        Looks up stored team patterns matching the canonical agent
        combination.  Returns the ``avg_token_cost`` from the
        highest-confidence matching pattern, or ``None`` if no match.

        Args:
            agents: List of agent names (order doesn't matter).

        Returns:
            Estimated token cost, or ``None`` if no data available.
        """
        combo = tuple(sorted(agents))
        patterns = self.load_team_patterns()
        for p in patterns:  # already sorted by confidence desc
            if tuple(sorted(p.agents)) == combo:
                return p.avg_token_cost
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_patterns(self, patterns: list[LearnedPattern]) -> None:
        self._patterns_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [p.to_dict() for p in patterns],
            indent=2,
            ensure_ascii=False,
        )
        self._patterns_path.write_text(payload + "\n", encoding="utf-8")

    def _write_team_patterns(self, patterns: list[TeamPattern]) -> None:
        path = self._root / _TEAM_PATTERNS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [p.to_dict() for p in patterns],
            indent=2,
            ensure_ascii=False,
        )
        path.write_text(payload + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _build_template_description(
    mode: str,
    agents: tuple[str, ...],
    avg_retries: float,
    avg_gate_rate: float | None,
) -> str:
    """Construct a human-readable template description string."""
    parts = [f"{mode} workflow"]
    if agents:
        parts.append(f"with {len(agents)} agent(s)")
    if avg_retries < 0.5:
        parts.append("low retry rate")
    elif avg_retries < 2.0:
        parts.append("moderate retry rate")
    else:
        parts.append("high retry rate")
    if avg_gate_rate is not None:
        parts.append(f"{avg_gate_rate:.0%} gate pass rate")
    return "; ".join(parts)


def _confidence_bar(confidence: float, width: int = 10) -> str:
    """ASCII confidence bar, e.g. '[========  ]'."""
    filled = round(confidence * width)
    return "[" + "=" * filled + " " * (width - filled) + "]"
