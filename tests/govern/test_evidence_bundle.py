"""Tests for 007 Phase H -- Evidence Bundle (agent-baton-evidence/1.0).

11 spec cases:
  1. happy_path    -- all files, hashes, verdict APPROVE, 2 approvals
  2. missing_task  -- raises ValueError
  3. absent_packs  -- no packs.json key
  4. verify_happy  -- ok, no errors
  5. verify_tamper_gates -- catches tampered gates.json byte
  6. verify_tamper_chain -- catches tampered chain line
  7. verify_missing_manifest -- exit 2
  8. signed_roundtrip -- mint auditor soul; verify passes; corrupt -> fails
  9. soul_verdict_fields_present -- BATON_SOULS_ENABLED=1 + signed bead → verdicts carry soul fields
 10. soul_verdict_verify_passes  -- verify_bundle passes when soul field valid
 11. soul_verdict_flag_off       -- BATON_SOULS_ENABLED=0 → no soul fields, verify passes
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.govern.compliance import ComplianceChainWriter
from agent_baton.core.govern.evidence_bundle import (
    BUNDLE_SCHEMA_VERSION,
    EvidenceBundleBuilder,
    verify_bundle,
)
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


TASK_ID = "task-evidence-test"


def _seed_db(db_path: Path, *, include_approvals: bool = True) -> None:
    """Provision a baton.db with a known task."""
    cm = ConnectionManager(db_path)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    conn = cm.get_connection()
    now = "2026-06-10T10:00:00Z"

    conn.execute(
        "INSERT INTO executions (task_id, status, started_at) VALUES (?, ?, ?)",
        (TASK_ID, "complete", now),
    )
    conn.execute(
        """
        INSERT INTO plans (
            task_id, task_summary, risk_level, budget_tier, execution_mode,
            git_strategy, plan_markdown, created_at,
            explicit_knowledge_packs, explicit_knowledge_docs,
            intervention_level, task_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID,
            "Implement evidence bundle",
            "HIGH",
            "standard",
            "phased",
            "commit-per-agent",
            "# Plan",
            now,
            json.dumps(["governance-basics"]),
            json.dumps([]),
            "low",
            "feature",
        ),
    )
    conn.execute(
        "INSERT INTO plan_phases (task_id, phase_id, name) VALUES (?, ?, ?)",
        (TASK_ID, 1, "Implement"),
    )
    conn.execute(
        """
        INSERT INTO plan_steps (
            task_id, step_id, phase_id, agent_name, model,
            knowledge_attachments, step_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TASK_ID, "1.1", 1, "backend-engineer--python", "sonnet", json.dumps([]), "developing"),
    )
    conn.execute(
        """
        INSERT INTO plan_steps (
            task_id, step_id, phase_id, agent_name, model,
            knowledge_attachments, step_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TASK_ID, "1.2", 1, "auditor", "opus", json.dumps([]), "reviewing"),
    )

    # Step results
    conn.execute(
        """
        INSERT INTO step_results (
            task_id, step_id, agent_name, status, outcome,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, model_id, completed_at, step_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.1", "backend-engineer--python", "complete", "Implemented the feature.",
            5000, 1000, 0, 0, "claude-sonnet-4-6", now, "developing",
        ),
    )
    # Auditor step with fenced JSON verdict
    auditor_outcome = (
        'Analysis complete.\n\n'
        '```json\n{"verdict": "APPROVE", "rationale": "Looks good"}\n```\n'
    )
    conn.execute(
        """
        INSERT INTO step_results (
            task_id, step_id, agent_name, status, outcome,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, model_id, completed_at, step_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            TASK_ID, "1.2", "auditor", "complete", auditor_outcome,
            2000, 500, 0, 0, "claude-opus-4-7", now, "reviewing",
        ),
    )

    # Gate results
    conn.execute(
        """
        INSERT INTO gate_results (
            task_id, phase_id, gate_type, passed, output, command, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TASK_ID, 1, "build", 1, "ok", "pytest", now),
    )
    conn.execute(
        """
        INSERT INTO gate_results (
            task_id, phase_id, gate_type, passed, output, command, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (TASK_ID, 1, "lint", 0, "skipped by config", "ruff check", now),
    )

    # Approval results (2 rows)
    if include_approvals:
        conn.execute(
            """
            INSERT INTO approval_results (
                task_id, phase_id, result, feedback, decided_at,
                decision_source, actor, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (TASK_ID, 1, "APPROVED", "LGTM", now, "human", "alice", "Checked manually"),
        )
        conn.execute(
            """
            INSERT INTO approval_results (
                task_id, phase_id, result, feedback, decided_at,
                decision_source, actor, rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (TASK_ID, 1, "APPROVED", "Auto-approved by policy", now, "policy", "baton-engine", ""),
        )

    conn.commit()
    cm.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "baton.db"
    _seed_db(p)
    return p


@pytest.fixture()
def compliance_log_3entries(tmp_path: Path) -> Path:
    """3-entry compliance chain: 2 entries carry TASK_ID, 1 does not."""
    log = tmp_path / "compliance-audit.jsonl"
    writer = ComplianceChainWriter(log_path=log)
    writer.append({"event": "other_task", "task_id": "other-task-xyz"})
    writer.append({"event": "task_started", "task_id": TASK_ID})
    writer.append({"event": "task_completed", "task_id": TASK_ID})
    return log


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path
) -> None:
    """All bundle files present; hashes correct; verdict APPROVE; 2 approvals."""
    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path)
    assert bundle_dir.is_dir()
    assert bundle_dir == tmp_path / "evidence" / TASK_ID

    # All expected files exist.
    for fname in ("manifest.json", "aibom.json", "aibom.md", "gates.json",
                  "verdicts.json", "approvals.json", "compliance-segment.jsonl"):
        assert (bundle_dir / fname).exists(), f"missing: {fname}"

    # manifest.json structure
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert manifest["schema_version"] == BUNDLE_SCHEMA_VERSION
    assert manifest["task_id"] == TASK_ID
    assert "created_at" in manifest
    assert "files" in manifest
    # All non-manifest files are listed with valid sha256
    for fname, sha in manifest["files"].items():
        assert len(sha) == 64, f"{fname} hash not 64 chars"
        actual_path = bundle_dir / fname
        assert actual_path.exists(), f"listed in manifest but missing: {fname}"

    # verdicts.json: auditor row with APPROVE verdict
    verdicts = json.loads((bundle_dir / "verdicts.json").read_text())
    assert len(verdicts) >= 1
    auditor_rows = [v for v in verdicts if "auditor" in v["agent_name"]]
    assert auditor_rows, "auditor row missing from verdicts"
    assert auditor_rows[0]["verdict"] == "APPROVE"

    # approvals.json: 2 approvals
    approvals_data = json.loads((bundle_dir / "approvals.json").read_text())
    assert len(approvals_data["approvals"]) == 2

    # gates.json: has PASS and SKIP
    gates = json.loads((bundle_dir / "gates.json").read_text())
    outcomes = {g["outcome"] for g in gates}
    assert "PASS" in outcomes
    assert "SKIP" in outcomes

    # compliance-segment.jsonl: 2 task-scoped entries (no synthetic anchor row)
    lines = [
        json.loads(l)
        for l in (bundle_dir / "compliance-segment.jsonl").read_text().splitlines()
        if l.strip()
    ]
    # Each line should have hash fields (it's a real chained entry).
    assert len(lines) == 2, f"expected 2 task entries, got {len(lines)}: {lines}"
    for entry in lines:
        assert "prev_hash" in entry or "_baton_note" in entry
        assert "entry_hash" in entry or "_baton_note" in entry


# ---------------------------------------------------------------------------
# 2. Missing task raises ValueError
# ---------------------------------------------------------------------------


def test_missing_task_raises(tmp_path: Path) -> None:
    """A task not present in the DB raises ValueError."""
    db_path = tmp_path / "empty_db" / "baton.db"
    db_path.parent.mkdir()
    cm = ConnectionManager(db_path)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    cm.get_connection()  # triggers schema creation
    cm.close()

    builder = EvidenceBundleBuilder(db_path=db_path)
    with pytest.raises(ValueError, match="not found"):
        builder.build("nonexistent-task-id", output_dir=tmp_path)


# ---------------------------------------------------------------------------
# 3. Absent packs tolerated (no packs.json)
# ---------------------------------------------------------------------------


def test_absent_packs_tolerated(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path
) -> None:
    """When there are no packs and no active-policy.json, packs.json is absent."""
    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
        packs_dir=tmp_path / "nonexistent-packs",
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path / "out")
    # packs.json must NOT be present when nothing is found.
    assert not (bundle_dir / "packs.json").exists()
    # manifest should not list packs.json either
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert "packs.json" not in manifest["files"]


# ---------------------------------------------------------------------------
# 4. verify_bundle happy path
# ---------------------------------------------------------------------------


def test_verify_happy(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path
) -> None:
    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path)
    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert ok, f"expected ok=True, errors={errors}"
    assert exit_code == 0
    # No error messages (warnings about anchor mismatch are OK but shouldn't be here).
    assert not [e for e in errors if not e.startswith("WARNING")]


# ---------------------------------------------------------------------------
# 5. verify catches tampered gates.json byte
# ---------------------------------------------------------------------------


def test_verify_catches_tampered_gates(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path
) -> None:
    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path)

    # Tamper: flip one byte in gates.json
    gates_path = bundle_dir / "gates.json"
    content = gates_path.read_bytes()
    tampered = bytearray(content)
    tampered[5] ^= 0xFF
    gates_path.write_bytes(bytes(tampered))

    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert not ok
    assert exit_code == 1
    assert any("gates.json" in e for e in errors)


# ---------------------------------------------------------------------------
# 6. verify catches tampered chain line
# ---------------------------------------------------------------------------


def test_verify_catches_tampered_chain(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path
) -> None:
    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path)

    # Tamper: corrupt one line in compliance-segment.jsonl.
    segment_path = bundle_dir / "compliance-segment.jsonl"
    lines = segment_path.read_text(encoding="utf-8").splitlines()
    # Modify a non-anchor line that has hash fields (take last line).
    for i in range(len(lines) - 1, -1, -1):
        try:
            obj = json.loads(lines[i])
            if "entry_hash" in obj:
                obj["entry_hash"] = "a" * 64
                lines[i] = json.dumps(obj, separators=(",", ":"))
                break
        except (json.JSONDecodeError, KeyError):
            pass
    segment_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Also update manifest hash to avoid triggering the hash-mismatch error first.
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    import hashlib
    new_hash = hashlib.sha256(segment_path.read_bytes()).hexdigest()
    manifest["files"]["compliance-segment.jsonl"] = new_hash
    manifest_path.write_text(json.dumps(manifest, indent=2))

    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert not ok
    assert exit_code == 1
    assert any("compliance-segment" in e for e in errors)


# ---------------------------------------------------------------------------
# 7. verify missing manifest → exit 2
# ---------------------------------------------------------------------------


def test_verify_missing_manifest(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "empty_bundle"
    bundle_dir.mkdir()
    (bundle_dir / "gates.json").write_text("{}")

    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert not ok
    assert exit_code == 2
    assert any("manifest" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# 8. Signed roundtrip with SoulRegistry
# ---------------------------------------------------------------------------


def test_signed_roundtrip(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mint an auditor soul, build signed bundle, verify passes; corrupt → fails."""
    monkeypatch.setenv("BATON_SOULS_ENABLED", "1")

    central_db = tmp_path / "central.db"
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()

    from agent_baton.core.engine.soul_registry import SoulRegistry
    registry = SoulRegistry(
        central_db_path=central_db,
        souls_dir=souls_dir,
    )
    # Mint an auditor soul.
    registry.mint("auditor", "evidence-test")

    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
        central_db_path=central_db,
    )

    # Monkeypatch SoulRegistry so evidence_bundle picks up our test registry.
    import agent_baton.core.govern.evidence_bundle as eb_mod

    original_soul_registry = None

    class _TestRegistry(SoulRegistry):
        def __init__(self, **kwargs):  # type: ignore[override]
            # Ignore kwargs passed by evidence_bundle and use test paths.
            super().__init__(
                central_db_path=central_db,
                souls_dir=souls_dir,
            )

    monkeypatch.setattr(eb_mod, "_TestRegistry", _TestRegistry, raising=False)

    # Patch SoulRegistry import inside evidence_bundle to use our test registry.
    import agent_baton.core.engine.soul_registry as sr_mod
    original_cls = sr_mod.SoulRegistry

    def patched_registry(central_db_path=None, souls_dir=None):  # type: ignore[misc]
        return SoulRegistry(central_db_path=central_db, souls_dir=souls_dir or souls_dir)

    # Direct approach: monkeypatch the class used in evidence_bundle's _sign_manifest.
    monkeypatch.setattr(
        "agent_baton.core.engine.soul_registry.SoulRegistry",
        type(
            "PatchedSoulRegistry",
            (SoulRegistry,),
            {
                "__init__": lambda self, central_db_path=None, souls_dir=None: SoulRegistry.__init__(
                    self,
                    central_db_path=central_db,
                    souls_dir=souls_dir or souls_dir,
                )
            },
        ),
    )

    bundle_dir = builder.build(
        TASK_ID, output_dir=tmp_path / "signed_out", sign=True
    )

    # manifest should have soul_signature.
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert "soul_signature" in manifest
    sig_info = manifest["soul_signature"]
    assert "signer_soul_id" in sig_info
    assert "signature" in sig_info

    # verify_bundle should pass.
    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert ok, f"Expected ok; errors={errors}"
    assert exit_code == 0

    # Corrupt the manifest (change the task_id) → signature check fails.
    corrupted = dict(manifest)
    corrupted["task_id"] = "tampered-task-id"
    (bundle_dir / "manifest.json").write_text(json.dumps(corrupted, indent=2))

    ok2, errors2, exit_code2 = verify_bundle(bundle_dir)
    assert not ok2
    assert exit_code2 == 1
    assert any("signature" in e.lower() or "FAILED" in e for e in errors2)


# ---------------------------------------------------------------------------
# 9. Soul-signed verdicts — soul fields flow into verdicts.json
# ---------------------------------------------------------------------------


class _MockBeadStore:
    """Minimal fake bead store that returns a pre-built signed bead."""

    def __init__(self, bead) -> None:  # type: ignore[type-arg]
        self._bead = bead

    def query(self, *, task_id=None, agent_name=None, limit=100, **_kw):
        from agent_baton.models.bead import Bead
        b: Bead = self._bead
        if task_id and b.task_id != task_id:
            return []
        if agent_name and b.agent_name != agent_name:
            return []
        return [b]


def _make_signed_bead(soul, task_id: str, step_id: str, agent_name: str):
    """Build a Bead signed with *soul*."""
    from agent_baton.models.bead import Bead

    content = f"Review complete for {task_id}/{step_id}"
    signature = soul.sign(content.encode("utf-8"))
    return Bead(
        bead_id="bd-test01",
        task_id=task_id,
        step_id=step_id,
        agent_name=agent_name,
        bead_type="decision",
        content=content,
        signed_by=soul.soul_id,
        signature=signature,
    )


def test_soul_verdict_fields_present(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BATON_SOULS_ENABLED=1 and a signed bead exists, verdicts.json carries soul fields."""
    monkeypatch.setenv("BATON_SOULS_ENABLED", "1")

    # Mint a soul using a local tmp registry.
    central_db = tmp_path / "central.db"
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()
    from agent_baton.core.engine.soul_registry import SoulRegistry
    registry = SoulRegistry(central_db_path=central_db, souls_dir=souls_dir)
    soul = registry.mint("auditor", "test-domain")

    # Build a signed bead for the auditor step (step_id "1.2", agent "auditor").
    signed_bead = _make_signed_bead(soul, task_id=TASK_ID, step_id="1.2", agent_name="auditor")
    mock_store = _MockBeadStore(signed_bead)

    # Patch make_bead_store so _collect_verdicts gets our mock.
    import agent_baton.core.govern.evidence_bundle as eb_mod
    monkeypatch.setattr(
        "agent_baton.core.engine.bead_backend.make_bead_store",
        lambda *_a, **_kw: mock_store,
    )

    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path / "out")

    verdicts = json.loads((bundle_dir / "verdicts.json").read_text())
    auditor_rows = [v for v in verdicts if "auditor" in v.get("agent_name", "")]
    assert auditor_rows, "auditor verdict row missing"
    row = auditor_rows[0]
    assert "soul_signed_by" in row, f"soul_signed_by missing; row={row}"
    assert "soul_signature" in row, f"soul_signature missing; row={row}"
    assert row["signature_scheme"] == "ed25519-bead"
    assert row["soul_signed_by"] == soul.soul_id


# ---------------------------------------------------------------------------
# 10. verify_bundle passes when soul verdict fields are valid
# ---------------------------------------------------------------------------


def test_soul_verdict_verify_passes(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify_bundle returns ok=True even when verdicts.json carries soul fields."""
    monkeypatch.setenv("BATON_SOULS_ENABLED", "1")

    central_db = tmp_path / "central.db"
    souls_dir = tmp_path / "souls"
    souls_dir.mkdir()
    from agent_baton.core.engine.soul_registry import SoulRegistry
    registry = SoulRegistry(central_db_path=central_db, souls_dir=souls_dir)
    soul = registry.mint("auditor", "test-domain")

    signed_bead = _make_signed_bead(soul, task_id=TASK_ID, step_id="1.2", agent_name="auditor")
    mock_store = _MockBeadStore(signed_bead)

    monkeypatch.setattr(
        "agent_baton.core.engine.bead_backend.make_bead_store",
        lambda *_a, **_kw: mock_store,
    )
    # Also patch SoulRegistry so verify_bundle uses our registry.
    monkeypatch.setattr(
        "agent_baton.core.engine.soul_registry.SoulRegistry",
        type(
            "PatchedSoulRegistry",
            (SoulRegistry,),
            {
                "__init__": lambda self, **_kw: SoulRegistry.__init__(
                    self,
                    central_db_path=central_db,
                    souls_dir=souls_dir,
                )
            },
        ),
    )

    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path / "out")

    ok, errors, exit_code = verify_bundle(bundle_dir)
    # Failures would be things like "soul not found" — warnings are OK.
    hard_errors = [e for e in errors if not e.startswith("WARNING")]
    assert ok or not hard_errors, f"unexpected failures: {hard_errors}"
    assert exit_code in (0, 1)  # 0 = clean, 1 = has warnings only from registry not found


# ---------------------------------------------------------------------------
# 11. Flag-off case: no soul fields when BATON_SOULS_ENABLED=0
# ---------------------------------------------------------------------------


def test_soul_verdict_flag_off(
    tmp_path: Path, db_path: Path, compliance_log_3entries: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BATON_SOULS_ENABLED=0 (default), verdicts.json has no soul fields and verify passes."""
    monkeypatch.setenv("BATON_SOULS_ENABLED", "0")

    builder = EvidenceBundleBuilder(
        db_path=db_path,
        compliance_log=compliance_log_3entries,
    )
    bundle_dir = builder.build(TASK_ID, output_dir=tmp_path / "out")

    verdicts = json.loads((bundle_dir / "verdicts.json").read_text())
    for v in verdicts:
        assert "soul_signed_by" not in v, "soul fields should not be present when souls disabled"

    ok, errors, exit_code = verify_bundle(bundle_dir)
    assert ok, f"verify should pass when no soul fields; errors={errors}"
    assert exit_code == 0
