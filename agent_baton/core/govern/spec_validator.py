"""Validate agent output against specifications (JSON Schema, file structure,
API contracts).

This module provides gate checks that verify agent-produced artifacts
match expected specifications. It supports four validation modes:

1. **JSON Schema validation** -- validate a JSON file against a JSON
   Schema document. Uses a built-in lightweight validator (no external
   ``jsonschema`` dependency) that checks types, required fields, enums,
   and nested structures. Does not support ``$ref``, ``allOf/anyOf/oneOf``,
   ``pattern``, or ``format``.

2. **File structure validation** -- verify that expected files exist under
   a root directory.

3. **Python export validation** -- verify that a Python module defines
   expected classes, functions, or variables by scanning the source text
   (no import required).

4. **API contract validation** -- verify that a Python file implements
   expected functions, classes, and methods by scanning definitions in
   the source text.

5. **Generic gate runner** -- execute arbitrary ``(name, callable)``
   check pairs where each callable returns ``(bool, message)``.

All validators produce ``SpecValidationResult`` objects containing
individual ``SpecCheck`` entries. The result is considered passing only
when every check passes.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SpecCheck:
    """Result of a single spec validation check."""

    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    message: str = ""


@dataclass
class SpecValidationResult:
    """Result of validating output against a spec."""

    spec_path: Path | None = None
    checks: list[SpecCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks) if self.checks else False

    @property
    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.passed)
        total = len(self.checks)
        return f"{passed}/{total} checks passed"

    def to_markdown(self) -> str:
        """Render the validation result as a markdown table."""
        lines: list[str] = []
        if self.spec_path:
            lines.append(f"## Spec Validation: `{self.spec_path}`")
        else:
            lines.append("## Spec Validation")
        lines.append("")
        lines.append(f"**Result**: {'PASSED' if self.passed else 'FAILED'}  ")
        lines.append(f"**Summary**: {self.summary}")
        lines.append("")
        if not self.checks:
            lines.append("_No checks were run._")
            return "\n".join(lines)

        lines.append("| Check | Status | Details |")
        lines.append("|-------|--------|---------|")
        for c in self.checks:
            status = "PASS" if c.passed else "FAIL"
            detail_parts: list[str] = []
            if c.message:
                detail_parts.append(c.message)
            if c.expected and not c.passed:
                detail_parts.append(f"expected: {c.expected}")
            if c.actual and not c.passed:
                detail_parts.append(f"actual: {c.actual}")
            detail = "; ".join(detail_parts) if detail_parts else ""
            lines.append(f"| {c.name} | {status} | {detail} |")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON Schema type names
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _check_type(value: object, type_name: str) -> bool:
    """Return True if *value* matches the JSON Schema *type_name*."""
    expected_type = _JSON_TYPE_MAP.get(type_name)
    if expected_type is None:
        return True  # unknown type — let it pass
    # JSON Schema: "number" includes integers
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected_type)  # type: ignore[arg-type]


def _validate_value_against_schema(
    value: object,
    schema: dict,
    path: str,
) -> list[SpecCheck]:
    """Recursively validate *value* against a (subset of) JSON Schema."""
    checks: list[SpecCheck] = []

    # type check
    type_name: str | None = schema.get("type")
    if type_name is not None:
        ok = _check_type(value, type_name)
        checks.append(
            SpecCheck(
                name=f"{path}: type",
                passed=ok,
                expected=type_name,
                actual=type(value).__name__,
                message="" if ok else f"expected type '{type_name}', got '{type(value).__name__}'",
            )
        )
        if not ok:
            # No point recursing if the type is wrong
            return checks

    # enum check
    enum_values: list | None = schema.get("enum")
    if enum_values is not None:
        ok = value in enum_values
        checks.append(
            SpecCheck(
                name=f"{path}: enum",
                passed=ok,
                expected=str(enum_values),
                actual=repr(value),
                message="" if ok else f"value {value!r} not in enum {enum_values}",
            )
        )

    # object: required + properties
    if isinstance(value, dict):
        required: list[str] = schema.get("required", [])
        for req_field in required:
            present = req_field in value
            checks.append(
                SpecCheck(
                    name=f"{path}.{req_field}: required",
                    passed=present,
                    expected="present",
                    actual="missing" if not present else "present",
                    message="" if present else f"required field '{req_field}' is missing",
                )
            )

        properties: dict = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            if prop_name in value:
                checks.extend(
                    _validate_value_against_schema(
                        value[prop_name], prop_schema, f"{path}.{prop_name}"
                    )
                )

    # array: items schema
    if isinstance(value, list):
        items_schema: dict | None = schema.get("items")
        if items_schema is not None:
            for idx, item in enumerate(value):
                checks.extend(
                    _validate_value_against_schema(item, items_schema, f"{path}[{idx}]")
                )

    return checks


class SpecValidator:
    """Validate files and structures against JSON Schema or custom specs.

    Provides multiple validation strategies (JSON Schema, file structure,
    Python exports, API contracts, and generic gates) that all produce
    uniform ``SpecValidationResult`` output. The validator is stateless
    and safe to reuse across multiple calls.
    """

    # ------------------------------------------------------------------
    # JSON Schema validation
    # ------------------------------------------------------------------

    def validate_json_against_schema(
        self, data_path: Path, schema_path: Path
    ) -> SpecValidationResult:
        """Validate a JSON file against a JSON Schema file.

        Uses basic structural validation (no jsonschema dependency):
        - Required fields present
        - Types match (string, number, array, object, boolean)
        - Enum values are valid
        Does NOT support: $ref, allOf/anyOf/oneOf, pattern, format
        """
        result = SpecValidationResult(spec_path=schema_path)

        # Load data file
        try:
            data_text = data_path.read_text(encoding="utf-8")
        except OSError as exc:
            result.checks.append(
                SpecCheck(
                    name="read data file",
                    passed=False,
                    message=f"cannot read '{data_path}': {exc}",
                )
            )
            return result

        try:
            data = json.loads(data_text)
        except json.JSONDecodeError as exc:
            result.checks.append(
                SpecCheck(
                    name="parse data file",
                    passed=False,
                    message=f"data file is not valid JSON: {exc}",
                )
            )
            return result

        # Load schema file
        try:
            schema_text = schema_path.read_text(encoding="utf-8")
        except OSError as exc:
            result.checks.append(
                SpecCheck(
                    name="read schema file",
                    passed=False,
                    message=f"cannot read schema '{schema_path}': {exc}",
                )
            )
            return result

        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as exc:
            result.checks.append(
                SpecCheck(
                    name="parse schema file",
                    passed=False,
                    message=f"schema file is not valid JSON: {exc}",
                )
            )
            return result

        result.checks.extend(
            _validate_value_against_schema(data, schema, "$")
        )
        return result

    # ------------------------------------------------------------------
    # File structure validation
    # ------------------------------------------------------------------

    def validate_file_structure(
        self, root: Path, expected_files: list[str]
    ) -> SpecValidationResult:
        """Validate that expected files exist under root."""
        result = SpecValidationResult(spec_path=root)

        for rel_path in expected_files:
            target = root / rel_path
            exists = target.exists()
            result.checks.append(
                SpecCheck(
                    name=f"exists: {rel_path}",
                    passed=exists,
                    expected="file exists",
                    actual="missing" if not exists else "exists",
                    message="" if exists else f"'{rel_path}' not found under '{root}'",
                )
            )

        return result

    # ------------------------------------------------------------------
    # Python export validation (text-based, no import)
    # ------------------------------------------------------------------

    # Patterns for top-level definitions in Python source
    _DEF_RE = re.compile(r"^(?:def|async def|class)\s+(\w+)", re.MULTILINE)
    _ASSIGN_RE = re.compile(r"^(\w+)\s*(?::\s*\S[^\n]*)?\s*=", re.MULTILINE)

    def validate_exports(
        self, module_path: Path, expected_names: list[str]
    ) -> SpecValidationResult:
        """Validate that a Python module exports expected names.

        Reads the file and checks for class/function/variable definitions
        matching expected_names. Does NOT import the module.
        """
        result = SpecValidationResult(spec_path=module_path)

        try:
            source = module_path.read_text(encoding="utf-8")
        except OSError as exc:
            result.checks.append(
                SpecCheck(
                    name="read module",
                    passed=False,
                    message=f"cannot read '{module_path}': {exc}",
                )
            )
            return result

        defined: set[str] = set()
        defined.update(m.group(1) for m in self._DEF_RE.finditer(source))
        defined.update(m.group(1) for m in self._ASSIGN_RE.finditer(source))

        for name in expected_names:
            found = name in defined
            result.checks.append(
                SpecCheck(
                    name=f"export: {name}",
                    passed=found,
                    expected="defined",
                    actual="missing" if not found else "defined",
                    message="" if found else f"'{name}' not found in '{module_path}'",
                )
            )

        return result

    # ------------------------------------------------------------------
    # API contract validation (text-based, no import)
    # ------------------------------------------------------------------

    def validate_api_contract(
        self, implementation_path: Path, contract: dict
    ) -> SpecValidationResult:
        """Validate that a Python file implements expected functions/classes.

        contract format::

            {
                "functions": ["func_name", ...],
                "classes": ["ClassName", ...],
                "methods": {"ClassName": ["method1", "method2"]}
            }

        Parses the file text (grep-based, not import-based) to check presence.
        """
        result = SpecValidationResult(spec_path=implementation_path)

        try:
            source = implementation_path.read_text(encoding="utf-8")
        except OSError as exc:
            result.checks.append(
                SpecCheck(
                    name="read implementation",
                    passed=False,
                    message=f"cannot read '{implementation_path}': {exc}",
                )
            )
            return result

        # Top-level functions
        func_re = re.compile(r"^(?:def|async def)\s+(\w+)\s*\(", re.MULTILINE)
        defined_functions: set[str] = {m.group(1) for m in func_re.finditer(source)}

        for func_name in contract.get("functions", []):
            found = func_name in defined_functions
            result.checks.append(
                SpecCheck(
                    name=f"function: {func_name}",
                    passed=found,
                    expected="defined",
                    actual="missing" if not found else "defined",
                    message="" if found else f"function '{func_name}' not found",
                )
            )

        # Top-level classes
        class_re = re.compile(r"^class\s+(\w+)\s*[:(]", re.MULTILINE)
        defined_classes: set[str] = {m.group(1) for m in class_re.finditer(source)}

        for class_name in contract.get("classes", []):
            found = class_name in defined_classes
            result.checks.append(
                SpecCheck(
                    name=f"class: {class_name}",
                    passed=found,
                    expected="defined",
                    actual="missing" if not found else "defined",
                    message="" if found else f"class '{class_name}' not found",
                )
            )

        # Methods: scan for `def method(` or `async def method(` anywhere
        # in the source (indented), scoped loosely after the class definition.
        method_re = re.compile(r"^\s+(?:def|async def)\s+(\w+)\s*\(", re.MULTILINE)
        defined_methods: set[str] = {m.group(1) for m in method_re.finditer(source)}

        for class_name, method_names in contract.get("methods", {}).items():
            for method_name in method_names:
                found = method_name in defined_methods
                result.checks.append(
                    SpecCheck(
                        name=f"method: {class_name}.{method_name}",
                        passed=found,
                        expected="defined",
                        actual="missing" if not found else "defined",
                        message="" if found else (
                            f"method '{method_name}' not found "
                            f"(expected on class '{class_name}')"
                        ),
                    )
                )

        return result

    # ------------------------------------------------------------------
    # Generic gate runner
    # ------------------------------------------------------------------

    def run_gate(
        self, checks: list[tuple[str, Callable[[], tuple[bool, str]]]]
    ) -> SpecValidationResult:
        """Run a list of named check functions.

        Each check is ``(name, callable)`` where the callable returns
        ``(bool, message)``.  This is the generic escape hatch for custom
        validations.
        """
        result = SpecValidationResult()

        for name, fn in checks:
            try:
                passed, message = fn()
                result.checks.append(
                    SpecCheck(name=name, passed=bool(passed), message=str(message))
                )
            except Exception as exc:  # noqa: BLE001
                result.checks.append(
                    SpecCheck(
                        name=name,
                        passed=False,
                        message=f"check raised exception: {exc}",
                    )
                )

        return result
