"""Tests for agent_baton.core.federate.rubric — deterministic spec-quality check.

4 spec-quality cases:
  1. well_specified  — all five criteria satisfied; score == 100; missing empty.
  2. vague_spec      — none of the criteria satisfied; score == 0; all 5 missing.
  3. partial_spec    — some criteria satisfied; intermediate score; some missing.
  4. enrich_integration — enrich() includes spec_quality in returned EnrichmentData.
"""
from __future__ import annotations

from agent_baton.core.federate.rubric import (
    SpecQualityReport,
    check_spec_quality,
)


# ---------------------------------------------------------------------------
# 1. Well-specified spec scores high with no missing elements
# ---------------------------------------------------------------------------


def test_well_specified_scores_100() -> None:
    """A spec with all five criteria present scores 100 and has no missing."""
    title = "Add rate-limiting middleware to /api/v1/auth endpoints"
    body = (
        "## Acceptance criteria\n"
        "- All requests to `/api/v1/auth/*` are limited to 10 req/min per IP.\n"
        "- Exceeding the limit returns HTTP 429 with a `Retry-After` header.\n"
        "- Must pass the existing `tests/api/test_auth_rate_limit.py` test suite.\n\n"
        "## Scope\n"
        "In scope: `agent_baton/api/middleware/rate_limit.py`.\n"
        "Out of scope: per-user rate limiting, dashboard changes.\n\n"
        "## Constraints\n"
        "- Must use the `slowapi` library (already in `pyproject.toml`).\n"
        "- No breaking change to the existing `/api/v1/auth/token` route shape.\n"
        "- Performance: adds < 2 ms overhead per request.\n\n"
        "## Artifacts\n"
        "New file: `agent_baton/api/middleware/rate_limit.py`.\n"
        "Updated: `agent_baton/api/server.py` to register the middleware.\n"
        "Run with: `--limit 10` flag via `baton execute start`.\n"
    )
    report = check_spec_quality(title, body)
    assert isinstance(report, SpecQualityReport)
    assert report.score == 100, f"expected 100, got {report.score}; missing={report.missing}"
    assert report.missing == [], f"expected no missing; got {report.missing}"


# ---------------------------------------------------------------------------
# 2. Vague spec scores 0 with all five elements missing
# ---------------------------------------------------------------------------


def test_vague_spec_flags_all_missing() -> None:
    """A single-word body with a generic title scores 0 and flags all elements."""
    title = "fix"
    body = "update"  # 1 word, no heuristics satisfied
    report = check_spec_quality(title, body)
    assert report.score == 0, f"expected 0, got {report.score}"
    # All 5 criteria absent → 5 items in missing
    assert len(report.missing) == 5, (
        f"expected 5 missing elements, got {len(report.missing)}: {report.missing}"
    )


# ---------------------------------------------------------------------------
# 3. Partial spec flags some elements
# ---------------------------------------------------------------------------


def test_partial_spec_flags_some_elements() -> None:
    """A spec with verification + artifacts but no scope/constraints/size flags 3."""
    title = "Implement caching"
    body = (
        "## Acceptance criteria\n"
        "- `GET /api/v1/plans` must respond in < 100 ms on a warm cache.\n"
        "- Must pass `tests/test_cache.py`.\n"
        # no scope, no constraint keywords, body < 40 words
    )
    report = check_spec_quality(title, body)
    # verification (30) + artifacts (20) = 50; scope, constraints, size missing
    assert report.score == 50, f"expected 50, got {report.score}"
    missing_labels = " ".join(report.missing).lower()
    assert "scope" in missing_labels
    assert "constraint" in missing_labels
    assert "size" in missing_labels or "improve" in missing_labels


# ---------------------------------------------------------------------------
# 4. enrich() integration — EnrichmentData carries spec_quality
# ---------------------------------------------------------------------------


def test_enrich_includes_spec_quality() -> None:
    """enrich() returns EnrichmentData with a non-None spec_quality dict."""
    from agent_baton.core.federate.enrich import enrich

    data = enrich(
        title="Add spec-quality rubric to the federation queue",
        body=(
            "## Acceptance criteria\n"
            "- `check_spec_quality` scores a well-specified spec at ≥ 80.\n"
            "- Empty body scores 0.\n\n"
            "## Scope\n"
            "In scope: `agent_baton/core/federate/rubric.py` only.\n"
            "Out of scope: LLM-based scoring.\n\n"
            "## Constraints\n"
            "- No external dependencies (pure stdlib + regex).\n"
            "- Must not call `ANTHROPIC_API_KEY`.\n\n"
            "## Artifacts\n"
            "New: `agent_baton/core/federate/rubric.py`.\n"
            "Updated: `agent_baton/core/federate/enrich.py` to call the rubric.\n"
        ),
    )
    assert data.spec_quality is not None, "enrich() should populate spec_quality"
    assert isinstance(data.spec_quality, dict)
    assert "score" in data.spec_quality
    assert "missing" in data.spec_quality
    assert "notes" in data.spec_quality
    assert isinstance(data.spec_quality["score"], int)
    assert 0 <= data.spec_quality["score"] <= 100
