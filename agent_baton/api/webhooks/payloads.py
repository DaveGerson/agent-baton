"""Webhook payload formatters.

Each formatter takes an :class:`~agent_baton.models.events.Event` and returns
a dict that will be JSON-serialised and POSTed to the webhook endpoint.

Two formatters are provided:

- :func:`format_generic` — the default; just wraps ``event.to_dict()``.
- :func:`format_slack` — Slack Block Kit layout for ``human.decision_needed``
  events so that operators get a rich, actionable notification in Slack.
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
    - A context block with task ID and timestamp.
    - Action buttons — one per option — for interactive Slack apps that
      implement the ``interactions_endpoint`` (the button payloads carry the
      ``request_id`` and ``option`` values).

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

    # Action buttons — one per option (capped at 5, Slack's limit per block)
    if options:
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": opt, "emoji": False},
                "value": f"{request_id}::{opt}",
                "action_id": f"decision_{idx}",
            }
            for idx, opt in enumerate(options[:5])
        ]
        blocks.append({"type": "actions", "elements": buttons})

    return {
        "text": f"Decision required for task `{event.task_id}`: {decision_type}",
        "blocks": blocks,
    }
