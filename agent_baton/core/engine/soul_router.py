"""Wave 6.1 Part B — Persistent Agent Souls: SoulRouter (bd-d975).
v33 addendum — Revocation check in signature-verification path.

Routes agent dispatches to the best-matching soul for a given
(role, affected_files) pair based on two signals:

1. **Authorship** — ``git log --author='<soul_id>@baton.local'`` over the
   trailing 90 days.  Lines touched / total, decayed with a 30-day half-life.
2. **Bead authorship** — count of beads where ``signed_by == soul_id`` and
   ``affected_files`` overlaps the current set, weighted by ``quality_score``.

Combined score = ``0.6 * authorship + 0.4 * bead_authorship``.

Expertise weights are lazily recomputed when the ``soul_expertise`` row is
older than 24 h.  Background recomputation is Wave 6.2 territory; here we
recompute synchronously on the first read after the TTL expires.

Revocation guard (v33)
----------------------
``verify_signature(soul_id, data, signature)`` is the single entry point for
signature verification.  It calls ``registry.is_revoked()`` *before* the
cryptographic check.  If the soul is revoked the signature is rejected
unconditionally, a WARNING is emitted to the log, and a structured warning
bead line is printed so that upstream tooling (BeadStore, etc.) can surface
the alert.  The successor soul_id is included in the warning when present.
"""
from __future__ import annotations

import logging
import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.soul_registry import AgentSoul, SoulRegistry

_log = logging.getLogger(__name__)

_AUTHORSHIP_WEIGHT = 0.6
_BEAD_AUTHORSHIP_WEIGHT = 0.4
_HALF_LIFE_DAYS = 30.0
_WINDOW_DAYS = 90
_EXPERTISE_TTL_HOURS = 24.0
_DOMAIN_FROM_PATH_DEPTH = 2  # number of path components used to derive domain


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decay_factor(days_ago: float, half_life: float = _HALF_LIFE_DAYS) -> float:
    """Exponential decay: ``2^(-days_ago / half_life)``."""
    return math.pow(2.0, -days_ago / half_life)


def _hours_since(timestamp: str) -> float:
    """Return fractional hours elapsed since *timestamp* (ISO 8601 UTC)."""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
        return float("inf")


def _domain_from_files(files: list[Path]) -> str:
    """Derive a single domain token from the most common path component.

    Takes the first component after the project root for each file, then
    returns the mode (most frequent).  Falls back to ``"general"`` when
    the list is empty or all files are top-level.
    """
    tokens: list[str] = []
    for f in files:
        parts = f.parts
        # Skip leading separators / absolute root.
        useful = [p for p in parts if p not in ("", "/", ".")]
        if len(useful) >= _DOMAIN_FROM_PATH_DEPTH:
            tokens.append(useful[0])
        elif useful:
            tokens.append(useful[0])
    if not tokens:
        return "general"
    return max(set(tokens), key=tokens.count)


class SoulRouter:
    """Routes dispatches to the highest-expertise soul for a (role, files) pair.

    Args:
        registry: The :class:`~agent_baton.core.engine.soul_registry.SoulRegistry`
            to read/write souls and expertise.
        repo_root: Absolute path to the project repository root.  Used as
            the cwd for git commands.
    """

    THRESHOLD: float = 0.4

    def __init__(
        self,
        registry: "SoulRegistry",
        repo_root: Path,
    ) -> None:
        self._registry = registry
        self._repo_root = repo_root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend(
        self,
        role: str,
        affected_files: list[Path],
    ) -> list[tuple[str, float]]:
        """Return ranked ``[(soul_id, score), ...]`` for the given role+files.

        All active souls for *role* are scored; list is sorted descending by
        score.  Scores are not cached here — callers should use
        :meth:`current_soul` for single-pick dispatch.

        Args:
            role: Agent role string, e.g. ``"code-reviewer"``.
            affected_files: Files the step will touch (used for expertise scoring).

        Returns:
            List of ``(soul_id, score)`` tuples, highest score first.
        """
        souls = self._registry.list_for_role(role)
        results: list[tuple[str, float]] = []
        for soul in souls:
            score = self._get_or_recompute_score(soul, affected_files)
            results.append((soul.soul_id, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def current_soul(
        self,
        role: str,
        affected_files: list[Path],
    ) -> "AgentSoul | None":
        """Return the best soul for *role* + *files*, auto-minting when needed.

        Resolution:
        1. Score all active souls for *role*.
        2. Return the highest-scoring soul if its score >= ``THRESHOLD``.
        3. If no soul scores above ``THRESHOLD``, auto-mint a new one.

        Args:
            role: Agent role string.
            affected_files: Files the current step will touch.

        Returns:
            An :class:`AgentSoul`, or ``None`` on any failure.
        """
        try:
            ranked = self.recommend(role, affected_files)
            if ranked and ranked[0][1] >= self.THRESHOLD:
                best_soul_id = ranked[0][0]
                soul = self._registry.get(best_soul_id)
                if soul is not None:
                    _log.debug(
                        "soul.routed soul_id=%s role=%s score=%.3f",
                        soul.soul_id, role, ranked[0][1],
                    )
                    return soul
            # Auto-mint: no incumbent above threshold.
            domain = _domain_from_files(affected_files)
            _log.info(
                "soul.auto_mint role=%s domain=%s (no incumbent above threshold %.2f)",
                role, domain, self.THRESHOLD,
            )
            # Determine origin project from repo root.
            project = str(self._repo_root)
            soul = self._registry.mint(role=role, domain=domain, project=project)
            return soul
        except Exception as exc:
            _log.warning("SoulRouter.current_soul failed for role=%s: %s", role, exc)
            return None

    # ------------------------------------------------------------------
    # Signature verification with revocation guard (v33)
    # ------------------------------------------------------------------

    def verify_signature(
        self,
        soul_id: str,
        data: bytes,
        signature: str,
    ) -> bool:
        """Verify *signature* over *data* for *soul_id*, checking revocation first.

        This is the canonical entry point for all signature verification.
        Callers (BeadStore, executor, etc.) MUST use this method rather than
        calling ``soul.verify()`` directly so that revocation is enforced.

        Verification logic:
        1. Look up the soul in the registry.  If not found → ``False``.
        2. Call ``registry.is_revoked(soul_id)``.  If revoked:
           - Emit a WARNING log line with soul_id, revoked_at, reason.
           - If a successor soul exists, include it in the warning.
           - Print a ``BEAD_WARNING:`` line for upstream tooling.
           - Return ``False`` unconditionally (revoked keys are never trusted).
        3. Delegate to ``soul.verify(data, signature)`` for the cryptographic check.

        Args:
            soul_id: The soul that allegedly produced the signature.
            data: The raw bytes that were signed.
            signature: Base64-encoded signature string (``"ed25519:<b64>"``).

        Returns:
            ``True`` if the signature is valid AND the soul is not revoked.
            ``False`` for any failure mode (unknown soul, revoked, bad sig).
        """
        try:
            soul = self._registry.get(soul_id)
            if soul is None:
                _log.warning(
                    "soul.verify_unknown soul_id=%s — soul not found in registry",
                    soul_id,
                )
                return False

            if self._registry.is_revoked(soul_id):
                # Fetch revocation metadata for the warning message.
                revocations = self._registry.list_revocations()
                rev = next((r for r in revocations if r.soul_id == soul_id), None)
                if rev is not None:
                    successor_hint = (
                        f" successor={rev.successor_soul_id}"
                        if rev.successor_soul_id
                        else ""
                    )
                    _log.warning(
                        "soul.revoked_signature_rejected soul_id=%s revoked_at=%s "
                        "reason=%r%s — dispatch decision signed by a revoked soul",
                        soul_id, rev.revoked_at, rev.reason, successor_hint,
                    )
                    print(
                        f"BEAD_WARNING: dispatch decision signed by revoked soul "
                        f"{soul_id} (revoked_at={rev.revoked_at}, "
                        f"reason={rev.reason!r}"
                        + (f", successor={rev.successor_soul_id}" if rev.successor_soul_id else "")
                        + ")"
                    )
                else:
                    # Revoked via legacy notes prefix — no structured metadata.
                    _log.warning(
                        "soul.revoked_signature_rejected soul_id=%s "
                        "(legacy notes-based revocation) — signature rejected",
                        soul_id,
                    )
                    print(
                        f"BEAD_WARNING: dispatch decision signed by revoked soul "
                        f"{soul_id} (legacy revocation, no structured metadata)"
                    )
                return False

            return soul.verify(data, signature)
        except Exception as exc:
            _log.warning(
                "SoulRouter.verify_signature error for soul_id=%s: %s", soul_id, exc
            )
            return False

    # ------------------------------------------------------------------
    # Scoring internals
    # ------------------------------------------------------------------

    def _get_or_recompute_score(
        self,
        soul: "AgentSoul",
        files: list[Path],
    ) -> float:
        """Return a cached or freshly computed expertise score.

        Checks soul_expertise for each file in *files*.  If any row is
        older than ``_EXPERTISE_TTL_HOURS``, recomputes all rows for this
        soul synchronously.
        """
        try:
            expertise_rows = self._registry.get_expertise(soul.soul_id)
            file_refs = {str(f) for f in files}
            relevant = [r for r in expertise_rows if r["ref"] in file_refs]

            # Check staleness.
            stale = any(
                _hours_since(r["last_touched_at"]) > _EXPERTISE_TTL_HOURS
                for r in relevant
            )
            if stale or not relevant:
                self._recompute_expertise(soul, files)
                # Re-read after recompute.
                expertise_rows = self._registry.get_expertise(soul.soul_id)
                relevant = [r for r in expertise_rows if r["ref"] in file_refs]

            if not relevant:
                return 0.0

            # Average the weights across all matching file rows.
            return sum(r["weight"] for r in relevant) / len(relevant)
        except Exception as exc:
            _log.debug("SoulRouter._get_or_recompute_score error: %s", exc)
            return 0.0

    def _recompute_expertise(
        self,
        soul: "AgentSoul",
        files: list[Path],
    ) -> None:
        """Recompute and persist expertise rows for *soul* over *files*."""
        for f in files:
            auth_score = self._authorship_score(soul, f)
            bead_score = self._bead_authorship_score(soul, f)
            combined = _AUTHORSHIP_WEIGHT * auth_score + _BEAD_AUTHORSHIP_WEIGHT * bead_score
            self._registry.upsert_expertise(
                soul_id=soul.soul_id,
                scope="file",
                ref=str(f),
                weight=combined,
            )

    def _expertise_score(self, soul: "AgentSoul", files: list[Path]) -> float:
        """Public scoring method: 0.6 * authorship + 0.4 * bead_authorship.

        Half-life 30 days applied to the authorship signal.  This is the
        canonical scoring formula from the Part B spec.

        Args:
            soul: The soul to score.
            files: Affected files for the current dispatch.

        Returns:
            Score in ``[0.0, 1.0]``.
        """
        if not files:
            return 0.0
        scores = []
        for f in files:
            a = self._authorship_score(soul, f)
            b = self._bead_authorship_score(soul, f)
            scores.append(_AUTHORSHIP_WEIGHT * a + _BEAD_AUTHORSHIP_WEIGHT * b)
        return sum(scores) / len(scores)

    def _authorship_score(self, soul: "AgentSoul", path: Path) -> float:
        """Git-log authorship score for *soul* over *path*.

        Runs ``git log --author='<soul_id>@baton.local' --numstat -- <path>``
        over the trailing 90 days, sums lines-touched per commit with
        exponential decay (half-life 30 days), and normalises to [0, 1]
        by capping at the total lines in the file.

        Falls back to 0.0 on any git error (detached HEAD, missing repo, etc.)
        """
        try:
            email = soul.synthetic_email()
            since = (datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            result = subprocess.run(
                [
                    "git", "log",
                    f"--author={email}",
                    f"--since={since}",
                    "--numstat",
                    "--format=%H %ai",
                    "--",
                    str(path),
                ],
                cwd=str(self._repo_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return 0.0

            now = datetime.now(timezone.utc)
            total_decayed = 0.0
            current_date: datetime | None = None

            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Format lines from --numstat: "<added>\t<deleted>\t<file>"
                parts = line.split("\t")
                if len(parts) == 3:
                    try:
                        added = int(parts[0]) if parts[0] != "-" else 0
                        deleted = int(parts[1]) if parts[1] != "-" else 0
                        lines_touched = added + deleted
                    except ValueError:
                        continue
                    if current_date is not None:
                        days_ago = (now - current_date).total_seconds() / 86400.0
                        decay = _decay_factor(days_ago)
                        total_decayed += lines_touched * decay
                else:
                    # Commit hash + date line: "<hash> <date> <tz>"
                    try:
                        date_str = " ".join(line.split()[1:3])
                        current_date = datetime.fromisoformat(date_str)
                        if current_date.tzinfo is None:
                            current_date = current_date.replace(tzinfo=timezone.utc)
                    except (ValueError, IndexError):
                        current_date = None

            if total_decayed <= 0:
                return 0.0
            # Normalise: cap at 1.0 (100 decayed-lines = max score).
            return min(1.0, total_decayed / 100.0)
        except Exception as exc:
            _log.debug("SoulRouter._authorship_score error for %s: %s", soul.soul_id, exc)
            return 0.0

    def _bead_authorship_score(self, soul: "AgentSoul", path: Path) -> float:
        """Bead-authorship score: count of beads signed by this soul touching *path*.

        Reads from the per-project baton.db (whichever is discoverable from
        repo_root) via a raw sqlite3 connection.  Returns 0.0 when unavailable.

        Score = sum(quality_score + 1.0) for matching beads, normalised
        by capping at 10.0 weighted beads.
        """
        try:
            db_candidates = list(self._repo_root.rglob(".claude/team-context/baton.db"))
            if not db_candidates:
                return 0.0
            db_path = db_candidates[0]

            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(str(db_path), timeout=5.0)
            conn.row_factory = _sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT quality_score, affected_files FROM beads
                    WHERE signed_by = ?
                      AND status != 'archived'
                    """,
                    (soul.soul_id,),
                ).fetchall()
            except Exception:
                conn.close()
                return 0.0
            conn.close()

            path_str = str(path)
            import json as _json
            total = 0.0
            for row in rows:
                try:
                    files = _json.loads(row["affected_files"] or "[]")
                except (ValueError, TypeError):
                    files = []
                if path_str in files or any(path_str in f for f in files):
                    qs = float(row["quality_score"] or 0.0)
                    total += max(0.0, qs + 1.0)  # shift so 0-quality = 1 weight

            return min(1.0, total / 10.0)
        except Exception as exc:
            _log.debug("SoulRouter._bead_authorship_score error: %s", exc)
            return 0.0
