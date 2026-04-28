"""L2.1 retirement smoke tests (bd-362f).

Asserts that the deprecated ``PromptEvolutionEngine`` and
``ExperimentManager`` classes -- and their underlying modules -- are
no longer importable, and that no surviving Python source file in the
``agent_baton`` package still references the names.

Replacement guidance (for the curious): the prompt-evolution responsibility
moved to the ``learning-analyst`` agent dispatched via ``baton learn
run-cycle``; experiment-style impact validation now flows through that
same learning-cycle pipeline (before/after scorecard comparison).
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import agent_baton


_DEPRECATED_NAMES = ("PromptEvolutionEngine", "ExperimentManager")
_DEPRECATED_MODULES = (
    "agent_baton.core.improve.evolution",
    "agent_baton.core.improve.experiments",
    "agent_baton.cli.commands.improve.evolve",
    "agent_baton.cli.commands.improve.experiment",
)


@pytest.mark.parametrize("module_path", _DEPRECATED_MODULES)
def test_deprecated_modules_not_importable(module_path: str) -> None:
    """Each retired module must raise ``ModuleNotFoundError`` on import."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_path)


@pytest.mark.parametrize("name", _DEPRECATED_NAMES)
def test_deprecated_names_not_exported_from_improve_package(name: str) -> None:
    """The improve sub-package must not re-export the retired classes."""
    from agent_baton.core import improve

    assert not hasattr(improve, name), (
        f"agent_baton.core.improve still exposes {name!r}; "
        "it should have been removed by L2.1 (bd-362f)."
    )


def test_no_python_file_in_package_uses_retired_names_in_code() -> None:
    """No ``.py`` file under the ``agent_baton`` package may *use* the
    retired class names in executable code (imports, instantiations,
    attribute access).  Docstring/comment mentions that explicitly call
    out the L2.1 retirement (lines containing the word ``retired``) are
    permitted -- they're how we point readers at the replacement.

    Backward-compatible parameter aliases like ``experiment_manager``
    (lowercase, snake_case) are NOT covered -- only the class identifiers."""
    pkg_root = Path(agent_baton.__file__).resolve().parent
    name_pattern = re.compile(r"\b(?:" + "|".join(_DEPRECATED_NAMES) + r")\b")

    offenders: list[tuple[str, int, str]] = []
    for py_path in pkg_root.rglob("*.py"):
        try:
            text = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not name_pattern.search(line):
                continue
            # Permit lines that explicitly document the retirement.
            if "retired" in line.lower() or "deprecated" in line.lower():
                continue
            offenders.append(
                (str(py_path.relative_to(pkg_root.parent)), lineno, line.strip())
            )

    assert offenders == [], (
        "The following package files still use retired L2.1 names "
        f"({', '.join(_DEPRECATED_NAMES)}) outside of retirement docstrings:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in offenders)
    )
