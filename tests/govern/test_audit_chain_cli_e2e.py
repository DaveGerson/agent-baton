"""End-to-end tests for the F0.3 hash-chain audit CLI surface.

Exercises `baton compliance verify` and `baton compliance rechain` as
black-box CLI commands.  Acceptance criterion (strategic spec):
tampering with any line in compliance-audit.jsonl makes verify exit
non-zero and report the first divergent entry.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _run_cli(argv: list[str]) -> int:
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


def test_e2e_verify_clean_chain_exits_zero(tmp_path: Path, capsys) -> None:
    """A freshly-written hash chain must verify successfully (rc=0)."""
    from agent_baton.core.govern.compliance import ComplianceChainWriter

    log = tmp_path / "compliance-audit.jsonl"
    writer = ComplianceChainWriter(log_path=log)
    writer.append({"task": "t1", "actor": "auditor", "verdict": "APPROVE"})
    writer.append({"task": "t1", "actor": "auditor", "verdict": "APPROVE"})
    writer.append({"task": "t1", "actor": "auditor", "verdict": "APPROVE"})

    rc = _run_cli(["compliance", "verify", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc == 0, f"verify failed: {out}"
    assert "ok" in out.lower() or "valid" in out.lower() or rc == 0


def test_e2e_verify_tampered_payload_exits_nonzero(
    tmp_path: Path, capsys
) -> None:
    """Mutating any payload field after-the-fact must fail verification.

    This is the strategic spec's tamper-evidence promise: the hash chain
    catches any modification of a written record.
    """
    from agent_baton.core.govern.compliance import ComplianceChainWriter

    log = tmp_path / "compliance-audit.jsonl"
    writer = ComplianceChainWriter(log_path=log)
    writer.append({"task": "t1", "actor": "auditor", "verdict": "APPROVE"})
    writer.append({"task": "t1", "actor": "auditor", "verdict": "VETO"})
    writer.append({"task": "t1", "actor": "auditor", "verdict": "APPROVE"})

    # Tamper with the middle entry: change VETO -> APPROVE without
    # re-hashing.  This breaks the chain.
    lines = log.read_text(encoding="utf-8").splitlines()
    middle = json.loads(lines[1])
    middle["verdict"] = "APPROVE"
    lines[1] = json.dumps(middle)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rc = _run_cli(["compliance", "verify", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc != 0, f"tamper went undetected; CLI output:\n{out}"


def test_e2e_verify_missing_log_does_not_crash(
    tmp_path: Path, capsys
) -> None:
    """Verifying a non-existent log must produce a graceful message,
    not a stack trace."""
    rc = _run_cli([
        "compliance", "verify",
        "--log", str(tmp_path / "no-such.jsonl"),
    ])
    # Either rc=0 with an empty-log message or rc=1 with an explanation
    # is acceptable; what matters is no uncaught exception.
    out = capsys.readouterr().out
    assert isinstance(rc, int)
    assert out  # some message printed


def test_e2e_rechain_then_verify_passes(tmp_path: Path, capsys) -> None:
    """rechain on a plain log followed by verify must succeed.

    This is the migration path for legacy compliance logs.
    """
    log = tmp_path / "legacy.jsonl"
    plain = [
        {"task": "t1", "actor": "auditor", "verdict": "APPROVE"},
        {"task": "t1", "actor": "auditor", "verdict": "VETO"},
    ]
    log.write_text(
        "\n".join(json.dumps(e) for e in plain) + "\n",
        encoding="utf-8",
    )

    rc = _run_cli(["compliance", "rechain", "--log", str(log)])
    assert rc == 0
    capsys.readouterr()

    rc = _run_cli(["compliance", "verify", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc == 0, f"rechained log failed verify: {out}"
