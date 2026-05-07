"""Shared command-safety helpers for gate-command validation.

Used by both :mod:`artifact_validator` (workflow ``run:`` lines) and
:mod:`gate_addition` (agent-declared ``GATE_ADDITION:`` commands) to
apply a consistent defence-in-depth layer before any agent-authored
string reaches the shell.

Public surface
--------------
- :func:`is_safe_gate_command` — rejects shell metacharacters that would
  allow a caller to smuggle in additional chaining or redirection.
- :func:`is_destructive` — rejects well-known destructive command patterns.
- :data:`MAX_GATE_COMMAND_LENGTH` — hard length cap (256 bytes).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_GATE_COMMAND_LENGTH: int = 256

# ---------------------------------------------------------------------------
# Shell metacharacter denylist
# ---------------------------------------------------------------------------

# Characters / substrings that allow shell injection or output redirection
# when a command is concatenated with && by the gate runner.  We allow a
# single quoted arg to contain most of these inside quotes, but we can't
# parse shell quoting reliably here — applying a conservative string-level
# check is the right tradeoff for a defence-in-depth layer.
#
# Banned: ; && || | > < backtick $( & (backgrounding) newline null byte
#
# Note: we check for '&&' explicitly.  The gate runner itself chains
# commands with &&, but a caller embedding '&&' in their command body would
# introduce an uncontrolled chain.  '||' is also banned for the same reason.
_SHELL_META_RE = re.compile(
    r";|&&|\|\||[|><`]|\$\(|&|\n|\r|\x00"
)


def is_safe_gate_command(cmd: str) -> bool:
    """Return True when *cmd* contains no dangerous shell metacharacters.

    Specifically rejects commands that include:
    - ``;``  — statement separator / sequence operator
    - ``&&`` — AND-list (chain injection)
    - ``||`` — OR-list (chain injection)
    - ``|``  — pipeline
    - ``>``  — output redirection
    - ``<``  — input redirection
    - backtick — command substitution
    - ``$(`` — command substitution
    - ``&``  — background operator
    - newline or carriage return — multi-command injection
    - null byte — string termination injection

    A command like ``pytest tests/ -q`` with quoted arguments containing
    spaces will pass; a command that embeds ``; rm -rf /`` will not.
    """
    return _SHELL_META_RE.search(cmd) is None


# ---------------------------------------------------------------------------
# Destructive pattern denylist
# ---------------------------------------------------------------------------

# Each pattern is compiled once at import time.  Match case-insensitively
# where the command name itself is typically lowercase but arguments may
# vary.  We use word boundaries (\b) on command names to avoid false
# positives on path fragments or test file names that happen to contain
# a denylist word.
#
# Judgment on word-boundary tightness:
#   ``\brm\s+-[rRf]+`` matches ``rm -rf`` / ``rm -fr`` / ``rm -r`` but
#   NOT ``pytest tests/test_aws_s3_rm.py`` — the word boundary before
#   ``rm`` only fires when ``rm`` is a complete token (preceded by a
#   non-word char or start-of-string).
#
#   ``\baws\s+s3\s+rm\b`` matches ``aws s3 rm`` but NOT
#   ``pytest tests/test_aws_s3_rm.py``.
#
#   ``\bgit\s+push\s+--force\b`` is intentionally tight — ``git push``
#   alone is allowed; only ``git push --force`` is rejected.
#   ``\bgit\s+reset\s+--hard\b`` likewise.
#
#   ``\bcurl\b.*\|\s*(?:sh|bash)\b`` requires both ``curl`` AND a pipe
#   into sh/bash in the same command string.  A plain ``curl`` download
#   without piping is not rejected here (it may be useful in a gate).
#
# False-positive risk:
#   Patterns with ``\bsudo\b`` will reject any command that contains the
#   word ``sudo`` as a standalone token.  This is intentional — gates
#   should never run with elevated privileges.
#
#   ``\bcurl\b`` alone is NOT in the denylist; only the pipe-to-shell
#   variant is.  A ``curl`` download to a file is a legitimate gate step
#   (e.g. downloading a test fixture).

_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Recursive/forced file deletion
    re.compile(r"\brm\s+-[rRfFbBiIvV]*[rRf][rRfFbBiIvV]*", re.IGNORECASE),
    # Recursive permission changes
    re.compile(r"\bchmod\s+-R\b", re.IGNORECASE),
    # World-writable permission grant
    re.compile(r"\bchmod\s+0?7?77\b"),
    # Recursive ownership change
    re.compile(r"\bchown\s+-R\b", re.IGNORECASE),
    # Disk-overwrite via dd
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    # Filesystem creation (wipes a block device)
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    # Privilege escalation
    re.compile(r"\bsudo\b"),
    # Pipe-to-shell via curl or wget (supply-chain attack vector)
    re.compile(r"\bcurl\b.*\|\s*(?:sh|bash)\b", re.IGNORECASE),
    re.compile(r"\bwget\b.*\|\s*(?:sh|bash)\b", re.IGNORECASE),
    # Cloud storage bulk-delete
    re.compile(r"\baws\s+s3\s+rm\b", re.IGNORECASE),
    # Terraform auto-apply without review
    re.compile(r"\bterraform\s+apply\b.*-auto-approve", re.IGNORECASE),
    # Fork bomb
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:&\s*\}\s*;:"),
    # Forced git push (overwrites remote history)
    re.compile(r"\bgit\s+push\s+--force\b", re.IGNORECASE),
    # Hard git reset (discards local commits irreversibly)
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
)


def is_destructive(cmd: str) -> bool:
    """Return True when *cmd* matches any known destructive command pattern.

    Designed to catch well-known destructive shell commands before they
    reach the gate runner.  The check is best-effort and defence-in-depth
    — it does not substitute for a proper sandbox.

    See module-level comments for word-boundary decisions and
    false-positive risk analysis.
    """
    return any(p.search(cmd) is not None for p in _DESTRUCTIVE_PATTERNS)
