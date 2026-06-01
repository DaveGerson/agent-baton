"""Regression tests for bd-1ca2: production callers of signature verification
must route through SoulRouter.verify_signature so that the revocation guard is
enforced.

ADR-13b WP-G: BeadStore (SQLite) and its _verify_bead_signature method were
deleted.  The remaining tests cover SoulRouter and SoulRegistry behaviour which
is backend-agnostic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.soul_registry import SoulRegistry
from agent_baton.core.engine.soul_router import SoulRouter


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path: Path) -> SoulRegistry:
    central_db = tmp_path / "central.db"
    souls_dir = tmp_path / "souls"
    return SoulRegistry(central_db_path=central_db, souls_dir=souls_dir)


@pytest.fixture()
def router(registry: SoulRegistry, tmp_path: Path) -> SoulRouter:
    return SoulRouter(registry=registry, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# SoulRegistry / direct-verify bypass (the original bd-1ca2 regression target)
# ---------------------------------------------------------------------------


class TestRevocationViaRegistryAndRouter:
    """soul.verify() ignores revocation; only SoulRouter.verify_signature() enforces it."""

    def test_revoked_soul_direct_verify_still_passes_cryptographically(
        self, registry: SoulRegistry
    ) -> None:
        """Demonstrates the bug that bd-1ca2 fixes: soul.verify() alone does
        NOT check revocation, so the guard must live in the router layer."""
        soul = registry.mint(role="backend-engineer--python", domain="auth")
        data = b"sensitive payload"
        sig = soul.sign(data)

        registry.revoke(soul.soul_id, reason="test revocation")

        # Re-fetch so is_revoked is set.
        refreshed = registry.get(soul.soul_id)
        assert refreshed is not None
        assert refreshed.is_revoked, "soul must be marked revoked after registry.revoke()"

        # Direct soul.verify() passes cryptographically — this is the bypass.
        assert refreshed.verify(data, sig) is True, (
            "soul.verify() is crypto-only and passes even for revoked souls — "
            "this is why callers must use router.verify_signature() instead"
        )

    def test_router_verify_signature_blocks_revoked_soul(
        self, router: SoulRouter, registry: SoulRegistry
    ) -> None:
        """router.verify_signature() must return False for a revoked soul
        even when the cryptographic signature is valid."""
        soul = registry.mint(role="backend-engineer--python", domain="auth")
        data = b"test payload"
        sig = soul.sign(data)

        registry.revoke(soul.soul_id, reason="key compromise")

        result = router.verify_signature(soul.soul_id, data, sig)
        assert result is False, (
            "SoulRouter.verify_signature must return False for a revoked soul"
        )

    def test_non_revoked_soul_signature_accepted_by_router(
        self, router: SoulRouter, registry: SoulRegistry
    ) -> None:
        """Sanity: a valid, non-revoked soul's signature is accepted."""
        soul = registry.mint(role="test-engineer", domain="ci")
        data = b"some payload"
        sig = soul.sign(data)

        result = router.verify_signature(soul.soul_id, data, sig)
        assert result is True, (
            "SoulRouter.verify_signature must accept a valid, non-revoked signature"
        )

    def test_verify_with_unknown_soul_id_returns_false(
        self, router: SoulRouter
    ) -> None:
        """An unrecognised soul_id must not blow up — return False."""
        result = router.verify_signature("soul-does-not-exist", b"data", "fakesig")
        assert result is False
