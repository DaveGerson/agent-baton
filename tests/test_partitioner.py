"""Tests for parse-failure warning bead emission in ASTPartitioner.

End-user readiness concern #9: when libcst fails to parse a file (e.g. due
to a mid-edit syntax error) the partitioner previously fell back silently to
a single chunk, defeating parallelism without any signal to the developer.

These tests verify:
1. A warning bead is emitted on parse failure (BeadStore is called).
2. The single-chunk fallback still works — semantic preservation.
3. No warning bead is emitted for a clean, parseable file.
4. The bead content includes both the file path and the libcst error message.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

pytest.importorskip("libcst")

from agent_baton.core.swarm.partitioner import ASTPartitioner, RenameSymbol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal Python snippet that trips libcst's parser but is otherwise valid text.
_BROKEN_SOURCE = "def f(:\n    pass\n"

# A well-formed Python file that references the rename target.
_CLEAN_SOURCE = "class OldName:\n    pass\n"


def _make_repo_with_broken_file(tmp_path: Path) -> Path:
    """Create a minimal repo where one file has a syntax error."""
    # Good file — has the symbol being renamed
    (tmp_path / "good.py").write_text(_CLEAN_SOURCE, encoding="utf-8")
    # Broken file — libcst cannot parse it
    (tmp_path / "broken.py").write_text(_BROKEN_SOURCE, encoding="utf-8")
    return tmp_path


def _make_mock_bead_store() -> MagicMock:
    """Return a MagicMock that mimics BeadStore.write()."""
    store = MagicMock()
    store.write = MagicMock(return_value="bd-test")
    return store


# ---------------------------------------------------------------------------
# Test 1: warning bead is emitted on parse failure
# ---------------------------------------------------------------------------


def test_partition_emits_warning_bead_on_parse_failure(tmp_path: Path) -> None:
    """A parse failure must trigger exactly one BeadStore.write() call."""
    _make_repo_with_broken_file(tmp_path)
    store = _make_mock_bead_store()

    partitioner = ASTPartitioner(tmp_path, bead_store=store)
    partitioner.partition(RenameSymbol(old="OldName", new="NewName"))

    # BeadStore.write must have been called at least once (for the broken file)
    assert store.write.called, (
        "Expected BeadStore.write() to be called for the broken file, "
        "but it was never invoked."
    )

    # Inspect the bead that was written
    written_bead = store.write.call_args[0][0]
    assert written_bead.bead_type == "warning", (
        f"Expected bead_type='warning', got {written_bead.bead_type!r}"
    )
    assert "partitioner" in written_bead.tags
    assert "parse-failure" in written_bead.tags
    assert "parallelism-loss" in written_bead.tags


# ---------------------------------------------------------------------------
# Test 2: single-chunk fallback still works (semantic preservation)
# ---------------------------------------------------------------------------


def test_partition_falls_back_to_single_chunk_on_parse_failure(tmp_path: Path) -> None:
    """After a parse failure the partitioner must still return usable chunks.

    The broken file itself produces no call sites (it cannot be parsed), but
    the good file that contains ``OldName`` should still be partitioned.
    The overall result must be a non-empty list of chunks.
    """
    _make_repo_with_broken_file(tmp_path)
    store = _make_mock_bead_store()

    partitioner = ASTPartitioner(tmp_path, bead_store=store)
    chunks = partitioner.partition(RenameSymbol(old="OldName", new="NewName"))

    # The good file should still yield at least one chunk
    assert isinstance(chunks, list), "partition() must always return a list"
    assert len(chunks) >= 1, (
        "Expected at least one chunk from the parseable file; "
        "the broken file should not prevent the good file from being partitioned."
    )

    # All chunks must have at least one file
    for chunk in chunks:
        assert len(chunk.files) >= 1, "Every chunk must reference at least one file"

    # The broken file must NOT appear in any chunk (it could not be parsed)
    broken = (tmp_path / "broken.py").resolve()
    all_chunk_files = {f.resolve() for chunk in chunks for f in chunk.files}
    assert broken not in all_chunk_files, (
        "The broken file should not appear in any chunk because it could not be parsed."
    )


# ---------------------------------------------------------------------------
# Test 3: no warning bead on a clean parse
# ---------------------------------------------------------------------------


def test_partition_silent_on_clean_parse(tmp_path: Path) -> None:
    """No warning bead should be emitted when all files parse successfully."""
    # Write two parseable files
    (tmp_path / "a.py").write_text(_CLEAN_SOURCE, encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "from a import OldName\nx = OldName()\n", encoding="utf-8"
    )
    store = _make_mock_bead_store()

    partitioner = ASTPartitioner(tmp_path, bead_store=store)
    partitioner.partition(RenameSymbol(old="OldName", new="NewName"))

    # BeadStore.write should not have been called for a warning bead
    for write_call in store.write.call_args_list:
        bead = write_call[0][0]
        assert bead.bead_type != "warning" or "parse-failure" not in bead.tags, (
            "A parse-failure warning bead must not be emitted for a clean file."
        )


# ---------------------------------------------------------------------------
# Test 4: bead content includes file path and libcst error message
# ---------------------------------------------------------------------------


def test_partition_warning_includes_file_path_and_error(tmp_path: Path) -> None:
    """The warning bead's content must include the broken file's path and the error."""
    _make_repo_with_broken_file(tmp_path)
    store = _make_mock_bead_store()

    partitioner = ASTPartitioner(tmp_path, bead_store=store)
    partitioner.partition(RenameSymbol(old="OldName", new="NewName"))

    assert store.write.called, "Expected BeadStore.write() to be called"

    # Find the parse-failure warning bead
    parse_failure_beads = [
        write_call[0][0]
        for write_call in store.write.call_args_list
        if "parse-failure" in (write_call[0][0].tags if write_call[0][0].tags else [])
    ]
    assert len(parse_failure_beads) >= 1, (
        "Expected at least one parse-failure bead to be written"
    )

    bead = parse_failure_beads[0]

    # Content must mention the broken file's absolute path
    broken_abs = str((tmp_path / "broken.py").resolve())
    assert broken_abs in bead.content, (
        f"Expected broken file path {broken_abs!r} in bead content, "
        f"but content was: {bead.content!r}"
    )

    # Content must include some portion of the libcst syntax error message.
    # libcst raises ParserSyntaxError whose str() mentions "Syntax Error" or
    # "error at" — we check that the content is not a bare path-only message.
    assert len(bead.content) > len(broken_abs) + 20, (
        "Bead content appears to contain only the file path — "
        "it must also include the libcst error message."
    )

    # The affected_files list must also reference the broken file
    assert any(
        broken_abs in af for af in bead.affected_files
    ), (
        f"Expected {broken_abs!r} in bead.affected_files, "
        f"got {bead.affected_files!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 (bonus): deduplication — second call for same file emits only one bead
# ---------------------------------------------------------------------------


def test_partition_warning_bead_emitted_at_most_once_per_file(tmp_path: Path) -> None:
    """Calling partition() twice must not double-emit for the same broken file."""
    _make_repo_with_broken_file(tmp_path)
    store = _make_mock_bead_store()

    partitioner = ASTPartitioner(tmp_path, bead_store=store)
    # Call partition twice — the in-memory set must suppress the second bead
    partitioner.partition(RenameSymbol(old="OldName", new="NewName"))
    partitioner.partition(RenameSymbol(old="OldName", new="OtherName"))

    parse_failure_calls = [
        c
        for c in store.write.call_args_list
        if "parse-failure" in (c[0][0].tags if c[0][0].tags else [])
    ]
    assert len(parse_failure_calls) == 1, (
        f"Expected exactly 1 parse-failure bead across two partition() calls, "
        f"got {len(parse_failure_calls)}"
    )
