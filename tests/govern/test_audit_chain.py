"""Tests for F0.3 tamper-evident compliance-audit.jsonl chain."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from agent_baton.core.govern.compliance import (
    ComplianceChainWriter,
    verify_chain,
    rechain,
    _GENESIS_HASH,
    _entry_hash,
)


# ---------------------------------------------------------------------------
# ComplianceChainWriter
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "compliance-audit.jsonl"


@pytest.fixture()
def writer(log_path: Path) -> ComplianceChainWriter:
    return ComplianceChainWriter(log_path=log_path)


def test_first_entry_uses_genesis_hash(writer: ComplianceChainWriter, log_path: Path) -> None:
    entry = writer.append({"event": "action1", "task_id": "t1"})
    assert entry["prev_hash"] == _GENESIS_HASH
    assert entry["entry_hash"] != ""


def test_second_entry_prev_hash_matches_first(writer: ComplianceChainWriter) -> None:
    e1 = writer.append({"event": "e1"})
    e2 = writer.append({"event": "e2"})
    assert e2["prev_hash"] == e1["entry_hash"]


def test_three_entries_chain_is_continuous(writer: ComplianceChainWriter) -> None:
    e1 = writer.append({"event": "a"})
    e2 = writer.append({"event": "b"})
    e3 = writer.append({"event": "c"})
    assert e2["prev_hash"] == e1["entry_hash"]
    assert e3["prev_hash"] == e2["entry_hash"]


def test_entry_hash_is_reproducible() -> None:
    entry = {"event": "x", "task_id": "t", "prev_hash": "abc123"}
    h1 = _entry_hash(entry)
    h2 = _entry_hash(entry)
    assert h1 == h2


def test_entry_hash_excludes_hash_fields() -> None:
    # Both prev_hash and entry_hash are stripped before hashing.
    # Two entries with same payload but different hash fields must produce same hash.
    e1 = {"event": "x", "prev_hash": "aaa", "entry_hash": "bbb"}
    e2 = {"event": "x", "prev_hash": "ccc", "entry_hash": "ddd"}
    assert _entry_hash(e1) == _entry_hash(e2)

    # Two entries with different payload must produce different hash.
    e3 = {"event": "y", "prev_hash": "aaa", "entry_hash": "bbb"}
    assert _entry_hash(e1) != _entry_hash(e3)


def test_append_override_writes_override_type(writer: ComplianceChainWriter, log_path: Path) -> None:
    entry = writer.append_override(
        task_id="t1", actor="alice",
        justification="urgent prod issue", overridden_verdict="VETO"
    )
    assert entry["entry_type"] == "Override"
    assert entry["actor"] == "alice"
    assert entry["overridden_verdict"] == "VETO"


# ---------------------------------------------------------------------------
# verify_chain
# ---------------------------------------------------------------------------

def test_verify_chain_nonexistent_log(tmp_path: Path) -> None:
    ok, msg = verify_chain(tmp_path / "no-such.jsonl")
    assert ok is True
    assert "does not exist" in msg.lower() or "nothing" in msg.lower()


def test_verify_chain_empty_log(log_path: Path) -> None:
    log_path.write_text("", encoding="utf-8")
    ok, msg = verify_chain(log_path)
    assert ok is True


def test_verify_chain_valid(writer: ComplianceChainWriter, log_path: Path) -> None:
    writer.append({"event": "a"})
    writer.append({"event": "b"})
    writer.append({"event": "c"})
    ok, msg = verify_chain(log_path)
    assert ok is True
    assert "3 entries" in msg


def test_verify_chain_detects_tamper_in_prev_hash(writer: ComplianceChainWriter, log_path: Path) -> None:
    writer.append({"event": "a"})
    writer.append({"event": "b"})
    # Tamper: overwrite first line's entry_hash with garbage
    lines = log_path.read_text().splitlines()
    first = json.loads(lines[0])
    first["entry_hash"] = "badhash" * 8
    lines[0] = json.dumps(first)
    log_path.write_text("\n".join(lines) + "\n")
    ok, msg = verify_chain(log_path)
    assert ok is False
    assert "mismatch" in msg.lower()


def test_verify_chain_detects_tamper_in_content(writer: ComplianceChainWriter, log_path: Path) -> None:
    writer.append({"event": "original"})
    # Tamper: change content without fixing hash
    lines = log_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["event"] = "tampered"
    # Keep old entry_hash and prev_hash
    lines[0] = json.dumps(entry)
    log_path.write_text("\n".join(lines) + "\n")
    ok, msg = verify_chain(log_path)
    assert ok is False


# ---------------------------------------------------------------------------
# rechain
# ---------------------------------------------------------------------------

def test_rechain_adds_hashes_to_plain_log(tmp_path: Path) -> None:
    log_path = tmp_path / "plain.jsonl"
    entries = [{"event": "a"}, {"event": "b"}, {"event": "c"}]
    with log_path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    count = rechain(log_path)
    assert count == 3

    # Verify the rechained log
    ok, msg = verify_chain(log_path)
    assert ok is True
    assert "3 entries" in msg


def test_rechain_to_separate_output(tmp_path: Path) -> None:
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "dst.jsonl"
    src.write_text('{"event": "x"}\n{"event": "y"}\n', encoding="utf-8")
    count = rechain(src, dst)
    assert count == 2
    assert dst.exists()
    # Source unchanged (no hashes added)
    lines = src.read_text().splitlines()
    first = json.loads(lines[0])
    assert "entry_hash" not in first
    # Destination is valid chain
    ok, _ = verify_chain(dst)
    assert ok is True


def test_rechain_existing_hashed_log_is_idempotent(writer: ComplianceChainWriter, log_path: Path) -> None:
    writer.append({"event": "a"})
    writer.append({"event": "b"})
    count = rechain(log_path)
    assert count == 2
    ok, _ = verify_chain(log_path)
    assert ok is True
