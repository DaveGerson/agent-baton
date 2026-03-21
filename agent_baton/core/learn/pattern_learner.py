"""PatternLearner — derive recurring orchestration patterns from usage logs.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.usage import TaskUsageRecord
from agent_baton.models.pattern import LearnedPattern

_PATTERNS_FILE = "learned-patterns.json"
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
        self._root = team_context_root or _DEFAULT_TEAM_CONTEXT
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

    def generate_report(self) -> str:
        """Return a markdown report summarising all stored patterns."""
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
