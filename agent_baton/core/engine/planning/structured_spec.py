"""Structured-spec parsing — extracts named phases from a task summary.

When an operator hands the planner a task like::

    Build OAuth onboarding.
    Phase 1: Authentication — implement OAuth callback
    Phase 2: Authorization — token validation + RBAC
    Phase 3: Tenancy — per-org isolation

…the legacy parser detects three phases but throws away the titles
("Authentication", "Authorization", "Tenancy") and labels them
generically as "Phase 1", "Phase 2", "Phase 3".  That loss of
information is one root cause of the plan-explosion incident: the
operator can no longer tell which baton phase corresponds to which
spec phase, so they bypass the planner and dispatch each spec line
manually.

This module contains two helpers:

* :func:`extract_phase_titles` — given the original summary, return
  the list of ``(number, title)`` pairs the planner detected.  Used
  to enrich the bare ``"Phase N"`` names the legacy parser produces.

* :func:`enrich_phase_titles` — given the dicts the legacy parser
  produced, replace each ``name`` with the matching extracted title
  when one is available.

Both are pure functions; ClassificationStage calls them after the
legacy parser to upgrade phase names without changing any of the
legacy parser's other behavior (agent detection, count, ordering).
"""
from __future__ import annotations

import re

# The PHASE_HEADER regex in rules/concerns.py recognises markdown
# headings; for inline summaries we want a more permissive match that
# also accepts em-dashes and hyphens as title delimiters.
_PHASE_TITLE = re.compile(
    r"(?:^|\s)"                          # boundary
    r"(?:phase|step|stage|milestone)\s+" # section keyword
    r"(\d+(?:\.\d+)?)"                   # number (group 1)
    r"\s*[:\-—]\s*"                      # delimiter (colon, hyphen, em-dash)
    r"([^.;\n]{2,80}?)"                  # title (group 2): up to 80 chars,
                                         # stops at sentence/clause break
    r"(?=[.;\n]|$)",                     # followed by terminator or EOS
    re.IGNORECASE,
)


def extract_phase_titles(summary: str) -> list[tuple[str, str]]:
    """Return ``[(number, title), …]`` extracted from *summary*.

    Returns an empty list when no phase headers are found.  Numbers
    preserve their original form (``"1"``, ``"1.1"``, etc.) so callers
    can match them against legacy "Phase N" placeholders.

    The title is whitespace-stripped and any trailing connective
    ("with", "by", "using") is dropped so titles read as nouns.
    """
    titles: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _PHASE_TITLE.finditer(summary):
        number = m.group(1)
        if number in seen:
            continue
        title = m.group(2).strip()
        # If the title contains a body-separator (em-dash, double-hyphen,
        # or "—"), keep only the part before it.  That isolates the
        # noun-phrase title from the descriptive body that follows.
        for sep in (" — ", " -- ", " – "):
            if sep in title:
                title = title.split(sep, 1)[0].rstrip()
                break
        # Drop trailing connectives so the title reads as a noun.
        for connective in (" with", " by", " using"):
            if title.lower().endswith(connective):
                title = title[: -len(connective)].rstrip()
                break
        if title:
            titles.append((number, title))
            seen.add(number)
    return titles


def enrich_phase_titles(
    phase_dicts: list[dict],
    summary: str,
) -> list[dict]:
    """Replace generic ``"Phase N"`` names with extracted titles.

    Mutates and returns the input list (mutating to keep API symmetry
    with the legacy parser, which does the same).  When *summary*
    yields no titles, *phase_dicts* is returned unchanged.
    """
    if not phase_dicts:
        return phase_dicts
    titles = extract_phase_titles(summary)
    if not titles:
        return phase_dicts

    title_by_number: dict[str, str] = dict(titles)
    for idx, phase in enumerate(phase_dicts, start=1):
        # Match by ordinal first (Phase 1 → first dict), then by the
        # number embedded in the legacy-produced name.
        title = title_by_number.get(str(idx))
        if not title:
            existing_name = phase.get("name", "")
            m = re.search(r"\d+(?:\.\d+)?", existing_name)
            if m:
                title = title_by_number.get(m.group(0))
        if title:
            # Preserve the ordinal prefix for traceability:
            # "Phase 1: Authentication" is more useful than just "Authentication".
            phase["name"] = f"Phase {idx}: {title}"
    return phase_dicts
