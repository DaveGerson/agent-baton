"""Wave 6.1 Part B — Persistent Agent Souls: SoulRegistry (bd-d975).

Manages cryptographic identities (souls) for agent dispatch.  Souls are
cross-project entities stored exclusively in ``~/.baton/central.db``
(never in per-project baton.db — per feedback_schema_project_id.md).

Each soul is an ed25519 keypair:
- Public key: stored in central.db ``agent_souls`` table (federated).
- Private key: stored at ``~/.config/baton/souls/<soul_id>.ed25519``
  with mode 0600 (machine-bound).

The soul_id format is ``<role>_<domain>_<3-char-pubkey-fingerprint>``,
e.g. ``code_reviewer_auth_f7x``.  Suffix collisions get a 4th char.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import stat
from dataclasses import dataclass, field
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

    # Internal revocation flag — NOT stored in the row, derived from
    # the presence of a ``revoked:`` prefix in ``notes``.
    @property
    def is_revoked(self) -> bool:
        """True when this soul has been revoked (compromised key)."""
        return self.notes.startswith("revoked:")

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
            # cryptography 3.x: Ed25519PublicKey.from_public_bytes(raw_bytes)
            public_key = Ed25519PublicKey.from_public_bytes(self.pubkey)
            public_key.verify(sig_bytes, data)
            return True
        except InvalidSignature:
            return False
        except Exception:
            return False


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
        """Create agent_souls / soul_expertise tables if absent."""
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
    def _row_to_soul(row: sqlite3.Row) -> AgentSoul:
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
        )

    # ------------------------------------------------------------------
    # Public API
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
            conn.close()
            if row is None:
                return None
            soul = self._row_to_soul(row)
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
            conn.close()
            souls = [self._row_to_soul(r) for r in rows]
            # Filter out revoked souls (revocation is stored in notes field).
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
            # Fetch current notes to append successor info.
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

    def revoke(self, soul_id: str) -> None:
        """Revoke *soul_id* — marks the key as compromised.

        Sets a ``revoked:<timestamp>`` prefix in ``notes``.  All beads
        signed by a revoked soul will produce signature-invalid warnings
        on read (degrade, don't fail).  Does NOT delete the private key
        (operator must do that manually).

        Args:
            soul_id: Soul to revoke.
        """
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT notes FROM agent_souls WHERE soul_id = ?", (soul_id,)
            ).fetchone()
            if row is None:
                _log.warning("SoulRegistry.revoke: soul %s not found", soul_id)
                conn.close()
                return
            existing_notes = row["notes"] or ""
            revocation_prefix = f"revoked:{_utcnow()}"
            new_notes = revocation_prefix + (f"|{existing_notes}" if existing_notes else "")
            conn.execute(
                "UPDATE agent_souls SET notes = ? WHERE soul_id = ?",
                (new_notes, soul_id),
            )
            conn.commit()
            conn.close()
            _log.info("SoulRegistry.revoke: revoked soul %s", soul_id)
        except Exception as exc:
            _log.warning("SoulRegistry.revoke failed for %s: %s", soul_id, exc)

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
