"""Unit tests for agent_baton.models.bead — Bead and BeadLink dataclasses.

Coverage:
- _generate_bead_id: prefix, progressive scaling thresholds, determinism, uniqueness
- BeadLink.to_dict / BeadLink.from_dict round-trip and backward-compatible defaults
- Bead.to_dict / Bead.from_dict round-trip for all fields
- Bead.from_dict graceful defaults for missing fields (backward compatibility)
- Bead with populated tags, affected_files, and links serialises correctly
"""
from __future__ import annotations

import pytest

from agent_baton.models.bead import Bead, BeadLink, _generate_bead_id


# ---------------------------------------------------------------------------
# _generate_bead_id — ID generation and progressive scaling
# ---------------------------------------------------------------------------


class TestGenerateBeadId:
    def test_id_has_bd_prefix(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 0)
        assert bead_id.startswith("bd-")

    def test_bead_count_below_500_produces_4_char_hash(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 0)
        # "bd-" is 3 chars, hash portion should be 4 chars
        hash_part = bead_id[len("bd-"):]
        assert len(hash_part) == 4

    def test_bead_count_499_still_produces_4_char_hash(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 499)
        hash_part = bead_id[len("bd-"):]
        assert len(hash_part) == 4

    def test_bead_count_500_produces_5_char_hash(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 500)
        hash_part = bead_id[len("bd-"):]
        assert len(hash_part) == 5

    def test_bead_count_1499_still_produces_5_char_hash(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 1499)
        hash_part = bead_id[len("bd-"):]
        assert len(hash_part) == 5

    def test_bead_count_1500_produces_6_char_hash(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 1500)
        hash_part = bead_id[len("bd-"):]
        assert len(hash_part) == 6

    def test_bead_count_9999_produces_6_char_hash(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 9999)
        hash_part = bead_id[len("bd-"):]
        assert len(hash_part) == 6

    def test_id_is_deterministic_for_same_inputs(self):
        args = ("task-abc", "1.1", "discovered JWT", "2026-01-01T00:00:00Z", 0)
        id1 = _generate_bead_id(*args)
        id2 = _generate_bead_id(*args)
        assert id1 == id2

    def test_different_content_produces_different_ids(self):
        id1 = _generate_bead_id("t1", "s1", "content A", "2026-01-01T00:00:00Z", 0)
        id2 = _generate_bead_id("t1", "s1", "content B", "2026-01-01T00:00:00Z", 0)
        assert id1 != id2

    def test_different_task_id_produces_different_ids(self):
        id1 = _generate_bead_id("task-1", "s1", "same content", "2026-01-01T00:00:00Z", 0)
        id2 = _generate_bead_id("task-2", "s1", "same content", "2026-01-01T00:00:00Z", 0)
        assert id1 != id2

    def test_id_contains_only_hex_chars_after_prefix(self):
        bead_id = _generate_bead_id("t1", "s1", "content", "2026-01-01T00:00:00Z", 0)
        hash_part = bead_id[len("bd-"):]
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_scaling_boundary_transitions_are_precise(self):
        """Confirm the exact thresholds: <500 -> 4, [500,1500) -> 5, >=1500 -> 6."""
        lengths = {}
        for count in [499, 500, 1499, 1500]:
            bead_id = _generate_bead_id("t1", "s1", "c", "ts", count)
            lengths[count] = len(bead_id[len("bd-"):])

        assert lengths[499] == 4
        assert lengths[500] == 5
        assert lengths[1499] == 5
        assert lengths[1500] == 6


# ---------------------------------------------------------------------------
# BeadLink — serialisation round-trip
# ---------------------------------------------------------------------------


class TestBeadLink:
    def _make_link(self, **kwargs) -> BeadLink:
        defaults = dict(
            target_bead_id="bd-abcd",
            link_type="relates_to",
            created_at="2026-01-01T00:00:00Z",
        )
        defaults.update(kwargs)
        return BeadLink(**defaults)

    def test_to_dict_contains_all_fields(self):
        link = self._make_link()
        d = link.to_dict()
        assert d["target_bead_id"] == "bd-abcd"
        assert d["link_type"] == "relates_to"
        assert d["created_at"] == "2026-01-01T00:00:00Z"

    def test_from_dict_round_trip(self):
        link = self._make_link(link_type="blocks")
        restored = BeadLink.from_dict(link.to_dict())
        assert restored.target_bead_id == link.target_bead_id
        assert restored.link_type == link.link_type
        assert restored.created_at == link.created_at

    def test_from_dict_defaults_link_type_when_missing(self):
        """Backward compatibility: link_type absent defaults to 'relates_to'."""
        d = {"target_bead_id": "bd-1234"}
        link = BeadLink.from_dict(d)
        assert link.link_type == "relates_to"

    def test_from_dict_defaults_created_at_when_missing(self):
        d = {"target_bead_id": "bd-1234", "link_type": "extends"}
        link = BeadLink.from_dict(d)
        assert link.created_at == ""

    def test_all_link_types_preserved(self):
        for link_type in ("blocks", "blocked_by", "relates_to",
                          "discovered_from", "validates", "contradicts", "extends"):
            link = BeadLink(target_bead_id="bd-x", link_type=link_type)
            restored = BeadLink.from_dict(link.to_dict())
            assert restored.link_type == link_type


# ---------------------------------------------------------------------------
# Bead — serialisation round-trip
# ---------------------------------------------------------------------------


class TestBeadToFromDict:
    def _make_bead(self, **kwargs) -> Bead:
        defaults = dict(
            bead_id="bd-a1b2",
            task_id="task-001",
            step_id="1.1",
            agent_name="backend-engineer--python",
            bead_type="discovery",
            content="The auth module uses JWT with RS256, not HS256.",
            confidence="high",
            scope="step",
            tags=["auth", "jwt"],
            affected_files=["auth.py"],
            status="open",
            created_at="2026-01-01T00:00:00Z",
            closed_at="",
            summary="",
            links=[],
            source="agent-signal",
            token_estimate=42,
        )
        defaults.update(kwargs)
        return Bead(**defaults)

    def test_to_dict_contains_all_required_keys(self):
        bead = self._make_bead()
        d = bead.to_dict()
        required = {
            "bead_id", "task_id", "step_id", "agent_name", "bead_type",
            "content", "confidence", "scope", "tags", "affected_files",
            "status", "created_at", "closed_at", "summary", "links",
            "source", "token_estimate",
        }
        assert required <= set(d.keys())

    def test_from_dict_round_trip_simple(self):
        bead = self._make_bead()
        restored = Bead.from_dict(bead.to_dict())
        assert restored.bead_id == bead.bead_id
        assert restored.task_id == bead.task_id
        assert restored.step_id == bead.step_id
        assert restored.agent_name == bead.agent_name
        assert restored.bead_type == bead.bead_type
        assert restored.content == bead.content
        assert restored.confidence == bead.confidence
        assert restored.scope == bead.scope
        assert restored.tags == bead.tags
        assert restored.affected_files == bead.affected_files
        assert restored.status == bead.status
        assert restored.created_at == bead.created_at
        assert restored.source == bead.source
        assert restored.token_estimate == bead.token_estimate

    def test_from_dict_round_trip_with_links(self):
        link = BeadLink(target_bead_id="bd-zz99", link_type="blocks",
                        created_at="2026-01-01T00:00:00Z")
        bead = self._make_bead(links=[link])
        restored = Bead.from_dict(bead.to_dict())
        assert len(restored.links) == 1
        assert restored.links[0].target_bead_id == "bd-zz99"
        assert restored.links[0].link_type == "blocks"

    def test_from_dict_round_trip_multiple_links(self):
        links = [
            BeadLink(target_bead_id="bd-aa01", link_type="relates_to"),
            BeadLink(target_bead_id="bd-bb02", link_type="contradicts"),
        ]
        bead = self._make_bead(links=links)
        restored = Bead.from_dict(bead.to_dict())
        assert len(restored.links) == 2
        types = {lnk.link_type for lnk in restored.links}
        assert types == {"relates_to", "contradicts"}

    def test_from_dict_defaults_bead_type_when_missing(self):
        d = {"bead_id": "bd-1111", "task_id": "t", "step_id": "s",
             "agent_name": "a", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.bead_type == "discovery"

    def test_from_dict_defaults_confidence_when_missing(self):
        d = {"bead_id": "bd-2222", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "warning", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.confidence == "medium"

    def test_from_dict_defaults_scope_when_missing(self):
        d = {"bead_id": "bd-3333", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "decision", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.scope == "step"

    def test_from_dict_defaults_tags_to_empty_list(self):
        d = {"bead_id": "bd-4444", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "discovery", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.tags == []

    def test_from_dict_defaults_affected_files_to_empty_list(self):
        d = {"bead_id": "bd-5555", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "discovery", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.affected_files == []

    def test_from_dict_defaults_links_to_empty_list(self):
        d = {"bead_id": "bd-6666", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "discovery", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.links == []

    def test_from_dict_defaults_source_to_agent_signal(self):
        d = {"bead_id": "bd-7777", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "discovery", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.source == "agent-signal"

    def test_from_dict_defaults_token_estimate_to_zero(self):
        d = {"bead_id": "bd-8888", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "discovery", "content": "c"}
        bead = Bead.from_dict(d)
        assert bead.token_estimate == 0

    def test_from_dict_coerces_token_estimate_to_int(self):
        d = {"bead_id": "bd-9999", "task_id": "t", "step_id": "s",
             "agent_name": "a", "bead_type": "discovery", "content": "c",
             "token_estimate": "128"}
        bead = Bead.from_dict(d)
        assert bead.token_estimate == 128
        assert isinstance(bead.token_estimate, int)

    def test_to_dict_links_are_list_of_dicts_not_objects(self):
        """Links in to_dict() must be plain dicts, not BeadLink objects."""
        link = BeadLink(target_bead_id="bd-cc03", link_type="extends")
        bead = self._make_bead(links=[link])
        d = bead.to_dict()
        assert isinstance(d["links"], list)
        assert isinstance(d["links"][0], dict)

    def test_bead_type_values_round_trip(self):
        for bead_type in ("discovery", "decision", "warning", "outcome", "planning"):
            bead = self._make_bead(bead_type=bead_type)
            restored = Bead.from_dict(bead.to_dict())
            assert restored.bead_type == bead_type

    def test_status_values_round_trip(self):
        for status in ("open", "closed", "archived"):
            bead = self._make_bead(status=status)
            restored = Bead.from_dict(bead.to_dict())
            assert restored.status == status

    def test_empty_content_preserved(self):
        bead = self._make_bead(content="")
        restored = Bead.from_dict(bead.to_dict())
        assert restored.content == ""
