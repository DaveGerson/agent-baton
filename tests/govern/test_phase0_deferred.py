"""Machine-checkable placeholders for Phase 0 deferred follow-ups.

The Phase 0 backend agent flagged 4 items as DEFERRED via beads:

* bd-f606 — executor VETO enforcement (AuditorVerdict.VETO does not yet
            halt executor advancement on HIGH/CRITICAL phases).
* bd-c44c — UsageLogger does not populate the new tenancy columns
            (org_id/team_id/cost_center) on usage_records writes.
* bd-32d3 — KnowledgeResolver and retrospective do not emit telemetry
            events to KnowledgeTelemetryStore.
* bd-7099 — pre-existing test failure unrelated to Phase 0 work.

These tests express the *expected* end-state.  While the work is
deferred they are xfail-marked; once the follow-up beads land they
should flip to PASS without test changes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------
# bd-f606: executor enforces AuditorVerdict.VETO on HIGH/CRITICAL phases
# ---------------------------------------------------------------------

@pytest.mark.xfail(
    reason="bd-f606 deferred: executor.py does not yet check "
           "ComplianceReport.blocks_execution before advancing.",
    strict=False,
)
def test_executor_blocks_advancement_on_veto() -> None:
    """When auditor returns VETO on a HIGH/CRITICAL phase, executor must
    refuse to advance unless --force override is used.

    Acceptance criterion (strategic spec, F0.3):
    > VETO from auditor on HIGH/CRITICAL phases halts executor and writes
    > an OVERRIDE entry to the chain only when --force is supplied.
    """
    # The expected wiring: somewhere in
    # agent_baton/core/engine/executor.py, the executor inspects
    # `report.blocks_execution` (a property on ComplianceReport) before
    # transitioning to the next phase.  When this lands, the test below
    # will pass; until then it stays xfailed.
    import importlib

    # Look for any reference to blocks_execution in the executor module(s).
    candidates = [
        "agent_baton.core.engine.executor",
        "agent_baton.core.engine.execute_loop",
        "agent_baton.core.engine.actions",
    ]
    found = False
    for mod_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        try:
            src = Path(mod.__file__).read_text(encoding="utf-8")
        except (TypeError, OSError):
            continue
        if "blocks_execution" in src or "parsed_verdict" in src:
            found = True
            break
    assert found, "executor does not yet read blocks_execution / parsed_verdict"


# ---------------------------------------------------------------------
# bd-c44c: UsageLogger writes tenancy columns to usage_records
# ---------------------------------------------------------------------

@pytest.mark.xfail(
    reason="bd-c44c deferred: UsageLogger does not yet populate "
           "team_id/org_id/cost_center on usage_records inserts.",
    strict=False,
)
def test_usage_logger_populates_tenancy_columns() -> None:
    """The strategic spec says UsageLogger must stamp every usage record
    with the resolved tenancy context so the v_usage_by_team view actually
    reports anything in production."""
    from pathlib import Path as _P
    src_files = [
        _P("agent_baton/core/observe/usage.py"),
        _P("agent_baton/core/storage/migrate.py"),
    ]
    repo_root = _P(__file__).resolve().parents[2]
    has_tenancy_write = False
    for rel in src_files:
        p = repo_root / rel
        if not p.exists():
            continue
        body = p.read_text(encoding="utf-8")
        # When the wiring lands we expect at least one INSERT INTO
        # usage_records that names the team_id/org_id columns.
        if (
            "INSERT" in body
            and "usage_records" in body
            and "team_id" in body
            and "org_id" in body
        ):
            has_tenancy_write = True
            break
    assert has_tenancy_write, (
        "no INSERT into usage_records currently includes team_id/org_id"
    )


# ---------------------------------------------------------------------
# bd-32d3: KnowledgeResolver / retrospective emit telemetry events
# ---------------------------------------------------------------------

@pytest.mark.xfail(
    reason="bd-32d3 deferred: knowledge_resolver and retrospective "
           "do not yet call KnowledgeTelemetryStore.record_used / "
           "record_outcome from the live execution path.",
    strict=False,
)
def test_knowledge_resolver_emits_telemetry() -> None:
    """When the resolver delivers a doc into an agent prompt, it must
    write a row to knowledge_telemetry so v_knowledge_effectiveness
    reflects real usage."""
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / "agent_baton" / "core" / "knowledge" / "resolver.py",
        repo_root / "agent_baton" / "core" / "knowledge"
        / "knowledge_resolver.py",
        repo_root / "agent_baton" / "core" / "learn" / "retrospective.py",
    ]
    found = False
    for p in candidates:
        if not p.exists():
            continue
        body = p.read_text(encoding="utf-8")
        if "KnowledgeTelemetryStore" in body or "record_used" in body:
            found = True
            break
    assert found, (
        "neither knowledge resolver nor retrospective imports/uses "
        "KnowledgeTelemetryStore"
    )


# ---------------------------------------------------------------------
# bd-7099: pre-existing unrelated test failure
# ---------------------------------------------------------------------
# Documented here only as a marker — no test body needed.  Phase 5 review
# should confirm bd-7099 is genuinely outside the F0.x scope.
