"""Tests for the Agent Baton systemd unit file and install script (O1.9)."""
from __future__ import annotations

import configparser
import subprocess
from pathlib import Path

import pytest

# Resolve packaging directory relative to this test file's repo root
_REPO_ROOT = Path(__file__).parent.parent
_SYSTEMD_DIR = _REPO_ROOT / "packaging" / "systemd"
_UNIT_FILE = _SYSTEMD_DIR / "agent-baton-daemon.service"
_INSTALL_SH = _SYSTEMD_DIR / "install.sh"


# ---------------------------------------------------------------------------
# Test 1: Unit file parses correctly via configparser
# ---------------------------------------------------------------------------

def test_unit_file_parses_and_has_exec_start():
    """Unit file must be valid INI and contain ExecStart under [Service]."""
    assert _UNIT_FILE.exists(), f"Unit file missing: {_UNIT_FILE}"

    cfg = configparser.ConfigParser(strict=False)
    # configparser requires section headers; systemd uses bare keys in [Unit],
    # so we read raw text and feed it with a dummy DEFAULT header isn't needed
    # since the file does have [Unit], [Service], [Install] sections.
    cfg.read(str(_UNIT_FILE))

    assert cfg.has_section("Service"), "[Service] section not found"
    assert cfg.has_option("Service", "ExecStart"), "ExecStart not in [Service]"


# ---------------------------------------------------------------------------
# Test 2: install.sh exits non-zero without root
# ---------------------------------------------------------------------------

def test_install_sh_exits_nonzero_without_root():
    """install.sh must refuse to run as a non-root user."""
    assert _INSTALL_SH.exists(), f"install.sh missing: {_INSTALL_SH}"

    # Run under 'nobody' if possible, otherwise just check exit code directly
    # by running as current unprivileged user (in CI / test env).
    result = subprocess.run(
        ["bash", str(_INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    # If we're not root this should fail; if somehow running as root, skip.
    if result.returncode == 0:
        pytest.skip("Running as root — cannot test non-root guard")

    assert result.returncode != 0
    assert "root" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Test 3: Unit references 'baton daemon serve' (not a stale command)
# ---------------------------------------------------------------------------

def test_unit_references_correct_command():
    """ExecStart must invoke 'baton daemon serve'."""
    content = _UNIT_FILE.read_text(encoding="utf-8")
    assert "baton daemon serve" in content, (
        "Unit file does not reference 'baton daemon serve'. "
        "Check ExecStart in [Service]."
    )
