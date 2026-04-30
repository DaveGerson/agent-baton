"""Shared status-to-color mapping constants.

Each entry maps a status string to a ``(rich_style, css_class, hex_color)``
tuple.  Consumed by both the CLI renderer (``rich_style``) and the web
renderer (``css_class`` / ``hex_color``).
"""
from __future__ import annotations

# (rich_style, css_class, hex_color)
STATUS_COLORS: dict[str, tuple[str, str, str]] = {
    "complete":    ("green",         "complete",    "#3fb950"),
    "running":     ("cyan",          "running",     "#58a6ff"),
    "dispatched":  ("cyan",          "running",     "#58a6ff"),
    "pending":     ("dim",           "pending",     "#484f58"),
    "failed":      ("red",           "failed",      "#f85149"),
    "skipped":     ("dim strike",    "skipped",     "#484f58"),
    "interrupted": ("yellow",        "interrupted", "#d29922"),
    "interacting": ("bright_cyan",   "interacting", "#79c0ff"),
    "passed":      ("green",         "passed",      "#3fb950"),
    "gate_pending": ("yellow",       "gate-pending", "#d29922"),
    "not_started": ("dim",           "not-started", "#484f58"),
}

RISK_COLORS: dict[str, tuple[str, str, str]] = {
    "LOW":      ("green",          "risk-low",      "#3fb950"),
    "MEDIUM":   ("yellow",         "risk-medium",   "#d29922"),
    "HIGH":     ("bright_red",     "risk-high",     "#f0883e"),
    "CRITICAL": ("bright_magenta", "risk-critical", "#bc8cff"),
}
