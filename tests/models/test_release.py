"""Tests for :class:`agent_baton.models.release.Release` (R3.1).

Coverage:
- Construction defaults: ``status='planned'``, empty target_date/notes/name.
- Auto-set ``created_at`` is ISO 8601 and stable when explicitly provided.
- ``to_dict`` round-trips through ``from_dict``.
- ``RELEASE_STATUSES`` exposes the documented lifecycle values.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from agent_baton.models.release import RELEASE_STATUSES, Release


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\+\d{2}:\d{2}|Z)?$")


class TestConstruction:
    def test_minimal_construction(self) -> None:
        rel = Release(release_id="v2.5.0")
        assert rel.release_id == "v2.5.0"
        assert rel.name == ""
        assert rel.target_date == ""
        assert rel.status == "planned"
        assert rel.notes == ""
        assert _ISO_RE.match(rel.created_at), rel.created_at

    def test_full_construction(self) -> None:
        rel = Release(
            release_id="2026-Q2-stability",
            name="Q2 Stability Release",
            target_date="2026-06-30",
            status="active",
            notes="Theme: reliability",
        )
        assert rel.release_id == "2026-Q2-stability"
        assert rel.name == "Q2 Stability Release"
        assert rel.target_date == "2026-06-30"
        assert rel.status == "active"
        assert rel.notes == "Theme: reliability"

    def test_explicit_created_at_preserved(self) -> None:
        ts = "2026-04-25T12:34:56+00:00"
        rel = Release(release_id="v1.0", created_at=ts)
        assert rel.created_at == ts

    def test_status_choices_documented(self) -> None:
        assert "planned" in RELEASE_STATUSES
        assert "active" in RELEASE_STATUSES
        assert "released" in RELEASE_STATUSES
        assert "cancelled" in RELEASE_STATUSES


class TestSerialization:
    def test_to_dict_keys(self) -> None:
        rel = Release(release_id="v1.0", name="initial")
        d = rel.to_dict()
        assert set(d.keys()) == {
            "release_id",
            "name",
            "target_date",
            "status",
            "notes",
            "created_at",
        }
        assert d["release_id"] == "v1.0"
        assert d["name"] == "initial"
        assert d["status"] == "planned"

    def test_round_trip(self) -> None:
        original = Release(
            release_id="v3.0",
            name="major",
            target_date="2026-12-01",
            status="released",
            notes="ship it",
            created_at="2026-04-25T10:00:00+00:00",
        )
        round_tripped = Release.from_dict(original.to_dict())
        assert round_tripped == original

    def test_from_dict_minimal(self) -> None:
        rel = Release.from_dict({"release_id": "v0.1"})
        assert rel.release_id == "v0.1"
        assert rel.name == ""
        assert rel.status == "planned"
        # created_at is auto-set when missing/empty
        assert rel.created_at != ""

    def test_from_dict_missing_id_raises(self) -> None:
        with pytest.raises(KeyError):
            Release.from_dict({"name": "no id"})


class TestStatusTransitions:
    """Status is just a string; the dataclass itself enforces nothing.
    Validity is enforced by ReleaseStore.update_status; tested there.
    Here we verify that all documented status values are assignable.
    """

    @pytest.mark.parametrize("status", list(RELEASE_STATUSES))
    def test_status_assignable(self, status: str) -> None:
        rel = Release(release_id="v1.0", status=status)
        assert rel.status == status
        # Round-trips intact
        assert Release.from_dict(rel.to_dict()).status == status

    def test_status_can_be_mutated(self) -> None:
        rel = Release(release_id="v1.0")
        assert rel.status == "planned"
        rel.status = "active"
        assert rel.status == "active"
        rel.status = "released"
        assert rel.status == "released"
