"""Deterministic scope-contract primitives: path normalization, directory-
prefix matching, generated-file policy, and write-scope derivation.

Context: agent-baton middle-manager hardening plan, Phase 3 "Make scope
contracts authoritative". Manager-mode's PMO layer (``agent_baton.core.
manager.scope`` / ``agent_baton.core.manager.planner``) previously treated
a step's ``allowed_paths`` as advisory: an empty list silently fell back to
coarse, sometimes-empty defaults, and a step with no explicit paths could
end up inheriting a sibling's -- or the whole workstream's -- write scope.
This module is the shared, pure (no clock, no randomness, no filesystem
writes) foundation that replaces that ad hoc behavior:

* :func:`normalize_scope_path` -- the path normalization contract every
  allowed/blocked path is put through before comparison or storage.
* :func:`paths_overlap` -- directory-prefix containment, used both to
  detect a path *inside* an allowed area and a path that collides with a
  blocked one.
* :func:`is_generated_path` -- the generated-file policy: build/tooling
  output is excluded from *inferred* evidence (an agent is never granted
  write access to ``dist/`` just because a deliverable string mentioned
  it), but an operator who *explicitly* lists a generated path is always
  honored -- see :func:`derive_allowed_paths`'s ``explicit_paths`` tier.
* :data:`WRITE_CAPABLE_STEP_TYPES` / :data:`READ_ONLY_STEP_TYPES` -- the
  step-type classification that answers "does this step need write scope
  at all?" ``developing``, ``testing``, ``automation``, and ``synthesis``
  are write-capable; ``reviewing`` and ``consulting`` are intentionally
  read-only and must never be silently handed a workstream's write scope.
* :func:`derive_allowed_paths` -- the deterministic evidence pipeline:
  decomposition evidence (explicit paths + path-shaped deliverable text)
  -> context files -> repository topology (charter-confirmed real
  directories) -> agent role conventions (only ever a *filter* over
  candidates that already exist on disk -- never an invented path).
* :func:`diagnose_step_scope` -- classifies a step's resolved scope as
  clean, ambiguous (write-capable with no derivable paths), or
  contradictory (an allowed path collides with a blocked one).
* :class:`ScopeContractError` -- raised by callers that opt into strict
  enforcement (see ``agent_baton.core.manager.planner.ManagerModePlanner``'s
  ``strict_scope`` constructor flag) for ambiguous/contradictory scope.

This module has no manager-mode dependency (it lives under ``core/engine/
planning/`` and only depends on the standard library) so it can be reused
by any future planning-side consumer, not just the PMO layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "WRITE_CAPABLE_STEP_TYPES",
    "READ_ONLY_STEP_TYPES",
    "GENERATED_PATH_MARKERS",
    "ROLE_PATH_HINTS",
    "ScopeContractError",
    "ScopeDiagnostic",
    "normalize_scope_path",
    "normalize_path_list",
    "paths_overlap",
    "path_within",
    "is_generated_path",
    "is_write_capable",
    "is_intentionally_read_only",
    "path_candidates_from_text",
    "derive_allowed_paths",
    "diagnose_step_scope",
]

# ---------------------------------------------------------------------------
# Step-type classification
# ---------------------------------------------------------------------------

# Step types that dispatch an agent expected to change the working tree.
# A write-capable step with no resolvable ``allowed_paths`` is "ambiguous
# write scope" -- the exact condition this module exists to catch.
WRITE_CAPABLE_STEP_TYPES: frozenset[str] = frozenset(
    {"developing", "testing", "automation", "synthesis"}
)

# Step types that are intentionally read-only by convention: they produce
# verdicts, reports, or investigative findings, not code changes. An empty
# ``allowed_paths`` on one of these is *valid* and must be represented as
# such -- never silently backfilled with a workstream's or repo's write
# scope (that is exactly "accidentally granting the repository").
READ_ONLY_STEP_TYPES: frozenset[str] = frozenset({"reviewing", "consulting"})


def is_write_capable(step_type: str) -> bool:
    """True when *step_type* dispatches an agent expected to write files."""
    return (step_type or "") in WRITE_CAPABLE_STEP_TYPES


def is_intentionally_read_only(step_type: str) -> bool:
    """True when *step_type* is read-only by convention (see module docs)."""
    return (step_type or "") in READ_ONLY_STEP_TYPES


# ---------------------------------------------------------------------------
# Path normalization contract
# ---------------------------------------------------------------------------

_DUPLICATE_SLASH_RE = re.compile(r"/{2,}")
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:/")


class ScopeContractError(ValueError):
    """A step's write scope is malformed, ambiguous, or contradictory."""


def normalize_scope_path(raw: str) -> str:
    """Normalize *raw* into a repo-relative, forward-slash path string.

    Contract (binding for every ``allowed_paths``/``blocked_paths`` entry
    this module touches):

    * Cross-platform: backslashes become forward slashes first, so a
      Windows-authored path (``app\\reporting\\service.py``) and a
      POSIX-authored one normalize identically.
    * Duplicate slashes collapse; a leading ``./`` is stripped; a trailing
      slash is stripped (directories and files compare equally under
      :func:`paths_overlap` -- the trailing slash carries no information
      once containment is prefix-based).
    * Repo-relative only: an absolute POSIX path (leading ``/``), a
      Windows drive path (``C:/...``), or a UNC path (``//host/...``) is
      rejected -- a scope contract only ever describes paths inside the
      repository the plan was built for.
    * No traversal: any ``..`` path segment is rejected -- normalization
      never silently collapses ``..`` (that would let ``a/../../etc`` look
      like ``a`` survives when it actually escapes); it fails closed
      instead.
    * Glob markers (``*``, ``**``) are passed through unchanged as regular
      path segments -- callers that care about glob semantics (see
      :func:`paths_overlap`) interpret them; normalization itself treats
      them as opaque segment text.

    Raises :class:`ScopeContractError` for anything that fails these
    checks, so a malformed or adversarial path is caught at plan-build
    time rather than silently reaching a dispatched agent.
    """
    if raw is None:
        raise ScopeContractError("scope path is None")
    text = str(raw).strip()
    if not text:
        raise ScopeContractError("scope path is empty")

    text = text.replace("\\", "/")
    text = _DUPLICATE_SLASH_RE.sub("/", text)
    while text.startswith("./"):
        text = text[2:]

    if text in ("", "."):
        raise ScopeContractError(f"scope path normalizes to empty: {raw!r}")
    if text.startswith("/") or text.startswith("//") or _DRIVE_LETTER_RE.match(text):
        raise ScopeContractError(
            f"scope path must be repo-relative, got an absolute path: {raw!r}"
        )

    segments = [seg for seg in text.split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in segments):
        raise ScopeContractError(
            f"scope path traverses outside the repository root: {raw!r}"
        )
    if not segments:
        raise ScopeContractError(f"scope path normalizes to empty: {raw!r}")

    return "/".join(segments)


def normalize_path_list(paths: "list[str] | None") -> list[str]:
    """Normalize *paths*, dropping falsy entries, order-preserving dedupe.

    Unlike :func:`normalize_scope_path`, this never raises for the list as
    a whole -- an individual malformed entry is skipped (not silently
    kept) rather than aborting normalization of the rest of the list.
    Callers that need fail-closed behavior for a single path should call
    :func:`normalize_scope_path` directly.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        if not raw:
            continue
        try:
            candidate = normalize_scope_path(raw)
        except ScopeContractError:
            continue
        if candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def paths_overlap(candidate: str, allowed: str) -> bool:
    """Directory-prefix containment: is *candidate* inside *allowed*?

    True when:

    * *candidate* equals *allowed* exactly, or
    * *allowed* ends in a ``**`` glob segment and *candidate* falls under
      the directory the glob is rooted at, or
    * *candidate* is a path segment-wise descendant of *allowed* (i.e.
      *allowed* names a directory that contains *candidate*), or
    * *allowed* is a path segment-wise descendant of *candidate* (a
      coarser *candidate* directory already covers the more specific
      *allowed* entry -- used by blocked-path collision checks, where
      either side may be the more specific one).

    Both arguments are normalized internally, so callers may pass raw
    (un-normalized) strings; a malformed path never overlaps anything
    (returns ``False`` rather than raising, since this is a predicate, not
    a validator -- see :func:`diagnose_step_scope` for the validating
    caller).
    """
    try:
        c = normalize_scope_path(candidate)
        a = normalize_scope_path(allowed)
    except ScopeContractError:
        return False

    if c == a:
        return True

    a_segments = a.split("/")
    if a_segments[-1] == "**":
        prefix = "/".join(a_segments[:-1])
        return not prefix or c == prefix or c.startswith(prefix + "/")

    c_segments = c.split("/")
    if c_segments[-1] == "**":
        prefix = "/".join(c_segments[:-1])
        return not prefix or a == prefix or a.startswith(prefix + "/")

    return c.startswith(a + "/") or a.startswith(c + "/")


def path_within(candidate: str, allowed: str) -> bool:
    """Directional containment: is *candidate* the same as, or nested
    inside, *allowed*?

    Unlike :func:`paths_overlap` (which is deliberately *bidirectional* --
    it also returns ``True`` when *allowed* is nested inside *candidate*,
    needed by the blocked-path collision / collapsed-directory checks),
    this is one-directional: it answers strictly "does *candidate* fall
    under *allowed*?" and nothing else.

    That direction matters for verifying a concrete changed file against an
    allow-list contract (see ``agent_baton.core.engine.manager_scope_signal.
    derive_scope_expansion_from_diff``): a real diff entry is only in-scope
    when it is the allowed path itself or lives underneath it. Using the
    bidirectional :func:`paths_overlap` there is a fail-*open* bug:

    * a changed file whose own trailing segment is ``**`` (a file literally
      named ``**`` -- valid on POSIX) would be glob-interpreted and match
      any allowed path whose prefix it covers, and
    * git's whole-new-untracked-directory collapse (``newdir/``) would be
      treated as "inside" a more-specific allowed *file*
      (``newdir/allowed.py``) because *allowed* is nested under the coarse
      *candidate* -- silently admitting an out-of-scope sibling created in
      the same new directory.

    Here *candidate*'s own segments are always literal (a segment named
    ``**`` is just a directory called ``**``); only *allowed* may carry a
    trailing ``**`` glob. A malformed path never contains anything
    (returns ``False`` rather than raising).
    """
    try:
        c = normalize_scope_path(candidate)
        a = normalize_scope_path(allowed)
    except ScopeContractError:
        return False

    if c == a:
        return True

    a_segments = a.split("/")
    if a_segments[-1] == "**":
        prefix = "/".join(a_segments[:-1])
        return not prefix or c == prefix or c.startswith(prefix + "/")

    return c.startswith(a + "/")


# ---------------------------------------------------------------------------
# Generated-file policy
# ---------------------------------------------------------------------------

# Directory-name markers recognized as build/tooling output. A path under
# one of these is produced BY tooling, not edited BY an agent -- excluded
# from *inferred* evidence tiers in derive_allowed_paths(), but never
# stripped from an operator's *explicit* allowed_paths (an explicit choice
# is always honored; see derive_allowed_paths()'s "explicit" tier).
GENERATED_PATH_MARKERS: frozenset[str] = frozenset(
    {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
        "node_modules", "dist", "build", ".venv", "venv", ".next", "target",
        "coverage", "htmlcov", ".egg-info", "vendor", ".git",
    }
)


def is_generated_path(path: str) -> bool:
    """True when any segment of *path* names a recognized generated/
    build-output directory (see :data:`GENERATED_PATH_MARKERS`).

    A malformed path is conservatively treated as *not* generated (``False``)
    -- callers that need strict validation should normalize first.
    """
    try:
        normalized = normalize_scope_path(path)
    except ScopeContractError:
        return False
    return any(
        seg in GENERATED_PATH_MARKERS or seg.endswith(".egg-info")
        for seg in normalized.split("/")
    )


# ---------------------------------------------------------------------------
# Deterministic write-scope derivation
# ---------------------------------------------------------------------------

# Conventional path segments a role's agent typically owns -- used ONLY to
# rank/select among candidates that repository-topology evidence already
# confirmed exist on disk (see derive_allowed_paths() tier 4). Never used
# to invent a path that isn't independently confirmed real: that would
# violate the "never invented" discipline the rest of the manager-mode
# layer already follows (see agent_baton.core.manager.charter).
ROLE_PATH_HINTS: dict[str, tuple[str, ...]] = {
    "test-engineer": ("tests", "test", "spec", "specs"),
    "backend-engineer": ("app", "backend", "server", "api", "src"),
    "frontend-engineer": ("frontend", "web", "ui", "client", "src"),
    "database-engineer": ("migrations", "db", "database"),
    "devops-engineer": (".github", "infra", "deploy", "ops"),
    "technical-writer": ("docs",),
    "data-engineer": ("data", "pipelines", "etl"),
    "data-scientist": ("notebooks", "analysis", "data"),
    "ai-systems-architect": ("app", "src"),
}

# A path-shaped token needs a recognizable extension or an explicit path
# separator to count as decomposition evidence extracted from prose (a
# deliverable like "reporting endpoint" is not a path; "app/reporting.py"
# or "reporting.py" is).
_PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+")

# Sentinel context file injected by EnrichmentStage._inject_context_files
# for every step lacking explicit context files -- a "read this" hint, not
# a repo area, so it must never masquerade as write-scope evidence (same
# rationale as agent_baton.core.manager.charter._likely_repo_areas's I1
# fix for the identical sentinel).
_CONTEXT_FILE_SENTINEL = "CLAUDE.md"


def path_candidates_from_text(text: str) -> list[str]:
    """Extract path-shaped tokens from free text (deliverable strings,
    task descriptions), order-preserving, deduped. Generated-path
    candidates are dropped (see :data:`GENERATED_PATH_MARKERS`) -- text-
    derived evidence is inferred, not explicit, so the generated-file
    policy applies to it.
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for match in _PATH_TOKEN_RE.finditer(text or ""):
        token = match.group(0).strip().strip(".,;:()[]{}\"'")
        if not token or token in seen:
            continue
        try:
            normalized = normalize_scope_path(token)
        except ScopeContractError:
            continue
        if is_generated_path(normalized):
            continue
        seen.add(token)
        candidates.append(normalized)
    return candidates


def derive_allowed_paths(
    *,
    explicit_paths: "list[str] | None" = None,
    deliverables: "list[str] | None" = None,
    context_files: "list[str] | None" = None,
    likely_repo_areas: "list[str] | None" = None,
    agent_base: str = "",
    existing_dirs: "frozenset[str] | None" = None,
) -> tuple[list[str], str]:
    """Deterministic write-scope derivation. Returns ``(paths, source)``.

    Preference order -- the first tier that yields at least one normalized
    path wins; later tiers are never consulted once an earlier one
    succeeds (this mirrors ``agent_baton.core.manager.charter.
    _likely_repo_areas``'s fall-through discipline: never invent, never
    blend tiers, always record which tier actually produced the answer so
    callers can surface it in diagnostics):

    1. ``"explicit"`` -- *explicit_paths* (already-assigned decomposition
       evidence -- e.g. a director-supplied structured spec, or a value a
       prior stage already set on the step). Always trusted verbatim,
       including generated paths (an explicit choice is never overridden).
    2. ``"deliverables"`` -- path-shaped tokens extracted from
       *deliverables* strings (decomposition evidence encoded in prose --
       e.g. a deliverable of ``"app/reporting/service.py"``).
    3. ``"context_files"`` -- *context_files* minus the universal
       ``CLAUDE.md`` sentinel (a read-this hint, not a target).
    4. ``"repo_topology"`` -- *likely_repo_areas* (repository topology
       already confirmed to exist by the caller, e.g.
       ``ProjectCharter.likely_repo_areas``).
    5. ``"agent_role"`` -- when *existing_dirs* is supplied (real
       directory names known to exist under the project root), the subset
       of :data:`ROLE_PATH_HINTS` for *agent_base* that is confirmed
       real. Without *existing_dirs* this tier never fires -- it would
       otherwise invent unconfirmed paths purely from role convention.

    Every candidate is normalized; malformed entries are dropped rather
    than aborting the whole tier. Returns ``([], "none")`` when no tier
    yields anything.
    """
    explicit = normalize_path_list(explicit_paths)
    if explicit:
        return explicit, "explicit"

    from_deliverables: list[str] = []
    seen: set[str] = set()
    for deliverable in deliverables or []:
        for candidate in path_candidates_from_text(deliverable):
            if candidate not in seen:
                seen.add(candidate)
                from_deliverables.append(candidate)
    if from_deliverables:
        return from_deliverables, "deliverables"

    from_context = normalize_path_list(
        [f for f in (context_files or []) if f != _CONTEXT_FILE_SENTINEL]
    )
    from_context = [p for p in from_context if not is_generated_path(p)]
    if from_context:
        return from_context, "context_files"

    from_topology = normalize_path_list(likely_repo_areas)
    if from_topology:
        return from_topology, "repo_topology"

    if existing_dirs:
        base = (agent_base or "").split("--")[0]
        hints = ROLE_PATH_HINTS.get(base, ())
        from_role = [h for h in hints if h in existing_dirs]
        if from_role:
            return normalize_path_list(from_role), "agent_role"

    return [], "none"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeDiagnostic:
    """One scope-contract finding for a single step."""

    step_id: str
    code: str  # "write_scope_missing" | "write_scope_contradictory"
    severity: str  # "warning" | "critical"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


def diagnose_step_scope(
    step_id: str,
    step_type: str,
    allowed_paths: "list[str] | None",
    blocked_paths: "list[str] | None" = None,
) -> "ScopeDiagnostic | None":
    """Classify *step_id*'s resolved scope. Returns ``None`` when clean.

    Two findings, in priority order:

    * ``"write_scope_contradictory"`` (critical) -- an allowed path
      collides with a blocked path (see :func:`paths_overlap`). This is a
      genuine contract error regardless of step type: a step is never
      simultaneously permitted and forbidden to touch the same area.
    * ``"write_scope_missing"`` (warning) -- *step_type* is write-capable
      (see :data:`WRITE_CAPABLE_STEP_TYPES`) and *allowed_paths* is empty
      after normalization. Read-only step types (see
      :data:`READ_ONLY_STEP_TYPES`) never trigger this -- an empty
      ``allowed_paths`` on a review/consulting step is the *valid*,
      intentional representation of "this step does not write", not an
      omission.

    Malformed entries in either list are ignored for the contradiction
    check (a malformed path can't overlap anything) but still count
    towards "empty" if they were the only entries -- a step whose sole
    ``allowed_paths`` entry fails to normalize has, in effect, no usable
    write scope.
    """
    normalized_allowed = normalize_path_list(allowed_paths)
    normalized_blocked = normalize_path_list(blocked_paths)

    if normalized_allowed and normalized_blocked:
        colliding = [
            path
            for path in normalized_allowed
            if any(paths_overlap(path, blocked) for blocked in normalized_blocked)
        ]
        if colliding:
            return ScopeDiagnostic(
                step_id=step_id,
                code="write_scope_contradictory",
                severity="critical",
                message=(
                    f"step {step_id!r} allowed_paths {colliding} overlap "
                    f"blocked_paths {normalized_blocked}"
                ),
            )

    if is_write_capable(step_type) and not normalized_allowed:
        return ScopeDiagnostic(
            step_id=step_id,
            code="write_scope_missing",
            severity="warning",
            message=(
                f"step {step_id!r} (step_type={step_type!r}) is write-capable "
                "but has no derivable allowed_paths -- write scope is "
                "ambiguous. Remediation: supply an explicit allowed_paths, "
                "a path-shaped deliverable, a context file, or a confirmed "
                "repo area so scope can be derived deterministically."
            ),
        )

    return None
