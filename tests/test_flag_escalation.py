"""Tests for the Flag Escalation System (Layer 2).

Coverage:
1. Flag Parsing
   - Well-formed DESIGN_CHOICE block parsed correctly (all fields)
   - Well-formed CONFLICT block parsed correctly (parties list)
   - Missing CONFIDENCE/RECOMMENDATION -> defaults applied
   - Malformed flag (missing OPTION lines) -> partial parse, not crash
   - No flag in output -> None returned
   - Flag buried in middle of long output -> still found
   - Multiple OPTION_[A-Z] lines collected in alphabetical order
   - Options with letters out of order in text -> sorted by letter
   - OPTION lines present but no DESIGN_CHOICE -> not parsed as DesignFlag

2. Routing
   - design-choice routes to architect
   - conflict routes to architect
   - Unknown flag type routes to architect (fallback)
   - Routing table contains all expected entries

3. to_consultation_description()
   - DesignFlag produces required markdown sections
   - DesignFlag with no options produces "(no options provided)" marker
   - DesignFlag with no recommendation produces "(none provided)" marker
   - DesignFlag with long partial_outcome truncated to _CONTEXT_EXCERPT_CHARS
   - DesignFlag excerpt lines are blockquoted
   - ConflictFlag produces required markdown sections including Parties + Conflict Detail
   - ConflictFlag with no parties produces "(no parties identified)" marker
   - ConflictFlag with no conflict_detail produces "(no detail provided)" marker
   - step_id and agent_name appear in both flag descriptions

4. PlanAmendment.metadata
   - Round-trips through to_dict / from_dict
   - Backward compat: missing metadata key defaults to empty dict

5. provide_interact_input source parameter
   - source="auto-agent" recorded on InteractionTurn
   - source defaults to "human" when not supplied

6. Escalation Chain (engine integration)
   - Flag in step output -> original step marked interrupted
   - Flag in step output -> consulting step inserted into same phase
   - Flag in step output -> amendment recorded with trigger="flag:design-choice"
   - Flag -> Tier 1 FLAG_RESOLVED -> ResolvedDecision recorded
   - Flag -> Tier 1 FLAG_RESOLVED -> re-dispatch step inserted
   - Flag -> Tier 1 FLAG_RESOLVED -> bead NOT explicitly tested (implementation detail)
   - Flag -> Tier 1 ESCALATE_TO_INTERACT -> consulting PlanStep.interactive=True
   - Flag -> Tier 1 ESCALATE_TO_INTERACT -> consulting StepResult status="interacting"
   - Flag -> Tier 1 KNOWLEDGE_GAP -> consulting step does NOT re-enter Tier 1 (anti-loop)

7. Anti-Lock
   - Consulting step output with DESIGN_CHOICE does NOT trigger new consultation
   - Consulting step output with CONFLICT does NOT trigger new consultation
   - Two flags in same phase -> two independent consultation chains
   - Output with both flag + KNOWLEDGE_GAP -> flag takes priority (early return)

8. Resolution Markers
   - parse_flag_resolution returns decision text from FLAG_RESOLVED: line
   - parse_flag_resolution returns None when absent
   - has_escalate_to_interact returns True when marker present
   - has_escalate_to_interact returns False when absent
   - FLAG_RESOLVED: case-insensitive match

9. Anti-Over-Reliance (observability)
   - Amendments with trigger="flag:design-choice" are countable per session
   - Amendment metadata carries original_step_id and consulting_step_id

10. End-to-End Integration
    - Full chain: developer flag -> architect consultation -> FLAG_RESOLVED -> re-dispatch
    - Conflict flag triggers consulting step for architect
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.flags import (
    DesignFlag,
    ConflictFlag,
    _FLAG_ROUTING,
    _FLAG_ROUTING_DEFAULT,
    _CONTEXT_EXCERPT_CHARS,
    has_escalate_to_interact,
    parse_conflict_flag,
    parse_design_flag,
    parse_flag_resolution,
)
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Shared plan/engine factories  (same pattern as test_interactive_steps.py)
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    step_type: str = "developing",
    interactive: bool = False,
    max_turns: int = 10,
    depends_on: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        step_type=step_type,
        interactive=interactive,
        max_turns=max_turns,
        depends_on=depends_on or [],
    )


def _gate(gate_type: str = "test", command: str = "pytest") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command)


def _phase(
    phase_id: int = 1,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-flag-001",
    task_summary: str = "Build a thing",
    phases: list[PlanPhase] | None = None,
    shared_context: str = "Shared context here.",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        phases=phases or [_phase()],
        shared_context=shared_context,
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


# Canonical well-formed flag outputs used across many tests

_DESIGN_CHOICE_OUTPUT = """\
I've analysed the auth requirements.

DESIGN_CHOICE: JWT refresh tokens vs session cookies for auth persistence
OPTION_A: JWT with refresh — stateless, better for API consumers
OPTION_B: Session cookies — simpler, existing helper works
CONFIDENCE: partial
RECOMMENDATION: Option A based on API-first signals in codebase

Further analysis below.
"""

_CONFLICT_OUTPUT = """\
Synthesis complete. Issue detected.

CONFLICT: API contract mismatch between backend and frontend
PARTIES: backend-engineer--python (step 2.1), frontend-engineer--react (step 2.2)
DESCRIPTION: Response field naming — backend uses snake_case, frontend expects camelCase
CONFIDENCE: partial
RECOMMENDATION: Backend adapts — add serialization layer
"""


# ===========================================================================
# 1. Flag Parsing
# ===========================================================================

class TestDesignFlagParsing:
    """parse_design_flag() — correctness for well-formed and malformed input."""

    def test_well_formed_block_parsed_correctly(self) -> None:
        flag = parse_design_flag(_DESIGN_CHOICE_OUTPUT, step_id="2.1", agent_name="backend-engineer--python")
        assert flag is not None
        assert flag.description == "JWT refresh tokens vs session cookies for auth persistence"
        assert flag.options == [
            "JWT with refresh — stateless, better for API consumers",
            "Session cookies — simpler, existing helper works",
        ]
        assert flag.confidence == "partial"
        assert flag.recommendation == "Option A based on API-first signals in codebase"
        assert flag.step_id == "2.1"
        assert flag.agent_name == "backend-engineer--python"
        assert flag.flag_type == "design-choice"

    def test_no_flag_returns_none(self) -> None:
        result = parse_design_flag("Completed all tasks successfully. No issues found.")
        assert result is None

    def test_missing_confidence_defaults_to_low(self) -> None:
        outcome = "DESIGN_CHOICE: Which cache backend to use\nOPTION_A: Redis\nOPTION_B: Memcached"
        flag = parse_design_flag(outcome)
        assert flag is not None
        assert flag.confidence == "low"

    def test_missing_recommendation_defaults_to_empty_string(self) -> None:
        outcome = "DESIGN_CHOICE: Database ORM choice\nOPTION_A: SQLAlchemy\nCONFIDENCE: low"
        flag = parse_design_flag(outcome)
        assert flag is not None
        assert flag.recommendation == ""

    def test_missing_options_produces_empty_list_not_crash(self) -> None:
        # Partial parse: DESIGN_CHOICE line present, no OPTION_ lines at all
        outcome = "DESIGN_CHOICE: Something tricky\nCONFIDENCE: none"
        flag = parse_design_flag(outcome)
        assert flag is not None
        assert flag.options == []
        assert flag.confidence == "none"

    def test_flag_buried_in_long_output_still_found(self) -> None:
        preamble = "x\n" * 200
        suffix = "\n" + "y\n" * 100
        outcome = preamble + "DESIGN_CHOICE: Use async or sync?\nOPTION_A: Async\nOPTION_B: Sync\n" + suffix
        flag = parse_design_flag(outcome)
        assert flag is not None
        assert flag.description == "Use async or sync?"

    def test_multiple_options_collected_in_letter_order(self) -> None:
        outcome = (
            "DESIGN_CHOICE: Deployment strategy\n"
            "OPTION_C: Blue-green\n"
            "OPTION_A: Rolling\n"
            "OPTION_B: Canary\n"
        )
        flag = parse_design_flag(outcome)
        assert flag is not None
        # Sorted by letter: A, B, C
        assert flag.options[0] == "Rolling"
        assert flag.options[1] == "Canary"
        assert flag.options[2] == "Blue-green"

    def test_partial_outcome_stored_on_flag(self) -> None:
        flag = parse_design_flag(_DESIGN_CHOICE_OUTPUT, step_id="1.1", agent_name="agent")
        assert flag is not None
        assert flag.partial_outcome == _DESIGN_CHOICE_OUTPUT

    def test_option_lines_without_design_choice_not_parsed(self) -> None:
        # Lone OPTION lines must not produce a DesignFlag
        outcome = "OPTION_A: Something\nOPTION_B: Something else"
        result = parse_design_flag(outcome)
        assert result is None

    def test_confidence_normalised_to_lowercase(self) -> None:
        outcome = "DESIGN_CHOICE: Arch decision\nCONFIDENCE: PARTIAL"
        flag = parse_design_flag(outcome)
        assert flag is not None
        assert flag.confidence == "partial"

    def test_invalid_confidence_value_falls_back_to_low(self) -> None:
        # "medium" is not a valid confidence level
        outcome = "DESIGN_CHOICE: Arch decision\nCONFIDENCE: medium"
        flag = parse_design_flag(outcome)
        assert flag is not None
        assert flag.confidence == "low"


class TestConflictFlagParsing:
    """parse_conflict_flag() — correctness for well-formed and malformed input."""

    def test_well_formed_block_parsed_correctly(self) -> None:
        flag = parse_conflict_flag(_CONFLICT_OUTPUT, step_id="2.3", agent_name="architect")
        assert flag is not None
        assert flag.description == "API contract mismatch between backend and frontend"
        assert flag.parties == [
            "backend-engineer--python (step 2.1)",
            "frontend-engineer--react (step 2.2)",
        ]
        assert flag.conflict_detail == "Response field naming — backend uses snake_case, frontend expects camelCase"
        assert flag.confidence == "partial"
        assert flag.recommendation == "Backend adapts — add serialization layer"
        assert flag.step_id == "2.3"
        assert flag.agent_name == "architect"
        assert flag.flag_type == "conflict"

    def test_no_flag_returns_none(self) -> None:
        result = parse_conflict_flag("Everything looks good, no conflicts detected.")
        assert result is None

    def test_missing_parties_defaults_to_empty_list(self) -> None:
        outcome = "CONFLICT: Module naming collision\nCONFIDENCE: low"
        flag = parse_conflict_flag(outcome)
        assert flag is not None
        assert flag.parties == []

    def test_missing_description_line_defaults_to_empty_conflict_detail(self) -> None:
        # DESCRIPTION: line absent — conflict_detail should default to ""
        outcome = "CONFLICT: Naming collision\nPARTIES: agent-a, agent-b\nCONFIDENCE: low"
        flag = parse_conflict_flag(outcome)
        assert flag is not None
        assert flag.conflict_detail == ""

    def test_missing_recommendation_defaults_to_empty_string(self) -> None:
        outcome = "CONFLICT: API mismatch\nCONFIDENCE: none"
        flag = parse_conflict_flag(outcome)
        assert flag is not None
        assert flag.recommendation == ""

    def test_parties_split_on_comma(self) -> None:
        outcome = "CONFLICT: X\nPARTIES: agent-a (step 1), agent-b (step 2), agent-c (step 3)"
        flag = parse_conflict_flag(outcome)
        assert flag is not None
        assert len(flag.parties) == 3
        assert "agent-a (step 1)" in flag.parties
        assert "agent-c (step 3)" in flag.parties

    def test_flag_buried_in_long_output_still_found(self) -> None:
        preamble = "z\n" * 300
        outcome = preamble + "CONFLICT: Deadlock in module loading\nPARTIES: a, b\n"
        flag = parse_conflict_flag(outcome)
        assert flag is not None
        assert flag.description == "Deadlock in module loading"

    def test_partial_outcome_stored_on_flag(self) -> None:
        flag = parse_conflict_flag(_CONFLICT_OUTPUT, step_id="3.1", agent_name="synthesis-agent")
        assert flag is not None
        assert flag.partial_outcome == _CONFLICT_OUTPUT


# ===========================================================================
# 2. Routing
# ===========================================================================

class TestFlagRouting:
    """_FLAG_ROUTING and _FLAG_ROUTING_DEFAULT map flag types to specialists."""

    def test_design_choice_routes_to_architect(self) -> None:
        assert _FLAG_ROUTING["design-choice"] == "architect"

    def test_conflict_routes_to_architect(self) -> None:
        assert _FLAG_ROUTING["conflict"] == "architect"

    def test_default_fallback_is_architect(self) -> None:
        assert _FLAG_ROUTING_DEFAULT == "architect"

    def test_unknown_flag_type_falls_back_to_architect_via_get(self) -> None:
        specialist = _FLAG_ROUTING.get("unknown-type", _FLAG_ROUTING_DEFAULT)
        assert specialist == "architect"

    def test_extensibility_entries_present(self) -> None:
        # domain-gap and security are defined as placeholders per spec
        assert "domain-gap" in _FLAG_ROUTING
        assert "security" in _FLAG_ROUTING


# ===========================================================================
# 3. to_consultation_description()
# ===========================================================================

class TestDesignFlagConsultationDescription:
    """DesignFlag.to_consultation_description() produces correct markdown."""

    def _make_flag(self, **kwargs) -> DesignFlag:
        defaults = dict(
            description="JWT vs sessions",
            options=["JWT with refresh", "Session cookies"],
            confidence="partial",
            recommendation="Option A for API-first codebase",
            step_id="2.1",
            agent_name="backend-engineer--python",
            partial_outcome="Some context output here.",
        )
        defaults.update(kwargs)
        return DesignFlag(**defaults)

    def test_header_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "## Design Choice Requiring Resolution" in desc

    def test_step_id_and_agent_in_description(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "2.1" in desc
        assert "backend-engineer--python" in desc

    def test_choice_description_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "JWT vs sessions" in desc

    def test_options_section_with_letter_labels(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "### Options" in desc
        assert "**Option A:**" in desc
        assert "**Option B:**" in desc

    def test_no_options_shows_placeholder(self) -> None:
        desc = self._make_flag(options=[]).to_consultation_description()
        assert "*(no options provided)*" in desc

    def test_recommendation_with_confidence_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "### Agent's Recommendation" in desc
        assert "Option A for API-first codebase" in desc
        assert "confidence: partial" in desc

    def test_no_recommendation_shows_placeholder_with_confidence(self) -> None:
        desc = self._make_flag(recommendation="").to_consultation_description()
        assert "*(none provided — confidence:" in desc

    def test_context_section_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "### Relevant Context from Agent Output" in desc

    def test_excerpt_lines_are_blockquoted(self) -> None:
        desc = self._make_flag(partial_outcome="Line one\nLine two").to_consultation_description()
        assert "> Line one" in desc
        assert "> Line two" in desc

    def test_long_output_truncated_to_context_excerpt_chars(self) -> None:
        long_output = "x" * (_CONTEXT_EXCERPT_CHARS + 500)
        flag = self._make_flag(partial_outcome=long_output)
        desc = flag.to_consultation_description()
        # The excerpt section should contain exactly _CONTEXT_EXCERPT_CHARS chars
        # (the last slice). Verify length of raw partial_outcome > _CONTEXT_EXCERPT_CHARS
        # but desc doesn't grow proportionally.
        assert len(long_output) > _CONTEXT_EXCERPT_CHARS
        # The blockquote section must contain the tail of the output
        tail = long_output[-_CONTEXT_EXCERPT_CHARS:]
        assert tail in desc

    def test_no_partial_outcome_shows_placeholder(self) -> None:
        desc = self._make_flag(partial_outcome="").to_consultation_description()
        assert "*(no output available)*" in desc


class TestConflictFlagConsultationDescription:
    """ConflictFlag.to_consultation_description() produces correct markdown."""

    def _make_flag(self, **kwargs) -> ConflictFlag:
        defaults = dict(
            description="API contract mismatch",
            parties=["backend-engineer--python (step 2.1)", "frontend-engineer--react (step 2.2)"],
            conflict_detail="snake_case vs camelCase",
            confidence="partial",
            recommendation="Backend adds serialization layer",
            step_id="2.3",
            agent_name="architect",
            partial_outcome="Context here.",
        )
        defaults.update(kwargs)
        return ConflictFlag(**defaults)

    def test_header_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "## Conflict Requiring Arbitration" in desc

    def test_step_id_and_agent_in_description(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "2.3" in desc
        assert "architect" in desc

    def test_parties_section_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "### Parties" in desc
        assert "backend-engineer--python (step 2.1)" in desc
        assert "frontend-engineer--react (step 2.2)" in desc

    def test_no_parties_shows_placeholder(self) -> None:
        desc = self._make_flag(parties=[]).to_consultation_description()
        assert "*(no parties identified)*" in desc

    def test_conflict_detail_section_present(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "### Conflict Detail" in desc
        assert "snake_case vs camelCase" in desc

    def test_no_conflict_detail_shows_placeholder(self) -> None:
        desc = self._make_flag(conflict_detail="").to_consultation_description()
        assert "*(no detail provided)*" in desc

    def test_recommendation_with_confidence(self) -> None:
        desc = self._make_flag().to_consultation_description()
        assert "### Agent's Recommendation" in desc
        assert "Backend adds serialization layer" in desc
        assert "confidence: partial" in desc

    def test_context_section_present_with_blockquote(self) -> None:
        desc = self._make_flag(partial_outcome="Some output line").to_consultation_description()
        assert "### Relevant Context from Agent Output" in desc
        assert "> Some output line" in desc


# ===========================================================================
# 4. PlanAmendment.metadata — round-trip and backward compat
# ===========================================================================

class TestPlanAmendmentMetadata:
    """PlanAmendment.metadata round-trips through to_dict/from_dict."""

    def test_metadata_round_trips(self) -> None:
        amendment = PlanAmendment(
            amendment_id="amend-1",
            trigger="flag:design-choice",
            trigger_phase_id=1,
            description="Consulting architect",
            metadata={"original_step_id": "1.1", "consulting_step_id": "1.2"},
        )
        data = amendment.to_dict()
        restored = PlanAmendment.from_dict(data)
        assert restored.metadata["original_step_id"] == "1.1"
        assert restored.metadata["consulting_step_id"] == "1.2"

    def test_metadata_missing_from_dict_defaults_to_empty(self) -> None:
        # Old serialised amendments without 'metadata' load cleanly
        data = {
            "amendment_id": "amend-old",
            "trigger": "manual",
            "trigger_phase_id": 0,
            "description": "Old amendment",
        }
        amendment = PlanAmendment.from_dict(data)
        assert amendment.metadata == {}

    def test_metadata_to_dict_includes_key(self) -> None:
        amendment = PlanAmendment(
            amendment_id="amend-2",
            trigger="flag:conflict",
            trigger_phase_id=2,
            description="Conflict resolved",
            metadata={"resolution": "Use snake_case everywhere"},
        )
        d = amendment.to_dict()
        assert "metadata" in d
        assert d["metadata"]["resolution"] == "Use snake_case everywhere"

    def test_empty_metadata_round_trips_as_empty_dict(self) -> None:
        amendment = PlanAmendment(
            amendment_id="amend-3",
            trigger="manual",
            trigger_phase_id=0,
            description="No metadata",
        )
        data = amendment.to_dict()
        restored = PlanAmendment.from_dict(data)
        assert restored.metadata == {}


# ===========================================================================
# 5. provide_interact_input source parameter
# ===========================================================================

class TestProvideInteractInputSource:
    """provide_interact_input(source=) records the correct source on InteractionTurn."""

    def test_source_auto_agent_recorded(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        engine.provide_interact_input("1.1", "Go with JWT.", source="auto-agent")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        human_turns = [t for t in result.interaction_history if t.role == "human"]
        assert len(human_turns) == 1
        assert human_turns[0].source == "auto-agent"

    def test_source_defaults_to_human(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        engine.provide_interact_input("1.1", "Go with sessions.")  # no source arg

        state = engine._load_state()
        result = state.get_step_result("1.1")
        human_turns = [t for t in result.interaction_history if t.role == "human"]
        assert len(human_turns) == 1
        assert human_turns[0].source == "human"

    def test_source_webhook_recorded(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        engine.provide_interact_input("1.1", "Webhook says use Option A.", source="webhook")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        human_turns = [t for t in result.interaction_history if t.role == "human"]
        assert human_turns[0].source == "webhook"


# ===========================================================================
# 6. Resolution Markers (parse_flag_resolution, has_escalate_to_interact)
# ===========================================================================

class TestResolutionMarkers:
    """Low-level parsing of FLAG_RESOLVED: and ESCALATE_TO_INTERACT: lines."""

    def test_flag_resolved_extracts_decision_text(self) -> None:
        outcome = "After reviewing the options:\nFLAG_RESOLVED: Use JWT with refresh tokens\nEnd of analysis."
        decision = parse_flag_resolution(outcome)
        assert decision == "Use JWT with refresh tokens"

    def test_flag_resolved_returns_none_when_absent(self) -> None:
        result = parse_flag_resolution("No resolution markers here.")
        assert result is None

    def test_flag_resolved_case_insensitive(self) -> None:
        outcome = "flag_resolved: Prefer snake_case"
        decision = parse_flag_resolution(outcome)
        assert decision == "Prefer snake_case"

    def test_has_escalate_to_interact_true_when_present(self) -> None:
        outcome = "I need more context.\nESCALATE_TO_INTERACT:\nPlease provide details."
        assert has_escalate_to_interact(outcome) is True

    def test_has_escalate_to_interact_false_when_absent(self) -> None:
        outcome = "FLAG_RESOLVED: Use approach A"
        assert has_escalate_to_interact(outcome) is False

    def test_has_escalate_to_interact_case_insensitive(self) -> None:
        outcome = "escalate_to_interact:"
        assert has_escalate_to_interact(outcome) is True

    def test_flag_resolved_with_only_whitespace_after_colon(self) -> None:
        # Edge case: empty decision text after whitespace strip
        outcome = "FLAG_RESOLVED:   \n"
        decision = parse_flag_resolution(outcome)
        # strip() on an all-whitespace capture group returns ""
        assert decision == "" or decision is None  # implementation-defined, both acceptable


# ===========================================================================
# 7. Engine integration — flag detection and consultation insertion
# ===========================================================================

class TestFlagHandledByEngine:
    """Engine detects flags in record_step_result and inserts consulting steps."""

    def test_design_flag_marks_original_step_interrupted(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "interrupted"

    def test_design_flag_inserts_consulting_step_in_phase(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT)
        state = engine._load_state()
        phase = state.plan.phases[0]
        step_types = [s.step_type for s in phase.steps]
        assert "consulting" in step_types

    def test_design_flag_consulting_step_targets_architect(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT)
        state = engine._load_state()
        phase = state.plan.phases[0]
        consulting = next(s for s in phase.steps if s.step_type == "consulting")
        assert consulting.agent_name == "architect"

    def test_design_flag_records_amendment_with_correct_trigger(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT)
        state = engine._load_state()
        flag_amendments = [a for a in state.amendments if a.trigger == "flag:design-choice"]
        assert len(flag_amendments) == 1

    def test_design_flag_amendment_metadata_has_original_step_id(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT)
        state = engine._load_state()
        flag_amendment = next(a for a in state.amendments if a.trigger == "flag:design-choice")
        assert flag_amendment.metadata.get("original_step_id") == "1.1"

    def test_conflict_flag_marks_original_step_interrupted(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("2.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("2.1", "architect", status="complete", outcome=_CONFLICT_OUTPUT)
        state = engine._load_state()
        result = state.get_step_result("2.1")
        assert result is not None
        assert result.status == "interrupted"

    def test_conflict_flag_inserts_consulting_step(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=_CONFLICT_OUTPUT)
        state = engine._load_state()
        phase = state.plan.phases[0]
        assert any(s.step_type == "consulting" for s in phase.steps)

    def test_conflict_flag_amendment_trigger(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=_CONFLICT_OUTPUT)
        state = engine._load_state()
        flag_amendments = [a for a in state.amendments if a.trigger == "flag:conflict"]
        assert len(flag_amendments) == 1

    def test_design_flag_takes_precedence_over_conflict_when_both_present(self, tmp_path: Path) -> None:
        # Both flags in same output — design-choice is parsed first per spec
        combined = _DESIGN_CHOICE_OUTPUT + "\n" + _CONFLICT_OUTPUT
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=combined)
        state = engine._load_state()
        # Only one consulting step — the first flag wins, method returns early
        consulting_steps = [s for s in state.plan.phases[0].steps if s.step_type == "consulting"]
        assert len(consulting_steps) == 1
        flag_amendments = [a for a in state.amendments if a.trigger.startswith("flag:")]
        assert len(flag_amendments) == 1
        assert flag_amendments[0].trigger == "flag:design-choice"


class TestFlagPrecedenceOverKnowledgeGap:
    """When both a flag and KNOWLEDGE_GAP appear, the flag takes priority."""

    def test_flag_plus_knowledge_gap_flag_handled_no_gap_queued(self, tmp_path: Path) -> None:
        combined = (
            "DESIGN_CHOICE: Use Redis or Memcached\n"
            "OPTION_A: Redis — richer data types\n"
            "OPTION_B: Memcached — simpler\n"
            "CONFIDENCE: low\n"
            "\nKNOWLEDGE_GAP: What is the expected cache size?\n"
            "TYPE: factual\n"
            "CONTEXT: Need to size the instance\n"
        )
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome=combined)
        state = engine._load_state()
        # Flag was handled — consulting step exists
        assert any(s.step_type == "consulting" for s in state.plan.phases[0].steps)
        # Knowledge gap was NOT queued because flag handler returned early
        assert len(state.pending_gaps) == 0


# ===========================================================================
# 8. Anti-Lock — consulting step exemption
# ===========================================================================

class TestAntiLockConsultingExemption:
    """Consulting steps are exempt from flag detection — prevents infinite loops."""

    def test_consulting_step_output_with_design_flag_not_escalated(self, tmp_path: Path) -> None:
        # Manually insert a consulting step and have it output a DESIGN_CHOICE flag.
        consulting = _step("1.2", agent_name="architect", step_type="consulting",
                           task="Resolve JWT vs sessions")
        plan = _plan(phases=[_phase(steps=[_step("1.1"), consulting])])
        engine = _engine(tmp_path)
        engine.start(plan)
        # Record the original step as interrupted (simulating prior flag handling).
        # This legitimately inserts a new consulting step (1.3) via _handle_flags.
        engine.record_step_result("1.1", "backend-engineer", status="interrupted",
                                  outcome="DESIGN_CHOICE: Which DB to use\nOPTION_A: Postgres\nCONFIDENCE: low")
        state_before = engine._load_state()
        consulting_count_before = len([
            s for s in state_before.plan.phases[0].steps if s.step_type == "consulting"
        ])
        # Now the consulting step emits another DESIGN_CHOICE (pathological case).
        # The anti-loop guard should prevent a new consulting step from being inserted.
        engine.record_step_result("1.2", "architect", status="complete",
                                  outcome=_DESIGN_CHOICE_OUTPUT)
        state_after = engine._load_state()
        consulting_count_after = len([
            s for s in state_after.plan.phases[0].steps if s.step_type == "consulting"
        ])
        # Consulting step 1.2's flag output must NOT have spawned another consulting step.
        assert consulting_count_after == consulting_count_before

    def test_consulting_step_output_with_conflict_not_escalated(self, tmp_path: Path) -> None:
        consulting = _step("1.2", agent_name="architect", step_type="consulting",
                           task="Arbitrate API conflict")
        plan = _plan(phases=[_phase(steps=[_step("1.1"), consulting])])
        engine = _engine(tmp_path)
        engine.start(plan)
        # Recording 1.1 with a CONFLICT flag legitimately inserts consulting step 1.3.
        engine.record_step_result("1.1", "backend-engineer", status="interrupted",
                                  outcome="CONFLICT: X\nPARTIES: a, b\nCONFIDENCE: low")
        state_before = engine._load_state()
        consulting_count_before = len([
            s for s in state_before.plan.phases[0].steps if s.step_type == "consulting"
        ])
        # Consulting step 1.2's conflict output must not spawn another consulting step.
        engine.record_step_result("1.2", "architect", status="complete",
                                  outcome=_CONFLICT_OUTPUT)
        state_after = engine._load_state()
        consulting_count_after = len([
            s for s in state_after.plan.phases[0].steps if s.step_type == "consulting"
        ])
        assert consulting_count_after == consulting_count_before


# ===========================================================================
# 9. Anti-Lock — two flags in same phase -> independent chains
# ===========================================================================

class TestTwoFlagsInSamePhase:
    """Two separate steps emitting flags each get their own consulting step."""

    def test_two_design_flags_produce_two_amendments(self, tmp_path: Path) -> None:
        step_a = _step("1.1", agent_name="backend-engineer--python", task="Build API")
        step_b = _step("1.2", agent_name="frontend-engineer--react", task="Build UI",
                       depends_on=["1.1"])
        plan = _plan(phases=[_phase(steps=[step_a, step_b])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            "1.1", "backend-engineer--python", status="complete",
            outcome="DESIGN_CHOICE: Auth approach\nOPTION_A: JWT\nOPTION_B: Sessions\nCONFIDENCE: low",
        )
        engine.record_step_result(
            "1.2", "frontend-engineer--react", status="complete",
            outcome="DESIGN_CHOICE: State management\nOPTION_A: Redux\nOPTION_B: Context API\nCONFIDENCE: low",
        )

        state = engine._load_state()
        flag_amendments = [a for a in state.amendments if a.trigger == "flag:design-choice"]
        assert len(flag_amendments) == 2

    def test_two_design_flags_produce_two_consulting_steps(self, tmp_path: Path) -> None:
        step_a = _step("1.1", agent_name="backend-engineer--python", task="Build API")
        step_b = _step("1.2", agent_name="frontend-engineer--react", task="Build UI",
                       depends_on=["1.1"])
        plan = _plan(phases=[_phase(steps=[step_a, step_b])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            "1.1", "backend-engineer--python", status="complete",
            outcome="DESIGN_CHOICE: Auth approach\nOPTION_A: JWT\nOPTION_B: Sessions\nCONFIDENCE: low",
        )
        engine.record_step_result(
            "1.2", "frontend-engineer--react", status="complete",
            outcome="DESIGN_CHOICE: State mgmt\nOPTION_A: Redux\nOPTION_B: Context\nCONFIDENCE: low",
        )

        state = engine._load_state()
        phase = state.plan.phases[0]
        consulting_steps = [s for s in phase.steps if s.step_type == "consulting"]
        assert len(consulting_steps) == 2


# ===========================================================================
# 10. Escalation Chain — Tier 1 FLAG_RESOLVED
# ===========================================================================

class TestTier1FlagResolved:
    """FLAG_RESOLVED: in consulting output triggers re-dispatch."""

    def _run_to_consulting_complete(
        self,
        engine: ExecutionEngine,
        original_step_id: str,
        original_agent: str,
        flag_output: str,
        resolution_output: str,
    ) -> None:
        """Drive: original step emits flag -> consulting step resolves it."""
        engine.record_step_result(
            step_id=original_step_id,
            agent_name=original_agent,
            status="complete",
            outcome=flag_output,
        )
        state = engine._load_state()
        phase = state.plan.phases[0]
        consulting = next(s for s in phase.steps if s.step_type == "consulting")
        # Consulting step result
        engine.record_step_result(
            step_id=consulting.step_id,
            agent_name=consulting.agent_name,
            status="complete",
            outcome=resolution_output,
        )

    def test_flag_resolved_records_resolved_decision(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        self._run_to_consulting_complete(
            engine, "1.1", "backend-engineer",
            flag_output=_DESIGN_CHOICE_OUTPUT,
            resolution_output="FLAG_RESOLVED: Use JWT with refresh tokens",
        )
        state = engine._load_state()
        assert len(state.resolved_decisions) >= 1
        assert any("JWT" in d.resolution for d in state.resolved_decisions)

    def test_flag_resolved_inserts_redispatch_step(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        self._run_to_consulting_complete(
            engine, "1.1", "backend-engineer",
            flag_output=_DESIGN_CHOICE_OUTPUT,
            resolution_output="FLAG_RESOLVED: Use JWT with refresh tokens",
        )
        state = engine._load_state()
        phase = state.plan.phases[0]
        step_ids = [s.step_id for s in phase.steps]
        # Should have at least 3 steps: original (1.1), consulting, redispatch
        assert len(step_ids) >= 3

    def test_flag_resolved_redispatch_targets_original_agent(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", agent_name="backend-engineer--python")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        self._run_to_consulting_complete(
            engine, "1.1", "backend-engineer--python",
            flag_output=_DESIGN_CHOICE_OUTPUT,
            resolution_output="FLAG_RESOLVED: Use JWT with refresh tokens",
        )
        state = engine._load_state()
        phase = state.plan.phases[0]
        # The last non-consulting step should target the original agent
        non_consulting = [s for s in phase.steps if s.step_type != "consulting"]
        # There should be a redispatch step (at least one beyond the original)
        assert len(non_consulting) >= 2
        # Last one should be the redispatch for the original agent
        assert non_consulting[-1].agent_name == "backend-engineer--python"

    def test_flag_resolved_records_flag_resolved_amendment(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        self._run_to_consulting_complete(
            engine, "1.1", "backend-engineer",
            flag_output=_DESIGN_CHOICE_OUTPUT,
            resolution_output="FLAG_RESOLVED: Use JWT with refresh tokens",
        )
        state = engine._load_state()
        resolved_amendments = [a for a in state.amendments if a.trigger == "flag:resolved"]
        assert len(resolved_amendments) >= 1

    def test_flag_resolved_task_description_continues_from_partial(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", task="Implement auth module")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        self._run_to_consulting_complete(
            engine, "1.1", "backend-engineer",
            flag_output=_DESIGN_CHOICE_OUTPUT,
            resolution_output="FLAG_RESOLVED: Use JWT with refresh tokens",
        )
        state = engine._load_state()
        phase = state.plan.phases[0]
        non_consulting = [s for s in phase.steps if s.step_type != "consulting"]
        redispatch = non_consulting[-1]
        assert "Continue from partial progress" in redispatch.task_description


# ===========================================================================
# 11. Escalation Chain — Tier 2 ESCALATE_TO_INTERACT
# ===========================================================================

class TestTier2EscalateToInteract:
    """ESCALATE_TO_INTERACT: in consulting output promotes to Tier 2 dialogue."""

    def test_escalate_sets_consulting_step_interactive_true(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        phase = state.plan.phases[0]
        consulting = next(s for s in phase.steps if s.step_type == "consulting")
        consulting_id = consulting.step_id

        engine.record_step_result(
            consulting_id, "architect", status="complete",
            outcome="I need to discuss this further.\nESCALATE_TO_INTERACT:\nMore context needed.",
        )

        state = engine._load_state()
        phase_after = state.plan.phases[0]
        consulting_after = next(s for s in phase_after.steps if s.step_id == consulting_id)
        assert consulting_after.interactive is True

    def test_escalate_sets_consulting_step_result_to_interacting(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        consulting = next(s for s in state.plan.phases[0].steps if s.step_type == "consulting")
        consulting_id = consulting.step_id

        engine.record_step_result(
            consulting_id, "architect", status="complete",
            outcome="Need more context.\nESCALATE_TO_INTERACT:",
        )

        state = engine._load_state()
        consulting_result = state.get_step_result(consulting_id)
        assert consulting_result is not None
        assert consulting_result.status == "interacting"

    def test_escalate_next_action_is_interact(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        consulting = next(s for s in state.plan.phases[0].steps if s.step_type == "consulting")
        consulting_id = consulting.step_id

        engine.record_step_result(
            consulting_id, "architect", status="complete",
            outcome="Need clarification.\nESCALATE_TO_INTERACT:",
        )

        action = engine.next_action()
        assert action.action_type == ActionType.INTERACT
        assert action.interact_step_id == consulting_id


# ===========================================================================
# 12. Anti-Over-Reliance Observability
# ===========================================================================

class TestAntiOverRelianceObservability:
    """Flag amendments are queryable for over-reliance analysis."""

    def test_flag_amendments_countable_by_trigger(self, tmp_path: Path) -> None:
        # Simulate two agents each emitting design-choice flags
        step_a = _step("1.1", agent_name="backend-engineer--python", task="Build API")
        step_b = _step("1.2", agent_name="frontend-engineer--react", task="Build UI",
                       depends_on=["1.1"])
        plan = _plan(phases=[_phase(steps=[step_a, step_b])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            "1.1", "backend-engineer--python", status="complete",
            outcome="DESIGN_CHOICE: Auth\nOPTION_A: JWT\nOPTION_B: Sessions\nCONFIDENCE: low",
        )
        engine.record_step_result(
            "1.2", "frontend-engineer--react", status="complete",
            outcome="DESIGN_CHOICE: State\nOPTION_A: Redux\nOPTION_B: Context\nCONFIDENCE: low",
        )

        state = engine._load_state()
        design_choice_count = sum(
            1 for a in state.amendments if a.trigger == "flag:design-choice"
        )
        assert design_choice_count == 2

    def test_amendment_metadata_stores_agent_context(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", agent_name="backend-engineer--python")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer--python", status="complete", outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        flag_amendment = next(a for a in state.amendments if a.trigger == "flag:design-choice")
        # Amendment should reference both the original and consulting step IDs
        assert "original_step_id" in flag_amendment.metadata
        assert "consulting_step_id" in flag_amendment.metadata
        assert flag_amendment.metadata["original_step_id"] == "1.1"

    def test_flag_amendments_distinguishable_from_other_amendments(self, tmp_path: Path) -> None:
        """flag: prefix makes it easy to filter only flag-caused amendments."""
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        all_flag_amendments = [a for a in state.amendments if a.trigger.startswith("flag:")]
        # All flag-related amendments (escalation + any resolved) have the flag: prefix
        assert len(all_flag_amendments) >= 1


# ===========================================================================
# 13. Dispatcher — _FLAG_SIGNALS_LINE present in delegation prompts
# ===========================================================================

class TestDispatcherFlagSignalsLine:
    """Flag emission instructions appear in delegation prompts."""

    def test_flag_signals_in_delegation_prompt(self, tmp_path: Path) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = _step("1.1", agent_name="backend-engineer")
        prompt = dispatcher.build_delegation_prompt(
            step=step,
            shared_context="Context here.",
            handoff_from="",
        )
        assert "DESIGN_CHOICE" in prompt
        assert "CONFLICT" in prompt

    def test_flag_signals_in_consultation_prompt(self, tmp_path: Path) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        consulting_step = _step("1.2", agent_name="architect", step_type="consulting",
                                task="## Design Choice Requiring Resolution\n**Choice:** JWT vs sessions")
        prompt = dispatcher.build_consultation_prompt(
            step=consulting_step,
        )
        # Consultation prompt should include the task description (the flag brief)
        assert "Design Choice Requiring Resolution" in prompt


# ===========================================================================
# 14. End-to-End Integration
# ===========================================================================

class TestEndToEndFlagEscalation:
    """Full chain: developer emits flag, architect resolves, agent re-dispatched."""

    def test_full_tier1_chain_design_choice(self, tmp_path: Path) -> None:
        """
        Phase 1:
          1.1 [developing] backend-engineer builds API
              -> output contains DESIGN_CHOICE: (JWT vs sessions)
          1.2 [consulting] architect consultation (auto-inserted)
              -> output contains FLAG_RESOLVED: JWT
          1.3 [developing] backend-engineer re-dispatch with decision
              -> completes successfully
        """
        plan = _plan(
            phases=[_phase(
                phase_id=1,
                steps=[_step("1.1", agent_name="backend-engineer", task="Implement auth module")],
            )]
        )
        engine = _engine(tmp_path)
        engine.start(plan)

        # Step 1: backend-engineer emits a design flag
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome=_DESIGN_CHOICE_OUTPUT,
        )
        state = engine._load_state()
        assert state.get_step_result("1.1").status == "interrupted"

        # Locate the auto-inserted consulting step
        phase = state.plan.phases[0]
        consulting = next(s for s in phase.steps if s.step_type == "consulting")
        assert consulting.agent_name == "architect"
        assert "Design Choice Requiring Resolution" in consulting.task_description

        # Step 2: architect resolves
        engine.record_step_result(
            consulting.step_id, "architect", status="complete",
            outcome="After reviewing the options:\nFLAG_RESOLVED: Use JWT with refresh tokens for API-first design",
        )
        state = engine._load_state()
        assert len(state.resolved_decisions) >= 1

        # Verify re-dispatch step was inserted
        phase = state.plan.phases[0]
        non_consulting = [s for s in phase.steps if s.step_type != "consulting"]
        assert len(non_consulting) >= 2
        redispatch = non_consulting[-1]
        assert redispatch.agent_name == "backend-engineer"
        assert "Continue from partial progress" in redispatch.task_description

        # Step 3: re-dispatched backend-engineer completes
        engine.record_step_result(
            redispatch.step_id, "backend-engineer", status="complete",
            outcome="Auth module implemented using JWT with refresh tokens.",
        )
        state = engine._load_state()
        final_result = state.get_step_result(redispatch.step_id)
        assert final_result.status == "complete"

    def test_full_tier1_chain_conflict_flag(self, tmp_path: Path) -> None:
        """Conflict flag also routes to architect and enables re-dispatch."""
        plan = _plan(
            phases=[_phase(
                phase_id=1,
                steps=[_step("1.1", agent_name="architect", task="Synthesize API outputs")],
            )]
        )
        engine = _engine(tmp_path)
        engine.start(plan)

        # Synthesis step emits conflict
        engine.record_step_result(
            "1.1", "architect", status="complete", outcome=_CONFLICT_OUTPUT,
        )
        state = engine._load_state()
        assert state.get_step_result("1.1").status == "interrupted"

        phase = state.plan.phases[0]
        consulting = next(s for s in phase.steps if s.step_type == "consulting")
        assert consulting.agent_name == "architect"
        assert "Conflict Requiring Arbitration" in consulting.task_description

        # Architect resolves the conflict
        engine.record_step_result(
            consulting.step_id, "architect", status="complete",
            outcome="FLAG_RESOLVED: Backend adds a serialization layer to produce camelCase responses",
        )
        state = engine._load_state()
        assert any("camelCase" in d.resolution for d in state.resolved_decisions)

    def test_no_flag_output_completes_step_normally(self, tmp_path: Path) -> None:
        """A step with no flag output completes without inserting any consulting step."""
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete",
            outcome="Implemented the auth module using JWT. All tests passing.",
        )
        state = engine._load_state()
        assert state.get_step_result("1.1").status == "complete"
        phase = state.plan.phases[0]
        assert all(s.step_type != "consulting" for s in phase.steps)
        assert all(not a.trigger.startswith("flag:") for a in state.amendments)
