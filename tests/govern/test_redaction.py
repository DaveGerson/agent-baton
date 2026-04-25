"""Tests for G1.5 sensitive-data redaction (bd-1a09)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_baton.core.govern._redaction import (
    Redactor,
    default_redactor,
    redaction_enabled,
)
from agent_baton.core.govern.compliance import (
    ComplianceChainWriter,
    verify_chain,
)


# ---------------------------------------------------------------------------
# Per-pattern: positive + negative
# ---------------------------------------------------------------------------

@pytest.fixture()
def redactor() -> Redactor:
    return Redactor()


def test_redact_email_positive(redactor: Redactor) -> None:
    out = redactor.redact("contact alice@example.com today")
    assert "alice@example.com" not in out
    assert "[REDACTED:email]" in out
    assert redactor.last_counts["email"] == 1


def test_redact_email_negative(redactor: Redactor) -> None:
    out = redactor.redact("just an @ symbol and a domain.com — no match")
    assert "[REDACTED:email]" not in out
    assert redactor.last_counts.get("email", 0) == 0


def test_redact_ssn_positive(redactor: Redactor) -> None:
    out = redactor.redact("SSN is 123-45-6789 on file")
    assert "123-45-6789" not in out
    assert "[REDACTED:ssn]" in out


def test_redact_ssn_negative(redactor: Redactor) -> None:
    out = redactor.redact("phone 555-1234 ext 7")
    assert "[REDACTED:ssn]" not in out


def test_redact_aws_access_key_positive(redactor: Redactor) -> None:
    out = redactor.redact("key=AKIAIOSFODNN7EXAMPLE here")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_access_key]" in out


def test_redact_aws_access_key_negative(redactor: Redactor) -> None:
    out = redactor.redact("AKIASHORT and AKIA-no-good")
    assert "[REDACTED:aws_access_key]" not in out


def test_redact_aws_secret_key_positive(redactor: Redactor) -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # 40 chars
    line = f"AWS_SECRET_ACCESS_KEY={secret}"
    out = redactor.redact(line)
    assert secret not in out
    assert "[REDACTED:aws_secret_key]" in out


def test_redact_aws_secret_key_negative(redactor: Redactor) -> None:
    # 40-char base64 with no AWS_SECRET key=val context: must NOT redact
    # as an aws_secret_key (high false-positive risk by design).
    raw = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    out = redactor.redact(f"some random base64 blob: {raw}")
    assert "[REDACTED:aws_secret_key]" not in out


def test_redact_generic_secret_positive(redactor: Redactor) -> None:
    out = redactor.redact('api_key="sk_live_abcdef123456"')
    assert "sk_live_abcdef123456" not in out
    assert "[REDACTED:secret]" in out


def test_redact_generic_secret_authorization_bearer(redactor: Redactor) -> None:
    out = redactor.redact("Authorization: Bearer abcdef0123456789ZZ")
    assert "abcdef0123456789ZZ" not in out
    assert "[REDACTED:secret]" in out


def test_redact_generic_secret_negative(redactor: Redactor) -> None:
    # Matching keyword without a value pair — should not redact.
    out = redactor.redact("Please rotate the password.")
    assert "[REDACTED:secret]" not in out


def test_redact_jwt_positive(redactor: Redactor) -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NSIsIm5hbWUiOiJBbGljZSJ9."
        "abc-DEF_123xyz"
    )
    out = redactor.redact(f"token={jwt}")
    assert jwt not in out
    assert "[REDACTED:jwt]" in out


def test_redact_jwt_negative(redactor: Redactor) -> None:
    out = redactor.redact("eyJ-incomplete-no-dots")
    assert "[REDACTED:jwt]" not in out


def test_redact_private_key_block(redactor: Redactor) -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdef\n"
        "moredata=\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redactor.redact(f"key:\n{pem}\nend")
    assert "MIIEpAIBAAKCAQEA1234567890abcdef" not in out
    assert "[REDACTED:private_key]" in out


def test_redact_private_key_negative(redactor: Redactor) -> None:
    out = redactor.redact("PRIVATE KEY: stored in vault elsewhere")
    assert "[REDACTED:private_key]" not in out


def test_redact_ipv4_off_by_default() -> None:
    """IPv4 redaction is opt-in (default off) — too noisy on prose / version
    strings like ``chrome/120.0.0.0`` to be on by default."""
    r = Redactor()
    out = r.redact("server at 192.168.1.42 listening")
    assert "192.168.1.42" in out


def test_redact_ipv4_when_opted_in() -> None:
    r = Redactor(include_ipv4=True)
    out = r.redact("server at 192.168.1.42 listening")
    assert "192.168.1.42" not in out
    assert "[REDACTED:ipv4]" in out


def test_redact_ipv4_negative_when_opted_in() -> None:
    r = Redactor(include_ipv4=True)
    out = r.redact("version 1.2.3 minor build 9")
    assert "[REDACTED:ipv4]" not in out


# ---------------------------------------------------------------------------
# redact_payload: recursion through dicts and lists
# ---------------------------------------------------------------------------

def test_redact_payload_nested_dict_and_list(redactor: Redactor) -> None:
    payload = {
        "user": {"email": "bob@example.com", "ssn": "987-65-4321"},
        "tags": ["AKIAIOSFODNN7EXAMPLE", "harmless"],
        "count": 42,
        "active": True,
    }
    out = redactor.redact_payload(payload)
    assert out["user"]["email"] == "[REDACTED:email]"
    assert out["user"]["ssn"] == "[REDACTED:ssn]"
    assert out["tags"][0] == "[REDACTED:aws_access_key]"
    assert out["tags"][1] == "harmless"
    assert out["count"] == 42
    assert out["active"] is True
    # Counts accumulate across the whole tree.
    assert redactor.last_counts["email"] == 1
    assert redactor.last_counts["ssn"] == 1
    assert redactor.last_counts["aws_access_key"] == 1


def test_redact_payload_keys_unchanged(redactor: Redactor) -> None:
    payload = {"alice@example.com": "value"}
    out = redactor.redact_payload(payload)
    # Keys are NOT redacted (typically schema/field names).
    assert "alice@example.com" in out
    assert out["alice@example.com"] == "value"


def test_default_redactor_is_singleton() -> None:
    a = default_redactor()
    b = default_redactor()
    assert a is b


# ---------------------------------------------------------------------------
# Integration with ComplianceChainWriter
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "compliance-audit.jsonl"


def test_chain_writer_redacts_when_enabled(
    log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BATON_REDACTION_ENABLED", "1")
    writer = ComplianceChainWriter(log_path=log_path)
    writer.append({
        "event": "action",
        "task_id": "t1",
        "actor_email": "carol@example.com",
        "note": "cleared SSN 111-22-3333 ok",
    })
    on_disk = log_path.read_text(encoding="utf-8")
    assert "carol@example.com" not in on_disk
    assert "111-22-3333" not in on_disk
    assert "[REDACTED:email]" in on_disk
    assert "[REDACTED:ssn]" in on_disk


def test_chain_writer_skips_redaction_when_disabled(
    log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BATON_REDACTION_ENABLED", "0")
    writer = ComplianceChainWriter(log_path=log_path)
    writer.append({
        "event": "action",
        "task_id": "t2",
        "actor_email": "dan@example.com",
    })
    on_disk = log_path.read_text(encoding="utf-8")
    assert "dan@example.com" in on_disk
    assert "[REDACTED:" not in on_disk


def test_chain_remains_verifiable_after_redaction(
    log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BATON_REDACTION_ENABLED", "1")
    writer = ComplianceChainWriter(log_path=log_path)
    writer.append({"event": "e1", "email": "a@b.com"})
    writer.append({"event": "e2", "ssn": "111-22-3333"})
    writer.append({"event": "e3", "note": "no secrets"})
    ok, msg = verify_chain(log_path)
    assert ok, msg
    # Each entry on disk has the redacted form.
    lines = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
    assert lines[0]["email"] == "[REDACTED:email]"
    assert lines[1]["ssn"] == "[REDACTED:ssn]"


def test_redaction_enabled_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BATON_REDACTION_ENABLED", raising=False)
    assert redaction_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off"])
def test_redaction_enabled_env_disable(
    val: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BATON_REDACTION_ENABLED", val)
    assert redaction_enabled() is False


def test_tampered_redacted_chain_fails_verify(
    log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A redacted chain must still detect on-disk mutation post-write."""
    monkeypatch.setenv("BATON_REDACTION_ENABLED", "1")
    writer = ComplianceChainWriter(log_path=log_path)
    writer.append({"event": "e1", "email": "a@b.com"})
    writer.append({"event": "e2", "note": "second"})
    writer.append({"event": "e3", "note": "third"})
    # Mutate the middle entry's payload after-the-fact (post-redaction).
    lines = log_path.read_text().splitlines()
    obj = json.loads(lines[1])
    obj["note"] = "tampered"
    lines[1] = json.dumps(obj, separators=(",", ":"))
    log_path.write_text("\n".join(lines) + "\n")
    ok, _msg = verify_chain(log_path)
    assert ok is False, "tamper of redacted chain entry must be detected"


def test_prose_with_secret_keyword_does_not_over_redact() -> None:
    """``password rotation`` (prose) must NOT trigger generic-secret redaction.

    The pattern requires a ``key=val`` form (with ``:``/``=`` separator) so
    natural-language sentences containing 'password' / 'token' / 'authorization'
    keywords stay readable in audit logs.
    """
    r = Redactor()
    cases = [
        "password rotation completed yesterday",
        "token expired and was reissued",
        "authorization granted by the manager",
    ]
    for sentence in cases:
        out = r.redact(sentence)
        assert out == sentence, f"over-redacted prose: {sentence!r} -> {out!r}"
