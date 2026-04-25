"""Sensitive-data redaction for compliance audit log entries.

G1.5 Governance Maturity (bd-1a09).

This module provides a :class:`Redactor` that scrubs common secret
patterns (emails, SSNs, AWS keys, API tokens, JWTs, private keys, IPs)
from arbitrary strings or nested JSON-like payloads.  Matches are
replaced with ``[REDACTED:<kind>]`` so the audit trail still records
what category was scrubbed without leaking the value.

The redactor is integrated into :class:`ComplianceChainWriter.append`
so that hashed entries are *already* redacted on disk — making the
hash chain verifiable from the redacted payload and ensuring post-hoc
redaction cannot rewrite history.

Toggle redaction off via the ``BATON_REDACTION_ENABLED=0`` environment
variable.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Pattern catalog — compiled once at import time.
# ---------------------------------------------------------------------------

# Each tuple is (kind, compiled_pattern, replacement_template).
# Replacement templates use the literal "[REDACTED:<kind>]" form so that
# downstream consumers can grep on a stable token.
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----",
)

_JWT_PATTERN = re.compile(
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
)

_AWS_ACCESS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# AWS secret keys are 40-char base64-ish strings; high false-positive risk
# unless qualified by a key=value context (e.g. AWS_SECRET_ACCESS_KEY=...).
_AWS_SECRET_KEY_PATTERN = re.compile(
    r"(AWS_SECRET[A-Z_]*\s*[:=]\s*['\"]?)([A-Za-z0-9/+=]{40})(['\"]?)",
)

# Generic API key / token / password / bearer credentials.
# Matches: api_key="...", token: '...', authorization: ..., bearer <tok>, etc.
# The optional ``bearer\s+`` prefix between separator and value lets us catch
# the common ``Authorization: Bearer <tok>`` form in a single pass.
_GENERIC_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|bearer)"
    r"(\s*[:=]\s*)"
    r"(?:bearer\s+)?"
    r"['\"]?(?!\[REDACTED:)([^\s'\"<>]{6,})['\"]?",
)

_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b",
)

_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_IPV4_PATTERN = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b",
)


def _replace_private_key(_m: re.Match[str]) -> str:
    return "[REDACTED:private_key]"


def _replace_jwt(_m: re.Match[str]) -> str:
    return "[REDACTED:jwt]"


def _replace_aws_access_key(_m: re.Match[str]) -> str:
    return "[REDACTED:aws_access_key]"


def _replace_aws_secret_key(m: re.Match[str]) -> str:
    # Preserve the key=val prefix/suffix quoting so the surrounding
    # context (e.g. shell snippet) remains readable.
    return f"{m.group(1)}[REDACTED:aws_secret_key]{m.group(3)}"


def _replace_generic_secret(m: re.Match[str]) -> str:
    return f"{m.group(1)}{m.group(2)}[REDACTED:secret]"


def _replace_email(_m: re.Match[str]) -> str:
    return "[REDACTED:email]"


def _replace_ssn(_m: re.Match[str]) -> str:
    return "[REDACTED:ssn]"


def _replace_ipv4(_m: re.Match[str]) -> str:
    return "[REDACTED:ipv4]"


# Order matters: high-confidence / specific patterns first so they consume
# their matches before broader patterns get a chance.
_DEFAULT_PATTERNS: list[tuple[str, re.Pattern[str], Any]] = [
    ("private_key", _PRIVATE_KEY_PATTERN, _replace_private_key),
    ("jwt", _JWT_PATTERN, _replace_jwt),
    ("aws_access_key", _AWS_ACCESS_KEY_PATTERN, _replace_aws_access_key),
    ("aws_secret_key", _AWS_SECRET_KEY_PATTERN, _replace_aws_secret_key),
    ("secret", _GENERIC_SECRET_PATTERN, _replace_generic_secret),
    ("email", _EMAIL_PATTERN, _replace_email),
    ("ssn", _SSN_PATTERN, _replace_ssn),
    ("ipv4", _IPV4_PATTERN, _replace_ipv4),
]


class Redactor:
    """Scrub sensitive substrings from text or nested JSON-like payloads.

    Each call to :meth:`redact` or :meth:`redact_payload` resets the
    per-call counters.  Use :attr:`last_counts` to learn which categories
    fired during the most recent operation — useful for governance
    telemetry without exposing the redacted values themselves.

    Args:
        patterns: Optional override of the default pattern catalog.  Each
            entry is a ``(kind, compiled_regex, replacement)`` tuple where
            ``replacement`` is either a string or a callable accepted by
            :func:`re.sub`.  Defaults to the module-level catalog.
        include_ipv4: Whether to redact IPv4 addresses.  IPs are often
            legitimate (telemetry, log shipping, version strings like
            ``chrome/120.0.0.0``) so callers may opt in.  Defaults to
            ``False`` to minimise audit-log debuggability damage.
    """

    def __init__(
        self,
        patterns: Iterable[tuple[str, re.Pattern[str], Any]] | None = None,
        *,
        include_ipv4: bool = False,
    ) -> None:
        if patterns is None:
            cat = list(_DEFAULT_PATTERNS)
            if not include_ipv4:
                cat = [p for p in cat if p[0] != "ipv4"]
            self._patterns: list[tuple[str, re.Pattern[str], Any]] = cat
        else:
            self._patterns = list(patterns)
        self.last_counts: dict[str, int] = {}

    def _reset_counts(self) -> None:
        self.last_counts = {kind: 0 for kind, _pat, _rep in self._patterns}

    def redact(self, text: str) -> str:
        """Redact sensitive substrings from *text*.

        Resets :attr:`last_counts` and updates it with per-pattern hit
        counts as a side effect.

        Args:
            text: Input string.  Non-strings are returned unchanged after
                being coerced via ``str()`` for safety.

        Returns:
            The redacted string with each match replaced by
            ``[REDACTED:<kind>]``.
        """
        self._reset_counts()
        return self._apply(text)

    def _apply(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        out = text
        for kind, pattern, replacement in self._patterns:
            new_out, n = pattern.subn(replacement, out)
            if n:
                self.last_counts[kind] = self.last_counts.get(kind, 0) + n
            out = new_out
        return out

    def redact_payload(self, obj: Any) -> Any:
        """Recursively redact strings inside dicts, lists, and tuples.

        Resets :attr:`last_counts` once at the top level and accumulates
        hits across the whole tree.  Non-string scalars (ints, floats,
        bools, ``None``) pass through untouched.

        Args:
            obj: Arbitrary JSON-serialisable value.

        Returns:
            A new structure of the same shape with strings redacted.
            Dict keys are NOT redacted (they are typically schema names).
        """
        self._reset_counts()
        return self._walk(obj)

    def _walk(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self._apply(obj)
        if isinstance(obj, dict):
            return {k: self._walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._walk(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self._walk(v) for v in obj)
        return obj


# ---------------------------------------------------------------------------
# Module singleton + config switch
# ---------------------------------------------------------------------------

_DEFAULT_REDACTOR: Redactor | None = None


def default_redactor() -> Redactor:
    """Return the process-wide default :class:`Redactor` singleton."""
    global _DEFAULT_REDACTOR
    if _DEFAULT_REDACTOR is None:
        _DEFAULT_REDACTOR = Redactor()
    return _DEFAULT_REDACTOR


def redaction_enabled() -> bool:
    """Return whether redaction should run (env var ``BATON_REDACTION_ENABLED``).

    Default is ``True``.  Set ``BATON_REDACTION_ENABLED=0`` (or ``false`` /
    ``no``) to disable.  Disabling is intended for test fixtures and
    deliberate plaintext audits — production callers should leave it on.
    """
    raw = os.environ.get("BATON_REDACTION_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "")
