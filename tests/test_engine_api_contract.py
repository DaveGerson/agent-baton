"""API-contract canary for ExecutionEngine (005b step 2.2A).

This test file pins the public API surface of ``ExecutionEngine`` so that any
refactor (005b Phase 2) that accidentally breaks the interface fails loudly
*before* the code changes land.  It is deliberately read-only — it imports the
existing engine and inspects it without constructing heavy state.

Tests are organised in five groups matching design §1.1–§1.5:

  1. Module-level import paths
  2. ``ExecutionEngine.__init__`` constructor signature (11 params)
  3. All 15 ``ExecutionDriver`` Protocol methods present on ``ExecutionEngine``
     with matching parameter names and return annotations
  4. ``record_step_result`` extra kwargs (3 added beyond the Protocol,
     BEAD_DISCOVERY from design §1.4)
  5. ``set_swarm_launcher`` callable (CLI post-construction hook)
  6. Engine instance attributes present after minimal construction

Run this test to establish the green baseline before Phase 2 editing begins::

    pytest tests/test_engine_api_contract.py -x
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_type_hints

import pytest


# ---------------------------------------------------------------------------
# 1. Module-level import paths
# ---------------------------------------------------------------------------

class TestImportPaths:
    """All three canonical import paths must resolve."""

    def test_import_from_core_engine_package(self) -> None:
        from agent_baton.core.engine import ExecutionEngine  # noqa: F401

    def test_import_from_executor_module(self) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine  # noqa: F401

    def test_import_execution_driver_protocol(self) -> None:
        from agent_baton.core.engine.protocols import ExecutionDriver  # noqa: F401

    def test_execution_engine_is_class(self) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine
        assert isinstance(ExecutionEngine, type)

    def test_execution_driver_is_protocol(self) -> None:
        from agent_baton.core.engine.protocols import ExecutionDriver
        # Protocol classes are a typing construct — they are still regular classes.
        assert isinstance(ExecutionDriver, type)


# ---------------------------------------------------------------------------
# 2. Constructor signature
# ---------------------------------------------------------------------------

class TestConstructorSignature:
    """Pin ``ExecutionEngine.__init__`` parameter list, kinds, and defaults.

    The constructor currently has 11 parameters after ``self``.  If a refactor
    adds, removes, or renames a parameter this group will fail immediately.
    """

    @pytest.fixture(scope="class")
    def params(self):
        from agent_baton.core.engine.executor import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.__init__)
        # Drop 'self'
        return {
            name: param
            for name, param in sig.parameters.items()
            if name != "self"
        }

    def test_parameter_count(self, params) -> None:
        assert len(params) == 11, (
            f"ExecutionEngine.__init__ expected 11 parameters (excluding self), "
            f"got {len(params)}: {list(params)}"
        )

    def test_parameter_names_in_order(self, params) -> None:
        expected = [
            "team_context_root",
            "bus",
            "task_id",
            "storage",
            "knowledge_resolver",
            "policy_engine",
            "enforce_token_budget",
            "token_budget",
            "max_gate_retries",
            "force_override",
            "override_justification",
        ]
        assert list(params) == expected

    def test_all_params_are_positional_or_keyword(self, params) -> None:
        POK = inspect.Parameter.POSITIONAL_OR_KEYWORD
        for name, param in params.items():
            assert param.kind == POK, (
                f"Parameter '{name}' expected POSITIONAL_OR_KEYWORD, got {param.kind.name}"
            )

    def test_team_context_root_default_none(self, params) -> None:
        assert params["team_context_root"].default is None

    def test_bus_default_none(self, params) -> None:
        assert params["bus"].default is None

    def test_task_id_default_none(self, params) -> None:
        assert params["task_id"].default is None

    def test_storage_default_none(self, params) -> None:
        assert params["storage"].default is None

    def test_knowledge_resolver_default_none(self, params) -> None:
        assert params["knowledge_resolver"].default is None

    def test_policy_engine_default_none(self, params) -> None:
        assert params["policy_engine"].default is None

    def test_enforce_token_budget_default_true(self, params) -> None:
        assert params["enforce_token_budget"].default is True

    def test_token_budget_default_none(self, params) -> None:
        assert params["token_budget"].default is None

    def test_max_gate_retries_default_3(self, params) -> None:
        assert params["max_gate_retries"].default == 3

    def test_force_override_default_false(self, params) -> None:
        assert params["force_override"].default is False

    def test_override_justification_default_empty_string(self, params) -> None:
        assert params["override_justification"].default == ""


# ---------------------------------------------------------------------------
# 3. All 15 ExecutionDriver Protocol methods present on ExecutionEngine
# ---------------------------------------------------------------------------

_PROTOCOL_METHOD_SPECS: dict[str, dict] = {
    # name → {"params": [names excluding self], "return_annotation": type or string}
    "start": {
        "params": ["plan"],
        "return_name": "ExecutionAction",
    },
    "next_action": {
        "params": [],
        "return_name": "ExecutionAction",
    },
    "next_actions": {
        "params": [],
        "return_name": "list[ExecutionAction]",
    },
    "mark_dispatched": {
        "params": ["step_id", "agent_name"],
        "return_name": "None",
    },
    "record_step_result": {
        # Protocol defines 9 params; engine adds 3 more — tested in group 4.
        # Here we only assert the Protocol-required 9 are present.
        "params": [
            "step_id", "agent_name", "status", "outcome", "files_changed",
            "commit_hash", "estimated_tokens", "duration_seconds", "error",
        ],
        "return_name": "None",
    },
    "record_gate_result": {
        "params": ["phase_id", "passed", "output"],
        "return_name": "None",
    },
    "record_approval_result": {
        "params": ["phase_id", "result", "feedback"],
        "return_name": "None",
    },
    "record_feedback_result": {
        "params": ["phase_id", "question_id", "chosen_index"],
        "return_name": "None",
    },
    "amend_plan": {
        "params": [
            "description", "new_phases", "insert_after_phase",
            "add_steps_to_phase", "new_steps", "trigger",
            "trigger_phase_id", "feedback",
        ],
        "return_name": "PlanAmendment",
    },
    "record_team_member_result": {
        "params": [
            "step_id", "member_id", "agent_name", "status", "outcome", "files_changed",
        ],
        "return_name": "None",
    },
    "complete": {
        "params": [],
        "return_name": "str",
    },
    "status": {
        "params": [],
        "return_name": "dict",
    },
    "resume": {
        "params": [],
        "return_name": "ExecutionAction",
    },
    "provide_interact_input": {
        "params": ["step_id", "input_text", "source"],
        "return_name": "None",
    },
    "complete_interaction": {
        "params": ["step_id"],
        "return_name": "None",
    },
}

_PROTOCOL_METHODS = list(_PROTOCOL_METHOD_SPECS.keys())


@pytest.mark.parametrize("method_name", _PROTOCOL_METHODS)
def test_protocol_method_present_on_engine(method_name: str) -> None:
    """Each of the 15 ExecutionDriver methods must exist on ExecutionEngine."""
    from agent_baton.core.engine.executor import ExecutionEngine
    assert hasattr(ExecutionEngine, method_name), (
        f"ExecutionEngine is missing Protocol method '{method_name}'"
    )
    assert callable(getattr(ExecutionEngine, method_name)), (
        f"ExecutionEngine.{method_name} is not callable"
    )


@pytest.mark.parametrize("method_name", _PROTOCOL_METHODS)
def test_protocol_method_contains_required_params(method_name: str) -> None:
    """Each Protocol method on ExecutionEngine must include all Protocol-defined parameters."""
    from agent_baton.core.engine.executor import ExecutionEngine
    expected_params = _PROTOCOL_METHOD_SPECS[method_name]["params"]
    attr = getattr(ExecutionEngine, method_name)
    sig = inspect.signature(attr)
    actual_params = [p for p in sig.parameters if p != "self"]
    # Engine may ADD params (like record_step_result, record_gate_result,
    # record_approval_result, record_team_member_result) but must contain
    # all Protocol-required params in the same relative order.
    for expected in expected_params:
        assert expected in actual_params, (
            f"ExecutionEngine.{method_name} is missing Protocol parameter '{expected}'. "
            f"Actual params: {actual_params}"
        )
    # Verify ordering of required params is preserved.
    indices = [actual_params.index(p) for p in expected_params if p in actual_params]
    assert indices == sorted(indices), (
        f"ExecutionEngine.{method_name} Protocol parameters are out of order. "
        f"Expected {expected_params} (subset), actual: {actual_params}"
    )


@pytest.mark.parametrize("method_name", _PROTOCOL_METHODS)
def test_protocol_method_return_annotation(method_name: str) -> None:
    """Each Protocol method return annotation must match the Protocol definition."""
    from agent_baton.core.engine.executor import ExecutionEngine
    expected_return_name = _PROTOCOL_METHOD_SPECS[method_name]["return_name"]
    attr = getattr(ExecutionEngine, method_name)
    sig = inspect.signature(attr)
    ann = sig.return_annotation
    if ann is inspect.Parameter.empty:
        pytest.fail(
            f"ExecutionEngine.{method_name} has no return annotation "
            f"(expected '{expected_return_name}')"
        )
    # Annotation may be a string (PEP 563 / from __future__ import annotations)
    # or a live type. Normalise to string for comparison.
    if ann is None or ann == type(None):
        ann_str = "None"
    elif isinstance(ann, str):
        ann_str = ann
    else:
        ann_str = getattr(ann, "__name__", repr(ann))
    assert ann_str == expected_return_name, (
        f"ExecutionEngine.{method_name} return annotation mismatch: "
        f"expected '{expected_return_name}', got '{ann_str}'"
    )


# ---------------------------------------------------------------------------
# 4. record_step_result — full 12-parameter signature (BEAD_DISCOVERY §1.4)
# ---------------------------------------------------------------------------

class TestRecordStepResultSignature:
    """Pin all 12 parameters of ExecutionEngine.record_step_result.

    The engine adds 3 kwargs beyond the Protocol (BEAD_DISCOVERY pattern):
    ``session_id``, ``step_started_at``, ``outcome_spillover_path``.
    Any removal or rename of these is a breaking API change for CLI callers.
    """

    @pytest.fixture(scope="class")
    def params(self):
        from agent_baton.core.engine.executor import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.record_step_result)
        return {
            name: param
            for name, param in sig.parameters.items()
            if name != "self"
        }

    def test_total_parameter_count(self, params) -> None:
        assert len(params) == 12, (
            f"record_step_result expected 12 parameters (excluding self), "
            f"got {len(params)}: {list(params)}"
        )

    def test_parameter_names_in_order(self, params) -> None:
        expected = [
            "step_id",
            "agent_name",
            "status",
            "outcome",
            "files_changed",
            "commit_hash",
            "estimated_tokens",
            "duration_seconds",
            "error",
            # Engine extensions (BEAD_DISCOVERY §1.4)
            "session_id",
            "step_started_at",
            "outcome_spillover_path",
        ]
        assert list(params) == expected

    def test_step_id_no_default(self, params) -> None:
        assert params["step_id"].default is inspect.Parameter.empty

    def test_agent_name_no_default(self, params) -> None:
        assert params["agent_name"].default is inspect.Parameter.empty

    def test_status_default(self, params) -> None:
        assert params["status"].default == "complete"

    def test_outcome_default(self, params) -> None:
        assert params["outcome"].default == ""

    def test_files_changed_default(self, params) -> None:
        assert params["files_changed"].default is None

    def test_commit_hash_default(self, params) -> None:
        assert params["commit_hash"].default == ""

    def test_estimated_tokens_default(self, params) -> None:
        assert params["estimated_tokens"].default == 0

    def test_duration_seconds_default(self, params) -> None:
        assert params["duration_seconds"].default == 0.0

    def test_error_default(self, params) -> None:
        assert params["error"].default == ""

    def test_session_id_default(self, params) -> None:
        """BEAD_DISCOVERY §1.4: session_id is an engine-only extension."""
        assert params["session_id"].default == ""

    def test_step_started_at_default(self, params) -> None:
        """BEAD_DISCOVERY §1.4: step_started_at is an engine-only extension."""
        assert params["step_started_at"].default == ""

    def test_outcome_spillover_path_default(self, params) -> None:
        """BEAD_DISCOVERY §1.4: outcome_spillover_path is an engine-only extension."""
        assert params["outcome_spillover_path"].default == ""


# ---------------------------------------------------------------------------
# 5. set_swarm_launcher — post-construction CLI hook (executor.py:588)
# ---------------------------------------------------------------------------

class TestSetSwarmLauncher:
    """Pin the ``set_swarm_launcher`` method used by the CLI execute loop."""

    def test_method_exists(self) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine
        assert hasattr(ExecutionEngine, "set_swarm_launcher")
        assert callable(getattr(ExecutionEngine, "set_swarm_launcher"))

    def test_signature(self) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.set_swarm_launcher)
        params = [p for p in sig.parameters if p != "self"]
        assert params == ["launcher"], (
            f"set_swarm_launcher expected ['launcher'], got {params}"
        )

    def test_launcher_param_has_no_default(self) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.set_swarm_launcher)
        param = sig.parameters["launcher"]
        assert param.default is inspect.Parameter.empty

    def test_return_annotation_is_none(self) -> None:
        from agent_baton.core.engine.executor import ExecutionEngine
        sig = inspect.signature(ExecutionEngine.set_swarm_launcher)
        assert sig.return_annotation in (None, type(None), "None"), (
            f"set_swarm_launcher return annotation should be None, "
            f"got {sig.return_annotation!r}"
        )


# ---------------------------------------------------------------------------
# 6. Engine instance attributes (design §1.5)
# ---------------------------------------------------------------------------

class TestEngineInstanceAttributes:
    """Verify that external-caller-visible attributes are present after init.

    Uses a minimal construction (tmp dir, no storage backend) so that the
    test remains fast and avoids database I/O.  Attributes that are
    unconditionally set in ``__init__`` are asserted; attributes gated behind
    feature flags (``_swarm``, ``_team_registry``, etc.) are skipped.
    """

    @pytest.fixture(scope="class")
    def engine(self, tmp_path_factory):
        from agent_baton.core.engine.executor import ExecutionEngine
        ctx_root = tmp_path_factory.mktemp("team-context")
        # Construct with no storage, no bus — minimal configuration.
        return ExecutionEngine(
            team_context_root=ctx_root,
            task_id="test-attr-check",
        )

    # Core identity / path attributes
    def test_has_task_id(self, engine) -> None:
        assert hasattr(engine, "_task_id")

    def test_task_id_matches_constructor_arg(self, engine) -> None:
        assert engine._task_id == "test-attr-check"

    def test_has_root(self, engine) -> None:
        assert hasattr(engine, "_root")
        assert isinstance(engine._root, Path)

    # Storage / bus
    def test_has_storage(self, engine) -> None:
        assert hasattr(engine, "_storage")

    def test_has_bus(self, engine) -> None:
        assert hasattr(engine, "_bus")

    # Budget / gate controls
    def test_has_max_gate_retries(self, engine) -> None:
        assert hasattr(engine, "_max_gate_retries")
        assert engine._max_gate_retries == 3  # default

    def test_has_enforce_token_budget(self, engine) -> None:
        assert hasattr(engine, "_enforce_token_budget")
        assert engine._enforce_token_budget is True  # default

    def test_has_token_budget(self, engine) -> None:
        assert hasattr(engine, "_token_budget")

    # Policy / override
    def test_has_policy_approved_steps(self, engine) -> None:
        assert hasattr(engine, "_policy_approved_steps")
        assert isinstance(engine._policy_approved_steps, set)

    def test_has_force_override(self, engine) -> None:
        assert hasattr(engine, "_force_override")
        assert engine._force_override is False  # default

    def test_has_override_justification(self, engine) -> None:
        assert hasattr(engine, "_override_justification")
        assert engine._override_justification == ""  # default
