"""Spec Draft models for Phase I Spec Federation MVP.

``SpecDraft`` tracks a team-submitted spec through its lifecycle:
submitted → enriched → approved | bounced → (bounced→submitted) | fired.

``EnrichmentData`` holds the auto-enrichment results produced by
``core/federate/enrich.py``: risk classification, required reviewers,
and cost forecast.

``ReviewData`` holds the outcome of the architect review: either an
approval actor/timestamp pair or a bounce with feedback text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# Valid status values (ordered lifecycle)
SpecDraftStatus = Literal["submitted", "enriched", "approved", "bounced", "fired"]

# Valid lifecycle transitions
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "submitted": frozenset({"enriched"}),
    "enriched":  frozenset({"approved", "bounced"}),
    "approved":  frozenset({"fired"}),
    "bounced":   frozenset({"submitted"}),
    "fired":     frozenset(),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EnrichmentData(BaseModel):
    """Auto-enrichment output for a spec draft.

    Attributes:
        risk_level: Classified risk tier (LOW/MEDIUM/HIGH/CRITICAL).
        guardrail_preset: Active guardrail preset name.
        required_reviewers: Agent names required by policy require_agent rules.
        signals_found: Signal keywords found during classification.
        confidence: Classification confidence (high/low).
        est_usd_low: Low-band cost estimate in USD.
        est_usd_mid: Mid-point cost estimate in USD.
        est_usd_high: High-band cost estimate in USD.
        cost_confidence: Either ``"high"`` (history-calibrated) or ``"default"``.
        breakdown: Per-agent cost breakdown rows.
        enriched_at: ISO-8601 timestamp of enrichment.
        spec_quality: Deterministic spec-quality rubric report (score, missing,
            notes).  ``None`` for enrichments produced before this field was
            added (backward-compatible default).
    """

    risk_level: str = "LOW"
    guardrail_preset: str = "Standard Development"
    required_reviewers: list[str] = Field(default_factory=list)
    signals_found: list[str] = Field(default_factory=list)
    confidence: str = "high"
    est_usd_low: float = 0.0
    est_usd_mid: float = 0.0
    est_usd_high: float = 0.0
    cost_confidence: str = "default"
    breakdown: list[dict[str, Any]] = Field(default_factory=list)
    enriched_at: str = Field(default_factory=_now_iso)
    spec_quality: dict[str, Any] | None = None


class ReviewData(BaseModel):
    """Outcome of an architect review.

    Attributes:
        action: Either ``"approved"`` or ``"bounced"``.
        actor: User ID of the reviewer.
        feedback: Required when ``action == "bounced"``; empty string otherwise.
        reviewed_at: ISO-8601 timestamp of the review decision.
    """

    action: Literal["approved", "bounced"]
    actor: str
    feedback: str = ""
    reviewed_at: str = Field(default_factory=_now_iso)


class SpecDraft(BaseModel):
    """A team-submitted spec awaiting enrichment and architect approval.

    Attributes:
        id: UUID primary key.
        title: Short human-readable title.
        body: Full markdown spec body.
        source: Origin of the draft (``"manual"``, ``"github"``, ``"ado"``).
        source_ref: External reference (issue URL, ADO work item ID, etc.).
        submitted_by: User ID of the submitter.
        submitted_at: ISO-8601 submission timestamp.
        status: Lifecycle state.
        enrichment: Populated after auto-enrichment; ``None`` before.
        review: Populated after architect review; ``None`` before.
        task_id: Set to the fired execution task ID on ``fired`` status.
        updated_at: ISO-8601 last-updated timestamp.
    """

    id: str
    title: str
    body: str = ""
    source: str = "manual"
    source_ref: str = ""
    submitted_by: str = "local-user"
    submitted_at: str = Field(default_factory=_now_iso)
    status: str = "submitted"
    enrichment: EnrichmentData | None = None
    review: ReviewData | None = None
    task_id: str | None = None
    updated_at: str = Field(default_factory=_now_iso)

    def valid_next_statuses(self) -> frozenset[str]:
        """Return the set of statuses that can follow the current one."""
        return _VALID_TRANSITIONS.get(self.status, frozenset())

    def can_transition_to(self, new_status: str) -> bool:
        """Return True if the transition to *new_status* is valid."""
        return new_status in self.valid_next_statuses()
