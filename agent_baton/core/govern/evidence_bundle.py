"""Evidence Bundle -- verifiable per-task assurance artifact (007 Phase H).

``EvidenceBundleBuilder.build()`` writes a self-contained directory under
``output_dir/evidence/<task-id>/`` containing:

- ``manifest.json``           — SHA-256 inventory + optional soul signature
- ``aibom.json``              — AI Bill of Materials (JSON)
- ``aibom.md``                — AI Bill of Materials (Markdown)
- ``compliance-segment.jsonl`` — compliance-audit entries scoped to this task
- ``gates.json``              — full gate_results dump for this task
- ``verdicts.json``           — auditor/reviewer step verdicts
- ``approvals.json``          — approval_results + pending approval request
- ``packs.json``              — assurance packs + active-policy snapshot

``verify_bundle(path)`` is CI-runnable and network-free.  It accepts either
a directory path or a ``.tar.gz`` archive and returns ``(ok, errors,
exit_code)`` where exit_code is 0 (ok/warnings), 1 (failures), or 2
(unusable — manifest missing/unparseable).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent_baton as _pkg
from agent_baton.core.govern.aibom import AIBOMBuilder
from agent_baton.core.govern.compliance import extract_verdict_from_text, verify_chain

# Pattern that the executor writes as a truncation breadcrumb.
_TRUNCATED_RE = re.compile(r"^TRUNCATED:\s*(\S+)", re.MULTILINE)

BUNDLE_SCHEMA_VERSION = "agent-baton-evidence/1.0"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class EvidenceBundleBuilder:
    """Build a verifiable evidence bundle for a task.

    Parameters
    ----------
    db_path:
        Path to the project's ``baton.db``.
    compliance_log:
        Override path to ``compliance-audit.jsonl``.  When ``None`` we look
        at ``.claude/team-context/compliance-audit.jsonl`` next to ``db_path``.
    packs_dir:
        Override path to the assurance packs directory.  When ``None`` we
        look at ``.claude/packs/`` next to ``db_path``.
    central_db_path:
        Override path to ``central.db`` (used for soul signing).  When
        ``None`` the SoulRegistry default is used.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        compliance_log: Path | None = None,
        packs_dir: Path | None = None,
        central_db_path: Path | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._compliance_log = (
            Path(compliance_log) if compliance_log is not None else None
        )
        self._packs_dir = Path(packs_dir) if packs_dir is not None else None
        self._central_db_path = (
            Path(central_db_path) if central_db_path is not None else None
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(
        self,
        task_id: str,
        output_dir: Path,
        *,
        sign: bool = False,
        tar: bool = False,
    ) -> Path:
        """Build the evidence bundle for *task_id*.

        Parameters
        ----------
        task_id:
            The task to collect evidence for.
        output_dir:
            Root directory; bundle is written to
            ``<output_dir>/evidence/<task_id>/``.
        sign:
            When ``True`` and ``BATON_SOULS_ENABLED=1``, sign the manifest
            with a soul key (prefer role ``auditor``, mint
            ``evidence-signer`` if absent).
        tar:
            When ``True``, package the directory into a ``.tar.gz`` and
            remove the directory.

        Returns
        -------
        Path
            Path to the bundle directory (or ``.tar.gz`` when *tar* is True).

        Raises
        ------
        ValueError
            When the task does not exist in the database.
        """
        import sqlite3

        bundle_dir = Path(output_dir) / "evidence" / task_id
        bundle_dir.mkdir(parents=True, exist_ok=True)

        # ---- 1. AIBOM (JSON + Markdown) -----------------------------------
        aibom_builder = AIBOMBuilder(
            db_path=self._db_path,
            compliance_log=self._compliance_log,
        )
        # Raises ValueError when task not found — let it propagate.
        aibom = aibom_builder.build(task_id)
        (bundle_dir / "aibom.json").write_text(
            aibom.to_json(), encoding="utf-8"
        )
        (bundle_dir / "aibom.md").write_text(
            aibom.to_markdown(), encoding="utf-8"
        )

        # ---- 2. Compliance segment ----------------------------------------
        compliance_log = self._resolve_compliance_log()
        if compliance_log is not None and compliance_log.exists():
            segment = self._build_compliance_segment(task_id, compliance_log)
            (bundle_dir / "compliance-segment.jsonl").write_text(
                "\n".join(json.dumps(e, separators=(",", ":")) for e in segment) + "\n",
                encoding="utf-8",
            )

        # ---- 3. Gates -------------------------------------------------------
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            gates = self._collect_gates(conn, task_id)
            verdicts = self._collect_verdicts(conn, task_id)
            approvals = self._collect_approvals(conn, task_id)
        finally:
            conn.close()

        (bundle_dir / "gates.json").write_text(
            json.dumps(gates, indent=2), encoding="utf-8"
        )

        # ---- 4. Verdicts ----------------------------------------------------
        (bundle_dir / "verdicts.json").write_text(
            json.dumps(verdicts, indent=2), encoding="utf-8"
        )

        # ---- 5. Approvals ---------------------------------------------------
        (bundle_dir / "approvals.json").write_text(
            json.dumps(approvals, indent=2), encoding="utf-8"
        )

        # ---- 6. Packs (optional) --------------------------------------------
        packs_data = self._collect_packs()
        if packs_data:
            (bundle_dir / "packs.json").write_text(
                json.dumps(packs_data, indent=2), encoding="utf-8"
            )

        # ---- 7. Manifest (unsigned) ----------------------------------------
        manifest = self._build_manifest(task_id, bundle_dir)
        (bundle_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        # ---- 8. Sign if requested ------------------------------------------
        if sign:
            manifest = self._sign_manifest(manifest, bundle_dir)
            (bundle_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )

        # ---- 9. Tar if requested -------------------------------------------
        if tar:
            tar_path = bundle_dir.parent / f"{task_id}.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(bundle_dir, arcname=task_id)
            # Remove the directory
            import shutil
            shutil.rmtree(bundle_dir)
            return tar_path

        return bundle_dir

    # ------------------------------------------------------------------
    # Collectors
    # ------------------------------------------------------------------

    def _build_compliance_segment(
        self, task_id: str, log_path: Path
    ) -> list[dict[str, Any]]:
        """Return compliance entries scoped to *task_id*.

        The segment contains only the actual task-scoped entries from the
        chain (with their original ``prev_hash``/``entry_hash`` fields intact).
        When no entries match, a synthetic note entry is written (also
        chained so ``verify_chain`` can walk it).

        An ``_anchor_hash`` metadata key on the first synthetic note (or on
        the manifest side-channel stored in ``manifest.json``) records the
        chain head at bundle time so the segment is independently anchored.
        """
        from agent_baton.core.govern.compliance import _GENESIS_HASH, _entry_hash

        entries: list[dict[str, Any]] = []
        with log_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Match by top-level task_id OR payload.task_id.
                entry_task = entry.get("task_id") or entry.get("payload", {}).get(
                    "task_id", ""
                )
                if entry_task == task_id:
                    entries.append(entry)

        if entries:
            return entries

        # No entries for this task -- write a single synthetic chained note.
        anchor_hash = _chain_head_hash(log_path)
        note: dict[str, Any] = {
            "_baton_note": f"no entries for task {task_id}",
            "_anchor_hash": anchor_hash,
            "task_id": task_id,
            "prev_hash": _GENESIS_HASH,
        }
        note["entry_hash"] = _entry_hash(note)
        return [note]

    def _collect_gates(
        self, conn: Any, task_id: str
    ) -> list[dict[str, Any]]:
        """Full dump of gate_results for *task_id*."""
        rows = conn.execute(
            """
            SELECT id, task_id, phase_id, gate_type, passed, output,
                   checked_at, command, exit_code, decision_source, actor
            FROM gate_results
            WHERE task_id = ?
            ORDER BY id ASC
            """,
            (task_id,),
        ).fetchall()

        gates = []
        for r in rows:
            output = (r["output"] or "").lower()
            if "skip" in output and not r["passed"]:
                outcome = "SKIP"
            else:
                outcome = "PASS" if int(r["passed"]) else "FAIL"
            gates.append(
                {
                    "id": r["id"],
                    "task_id": r["task_id"],
                    "phase_id": r["phase_id"],
                    "gate_type": r["gate_type"] or "",
                    "passed": bool(r["passed"]),
                    "outcome": outcome,
                    "output": r["output"] or "",
                    "checked_at": r["checked_at"] or "",
                    "command": r["command"] or "",
                    "exit_code": r["exit_code"],
                    "decision_source": r["decision_source"] or "",
                    "actor": r["actor"] or "",
                }
            )
        return gates

    def _collect_verdicts(
        self, conn: Any, task_id: str
    ) -> list[dict[str, Any]]:
        """Collect auditor/reviewer step verdicts.

        Rows included: agent_name LIKE %auditor% OR %reviewer%
        OR step_type = 'reviewing'.
        """
        rows = conn.execute(
            """
            SELECT task_id, step_id, agent_name, step_type, outcome,
                   status, completed_at
            FROM step_results
            WHERE task_id = ?
              AND (
                agent_name LIKE '%auditor%'
                OR agent_name LIKE '%reviewer%'
                OR step_type = 'reviewing'
              )
            ORDER BY step_id ASC
            """,
            (task_id,),
        ).fetchall()

        verdicts = []
        for r in rows:
            outcome_text = r["outcome"] or ""
            # Check for truncation breadcrumb.
            truncated = bool(_TRUNCATED_RE.search(outcome_text))
            # If truncated, attempt to read spillover — but spillover is not in
            # the DB column; it's runtime-only.  We flag it and move on.
            verdict = extract_verdict_from_text(outcome_text)
            verdicts.append(
                {
                    "task_id": r["task_id"],
                    "step_id": r["step_id"],
                    "agent_name": r["agent_name"] or "",
                    "step_type": r["step_type"] or "",
                    "outcome": outcome_text,
                    "outcome_truncated": truncated,
                    "verdict": verdict.value if verdict is not None else None,
                    "status": r["status"] or "",
                    "completed_at": r["completed_at"] or "",
                }
            )
        return verdicts

    def _collect_approvals(
        self, conn: Any, task_id: str
    ) -> dict[str, Any]:
        """Collect approval_results + pending approval request."""
        rows = conn.execute(
            """
            SELECT id, task_id, phase_id, result, feedback,
                   decided_at, decision_source, actor, rationale
            FROM approval_results
            WHERE task_id = ?
            ORDER BY id ASC
            """,
            (task_id,),
        ).fetchall()

        approvals = [dict(r) for r in rows]

        # Check for pending approval request on the execution.
        exec_row = conn.execute(
            "SELECT pending_approval_request_json FROM executions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        pending = None
        if exec_row is not None:
            raw = exec_row["pending_approval_request_json"]
            if raw:
                try:
                    pending = json.loads(raw)
                    pending["_pending"] = True
                except (json.JSONDecodeError, TypeError):
                    pass

        return {
            "approvals": approvals,
            "pending_approval_request": pending,
        }

    def _collect_packs(self) -> dict[str, Any] | None:
        """Scan packs_dir for pack.json files + active-policy.json.

        Returns ``None`` when nothing is found so the caller can omit
        ``packs.json`` entirely.
        """
        packs_dir = self._resolve_packs_dir()
        items: list[dict[str, Any]] = []

        if packs_dir is not None and packs_dir.is_dir():
            for pack_json in sorted(packs_dir.glob("*/pack.json")):
                try:
                    data = json.loads(pack_json.read_text(encoding="utf-8"))
                    items.append(
                        {
                            "name": data.get("name", pack_json.parent.name),
                            "version": data.get("version", ""),
                            "source": "pack",
                        }
                    )
                except (OSError, json.JSONDecodeError):
                    # Include the pack with minimal info rather than silently
                    # dropping it.
                    items.append(
                        {
                            "name": pack_json.parent.name,
                            "version": "",
                            "source": "pack",
                        }
                    )

        # active-policy.json lives next to baton.db
        active_policy_path = self._db_path.parent / "active-policy.json"
        if not active_policy_path.exists():
            # Also look at .claude/packs/active-policy.json
            if packs_dir is not None:
                alt = packs_dir.parent / "active-policy.json"
                if alt.exists():
                    active_policy_path = alt

        if active_policy_path.exists():
            try:
                policy = json.loads(
                    active_policy_path.read_text(encoding="utf-8")
                )
                items.append(
                    {
                        "preset": policy.get("preset", ""),
                        "source": "active-policy",
                    }
                )
            except (OSError, json.JSONDecodeError):
                pass

        return {"packs": items} if items else None

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def _build_manifest(
        self, task_id: str, bundle_dir: Path
    ) -> dict[str, Any]:
        """Compute per-file SHA-256 and build the unsigned manifest."""
        files: dict[str, str] = {}
        for f in sorted(bundle_dir.iterdir()):
            if f.name == "manifest.json":
                continue
            files[f.name] = _sha256_file(f)

        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "task_id": task_id,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": f"agent-baton-{_pkg.__version__}",
            "files": files,
        }

    def _sign_manifest(
        self, manifest: dict[str, Any], bundle_dir: Path
    ) -> dict[str, Any]:
        """Sign *manifest* with a soul key; inject ``soul_signature``.

        Returns the manifest dict (possibly mutated if signing succeeds).
        When BATON_SOULS_ENABLED is not "1", emits a warning and returns
        the unsigned manifest unchanged.
        """
        if os.environ.get("BATON_SOULS_ENABLED", "0") != "1":
            print(
                "warning: --sign requested but BATON_SOULS_ENABLED is not 1; "
                "bundle will be unsigned.",
                file=sys.stderr,
            )
            return manifest

        try:
            from agent_baton.core.engine.soul_registry import SoulRegistry

            registry = SoulRegistry(central_db_path=self._central_db_path)

            # Prefer auditor soul; fall back to evidence-signer.
            soul = None
            for role in ("auditor", "evidence-signer"):
                conn = registry._conn()
                try:
                    row = conn.execute(
                        "SELECT * FROM agent_souls WHERE role = ? AND retired_at = '' LIMIT 1",
                        (role,),
                    ).fetchone()
                    if row is not None:
                        revoked = registry._soul_is_revoked_in_db(conn, row["soul_id"])
                        soul_candidate = registry._row_to_soul(row, revoked=revoked)
                        if soul_candidate.is_active:
                            soul = soul_candidate
                            break
                finally:
                    conn.close()

            if soul is None:
                # Mint an evidence-signer soul.
                soul = registry.mint("evidence-signer", "evidence")

            # Canonical bytes = JSON of manifest WITHOUT soul_signature key,
            # sorted keys.
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            # Sign the SHA-256 of the canonical bytes.
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            signature = soul.sign(digest.encode("utf-8"))

            manifest = {**manifest, "soul_signature": {
                "signer_soul_id": soul.soul_id,
                "signature": signature,
            }}
        except Exception as exc:
            print(
                f"warning: signing failed ({exc}); bundle will be unsigned.",
                file=sys.stderr,
            )
        return manifest

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _resolve_compliance_log(self) -> Path | None:
        if self._compliance_log is not None:
            return self._compliance_log
        default = self._db_path.parent / "compliance-audit.jsonl"
        return default if default.exists() else None

    def _resolve_packs_dir(self) -> Path | None:
        if self._packs_dir is not None:
            return self._packs_dir
        # Look for .claude/packs/ relative to baton.db's parent hierarchy.
        candidate = self._db_path.parent.parent / "packs"
        if candidate.is_dir():
            return candidate
        # Also try cwd-relative.
        cwd_candidate = Path.cwd() / ".claude" / "packs"
        if cwd_candidate.is_dir():
            return cwd_candidate
        return None


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def verify_bundle(path: Path) -> tuple[bool, list[str], int]:
    """Verify a previously built evidence bundle.

    Parameters
    ----------
    path:
        Path to a bundle directory or a ``.tar.gz`` archive.

    Returns
    -------
    tuple[bool, list[str], int]
        ``(ok, errors, exit_code)`` where:

        - ``ok`` is ``True`` when there are no failures (warnings OK).
        - ``errors`` is a list of human-readable problem strings.
        - ``exit_code`` is 0 (clean/warnings), 1 (failures), 2 (unusable).
    """
    tmp_dir: Path | None = None
    try:
        bundle_dir, tmp_dir = _resolve_bundle_dir(path)
    except Exception as exc:
        return False, [f"Cannot open bundle: {exc}"], 2

    errors: list[str] = []
    warnings: list[str] = []

    # ---- 1. Manifest present and parseable --------------------------------
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        _cleanup_tmp(tmp_dir)
        return False, ["manifest.json missing — bundle is unusable"], 2

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _cleanup_tmp(tmp_dir)
        return False, [f"manifest.json unparseable: {exc}"], 2

    # ---- 2. Per-file SHA-256 checks ----------------------------------------
    expected_files: dict[str, str] = manifest.get("files", {})
    for fname, expected_hash in expected_files.items():
        fpath = bundle_dir / fname
        if not fpath.exists():
            errors.append(f"{fname}: file listed in manifest is missing")
            continue
        actual = _sha256_file(fpath)
        if actual != expected_hash:
            errors.append(
                f"{fname}: SHA-256 mismatch "
                f"(expected {expected_hash[:12]}…, got {actual[:12]}…)"
            )

    # Check no extra files (other than manifest.json itself) are present
    # without being in the manifest — a soft warning.
    for fpath in bundle_dir.iterdir():
        if fpath.name == "manifest.json":
            continue
        if fpath.name not in expected_files:
            warnings.append(f"{fpath.name}: present in bundle but not in manifest")

    # ---- 3. Compliance segment chain verification -------------------------
    segment_path = bundle_dir / "compliance-segment.jsonl"
    if segment_path.exists():
        ok_seg, msg_seg = _verify_segment_chain(segment_path)
        if not ok_seg:
            errors.append(f"compliance-segment.jsonl chain broken: {msg_seg}")

        # Cross-check AIBOM chain_anchor vs segment tail.
        aibom_path = bundle_dir / "aibom.json"
        if aibom_path.exists():
            try:
                aibom_data = json.loads(aibom_path.read_text(encoding="utf-8"))
                aibom_anchor = aibom_data.get("chain_anchor", "")
                segment_tail = _chain_head_hash(segment_path)
                if aibom_anchor and segment_tail and aibom_anchor != segment_tail:
                    warnings.append(
                        "WARNING: AIBOM chain_anchor does not match compliance-segment "
                        "tail hash — the compliance log may have grown since bundle "
                        "creation (non-fatal)"
                    )
            except (OSError, json.JSONDecodeError):
                pass

    # ---- 4. Signature verification (when present) -------------------------
    sig_info = manifest.get("soul_signature")
    if sig_info is not None:
        sig_errors = _verify_signature(manifest, sig_info)
        errors.extend(sig_errors)

    # ---- 5. Determine result ----------------------------------------------
    _cleanup_tmp(tmp_dir)

    all_messages = errors + warnings
    if errors:
        return False, all_messages, 1
    return True, all_messages, 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_segment_chain(segment_path: Path) -> tuple[bool, str]:
    """Verify the internal consistency of a compliance segment file.

    Unlike ``verify_chain`` (which requires the chain to start from the
    genesis hash), this function only checks that each entry's
    ``prev_hash`` matches the previous entry's ``entry_hash`` —
    i.e. the segment is internally consistent, not necessarily starting
    from genesis.  Entries without hash fields (e.g. synthetic note
    entries) are verified as-is using :func:`_entry_hash`.

    Synthetic note entries with ``_baton_note`` are verified with their
    own ``prev_hash``/``entry_hash`` fields (written using the genesis
    hash as the start since they are standalone).

    Returns ``(True, message)`` when the segment is intact.
    """
    from agent_baton.core.govern.compliance import _entry_hash as _ce_hash

    if not segment_path.exists():
        return True, "Segment does not exist."

    entries: list[dict[str, Any]] = []
    with segment_path.open("r", encoding="utf-8") as fh:
        for i, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                return False, f"Line {i}: JSON parse error — {exc}"
            entries.append(obj)

    if not entries:
        return True, "Empty segment — nothing to verify."

    prev_hash: str | None = None
    for i, entry in enumerate(entries, 1):
        stored_prev = entry.get("prev_hash", "")
        stored_hash = entry.get("entry_hash", "")

        if not stored_prev and not stored_hash:
            return (
                False,
                f"Line {i}: missing prev_hash/entry_hash — "
                f"this row pre-dates the F0.3 hash chain.",
            )

        # On first entry, accept whatever prev_hash it has (may be mid-chain).
        if prev_hash is not None and stored_prev != prev_hash:
            return (
                False,
                f"Line {i}: prev_hash mismatch "
                f"(expected {prev_hash!r}, got {stored_prev!r})",
            )

        recomputed = _ce_hash(entry)
        if recomputed != stored_hash:
            return (
                False,
                f"Line {i}: entry_hash mismatch "
                f"(expected {recomputed!r}, got {stored_hash!r})",
            )

        prev_hash = stored_hash

    return True, f"Segment intact — {len(entries)} entries verified."


def _sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _chain_head_hash(log_path: Path) -> str:
    """Return the entry_hash of the last valid line in a JSONL chain log."""
    if not log_path.exists():
        return ""
    try:
        with log_path.open("rb") as fh:
            raw = fh.read()
    except OSError:
        return ""
    for line in reversed(raw.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        h = obj.get("entry_hash")
        if isinstance(h, str) and len(h) == 64:
            return h
    return ""


def _resolve_bundle_dir(path: Path) -> tuple[Path, Path | None]:
    """Return (bundle_dir, tmp_dir_or_None).

    When *path* is a ``.tar.gz``, extracts to a temporary directory.
    """
    path = Path(path)
    if path.is_dir():
        return path, None

    if str(path).endswith(".tar.gz") and path.is_file():
        tmp_dir = Path(tempfile.mkdtemp(prefix="baton_evidence_"))
        with tarfile.open(path, "r:gz") as tf:
            # Safety filter: skip absolute paths and traversal.
            members = [
                m for m in tf.getmembers()
                if not m.name.startswith("/") and ".." not in m.name
            ]
            tf.extractall(tmp_dir, members=members)
        # The tarball contains a single top-level directory (the task-id).
        contents = [p for p in tmp_dir.iterdir() if p.is_dir()]
        if len(contents) == 1:
            return contents[0], tmp_dir
        return tmp_dir, tmp_dir

    raise ValueError(f"Path is neither a directory nor a .tar.gz: {path}")


def _cleanup_tmp(tmp_dir: Path | None) -> None:
    if tmp_dir is not None:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass


def _verify_signature(
    manifest: dict[str, Any], sig_info: dict[str, Any]
) -> list[str]:
    """Verify the soul signature recorded in *sig_info*.

    Returns a list of error strings (empty = OK).
    """
    errors: list[str]
    soul_id = sig_info.get("signer_soul_id", "")
    signature = sig_info.get("signature", "")
    if not soul_id or not signature:
        return ["soul_signature present but signer_soul_id or signature missing"]

    try:
        from agent_baton.core.engine.soul_registry import SoulRegistry

        registry = SoulRegistry()
        soul = registry.get(soul_id)
        if soul is None:
            return [f"soul_signature: signer soul {soul_id!r} not found in registry"]
        if soul.is_revoked:
            return [f"soul_signature: signer soul {soul_id!r} has been revoked"]
        if not soul.is_active:
            return [f"soul_signature: signer soul {soul_id!r} is not active"]

        # Reconstruct the canonical bytes that were signed.
        manifest_without_sig = {
            k: v for k, v in manifest.items() if k != "soul_signature"
        }
        canonical = json.dumps(
            manifest_without_sig, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not soul.verify(digest.encode("utf-8"), signature):
            return [f"soul_signature: signature verification FAILED for soul {soul_id!r}"]
        return []
    except ImportError:
        return ["soul_signature: cryptography package not available for verification"]
    except Exception as exc:
        return [f"soul_signature: verification error — {exc}"]
