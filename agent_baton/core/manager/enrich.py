"""``BATON_MANAGER_ENRICH`` -- optional LLM polish for a deterministic charter (M2).

See docs/internal/manager-mode-pmo-design.md ("Locked decision 3") and
docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5. Mirrors the
client pattern used by ``agent_baton.core.engine.goal_evaluator``
(``BATON_GOAL_EVALUATOR``): a stub/off path that is the default and is
always deterministic, plus an opt-in LLM path that lazily imports the
``anthropic`` SDK and falls back to the input unchanged on *any* failure
(missing SDK, missing API key, network error, malformed response).

``ProjectCharterBuilder`` (``agent_baton.core.manager.charter``) never
calls this module itself -- it stays fully deterministic (no clock, no
randomness, no env reads). Callers that want enrichment invoke
:func:`maybe_enrich_charter` explicitly on the builder's output (wired by
``ManagerModePlanner`` in Wave 3).

Env var: ``BATON_MANAGER_ENRICH`` = ``off`` (default, unset) | ``haiku``
| ``sonnet`` | ``opus``. Tests exercise only the off/stub path -- no live
Claude calls anywhere in tests (see
docs/internal/manager-mode-pmo-plan.md "Rules for every task").

The LLM path only polishes wording on ``objective``, ``background``, and
``assumptions`` -- it is instructed never to invent new paths,
workstreams, or facts, and any output that fails to parse is discarded in
favor of the deterministic charter.
"""
from __future__ import annotations

import logging
import os

from agent_baton.models.manager import ProjectCharter

logger = logging.getLogger(__name__)

_MODELS = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-5",
    "opus": "claude-opus-4-8",
}

_ENRICH_PROMPT = """\
You are polishing the wording of a project charter for an AI orchestration \
engine. A deterministic pipeline already derived the facts below from a \
real execution plan -- do NOT invent new scope, paths, workstreams, or \
facts. Only improve clarity and phrasing of the existing objective, \
background, and assumptions.

Original task summary:
{task_summary}

Current objective:
{objective}

Current background:
{background}

Current assumptions:
{assumptions}

Respond with ONLY a JSON object of this shape:
{{
  "objective": <string>,
  "background": <string>,
  "assumptions": [<string>, ...]
}}
"""


def maybe_enrich_charter(charter: ProjectCharter, task_summary: str) -> ProjectCharter:
    """Optionally polish *charter* wording via an LLM; identity by default.

    Reads ``BATON_MANAGER_ENRICH`` from the environment. ``off``
    (default), unset, or an unrecognized value returns *charter*
    unchanged. ``haiku``/``sonnet``/``opus`` call Anthropic to polish
    ``objective``/``background``/``assumptions`` only; any exception
    (missing SDK, missing ``ANTHROPIC_API_KEY``, network error, malformed
    JSON response) is logged at debug level and falls back to *charter*
    unchanged -- enrichment failure must never break planning.
    """
    mode = os.environ.get("BATON_MANAGER_ENRICH", "off").strip().lower()
    if mode in ("", "off", "0", "false"):
        return charter

    if mode not in _MODELS:
        logger.debug(
            "maybe_enrich_charter: unrecognized BATON_MANAGER_ENRICH=%r "
            "(expected off|haiku|sonnet|opus); skipping enrichment",
            mode,
        )
        return charter

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug(
            "maybe_enrich_charter: ANTHROPIC_API_KEY unset; skipping enrichment"
        )
        return charter

    try:
        import json as _json

        import anthropic  # type: ignore[import-not-found]

        client = anthropic.Anthropic(api_key=api_key)
        prompt = _ENRICH_PROMPT.format(
            task_summary=task_summary,
            objective=charter.objective,
            background=charter.background,
            assumptions="\n".join(f"- {a}" for a in charter.assumptions) or "(none)",
        )
        response = client.messages.create(
            model=_MODELS[mode],
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ).strip()
        data = _json.loads(text)

        objective = str(data.get("objective") or "").strip() or charter.objective
        background = str(data.get("background") or "").strip() or charter.background
        assumptions_raw = data.get("assumptions")
        assumptions = (
            [str(a) for a in assumptions_raw]
            if isinstance(assumptions_raw, list) and assumptions_raw
            else charter.assumptions
        )

        return charter.model_copy(
            update={
                "objective": objective,
                "background": background,
                "assumptions": assumptions,
            }
        )
    except Exception as exc:  # noqa: BLE001 -- enrichment must never break planning
        logger.debug(
            "maybe_enrich_charter: enrichment failed (%s); using deterministic charter",
            exc,
        )
        return charter
