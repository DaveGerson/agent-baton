"""BudgetTuner — analyse historical token usage and recommend budget tier changes.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.usage import TaskUsageRecord

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

# Each tier is a (lower_inclusive, upper_inclusive) token boundary.
# "full" has no upper bound; we use sys.maxsize as a sentinel.
_TIER_LEAN = "lean"
_TIER_STANDARD = "standard"
_TIER_FULL = "full"

_TIER_LOWER: dict[str, int] = {
    _TIER_LEAN: 0,
    _TIER_STANDARD: 50_001,
    _TIER_FULL: 500_001,
}
_TIER_UPPER: dict[str, int] = {
    _TIER_LEAN: 50_000,
    _TIER_STANDARD: 500_000,
    _TIER_FULL: 10_000_000,   # effectively unbounded
}
# Midpoint used for potential-savings calculation on downgrades.
_TIER_MIDPOINT: dict[str, int] = {
    _TIER_LEAN: 25_000,
    _TIER_STANDARD: 275_000,
    _TIER_FULL: 750_000,
}

_TIERS_ORDERED = [_TIER_LEAN, _TIER_STANDARD, _TIER_FULL]

_RECOMMENDATIONS_FILE = "budget-recommendations.json"
_DEFAULT_TEAM_CONTEXT = Path(".claude/team-context")

# Minimum records in a group before we generate a recommendation.
_MIN_SAMPLE = 3


# ---------------------------------------------------------------------------
# Statistics helpers (no external deps)
# ---------------------------------------------------------------------------

def _total_tokens(record: TaskUsageRecord) -> int:
    return sum(a.estimated_tokens for a in record.agents_used)


def _median(values: list[int]) -> int:
    """Return the median of a non-empty sorted-or-unsorted list."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) // 2


def _p95(values: list[int]) -> int:
    """Return the 95th-percentile value (nearest-rank method)."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    # Nearest-rank: index = ceil(p * n) - 1, clamped to [0, n-1]
    idx = max(0, min(n - 1, int(0.95 * n + 0.5) - 1))
    return sorted_vals[idx]


def _tier_for_tokens(tokens: int) -> str:
    """Map a token count to the tier whose range contains it."""
    if tokens <= _TIER_UPPER[_TIER_LEAN]:
        return _TIER_LEAN
    if tokens <= _TIER_UPPER[_TIER_STANDARD]:
        return _TIER_STANDARD
    return _TIER_FULL


# ---------------------------------------------------------------------------
# BudgetTuner
# ---------------------------------------------------------------------------

class BudgetTuner:
    """Analyse historical token usage and recommend budget tier adjustments.

    Usage::

        tuner = BudgetTuner()
        recs = tuner.analyze()
        print(tuner.recommend())
        tuner.save_recommendations()

    The tuner reads :class:`~agent_baton.models.usage.TaskUsageRecord` objects
    from the JSONL usage log written by
    :class:`~agent_baton.core.observe.usage.UsageLogger`, groups them by
    ``sequencing_mode``, and applies tier-boundary rules to produce
    :class:`~agent_baton.models.budget.BudgetRecommendation` objects.

    Tier boundaries
    ---------------
    - **Lean**: 0 – 50,000 tokens
    - **Standard**: 50,001 – 500,000 tokens
    - **Full**: 500,001+ tokens

    Recommendation rules (applied per group with >= 3 records)
    -----------------------------------------------------------
    - *Upgrade*: median > upper_bound_of_current_tier * 0.8
    - *Downgrade*: p95 < lower_bound_of_current_tier

    Confidence formula::

        confidence = min(1.0, sample_size / 10)
    """

    def __init__(self, team_context_root: Path | None = None) -> None:
        self._root = team_context_root or _DEFAULT_TEAM_CONTEXT
        self._log_path = self._root / "usage-log.jsonl"
        self._recs_path = self._root / _RECOMMENDATIONS_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> list[BudgetRecommendation]:
        """Read the usage log and return budget recommendations.

        Groups records by ``sequencing_mode``.  Groups with fewer than
        :data:`_MIN_SAMPLE` records are skipped.  Only groups where the
        recommended tier differs from the current (median-based) tier produce
        a recommendation entry.

        Returns:
            Recommendations sorted by task_type alphabetically.
        """
        logger = UsageLogger(self._log_path)
        records = logger.read_all()

        if not records:
            return []

        # Group by sequencing_mode
        groups: dict[str, list[TaskUsageRecord]] = {}
        for rec in records:
            mode = rec.sequencing_mode or "unknown"
            groups.setdefault(mode, []).append(rec)

        recommendations: list[BudgetRecommendation] = []

        for mode, group in sorted(groups.items()):
            if len(group) < _MIN_SAMPLE:
                continue

            token_totals = [_total_tokens(r) for r in group]
            avg_tokens = int(sum(token_totals) / len(token_totals))
            median_tokens = _median(token_totals)
            p95_tokens = _p95(token_totals)
            sample_size = len(group)
            confidence = round(min(1.0, sample_size / 10), 4)

            current_tier = _tier_for_tokens(median_tokens)
            recommended_tier, reason = _determine_recommendation(
                current_tier, median_tokens, p95_tokens
            )

            if recommended_tier == current_tier:
                continue  # No change needed

            potential_savings = _compute_savings(
                current_tier, recommended_tier, avg_tokens
            )

            recommendations.append(
                BudgetRecommendation(
                    task_type=mode,
                    current_tier=current_tier,
                    recommended_tier=recommended_tier,
                    reason=reason,
                    avg_tokens_used=avg_tokens,
                    median_tokens_used=median_tokens,
                    p95_tokens_used=p95_tokens,
                    sample_size=sample_size,
                    confidence=confidence,
                    potential_savings=potential_savings,
                )
            )

        return recommendations

    def recommend(self) -> str:
        """Return a human-readable markdown report of budget recommendations.

        Runs :meth:`analyze` and formats the results as a markdown document.
        Returns a brief "no recommendations" message when everything is already
        well-sized.
        """
        recs = self.analyze()

        if not recs:
            return (
                "# Budget Recommendations\n\n"
                "No budget adjustments needed — all task types are within their "
                "expected tier boundaries.\n"
            )

        lines: list[str] = [
            "# Budget Recommendations",
            "",
            f"*{len(recs)} recommendation(s) based on usage log analysis.*",
            "",
        ]

        for rec in recs:
            arrow = "upgrade" if _tier_index(rec.recommended_tier) > _tier_index(rec.current_tier) else "downgrade"
            lines += [
                f"## {rec.task_type}",
                "",
                f"**Action:** {arrow.capitalize()} `{rec.current_tier}` -> `{rec.recommended_tier}`",
                f"**Reason:** {rec.reason}",
                f"**Confidence:** {rec.confidence:.0%}  |  "
                f"**Sample size:** {rec.sample_size}",
                f"**Avg tokens:** {rec.avg_tokens_used:,}  |  "
                f"**Median:** {rec.median_tokens_used:,}  |  "
                f"**p95:** {rec.p95_tokens_used:,}",
            ]
            if rec.potential_savings > 0:
                lines.append(
                    f"**Estimated savings:** {rec.potential_savings:,} tokens/task"
                )
            lines += ["", "---", ""]

        return "\n".join(lines)

    def save_recommendations(self) -> Path:
        """Analyse and write recommendations to ``budget-recommendations.json``.

        Creates the parent directory if it does not yet exist.

        Returns:
            The path to the written file.
        """
        recs = self.analyze()
        self._recs_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [r.to_dict() for r in recs],
            indent=2,
            ensure_ascii=False,
        )
        self._recs_path.write_text(payload + "\n", encoding="utf-8")
        return self._recs_path

    def load_recommendations(self) -> list[BudgetRecommendation] | None:
        """Read previously saved recommendations from disk.

        Returns ``None`` if the file does not exist.  Returns an empty list
        if the file exists but contains no valid records (e.g. ``[]`` or
        malformed JSON).
        """
        if not self._recs_path.exists():
            return None

        try:
            raw = json.loads(self._recs_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(raw, list):
            return []

        results: list[BudgetRecommendation] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                results.append(BudgetRecommendation.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return results


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _tier_index(tier: str) -> int:
    """Return the ordinal position of a tier name (lean=0, standard=1, full=2)."""
    return _TIERS_ORDERED.index(tier)


def _determine_recommendation(
    current_tier: str,
    median_tokens: int,
    p95_tokens: int,
) -> tuple[str, str]:
    """Apply upgrade/downgrade rules and return (recommended_tier, reason).

    Rules (in order of precedence):
    1. Upgrade: median > upper_bound * 0.8
    2. Downgrade: p95 < lower_bound of current tier
    """
    upper = _TIER_UPPER[current_tier]
    lower = _TIER_LOWER[current_tier]

    # Upgrade check: median usage exceeds 80% of the tier's upper bound
    if median_tokens > upper * 0.8:
        tier_idx = _tier_index(current_tier)
        if tier_idx < len(_TIERS_ORDERED) - 1:
            next_tier = _TIERS_ORDERED[tier_idx + 1]
            reason = (
                f"Median usage ({median_tokens:,} tokens) exceeds 80% of the "
                f"{current_tier} tier ceiling ({int(upper * 0.8):,} tokens). "
                f"Consider upgrading to {next_tier}."
            )
            return next_tier, reason

    # Downgrade check: p95 falls entirely below the current tier's lower bound
    # (only applicable to standard and full — lean has no lower tier)
    tier_idx = _tier_index(current_tier)
    if tier_idx > 0 and p95_tokens < lower:
        prev_tier = _TIERS_ORDERED[tier_idx - 1]
        reason = (
            f"Even the 95th-percentile usage ({p95_tokens:,} tokens) is below "
            f"the {current_tier} tier floor ({lower:,} tokens). "
            f"You can safely downgrade to {prev_tier}."
        )
        return prev_tier, reason

    return current_tier, ""


def _compute_savings(
    current_tier: str,
    recommended_tier: str,
    avg_tokens: int,
) -> int:
    """Estimate tokens saved per task for downgrade recommendations.

    For upgrades (where the caller would be allocating *more* budget),
    potential_savings is 0 since we are not saving anything.

    For downgrades, savings = max(0, avg_tokens - midpoint_of_recommended_tier).
    """
    if _tier_index(recommended_tier) >= _tier_index(current_tier):
        return 0
    midpoint = _TIER_MIDPOINT[recommended_tier]
    return max(0, avg_tokens - midpoint)
