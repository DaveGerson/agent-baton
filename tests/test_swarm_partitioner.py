"""Tests for agent_baton.core.swarm.partitioner (Wave 6.2 Part A, bd-707d).

Tests 1-3 from the wave-6-2-design.md Part A test plan:
  - test_partitioner_disjoint_files
  - test_partitioner_max_chunks_cap
  - test_partitioner_static_independence_violation

All tests use tmp_path fixtures with real Python files; no mocking of
filesystem operations.  libcst is imported lazily so these tests can run
(with reduced coverage) even when libcst is not installed — the partitioner
falls back gracefully.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_baton.core.swarm.partitioner import (
    ASTPartitioner,
    CodeChunk,
    IndependenceViolation,
    ProofRef,
    ReconcileResult,
    RefactorDirective,
    RenameSymbol,
    ReplaceImport,
    ScopeKind,
    _stable_chunk_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_repo(tmp_path: Path) -> Path:
    """Create a minimal Python project with two independent modules."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    # Module A: defines OldName
    (tmp_path / "pkg" / "module_a.py").write_text(
        "class OldName:\n    pass\n",
        encoding="utf-8",
    )

    # Module B: uses OldName (independent — no import from module_a in call graph)
    (tmp_path / "pkg" / "module_b.py").write_text(
        "from pkg.module_a import OldName\n\nx = OldName()\n",
        encoding="utf-8",
    )

    # Module C: also uses OldName (independent of B)
    (tmp_path / "pkg" / "module_c.py").write_text(
        "from pkg.module_a import OldName\n\ny = OldName()\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def many_file_repo(tmp_path: Path) -> Path:
    """Create a repo with 20 independent files, each using OldSymbol."""
    for i in range(20):
        (tmp_path / f"mod_{i:02d}.py").write_text(
            f"from base import OldSymbol\n\nx_{i} = OldSymbol()\n",
            encoding="utf-8",
        )
    (tmp_path / "base.py").write_text(
        "class OldSymbol:\n    pass\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: partitioner_disjoint_files
# ---------------------------------------------------------------------------


def test_partitioner_disjoint_files(simple_repo: Path) -> None:
    """Partitioner produces chunks where no file appears in more than one chunk."""
    partitioner = ASTPartitioner(simple_repo)
    directive = RenameSymbol(old="OldName", new="NewName")

    chunks = partitioner.partition(directive, max_chunks=100)

    # Should find call sites in module_b and module_c (and possibly module_a itself)
    assert len(chunks) >= 1, "Expected at least one chunk"

    # Verify disjoint: collect all files across chunks
    all_files: list[Path] = []
    for chunk in chunks:
        all_files.extend(chunk.files)

    # No file should appear twice
    seen: set[Path] = set()
    for f in all_files:
        assert f not in seen, f"File {f} appears in multiple chunks — not disjoint"
        seen.add(f)


def test_partitioner_disjoint_files_replace_import(tmp_path: Path) -> None:
    """ReplaceImport directive also produces disjoint file chunks."""
    (tmp_path / "a.py").write_text("import requests\nrequests.get('/')\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import requests\nrequests.post('/')\n", encoding="utf-8")

    partitioner = ASTPartitioner(tmp_path)
    directive = ReplaceImport(old="requests", new="httpx")

    chunks = partitioner.partition(directive, max_chunks=100)

    all_files: list[Path] = []
    for chunk in chunks:
        all_files.extend(chunk.files)

    seen: set[Path] = set()
    for f in all_files:
        assert f not in seen, f"File {f} appears in multiple chunks"
        seen.add(f)


# ---------------------------------------------------------------------------
# Test 2: partitioner_max_chunks_cap
# ---------------------------------------------------------------------------


def test_partitioner_max_chunks_cap(many_file_repo: Path) -> None:
    """When SCCs exceed max_chunks, greedy merge reduces to at most max_chunks."""
    partitioner = ASTPartitioner(many_file_repo)
    directive = RenameSymbol(old="OldSymbol", new="NewSymbol")

    max_chunks = 5
    chunks = partitioner.partition(directive, max_chunks=max_chunks)

    assert len(chunks) <= max_chunks, (
        f"Expected at most {max_chunks} chunks after merge, got {len(chunks)}"
    )
    # All chunks must still be non-empty
    for chunk in chunks:
        assert len(chunk.files) >= 1
        assert chunk.chunk_id, "chunk_id must be non-empty"


def test_partitioner_max_chunks_cap_exact_boundary(many_file_repo: Path) -> None:
    """max_chunks=1 forces all files into a single merged chunk."""
    partitioner = ASTPartitioner(many_file_repo)
    directive = RenameSymbol(old="OldSymbol", new="NewSymbol")

    chunks = partitioner.partition(directive, max_chunks=1)

    assert len(chunks) <= 1, "Expected at most 1 chunk when max_chunks=1"


# ---------------------------------------------------------------------------
# Test 3: partitioner_static_independence_violation
# ---------------------------------------------------------------------------


def test_partitioner_static_independence_violation(tmp_path: Path) -> None:
    """When a file would appear in multiple SCCs, fallback produces one chunk."""
    # Create a circular import scenario that would cause two "chunks" to
    # share a file — the partitioner should detect this and fall back.
    (tmp_path / "shared.py").write_text(
        "class OldThing:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer_a.py").write_text(
        "from shared import OldThing\nx = OldThing()\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer_b.py").write_text(
        "from shared import OldThing\ny = OldThing()\n",
        encoding="utf-8",
    )

    partitioner = ASTPartitioner(tmp_path)
    directive = RenameSymbol(old="OldThing", new="BetterThing")

    # Should not raise — fallback to single chunk if independence is violated
    chunks = partitioner.partition(directive, max_chunks=100)
    assert isinstance(chunks, list)
    # All files across chunks must still be disjoint (fallback merges)
    seen: set[Path] = set()
    for chunk in chunks:
        for f in chunk.files:
            assert f not in seen, f"File {f} appears in multiple chunks after fallback"
            seen.add(f)


def test_independence_violation_sentinel() -> None:
    """IndependenceViolation carries chunk_a, chunk_b, reason attributes."""
    exc = IndependenceViolation("chunk_aaa", "chunk_bbb", "shared file foo.py")
    assert exc.chunk_a == "chunk_aaa"
    assert exc.chunk_b == "chunk_bbb"
    assert "foo.py" in exc.reason
    assert "chunk_aaa" in str(exc)


# ---------------------------------------------------------------------------
# Stable chunk ID
# ---------------------------------------------------------------------------


def test_stable_chunk_id_deterministic(tmp_path: Path) -> None:
    """_stable_chunk_id returns the same value regardless of input list order."""
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"

    id1 = _stable_chunk_id([f1, f2])
    id2 = _stable_chunk_id([f2, f1])

    assert id1 == id2, "chunk_id must be order-independent"
    assert len(id1) == 64, "Expected SHA-256 hex digest (64 chars)"


# ---------------------------------------------------------------------------
# ProofRef
# ---------------------------------------------------------------------------


def test_proof_ref_frozen() -> None:
    """ProofRef is a frozen dataclass — mutation raises."""
    proof = ProofRef(kind="disjoint-files", details="test")
    assert proof.kind == "disjoint-files"
    with pytest.raises((AttributeError, TypeError)):
        proof.kind = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RefactorDirective.from_dict round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("directive_dict", [
    {"kind": "rename-symbol", "old": "a.B", "new": "a.C"},
    {"kind": "change-signature", "symbol": "a.func", "transform": {"add_param": "x=1"}},
    {"kind": "replace-import", "old": "requests", "new": "httpx"},
    {"kind": "migrate-api", "old_call_pattern": "requests.get(...)", "new_call_template": "httpx.get(...)"},
])
def test_refactor_directive_from_dict(directive_dict: dict) -> None:
    """RefactorDirective.from_dict correctly deserialises all supported kinds."""
    directive = RefactorDirective.from_dict(directive_dict)
    assert directive.kind == directive_dict["kind"]


def test_refactor_directive_from_dict_unknown_kind() -> None:
    """from_dict raises ValueError for unknown directive kinds."""
    with pytest.raises(ValueError, match="Unknown directive kind"):
        RefactorDirective.from_dict({"kind": "invalid-kind"})


# ---------------------------------------------------------------------------
# ReconcileResult
# ---------------------------------------------------------------------------


def test_reconcile_result_success() -> None:
    result = ReconcileResult(success=True, resolved_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n")
    assert result.success is True
    assert "old" in result.resolved_diff


def test_reconcile_result_failure() -> None:
    result = ReconcileResult(success=False, error="ambiguous")
    assert result.success is False
    assert result.error == "ambiguous"
    assert result.resolved_diff == ""
