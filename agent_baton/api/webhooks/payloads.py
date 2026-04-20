"""Webhook payload formatters.

Each formatter takes an :class:`~agent_baton.models.events.Event` and returns
a dict that will be JSON-serialised and POSTed to the webhook endpoint.

Two formatters are provided:

- :func:`format_generic` — the default; just wraps ``event.to_dict()``.
- :func:`format_slack` — Slack Block Kit layout for ``human.decision_needed``
  events so that operators get a readable notification in Slack with CLI
  instructions for responding.

Note on interactivity: Slack Block Kit action buttons are intentionally
omitted.  There is no callback endpoint to receive button payloads, so
buttons would appear clickable but do nothing.  When real Slack interactivity
is needed, add a proper Slack app manifest, OAuth flow, and callback endpoint
first, then re-introduce the ``actions`` block.  Until then, operators
respond via ``baton execute approve`` or ``baton decide`` in the CLI.
"""
from __future__ import annotations

from agent_baton.models.events import Event


def format_generic(event: Event) -> dict:
    """Default JSON payload — wraps :meth:`Event.to_dict` verbatim.

    This is the fallback used for all event types that do not have a
    specialised formatter.

    Args:
        event: The event to serialise.

    Returns:
        A plain dict containing all event fields.
    """
    return event.to_dict()


def format_slack(event: Event) -> dict:
    """Slack Block Kit payload for ``human.decision_needed`` events.

    Produces a ``blocks``-based message that shows:

    - A header with the decision type.
    - A section with the decision summary.
    - A list of available options (as bullet points).
    - A context block with task ID, request ID, and timestamp.
    - A CLI instructions block telling the operator how to respond.

    Action buttons are deliberately excluded — no callback endpoint exists.
    Operators respond via ``baton execute approve`` or ``baton decide`` in
    the CLI.

    For events other than ``human.decision_needed`` this falls back to a
    minimal text attachment carrying the raw event dict.

    Args:
        event: The event to format.

    Returns:
        A Slack-compatible Block Kit message payload dict.
    """
    if event.topic != "human.decision_needed":
        # Fallback: plain text attachment with raw event data.
        return {
            "text": f"Agent Baton event: `{event.topic}`",
            "attachments": [
                {
                    "color": "#439FE0",
                    "text": (
                        f"*task_id:* {event.task_id}\n"
                        f"*event_id:* {event.event_id}\n"
                        f"*timestamp:* {event.timestamp}"
                    ),
                    "mrkdwn_in": ["text"],
                }
            ],
        }

    payload = event.payload
    request_id: str = payload.get("request_id", "")
    decision_type: str = payload.get("decision_type", "Decision Required")
    summary: str = payload.get("summary", "")
    options: list[str] = payload.get("options", [])

    # Build options bullet list for the body section.
    options_text = (
        "\n".join(f"• `{opt}`" for opt in options) if options else "_No options listed._"
    )

    blocks: list[dict] = [
        # Header
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Decision Required: {decision_type}",
                "emoji": False,
            },
        },
        # Summary section
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": summary or "_No summary provided._",
            },
        },
    ]

    # Options section (only when options are present)
    if options:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Available options:*\n{options_text}",
                },
            }
        )

    # Divider
    blocks.append({"type": "divider"})

    # Context metadata
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*Task:* `{event.task_id}` | "
                        f"*Request:* `{request_id}` | "
                        f"*At:* {event.timestamp}"
                    ),
                }
            ],
        }
    )

    # CLI instructions — how to respond without Slack interactivity.
    cli_instructions = (
        f"`baton execute approve --phase-id <N> --result approve`  "
        f"or  `baton decide --request-id {request_id} --choice <option>`"
    )
    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*To respond, run in your terminal:*\n{cli_instructions}",
            },
        }
    )

    return {
        "text": f"Decision required for task `{event.task_id}`: {decision_type}",
        "blocks": blocks,
    }
