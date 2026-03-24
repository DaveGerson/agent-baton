"""Tests for agent_baton.core.spec_validator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.spec_validator import SpecCheck, SpecValidationResult, SpecValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(tmp_path: Path, name: str, data: object) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_text(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def validator() -> SpecValidator:
    return SpecValidator()


# ---------------------------------------------------------------------------
# SpecCheck / SpecValidationResult dataclass
# DECISION: Removed test_passed_empty_checks and test_summary_empty —
# both are trivial default/boundary checks that are covered by the parametrize
# below. Kept test_passed_all_pass / test_passed_one_fail as they directly
# exercise the `passed` property logic.
# ---------------------------------------------------------------------------


class TestSpecValidationResultDataclass:
    @pytest.mark.parametrize("checks,expected_passed", [
        ([SpecCheck("a", True), SpecCheck("b", True)], True),
        ([SpecCheck("a", True), SpecCheck("b", False)], False),
        ([], False),
    ])
    def test_passed_property(self, checks: list, expected_passed: bool):
        r = SpecValidationResult(checks=checks)
        assert r.passed is expected_passed

    @pytest.mark.parametrize("checks,expected_summary", [
        ([SpecCheck("a", True), SpecCheck("b", True)], "2/2 checks passed"),
        ([SpecCheck("a", True), SpecCheck("b", False)], "1/2 checks passed"),
        ([], "0/0 checks passed"),
    ])
    def test_summary_property(self, checks: list, expected_summary: str):
        r = SpecValidationResult(checks=checks)
        assert r.summary == expected_summary


class TestToMarkdown:
    def test_contains_pass_fail_labels(self):
        r = SpecValidationResult(
            checks=[SpecCheck("ok", True), SpecCheck("nope", False, message="missing")]
        )
        md = r.to_markdown()
        assert isinstance(md, str)
        assert "PASS" in md
        assert "FAIL" in md

    def test_contains_summary(self):
        r = SpecValidationResult(
            checks=[SpecCheck("a", True)]
        )
        md = r.to_markdown()
        assert "1/1 checks passed" in md

    def test_shows_spec_path(self, tmp_path: Path):
        p = tmp_path / "schema.json"
        r = SpecValidationResult(spec_path=p)
        md = r.to_markdown()
        assert "schema.json" in md

    def test_no_checks_message(self):
        r = SpecValidationResult()
        md = r.to_markdown()
        assert "No checks" in md

    def test_fail_details_shown(self):
        r = SpecValidationResult(
            checks=[
                SpecCheck("field-x", False, expected="string", actual="int", message="type mismatch")
            ]
        )
        md = r.to_markdown()
        assert "type mismatch" in md


# ---------------------------------------------------------------------------
# validate_json_against_schema
# DECISION: Merged 11 tests into 2 parametrized groups:
# - pass/fail by data content (7 tests → 1 parametrized)
# - file/IO errors (3 tests → 1 parametrized)
# test_spec_path_set_to_schema and test_integer_not_confused_with_boolean
# kept separate (non-obvious correctness properties worth highlighting).
# ---------------------------------------------------------------------------


SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["name", "version"],
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "number"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "active": {"type": "boolean"},
        "status": {"type": "string", "enum": ["draft", "published", "archived"]},
    },
}


class TestValidateJsonAgainstSchema:
    @pytest.mark.parametrize("data,should_pass,error_hint", [
        # valid data
        ({"name": "my-app", "version": 1.0, "active": True, "status": "draft"}, True, None),
        # valid enum value
        ({"name": "app", "version": 1, "status": "published"}, True, None),
        # valid boolean field
        ({"name": "app", "version": 1, "active": True}, True, None),
        # missing required field
        ({"name": "my-app"}, False, "version"),
        # wrong type for 'name'
        ({"name": 42, "version": 1.0}, False, "name"),
        # invalid enum value
        ({"name": "app", "version": 1, "status": "pending"}, False, "enum"),
        # array item wrong type
        ({"name": "app", "version": 2, "tags": ["good", 123]}, False, None),
    ])
    def test_schema_validation(
        self,
        tmp_path: Path,
        validator: SpecValidator,
        data: dict,
        should_pass: bool,
        error_hint: str | None,
    ):
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
        result = validator.validate_json_against_schema(data_path, schema_path)
        assert result.passed is should_pass
        if error_hint:
            failed_names = [c.name for c in result.checks if not c.passed]
            assert any(error_hint in n for n in failed_names)

    @pytest.mark.parametrize("missing_data,missing_schema,expected_msg", [
        (True, False, "cannot read"),
        (False, True, None),
        (False, False, "not valid JSON"),  # invalid JSON in data file
    ])
    def test_file_io_errors(
        self,
        tmp_path: Path,
        validator: SpecValidator,
        missing_data: bool,
        missing_schema: bool,
        expected_msg: str | None,
    ):
        if missing_data:
            schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
            result = validator.validate_json_against_schema(tmp_path / "nope.json", schema_path)
        elif missing_schema:
            data_path = _write_json(tmp_path, "data.json", {"x": 1})
            result = validator.validate_json_against_schema(data_path, tmp_path / "nope_schema.json")
        else:
            # invalid JSON data
            data_path = _write_text(tmp_path, "data.json", "not json {{{")
            schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
            result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed
        if expected_msg:
            assert any(expected_msg in c.message for c in result.checks)

    def test_spec_path_set_to_schema(self, tmp_path: Path, validator: SpecValidator):
        data_path = _write_json(tmp_path, "data.json", {"name": "x", "version": 1})
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
        result = validator.validate_json_against_schema(data_path, schema_path)
        assert result.spec_path == schema_path

    def test_integer_not_confused_with_boolean(self, tmp_path: Path, validator: SpecValidator):
        # bool is a subclass of int in Python; make sure we reject True where int is expected
        schema = {
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        }
        data = {"count": True}  # True is a bool, should NOT pass "integer" check
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", schema)
        result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# validate_file_structure
# DECISION: Merged 4 tests into 2 parametrized tests.
# test_spec_path_set_to_root kept because it checks a different property.
# ---------------------------------------------------------------------------


class TestValidateFileStructure:
    @pytest.mark.parametrize("create_files,expected_paths,should_pass,expected_fail_count", [
        # all files exist
        (["app.py", "README.md", "src/main.py"], ["app.py", "README.md", "src/main.py"], True, 0),
        # one file missing
        (["exists.py"], ["exists.py", "missing.py"], False, 1),
        # empty expected list
        ([], [], False, 0),
    ])
    def test_file_structure(
        self,
        tmp_path: Path,
        validator: SpecValidator,
        create_files: list[str],
        expected_paths: list[str],
        should_pass: bool,
        expected_fail_count: int,
    ):
        for f in create_files:
            p = tmp_path / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x = 1")
        result = validator.validate_file_structure(tmp_path, expected_paths)
        assert result.passed is should_pass
        if expected_fail_count > 0:
            failed = [c for c in result.checks if not c.passed]
            assert len(failed) == expected_fail_count

    def test_spec_path_set_to_root(self, tmp_path: Path, validator: SpecValidator):
        result = validator.validate_file_structure(tmp_path, [])
        assert result.spec_path == tmp_path


# ---------------------------------------------------------------------------
# validate_exports
# DECISION: Merged 5 tests into 2 parametrized tests.
# test_missing_module_fails kept separate (different code path: file read).
# test_empty_expected_list kept separate (boundary condition).
# ---------------------------------------------------------------------------


SAMPLE_MODULE = '''\
from __future__ import annotations

MY_CONSTANT = 42
_PRIVATE = "hidden"

def public_func(x: int) -> int:
    return x + 1

async def async_helper() -> None:
    pass

class MyService:
    pass

class _InternalHelper:
    pass
'''


class TestValidateExports:
    @pytest.mark.parametrize("symbols,should_pass,expected_fail", [
        (["MyService", "public_func", "async_helper", "MY_CONSTANT"], True, None),
        (["_PRIVATE"], True, None),
        (["MissingClass", "public_func"], False, "MissingClass"),
    ])
    def test_export_detection(
        self,
        tmp_path: Path,
        validator: SpecValidator,
        symbols: list[str],
        should_pass: bool,
        expected_fail: str | None,
    ):
        mod = _write_text(tmp_path, "module.py", SAMPLE_MODULE)
        result = validator.validate_exports(mod, symbols)
        assert result.passed is should_pass
        if expected_fail:
            failed = [c for c in result.checks if not c.passed]
            assert any(expected_fail in c.name for c in failed)

    def test_missing_module_fails(self, tmp_path: Path, validator: SpecValidator):
        result = validator.validate_exports(tmp_path / "no_such.py", ["foo"])
        assert not result.passed
        assert any("cannot read" in c.message for c in result.checks)

    def test_empty_expected_list(self, tmp_path: Path, validator: SpecValidator):
        mod = _write_text(tmp_path, "module.py", SAMPLE_MODULE)
        result = validator.validate_exports(mod, [])
        assert result.checks == []


# ---------------------------------------------------------------------------
# validate_api_contract
# DECISION: Merged 9 tests into 3 parametrized tests grouped by:
# - function/class detection (pass + fail cases)
# - method detection (pass + fail cases)
# test_combined_contract, test_missing_implementation_file_fails, and
# test_empty_contract kept separate (integration, error path, boundary).
# ---------------------------------------------------------------------------


IMPL_SOURCE = '''\
from __future__ import annotations


def create_user(name: str) -> dict:
    return {"name": name}


async def fetch_data(url: str) -> bytes:
    return b""


class UserRepository:
    def get(self, user_id: int) -> dict:
        return {}

    def save(self, user: dict) -> None:
        pass


class ProductService:
    async def list_products(self) -> list:
        return []
'''


class TestValidateApiContract:
    @pytest.mark.parametrize("contract,should_pass,expected_fail", [
        ({"functions": ["create_user", "fetch_data"]}, True, None),
        ({"functions": ["create_user", "delete_user"]}, False, "delete_user"),
        ({"classes": ["UserRepository", "ProductService"]}, True, None),
        ({"classes": ["UserRepository", "OrderService"]}, False, "OrderService"),
    ])
    def test_function_and_class_detection(
        self,
        tmp_path: Path,
        validator: SpecValidator,
        contract: dict,
        should_pass: bool,
        expected_fail: str | None,
    ):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        result = validator.validate_api_contract(impl, contract)
        assert result.passed is should_pass
        if expected_fail:
            failed = [c for c in result.checks if not c.passed]
            assert any(expected_fail in c.name for c in failed)

    @pytest.mark.parametrize("contract,should_pass,expected_fail", [
        ({"methods": {"UserRepository": ["get", "save"]}}, True, None),
        ({"methods": {"UserRepository": ["get", "delete"]}}, False, "delete"),
        ({"methods": {"ProductService": ["list_products"]}}, True, None),
    ])
    def test_method_detection(
        self,
        tmp_path: Path,
        validator: SpecValidator,
        contract: dict,
        should_pass: bool,
        expected_fail: str | None,
    ):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        result = validator.validate_api_contract(impl, contract)
        assert result.passed is should_pass
        if expected_fail:
            failed = [c for c in result.checks if not c.passed]
            assert any(expected_fail in c.name for c in failed)

    def test_combined_contract(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {
            "functions": ["create_user"],
            "classes": ["UserRepository"],
            "methods": {"UserRepository": ["get", "save"]},
        }
        result = validator.validate_api_contract(impl, contract)
        assert result.passed

    def test_missing_implementation_file_fails(self, tmp_path: Path, validator: SpecValidator):
        result = validator.validate_api_contract(tmp_path / "no_impl.py", {})
        assert not result.passed

    def test_empty_contract(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        result = validator.validate_api_contract(impl, {})
        assert result.checks == []


# ---------------------------------------------------------------------------
# run_gate
# ---------------------------------------------------------------------------


class TestRunGate:
    def test_all_pass(self, validator: SpecValidator):
        checks = [
            ("check-a", lambda: (True, "ok")),
            ("check-b", lambda: (True, "also ok")),
        ]
        result = validator.run_gate(checks)
        assert result.passed
        assert len(result.checks) == 2

    def test_one_fails(self, validator: SpecValidator):
        checks = [
            ("passes", lambda: (True, "fine")),
            ("fails", lambda: (False, "something went wrong")),
        ]
        result = validator.run_gate(checks)
        assert not result.passed
        assert result.checks[1].message == "something went wrong"

    def test_empty_checks(self, validator: SpecValidator):
        result = validator.run_gate([])
        assert result.checks == []
        assert not result.passed

    def test_exception_in_check_fails_gracefully(self, validator: SpecValidator):
        def bad_check():
            raise ValueError("something exploded")

        checks = [("boom", bad_check)]
        result = validator.run_gate(checks)
        assert not result.passed
        assert "something exploded" in result.checks[0].message

    def test_check_names_are_preserved(self, validator: SpecValidator):
        checks = [
            ("my-named-check", lambda: (True, "")),
        ]
        result = validator.run_gate(checks)
        assert result.checks[0].name == "my-named-check"

    def test_summary_reflects_outcomes(self, validator: SpecValidator):
        checks = [
            ("a", lambda: (True, "")),
            ("b", lambda: (True, "")),
            ("c", lambda: (False, "nope")),
        ]
        result = validator.run_gate(checks)
        assert result.summary == "2/3 checks passed"
