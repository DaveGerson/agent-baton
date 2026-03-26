"""Webhook management endpoints for the Agent Baton API.

POST   /webhooks               — register a new webhook subscription
GET    /webhooks               — list all registered webhooks
DELETE /webhooks/{webhook_id}  — remove a webhook subscription
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_baton.api.deps import get_webhook_registry
from agent_baton.api.models.requests import RegisterWebhookRequest
from agent_baton.api.models.responses import WebhookResponse
from agent_baton.api.webhooks.registry import WebhookRegistry

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /webhooks — register a webhook
# ---------------------------------------------------------------------------

@router.post("/webhooks", response_model=WebhookResponse, status_code=201)
async def register_webhook(
    body: RegisterWebhookRequest,
    registry: WebhookRegistry = Depends(get_webhook_registry),
) -> WebhookResponse:
    """Register a new outbound webhook subscription.

    POST /api/v1/webhooks

    The webhook will receive a POST request for every event whose topic
    matches one of the supplied ``events`` patterns.  Patterns are
    glob-style (e.g. ``step.*`` matches ``step.completed`` and
    ``step.failed``).

    When a ``secret`` is supplied, every delivery will include an
    ``X-Baton-Signature`` header carrying the HMAC-SHA256 hex digest of
    the request body, computed with the secret as key.  Receivers can
    use this to verify payload authenticity.

    Args:
        body: Validated request body with ``url``, ``events`` list,
            and optional ``secret``.
        registry: Injected ``WebhookRegistry`` singleton.

    Returns:
        A ``WebhookResponse`` with the auto-assigned ``webhook_id``
        (201 Created).
    """
    entry = registry.register(
        url=body.url,
        events=body.events,
        secret=body.secret,
    )
    return WebhookResponse(
        webhook_id=entry["webhook_id"],
        url=entry["url"],
        events=entry["events"],
        created=entry["created"],
    )


# ---------------------------------------------------------------------------
# GET /webhooks — list all webhooks
# ---------------------------------------------------------------------------

@router.get("/webhooks", response_model=list[WebhookResponse])
async def list_webhooks(
    registry: WebhookRegistry = Depends(get_webhook_registry),
) -> list[WebhookResponse]:
    """Return all registered webhook subscriptions.

    GET /api/v1/webhooks

    Includes both enabled and disabled webhooks.  Disabled webhooks are
    those that have been automatically paused after exceeding the
    consecutive-failure threshold (10 consecutive failures).

    Args:
        registry: Injected ``WebhookRegistry`` singleton.

    Returns:
        A list of ``WebhookResponse`` objects.
    """
    entries = registry.list_all()
    return [
        WebhookResponse(
            webhook_id=e["webhook_id"],
            url=e["url"],
            events=e["events"],
            created=e["created"],
        )
        for e in entries
    ]


# ---------------------------------------------------------------------------
# DELETE /webhooks/{webhook_id} — remove a webhook
# ---------------------------------------------------------------------------

@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    registry: WebhookRegistry = Depends(get_webhook_registry),
) -> dict:
    """Remove a webhook subscription permanently.

    DELETE /api/v1/webhooks/{webhook_id}

    Args:
        webhook_id: The webhook registration ID (URL path parameter).
        registry: Injected ``WebhookRegistry`` singleton.

    Returns:
        ``{"deleted": true}``

    Raises:
        HTTPException 404: If no webhook with the given *webhook_id*
            exists.
    """
    deleted = registry.delete(webhook_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Webhook '{webhook_id}' not found.",
        )
    return {"deleted": True}
