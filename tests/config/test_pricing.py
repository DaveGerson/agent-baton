"""Tests for ``agent_baton.core.config.pricing``.

Covers:
- Default PRICING dict contains all four families.
- blended() formula: 75% input + 25% output.
- normalise_family(): bare names, vendor IDs, fable, legacy suffix rule.
- get_pricing(): default returned when no override file present.
- get_pricing(): override file merges correctly.
- get_pricing(): invalid / malformed override file is tolerated (no raise).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_baton.core.config.pricing import (
    PRICING,
    ModelPrice,
    blended,
    get_pricing,
    normalise_family,
)


# ---------------------------------------------------------------------------
# PRICING defaults
# ---------------------------------------------------------------------------

class TestPricingDefaults:
    def test_all_four_families_present(self) -> None:
        for family in ("haiku", "sonnet", "opus", "fable"):
            assert family in PRICING, f"{family!r} missing from PRICING"

    def test_modelprices_are_positive(self) -> None:
        for family, mp in PRICING.items():
            assert mp.input_per_mtok > 0, f"{family} input <= 0"
            assert mp.output_per_mtok > 0, f"{family} output <= 0"

    def test_haiku_values(self) -> None:
        assert PRICING["haiku"].input_per_mtok == pytest.approx(1.00)
        assert PRICING["haiku"].output_per_mtok == pytest.approx(5.00)

    def test_sonnet_values(self) -> None:
        assert PRICING["sonnet"].input_per_mtok == pytest.approx(3.00)
        assert PRICING["sonnet"].output_per_mtok == pytest.approx(15.00)

    def test_opus_values(self) -> None:
        assert PRICING["opus"].input_per_mtok == pytest.approx(5.00)
        assert PRICING["opus"].output_per_mtok == pytest.approx(25.00)

    def test_fable_values(self) -> None:
        assert PRICING["fable"].input_per_mtok == pytest.approx(10.00)
        assert PRICING["fable"].output_per_mtok == pytest.approx(50.00)

    def test_modelprices_are_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            PRICING["haiku"].input_per_mtok = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# blended()
# ---------------------------------------------------------------------------

class TestBlended:
    @pytest.mark.parametrize(
        "family, expected",
        [
            ("haiku",  2.0),   # 0.75*1 + 0.25*5
            ("sonnet", 6.0),   # 0.75*3 + 0.25*15
            ("opus",   10.0),  # 0.75*5 + 0.25*25
            ("fable",  20.0),  # 0.75*10 + 0.25*50
        ],
    )
    def test_blended_formula(self, family: str, expected: float) -> None:
        assert blended(family) == pytest.approx(expected)

    def test_unknown_family_falls_back_to_sonnet(self) -> None:
        # Unknown family should default to sonnet pricing.
        result = blended("nonexistent-model-family")
        assert result == pytest.approx(blended("sonnet"))


# ---------------------------------------------------------------------------
# normalise_family()
# ---------------------------------------------------------------------------

class TestNormaliseFamily:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Bare family names
            ("haiku",  "haiku"),
            ("sonnet", "sonnet"),
            ("opus",   "opus"),
            ("fable",  "fable"),
            # Full vendor IDs
            ("claude-haiku-4-5",  "haiku"),
            ("claude-sonnet-4-6", "sonnet"),
            ("claude-opus-4-8",   "opus"),
            ("claude-fable-5",    "fable"),
            # Versioned bare names
            ("opus-4",  "opus"),
            ("fable-5", "fable"),
            # Legacy suffix rule (claude-N-M-<family>)
            ("claude-3-5-sonnet", "sonnet"),
            ("claude-3-haiku",    "haiku"),
            # Case-insensitive
            ("OPUS",   "opus"),
            ("HAIKU",  "haiku"),
            ("SONNET", "sonnet"),
            ("FABLE",  "fable"),
            # Composite IDs — leading family wins
            ("opus-via-haiku-router",    "opus"),
            ("haiku-via-opus-cache",     "haiku"),
            ("fable-via-sonnet-gateway", "fable"),
            # Dated alias
            ("claude-haiku-4-5-20251001", "haiku"),
        ],
    )
    def test_family_resolution(self, raw: str, expected: str) -> None:
        assert normalise_family(raw) == expected

    def test_empty_string_returns_sonnet(self) -> None:
        assert normalise_family("") == "sonnet"

    def test_unknown_returns_sonnet_with_warning(self, caplog) -> None:
        import logging
        caplog.set_level(logging.WARNING, logger="agent_baton.core.config.pricing")
        result = normalise_family("gpt-5-turbo-unknown")
        assert result == "sonnet"
        assert any("gpt-5-turbo-unknown" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# get_pricing() — no override file
# ---------------------------------------------------------------------------

class TestGetPricingDefaults:
    def test_returns_all_four_families(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        pricing = get_pricing()
        for family in ("haiku", "sonnet", "opus", "fable"):
            assert family in pricing

    def test_returns_base_values_when_no_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        pricing = get_pricing()
        assert pricing["haiku"].input_per_mtok == pytest.approx(1.00)
        assert pricing["sonnet"].input_per_mtok == pytest.approx(3.00)


# ---------------------------------------------------------------------------
# get_pricing() — override file merge
# ---------------------------------------------------------------------------

class TestGetPricingOverride:
    def test_override_single_family(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "pricing.json").write_text(
            json.dumps({"opus": {"input_per_mtok": 99.0, "output_per_mtok": 199.0}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        pricing = get_pricing()
        assert pricing["opus"].input_per_mtok == pytest.approx(99.0)
        assert pricing["opus"].output_per_mtok == pytest.approx(199.0)
        # Non-overridden families unchanged.
        assert pricing["haiku"].input_per_mtok == pytest.approx(1.00)

    def test_override_new_family(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "pricing.json").write_text(
            json.dumps({"custom-model": {"input_per_mtok": 7.0, "output_per_mtok": 21.0}}),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        pricing = get_pricing()
        assert "custom-model" in pricing
        assert pricing["custom-model"].input_per_mtok == pytest.approx(7.0)

    def test_missing_override_file_returns_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        # No .claude/pricing.json exists.
        pricing = get_pricing()
        assert pricing["sonnet"].input_per_mtok == pytest.approx(3.00)

    def test_invalid_json_tolerated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "pricing.json").write_text("this is not JSON {{{", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        # Must not raise — returns defaults.
        pricing = get_pricing()
        assert pricing["haiku"].input_per_mtok == pytest.approx(1.00)

    def test_non_object_json_tolerated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "pricing.json").write_text("[1, 2, 3]", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        pricing = get_pricing()
        assert pricing["sonnet"].input_per_mtok == pytest.approx(3.00)

    def test_invalid_entry_in_override_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "pricing.json").write_text(
            json.dumps({
                "haiku": "not-a-dict",
                "opus": {"input_per_mtok": 9.0, "output_per_mtok": 29.0},
            }),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        pricing = get_pricing()
        # haiku bad entry skipped → defaults remain.
        assert pricing["haiku"].input_per_mtok == pytest.approx(1.00)
        # opus good entry applied.
        assert pricing["opus"].input_per_mtok == pytest.approx(9.0)
