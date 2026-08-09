"""Fixtures for ``tests/workflow`` — thin wrappers over ``_plans.py``.

The builders themselves live in :mod:`tests.workflow._plans` (a plain module,
not this conftest) so ``tests/cli`` can import them without reaching into a
conftest. See that module for what each base plan is designed to pin.
"""
from __future__ import annotations

import pytest

from agent_baton.models.execution import MachinePlan

from tests.workflow._plans import (
    build_audit_carryover_plan,
    build_auditor_in_implement_phase_plan,
    build_automation_step_plan,
    build_base_plan,
    build_build_gate_plan,
    build_developing_fallback_plan,
    build_gateless_plan,
    build_harvest_predicate_plan,
    build_no_implementation_plan,
    build_team_step_plan,
)


@pytest.fixture
def base_plan() -> MachinePlan:
    return build_base_plan()


@pytest.fixture
def team_step_plan() -> MachinePlan:
    return build_team_step_plan()


@pytest.fixture
def harvest_predicate_plan() -> MachinePlan:
    return build_harvest_predicate_plan()


@pytest.fixture
def developing_fallback_plan() -> MachinePlan:
    return build_developing_fallback_plan()


@pytest.fixture
def no_implementation_plan() -> MachinePlan:
    return build_no_implementation_plan()


@pytest.fixture
def audit_carryover_plan() -> MachinePlan:
    return build_audit_carryover_plan()


@pytest.fixture
def automation_step_plan() -> MachinePlan:
    return build_automation_step_plan()


@pytest.fixture
def gateless_plan() -> MachinePlan:
    return build_gateless_plan()


@pytest.fixture
def auditor_in_implement_phase_plan() -> MachinePlan:
    return build_auditor_in_implement_phase_plan()


@pytest.fixture
def build_gate_plan() -> MachinePlan:
    return build_build_gate_plan()
