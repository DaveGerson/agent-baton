"""Wave 6.1 Part B — Persistent Agent Souls: SoulRegistry (bd-d975).
v34 addendum — Soul Revocation + Rotation (end-user readiness concern #6).

Manages cryptographic identities (souls) for agent dispatch.  Souls are
cross-project entities stored exclusively in ``~/.baton/central.db``
(never in per-project baton.db — per feedback_schema_project_id.md).

Each soul is an ed25519 keypair:
- Public key: stored in central.db ``agent_souls`` table (federated).
- Private key: stored at ``~/.config/baton/souls/<soul_id>.ed25519``
  with mode 0600 (machine-bound).

The soul_id format is ``<role>_<domain>_<3-char-pubkey-fingerprint>``,
e.g. ``code_reviewer_auth_f7x``.  Suffix collisions get a 4th char.

Revocation
----------
Revoked souls are recorded in the ``soul_revocations`` table (schema v34).
A revocation entry is permanent — double-revoke raises ``ValueError``.
The old notes-based heuristic (``revoked:`` prefix) is preserved for
backward-compatibility when reading rows that pre-date v34, but all new
revocations write to the dedicated table.

Rotation
--------
``revoke(soul_id, ..., successor_soul_id=<new_id>)`` records a revocation
with a pointer to the pre-existing successor.  The higher-level
``rotate()`` convenience method mints the successor keypair and performs
both the mint and the revocation atomically in a single transaction.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import socket
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"
_SOULS_DIR_DEFAULT = Path.home() / ".config" / "baton" / "souls"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _role_slug(role: str) -> str:
    """Normalise a role string to a filesystem/SQL-safe slug.

    ``"code-reviewer"`` → ``"code_reviewer"``.
    """
    return role.replace("-", "_").replace(" ", "_").lower()


def _domain_slug(domain: str) -> str:
    """Normalise a domain token to a slug."""
    return domain.replace("-", "_").replace(" ", "_").lower()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentSoul:
    """Immutable record of a persistent agent identity.

    Attributes:
        soul_id: Canonical soul identifier, e.g. ``"code_reviewer_auth_f7x"``.
        role: Agent role, e.g. ``"code-reviewer"``.
        pubkey: 32-byte raw ed25519 public key.
        privkey_path: Path to the local private key file, or ``None`` if this
            machine does not hold the private key.
        created_at: ISO 8601 creation timestamp.
        retired_at: ISO 8601 retirement timestamp; empty string when active.
        parent_soul_id: ID of the predecessor soul (on retire/successor), or
            empty string.
        origin_project: Project path where this soul was first minted, or
            empty string.
        notes: Free-form operator notes.
    """

    soul_id: str
    role: str
    pubkey: bytes
    privkey_path: Path | None
    created_at: str
    retired_at: str = ""
    parent_soul_id: str = ""
    origin_project: str = ""
    notes: str = ""

    # Internal revocation flag — derived from the presence of a
    # ``revoked:`` prefix in ``notes`` (legacy) OR from soul_revocations
    # table (v34+).  The registry sets this via the _revoked kwarg when
    # constructing souls from a JOIN query.
    _revoked: bool = False

    @property
    def is_revoked(self) -> bool:
        """True when this soul has been revoked (compromised key).

        Checks both the dedicated revocation table (v34) and the legacy
        notes-prefix for backward compatibility.
        """
        return self._revoked or self.notes.startswith("revoked:")

    @property
    def is_active(self) -> bool:
        """True when this soul is neither retired nor revoked."""
        return not self.retired_at and not self.is_revoked

    def synthetic_email(self) -> str:
        """Synthetic git author email for commit attribution."""
        return f"{self.soul_id}@baton.local"

    def sign(self, data: bytes) -> str:
        """Sign *data* with this soul's ed25519 private key.

        Returns a base64-encoded signature string (``"ed25519:<b64>"``).
        Raises ``RuntimeError`` when the private key is unavailable on this
        machine.
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        if self.privkey_path is None or not self.privkey_path.exists():
            raise RuntimeError(
                f"Private key for soul {self.soul_id} not available on this machine "
                f"(expected at {self.privkey_path})"
            )
        raw = self.privkey_path.read_bytes()
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
        sig_bytes = private_key.sign(data)
        return "ed25519:" + base64.b64encode(sig_bytes).decode()

    def verify(self, data: bytes, signature: str) -> bool:
        """Verify *signature* over *data* using this soul's public key.

        Returns ``False`` (rather than raising) on any verification failure,
        so callers can degrade gracefully.
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        try:
            if not signature.startswith("ed25519:"):
                return False
            sig_bytes = base64.b64decode(signature[len("ed25519:"):])
            public_key = Ed25519PublicKey.from_public_bytes(self.pubkey)
            public_key.verify(sig_bytes, data)
            return True
        except InvalidSignature:
            return False
        except Exception:
            return False


@dataclass(frozen=True)
class Revocation:
    """Immutable record of a soul revocation event.

    Attributes:
        soul_id: The revoked soul.
        revoked_at: ISO 8601 UTC timestamp.
        revoked_by: Operator identifier (user, hostname, or tool name).
        reason: Human-readable revocation reason.
        successor_soul_id: ID of the replacement soul, or ``None``.
    """

    soul_id: str
    revoked_at: str
    revoked_by: str
    reason: str
    successor_soul_id: str | None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SoulRegistry:
    """Cross-project agent soul registry backed by central.db.

    Souls live in ``central.db`` (cross-project federation).  Per-project
    ``baton.db`` never stores soul metadata (per feedback_schema_project_id.md).

    Args:
        central_db_path: Path to ``~/.baton/central.db``.  Defaults to the
            standard location when ``None``.
        souls_dir: Directory for private key files.  Defaults to
            ``~/.config/baton/souls/``.
    """

    def __init__(
        self,
        central_db_path: Path | None = None,
        souls_dir: Path | None = None,
    ) -> None:
        self._db_path = (central_db_path or _CENTRAL_DB_DEFAULT).resolve()
        self._souls_dir = (souls_dir or _SOULS_DIR_DEFAULT).resolve()
        self._souls_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        """Open (or reuse) a WAL-mode connection to central.db."""
        conn = sqlite3.connect(str(self._db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_tables(self) -> None:
        """Create agent_souls / soul_expertise / soul_revocations tables if absent."""
        ddl = """
        CREATE TABLE IF NOT EXISTS agent_souls (
            soul_id          TEXT PRIMARY KEY,
            role             TEXT NOT NULL,
            pubkey           BLOB NOT NULL,
            privkey_path     TEXT,
            created_at       TEXT NOT NULL,
            retired_at       TEXT NOT NULL DEFAULT '',
            parent_soul_id   TEXT NOT NULL DEFAULT '',
            origin_project   TEXT NOT NULL DEFAULT '',
            notes            TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_agent_souls_role ON agent_souls(role);
        CREATE TABLE IF NOT EXISTS soul_expertise (
            soul_id          TEXT NOT NULL,
            scope            TEXT NOT NULL,
            ref              TEXT NOT NULL,
            weight           REAL NOT NULL,
            last_touched_at  TEXT NOT NULL,
            PRIMARY KEY (soul_id, scope, ref)
        );
        CREATE INDEX IF NOT EXISTS idx_soul_expertise_soul ON soul_expertise(soul_id);
        CREATE TABLE IF NOT EXISTS soul_revocations (
            soul_id            TEXT PRIMARY KEY,
            revoked_at         TEXT NOT NULL,
            revoked_by         TEXT NOT NULL,
            reason             TEXT NOT NULL,
            successor_soul_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_soul_revocations_revoked_at
            ON soul_revocations(revoked_at);
        """
        try:
            conn = self._conn()
            conn.executescript(ddl)
            conn.commit()
            conn.close()
        except Exception as exc:
            _log.warning("SoulRegistry._ensure_tables failed: %s", exc)

    @staticmethod
    def _generate_keypair() -> tuple[bytes, bytes]:
        """Generate an ed25519 keypair.

        Returns:
            ``(privkey_raw_bytes, pubkey_raw_bytes)`` — 32 bytes each.
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PrivateFormat,
            PublicFormat,
            NoEncryption,
        )

        private_key = Ed25519PrivateKey.generate()
        privkey_bytes = private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        pubkey_bytes = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        return privkey_bytes, pubkey_bytes

    @staticmethod
    def _pubkey_fingerprint(pubkey: bytes, length: int = 3) -> str:
        """Return *length* hex chars of the SHA-256 fingerprint of *pubkey*."""
        digest = hashlib.sha256(pubkey).hexdigest()
        return digest[:length]

    def _make_soul_id(self, role: str, domain: str, pubkey: bytes) -> str:
        """Derive a deterministic soul_id from role + domain + pubkey.

        Format: ``<role_slug>_<domain_slug>_<fingerprint>``.  Checks
        central.db for collisions and extends the fingerprint by one char
        until unique.
        """
        r = _role_slug(role)
        d = _domain_slug(domain)
        for length in range(3, 8):
            fp = self._pubkey_fingerprint(pubkey, length)
            candidate = f"{r}_{d}_{fp}"
            if self.get(candidate) is None:
                return candidate
        # Absolute fallback — 8-char fingerprint
        fp = self._pubkey_fingerprint(pubkey, 8)
        return f"{r}_{d}_{fp}"

    def _privkey_path(self, soul_id: str) -> Path:
        return self._souls_dir / f"{soul_id}.ed25519"

    def _write_privkey(self, soul_id: str, privkey_bytes: bytes) -> Path:
        """Write private key to disk with mode 0600."""
        path = self._privkey_path(soul_id)
        path.write_bytes(privkey_bytes)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return path

    @staticmethod
    def _row_to_soul(row: sqlite3.Row, *, revoked: bool = False) -> "AgentSoul":
        privkey_path_str = row["privkey_path"]
        return AgentSoul(
            soul_id=row["soul_id"],
            role=row["role"],
            pubkey=bytes(row["pubkey"]),
            privkey_path=Path(privkey_path_str) if privkey_path_str else None,
            created_at=row["created_at"] or "",
            retired_at=row["retired_at"] or "",
            parent_soul_id=row["parent_soul_id"] or "",
            origin_project=row["origin_project"] or "",
            notes=row["notes"] or "",
            _revoked=revoked,
        )

    def _soul_is_revoked_in_db(self, conn: sqlite3.Connection, soul_id: str) -> bool:
        """Return True if *soul_id* has a row in soul_revocations."""
        try:
            row = conn.execute(
                "SELECT 1 FROM soul_revocations WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            return row is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API — souls
    # ------------------------------------------------------------------

    def mint(self, role: str, domain: str, project: str = "") -> AgentSoul:
        """Mint a new soul for the given *role* and *domain*.

        Generates an ed25519 keypair, writes the private key to
        ``~/.config/baton/souls/<soul_id>.ed25519`` (mode 0600), and
        registers the soul in central.db.

        Args:
            role: Agent role, e.g. ``"code-reviewer"``.
            domain: Domain token, e.g. ``"auth"``.  Derived from the
                highest-expertise file path token at dispatch time.
            project: Project path where the soul is being minted.

        Returns:
            The newly minted :class:`AgentSoul`.
        """
        privkey_bytes, pubkey_bytes = self._generate_keypair()
        soul_id = self._make_soul_id(role, domain, pubkey_bytes)
        privkey_path = self._write_privkey(soul_id, privkey_bytes)
        now = _utcnow()
        soul = AgentSoul(
            soul_id=soul_id,
            role=role,
            pubkey=pubkey_bytes,
            privkey_path=privkey_path,
            created_at=now,
            origin_project=project,
        )
        try:
            conn = self._conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_souls
                    (soul_id, role, pubkey, privkey_path, created_at,
                     retired_at, parent_soul_id, origin_project, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    soul.soul_id,
                    soul.role,
                    soul.pubkey,
                    str(soul.privkey_path),
                    soul.created_at,
                    soul.retired_at,
                    soul.parent_soul_id,
                    soul.origin_project,
                    soul.notes,
                ),
            )
            conn.commit()
            conn.close()
            _log.info(
                "soul.minted soul_id=%s role=%s domain=%s project=%s",
                soul_id, role, domain, project,
            )
        except Exception as exc:
            _log.warning("SoulRegistry.mint: DB write failed for %s: %s", soul_id, exc)
        return soul

    def get(self, soul_id: str) -> AgentSoul | None:
        """Fetch a soul by ID.  Returns ``None`` if not found."""
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT * FROM agent_souls WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if row is None:
                conn.close()
                return None
            revoked = self._soul_is_revoked_in_db(conn, soul_id)
            conn.close()
            soul = self._row_to_soul(row, revoked=revoked)
            # Reattach privkey_path from local disk if it exists.
            local_path = self._privkey_path(soul_id)
            if local_path.exists() and soul.privkey_path is None:
                soul = AgentSoul(
                    soul_id=soul.soul_id,
                    role=soul.role,
                    pubkey=soul.pubkey,
                    privkey_path=local_path,
                    created_at=soul.created_at,
                    retired_at=soul.retired_at,
                    parent_soul_id=soul.parent_soul_id,
                    origin_project=soul.origin_project,
                    notes=soul.notes,
                    _revoked=revoked,
                )
            return soul
        except Exception as exc:
            _log.warning("SoulRegistry.get failed for %s: %s", soul_id, exc)
            return None

    def list_for_role(self, role: str) -> list[AgentSoul]:
        """Return all active (non-retired, non-revoked) souls for *role*."""
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM agent_souls WHERE role = ? AND retired_at = '' ORDER BY created_at",
                (role,),
            ).fetchall()
            # Build a set of revoked soul_ids for this batch.
            revoked_ids: set[str] = set()
            try:
                rev_rows = conn.execute(
                    "SELECT soul_id FROM soul_revocations"
                ).fetchall()
                revoked_ids = {r["soul_id"] for r in rev_rows}
            except Exception:
                pass
            conn.close()
            souls = [
                self._row_to_soul(r, revoked=(r["soul_id"] in revoked_ids))
                for r in rows
            ]
            # Filter out revoked souls (both table-based and legacy notes-based).
            return [s for s in souls if not s.is_revoked]
        except Exception as exc:
            _log.warning("SoulRegistry.list_for_role failed for %s: %s", role, exc)
            return []

    def retire(self, soul_id: str, successor_id: str | None = None) -> None:
        """Retire *soul_id*, optionally recording a *successor_id*.

        Sets ``retired_at`` to now.  Existing expertise rows are left in
        place; :class:`SoulRouter` will copy them to the successor at
        0.5x weight on first use.

        Args:
            soul_id: Soul to retire.
            successor_id: Optional replacement soul ID.
        """
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT notes FROM agent_souls WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if row is None:
                _log.warning("SoulRegistry.retire: soul %s not found", soul_id)
                conn.close()
                return
            notes = row["notes"] or ""
            if successor_id:
                notes = f"successor:{successor_id}" + (f"|{notes}" if notes else "")
            conn.execute(
                "UPDATE agent_souls SET retired_at = ?, notes = ? WHERE soul_id = ?",
                (_utcnow(), notes, soul_id),
            )
            conn.commit()
            conn.close()
            _log.info("SoulRegistry.retire: retired soul %s (successor=%s)", soul_id, successor_id)
        except Exception as exc:
            _log.warning("SoulRegistry.retire failed for %s: %s", soul_id, exc)

    # ------------------------------------------------------------------
    # Public API — revocation
    # ------------------------------------------------------------------

    def revoke(
        self,
        soul_id: str,
        reason: str,
        revoked_by: str = "",
        successor_soul_id: str | None = None,
    ) -> None:
        """Revoke *soul_id* — marks the key as permanently compromised.

        Inserts a row into ``soul_revocations``.  Raises ``ValueError`` if
        the soul does not exist or has already been revoked (double-revoke
        is an error — it would silently corrupt audit timestamps).

        Does NOT delete the private key from disk; the operator must do that
        manually (and destroy backups).

        Args:
            soul_id: Soul to revoke.
            reason: Human-readable revocation reason (required, non-empty).
            revoked_by: Operator identifier — defaults to ``socket.gethostname()``.
            successor_soul_id: If the soul is being rotated, the pre-existing
                replacement soul_id.  Pass ``None`` for plain revocations.

        Raises:
            ValueError: Soul not found, or already revoked, or empty reason.
        """
        if not reason or not reason.strip():
            raise ValueError("revoke() requires a non-empty reason")

        effective_revoked_by = revoked_by.strip() if revoked_by.strip() else socket.gethostname()

        try:
            conn = self._conn()
            # Verify the soul exists.
            row = conn.execute(
                "SELECT soul_id FROM agent_souls WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if row is None:
                conn.close()
                raise ValueError(f"Soul not found: {soul_id}")

            # Reject double-revoke.
            existing = conn.execute(
                "SELECT revoked_at FROM soul_revocations WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if existing is not None:
                conn.close()
                raise ValueError(
                    f"Soul {soul_id} is already revoked (at {existing['revoked_at']}). "
                    "Double-revoke is not allowed."
                )

            now = _utcnow()
            conn.execute(
                """
                INSERT INTO soul_revocations
                    (soul_id, revoked_at, revoked_by, reason, successor_soul_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (soul_id, now, effective_revoked_by, reason.strip(), successor_soul_id),
            )
            conn.commit()
            conn.close()
            _log.warning(
                "soul.revoked soul_id=%s revoked_by=%s reason=%r successor=%s",
                soul_id, effective_revoked_by, reason, successor_soul_id or "none",
            )
        except ValueError:
            raise
        except Exception as exc:
            _log.warning("SoulRegistry.revoke failed for %s: %s", soul_id, exc)
            raise

    def is_revoked(self, soul_id: str) -> bool:
        """Return ``True`` if *soul_id* appears in ``soul_revocations``.

        Also checks the legacy notes-based flag for souls revoked before v34.
        Returns ``False`` for unknown soul IDs (graceful degradation).

        Args:
            soul_id: Soul to check.
        """
        try:
            conn = self._conn()
            # v34 table check.
            row = conn.execute(
                "SELECT 1 FROM soul_revocations WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if row is not None:
                conn.close()
                return True
            # Legacy notes-based check.
            soul_row = conn.execute(
                "SELECT notes FROM agent_souls WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            conn.close()
            if soul_row is None:
                return False
            notes = soul_row["notes"] or ""
            return notes.startswith("revoked:")
        except Exception as exc:
            _log.warning("SoulRegistry.is_revoked failed for %s: %s", soul_id, exc)
            return False

    def list_revocations(self) -> list[Revocation]:
        """Return all revocation records, most recent first.

        Returns:
            List of :class:`Revocation` dataclasses.
        """
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT soul_id, revoked_at, revoked_by, reason, successor_soul_id "
                "FROM soul_revocations ORDER BY revoked_at DESC"
            ).fetchall()
            conn.close()
            return [
                Revocation(
                    soul_id=r["soul_id"],
                    revoked_at=r["revoked_at"],
                    revoked_by=r["revoked_by"],
                    reason=r["reason"],
                    successor_soul_id=r["successor_soul_id"],
                )
                for r in rows
            ]
        except Exception as exc:
            _log.warning("SoulRegistry.list_revocations failed: %s", exc)
            return []

    def rotate(
        self,
        soul_id: str,
        reason: str,
        revoked_by: str = "",
    ) -> AgentSoul:
        """Rotate *soul_id* — revoke it and atomically mint a successor.

        Generates a fresh ed25519 keypair for the successor soul, then
        performs the mint INSERT and the revocation INSERT inside a single
        SQLite transaction so both succeed or both fail.

        The successor soul inherits the same ``role`` and ``domain`` (derived
        from the original ``soul_id``'s domain token) and records the old soul
        as its ``parent_soul_id``.

        Args:
            soul_id: The soul to rotate (must exist and not already be revoked).
            reason: Human-readable rotation reason.
            revoked_by: Operator identifier — defaults to ``socket.gethostname()``.

        Returns:
            The newly minted successor :class:`AgentSoul`.

        Raises:
            ValueError: Soul not found or already revoked.
        """
        if not reason or not reason.strip():
            raise ValueError("rotate() requires a non-empty reason")

        effective_revoked_by = revoked_by.strip() if revoked_by.strip() else socket.gethostname()

        conn = self._conn()
        try:
            # Fetch the original soul.
            row = conn.execute(
                "SELECT * FROM agent_souls WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if row is None:
                conn.close()
                raise ValueError(f"Soul not found: {soul_id}")

            existing_rev = conn.execute(
                "SELECT revoked_at FROM soul_revocations WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if existing_rev is not None:
                conn.close()
                raise ValueError(
                    f"Soul {soul_id} is already revoked (at {existing_rev['revoked_at']}). "
                    "Cannot rotate an already-revoked soul."
                )

            original_soul = self._row_to_soul(row)
            role = original_soul.role

            # Derive domain from the soul_id: last token after role_slug + "_"
            # soul_id format: <role_slug>_<domain_slug>_<fingerprint>
            role_prefix = _role_slug(role) + "_"
            remainder = soul_id[len(role_prefix):] if soul_id.startswith(role_prefix) else soul_id
            # domain is everything between role and the fingerprint suffix
            parts = remainder.split("_")
            domain = parts[0] if parts else "general"

            # Generate successor keypair.
            privkey_bytes, pubkey_bytes = self._generate_keypair()
            successor_id = self._make_soul_id_conn(conn, role, domain, pubkey_bytes)
            privkey_path = self._write_privkey(successor_id, privkey_bytes)
            now = _utcnow()

            # Atomic transaction: mint successor + insert revocation.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_souls
                    (soul_id, role, pubkey, privkey_path, created_at,
                     retired_at, parent_soul_id, origin_project, notes)
                VALUES (?, ?, ?, ?, ?, '', ?, ?, '')
                """,
                (
                    successor_id,
                    role,
                    pubkey_bytes,
                    str(privkey_path),
                    now,
                    soul_id,  # parent_soul_id
                    original_soul.origin_project,
                ),
            )
            conn.execute(
                """
                INSERT INTO soul_revocations
                    (soul_id, revoked_at, revoked_by, reason, successor_soul_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (soul_id, now, effective_revoked_by, reason.strip(), successor_id),
            )
            conn.execute("COMMIT")
            conn.close()

            _log.warning(
                "soul.rotated old_soul_id=%s successor_soul_id=%s reason=%r",
                soul_id, successor_id, reason,
            )

            return AgentSoul(
                soul_id=successor_id,
                role=role,
                pubkey=pubkey_bytes,
                privkey_path=privkey_path,
                created_at=now,
                parent_soul_id=soul_id,
                origin_project=original_soul.origin_project,
            )
        except (ValueError, sqlite3.Error) as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            conn.close()
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            conn.close()
            _log.warning("SoulRegistry.rotate failed for %s: %s", soul_id, exc)
            raise

    def _make_soul_id_conn(
        self,
        conn: sqlite3.Connection,
        role: str,
        domain: str,
        pubkey: bytes,
    ) -> str:
        """Like ``_make_soul_id`` but uses an already-open connection (for transactions)."""
        r = _role_slug(role)
        d = _domain_slug(domain)
        for length in range(3, 8):
            fp = self._pubkey_fingerprint(pubkey, length)
            candidate = f"{r}_{d}_{fp}"
            existing = conn.execute(
                "SELECT 1 FROM agent_souls WHERE soul_id = ?", (candidate,)
            ).fetchone()
            if existing is None:
                return candidate
        fp = self._pubkey_fingerprint(pubkey, 8)
        return f"{r}_{d}_{fp}"

    # ------------------------------------------------------------------
    # Public API — expertise
    # ------------------------------------------------------------------

    def upsert_expertise(
        self,
        soul_id: str,
        scope: str,
        ref: str,
        weight: float,
    ) -> None:
        """Insert or update a soul_expertise row.

        Args:
            soul_id: The soul whose expertise is being recorded.
            scope: Scope category, e.g. ``"file"`` or ``"module"``.
            ref: The specific file path or module name.
            weight: Combined expertise score in ``[0.0, 1.0]``.
        """
        try:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO soul_expertise (soul_id, scope, ref, weight, last_touched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(soul_id, scope, ref)
                DO UPDATE SET weight = excluded.weight,
                              last_touched_at = excluded.last_touched_at
                """,
                (soul_id, scope, ref, weight, _utcnow()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            _log.warning(
                "SoulRegistry.upsert_expertise failed for %s/%s/%s: %s",
                soul_id, scope, ref, exc,
            )

    def get_expertise(self, soul_id: str) -> list[dict]:
        """Return all expertise rows for *soul_id* as plain dicts."""
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT scope, ref, weight, last_touched_at FROM soul_expertise "
                "WHERE soul_id = ? ORDER BY weight DESC",
                (soul_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as exc:
            _log.warning("SoulRegistry.get_expertise failed for %s: %s", soul_id, exc)
            return []
