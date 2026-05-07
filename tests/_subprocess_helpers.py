"""Helpers for subprocess invocations of the agent_baton CLI in tests.

Tests that invoke ``python -m agent_baton.cli.main ...`` as a subprocess
need the project root on PYTHONPATH so the spawned interpreter can import
``agent_baton`` even when pytest's tool environment does not have the
package installed into its site-packages.
"""
from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


def cli_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env dict suitable for subprocess.run() invocations of
    ``python -m agent_baton.cli.main ...``.  Ensures the project root is on
    PYTHONPATH so the subprocess interpreter can import ``agent_baton``
    even when pytest's tool env does not have the package installed.
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    parts = [_PROJECT_ROOT] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    if extra:
        env.update(extra)
    return env
