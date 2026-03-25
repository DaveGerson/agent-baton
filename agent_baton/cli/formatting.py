"""Centralized table formatting for CLI output."""
from __future__ import annotations


def print_table(
    rows: list[dict[str, str]],
    columns: list[str],
    *,
    headers: dict[str, str] | None = None,
    alignments: dict[str, str] | None = None,
    max_col_width: int = 0,
    prefix: str = "",
) -> None:
    """Print a formatted table to stdout.

    Args:
        rows: List of dicts, each mapping column key to display value.
        columns: Ordered list of column keys to display.
        headers: Optional display names for columns (default: uppercase key
            with underscores replaced by spaces).
        alignments: Per-column alignment: '<' left (default), '>' right.
        max_col_width: Truncate cell values to this width (0 = no truncation).
        prefix: String to prepend to each line (e.g., "  " for indentation).
    """
    if not rows:
        return

    headers = headers or {}
    alignments = alignments or {}

    # Compute display header labels
    display_headers = {c: headers.get(c, c.upper().replace("_", " ")) for c in columns}

    # Compute column widths: max of header label width and all cell value widths
    widths: dict[str, int] = {}
    for c in columns:
        vals = [str(r.get(c, "") or "") for r in rows]
        max_val = max((len(v) for v in vals), default=0)
        widths[c] = max(len(display_headers[c]), max_val)
        if max_col_width > 0:
            widths[c] = min(widths[c], max_col_width)

    # Header line
    hdr_parts = []
    for c in columns:
        align = alignments.get(c, "<")
        hdr_parts.append(f"{display_headers[c]:{align}{widths[c]}}")
    print(f"{prefix}{'  '.join(hdr_parts)}")

    # Separator line — spans the full header width including inter-column gaps
    total_width = sum(widths[c] for c in columns) + 2 * (len(columns) - 1)
    print(f"{prefix}{'-' * total_width}")

    # Data rows
    for row in rows:
        parts = []
        for c in columns:
            val = str(row.get(c, "") or "")
            if max_col_width > 0 and len(val) > max_col_width:
                val = val[: max_col_width - 1] + "\u2026"
            align = alignments.get(c, "<")
            parts.append(f"{val:{align}{widths[c]}}")
        print(f"{prefix}{'  '.join(parts)}")
