"""Unified Policy Engine -- evaluate agent assignments against guardrail policy rules.

The policy engine is the enforcement layer that checks whether an agent's
planned file writes and tool usage comply with the active guardrail preset.
Policies are expressed as declarative ``PolicyRule`` objects grouped into
``PolicySet`` collections.

Five standard presets are built in:

* **standard_dev** (LOW risk) -- blocks writes to ``.env``, ``secrets/``,
  ``node_modules/``; restricts review agents from Write/Bash tools.
* **data_analysis** (LOW risk) -- constrains data agents to ``output/``
  directories; blocks writes to source ``data/`` directories; requires
  PII masking gate.
* **infrastructure** (HIGH risk) -- limits infra file writes to devops
  agents; requires auditor pre-review and a rollback plan gate.
* **regulated** (HIGH/CRITICAL risk) -- requires subject-matter-expert
  and auditor agents; enforces append-only historical records and full
  audit trails; blocks Bash on regulated data.
* **security** (HIGH risk) -- requires auditor and security-reviewer
  agents; isolates auth code writes to the implementing agent; enforces
  no-hardcoded-credentials gate.

Custom presets can be persisted as JSON files under ``.claude/policies/``
and are loaded on demand by the engine. On-disk presets take precedence
over built-in presets of the same name.

Rule types and their evaluation semantics:

* ``path_block`` -- agent must not write to paths matching the glob pattern.
* ``path_allow`` -- advisory: agent writes outside the allowed pattern
  generate a warning.
* ``tool_restrict`` -- agent must not use the listed tools.
* ``require_agent`` -- the execution plan must include the named agent
  (structural check, surfaced as a warning during per-agent evaluation).
* ``require_gate`` -- the execution plan must include the named gate
  check (structural check, surfaced as a warning).

Each rule has a severity of ``"block"`` (hard failure) or ``"warn"``
(advisory). The engine returns a list of ``PolicyViolation`` objects so the
caller can decide how to handle blocks versus warnings.

**Status: Experimental** -- built and tested but not yet validated with real
usage data.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PolicyRule:
    """A single guardrail policy rule.

    Attributes:
        name: Machine-readable identifier for the rule (e.g.
            ``"block_env_files"``).
        description: Human-readable explanation of what the rule enforces.
        scope: Which agents this rule applies to. ``"all"`` matches every
            agent; otherwise interpreted as an ``fnmatch`` pattern against
            the agent name (e.g. ``"*reviewer*"``).
        rule_type: The enforcement mechanism. One of:

            - ``"path_block"`` -- block writes to matching paths.
            - ``"path_allow"`` -- warn when writes occur outside allowed
              paths.
            - ``"tool_restrict"`` -- block use of specified tools.
            - ``"require_agent"`` -- require the named agent in the plan.
            - ``"require_gate"`` -- require the named gate check in the
              plan.
        pattern: The target of the rule. Interpretation depends on
            ``rule_type``:

            - For path rules: an ``fnmatch``-compatible glob pattern.
            - For ``tool_restrict``: comma-separated tool names.
            - For ``require_agent`` / ``require_gate``: the required name.
        severity: ``"block"`` (hard failure) or ``"warn"`` (advisory).
    """

    name: str
    description: str = ""
    scope: str = "all"  # "all", agent name, or agent pattern
    rule_type: str = "path_block"  # "path_block", "path_allow", "tool_restrict", "require_agent", "require_gate"
    pattern: str = ""  # file path pattern, tool name, agent name, or gate type
    severity: str = "block"  # "block", "warn"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "rule_type": self.rule_type,
            "pattern": self.pattern,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PolicyRule:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            scope=data.get("scope", "all"),
            rule_type=data.get("rule_type", "path_block"),
            pattern=data.get("pattern", ""),
            severity=data.get("severity", "block"),
        )


@dataclass
class PolicyViolation:
    """A rule violation detected during policy evaluation.

    Attributes:
        agent_name: The agent whose assignment triggered the violation.
        rule: The ``PolicyRule`` that was violated.
        details: Human-readable explanation of why the violation occurred
            (e.g. which path matched, which tool was restricted).
    """

    agent_name: str
    rule: PolicyRule
    details: str = ""


@dataclass
class PolicySet:
    """A collection of policy rules representing a guardrail preset.

    A policy set groups related rules under a named preset. The five
    standard presets (``standard_dev``, ``data_analysis``, ``infrastructure``,
    ``regulated``, ``security``) are defined in this module. Custom presets
    can be created, serialized to JSON, and loaded from disk.

    Attributes:
        name: Machine-readable preset name used as the filename when
            persisted (e.g. ``"regulated"`` is stored as ``regulated.json``).
        description: Human-readable explanation of the preset's purpose
            and risk level.
        rules: Ordered list of ``PolicyRule`` objects in this preset.
    """

    name: str
    description: str = ""
    rules: list[PolicyRule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, data: dict) -> PolicySet:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            rules=[PolicyRule.from_dict(r) for r in data.get("rules", [])],
        )


# ---------------------------------------------------------------------------
# Standard preset definitions
# ---------------------------------------------------------------------------

def _standard_dev_preset() -> PolicySet:
    """Standard Development (LOW risk)."""
    return PolicySet(
        name="standard_dev",
        description="Standard Development (LOW risk). Default guardrails for everyday work.",
        rules=[
            PolicyRule(
                name="block_env_files",
                description="Block writes to .env files",
                scope="all",
                rule_type="path_block",
                pattern="**/.env",
                severity="block",
            ),
            PolicyRule(
                name="block_secrets_dir",
                description="Block writes to secrets/ directory",
                scope="all",
                rule_type="path_block",
                pattern="**/secrets/**",
                severity="block",
            ),
            PolicyRule(
                name="block_node_modules",
                description="Block writes to node_modules/",
                scope="all",
                rule_type="path_block",
                pattern="**/node_modules/**",
                severity="block",
            ),
            PolicyRule(
                name="reviewers_read_only",
                description="Review agents may not use Write or Bash tools",
                scope="*reviewer*",
                rule_type="tool_restrict",
                pattern="Write,Bash",
                severity="block",
            ),
        ],
    )


def _data_analysis_preset() -> PolicySet:
    """Data Analysis (LOW risk)."""
    return PolicySet(
        name="data_analysis",
        description="Data Analysis / Reporting (LOW risk). Read-only data, write only to output dirs.",
        rules=[
            PolicyRule(
                name="data_agents_write_output_only",
                description="Data agents may write only to output/ directories",
                scope="*data*",
                rule_type="path_allow",
                pattern="**/output/**",
                severity="warn",
            ),
            PolicyRule(
                name="block_source_data_writes",
                description="Block writes to data/ source directories",
                scope="all",
                rule_type="path_block",
                pattern="**/data/**",
                severity="block",
            ),
            PolicyRule(
                name="require_pii_masking",
                description="PII masking required in any output leaving the pipeline",
                scope="all",
                rule_type="require_gate",
                pattern="pii_masking",
                severity="warn",
            ),
        ],
    )


def _infrastructure_preset() -> PolicySet:
    """Infrastructure Changes (HIGH risk)."""
    return PolicySet(
        name="infrastructure",
        description="Infrastructure Changes (HIGH risk). Only devops writes infra files; auditor required.",
        rules=[
            PolicyRule(
                name="only_devops_writes_infra",
                description="Only devops agent may write infrastructure files",
                scope="all",
                rule_type="path_block",
                pattern="**/terraform/**",
                severity="block",
            ),
            PolicyRule(
                name="block_dockerfile_writes",
                description="Block non-devops agents from writing Dockerfiles",
                scope="all",
                rule_type="path_block",
                pattern="**/Dockerfile*",
                severity="block",
            ),
            PolicyRule(
                name="block_cicd_writes",
                description="Block writes to CI/CD config",
                scope="all",
                rule_type="path_block",
                pattern="**/.github/workflows/**",
                severity="block",
            ),
            PolicyRule(
                name="require_auditor",
                description="Auditor pre-execution review required",
                scope="all",
                rule_type="require_agent",
                pattern="auditor",
                severity="block",
            ),
            PolicyRule(
                name="require_rollback_plan",
                description="Rollback plan must be documented before execution",
                scope="all",
                rule_type="require_gate",
                pattern="rollback_plan",
                severity="block",
            ),
        ],
    )


def _regulated_data_preset() -> PolicySet:
    """Regulated Data (HIGH/CRITICAL risk)."""
    return PolicySet(
        name="regulated",
        description="Regulated Data (HIGH/CRITICAL risk). SME required; append-only for historical records.",
        rules=[
            PolicyRule(
                name="require_sme",
                description="Subject-matter-expert must validate before any write to compliance tables",
                scope="all",
                rule_type="require_agent",
                pattern="subject-matter-expert",
                severity="block",
            ),
            PolicyRule(
                name="require_auditor",
                description="Auditor pre-execution and post-execution review required",
                scope="all",
                rule_type="require_agent",
                pattern="auditor",
                severity="block",
            ),
            PolicyRule(
                name="no_bash_on_data",
                description="Implementation agents may not use Bash on regulated data",
                scope="all",
                rule_type="tool_restrict",
                pattern="Bash",
                severity="block",
            ),
            PolicyRule(
                name="append_only_historical",
                description="Historical records are append-only: no updates, no deletes",
                scope="all",
                rule_type="require_gate",
                pattern="append_only",
                severity="block",
            ),
            PolicyRule(
                name="require_audit_trail",
                description="Every write must log who, when, what, why",
                scope="all",
                rule_type="require_gate",
                pattern="audit_trail",
                severity="block",
            ),
        ],
    )


def _security_preset() -> PolicySet:
    """Security-Sensitive (HIGH risk)."""
    return PolicySet(
        name="security",
        description="Security-Sensitive (HIGH risk). Auth code isolated to single agent; auditor required.",
        rules=[
            PolicyRule(
                name="require_auditor",
                description="Auditor pre-execution review required",
                scope="all",
                rule_type="require_agent",
                pattern="auditor",
                severity="block",
            ),
            PolicyRule(
                name="require_security_reviewer",
                description="Security reviewer post-execution review required",
                scope="all",
                rule_type="require_agent",
                pattern="security-reviewer",
                severity="block",
            ),
            PolicyRule(
                name="block_auth_writes_non_implementing",
                description="Only the implementing agent may write auth code",
                scope="all",
                rule_type="path_block",
                pattern="**/auth/**",
                severity="block",
            ),
            PolicyRule(
                name="no_hardcoded_credentials",
                description="No hardcoded credentials — enforce env vars or secret manager",
                scope="all",
                rule_type="require_gate",
                pattern="no_hardcoded_credentials",
                severity="block",
            ),
        ],
    )


_STANDARD_PRESETS: dict[str, PolicySet] = {
    "standard_dev": _standard_dev_preset(),
    "data_analysis": _data_analysis_preset(),
    "infrastructure": _infrastructure_preset(),
    "regulated": _regulated_data_preset(),
    "security": _security_preset(),
}


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """Evaluate agent assignments against policy rules.

    Policy sets are stored as JSON files under .claude/policies/
    (or a custom directory supplied at construction).
    """

    _DEFAULT_POLICIES_DIR = Path(".claude/policies")

    def __init__(self, policies_dir: Path | None = None) -> None:
        self._dir = (policies_dir or self._DEFAULT_POLICIES_DIR).resolve()

    @property
    def policies_dir(self) -> Path:
        return self._dir

    # ── Persistence ────────────────────────────────────────────────────────

    def load_preset(self, name: str) -> PolicySet | None:
        """Load a named policy set from disk.

        First checks the on-disk policies directory, then falls back to the
        built-in standard presets.
        """
        path = self._dir / f"{name}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return PolicySet.from_dict(data)
            except (json.JSONDecodeError, KeyError, OSError):
                return None
        # Fall back to built-in presets
        return _STANDARD_PRESETS.get(name)

    def save_preset(self, policy: PolicySet) -> Path:
        """Write a policy set to disk as JSON. Returns the written path."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{policy.name}.json"
        path.write_text(
            json.dumps(policy.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def list_presets(self) -> list[str]:
        """List available policy set names (on-disk + built-in, deduplicated)."""
        names: set[str] = set(_STANDARD_PRESETS.keys())
        if self._dir.is_dir():
            for p in self._dir.glob("*.json"):
                names.add(p.stem)
        return sorted(names)

    # ── Evaluation ─────────────────────────────────────────────────────────

    @staticmethod
    def _path_matches(path: str, pattern: str) -> bool:
        """Return True if path matches pattern.

        Supports simple fnmatch patterns and ``**``-prefixed glob patterns
        on Python 3.10 (which does not support ``**`` in Path.match or
        fnmatch).  For a pattern like ``**/foo/**``, we try:

        1. Direct fnmatch (handles simple cases like ``*.env``).
        2. If the pattern starts with ``**/``, strip it and try again
           so that ``**/foo.py`` matches ``src/foo.py`` or ``foo.py``.
        3. Try ``*/`` + suffix so that ``**/secrets/**`` matches
           ``secrets/key`` via ``*/secrets/*``.
        """
        if fnmatch.fnmatch(path, pattern):
            return True
        # Normalise: reduce repeated **/ to a single **/ and try again
        stripped = pattern
        while stripped.startswith("**/"):
            stripped = stripped[3:]
        if stripped != pattern:
            if fnmatch.fnmatch(path, stripped):
                return True
            # Also try with a single leading wildcard directory component
            if fnmatch.fnmatch(path, "*/" + stripped):
                return True
        # Trim trailing /**
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            # Remove leading **/
            while prefix.startswith("**/"):
                prefix = prefix[3:]
            if fnmatch.fnmatch(path, prefix + "/*"):
                return True
            if fnmatch.fnmatch(path, "*/" + prefix + "/*"):
                return True
        return False

    def _scope_matches(self, scope: str, agent_name: str) -> bool:
        """Return True if the rule scope applies to the given agent."""
        if scope == "all":
            return True
        return fnmatch.fnmatch(agent_name, scope)

    def evaluate(
        self,
        policy: PolicySet,
        agent_name: str,
        allowed_paths: list[str],
        tools: list[str],
    ) -> list[PolicyViolation]:
        """Check an agent's planned assignment against a policy set.

        Iterates over every rule in the policy set, filters by scope, and
        checks whether the agent's file paths or tools trigger a violation.

        For ``path_block`` rules, each path in ``allowed_paths`` is tested
        against the rule's glob pattern. For ``tool_restrict`` rules, the
        agent's tool list is checked against the comma-separated restricted
        tools. For ``require_agent`` and ``require_gate`` rules, a warning
        violation is always emitted so the caller can verify the
        requirement is satisfied at the plan level.

        Args:
            policy: The ``PolicySet`` to evaluate against.
            agent_name: Name of the agent being checked.
            allowed_paths: File paths the agent is expected to write to.
            tools: Tool names the agent will have access to.

        Returns:
            A list of ``PolicyViolation`` objects. An empty list means the
            agent assignment is fully compliant with the policy set.
        """
        violations: list[PolicyViolation] = []

        for rule in policy.rules:
            if not self._scope_matches(rule.scope, agent_name):
                continue

            if rule.rule_type == "path_block":
                for path in allowed_paths:
                    if self._path_matches(path, rule.pattern):
                        violations.append(
                            PolicyViolation(
                                agent_name=agent_name,
                                rule=rule,
                                details=f"Path '{path}' matches blocked pattern '{rule.pattern}'",
                            )
                        )

            elif rule.rule_type == "tool_restrict":
                restricted = {t.strip() for t in rule.pattern.split(",")}
                for tool in tools:
                    if tool in restricted:
                        violations.append(
                            PolicyViolation(
                                agent_name=agent_name,
                                rule=rule,
                                details=f"Tool '{tool}' is restricted by rule '{rule.name}'",
                            )
                        )

            # require_agent and require_gate are informational/structural checks
            # that the orchestrator must satisfy at the plan level — not something
            # that can be evaluated from a single agent's tools/paths alone.
            # We surface them as warnings so callers are aware.
            elif rule.rule_type in ("require_agent", "require_gate"):
                violations.append(
                    PolicyViolation(
                        agent_name=agent_name,
                        rule=rule,
                        details=(
                            f"Policy requires '{rule.pattern}' ({rule.rule_type}). "
                            "Ensure it is present in the execution plan."
                        ),
                    )
                )

        return violations

    # ── Standard presets ───────────────────────────────────────────────────

    def create_standard_presets(self) -> list[PolicySet]:
        """Return the five standard preset PolicySet objects.

        Matches the presets documented in references/guardrail-presets.md:
        standard_dev, data_analysis, infrastructure, regulated, security.
        """
        return list(_STANDARD_PRESETS.values())
