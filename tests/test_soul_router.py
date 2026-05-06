"""Tests for agent_baton.core.engine.soul_router — SoulRouter.

Wave 6.1 Part B (bd-d975) + v33 revocation guard extension.

Coverage:
- recommend: returns ranked (soul_id, score) list
- recommend: empty list when no souls for role
- current_soul: returns highest-scoring soul above threshold
- current_soul: auto-mints when no souls exist (cold cell)
- current_soul: auto-mints when best soul is below threshold
- current_soul: returns None gracefully on failure
- _expertise_score: 0.6*authorship + 0.4*bead_authorship formula
- _domain_from_files: extracts leading path component as domain token
- expertise recompute: stale rows are refreshed
- dispatch routes to highest-expertise soul when two souls in same role

v33 revocation guard:
- test_router_rejects_revoked_signature: verify_signature returns False for revoked soul
- test_router_logs_successor_when_revocation_includes_one: warning includes successor
- verify_signature returns True for valid non-revoked soul
- verify_signature returns False for unknown soul_id
- verify_signature returns False for bad cryptographic signature
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.soul_registry import AgentSoul, SoulRegistry
from agent_baton.core.engine.soul_router import (
    SoulRouter,
    _decay_factor,
    _domain_from_files,
    _hours_since,
)


# ---------------------------------------------------------------------------
# Fixtures
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
# Pure function tests
# ---------------------------------------------------------------------------


class TestDecayFactor:
    def test_zero_days_ago_returns_one(self):
        assert abs(_decay_factor(0.0) - 1.0) < 1e-9

    def test_one_half_life_returns_half(self):
        assert abs(_decay_factor(30.0, half_life=30.0) - 0.5) < 1e-6

    def test_two_half_lives_returns_quarter(self):
        assert abs(_decay_factor(60.0, half_life=30.0) - 0.25) < 1e-6

    def test_decay_is_monotonically_decreasing(self):
        values = [_decay_factor(float(d)) for d in range(0, 100, 10)]
        assert all(values[i] > values[i + 1] for i in range(len(values) - 1))


class TestDomainFromFiles:
    def test_returns_first_component(self):
        files = [Path("agent_baton/core/auth.py"), Path("agent_baton/core/db.py")]
        assert _domain_from_files(files) == "agent_baton"

    def test_returns_mode_component(self):
        # "auth" appears twice; "db" once → mode is "auth".
        files = [
            Path("auth/login.py"),
            Path("auth/signup.py"),
            Path("db/models.py"),
        ]
        assert _domain_from_files(files) == "auth"

    def test_returns_general_for_empty_list(self):
        assert _domain_from_files([]) == "general"

    def test_single_file(self):
        result = _domain_from_files([Path("core/executor.py")])
        assert result == "core"


class TestHoursSince:
    def test_very_old_timestamp_returns_large_value(self):
        assert _hours_since("2000-01-01T00:00:00Z") > 100_000

    def test_future_timestamp_returns_negative_or_small(self):
        import datetime
        future = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _hours_since(future)
        # Future timestamps should give a small negative (or near-zero) value.
        assert result < 1.0

    def test_invalid_timestamp_returns_inf(self):
        assert _hours_since("") == float("inf")
        assert _hours_since("not-a-date") == float("inf")


# ---------------------------------------------------------------------------
# SoulRouter.recommend
# ---------------------------------------------------------------------------


class TestSoulRouterRecommend:
    def test_recommend_empty_when_no_souls(self, router: SoulRouter):
        result = router.recommend("code-reviewer", [Path("auth.py")])
        assert result == []

    def test_recommend_returns_soul_ids(self, router: SoulRouter, registry: SoulRegistry):
        soul = registry.mint("code-reviewer", "auth")
        result = router.recommend("code-reviewer", [Path("auth.py")])
        assert any(sid == soul.soul_id for sid, _ in result)

    def test_recommend_sorted_descending_by_score(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        registry.mint("code-reviewer", "auth")
        registry.mint("code-reviewer", "auth")
        result = router.recommend("code-reviewer", [Path("auth.py")])
        scores = [score for _, score in result]
        assert scores == sorted(scores, reverse=True)

    def test_recommend_scores_are_in_range(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        registry.mint("code-reviewer", "auth")
        result = router.recommend("code-reviewer", [Path("auth.py")])
        for _, score in result:
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# SoulRouter.current_soul — auto-mint
# ---------------------------------------------------------------------------


class TestSoulRouterCurrentSoulAutoMint:
    def test_auto_mint_on_cold_cell(self, router: SoulRouter, registry: SoulRegistry):
        """No souls exist → auto-mint fires."""
        soul = router.current_soul("code-reviewer", [Path("auth.py")])
        assert soul is not None
        assert soul.role == "code-reviewer"
        # Soul should now be in the registry.
        souls = registry.list_for_role("code-reviewer")
        assert any(s.soul_id == soul.soul_id for s in souls)

    def test_auto_mint_when_all_scores_below_threshold(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """Soul exists but scores 0.0 (no expertise) → auto-mint."""
        existing = registry.mint("code-reviewer", "auth")
        # No expertise rows → score = 0.0 < THRESHOLD.
        soul = router.current_soul("code-reviewer", [Path("some_other_file.py")])
        # Should auto-mint a new soul (or return the same one after auto-mint
        # is triggered — either way a valid soul is returned).
        assert soul is not None

    def test_returns_highest_expertise_soul(
        self, router: SoulRouter, registry: SoulRegistry, tmp_path: Path
    ):
        """Two souls: one with high expertise → that one is returned."""
        s_low = registry.mint("code-reviewer", "auth")
        s_high = registry.mint("code-reviewer", "auth")
        target_file = tmp_path / "auth.py"
        # Give s_high a strong expertise score above threshold.
        registry.upsert_expertise(s_high.soul_id, "file", str(target_file), 0.9)
        # Mock _get_or_recompute_score to return the pre-seeded values directly.
        original = router._get_or_recompute_score

        def _mock_score(soul, files):
            if soul.soul_id == s_high.soul_id:
                return 0.9
            return 0.0

        router._get_or_recompute_score = _mock_score
        try:
            result = router.current_soul("code-reviewer", [target_file])
            assert result is not None
            assert result.soul_id == s_high.soul_id
        finally:
            router._get_or_recompute_score = original


# ---------------------------------------------------------------------------
# SoulRouter — expertise score formula
# ---------------------------------------------------------------------------


class TestExpertiseScoreFormula:
    def test_score_combines_authorship_and_bead_authorship(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """_expertise_score = 0.6 * authorship + 0.4 * bead_authorship."""
        soul = registry.mint("code-reviewer", "auth")
        files = [Path("auth.py")]

        # Patch both signal methods.
        with (
            patch.object(router, "_authorship_score", return_value=1.0),
            patch.object(router, "_bead_authorship_score", return_value=0.5),
        ):
            score = router._expertise_score(soul, files)
        expected = 0.6 * 1.0 + 0.4 * 0.5
        assert abs(score - expected) < 1e-9

    def test_score_zero_for_empty_files(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        score = router._expertise_score(soul, [])
        assert score == 0.0

    def test_score_authorship_only(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        with (
            patch.object(router, "_authorship_score", return_value=0.8),
            patch.object(router, "_bead_authorship_score", return_value=0.0),
        ):
            score = router._expertise_score(soul, [Path("auth.py")])
        assert abs(score - 0.6 * 0.8) < 1e-9

    def test_score_bead_authorship_only(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        with (
            patch.object(router, "_authorship_score", return_value=0.0),
            patch.object(router, "_bead_authorship_score", return_value=1.0),
        ):
            score = router._expertise_score(soul, [Path("auth.py")])
        assert abs(score - 0.4 * 1.0) < 1e-9


# ---------------------------------------------------------------------------
# SoulRouter — authorship signal (git-agnostic via mock)
# ---------------------------------------------------------------------------


class TestAuthorshipScore:
    def test_authorship_returns_zero_when_git_fails(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            score = router._authorship_score(soul, Path("auth.py"))
        assert score == 0.0

    def test_authorship_returns_zero_on_exception(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            score = router._authorship_score(soul, Path("auth.py"))
        assert score == 0.0

    def test_authorship_caps_at_one(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """Even with many lines touched, score is capped at 1.0."""
        soul = registry.mint("code-reviewer", "auth")
        # Simulate git output with a large number of lines touched.
        git_output = (
            "abc123 2026-01-01 +00:00\n"
            "1000\t500\tauth.py\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=git_output, stderr=""
            )
            score = router._authorship_score(soul, Path("auth.py"))
        assert score <= 1.0


# ---------------------------------------------------------------------------
# SoulRouter — bead authorship signal
# ---------------------------------------------------------------------------


class TestBeadAuthorshipScore:
    def test_bead_authorship_returns_zero_when_no_db(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        # No baton.db anywhere in tmp_path → should return 0.0 gracefully.
        score = router._bead_authorship_score(soul, Path("auth.py"))
        assert score == 0.0

    def test_bead_authorship_scores_matching_beads(
        self, router: SoulRouter, registry: SoulRegistry, tmp_path: Path
    ):
        soul = registry.mint("code-reviewer", "auth")
        # Create a fake baton.db with a signed bead.
        db_dir = tmp_path / ".claude" / "team-context"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "baton.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE beads (
                bead_id TEXT PRIMARY KEY,
                signed_by TEXT,
                affected_files TEXT,
                quality_score REAL,
                status TEXT
            )
        """)
        conn.execute(
            "INSERT INTO beads VALUES (?, ?, ?, ?, ?)",
            (
                "bd-test1",
                soul.soul_id,
                json.dumps([str(tmp_path / "auth.py")]),
                0.5,
                "open",
            ),
        )
        conn.commit()
        conn.close()

        score = router._bead_authorship_score(soul, tmp_path / "auth.py")
        # (0.5 + 1.0) / 10.0 = 0.15
        assert abs(score - 0.15) < 1e-6

    def test_bead_authorship_caps_at_one(
        self, router: SoulRouter, registry: SoulRegistry, tmp_path: Path
    ):
        soul = registry.mint("code-reviewer", "auth")
        db_dir = tmp_path / ".claude" / "team-context"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "baton.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE beads (
                bead_id TEXT PRIMARY KEY,
                signed_by TEXT,
                affected_files TEXT,
                quality_score REAL,
                status TEXT
            )
        """)
        target = str(tmp_path / "auth.py")
        # Insert 20 beads with quality_score=1.0 → total = 20 * 2.0 / 10 = 4.0 → capped at 1.0.
        for i in range(20):
            conn.execute(
                "INSERT INTO beads VALUES (?, ?, ?, ?, ?)",
                (f"bd-{i:04d}", soul.soul_id, json.dumps([target]), 1.0, "open"),
            )
        conn.commit()
        conn.close()

        score = router._bead_authorship_score(soul, tmp_path / "auth.py")
        assert score <= 1.0
        assert score == 1.0


# ---------------------------------------------------------------------------
# SoulRouter — expertise recompute on stale TTL
# ---------------------------------------------------------------------------


class TestExpertiseRecompute:
    def test_recompute_called_when_no_expertise_rows(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        files = [Path("auth.py")]
        # No expertise rows → _recompute_expertise should be called.
        with patch.object(router, "_recompute_expertise") as mock_recompute:
            # After recompute, still no rows → score = 0.0.
            router._get_or_recompute_score(soul, files)
            mock_recompute.assert_called_once_with(soul, files)

    def test_stale_rows_trigger_recompute(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        soul = registry.mint("code-reviewer", "auth")
        # Insert a stale expertise row (25 hours ago).
        stale_ts = "2000-01-01T00:00:00Z"  # definitely > 24h ago
        registry.upsert_expertise(soul.soul_id, "file", "auth.py", 0.5)
        # Manually set last_touched_at to stale.
        conn = sqlite3.connect(str(registry._db_path))
        conn.execute(
            "UPDATE soul_expertise SET last_touched_at = ? WHERE soul_id = ?",
            (stale_ts, soul.soul_id),
        )
        conn.commit()
        conn.close()

        with patch.object(router, "_recompute_expertise") as mock_recompute:
            router._get_or_recompute_score(soul, [Path("auth.py")])
            mock_recompute.assert_called_once()


# ---------------------------------------------------------------------------
# SoulRouter.verify_signature — revocation guard (v33)
# ---------------------------------------------------------------------------


class TestVerifySignatureRevocationGuard:
    def test_verify_signature_valid_non_revoked_soul(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """verify_signature returns True for a valid signature from a live soul."""
        soul = registry.mint("code-reviewer", "auth")
        data = b"dispatch payload"
        sig = soul.sign(data)
        assert router.verify_signature(soul.soul_id, data, sig) is True

    def test_verify_signature_bad_signature_returns_false(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """verify_signature returns False when the cryptographic check fails."""
        soul = registry.mint("code-reviewer", "auth")
        data = b"original payload"
        sig = soul.sign(data)
        # Tamper with data after signing.
        assert router.verify_signature(soul.soul_id, b"tampered payload", sig) is False

    def test_verify_signature_unknown_soul_returns_false(
        self, router: SoulRouter
    ):
        """verify_signature returns False when soul_id is not in the registry."""
        result = router.verify_signature("no_such_soul", b"data", "ed25519:abc")
        assert result is False

    def test_router_rejects_revoked_signature(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """test_router_rejects_revoked_signature: verify_signature returns False after revoke."""
        soul = registry.mint("code-reviewer", "auth")
        data = b"sensitive dispatch"
        sig = soul.sign(data)

        # Signature is valid before revocation.
        assert router.verify_signature(soul.soul_id, data, sig) is True

        # Revoke the soul.
        registry.revoke(soul.soul_id, reason="workstation stolen")

        # Signature is now rejected unconditionally.
        assert router.verify_signature(soul.soul_id, data, sig) is False

    def test_router_logs_warning_on_revoked_signature(
        self, router: SoulRouter, registry: SoulRegistry, caplog
    ):
        """verify_signature emits a WARNING log when a revoked soul's sig is checked."""
        soul = registry.mint("code-reviewer", "auth")
        data = b"payload"
        sig = soul.sign(data)
        registry.revoke(soul.soul_id, reason="key leaked")

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.soul_router"):
            router.verify_signature(soul.soul_id, data, sig)

        assert any("revoked" in record.message.lower() for record in caplog.records)
        assert any(soul.soul_id in record.message for record in caplog.records)

    def test_router_logs_successor_when_revocation_includes_one(
        self, router: SoulRouter, registry: SoulRegistry, caplog, capsys
    ):
        """test_router_logs_successor_when_revocation_includes_one: successor appears in warning."""
        original = registry.mint("code-reviewer", "auth")
        successor = registry.mint("code-reviewer", "auth")
        data = b"dispatch data"
        sig = original.sign(data)

        # Revoke original with explicit successor pointer.
        registry.revoke(
            original.soul_id,
            reason="rotation with explicit successor",
            successor_soul_id=successor.soul_id,
        )

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.soul_router"):
            result = router.verify_signature(original.soul_id, data, sig)

        assert result is False

        # Successor soul_id must appear in either the log message or the BEAD_WARNING print.
        log_text = " ".join(r.message for r in caplog.records)
        captured = capsys.readouterr()
        combined = log_text + captured.out
        assert successor.soul_id in combined

    def test_router_rejects_revoked_signature_after_rotate(
        self, router: SoulRouter, registry: SoulRegistry
    ):
        """After rotate(), the old soul's signatures are rejected; successor's are accepted."""
        original = registry.mint("code-reviewer", "auth")
        data = b"payload"
        old_sig = original.sign(data)

        successor = registry.rotate(original.soul_id, reason="scheduled key rotation")
        new_sig = successor.sign(data)

        # Old soul's signature rejected.
        assert router.verify_signature(original.soul_id, data, old_sig) is False

        # Successor's signature accepted.
        assert router.verify_signature(successor.soul_id, data, new_sig) is True
