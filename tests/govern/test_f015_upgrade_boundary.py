"""RW-4 — the F015 digest change must not slander pre-existing compliance logs.

F015 made ``entry_hash`` commit to ``prev_hash``.  That fix is correct and
must stay, but it is an *unconditional* formula change: every row written
before it (an F0.3-era row whose digest covered only its own payload) now
fails ``verify_chain`` with

    Line 1: entry_hash mismatch (expected '9193…', got 'c426…')

and the project's own runbook (``references/compliance-audit-chain.md``)
maps that exact symptom to "Row payload mutated in place → chain is broken,
restore from backup".  So a clean upgrade reads to an operator as a
confirmed tamper incident.

The required remedy is *detect-and-advise*, not accept: on an ``entry_hash``
mismatch, also recompute under the legacy payload-only formula; when that
matches, say so and point at ``baton compliance rechain`` — while still
returning ``ok=False``.  Because the legacy row is never accepted, this is
not a version-flag dispatch and does not reopen the F015 hole.

Tests here therefore pin three things:

1. the legacy upgrade boundary is diagnosed (and only advised, never accepted);
2. genuine tampering is not misdiagnosed as an upgrade boundary;
3. the operator-facing runbooks document the second boundary instead of
   sending upgraders to restore-from-backup;

plus the skipped "Additionally" item: the AIBOM ``chain_anchor`` cross-check
in ``verify_bundle`` — the only compensating control against a whole-log
re-forge — must be a failure, not a non-fatal warning.
"""
from __future__ import annotations

import hashlib
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

_GENESIS = "0" * 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _legacy_entry_hash(entry: dict) -> str:
    """The pre-F015 digest: canonical JSON of the payload only.

    Reproduced verbatim from the pre-F015 implementation so the fixtures
    below are byte-identical to rows that real deployments already have on
    disk.  Deliberately *not* imported from the module — the module no
    longer has this formula, and pinning it here is the point.
    """
    clean = {k: v for k, v in entry.items() if k not in ("prev_hash", "entry_hash")}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _write(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries),
        encoding="utf-8",
    )


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_legacy_log(path: Path, n: int = 3) -> list[dict]:
    """Write a genuine, *untampered* pre-F015 log.

    Correct linkage (each ``prev_hash`` is its predecessor's stored
    ``entry_hash``), digests computed under the old payload-only formula.
    This is exactly what an honest deployment carries across the upgrade.
    """
    entries: list[dict] = []
    prev = _GENESIS
    for i in range(1, n + 1):
        entry = {
            "entry_type": "Action",
            "task_id": "legacy-task",
            "actor": f"agent-{i}",
            "detail": f"step {i}",
            "prev_hash": prev,
        }
        entry["entry_hash"] = _legacy_entry_hash(entry)
        entries.append(entry)
        prev = entry["entry_hash"]
    _write(path, entries)
    return entries


def _mentions_rechain(message: str) -> bool:
    return "rechain" in message.lower()


def _reads_as_upgrade_boundary(message: str) -> bool:
    """True when *message* tells the operator this is a version boundary.

    Accepts any phrasing that names the F015 digest change (or an
    equivalent "predates the new digest formula" wording) — the assertion
    is on the diagnosis conveyed, not on an exact sentence.
    """
    low = message.lower()
    named = "f015" in low or re.search(r"pre-?dates?|predating|older formula|legacy", low)
    return bool(named) and _mentions_rechain(message)


@pytest.fixture()
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "compliance-audit.jsonl"


# ---------------------------------------------------------------------------
# 1. A clean legacy log must be diagnosed as an upgrade boundary
# ---------------------------------------------------------------------------


def test_untampered_legacy_log_is_diagnosed_as_upgrade_boundary(log_path: Path) -> None:
    """An honest pre-F015 log must not be reported as an in-place mutation.

    The row's stored digest matches the legacy payload-only formula exactly,
    so the verifier can prove this is a formula change rather than tampering.
    """
    _write_legacy_log(log_path, 3)

    ok, msg = verify_chain(log_path)

    assert ok is False, "detect-and-advise must never accept a legacy row"
    assert _reads_as_upgrade_boundary(msg), (
        "verify_chain reports an honest pre-F015 log as a bare entry_hash "
        "mismatch, which the project runbook maps to 'row payload mutated in "
        f"place — restore from backup': {msg!r}"
    )


def test_legacy_boundary_message_carries_the_actionable_command(
    log_path: Path,
) -> None:
    """The advice must name ``rechain`` *and* the log the operator must pass."""
    _write_legacy_log(log_path, 2)

    ok, msg = verify_chain(log_path)

    assert ok is False
    assert _mentions_rechain(msg), msg
    assert str(log_path) in msg, (
        f"message does not tell the operator which log to rechain: {msg!r}"
    )


def test_legacy_boundary_is_reported_at_the_first_row(log_path: Path) -> None:
    """Diagnosis happens on line 1, where the divergence actually is."""
    _write_legacy_log(log_path, 4)

    ok, msg = verify_chain(log_path)

    assert ok is False
    assert re.search(r"line\s*1\b", msg, re.IGNORECASE), (
        f"message does not name line 1 as the divergence: {msg!r}"
    )


def test_legacy_boundary_diagnosis_still_fails_the_verification(
    log_path: Path,
) -> None:
    """Advising is not accepting — the exit status must stay a failure.

    If a fix ever made the legacy formula an accepted alternative (a
    version-flag dispatch), an attacker could downgrade any single row to
    the weak payload-only scheme and splice the chain freely.
    """
    entries = _write_legacy_log(log_path, 3)

    ok, msg = verify_chain(log_path)
    assert ok is False, f"legacy rows must not verify: {msg!r}"

    # And the weak scheme must not be usable to splice: excise row 2 and
    # re-point row 3 at row 1 under the legacy formula.  Still a failure.
    survivors = [entries[0], dict(entries[2])]
    survivors[1]["prev_hash"] = entries[0]["entry_hash"]
    survivors[1]["entry_hash"] = _legacy_entry_hash(survivors[1])
    _write(log_path, survivors)

    ok2, msg2 = verify_chain(log_path)
    assert ok2 is False, (
        f"a spliced legacy chain was accepted — F015 hole reopened: {msg2!r}"
    )


def test_advised_rechain_actually_resolves_the_legacy_boundary(
    log_path: Path,
) -> None:
    """The advertised remedy must work: rechain then verify → intact."""
    _write_legacy_log(log_path, 3)

    assert rechain(log_path) == 3
    ok, msg = verify_chain(log_path)
    assert ok is True, f"the advised remedy does not fix the log: {msg}"


def test_cli_verify_shows_the_boundary_advice_and_still_exits_nonzero(
    log_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator surface (``baton compliance verify``) must carry the advice.

    Exit status stays 1 — the log is not accepted — but the printed text has
    to distinguish "your log predates the digest change" from "your log was
    mutated in place".
    """
    import argparse

    from agent_baton.cli.commands.govern.compliance import handler

    _write_legacy_log(log_path, 2)

    args = argparse.Namespace(
        compliance_cmd="verify", log=str(log_path), out=None,
        task_id=None, count=None,
    )
    with pytest.raises(SystemExit) as exc:
        handler(args)

    assert exc.value.code == 1, "a legacy log must never verify successfully"
    out = capsys.readouterr().out
    assert _reads_as_upgrade_boundary(out), (
        f"the CLI tells the operator nothing about the upgrade boundary: {out!r}"
    )


# ---------------------------------------------------------------------------
# 2. Genuine tampering must not be excused as an upgrade boundary
# ---------------------------------------------------------------------------


def test_mutated_payload_is_not_misdiagnosed_as_an_upgrade_boundary(
    log_path: Path,
) -> None:
    """A row edited in place matches *neither* formula → no rechain advice.

    Over-advising is as harmful as under-advising: it teaches operators to
    run a migration and move on when the log really was mutated.
    """
    writer = ComplianceChainWriter(log_path=log_path)
    for i in range(1, 4):
        writer.append({"entry_type": "Action", "task_id": "t1", "detail": f"step {i}"})

    entries = _read(log_path)
    entries[1] = {**entries[1], "detail": "TAMPERED"}  # digest left untouched
    _write(log_path, entries)

    ok, msg = verify_chain(log_path)

    assert ok is False
    assert not _reads_as_upgrade_boundary(msg), (
        "an in-place payload mutation was excused as a pre-F015 row, which "
        f"tells the operator to migrate instead of investigate: {msg!r}"
    )


def test_modern_untampered_log_still_verifies_clean(log_path: Path) -> None:
    """Guard rail: the legacy probe must not introduce false negatives."""
    writer = ComplianceChainWriter(log_path=log_path)
    for i in range(1, 4):
        writer.append({"entry_type": "Action", "task_id": "t1", "detail": f"step {i}"})

    ok, msg = verify_chain(log_path)
    assert ok is True, msg
    assert "3 entries" in msg


def test_prev_hash_linkage_break_in_a_legacy_log_is_not_excused(
    log_path: Path,
) -> None:
    """A legacy log whose *linkage* is broken is a real incident, not a boundary.

    Row 3 is re-pointed at row 1 while keeping its legacy digest, so the
    first divergence is the pointer, not the digest formula.
    """
    entries = _write_legacy_log(log_path, 3)
    entries[2] = {**entries[2], "prev_hash": entries[0]["entry_hash"]}
    entries[2]["entry_hash"] = _legacy_entry_hash(entries[2])
    _write(log_path, entries)

    ok, msg = verify_chain(log_path)
    assert ok is False, msg


# ---------------------------------------------------------------------------
# 3. Operator-facing runbooks must document the second boundary
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _table_row_for(doc: str, symptom: str) -> str:
    """Return the markdown table row describing *symptom* (or "")."""
    for line in doc.splitlines():
        if line.lstrip().startswith("|") and symptom in line:
            return line
    return ""


def test_chain_reference_documents_the_f015_upgrade_boundary() -> None:
    """``references/compliance-audit-chain.md`` must cover the F015 boundary.

    It already documents the pre-F0.3 (missing-hash-fields) boundary; the
    F015 digest change created a second one that upgraders hit.
    """
    doc = (_REPO_ROOT / "references" / "compliance-audit-chain.md").read_text(
        encoding="utf-8"
    )
    assert "F015" in doc, (
        "the chain reference documents only the pre-F0.3 upgrade boundary; "
        "the F015 digest-formula boundary is undocumented"
    )
    assert _mentions_rechain(doc)


def test_chain_reference_stops_misdiagnosing_entry_hash_mismatch() -> None:
    """The failure-mode table must not send F015 upgraders to backups.

    The ``entry_hash mismatch`` row currently reads "Row payload mutated in
    place → restore from backup", which is the wrong call for the single
    most common cause of that symptom right after an upgrade.
    """
    doc = (_REPO_ROOT / "references" / "compliance-audit-chain.md").read_text(
        encoding="utf-8"
    )
    row = _table_row_for(doc, "entry_hash mismatch")
    assert row, "failure-mode table has no entry_hash mismatch row"
    low = row.lower()
    assert "f015" in low or "rechain" in low, (
        "the entry_hash mismatch row still points only at tampering / "
        f"restore-from-backup: {row.strip()!r}"
    )


def test_rollback_recipe_covers_the_digest_formula_change() -> None:
    """Rolling back across F015 inverts which logs verify — say so.

    ``rollback-recipe.md`` documents rechaining a pre-Phase-0 log; after
    F015 a rollback re-validates legacy rows and invalidates current ones.
    """
    doc = (
        _REPO_ROOT
        / "docs"
        / "architecture"
        / "phase-0-foundations"
        / "rollback-recipe.md"
    ).read_text(encoding="utf-8")
    assert "F015" in doc, (
        "the rollback recipe never mentions the F015 digest-formula change, "
        "so an operator rolling back has no warning that entry_hash "
        "semantics differ across the boundary"
    )


# ---------------------------------------------------------------------------
# 4. Skipped "Additionally": AIBOM chain_anchor mismatch must be fatal
# ---------------------------------------------------------------------------


def _retip_manifest(bundle_dir: Path) -> None:
    """Re-hash every listed file so per-file SHA checks pass cleanly."""
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for fname in list(manifest.get("files", {})):
        fpath = bundle_dir / fname
        if fpath.exists():
            manifest["files"][fname] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _build_bundle(tmp_path: Path) -> Path:
    from tests.govern.test_evidence_bundle import TASK_ID, _seed_db
    from agent_baton.core.govern.evidence_bundle import EvidenceBundleBuilder

    db_path = tmp_path / "baton.db"
    _seed_db(db_path)

    log = tmp_path / "compliance-audit.jsonl"
    writer = ComplianceChainWriter(log_path=log)
    writer.append({"event": "other_task", "task_id": "other-task-xyz"})
    writer.append({"event": "task_started", "task_id": TASK_ID})
    writer.append({"event": "task_completed", "task_id": TASK_ID})

    builder = EvidenceBundleBuilder(db_path=db_path, compliance_log=log)
    return builder.build(TASK_ID, output_dir=tmp_path / "out")


def test_untouched_bundle_still_verifies(tmp_path: Path) -> None:
    """Guard rail for the change below: a clean bundle must stay clean."""
    from agent_baton.core.govern.evidence_bundle import verify_bundle

    bundle_dir = _build_bundle(tmp_path)
    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert ok is True, errors
    assert exit_code == 0


def test_aibom_chain_anchor_mismatch_fails_the_bundle(tmp_path: Path) -> None:
    """A re-forged compliance log is only catchable via the AIBOM anchor.

    ``verify_chain``/``_verify_segment_chain`` cannot detect a whole-log
    re-forge (excise a row, recompute every digest from genesis, and the
    chain is internally perfect).  The AIBOM ``chain_anchor`` recorded at
    bundle-build time is the only independent witness to the real tail, so
    a mismatch must fail verification rather than emit a non-fatal warning.
    """
    from agent_baton.core.govern.evidence_bundle import verify_bundle

    bundle_dir = _build_bundle(tmp_path)

    aibom_path = bundle_dir / "aibom.json"
    aibom = json.loads(aibom_path.read_text(encoding="utf-8"))
    assert len(aibom.get("chain_anchor", "")) == 64, (
        "bundle carries no chain_anchor to cross-check"
    )
    aibom["chain_anchor"] = "d" * 64
    aibom_path.write_text(json.dumps(aibom, indent=2), encoding="utf-8")
    _retip_manifest(bundle_dir)

    ok, messages, exit_code = verify_bundle(bundle_dir)

    anchor_msgs = [m for m in messages if "chain_anchor" in m]
    assert anchor_msgs, f"anchor mismatch not reported at all: {messages!r}"
    assert not any(m.startswith("WARNING") for m in anchor_msgs), (
        "the anchor cross-check — the only compensating control against a "
        f"whole-log re-forge — is still a non-fatal warning: {anchor_msgs!r}"
    )
    assert ok is False, (
        f"verify_bundle passed a bundle whose compliance tail does not match "
        f"the AIBOM anchor: {messages!r}"
    )
    assert exit_code == 1


def test_reforged_segment_is_caught_by_the_anchor(tmp_path: Path) -> None:
    """End-to-end: excise a row and recompute the chain → must still fail.

    The recomputed segment is internally flawless, so only the anchor
    cross-check stands between an auditor and a doctored history.
    """
    from agent_baton.core.govern.evidence_bundle import verify_bundle

    bundle_dir = _build_bundle(tmp_path)
    segment = bundle_dir / "compliance-segment.jsonl"
    entries = _read(segment)
    assert len(entries) >= 2, entries

    # Drop the last row and re-chain the survivors from their original head.
    survivors = entries[:-1]
    prev = survivors[0]["prev_hash"]
    reforged = []
    for entry in survivors:
        entry = {k: v for k, v in entry.items() if k != "entry_hash"}
        entry["prev_hash"] = prev
        entry["entry_hash"] = _entry_hash(entry)
        reforged.append(entry)
        prev = entry["entry_hash"]
    _write(segment, reforged)
    _retip_manifest(bundle_dir)

    ok, messages, exit_code = verify_bundle(bundle_dir)
    assert ok is False, (
        "a re-forged compliance segment verified clean — auditors have no "
        f"way to detect an excised row: {messages!r}"
    )
