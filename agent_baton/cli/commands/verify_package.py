"""Backward-compatibility shim: re-exports from distribute/verify_package.py."""
from __future__ import annotations

from agent_baton.cli.commands.distribute.verify_package import handler, register  # noqa: F401

__all__ = ["handler", "register"]
