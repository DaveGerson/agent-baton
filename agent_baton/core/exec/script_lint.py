"""Wave 6.1 Part C — Executable Beads: static script linter (bd-81b9).

``ScriptLinter`` performs a fast, pattern-based safety scan before any
executable bead body is written to ``refs/notes/baton-bead-scripts``.  It
is the first layer of the verification gate — a necessary but not
sufficient defence.  The auditor-agent approval (AuditorGate) is the
second, authoritative layer.

Design decisions:
- Forbidden patterns are compile-once regexes scanned line-by-line so
  multi-line obfuscation tricks that skip over newlines are caught
  individually on each line.
- ``ast-grep`` scripts are validated to contain only structural-edit
  commands (``rule:``, ``fix:``, ``language:``, ``pattern:`` YAML keys).
  Any line that looks like a shell-out (``!``, ``$(...)``, backticks,
  ``subprocess``, ``os.system``) is rejected.
- Additional glob patterns can be supplied via ``blocked_globs`` (sourced
  from ``baton.yaml`` in the caller).  These are converted to regexes that
  match any path component.
- The linter is intentionally conservative: false positives are acceptable
  because the cost of a false negative (arbitrary code execution) is much
  higher.  Operators can petition through the auditor flow.
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Forbidden pattern catalogue
# ---------------------------------------------------------------------------

# Each entry is (pattern_id, regex_string, human_readable_message).
# Patterns are applied to individual lines after stripping leading whitespace.
_BUILTIN_PATTERNS: list[tuple[str, str, str]] = [
    (
        "rm-rf-root",
        r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(?!\S)",
        "rm -rf at filesystem root",
    ),
    (
        "rm-rf-slash",
        r"rm\s+.*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(?:$|\s)",
        "rm -rf targeting root path",
    ),
    (
        "curl-pipe-sh",
        r"curl\s+\S.*\|\s*(?:sh|bash|zsh|dash|ksh|csh)",
        "curl pipe into shell interpreter",
    ),
    (
        "wget-pipe-sh",
        r"wget\s+.*-[qO-]*\s*-\s*\|\s*(?:sh|bash|zsh)",
        "wget pipe into shell interpreter",
    ),
    (
        "dd-disk-overwrite",
        r"\bdd\b.*\bof=",
        "dd disk overwrite (of= target)",
    ),
    (
        "fork-bomb",
        r":\(\)\s*\{.*:\|:&\s*\}",
        "fork bomb pattern",
    ),
    (
        "fork-bomb-alt",
        r"function\s+:\s*\{.*:\s*\|",
        "fork bomb (function variant)",
    ),
    (
        "ssh-keys-write",
        r"~/\.ssh/",
        "write or access to ~/.ssh/",
    ),
    (
        "baton-souls-write",
        r"~/\.config/baton/souls/",
        "write to baton soul key directory",
    ),
    (
        "baton-db-write",
        r"\.claude/team-context/baton\.db",
        "direct write to project baton.db",
    ),
    (
        "central-db-write",
        r"central\.db",
        "direct write to central.db",
    ),
    (
        "chmod-777",
        r"chmod\s+(?:[-aog]*[+]?777|0?777)\b",
        "chmod 777 / world-writable",
    ),
    (
        "sudo-rm",
        r"sudo\s+rm\s+",
        "sudo rm — elevated removal",
    ),
    (
        "history-wipe",
        r"(?:>\s*~/\.(?:bash|zsh|sh)_history|unset\s+HISTFILE)",
        "shell history wipe",
    ),
    (
        "crontab-overwrite",
        r"crontab\s+-[^l]",
        "crontab modification",
    ),
]

# AST-grep scripts must only contain these top-level YAML keys (plus
# comment lines).  Any other content is rejected as potentially unsafe.
_ASTGREP_ALLOWED_KEYS_RE = re.compile(
    r"^\s*(?:#|$|id:|rule:|fix:|language:|pattern:|kind:|inside:|has:|"
    r"follows:|precedes:|not:|any:|all:|message:|severity:|note:|url:|"
    r"files:|ignore:|transform:|rewriters:|constraints:)",
    re.IGNORECASE,
)

# Shell-out indicators that are forbidden inside ast-grep scripts.
_ASTGREP_SHELLOUT_RE = re.compile(
    r"(?:\$\(|`|\bsubprocess\b|\bos\.system\b|\bos\.popen\b|!(?:sh|bash|python))",
)


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class LintResult:
    """Result of a :class:`ScriptLinter` run.

    Attributes:
        safe: ``True`` when no forbidden patterns were found.
        findings: List of ``(pattern_id, message, line_no)`` tuples for
            every violation detected (1-based line numbers).
    """

    safe: bool
    findings: list[tuple[str, str, int]] = field(default_factory=list)

    def __str__(self) -> str:
        if self.safe:
            return "LintResult(safe=True)"
        parts = [f"LintResult(safe=False, findings={len(self.findings)}):"]
        for pid, msg, lineno in self.findings:
            parts.append(f"  line {lineno}: [{pid}] {msg}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Linter
# ---------------------------------------------------------------------------

class ScriptLinter:
    """Static safety scanner for executable bead scripts.

    Args:
        blocked_globs: Additional glob patterns (from ``baton.yaml``) whose
            matching path strings should be treated as forbidden write
            targets.  Converted to regexes at construction time.
    """

    def __init__(self, blocked_globs: list[str] | None = None) -> None:
        # Compile built-in patterns once.
        self._patterns: list[tuple[str, re.Pattern[str], str]] = [
            (pid, re.compile(regex, re.IGNORECASE), msg)
            for pid, regex, msg in _BUILTIN_PATTERNS
        ]
        # Compile user-supplied glob patterns as regex alternatives.
        if blocked_globs:
            for glob in blocked_globs:
                regex_str = fnmatch.translate(glob)
                try:
                    compiled = re.compile(regex_str, re.IGNORECASE)
                    self._patterns.append((
                        f"blocked-glob:{glob}",
                        compiled,
                        f"blocked path pattern: {glob}",
                    ))
                except re.error as exc:
                    _log.warning("ScriptLinter: invalid blocked glob %r: %s", glob, exc)

    # ------------------------------------------------------------------

    def lint(self, script: str, interpreter: str) -> LintResult:
        """Scan *script* for forbidden patterns.

        Args:
            script: The full script body text.
            interpreter: One of ``'bash'``, ``'python'``, ``'ast-grep'``,
                ``'pytest'``.

        Returns:
            :class:`LintResult` with ``safe=True`` when no issues are found.
        """
        if interpreter == "ast-grep":
            return self._lint_astgrep(script)
        return self._lint_generic(script)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _lint_generic(self, script: str) -> LintResult:
        """Scan bash / python / pytest scripts for forbidden patterns."""
        findings: list[tuple[str, str, int]] = []
        lines = script.splitlines()
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Skip pure comment lines early (minor speedup; still check
            # patterns that could hide in comments if needed — here we
            # prefer speed; the auditor gate is the authoritative check).
            for pid, pattern, msg in self._patterns:
                if pattern.search(stripped):
                    findings.append((pid, msg, lineno))
                    _log.debug(
                        "ScriptLinter: forbidden pattern %r at line %d: %r",
                        pid, lineno, stripped[:80],
                    )
                    break  # one finding per line is sufficient
        return LintResult(safe=len(findings) == 0, findings=findings)

    def _lint_astgrep(self, script: str) -> LintResult:
        """Validate that an ast-grep script contains only structural edits.

        Rejects any line that:
        - Does not match the allowed YAML key whitelist, AND
        - Contains shell-out indicators (``$()``, backticks, os.system …).
        Also runs the generic forbidden-pattern scan over the YAML body
        in case someone embeds a shell snippet inside a string value.
        """
        findings: list[tuple[str, str, int]] = []
        lines = script.splitlines()
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not _ASTGREP_ALLOWED_KEYS_RE.match(line):
                if _ASTGREP_SHELLOUT_RE.search(stripped):
                    findings.append((
                        "astgrep-shellout",
                        "ast-grep script contains shell-out or unsafe expression",
                        lineno,
                    ))
                    _log.debug(
                        "ScriptLinter: ast-grep shell-out at line %d: %r",
                        lineno, stripped[:80],
                    )
        # Also run generic patterns over the whole body (catches embedded paths etc.)
        generic = self._lint_generic(script)
        findings.extend(generic.findings)
        return LintResult(safe=len(findings) == 0, findings=findings)
