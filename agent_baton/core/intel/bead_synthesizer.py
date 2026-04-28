"""Deterministic bead-graph synthesis (Wave 2.1, resolves bd-2c9d / bd-879f).

Beads are flat records by default.  ``BeadSynthesizer`` walks every pair
of open beads and derives three kinds of structure:

* **file_overlap edges** — beads whose ``affected_files`` intersect, with
  weight = jaccard(files_a, files_b).
* **tag_overlap edges** — same idea on the ``tags`` list.  Lower-priority
  signal than file overlap.
* **clusters** — connected components over file_overlap edges with
  weight ≥ 0.3.  Each component with ≥2 beads becomes a row in
  ``bead_clusters`` with a label (top shared tag, falling back to the
  first bead's content snippet).
* **conflict edges** — pairs that share a primary tag AND have
  ``bead_type='warning'`` AND have title/content token overlap < 0.2.
  Flags two warnings about the same area saying different things.

All writes are idempotent: edges are keyed on
``(src_bead_id, dst_bead_id, edge_type)`` and use SQLite ``INSERT OR
REPLACE`` semantics.  Clusters are regenerated wholesale each call
(deleted then re-inserted) — the synthesizer owns ``bead_clusters``.

Design constraints (from spec):
  * No embeddings.
  * No LLM calls.
  * Pure deterministic graph synthesis.
  * Best-effort: never raises in production paths; logs and returns a
    zero-counts result on failure.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class SynthesisResult:
    """Counts of structures created or refreshed during a synthesize() pass."""

    edges_added: int = 0
    clusters_created: int = 0
    conflicts_flagged: int = 0
    pairs_examined: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "edges_added": self.edges_added,
            "clusters_created": self.clusters_created,
            "conflicts_flagged": self.conflicts_flagged,
            "pairs_examined": self.pairs_examined,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Edges of these weights are "strong enough" to define a cluster.
CLUSTER_MIN_WEIGHT = 0.3

# Conflict detection: two warnings with same primary tag whose
# content-token jaccard is below this threshold are considered to be
# disagreeing about the same area.
CONFLICT_TOKEN_OVERLAP_MAX = 0.2


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_json_list(raw: str | None) -> list[str]:
    """Best-effort decode a JSON-string list column to a Python list."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val if x]
        return []
    except Exception:
        return []


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Symmetric jaccard similarity over two iterables of hashables."""
    set_a = {x for x in a if x}
    set_b = {x for x in b if x}
    if not set_a or not set_b:
        return 0.0
    inter = set_a & set_b
    if not inter:
        return 0.0
    return len(inter) / len(set_a | set_b)


def _tokenize(text: str) -> set[str]:
    """Cheap whitespace + punctuation tokenizer used for conflict detection."""
    if not text:
        return set()
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    # Drop very short tokens — they're noise (e.g. "a", "of", "to").
    return {tok for tok in cleaned.split() if len(tok) > 2}


def _primary_tag(tags: list[str]) -> str:
    """First non-empty tag, treated as the primary subject of the bead."""
    for tag in tags:
        if tag:
            return tag
    return ""


# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

class BeadSynthesizer:
    """Run deterministic graph inference over open beads.

    Stateless apart from a logger.  The caller passes in a live SQLite
    connection (e.g. ``BeadStore._conn()`` or any connection opened with
    the project schema applied).
    """

    def synthesize(self, conn) -> SynthesisResult:
        """Walk every open bead pair and persist inferred edges + clusters.

        Args:
            conn: Open ``sqlite3.Connection`` to a database with schema
                v28+ applied (``bead_edges`` and ``bead_clusters`` must
                exist).

        Returns:
            :class:`SynthesisResult` with counts of work performed.
            Never raises — surfaces failures via ``errors`` field and
            warning logs.
        """
        result = SynthesisResult()

        if conn is None:
            result.errors.append("no connection")
            return result

        # Verify required tables exist (graceful degradation when called
        # against an older schema or a DB that hasn't been migrated yet).
        if not self._tables_exist(conn):
            _log.debug(
                "BeadSynthesizer: bead_edges/bead_clusters missing — "
                "skipping (older schema)."
            )
            return result

        try:
            beads = self._load_open_beads(conn)
        except Exception as exc:
            _log.warning("BeadSynthesizer: failed to load beads: %s", exc)
            result.errors.append(f"load_failed: {exc}")
            return result

        if len(beads) < 2:
            # Nothing to correlate — empty pairs.  Still safe to refresh
            # clusters (which will result in a no-op delete).
            self._refresh_clusters(conn, beads, edges=[], result=result)
            return result

        # Edge inference: file_overlap + tag_overlap + conflict.
        edges_for_clustering: list[tuple[str, str, float]] = []
        now = _utcnow()

        for i in range(len(beads)):
            for j in range(i + 1, len(beads)):
                a, b = beads[i], beads[j]
                result.pairs_examined += 1

                # File overlap edge.
                file_w = _jaccard(a["files"], b["files"])
                if file_w > 0.0:
                    self._upsert_edge(
                        conn, a["bead_id"], b["bead_id"],
                        "file_overlap", file_w, now,
                    )
                    result.edges_added += 1
                    if file_w >= CLUSTER_MIN_WEIGHT:
                        edges_for_clustering.append(
                            (a["bead_id"], b["bead_id"], file_w)
                        )

                # Tag overlap edge — informational, lower-weight.
                tag_w = _jaccard(a["tags"], b["tags"])
                if tag_w > 0.0:
                    self._upsert_edge(
                        conn, a["bead_id"], b["bead_id"],
                        "tag_overlap", tag_w, now,
                    )
                    result.edges_added += 1

                # Conflict detection.
                if self._is_conflict(a, b):
                    self._upsert_edge(
                        conn, a["bead_id"], b["bead_id"],
                        "conflict", 1.0, now,
                    )
                    result.edges_added += 1
                    result.conflicts_flagged += 1

        try:
            conn.commit()
        except Exception as exc:
            _log.warning("BeadSynthesizer: edge commit failed: %s", exc)
            result.errors.append(f"edge_commit_failed: {exc}")

        # Cluster discovery — connected components over file_overlap edges
        # at weight ≥ CLUSTER_MIN_WEIGHT.
        try:
            self._refresh_clusters(conn, beads, edges_for_clustering, result)
        except Exception as exc:
            _log.warning("BeadSynthesizer: cluster refresh failed: %s", exc)
            result.errors.append(f"cluster_failed: {exc}")

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _tables_exist(conn) -> bool:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' "
                "AND name IN ('bead_edges', 'bead_clusters', 'beads')"
            ).fetchone()
            return row is not None and row[0] >= 3
        except Exception:
            return False

    @staticmethod
    def _load_open_beads(conn) -> list[dict]:
        """Load every open bead with the columns synthesis needs.

        Returns dicts (not Bead model instances) to keep this module
        decoupled from the model layer and cheap to call.
        """
        rows = conn.execute(
            "SELECT bead_id, bead_type, content, tags, affected_files "
            "FROM beads WHERE status = 'open'"
        ).fetchall()

        beads: list[dict] = []
        for r in rows:
            # Row may be tuple or sqlite3.Row depending on row_factory.
            if isinstance(r, dict):
                bead_id = r["bead_id"]
                bead_type = r["bead_type"]
                content = r["content"]
                tags_raw = r["tags"]
                files_raw = r["affected_files"]
            else:
                bead_id, bead_type, content, tags_raw, files_raw = r

            beads.append({
                "bead_id": bead_id,
                "bead_type": bead_type or "",
                "content": content or "",
                "tags": _decode_json_list(tags_raw),
                "files": _decode_json_list(files_raw),
            })
        # Deterministic order — keeps test snapshots stable.
        beads.sort(key=lambda b: b["bead_id"])
        return beads

    @staticmethod
    def _upsert_edge(
        conn,
        src: str,
        dst: str,
        edge_type: str,
        weight: float,
        created_at: str,
    ) -> None:
        # Always order endpoints lexicographically so we don't double-count
        # (src,dst) vs (dst,src) — the graph is undirected.
        if src > dst:
            src, dst = dst, src
        conn.execute(
            "INSERT INTO bead_edges "
            "  (src_bead_id, dst_bead_id, edge_type, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(src_bead_id, dst_bead_id, edge_type) "
            "DO UPDATE SET weight = excluded.weight",
            (src, dst, edge_type, float(weight), created_at),
        )

    @staticmethod
    def _is_conflict(a: dict, b: dict) -> bool:
        """Both beads are warnings, share primary tag, but disagree."""
        if a["bead_type"] != "warning" or b["bead_type"] != "warning":
            return False
        ptag_a = _primary_tag(a["tags"])
        ptag_b = _primary_tag(b["tags"])
        if not ptag_a or ptag_a != ptag_b:
            return False
        toks_a = _tokenize(a["content"])
        toks_b = _tokenize(b["content"])
        if not toks_a or not toks_b:
            return False
        overlap = _jaccard(toks_a, toks_b)
        return overlap < CONFLICT_TOKEN_OVERLAP_MAX

    def _refresh_clusters(
        self,
        conn,
        beads: list[dict],
        edges: list[tuple[str, str, float]],
        result: SynthesisResult,
    ) -> None:
        """Recompute connected components and replace ``bead_clusters``.

        Strategy: union-find over edges that meet the cluster weight
        threshold.  Components with <2 members are dropped.  Labels are
        derived from the most-common shared tag, falling back to the
        first bead's content snippet.
        """
        # Wipe prior clusters.  Synthesizer fully owns this table.
        conn.execute("DELETE FROM bead_clusters")

        if not edges:
            conn.commit()
            return

        bead_lookup = {b["bead_id"]: b for b in beads}

        # Union-find.
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                # Stable: smaller id becomes the root for deterministic
                # cluster_id derivation.
                if rx < ry:
                    parent[ry] = rx
                else:
                    parent[rx] = ry

        for src, dst, _w in edges:
            union(src, dst)

        # Collect components.
        groups: dict[str, list[str]] = {}
        for bead_id in parent:
            root = find(bead_id)
            groups.setdefault(root, []).append(bead_id)

        now = _utcnow()
        for root, members in groups.items():
            if len(members) < 2:
                continue
            members_sorted = sorted(members)
            cluster_id = self._derive_cluster_id(members_sorted)
            label = self._derive_label(members_sorted, bead_lookup)
            conn.execute(
                "INSERT OR REPLACE INTO bead_clusters "
                "  (cluster_id, label, bead_ids, created_at) "
                "VALUES (?, ?, ?, ?)",
                (cluster_id, label, json.dumps(members_sorted), now),
            )
            result.clusters_created += 1

        conn.commit()

    @staticmethod
    def _derive_cluster_id(members: list[str]) -> str:
        """Deterministic, short cluster id derived from the member set."""
        h = hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()[:8]
        return f"bc-{h}"

    @staticmethod
    def _derive_label(members: list[str], bead_lookup: dict) -> str:
        """Pick the most-common shared tag, else first bead's content snippet."""
        tag_counts: dict[str, int] = {}
        for bead_id in members:
            bead = bead_lookup.get(bead_id)
            if not bead:
                continue
            for tag in bead["tags"]:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if tag_counts:
            # Stable tie-break: highest count, then lexicographic.
            best_tag = sorted(
                tag_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )[0][0]
            return best_tag
        first = bead_lookup.get(members[0])
        if first:
            snippet = (first["content"] or "").strip().splitlines()[0:1]
            if snippet:
                return snippet[0][:60]
        return members[0]
