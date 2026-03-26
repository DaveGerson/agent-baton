"""Middleware package for the Agent Baton API.

Contains the middleware stack applied to every request:

- :mod:`~agent_baton.api.middleware.cors` -- Configures CORS headers.
  Defaults to allowing localhost/127.0.0.1 origins on any port.
- :mod:`~agent_baton.api.middleware.auth` -- Optional Bearer token
  authentication.  No-op when no token is configured.  Health and
  readiness probes are always exempt.

Middleware ordering matters: CORS is added before auth so that
pre-flight ``OPTIONS`` requests are answered without requiring a
token.
"""
