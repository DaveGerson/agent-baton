"""Tests for _validate_macros and its integration into HeadlessClaude.__init__.

Coverage targets
----------------
- test_valid_macros_pass               — well-formed macros dict does not raise
- test_macros_not_dict_raises          — None / list / str / int all rejected
- test_macro_entry_missing_required_key_raises — entry dict missing 'command'
- test_macro_entry_empty_command_raises         — command is "" or whitespace-only
- test_empty_macros_dict_is_allowed    — {} is explicitly valid
- test_validation_runs_at_construction — bad macros raise at HeadlessClaude() time

No subprocess spawning occurs; shutil.which is monkeypatched where needed.
"""
from __future__ import annotations

import pytest

from agent_baton.core.runtime.headless import (
    HeadlessConfig,
    HeadlessClaude,
    _validate_macros,
)


# ---------------------------------------------------------------------------
# _validate_macros — direct unit tests
# ---------------------------------------------------------------------------


class TestValidateMacrosDirect:
    def test_valid_macros_pass(self) -> None:
        """A well-formed macros dict must not raise."""
        _validate_macros(
            {
                "lint": {"command": "ruff check ."},
                "test": {"command": "pytest", "description": "Run test suite"},
            }
        )

    def test_empty_macros_dict_is_allowed(self) -> None:
        """An empty dict is explicitly valid — macros are optional."""
        _validate_macros({})  # must not raise

    def test_macros_not_dict_raises_for_none(self) -> None:
        with pytest.raises(ValueError, match="macros must be a dict"):
            _validate_macros(None)  # type: ignore[arg-type]

    def test_macros_not_dict_raises_for_list(self) -> None:
        with pytest.raises(ValueError, match="macros must be a dict"):
            _validate_macros([{"command": "echo hi"}])  # type: ignore[arg-type]

    def test_macros_not_dict_raises_for_string(self) -> None:
        with pytest.raises(ValueError, match="macros must be a dict"):
            _validate_macros("echo hi")  # type: ignore[arg-type]

    def test_macros_not_dict_raises_for_int(self) -> None:
        with pytest.raises(ValueError, match="macros must be a dict"):
            _validate_macros(42)  # type: ignore[arg-type]

    def test_macro_entry_missing_required_key_raises(self) -> None:
        """Entry dict that omits 'command' must raise with the macro name."""
        with pytest.raises(ValueError, match="missing required key 'command'"):
            _validate_macros({"lint": {"description": "run linter"}})

    def test_macro_entry_empty_command_raises(self) -> None:
        """command="" must be rejected."""
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_macros({"lint": {"command": ""}})

    def test_macro_entry_whitespace_only_command_raises(self) -> None:
        """command="   " (whitespace only) must also be rejected."""
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_macros({"lint": {"command": "   "}})

    def test_macro_entry_non_string_command_raises(self) -> None:
        """command=123 (non-str) must be rejected."""
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_macros({"lint": {"command": 123}})  # type: ignore[dict-item]

    def test_macro_entry_not_a_dict_raises(self) -> None:
        """Entry that is not a dict (e.g. a string) must raise."""
        with pytest.raises(ValueError, match="entry must be a dict"):
            _validate_macros({"lint": "ruff check ."})  # type: ignore[dict-item]

    def test_error_message_includes_macro_name(self) -> None:
        """ValueError message must name the offending macro."""
        with pytest.raises(ValueError, match="my-macro"):
            _validate_macros({"my-macro": {}})

    def test_extra_keys_in_entry_are_tolerated(self) -> None:
        """Unknown keys alongside 'command' must not raise."""
        _validate_macros(
            {
                "deploy": {
                    "command": "bash deploy.sh",
                    "description": "Deploy to staging",
                    "tags": ["infra"],
                }
            }
        )


# ---------------------------------------------------------------------------
# Integration: validation fires at HeadlessClaude construction time
# ---------------------------------------------------------------------------


class TestValidationRunsAtConstruction:
    def test_validation_runs_at_construction_with_bad_macros(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HeadlessClaude.__init__ must raise ValueError before touching the CLI."""
        # Patch shutil.which so the binary check is irrelevant.
        monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
        bad_config = HeadlessConfig(macros={"oops": {}})  # missing 'command'
        with pytest.raises(ValueError, match="missing required key 'command'"):
            HeadlessClaude(config=bad_config)

    def test_valid_macros_do_not_block_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Good macros must not interfere with normal construction."""
        monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
        cfg = HeadlessConfig(macros={"lint": {"command": "ruff check ."}})
        hc = HeadlessClaude(config=cfg)
        assert hc.is_available is True

    def test_empty_macros_do_not_block_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default empty macros must not raise."""
        monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
        hc = HeadlessClaude()
        assert hc.is_available is True

    def test_none_macros_raises_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing None as macros must raise at construction, not later."""
        monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude")
        # Bypass dataclass field typing via object.__setattr__
        cfg = HeadlessConfig()
        object.__setattr__(cfg, "macros", None)
        with pytest.raises(ValueError, match="macros must be a dict"):
            HeadlessClaude(config=cfg)


# ---------------------------------------------------------------------------
# HeadlessConfig serialization roundtrip with macros
# ---------------------------------------------------------------------------


class TestHeadlessConfigMacrosRoundtrip:
    def test_macros_survives_to_dict_from_dict(self) -> None:
        macros = {"lint": {"command": "ruff check .", "description": "Lint"}}
        cfg = HeadlessConfig(macros=macros)
        data = cfg.to_dict()
        restored = HeadlessConfig.from_dict(data)
        assert restored.macros == macros

    def test_empty_macros_roundtrip(self) -> None:
        cfg = HeadlessConfig()
        restored = HeadlessConfig.from_dict(cfg.to_dict())
        assert restored.macros == {}

    def test_from_dict_missing_macros_key_defaults_to_empty(self) -> None:
        restored = HeadlessConfig.from_dict({})
        assert restored.macros == {}
