"""Goal evaluator — checks completion condition at phase boundaries (G1).

Part of the ``/goal`` integration (see
``docs/internal/agent-teams-and-goal-design.md``).  The executor calls
``evaluate()`` at every phase boundary when ``plan.completion_condition``
is set; the result drives the wrap-and-refine loop:

* ``met=True``  → engine emits ``COMPLETE``.
* ``met=False`` and amend budget remains → engine calls ``amend_plan``
  with ``suggested_phases`` and continues.
* ``met=False`` and amend budget exhausted → engine emits ``FAILED``
  with reason ``"goal not met, amend budget exhausted"``.

Two evaluator strategies are available:

* ``StubGoalEvaluator`` — deterministic, no LLM.  Declares ``met`` iff
  every phase is complete AND the most-recent gate passed.  Useful for
  tests and as fallback when ``ANTHROPIC_API_KEY`` is unset.
* ``LLMGoalEvaluator`` — calls Anthropic (Haiku by default) with the
  goal text, recent step outcomes, and last gate output, and asks for
  a structured ``GoalCheck``.

``select_evaluator()`` picks the right one based on environment.

**Safety rail:** any evaluator that returns ``met=True`` while
``last_gate_passed=False`` is overridden to ``met=False``.  This prevents
an over-eager LLM from declaring success on a phase whose gate failed.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    GoalCheck,
    MachinePlan,
    PhaseStatus,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class GoalEvaluator(Protocol):
    """Strategy interface for goal evaluation at phase boundaries."""

    def evaluate(
        self,
        *,
        state: ExecutionState,
        plan: MachinePlan,
        last_gate_passed: bool,
        check_id: str,
    ) -> GoalCheck:
        """Return a ``GoalCheck`` for the current execution state.

        Args:
            state: Current ``ExecutionState`` (read-only — must not mutate).
            plan: The active ``MachinePlan`` (post-amendments).
            last_gate_passed: Whether the most recent gate passed. The
                returned ``GoalCheck.last_gate_passed`` will be coerced
                to this value, and ``met`` is forced to False when this
                is False (safety rail).
            check_id: Monotonic id to stamp on the returned record
                (e.g. ``"g3"``).
        """
        ...


# ---------------------------------------------------------------------------
# Safety rail
# ---------------------------------------------------------------------------

def _apply_safety_rail(check: GoalCheck, last_gate_passed: bool) -> GoalCheck:
    """Override ``met=True`` when the most recent gate did not pass.

    This is non-negotiable: the design (G1) requires evaluator
    agreement with the latest gate before declaring success.
    """
    check.last_gate_passed = last_gate_passed
    if check.met and not last_gate_passed:
        _log.info(
            "goal-evaluator safety rail: forcing met=False on check %s "
            "(evaluator said met=True but last_gate_passed=False)",
            check.check_id,
        )
        check.met = False
        if "last gate did not pass" not in " ".join(check.missing):
            check.missing.append("last gate did not pass — cannot confirm goal")
    return check


# ---------------------------------------------------------------------------
# Stub evaluator (deterministic, no LLM)
# ---------------------------------------------------------------------------

class StubGoalEvaluator:
    """Deterministic evaluator usable without an LLM.

    Logic:

    * ``met`` ⇔ every phase status is ``COMPLETE`` AND
      ``last_gate_passed`` is True.
    * When not met, ``missing`` enumerates phases that are not yet
      complete; no ``suggested_phases`` are produced (the stub does not
      author new work — that's the LLM evaluator's job).

    Used in tests and as fallback when ``ANTHROPIC_API_KEY`` is unset.
    """

    source = "stub"

    def evaluate(
        self,
        *,
        state: ExecutionState,
        plan: MachinePlan,
        last_gate_passed: bool,
        check_id: str,
    ) -> GoalCheck:
        condition = plan.completion_condition or ""
        incomplete = [
            f"phase {p.phase_id}: {p.name}"
            for p in plan.phases
            if PhaseStatus(p.status) != PhaseStatus.COMPLETE
        ] if hasattr(plan.phases[0] if plan.phases else None, "status") else []

        # Fall back to step-result-based check if phases lack status
        # tracking on this branch.
        if not incomplete and plan.phases:
            completed_steps = {sr.step_id for sr in state.step_results
                               if str(getattr(sr, "status", "")) in ("complete", "StepStatus.COMPLETE")}
            for phase in plan.phases:
                phase_step_ids = {s.step_id for s in phase.steps}
                if not phase_step_ids.issubset(completed_steps):
                    incomplete.append(
                        f"phase {phase.phase_id}: {phase.name} "
                        f"({len(phase_step_ids - completed_steps)} step(s) incomplete)"
                    )

        all_phases_done = len(incomplete) == 0
        met = all_phases_done and last_gate_passed

        check = GoalCheck(
            check_id=check_id,
            phase_id=plan.phases[state.current_phase].phase_id
            if 0 <= state.current_phase < len(plan.phases) else 0,
            completion_condition=condition,
            met=met,
            confidence=0.95 if met else 0.6,
            last_gate_passed=last_gate_passed,
            missing=incomplete,
            suggested_phases=[],
            reasoning=(
                "Stub evaluator: deterministic phase-completion + last-gate "
                "check. Does not author follow-up phases."
            ),
            evaluator_source=self.source,
        )
        return _apply_safety_rail(check, last_gate_passed)


# ---------------------------------------------------------------------------
# LLM evaluator (Anthropic Haiku by default)
# ---------------------------------------------------------------------------

_LLM_EVALUATOR_PROMPT = """\
You are evaluating whether a software-engineering task has met its goal.

Goal (completion condition):
{condition}

Execution summary:
- Phases planned: {n_phases}
- Steps completed: {n_steps_complete} / {n_steps_total}
- Most recent gate passed: {last_gate_passed}
- Last gate output (truncated):
{last_gate_output}

Recent step outcomes (most recent last):
{recent_outcomes}

Decide:
1. Is the goal met? Answer must be False unless the last gate passed.
2. If not met, list the gaps (1-5 short bullets).
3. If not met and you can author them, propose follow-up phases that
   would close the gaps. Each phase is a JSON object with keys
   {{phase_id, name, steps: [{{step_id, agent_name, task_description}}]}}.
   Use phase_ids starting after the highest existing one ({next_phase_id}).
   Keep proposed phases small (1-3 steps each).

Respond with ONLY a JSON object of this shape:
{{
  "met": <bool>,
  "confidence": <float 0..1>,
  "missing": [<string>, ...],
  "suggested_phases": [<phase obj>, ...],
  "reasoning": <string>
}}
"""


class LLMGoalEvaluator:
    """LLM-backed evaluator. Uses Anthropic Haiku by default.

    Lazy-imports the ``anthropic`` SDK so the module loads even when the
    dependency is not installed.  On any failure (missing API key,
    network error, malformed response) it falls back to the stub
    evaluator's verdict so the engine never deadlocks on a flaky LLM.
    """

    source = "haiku"

    def __init__(self, *, model: str = "claude-haiku-4-5") -> None:
        self.model = model
        self._stub_fallback = StubGoalEvaluator()

    def evaluate(
        self,
        *,
        state: ExecutionState,
        plan: MachinePlan,
        last_gate_passed: bool,
        check_id: str,
    ) -> GoalCheck:
        try:
            import json as _json

            import anthropic  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "LLMGoalEvaluator: anthropic SDK unavailable (%s); "
                "falling back to stub evaluator",
                exc,
            )
            return self._stub_fallback.evaluate(
                state=state, plan=plan,
                last_gate_passed=last_gate_passed, check_id=check_id,
            )

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            _log.warning(
                "LLMGoalEvaluator: ANTHROPIC_API_KEY unset; falling back "
                "to stub evaluator"
            )
            return self._stub_fallback.evaluate(
                state=state, plan=plan,
                last_gate_passed=last_gate_passed, check_id=check_id,
            )

        prompt = self._render_prompt(state, plan, last_gate_passed)
        try:
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in resp.content
                if getattr(block, "type", None) == "text"
            ).strip()
            data = _json.loads(text)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "LLMGoalEvaluator: call failed (%s); falling back to stub",
                exc,
            )
            return self._stub_fallback.evaluate(
                state=state, plan=plan,
                last_gate_passed=last_gate_passed, check_id=check_id,
            )

        check = GoalCheck(
            check_id=check_id,
            phase_id=plan.phases[state.current_phase].phase_id
            if 0 <= state.current_phase < len(plan.phases) else 0,
            completion_condition=plan.completion_condition or "",
            met=bool(data.get("met", False)),
            confidence=float(data.get("confidence", 0.5)),
            last_gate_passed=last_gate_passed,
            missing=[str(m) for m in data.get("missing", [])],
            suggested_phases=list(data.get("suggested_phases", [])),
            reasoning=str(data.get("reasoning", ""))[:2000],
            evaluator_source=self.source,
        )
        return _apply_safety_rail(check, last_gate_passed)

    def _render_prompt(
        self,
        state: ExecutionState,
        plan: MachinePlan,
        last_gate_passed: bool,
    ) -> str:
        completed = sum(
            1 for sr in state.step_results
            if str(getattr(sr, "status", "")).endswith("complete")
        )
        last_gate = state.gate_results[-1] if state.gate_results else None
        last_gate_output = (last_gate.output[:1500] if last_gate else "(no gate run yet)")
        recent = "\n".join(
            f"- {sr.step_id} ({sr.agent_name}): "
            f"{(getattr(sr, 'outcome', '') or '')[:240]}"
            for sr in state.step_results[-5:]
        ) or "(no step results yet)"
        next_phase_id = max(
            (p.phase_id for p in plan.phases), default=0
        ) + 1
        return _LLM_EVALUATOR_PROMPT.format(
            condition=plan.completion_condition or "",
            n_phases=len(plan.phases),
            n_steps_complete=completed,
            n_steps_total=plan.total_steps,
            last_gate_passed=last_gate_passed,
            last_gate_output=last_gate_output,
            recent_outcomes=recent,
            next_phase_id=next_phase_id,
        )


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

def select_evaluator() -> GoalEvaluator:
    """Pick an evaluator based on environment.

    * ``BATON_GOAL_EVALUATOR=stub`` → ``StubGoalEvaluator``.
    * ``BATON_GOAL_EVALUATOR=haiku`` (default) and
      ``ANTHROPIC_API_KEY`` set → ``LLMGoalEvaluator``.
    * Otherwise → ``StubGoalEvaluator``.
    """
    mode = os.environ.get("BATON_GOAL_EVALUATOR", "haiku").strip().lower()
    if mode == "stub":
        return StubGoalEvaluator()
    if mode in ("haiku", "llm", "opus") and os.environ.get("ANTHROPIC_API_KEY"):
        model = {
            "haiku": "claude-haiku-4-5",
            "llm": "claude-haiku-4-5",
            "opus": "claude-opus-4-8",
        }[mode]
        return LLMGoalEvaluator(model=model)
    return StubGoalEvaluator()


__all__ = [
    "GoalEvaluator",
    "StubGoalEvaluator",
    "LLMGoalEvaluator",
    "select_evaluator",
]
