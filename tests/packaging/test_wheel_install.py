"""Packaging smoke test (bd-rm-ux-p2).

Builds a real wheel from the repo, installs it into a *fresh* venv (no
source checkout on ``sys.path``, no editable install), and runs the
``baton`` console-script entry point from it. This is the only test in the
suite that proves the bundled resources referenced by
``agent_baton.cli.commands.diagnostics_cmd`` (``_bundled_agents/*.md``, and
friends) are actually importable *package data* rather than files that
merely happen to sit next to a developer's git checkout.

Slow (whole-wheel build + fresh venv + pip install) and requires the
``build`` package (``pip install build``). Excluded from the default test
run via the ``packaging`` marker -- see ``addopts`` in ``pyproject.toml``.
CI runs it explicitly in a dedicated packaging-smoke job with
``-m packaging``.

Run directly with::

    pip install build
    python -m pytest -q -m packaging tests/packaging/
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import venv
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_AVAILABLE = importlib.util.find_spec("build") is not None


def _venv_bin(venv_dir: Path, name: str) -> Path:
    """Resolve *name* (e.g. ``python``, ``baton``) inside *venv_dir*, honouring
    the Windows ``Scripts/`` vs. POSIX ``bin/`` layout difference."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


@pytest.mark.skipif(
    not _BUILD_AVAILABLE,
    reason="the 'build' package is not installed -- run `pip install build` "
    "to exercise this packaging smoke test",
)
def test_wheel_installs_and_cli_runs_without_source_checkout(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    venv_dir = tmp_path / "venv"
    # A working directory that is NOT the repo checkout -- proves the CLI
    # doesn't secretly depend on being run from inside the source tree.
    consumer_dir = tmp_path / "consumer-project"
    consumer_dir.mkdir()

    build_result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert build_result.returncode == 0, (
        f"wheel build failed:\nstdout={build_result.stdout}\n"
        f"stderr={build_result.stderr}"
    )

    wheels = sorted(dist_dir.glob("agent_baton-*.whl"))
    assert wheels, f"`python -m build --wheel` produced no wheel in {dist_dir}"
    wheel_path = wheels[-1]

    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = _venv_bin(venv_dir, "python")

    install_result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--quiet", str(wheel_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert install_result.returncode == 0, (
        f"wheel install failed:\nstdout={install_result.stdout}\n"
        f"stderr={install_result.stderr}"
    )

    baton_bin = _venv_bin(venv_dir, "baton")
    assert baton_bin.exists(), (
        f"pip install did not create the 'baton' console-script entry "
        f"point at {baton_bin}"
    )

    help_result = subprocess.run(
        [str(baton_bin), "--help"],
        cwd=consumer_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert help_result.returncode == 0, (
        f"stdout={help_result.stdout}\nstderr={help_result.stderr}"
    )
    assert "baton" in help_result.stdout.lower()

    doctor_result = subprocess.run(
        [str(baton_bin), "doctor", "--json"],
        cwd=consumer_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # doctor may exit 0 or 1 depending on environment-driven check statuses
    # (e.g. no `bd` binary in this throwaway venv/consumer dir) -- what
    # matters here is that the wheel install produced a working, importable
    # CLI that emits valid JSON and resolves its bundled resources, not that
    # every diagnostic check passes.
    payload = json.loads(doctor_result.stdout)
    assert payload["schema_version"] == 1
    assert isinstance(payload["ok"], bool)

    check_ids = {check["id"] for check in payload["checks"]}
    assert "bundled_agents" in check_ids
    bundled = next(c for c in payload["checks"] if c["id"] == "bundled_agents")
    assert bundled["details"]["count"] > 0, (
        "wheel-installed package resolved zero bundled agents -- package "
        "resources were not bundled correctly into the wheel"
    )
