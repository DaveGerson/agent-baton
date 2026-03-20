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
# ---------------------------------------------------------------------------


class TestSpecValidationResultDataclass:
    def test_passed_all_pass(self):
        r = SpecValidationResult(
            checks=[
                SpecCheck("a", passed=True),
                SpecCheck("b", passed=True),
            ]
        )
        assert r.passed is True

    def test_passed_one_fail(self):
        r = SpecValidationResult(
            checks=[
                SpecCheck("a", passed=True),
                SpecCheck("b", passed=False),
            ]
        )
        assert r.passed is False

    def test_passed_empty_checks(self):
        r = SpecValidationResult()
        assert r.passed is False

    def test_summary_all_pass(self):
        r = SpecValidationResult(
            checks=[SpecCheck("a", True), SpecCheck("b", True)]
        )
        assert r.summary == "2/2 checks passed"

    def test_summary_partial(self):
        r = SpecValidationResult(
            checks=[SpecCheck("a", True), SpecCheck("b", False)]
        )
        assert r.summary == "1/2 checks passed"

    def test_summary_empty(self):
        r = SpecValidationResult()
        assert r.summary == "0/0 checks passed"


class TestToMarkdown:
    def test_returns_string(self):
        r = SpecValidationResult(
            checks=[SpecCheck("check-one", True), SpecCheck("check-two", False, message="bad")]
        )
        md = r.to_markdown()
        assert isinstance(md, str)

    def test_contains_pass_fail_labels(self):
        r = SpecValidationResult(
            checks=[SpecCheck("ok", True), SpecCheck("nope", False, message="missing")]
        )
        md = r.to_markdown()
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
    def test_valid_data_passes(self, tmp_path: Path, validator: SpecValidator):
        data = {"name": "my-app", "version": 1.0, "active": True, "status": "draft"}
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)

        result = validator.validate_json_against_schema(data_path, schema_path)
        assert result.passed

    def test_missing_required_field_fails(self, tmp_path: Path, validator: SpecValidator):
        data = {"name": "my-app"}  # missing 'version'
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)

        result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed
        failed_names = [c.name for c in result.checks if not c.passed]
        assert any("version" in n for n in failed_names)

    def test_wrong_type_fails(self, tmp_path: Path, validator: SpecValidator):
        data = {"name": 42, "version": 1.0}  # name should be a string
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)

        result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed
        failed_names = [c.name for c in result.checks if not c.passed]
        assert any("type" in n and "name" in n for n in failed_names)

    def test_enum_violation_fails(self, tmp_path: Path, validator: SpecValidator):
        data = {"name": "app", "version": 1, "status": "pending"}  # invalid enum
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)

        result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed
        failed_names = [c.name for c in result.checks if not c.passed]
        assert any("enum" in n for n in failed_names)

    def test_enum_valid_value_passes(self, tmp_path: Path, validator: SpecValidator):
        data = {"name": "app", "version": 1, "status": "published"}
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)

        result = validator.validate_json_against_schema(data_path, schema_path)
        assert result.passed

    def test_array_items_type_checked(self, tmp_path: Path, validator: SpecValidator):
        data = {"name": "app", "version": 2, "tags": ["good", 123]}  # 123 is not string
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)

        result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed

    def test_missing_data_file_fails(self, tmp_path: Path, validator: SpecValidator):
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
        result = validator.validate_json_against_schema(
            tmp_path / "nope.json", schema_path
        )
        assert not result.passed
        assert any("cannot read" in c.message for c in result.checks)

    def test_missing_schema_file_fails(self, tmp_path: Path, validator: SpecValidator):
        data_path = _write_json(tmp_path, "data.json", {"x": 1})
        result = validator.validate_json_against_schema(
            data_path, tmp_path / "nope_schema.json"
        )
        assert not result.passed

    def test_invalid_json_data_fails(self, tmp_path: Path, validator: SpecValidator):
        data_path = _write_text(tmp_path, "data.json", "not json {{{")
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
        result = validator.validate_json_against_schema(data_path, schema_path)
        assert not result.passed
        assert any("not valid JSON" in c.message for c in result.checks)

    def test_spec_path_set_to_schema(self, tmp_path: Path, validator: SpecValidator):
        data_path = _write_json(tmp_path, "data.json", {"name": "x", "version": 1})
        schema_path = _write_json(tmp_path, "schema.json", SIMPLE_SCHEMA)
        result = validator.validate_json_against_schema(data_path, schema_path)
        assert result.spec_path == schema_path

    def test_boolean_type(self, tmp_path: Path, validator: SpecValidator):
        schema = {
            "type": "object",
            "required": ["flag"],
            "properties": {"flag": {"type": "boolean"}},
        }
        data = {"flag": True}
        data_path = _write_json(tmp_path, "data.json", data)
        schema_path = _write_json(tmp_path, "schema.json", schema)
        result = validator.validate_json_against_schema(data_path, schema_path)
        assert result.passed

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
# ---------------------------------------------------------------------------


class TestValidateFileStructure:
    def test_all_files_exist_passes(self, tmp_path: Path, validator: SpecValidator):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "README.md").write_text("# hi")
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("pass")

        result = validator.validate_file_structure(
            tmp_path, ["app.py", "README.md", "src/main.py"]
        )
        assert result.passed
        assert len(result.checks) == 3

    def test_missing_file_fails(self, tmp_path: Path, validator: SpecValidator):
        (tmp_path / "exists.py").write_text("x = 1")

        result = validator.validate_file_structure(
            tmp_path, ["exists.py", "missing.py"]
        )
        assert not result.passed
        failed = [c for c in result.checks if not c.passed]
        assert len(failed) == 1
        assert "missing.py" in failed[0].name

    def test_empty_expected_list(self, tmp_path: Path, validator: SpecValidator):
        result = validator.validate_file_structure(tmp_path, [])
        assert result.checks == []
        # passed is False when there are no checks
        assert not result.passed

    def test_spec_path_set_to_root(self, tmp_path: Path, validator: SpecValidator):
        result = validator.validate_file_structure(tmp_path, [])
        assert result.spec_path == tmp_path


# ---------------------------------------------------------------------------
# validate_exports
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
    def test_finds_classes_and_functions(self, tmp_path: Path, validator: SpecValidator):
        mod = _write_text(tmp_path, "module.py", SAMPLE_MODULE)
        result = validator.validate_exports(
            mod, ["MyService", "public_func", "async_helper", "MY_CONSTANT"]
        )
        assert result.passed

    def test_missing_export_fails(self, tmp_path: Path, validator: SpecValidator):
        mod = _write_text(tmp_path, "module.py", SAMPLE_MODULE)
        result = validator.validate_exports(mod, ["MissingClass", "public_func"])
        assert not result.passed
        failed = [c for c in result.checks if not c.passed]
        assert len(failed) == 1
        assert "MissingClass" in failed[0].name

    def test_private_name_is_detectable(self, tmp_path: Path, validator: SpecValidator):
        mod = _write_text(tmp_path, "module.py", SAMPLE_MODULE)
        result = validator.validate_exports(mod, ["_PRIVATE"])
        assert result.passed

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
    def test_finds_functions(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"functions": ["create_user", "fetch_data"]}
        result = validator.validate_api_contract(impl, contract)
        assert result.passed

    def test_missing_function_fails(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"functions": ["create_user", "delete_user"]}
        result = validator.validate_api_contract(impl, contract)
        assert not result.passed
        failed = [c for c in result.checks if not c.passed]
        assert len(failed) == 1
        assert "delete_user" in failed[0].name

    def test_finds_classes(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"classes": ["UserRepository", "ProductService"]}
        result = validator.validate_api_contract(impl, contract)
        assert result.passed

    def test_missing_class_fails(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"classes": ["UserRepository", "OrderService"]}
        result = validator.validate_api_contract(impl, contract)
        assert not result.passed
        failed = [c for c in result.checks if not c.passed]
        assert any("OrderService" in c.name for c in failed)

    def test_finds_methods_on_classes(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"methods": {"UserRepository": ["get", "save"]}}
        result = validator.validate_api_contract(impl, contract)
        assert result.passed

    def test_missing_method_fails(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"methods": {"UserRepository": ["get", "delete"]}}
        result = validator.validate_api_contract(impl, contract)
        assert not result.passed
        failed = [c for c in result.checks if not c.passed]
        assert any("delete" in c.name for c in failed)

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

    def test_async_method_found(self, tmp_path: Path, validator: SpecValidator):
        impl = _write_text(tmp_path, "impl.py", IMPL_SOURCE)
        contract = {"methods": {"ProductService": ["list_products"]}}
        result = validator.validate_api_contract(impl, contract)
        assert result.passed

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
