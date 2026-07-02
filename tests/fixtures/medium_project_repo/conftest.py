"""Prevent pytest from ever collecting anything under this fixture repo.

This directory is fixture DATA copied into a ``tmp_path`` by
``tests/e2e/test_manager_mode_planning.py`` -- it is not itself a test
suite (``tests_fixture/test_service.py`` matches the default ``test_*.py``
collection glob and would otherwise get swept up by a broad ``pytest
tests/`` run).
"""
from __future__ import annotations

collect_ignore_glob = ["*"]
