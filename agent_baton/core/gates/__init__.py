"""Gate implementations for non-shell quality checks.

This package houses gate runners that go beyond the local subprocess gates
in :mod:`agent_baton.core.engine.gates`.  The current contents:

- :mod:`agent_baton.core.gates.ci_gate` — polls CI provider workflows
  (GitHub Actions today, GitLab CI as future work) for the current
  branch's HEAD commit and returns a pass/fail :class:`CIGateResult`.

The split keeps engine-internal subprocess gates separate from
provider-integrated gates, which have different failure modes (network,
auth) and different testability requirements.
"""
from __future__ import annotations

from agent_baton.core.gates.ci_gate import (
    CIGateResult,
    CIGateRunner,
    parse_ci_gate_config,
)

__all__ = ["CIGateResult", "CIGateRunner", "parse_ci_gate_config"]
