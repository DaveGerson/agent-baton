"""F015 — the compliance hash chain must actually be tamper-evident.

``ComplianceChainWriter`` advertises (compliance.py docstring) that
"modifying any past entry breaks all subsequent hashes".  That property
only holds if each row's ``entry_hash`` commits to ``prev_hash``.  If the
digest is computed over the row's own payload alone, the chain degrades
into a list of independent per-row checksums plus an *unauthenticated*
``prev_hash`` pointer — an attacker can delete, splice or rewrite rows and
repair the log by editing one hex string.

These tests exercise the attack end-to-end through the public
``verify_chain`` / ``_verify_segment_chain`` surfaces.  They deliberately
give the attacker the full algorithm (the module's own hasher) — the
digest algorithm is public; only the history is meant to be unforgeable.

Backward compatibility note for the implementer: whatever migration or
``chain_version`` scheme is chosen, these tests only use rows produced by
the *current* writer (or by ``rechain()``), so a versioned digest is fine.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_baton.core.govern.compliance import (
    ComplianceChainWriter,
    _entry_hash,
    rechain,
    verify_chain,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "compliance-audit.jsonl"


@pytest.fixture()
def writer(log_path: Path) -> ComplianceChainWriter:
    return ComplianceChainWriter(log_path=log_path)


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries),
        encoding="utf-8",
    )


def _seed(writer: ComplianceChainWriter, n: int = 5, task_id: str = "t1") -> None:
    for i in range(1, n + 1):
        writer.append(
            {
                "entry_type": "Action",
                "task_id": task_id,
                "actor": f"agent-{i}",
                "detail": f"step {i}",
            }
        )


def _names_line(message: str, line_number: int) -> bool:
    """True when *message* identifies the given 1-based log line."""
    return bool(re.search(rf"line\s*{line_number}\b", message, re.IGNORECASE))


# ---------------------------------------------------------------------------
# The digest must commit to the chain position
# ---------------------------------------------------------------------------

def test_entry_digest_commits_to_prev_hash(writer: ComplianceChainWriter) -> None:
    """Re-pointing a written row at a different predecessor must change its digest.

    Otherwise ``prev_hash`` is an unauthenticated field: rewriting it costs
    the attacker nothing.
    """
    entry = writer.append({"entry_type": "Action", "task_id": "t1", "detail": "x"})

    relinked = {**entry, "prev_hash": "b" * 64}
    assert entry["prev_hash"] != relinked["prev_hash"]

    assert _entry_hash(relinked) != _entry_hash(entry), (
        "entry_hash is independent of prev_hash — the 'chain' is just a list "
        "of per-row checksums and history can be rewritten freely"
    )


def test_identical_payload_at_different_chain_positions_hashes_differently(
    tmp_path: Path,
) -> None:
    """The same payload appended at different positions must not collide.

    A position-independent digest lets an attacker lift a legitimately
    signed row out of one place in the log and drop it into another.
    """
    a = ComplianceChainWriter(log_path=tmp_path / "a.jsonl")
    b = ComplianceChainWriter(log_path=tmp_path / "b.jsonl")

    payload = {"entry_type": "Action", "task_id": "t1", "detail": "identical"}
    first = a.append(dict(payload))

    b.append({"entry_type": "Action", "task_id": "t1", "detail": "earlier row"})
    later = b.append(dict(payload))

    assert first["prev_hash"] != later["prev_hash"]
    assert first["entry_hash"] != later["entry_hash"], (
        "two rows with identical content but different history share an "
        "entry_hash — rows are transplantable between chain positions"
    )


# ---------------------------------------------------------------------------
# verify_chain must reject a *consistently repaired* forgery
# ---------------------------------------------------------------------------

def test_verify_chain_detects_deleted_entry_with_repointed_prev_hash(
    writer: ComplianceChainWriter, log_path: Path
) -> None:
    """Excise a row and re-point its successor at its predecessor → must fail.

    This is the real-world attack: delete the ``Override``/VETO row, edit one
    hex string in the next line, and the log still "verifies".
    """
    _seed(writer, 5)
    entries = _read(log_path)
    assert len(entries) == 5

    # Delete entry #2 and re-point entry #3 at entry #1.
    survivors = [entries[0], entries[2], entries[3], entries[4]]
    survivors[1] = {**survivors[1], "prev_hash": entries[0]["entry_hash"]}
    _write(log_path, survivors)

    ok, msg = verify_chain(log_path)

    assert ok is False, (
        "verify_chain accepted a log with a deleted entry whose successor was "
        f"simply re-pointed: {msg!r}"
    )
    assert _names_line(msg, 2), f"message does not name the divergence line: {msg!r}"


def test_verify_chain_detects_edited_payload_with_local_hash_repair(
    writer: ComplianceChainWriter, log_path: Path
) -> None:
    """Rewrite a row's payload, fix its own digest + the next row's pointer.

    Repairing a forgery must require recomputing the entire tail, not two
    fields.  The attacker here uses the module's own hasher, so a correct
    chain fails at the *following* row, whose digest still commits to the
    superseded predecessor hash.
    """
    _seed(writer, 5)
    entries = _read(log_path)

    tampered = {**entries[2], "detail": "TAMPERED"}
    tampered["entry_hash"] = _entry_hash(tampered)
    entries[2] = tampered
    entries[3] = {**entries[3], "prev_hash": tampered["entry_hash"]}
    _write(log_path, entries)

    ok, msg = verify_chain(log_path)

    assert ok is False, (
        "verify_chain accepted a rewritten payload that was repaired by "
        f"touching only two fields: {msg!r}"
    )
    assert _names_line(msg, 4), f"message does not name the divergence line: {msg!r}"


def test_verify_chain_still_accepts_an_untampered_log(
    writer: ComplianceChainWriter, log_path: Path
) -> None:
    """Guard rail: tamper-evidence must not be bought with false positives."""
    _seed(writer, 5)
    ok, msg = verify_chain(log_path)
    assert ok is True, msg
    assert "5 entries" in msg


# ---------------------------------------------------------------------------
# Migration path: a rechained legacy log gets the same guarantee
# ---------------------------------------------------------------------------

def test_rechained_legacy_log_is_tamper_evident(tmp_path: Path) -> None:
    """``baton compliance rechain`` must yield a chain with the real property.

    Whatever backward-compat scheme is chosen for pre-existing logs, the
    migrated output must not be forgeable by the delete-and-re-point edit.
    """
    plain = tmp_path / "legacy.jsonl"
    _write(
        plain,
        [{"entry_type": "Action", "task_id": "t1", "detail": f"step {i}"}
         for i in range(1, 6)],
    )

    assert rechain(plain) == 5
    ok, msg = verify_chain(plain)
    assert ok is True, f"rechain produced a log that does not verify: {msg}"

    entries = _read(plain)
    survivors = [entries[0], entries[2], entries[3], entries[4]]
    survivors[1] = {**survivors[1], "prev_hash": entries[0]["entry_hash"]}
    _write(plain, survivors)

    ok, msg = verify_chain(plain)
    assert ok is False, (
        f"rechained log is still forgeable by deleting one row: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Evidence bundles inherit the same guarantee
# ---------------------------------------------------------------------------

def test_evidence_segment_verification_detects_spliced_entry(
    writer: ComplianceChainWriter, log_path: Path, tmp_path: Path
) -> None:
    """The compliance segment shipped to auditors must be forgery-resistant too."""
    from agent_baton.core.govern.evidence_bundle import _verify_segment_chain

    _seed(writer, 4)
    entries = _read(log_path)

    segment = tmp_path / "compliance-segment.jsonl"
    _write(segment, entries)
    ok, msg = _verify_segment_chain(segment)
    assert ok is True, f"untouched segment failed verification: {msg}"

    survivors = [entries[0], entries[2], entries[3]]
    survivors[1] = {**survivors[1], "prev_hash": entries[0]["entry_hash"]}
    _write(segment, survivors)

    ok, msg = _verify_segment_chain(segment)
    assert ok is False, (
        f"evidence segment accepted a spliced compliance chain: {msg!r}"
    )
