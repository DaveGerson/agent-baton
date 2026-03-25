"""Outbound webhook subsystem for the Agent Baton API.

This package implements event-driven outbound webhooks that notify
external systems (Slack, CI pipelines, custom endpoints) when
orchestration events occur.

Components:

- :mod:`~agent_baton.api.webhooks.registry` -- CRUD for webhook
  subscriptions, persisted to ``webhooks.json``.
- :mod:`~agent_baton.api.webhooks.dispatcher` -- Subscribes to the
  shared ``EventBus``, delivers matching events with retry and
  HMAC-SHA256 signing.
- :mod:`~agent_baton.api.webhooks.payloads` -- Payload formatters
  (generic JSON and Slack Block Kit).

The dispatcher is wired to the bus in ``create_app()`` so that webhook
delivery begins automatically as soon as the API server starts.
"""