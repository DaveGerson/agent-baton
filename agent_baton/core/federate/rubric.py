"""Deterministic spec-quality rubric for the Spec Federation queue.

``check_spec_quality(title, body)`` returns a :class:`SpecQualityReport`
with a 0-100 score and a list of missing elements.  All checks are
case-insensitive regex/word searches — no LLM required.

The five heuristics are:

(a) **Verification criteria** — acceptance criteria, done-when, must-pass,
    gate, or test-plan phrases / section headers.
(b) **Scope boundaries** — out-of-scope, in-scope, do-not, don't-touch,
    only/limit phrases that narrow what the spec covers.
(c) **Constraints** — constraint, must-use, require, compat, perf,
    security, deadline keywords.
(d) **Concrete artifacts** — file paths, API routes (``/word``), backtick-
    delimited identifiers, or CLI commands.
(e) **Sufficient size** — body ≥ 40 words *and* title is non-generic.

Score = sum of per-criterion weights (see ``_WEIGHTS``).

Orgs may tune the word-lists and weights by patching the module-level
constants below — they are intentionally exposed at module scope for that
purpose.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Tunable word-lists  (orgs can patch these at startup to extend coverage)
# ---------------------------------------------------------------------------

# (a) Verification-criteria trigger words / phrases
VERIFICATION_PHRASES: list[str] = [
    r"accept\w*\s+criter",       # acceptance criteria
    r"acceptance\s+test",
    r"verif\w+",                  # verify, verification
    r"done[\s\-]when",
    r"definition[\s\-]of[\s\-]done",
    r"success\s+criter",
    r"must\s+pass",
    r"gate",
    r"test\s+plan",
    r"e2e\s+test",
    r"regression\s+test",
    r"checklist",
]

# (b) Scope-boundary trigger words / phrases
SCOPE_PHRASES: list[str] = [
    r"out[\s\-]of[\s\-]scope",
    r"in[\s\-]scope",
    r"do\s+not\s+\w",             # do not touch / do not modify
    r"don[''']?t\s+touch",
    r"don[''']?t\s+modif",
    r"\bonly\b",
    r"\blimit(ed)?\s+to\b",
    r"\bexclud",
    r"\binclude[sd]?\s+only\b",
    r"\bno\s+change\s+to\b",
]

# (c) Constraint trigger words
CONSTRAINT_PHRASES: list[str] = [
    r"\bconstraint",
    r"\bmust[\s\-]use\b",
    r"\brequir[ei]",
    r"\bcompat\w+",
    r"\bbackward[\s\-]compat",
    r"\bperformance\b",
    r"\bperf\b",
    r"\bsecurity\b",
    r"\bdeadline\b",
    r"\bsla\b",
    r"\blatency\b",
    r"\bthroughput\b",
    r"\bno[\s\-]breaking[\s\-]change",
]

# (d) Concrete-artifact patterns  (applied to the raw body text)
ARTIFACT_PATTERNS: list[str] = [
    r"`[^`]+`",                   # anything in backticks
    r"\b[\w\-]+/[\w\-./]+",       # path-like token  (a/b, src/foo.py, etc.)
    r"(?<!\w)/[a-zA-Z][\w/\-]*",  # /api/v1/... style routes
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD)\s+/\w",  # HTTP method + route
    r"\$[\w_]+",                  # shell / env vars
    r"--[\w\-]+",                 # CLI flags
]

# (e) Generic title fragments that indicate a placeholder title
GENERIC_TITLE_FRAGMENTS: list[str] = [
    "fix it",
    "todo",
    "test",
    "untitled",
    "new spec",
    "update",
    "changes",
    "misc",
    "temp",
    "wip",
    "draft",
    r"fix\s*$",
]

# ---------------------------------------------------------------------------
# Scoring weights  (must sum to 100)
# ---------------------------------------------------------------------------

# Each criterion contributes this many points when satisfied.
_WEIGHTS: dict[str, int] = {
    "verification": 30,
    "scope":        20,
    "constraints":  20,
    "artifacts":    20,
    "size":         10,
}

assert sum(_WEIGHTS.values()) == 100, "Weights must sum to 100"

# Minimum body word count considered "sufficient"
_MIN_BODY_WORDS = 40


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------


class SpecQualityReport(NamedTuple):
    """Result of a deterministic spec-quality check.

    Attributes:
        score: 0-100 composite quality score.
        missing: Short "add X" suggestions for absent elements.
        notes: Informational notes (e.g. which heuristics fired).
    """

    score: int
    missing: list[str]
    notes: list[str]

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage."""
        return {
            "score": self.score,
            "missing": list(self.missing),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


def check_spec_quality(title: str, body: str) -> SpecQualityReport:
    """Run all five heuristics and return a :class:`SpecQualityReport`.

    All checks are deterministic and case-insensitive.  No LLM is invoked.

    Args:
        title: Spec draft title.
        body: Spec draft body (plain text or markdown).

    Returns:
        A populated :class:`SpecQualityReport`.
    """
    combined = (title + "\n" + body).lower()
    missing: list[str] = []
    notes: list[str] = []
    score = 0

    # ---- (a) Verification criteria ----------------------------------------
    if _any_match(VERIFICATION_PHRASES, combined):
        score += _WEIGHTS["verification"]
        notes.append("verification criteria: found")
    else:
        missing.append(
            "Add verification criteria: acceptance tests, a 'done-when' "
            "section, or explicit pass/fail conditions."
        )

    # ---- (b) Scope boundaries ---------------------------------------------
    if _any_match(SCOPE_PHRASES, combined):
        score += _WEIGHTS["scope"]
        notes.append("scope boundaries: found")
    else:
        missing.append(
            "Add scope boundaries: name what is in-scope, out-of-scope, "
            "or explicitly excluded."
        )

    # ---- (c) Constraints --------------------------------------------------
    if _any_match(CONSTRAINT_PHRASES, combined):
        score += _WEIGHTS["constraints"]
        notes.append("constraints: found")
    else:
        missing.append(
            "Add constraints: performance, security, compatibility, deadline "
            "requirements, or must-use dependencies."
        )

    # ---- (d) Concrete artifacts -------------------------------------------
    if _any_match(ARTIFACT_PATTERNS, body):  # check raw body, not lowercased
        score += _WEIGHTS["artifacts"]
        notes.append("concrete artifacts: found")
    else:
        missing.append(
            "Add concrete artifacts: file paths, API routes, backtick-quoted "
            "identifiers, or CLI commands."
        )

    # ---- (e) Body length + non-generic title ------------------------------
    word_count = len(body.split())
    title_generic = _title_is_generic(title)
    if word_count >= _MIN_BODY_WORDS and not title_generic:
        score += _WEIGHTS["size"]
        notes.append(f"size/title: ok ({word_count} words, non-generic title)")
    else:
        parts: list[str] = []
        if word_count < _MIN_BODY_WORDS:
            parts.append(
                f"body is too short ({word_count} words; aim for ≥{_MIN_BODY_WORDS})"
            )
        if title_generic:
            parts.append("title appears generic — use a specific, descriptive title")
        missing.append("Improve size/title: " + "; ".join(parts) + ".")

    return SpecQualityReport(score=score, missing=missing, notes=notes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _any_match(patterns: list[str], text: str) -> bool:
    """Return True when any compiled pattern has a match in *text*."""
    for pat in patterns:
        try:
            if re.search(pat, text, flags=re.IGNORECASE):
                return True
        except re.error:
            pass
    return False


def _title_is_generic(title: str) -> bool:
    """Return True when *title* matches a known generic-title fragment."""
    t = title.strip().lower()
    if not t:
        return True
    for frag in GENERIC_TITLE_FRAGMENTS:
        try:
            if re.search(frag, t, flags=re.IGNORECASE):
                return True
        except re.error:
            pass
    return False
