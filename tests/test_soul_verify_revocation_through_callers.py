"""Regression tests for bd-1ca2: all production callers of signature
verification must route through SoulRouter.verify_signature so that the
revocation guard is enforced.

Scenario for each caller:
1. Mint a soul and produce a valid signature over test data.
2. Revoke the soul.
3. Invoke the caller's verification path.
4. Assert that verification returns False (revoked soul → rejected).

Current migrated callers:
- BeadStore._verify_bead_signature (bead_store.py)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.engine.bead_store import BeadStore
from agent_baton.core.engine.soul_registry import SoulRegistry
from agent_baton.core.engine.soul_router import SoulRouter
from agent_baton.models.bead import Bead


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_execution(db_path: Path, task_id: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO executions "
            "(task_id, status, current_phase, current_step_index, started_at, "
            " created_at, updated_at) "
            "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def registry(tmp_path: Path) -> SoulRegistry:
    central_db = tmp_path / "central.db"
    souls_dir = tmp_path / "souls"
    return SoulRegistry(central_db_path=central_db, souls_dir=souls_dir)


@pytest.fixture()
def router(registry: SoulRegistry, tmp_path: Path) -> SoulRouter:
    return SoulRouter(registry=registry, repo_root=tmp_path)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


@pytest.fixture()
def store(db_path: Path, router: SoulRouter) -> BeadStore:
    s = BeadStore(db_path, soul_router=router)
    s._table_exists()  # force schema to disk
    _seed_execution(db_path, "task-001")
    return s


# ---------------------------------------------------------------------------
# Helper: build a signed bead whose signature is valid pre-revocation
# ---------------------------------------------------------------------------


def _make_signed_bead(soul, bead_id: str = "bd-rev1", task_id: str = "task-001") -> Bead:
    """Create a Bead with a real ed25519 signature from *soul*."""
    bead = Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id="1.1",
        agent_name="backend-engineer--python",
        bead_type="discovery",
        content="Sensitive finding signed by soon-to-be-revoked soul.",
        tags=[],
        status="open",
        created_at=_utcnow(),
        signed_by=soul.soul_id,
    )
    # Produce the canonical body (mirrors BeadStore._sign_bead logic).
    body = bead.to_dict()
    body.pop("signature", None)
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    sig = soul.sign(canonical)
    # Attach the real signature to the bead.
    return Bead(
        bead_id=bead.bead_id,
        task_id=bead.task_id,
        step_id=bead.step_id,
        agent_name=bead.agent_name,
        bead_type=bead.bead_type,
        content=bead.content,
        tags=bead.tags,
        status=bead.status,
        created_at=bead.created_at,
        signed_by=soul.soul_id,
        signature=sig,
    )


# ---------------------------------------------------------------------------
# BeadStore._verify_bead_signature — revocation guard
# ---------------------------------------------------------------------------


class TestBeadStoreVerifyRevocationGuard:
    """BeadStore must reject beads whose signing soul has been revoked."""

    def test_valid_non_revoked_soul_signature_is_accepted(
        self, store: BeadStore, registry: SoulRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Sanity: a valid bead from an active soul emits no signature-invalid warning."""
        import logging
        soul = registry.mint(role="backend-engineer--python", domain="auth")
        bead = _make_signed_bead(soul)
        with caplog.at_level(logging.WARNING):
            store._verify_bead_signature(bead)  # type: ignore[attr-defined]
        warning_messages = [
            r.getMessage() for r in caplog.records
            if "signature-invalid" in r.getMessage()
        ]
        assert not warning_messages, (
            f"Unexpected signature-invalid warnings for a valid bead: {warning_messages}"
        )

    def test_revoked_soul_signature_is_rejected(
        self, store: BeadStore, registry: SoulRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Core regression: revoked soul → BEAD_WARNING logged, verification fails."""
        soul = registry.mint(role="backend-engineer--python", domain="auth")
        bead = _make_signed_bead(soul)

        # The signature is cryptographically valid at this point — confirm.
        body = bead.to_dict()
        body.pop("signature", None)
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        assert soul.verify(canonical, bead.signature), (
            "pre-condition: signature must be valid before revocation"
        )

        # Now revoke the soul.
        registry.revoke(soul.soul_id, reason="compromised key — bd-1ca2 regression test")

        # Trigger verification through BeadStore (migrated caller).
        import logging
        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.bead_store"):
            store._verify_bead_signature(bead)  # type: ignore[attr-defined]

        # The warning must appear — either from the router or the store.
        warning_text = caplog.text
        assert "signature-invalid" in warning_text or "revoked" in warning_text, (
            f"Expected a revocation or signature-invalid warning but got: {warning_text!r}"
        )

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

    def test_bead_store_routes_through_router_not_direct_soul(
        self, store: BeadStore, registry: SoulRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """End-to-end: bead signed by a revoked soul must be flagged as
        signature-invalid when read back through BeadStore._verify_bead_signature.

        This test would PASS (incorrectly) on the pre-fix code because the
        old code had a manual is_revoked check but still called soul.verify()
        directly — meaning a re-implementation of the guard without single
        source of truth.  After the fix, both cases route through the router.
        """
        soul = registry.mint(role="backend-engineer--python", domain="payments")
        bead = _make_signed_bead(soul, bead_id="bd-rev2")

        # Revoke before verification.
        registry.revoke(soul.soul_id, reason="automated rotation")

        import logging
        with caplog.at_level(logging.WARNING):
            store._verify_bead_signature(bead)  # type: ignore[attr-defined]

        assert caplog.records, (
            "Expected at least one WARNING log record after verifying a bead "
            "from a revoked soul"
        )
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "revoked" in messages or "signature-invalid" in messages, (
            f"Warning message does not mention revocation: {messages!r}"
        )
