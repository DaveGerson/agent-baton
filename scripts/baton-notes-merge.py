#!/usr/bin/env python3
"""JSON-aware git merge driver for refs/notes/baton-beads.

Part A of the Gastown bead architecture (bd-2870).

Installed via::

    git config merge.baton-notes.driver "scripts/baton-notes-merge %O %A %B"

Git passes three file paths to the driver:
    %O  — ancestor version (base)
    %A  — current branch version (ours)  ← driver MUST write result here
    %B  — other branch version (theirs)

Merge rules (per the Gastown design spec):
1. Parse all three files as JSON.  If either %A or %B is not valid JSON the
   driver falls back to ``cat_sort_uniq`` (write the union of both files).
2. Merge semantics:
   - ``closed_at``: take the later timestamp (or the non-empty one).
   - ``status``:    prefer "closed" over "open" over "archived" (i.e. if
                    either side closed the bead, the result is closed).
   - ``tags``:      union of both tag lists (de-duplicated, sorted).
   - ``links``:     union of both link lists (de-duplicated by
                    (target_bead_id, link_type) key, sorted).
   - ``quality_score``:  max of both values.
   - ``summary``:   prefer the non-empty / longer value.
   - ``content``:   prefer the non-empty / longer value.
   - All other fields: prefer %A (ours) unless %A is empty/None and %B
                       is not (additive).
3. If both sides set ``signed_by`` to **different non-empty values** that is
   a real conflict — emit ``BEAD_WARNING: conflict:unresolved`` to stderr
   and add the tag ``conflict:unresolved`` to the result bead.  The driver
   still writes a merged result (exit 0) so git does not stall; the
   synthesizer will flag the bead for review via the tag.
4. Exit 0 on success, 1 on unrecoverable error (git will then fall back
   to the default merge strategy).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_STATUS_RANK = {"closed": 2, "open": 1, "archived": 0}


def _later_timestamp(a: str, b: str) -> str:
    """Return the later of two ISO 8601 timestamps.  Empty string < any value."""
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def _merge_status(a: str, b: str) -> str:
    """Return the 'more terminal' status between *a* and *b*."""
    rank_a = _STATUS_RANK.get(a, 0)
    rank_b = _STATUS_RANK.get(b, 0)
    return a if rank_a >= rank_b else b


def _union_list(a: list, b: list) -> list:
    """Return de-duplicated union of *a* and *b*, preserving order (a first)."""
    seen: set = set()
    result: list = []
    for item in list(a) + list(b):
        key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _union_links(a: list, b: list) -> list:
    """Union of link lists, de-duplicated by (target_bead_id, link_type)."""
    seen: set = set()
    result: list = []
    for link in list(a) + list(b):
        if isinstance(link, dict):
            key = (link.get("target_bead_id", ""), link.get("link_type", ""))
        else:
            key = str(link)
        if key not in seen:
            seen.add(key)
            result.append(link)
    return result


def _prefer_longer(a: str, b: str) -> str:
    """Return the non-empty / longer of two strings."""
    if not a:
        return b
    if not b:
        return a
    return a if len(a) >= len(b) else b


def merge_beads(ancestor: dict, ours: dict, theirs: dict) -> tuple[dict, bool]:
    """Merge three bead dicts.  Returns ``(merged, has_conflict)``."""
    result = dict(ours)  # start from ours

    # --- additive scalar fields: prefer non-empty value ---
    for field in (
        "bead_id", "task_id", "step_id", "agent_name", "bead_type",
        "confidence", "scope", "source", "schema_version",
        "anchor_commit", "branch_at_create",
    ):
        if not result.get(field) and theirs.get(field):
            result[field] = theirs[field]

    # --- content / summary: prefer longer ---
    result["content"] = _prefer_longer(ours.get("content", ""), theirs.get("content", ""))
    result["summary"] = _prefer_longer(ours.get("summary", ""), theirs.get("summary", ""))

    # --- status: prefer more terminal ---
    result["status"] = _merge_status(
        ours.get("status", "open"), theirs.get("status", "open")
    )

    # --- closed_at: take later timestamp ---
    result["closed_at"] = _later_timestamp(
        ours.get("closed_at", ""), theirs.get("closed_at", "")
    )

    # --- created_at: take earlier (preserve provenance) ---
    a_ts = ours.get("created_at", "")
    b_ts = theirs.get("created_at", "")
    if a_ts and b_ts:
        result["created_at"] = a_ts if a_ts <= b_ts else b_ts
    else:
        result["created_at"] = a_ts or b_ts

    # --- tags: union ---
    a_tags = ours.get("tags", []) if isinstance(ours.get("tags"), list) else []
    b_tags = theirs.get("tags", []) if isinstance(theirs.get("tags"), list) else []
    result["tags"] = sorted(set(a_tags) | set(b_tags))

    # --- links: union by (target_bead_id, link_type) ---
    a_links = ours.get("links", []) if isinstance(ours.get("links"), list) else []
    b_links = theirs.get("links", []) if isinstance(theirs.get("links"), list) else []
    result["links"] = _union_links(a_links, b_links)

    # --- affected_files: union ---
    a_files = ours.get("affected_files", []) if isinstance(ours.get("affected_files"), list) else []
    b_files = theirs.get("affected_files", []) if isinstance(theirs.get("affected_files"), list) else []
    result["affected_files"] = sorted(set(a_files) | set(b_files))

    # --- quality_score: max ---
    qs_a = float(ours.get("quality_score", 0.0) or 0.0)
    qs_b = float(theirs.get("quality_score", 0.0) or 0.0)
    result["quality_score"] = max(qs_a, qs_b)

    # --- retrieval_count: max ---
    rc_a = int(ours.get("retrieval_count", 0) or 0)
    rc_b = int(theirs.get("retrieval_count", 0) or 0)
    result["retrieval_count"] = max(rc_a, rc_b)

    # --- token_estimate: max ---
    te_a = int(ours.get("token_estimate", 0) or 0)
    te_b = int(theirs.get("token_estimate", 0) or 0)
    result["token_estimate"] = max(te_a, te_b)

    # --- signed_by conflict detection (Part B placeholder) ---
    # signed_by is not shipped in Part A (bd-2870); this block is a stub for
    # when Part B (bd-d975) adds soul signatures.  If both sides set a
    # non-empty, different signed_by we flag it as a conflict.
    has_conflict = False
    signed_a = ours.get("signed_by", "") or ""
    signed_b = theirs.get("signed_by", "") or ""
    if signed_a and signed_b and signed_a != signed_b:
        has_conflict = True
        print(
            f"BEAD_WARNING: conflict:unresolved bead_id={result.get('bead_id', '?')} "
            f"signed_by_ours={signed_a!r} signed_by_theirs={signed_b!r}",
            file=sys.stderr,
        )
        # Tag the bead so the synthesizer can detect the conflict
        tags = result.get("tags", [])
        if isinstance(tags, list) and "conflict:unresolved" not in tags:
            tags.append("conflict:unresolved")
        result["tags"] = tags
        # Keep ours for now; v2 will require human resolution
        result["signed_by"] = signed_a

    return result, has_conflict


def main() -> int:
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <ancestor> <ours> <theirs>",
            file=sys.stderr,
        )
        return 1

    ancestor_path = Path(sys.argv[1])
    ours_path = Path(sys.argv[2])
    theirs_path = Path(sys.argv[3])

    # Read all three files
    def _read(p: Path) -> dict | None:
        try:
            text = p.read_text(encoding="utf-8").strip()
            if not text:
                return {}
            return json.loads(text)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"baton-notes-merge: cannot parse {p}: {exc}", file=sys.stderr)
            return None

    ancestor = _read(ancestor_path) if ancestor_path.exists() else {}
    ours = _read(ours_path)
    theirs = _read(theirs_path)

    # If either current or other is not JSON, fall back: write union text
    if ours is None or theirs is None:
        print("baton-notes-merge: non-JSON input — falling back to union", file=sys.stderr)
        # Write both contents concatenated to %A (ours)
        try:
            a_text = ours_path.read_text(encoding="utf-8") if ours_path.exists() else ""
            b_text = theirs_path.read_text(encoding="utf-8") if theirs_path.exists() else ""
            ours_path.write_text(a_text + "\n" + b_text, encoding="utf-8")
        except OSError:
            pass
        return 0  # non-fatal

    if ancestor is None:
        ancestor = {}

    merged, has_conflict = merge_beads(ancestor, ours, theirs)

    try:
        ours_path.write_text(
            json.dumps(merged, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"baton-notes-merge: cannot write result to {ours_path}: {exc}", file=sys.stderr)
        return 1

    # Exit 0 even on conflict — we wrote a valid merged result and tagged it.
    return 0


if __name__ == "__main__":
    sys.exit(main())
