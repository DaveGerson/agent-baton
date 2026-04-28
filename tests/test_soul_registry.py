"""Tests for agent_baton.core.engine.soul_registry — SoulRegistry + AgentSoul.

Wave 6.1 Part B (bd-d975).

Coverage:
- ed25519 sign/verify round-trip
- sign/verify mismatch returns False (tampered data)
- soul minting: soul_id format, pubkey in central.db, privkey on disk (mode 0600)
- soul minting determinism: same role+domain+distinct pubkeys → different soul_ids
  (soul_id includes pubkey fingerprint, so two minted souls for same role+domain differ)
- get: returns soul after mint; returns None for unknown ID
- list_for_role: returns active souls for role; excludes retired, excludes revoked
- retire: sets retired_at; soul no longer in list_for_role
- retire with successor: records successor in notes
- revoke: sets revoked: prefix in notes; is_revoked == True
- revoked soul is excluded from list_for_role
- upsert_expertise / get_expertise round-trip
- federation: pubkey written to central.db and readable from a separate connection
- graceful degradation: get() returns None for missing soul
"""
from __future__ import annotations

import sqlite3
import stat
import json
from pathlib import Path

import pytest

from agent_baton.core.engine.soul_registry import AgentSoul, SoulRegistry, _role_slug, _domain_slug


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path: Path) -> SoulRegistry:
    """SoulRegistry pointing at a temp central.db and temp souls dir."""
    central_db = tmp_path / "central.db"
    souls_dir = tmp_path / "souls"
    return SoulRegistry(central_db_path=central_db, souls_dir=souls_dir)


@pytest.fixture()
def soul(registry: SoulRegistry) -> AgentSoul:
    """A freshly minted soul."""
    return registry.mint(role="code-reviewer", domain="auth", project="/proj/test")


# ---------------------------------------------------------------------------
# AgentSoul helpers
# ---------------------------------------------------------------------------


class TestAgentSoulSignVerify:
    def test_sign_verify_round_trip(self, registry: SoulRegistry):
        s = registry.mint("code-reviewer", "auth")
        data = b"canonical bead body"
        sig = s.sign(data)
        assert sig.startswith("ed25519:")
        assert s.verify(data, sig) is True

    def test_verify_tampered_data_returns_false(self, registry: SoulRegistry):
        s = registry.mint("code-reviewer", "auth")
        sig = s.sign(b"original")
        assert s.verify(b"tampered", sig) is False

    def test_verify_wrong_prefix_returns_false(self, registry: SoulRegistry):
        s = registry.mint("code-reviewer", "auth")
        sig = s.sign(b"data")
        bad_sig = "rsa:" + sig[len("ed25519:"):]
        assert s.verify(b"data", bad_sig) is False

    def test_verify_corrupted_base64_returns_false(self, registry: SoulRegistry):
        s = registry.mint("code-reviewer", "auth")
        assert s.verify(b"data", "ed25519:!!!invalid!!!") is False

    def test_sign_raises_when_privkey_missing(self, tmp_path: Path):
        central_db = tmp_path / "central.db"
        souls_dir = tmp_path / "souls"
        reg = SoulRegistry(central_db_path=central_db, souls_dir=souls_dir)
        s = reg.mint("code-reviewer", "auth")
        # Remove the private key to simulate a machine that doesn't hold it.
        assert s.privkey_path is not None
        s.privkey_path.unlink()
        with pytest.raises(RuntimeError, match="not available on this machine"):
            s.sign(b"data")

    def test_synthetic_email_format(self, soul: AgentSoul):
        email = soul.synthetic_email()
        assert email.endswith("@baton.local")
        assert soul.soul_id in email


# ---------------------------------------------------------------------------
# Soul identity format
# ---------------------------------------------------------------------------


class TestSoulIdFormat:
    def test_soul_id_contains_role_slug(self, soul: AgentSoul):
        assert "code_reviewer" in soul.soul_id

    def test_soul_id_contains_domain_slug(self, soul: AgentSoul):
        assert "auth" in soul.soul_id

    def test_soul_id_has_fingerprint_suffix(self, soul: AgentSoul):
        # soul_id format: <role>_<domain>_<fingerprint>
        parts = soul.soul_id.split("_")
        assert len(parts) >= 3
        # Last part is the fingerprint (hex chars).
        fingerprint = parts[-1]
        assert all(c in "0123456789abcdef" for c in fingerprint)
        assert len(fingerprint) >= 3

    def test_two_minted_souls_same_role_domain_have_different_ids(
        self, registry: SoulRegistry
    ):
        s1 = registry.mint("code-reviewer", "auth")
        s2 = registry.mint("code-reviewer", "auth")
        # Different keypairs → different fingerprints → different soul_ids.
        assert s1.soul_id != s2.soul_id

    def test_role_slug_normalisation(self):
        assert _role_slug("code-reviewer") == "code_reviewer"
        assert _role_slug("Backend Engineer") == "backend_engineer"

    def test_domain_slug_normalisation(self):
        assert _domain_slug("auth-service") == "auth_service"


# ---------------------------------------------------------------------------
# SoulRegistry.mint
# ---------------------------------------------------------------------------


class TestSoulRegistryMint:
    def test_mint_returns_agent_soul(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert isinstance(soul, AgentSoul)

    def test_mint_pubkey_is_32_bytes(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert len(soul.pubkey) == 32

    def test_mint_privkey_written_to_disk(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert soul.privkey_path is not None
        assert soul.privkey_path.exists()

    def test_mint_privkey_mode_0600(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert soul.privkey_path is not None
        mode = oct(soul.privkey_path.stat().st_mode & 0o777)
        assert mode == oct(0o600)

    def test_mint_privkey_is_32_bytes(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert soul.privkey_path is not None
        assert len(soul.privkey_path.read_bytes()) == 32

    def test_mint_writes_to_central_db(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth", project="/proj/test")
        # Verify via a fresh connection to central.db.
        conn = sqlite3.connect(str(registry._db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM agent_souls WHERE soul_id = ?", (soul.soul_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert bytes(row["pubkey"]) == soul.pubkey
        assert row["role"] == "code-reviewer"
        assert row["origin_project"] == "/proj/test"

    def test_mint_created_at_is_set(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert soul.created_at != ""

    def test_mint_new_soul_is_active(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        assert soul.is_active is True
        assert soul.is_revoked is False

    def test_mint_federation_pubkey_readable_from_separate_connection(
        self, registry: SoulRegistry
    ):
        """Federation check: pubkey in central.db is readable from a new connection."""
        soul = registry.mint("code-reviewer", "auth", project="test-project")
        # Open a brand-new connection (simulates machine B reading central.db).
        conn2 = sqlite3.connect(str(registry._db_path))
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT pubkey FROM agent_souls WHERE soul_id = ?", (soul.soul_id,)
        ).fetchone()
        conn2.close()
        assert row is not None
        assert bytes(row["pubkey"]) == soul.pubkey


# ---------------------------------------------------------------------------
# SoulRegistry.get
# ---------------------------------------------------------------------------


class TestSoulRegistryGet:
    def test_get_returns_minted_soul(self, registry: SoulRegistry, soul: AgentSoul):
        fetched = registry.get(soul.soul_id)
        assert fetched is not None
        assert fetched.soul_id == soul.soul_id
        assert fetched.pubkey == soul.pubkey

    def test_get_returns_none_for_unknown_id(self, registry: SoulRegistry):
        assert registry.get("nonexistent_soul_xyz") is None

    def test_get_reattaches_local_privkey(self, registry: SoulRegistry, soul: AgentSoul):
        # Delete the privkey_path from the row (simulate it being None in DB).
        conn = sqlite3.connect(str(registry._db_path))
        conn.execute(
            "UPDATE agent_souls SET privkey_path = NULL WHERE soul_id = ?",
            (soul.soul_id,),
        )
        conn.commit()
        conn.close()
        # get() should reattach the local privkey if it exists on disk.
        fetched = registry.get(soul.soul_id)
        assert fetched is not None
        # If the local file exists, privkey_path should be set.
        local = registry._privkey_path(soul.soul_id)
        if local.exists():
            assert fetched.privkey_path == local


# ---------------------------------------------------------------------------
# SoulRegistry.list_for_role
# ---------------------------------------------------------------------------


class TestSoulRegistryListForRole:
    def test_list_for_role_returns_active_souls(self, registry: SoulRegistry):
        s1 = registry.mint("code-reviewer", "auth")
        s2 = registry.mint("code-reviewer", "db")
        ids = {s.soul_id for s in registry.list_for_role("code-reviewer")}
        assert s1.soul_id in ids
        assert s2.soul_id in ids

    def test_list_for_role_excludes_retired(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        registry.retire(soul.soul_id)
        ids = {s.soul_id for s in registry.list_for_role("code-reviewer")}
        assert soul.soul_id not in ids

    def test_list_for_role_excludes_revoked(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        registry.revoke(soul.soul_id)
        ids = {s.soul_id for s in registry.list_for_role("code-reviewer")}
        assert soul.soul_id not in ids

    def test_list_for_role_returns_empty_for_unknown_role(self, registry: SoulRegistry):
        assert registry.list_for_role("nonexistent-role") == []

    def test_list_for_role_does_not_cross_roles(self, registry: SoulRegistry):
        registry.mint("code-reviewer", "auth")
        registry.mint("test-engineer", "auth")
        cr_souls = registry.list_for_role("code-reviewer")
        te_souls = registry.list_for_role("test-engineer")
        assert all(s.role == "code-reviewer" for s in cr_souls)
        assert all(s.role == "test-engineer" for s in te_souls)


# ---------------------------------------------------------------------------
# SoulRegistry.retire
# ---------------------------------------------------------------------------


class TestSoulRegistryRetire:
    def test_retire_sets_retired_at(self, registry: SoulRegistry, soul: AgentSoul):
        registry.retire(soul.soul_id)
        updated = registry.get(soul.soul_id)
        assert updated is not None
        assert updated.retired_at != ""
        assert updated.is_active is False

    def test_retire_with_successor_records_in_notes(self, registry: SoulRegistry):
        s1 = registry.mint("code-reviewer", "auth")
        s2 = registry.mint("code-reviewer", "auth")
        registry.retire(s1.soul_id, successor_id=s2.soul_id)
        updated = registry.get(s1.soul_id)
        assert updated is not None
        assert s2.soul_id in updated.notes

    def test_retire_nonexistent_soul_is_noop(self, registry: SoulRegistry):
        # Should not raise.
        registry.retire("nonexistent_soul_xyz")

    def test_retire_idempotent(self, registry: SoulRegistry, soul: AgentSoul):
        registry.retire(soul.soul_id)
        first_retired_at = registry.get(soul.soul_id).retired_at
        # Retiring again overwrites retired_at but should not crash.
        registry.retire(soul.soul_id)
        # Still retired.
        assert registry.get(soul.soul_id).retired_at != ""


# ---------------------------------------------------------------------------
# SoulRegistry.revoke
# ---------------------------------------------------------------------------


class TestSoulRegistryRevoke:
    def test_revoke_sets_revoked_prefix(self, registry: SoulRegistry, soul: AgentSoul):
        registry.revoke(soul.soul_id)
        updated = registry.get(soul.soul_id)
        assert updated is not None
        assert updated.is_revoked is True
        assert updated.notes.startswith("revoked:")

    def test_revoked_soul_is_not_active(self, registry: SoulRegistry, soul: AgentSoul):
        registry.revoke(soul.soul_id)
        updated = registry.get(soul.soul_id)
        assert updated.is_active is False

    def test_revoke_nonexistent_is_noop(self, registry: SoulRegistry):
        registry.revoke("nonexistent_soul_xyz")

    def test_revoke_preserves_existing_notes(self, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        # Manually set notes.
        conn = sqlite3.connect(str(registry._db_path))
        conn.execute(
            "UPDATE agent_souls SET notes = ? WHERE soul_id = ?",
            ("original note", soul.soul_id),
        )
        conn.commit()
        conn.close()
        registry.revoke(soul.soul_id)
        updated = registry.get(soul.soul_id)
        assert updated.is_revoked is True
        assert "original note" in updated.notes


# ---------------------------------------------------------------------------
# SoulRegistry expertise
# ---------------------------------------------------------------------------


class TestSoulRegistryExpertise:
    def test_upsert_and_get_expertise(self, registry: SoulRegistry, soul: AgentSoul):
        registry.upsert_expertise(soul.soul_id, "file", "agent_baton/auth.py", 0.75)
        rows = registry.get_expertise(soul.soul_id)
        assert len(rows) == 1
        assert rows[0]["ref"] == "agent_baton/auth.py"
        assert abs(rows[0]["weight"] - 0.75) < 1e-6

    def test_upsert_updates_existing_row(self, registry: SoulRegistry, soul: AgentSoul):
        registry.upsert_expertise(soul.soul_id, "file", "auth.py", 0.5)
        registry.upsert_expertise(soul.soul_id, "file", "auth.py", 0.9)
        rows = registry.get_expertise(soul.soul_id)
        assert len(rows) == 1
        assert abs(rows[0]["weight"] - 0.9) < 1e-6

    def test_get_expertise_empty_for_new_soul(
        self, registry: SoulRegistry, soul: AgentSoul
    ):
        assert registry.get_expertise(soul.soul_id) == []

    def test_expertise_ordered_by_weight_desc(
        self, registry: SoulRegistry, soul: AgentSoul
    ):
        registry.upsert_expertise(soul.soul_id, "file", "low.py", 0.1)
        registry.upsert_expertise(soul.soul_id, "file", "high.py", 0.9)
        registry.upsert_expertise(soul.soul_id, "file", "mid.py", 0.5)
        rows = registry.get_expertise(soul.soul_id)
        weights = [r["weight"] for r in rows]
        assert weights == sorted(weights, reverse=True)
