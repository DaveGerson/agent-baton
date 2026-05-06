"""Render a PlanSnapshot as a self-contained HTML page."""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.visualize.snapshot import PlanSnapshot

_TEMPLATE_PATH = Path(__file__).parent / "web_template.html"


def render_html(snapshot: PlanSnapshot) -> str:
    """Inject snapshot JSON into the HTML template.

    Args:
        snapshot: A PlanSnapshot with a to_dict() method.

    Returns:
        Complete HTML string ready to serve or write to disk.
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    data_json = json.dumps(snapshot.to_dict(), indent=None)
    return template.replace("__BATON_PLAN_DATA__", data_json)
