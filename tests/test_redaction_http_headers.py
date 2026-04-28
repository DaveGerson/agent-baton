"""Tests for HTTP-header-style secret redaction in redact_sensitive() (bd-73a9)."""
from __future__ import annotations

import pytest

from agent_baton.core.runtime._redaction import redact_sensitive


def test_redacts_authorization_bearer() -> None:
    raw = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig"
    result = redact_sensitive(raw)
    assert "eyJhbGciOiJSUzI1NiJ9" not in result
    assert "Authorization: Bearer ***REDACTED***" in result


def test_redacts_authorization_basic() -> None:
    raw = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
    result = redact_sensitive(raw)
    assert "dXNlcjpwYXNzd29yZA==" not in result
    assert "Authorization: Basic ***REDACTED***" in result


def test_redacts_cookie_header() -> None:
    raw = "Cookie: session=abc123; csrf=xyz789"
    result = redact_sensitive(raw)
    assert "abc123" not in result
    assert "Cookie: ***REDACTED***" in result


def test_redacts_set_cookie_header() -> None:
    raw = "Set-Cookie: session_id=s3cr3t; Path=/; HttpOnly"
    result = redact_sensitive(raw)
    assert "s3cr3t" not in result
    assert "Set-Cookie: ***REDACTED***" in result


def test_redacts_x_api_key() -> None:
    raw = "X-Api-Key: my-super-secret-key-12345"
    result = redact_sensitive(raw)
    assert "my-super-secret-key-12345" not in result
    assert "X-Api-Key: ***REDACTED***" in result


def test_redacts_x_auth_token() -> None:
    raw = "X-Auth-Token: tok_abcdef0123456789"
    result = redact_sensitive(raw)
    assert "tok_abcdef0123456789" not in result
    assert "X-Auth-Token: ***REDACTED***" in result


def test_case_insensitive_header_name() -> None:
    # Lower-case header names (non-canonical but real in logs/curl -v output)
    assert "***REDACTED***" in redact_sensitive("authorization: Bearer secret_tok")
    assert "***REDACTED***" in redact_sensitive("AUTHORIZATION: Basic dXNlcjpwYXNz")
    assert "***REDACTED***" in redact_sensitive("cookie: user=dave; token=abc")
    assert "***REDACTED***" in redact_sensitive("x-api-key: lowercase_key")
    assert "***REDACTED***" in redact_sensitive("x-auth-token: lowercase_token")


def test_preserves_other_text_around_header() -> None:
    raw = (
        "< HTTP/1.1 200 OK\r\n"
        "< Content-Type: application/json\r\n"
        "< Authorization: Bearer supersecret\r\n"
        "< Content-Length: 42\r\n"
    )
    result = redact_sensitive(raw)
    assert "supersecret" not in result
    assert "HTTP/1.1 200 OK" in result
    assert "Content-Type: application/json" in result
    assert "Content-Length: 42" in result


def test_redacts_inside_json_string() -> None:
    raw = '{"Authorization": "Bearer eyJhbGci.payload.sig", "other": "value"}'
    result = redact_sensitive(raw)
    assert "eyJhbGci.payload.sig" not in result
    assert '"Authorization": "Bearer ***REDACTED***"' in result
    assert '"other": "value"' in result


def test_does_not_match_unrelated_text() -> None:
    # Should be returned unchanged — no false positives
    safe = "Content-Type: application/json\r\nAccept: */*\r\nHost: example.com"
    assert redact_sensitive(safe) == safe
