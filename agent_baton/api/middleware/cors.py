"""CORS configuration for the Agent Baton API.

By default only localhost origins are permitted, which is appropriate for a
daemon that runs on the developer's machine.  The caller can pass an explicit
``allowed_origins`` list to widen or restrict access.

DECISION: We use ``allow_origin_regex`` rather than ``allow_origins`` because
the default localhost patterns must match any port number
(e.g. ``http://localhost:3000`` and ``http://localhost:5173``).  FastAPI's
CORSMiddleware supports regex patterns via ``allow_origin_regex``, but to keep
the interface simple this module also accepts plain string origins in
``allowed_origins`` and passes them through ``allow_origins`` directly.  Both
lists are applied together if provided.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Regex covering all localhost and 127.0.0.1 origins on any port.
_DEFAULT_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"


def configure_cors(
    app: FastAPI,
    allowed_origins: list[str] | None = None,
) -> None:
    """Add :class:`CORSMiddleware` to *app*.

    Args:
        app: The FastAPI application instance.
        allowed_origins: Explicit list of allowed origin strings.  When
            ``None`` (the default) only localhost and 127.0.0.1 origins
            (any port) are accepted via the ``allow_origin_regex`` rule.
            Passing an empty list ``[]`` also falls back to the regex default.
            Passing ``["*"]`` allows all origins.
    """
    origins: list[str] = allowed_origins or []
    origin_regex: str | None = None

    if not origins:
        # No explicit origins — use the localhost regex default.
        origin_regex = _DEFAULT_ORIGIN_REGEX

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
