"""Cost forecasting for ``MachinePlan`` instances.

Used by ``baton plan --dry-run`` to give developers a sub-5-second
sanity check on a plan before they commit to a multi-thousand-token
execution.

The estimator is intentionally simple: it sums per-step token allowances
(role baseline + attached knowledge ``token_estimate``) and multiplies by
the per-million blended I/O price for the step's model.

Per-agent role baselines (in tokens, completion + reasoning headroom):

    architect, code-reviewer            8_000
    auditor, security-reviewer          6_000
    backend-engineer*, frontend-engineer*, test-engineer
                                        5_000
    everything else                     4_000

Pricing (USD per million tokens, blended input/output):

    opus     30.00
    sonnet    6.00
    haiku     1.25

This module is stdlib-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_baton.models.execution import MachinePlan, PlanStep

# ---------------------------------------------------------------------------
# Pricing & baselines (constants)
# ---------------------------------------------------------------------------

#: USD per million tokens, blended input/output.
MODEL_PRICING: dict[str, float] = {
    "opus": 30.0,
    "sonnet": 6.0,
    "haiku": 1.25,
}

#: Default model when a step's model string does not match a known family.
_DEFAULT_MODEL = "sonnet"

#: Token baseline per role family.  Matched against the agent_name prefix
#: (e.g. "backend-engineer--python" matches the "backend-engineer" key).
_ROLE_BASELINE_TOKENS: dict[str, int] = {
    "architect": 8_000,
    "code-reviewer": 8_000,
    "auditor": 6_000,
    "security-reviewer": 6_000,
    "backend-engineer": 5_000,
    "frontend-engineer": 5_000,
    "test-engineer": 5_000,
}

#: Fallback baseline for any role not in ``_ROLE_BASELINE_TOKENS``.
_DEFAULT_BASELINE_TOKENS = 4_000


# ---------------------------------------------------------------------------
# Forecast result
# ---------------------------------------------------------------------------

@dataclass
class CostForecast:
    """Output of :func:`forecast_plan`.

    Attributes:
        total_tokens: Sum of all per-step token allowances.
        total_cost_usd: Sum of per-step cost in USD.
        per_step_tokens: ``(step_id, tokens)`` for every step in plan order.
        model_breakdown: ``model_name -> total_tokens`` aggregated across
            steps that used that model.  Models normalised to the
            family key (``opus`` / ``sonnet`` / ``haiku``).
    """

    total_tokens: int = 0
    total_cost_usd: float = 0.0
    per_step_tokens: list[tuple[str, int]] = field(default_factory=list)
    model_breakdown: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalise_model(model: str) -> str:
    """Map an arbitrary model string to a known pricing family key.

    Examples:
        ``"opus"`` -> ``"opus"``
        ``"claude-opus-4-7"`` -> ``"opus"``
        ``"sonnet-3.5"`` -> ``"sonnet"``
        ``"haiku"`` -> ``"haiku"``
        ``""`` / unknown -> ``"sonnet"`` (the planner default).
    """
    if not model:
        return _DEFAULT_MODEL
    lower = model.lower()
    for family in MODEL_PRICING:
        if family in lower:
            return family
    return _DEFAULT_MODEL


def role_baseline_tokens(agent_name: str) -> int:
    """Return the role token baseline for *agent_name*.

    Matches by prefix so suffixed names (``backend-engineer--python``)
    still resolve to their family baseline.
    """
    if not agent_name:
        return _DEFAULT_BASELINE_TOKENS
    lower = agent_name.lower()
    for role, baseline in _ROLE_BASELINE_TOKENS.items():
        if lower.startswith(role):
            return baseline
    return _DEFAULT_BASELINE_TOKENS


def estimate_step_tokens(step: PlanStep) -> int:
    """Estimate token consumption for *step*.

    Knowledge ``token_estimate`` values are **added** to the role
    baseline, not substituted, so heavily-knowledge-loaded steps are
    correctly inflated above the baseline.
    """
    base = role_baseline_tokens(step.agent_name)
    knowledge = sum(getattr(att, "token_estimate", 0) or 0 for att in step.knowledge)
    return base + knowledge


def step_cost_usd(tokens: int, model: str) -> float:
    """Return USD cost for *tokens* run on *model*.

    Unknown models fall back to ``sonnet`` pricing (the planner default).
    """
    family = normalise_model(model)
    rate = MODEL_PRICING.get(family, MODEL_PRICING[_DEFAULT_MODEL])
    return (tokens / 1_000_000.0) * rate


def forecast_plan(plan: MachinePlan) -> CostForecast:
    """Compute a :class:`CostForecast` for *plan*.

    Walks every step in every phase.  Team members within a step are
    treated as additional dispatches (each member contributes its own
    role baseline; team members carry no knowledge attachments of their
    own in the current model, so only the baseline is added).
    """
    forecast = CostForecast()
    for phase in plan.phases:
        for step in phase.steps:
            tokens = estimate_step_tokens(step)
            cost = step_cost_usd(tokens, step.model)
            family = normalise_model(step.model)

            forecast.per_step_tokens.append((step.step_id, tokens))
            forecast.total_tokens += tokens
            forecast.total_cost_usd += cost
            forecast.model_breakdown[family] = (
                forecast.model_breakdown.get(family, 0) + tokens
            )

            # Team members: each is an additional dispatch with its own
            # role baseline.  Use the member's own model.
            for member in step.team:
                m_tokens = role_baseline_tokens(member.agent_name)
                m_cost = step_cost_usd(m_tokens, member.model)
                m_family = normalise_model(member.model)
                forecast.per_step_tokens.append((member.member_id, m_tokens))
                forecast.total_tokens += m_tokens
                forecast.total_cost_usd += m_cost
                forecast.model_breakdown[m_family] = (
                    forecast.model_breakdown.get(m_family, 0) + m_tokens
                )

    return forecast


# ---------------------------------------------------------------------------
# Wall-clock estimation
# ---------------------------------------------------------------------------

#: Default per-agent wall-clock minutes.
_AGENT_DURATION_MIN: dict[str, int] = {
    "architect": 4,
    "backend-engineer": 6,
    "frontend-engineer": 6,
    "test-engineer": 4,
    "code-reviewer": 3,
    "auditor": 5,
    "security-reviewer": 5,
}
_DEFAULT_AGENT_DURATION_MIN = 4


def agent_duration_minutes(agent_name: str) -> int:
    """Return rough wall-clock minutes for an agent dispatch."""
    if not agent_name:
        return _DEFAULT_AGENT_DURATION_MIN
    lower = agent_name.lower()
    for role, mins in _AGENT_DURATION_MIN.items():
        if lower.startswith(role):
            return mins
    return _DEFAULT_AGENT_DURATION_MIN


def estimate_gate_seconds(command: str) -> int:
    """Heuristic wall-clock estimate (seconds) for a gate *command*.

    Anchors:
        * ``pytest --cov`` (full suite + coverage)        ~2_220 s (37 min)
        * ``pytest tests/<scoped>/`` (narrow path)              30 s
        * ``echo`` / ``true`` / no-op                            1 s
        * Unknown command (default)                            60 s
    """
    if not command:
        return 1
    cmd = command.strip().lower()
    if cmd.startswith("echo") or cmd in {"true", ":"}:
        return 1
    # Heavy: full suite with coverage
    if "pytest" in cmd and "--cov" in cmd:
        return 2_220
    # Scoped pytest: a path argument follows pytest
    if "pytest" in cmd:
        # crude: bare 'pytest' with no path is full suite
        tokens = cmd.split()
        has_path = any(
            tok.startswith("tests/") or tok.endswith(".py") or "/" in tok
            for tok in tokens[1:]
        )
        if has_path:
            return 30
        return 2_220
    return 60


def estimate_wall_clock_minutes(plan: MachinePlan) -> tuple[int, int]:
    """Return ``(agent_minutes, gate_minutes)`` for *plan*.

    Agent minutes sum every step (and each team member) using
    :func:`agent_duration_minutes`.  Gate minutes sum every phase's
    gate command using :func:`estimate_gate_seconds` (rounded up).
    """
    agent_min = 0
    gate_seconds = 0
    for phase in plan.phases:
        for step in phase.steps:
            agent_min += agent_duration_minutes(step.agent_name)
            for member in step.team:
                agent_min += agent_duration_minutes(member.agent_name)
        if phase.gate is not None:
            gate_seconds += estimate_gate_seconds(phase.gate.command)
    gate_min = (gate_seconds + 59) // 60
    return agent_min, gate_min
