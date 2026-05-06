"""Shared sensitive-data redaction patterns for runtime output capture.

Both :mod:`~agent_baton.core.runtime.claude_launcher` and
:mod:`~agent_baton.core.runtime.headless` apply these patterns to captured
stdout/stderr before any persistence (step results, traces, retrospectives).

Patterns covered (A5):
- Anthropic API keys (``sk-ant-*``)
- GitHub personal access tokens (``ghp_*``, ``github_pat_*``)
- Slack bot/user tokens (``xoxb-*``, ``xoxp-*``)
- Generic JSON ``password``/``secret``/``token``/``api_key`` fields
- HTTP header secrets: Authorization (Bearer/Basic), Cookie, Set-Cookie,
  X-Api-Key, X-Auth-Token (bd-73a9)
- JSON Authorization Bearer strings
"""
from __future__ import annotations

import re

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Anthropic API keys
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "sk-ant-***REDACTED***"),
    # GitHub personal access tokens (classic and fine-grained)
    (re.compile(r"ghp_[A-Za-z0-9_]+"), "ghp_***REDACTED***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]+"), "github_pat_***REDACTED***"),
    # Slack bot/user tokens
    (re.compile(r"xoxb-[A-Za-z0-9_-]+"), "xoxb-***REDACTED***"),
    (re.compile(r"xoxp-[A-Za-z0-9_-]+"), "xoxp-***REDACTED***"),
    # Generic JSON password fields  (e.g. {"password": "hunter2"})
    (
        re.compile(r'"password"\s*:\s*"[^"]*"', re.IGNORECASE),
        '"password": "***REDACTED***"',
    ),
    # Generic JSON secret/token fields
    (
        re.compile(r'"(?:secret|token|api_key|apikey)"\s*:\s*"[^"]*"', re.IGNORECASE),
        r'"***REDACTED_KEY***": "***REDACTED***"',
    ),
    # HTTP header: Authorization: Bearer <token>  (bd-73a9)
    (
        re.compile(r"(Authorization\s*:\s*Bearer\s+)\S+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # HTTP header: Authorization: Basic <credentials>  (bd-73a9)
    (
        re.compile(r"(Authorization\s*:\s*Basic\s+)\S+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # HTTP header: Cookie: <value>  (bd-73a9)
    (
        re.compile(r"(Cookie\s*:)[^\r\n]+", re.IGNORECASE),
        r"\1 ***REDACTED***",
    ),
    # HTTP header: Set-Cookie: <value>  (bd-73a9)
    (
        re.compile(r"(Set-Cookie\s*:)[^\r\n]+", re.IGNORECASE),
        r"\1 ***REDACTED***",
    ),
    # HTTP header: X-Api-Key: <value>  (bd-73a9)
    (
        re.compile(r"(X-Api-Key\s*:\s*)\S+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # HTTP header: X-Auth-Token: <value>  (bd-73a9)
    (
        re.compile(r"(X-Auth-Token\s*:\s*)\S+", re.IGNORECASE),
        r"\1***REDACTED***",
    ),
    # JSON Authorization Bearer strings (bd-73a9)
    (
        re.compile(r'("Authorization"\s*:\s*"Bearer )[^"]+(")', re.IGNORECASE),
        r"\1***REDACTED***\2",
    ),
)


def redact_sensitive(text: str) -> str:
    """Strip known sensitive patterns from captured output or error text.

    Applied to both outcome and error text before storage in step results,
    traces, and retrospectives (A5 — stdout redaction for sensitive data).

    Args:
        text: Raw captured text that may contain secrets.

    Returns:
        The text with all recognised sensitive patterns replaced by their
        placeholder equivalents.
    """
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
