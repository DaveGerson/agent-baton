"""Declarative project-level configuration loaded from ``baton.yaml``.

Wave 1.2 of the strategic remediation roadmap: lets a project codify
defaults for agent routing, quality gates, and isolation policy so the
planner does the right thing without users repeating CLI flags.

Design constraints (from the roadmap):

* **Optional and additive.** If no ``baton.yaml`` exists, ``load()``
  returns an empty :class:`ProjectConfig` and the planner's behavior is
  identical to prior releases.
* **Best-effort.** Parse failures emit a logger warning and fall back to
  an empty config rather than aborting plan creation.
* **No env-var interpolation, no remote fetch, no schema versioning.**

Typical workflow::

    from agent_baton.core.config import ProjectConfig

    cfg = ProjectConfig.load()                    # walks up from cwd
    agent = cfg.default_agents.get("backend")     # may be None
    gates = cfg.default_gates                     # list of gate-type strings
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "baton.yaml"


@dataclass
class ProjectConfig:
    """Project-level defaults parsed from ``baton.yaml``.

    All fields are optional.  An "empty" config (the result of
    :meth:`load` when no ``baton.yaml`` is found) yields no behavioral
    change in the planner.

    Attributes:
        default_agents: Mapping of *domain key* (``"backend"``,
            ``"frontend"``, ``"test"``, ...) to preferred agent name.
            The planner consults this when a step has no explicit agent.
        default_gates: Gate-type strings (``"pytest"``, ``"lint"``, ...)
            that should be appended to every phase's gate list.
        default_risk_level: Risk level applied to tasks the classifier
            does not categorize.  ``"LOW" | "MEDIUM" | "HIGH"``.
        auto_route_rules: Ordered list of ``{"path_glob": ..., "agent":
            ...}`` rules.  When a step touches files matching the glob
            and has no explicit agent, the rule's agent is used.
        excluded_paths: Globs that the planner adds to every step's
            ``blocked_paths`` so agents never modify them.
        default_isolation: ``""`` (no isolation) or ``"worktree"``.
            Applied to dispatched steps that don't override the value.
        source_path: Filesystem location of the loaded config.  ``None``
            when this is the empty default.
    """

    default_agents: dict[str, str] = field(default_factory=dict)
    default_gates: list[str] = field(default_factory=list)
    default_risk_level: str = ""
    auto_route_rules: list[dict[str, Any]] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    default_isolation: str = ""
    source_path: Path | None = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        """Parse a ``baton.yaml`` file into a :class:`ProjectConfig`.

        Missing fields fall back to the dataclass defaults so partial
        configs are valid.  Top-level non-mapping documents (e.g. a YAML
        list) are rejected with :class:`ValueError`.

        Args:
            path: Filesystem path to the YAML file.

        Returns:
            A new :class:`ProjectConfig` with ``source_path`` set to the
            resolved path of the file.

        Raises:
            FileNotFoundError: When *path* does not exist.
            ValueError: When the YAML root is not a mapping or a typed
                field has the wrong shape (e.g. ``default_agents`` is a
                list instead of a mapping).
        """
        import yaml

        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"{path}: top-level YAML must be a mapping, got "
                f"{type(data).__name__}"
            )

        default_agents = data.get("default_agents", {}) or {}
        if not isinstance(default_agents, dict):
            raise ValueError(
                f"{path}: 'default_agents' must be a mapping, got "
                f"{type(default_agents).__name__}"
            )

        default_gates = data.get("default_gates", []) or []
        if not isinstance(default_gates, list):
            raise ValueError(
                f"{path}: 'default_gates' must be a list, got "
                f"{type(default_gates).__name__}"
            )

        auto_route_rules = data.get("auto_route_rules", []) or []
        if not isinstance(auto_route_rules, list):
            raise ValueError(
                f"{path}: 'auto_route_rules' must be a list, got "
                f"{type(auto_route_rules).__name__}"
            )
        for rule in auto_route_rules:
            if not isinstance(rule, dict):
                raise ValueError(
                    f"{path}: each auto_route_rule must be a mapping"
                )

        excluded_paths = data.get("excluded_paths", []) or []
        if not isinstance(excluded_paths, list):
            raise ValueError(
                f"{path}: 'excluded_paths' must be a list, got "
                f"{type(excluded_paths).__name__}"
            )

        return cls(
            default_agents={str(k): str(v) for k, v in default_agents.items()},
            default_gates=[str(g) for g in default_gates],
            default_risk_level=str(data.get("default_risk_level", "") or ""),
            auto_route_rules=[dict(r) for r in auto_route_rules],
            excluded_paths=[str(p) for p in excluded_paths],
            default_isolation=str(data.get("default_isolation", "") or ""),
            source_path=path.resolve(),
        )

    @classmethod
    def load(cls, start_dir: Path | None = None) -> ProjectConfig:
        """Discover and load the nearest ``baton.yaml``.

        Walks up from *start_dir* (defaulting to the current working
        directory) toward the filesystem root, returning the first
        config found.  When none is found, returns an empty config so
        callers always get a usable object.

        All errors (missing file, malformed YAML, invalid types) are
        swallowed and logged at WARNING; callers receive an empty
        :class:`ProjectConfig` in that case.  This keeps the planner
        velocity-positive — a broken project config never blocks
        ``baton plan``.

        Args:
            start_dir: Where to start the upward search.  Defaults to
                :func:`Path.cwd`.

        Returns:
            The discovered :class:`ProjectConfig`, or an empty one when
            no ``baton.yaml`` was found or parsing failed.
        """
        cwd = (start_dir or Path.cwd()).resolve()
        # Walk up from cwd toward filesystem root.
        candidates = [cwd, *cwd.parents]
        for d in candidates:
            candidate = d / CONFIG_FILENAME
            if candidate.is_file():
                try:
                    return cls.from_yaml(candidate)
                except Exception as exc:  # pragma: no cover - exercised via tests
                    logger.warning(
                        "Failed to load %s: %s — falling back to empty config",
                        candidate, exc,
                    )
                    return cls()
        return cls()

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def merge(self, other: ProjectConfig) -> ProjectConfig:
        """Return a new config combining *self* with *other* (other wins).

        Used for layered configs where, for example, a workspace-level
        config overrides shared project defaults.  Merge semantics:

        * **Mappings** (``default_agents``): ``other`` keys override
          ``self`` keys; missing keys in ``other`` are inherited.
        * **Lists** (``default_gates``, ``excluded_paths``): unique-
          preserving concatenation (``self`` first, then new entries
          from ``other``).
        * **Auto-route rules**: concatenated in order; ``other`` rules
          appended after ``self`` so workspace-level rules can override
          shared rules (planner consults the list in order, but uniques
          are not enforced — order is the contract).
        * **Scalars** (``default_risk_level``, ``default_isolation``):
          ``other`` wins when non-empty; otherwise ``self`` is kept.
        * **source_path**: takes ``other.source_path`` when set, else
          ``self.source_path``.

        Args:
            other: A config whose values take precedence.

        Returns:
            A new :class:`ProjectConfig`; neither input is mutated.
        """
        merged_agents = dict(self.default_agents)
        merged_agents.update(other.default_agents)

        def _unique_extend(a: list[str], b: list[str]) -> list[str]:
            seen = set(a)
            out = list(a)
            for item in b:
                if item not in seen:
                    out.append(item)
                    seen.add(item)
            return out

        return ProjectConfig(
            default_agents=merged_agents,
            default_gates=_unique_extend(self.default_gates, other.default_gates),
            default_risk_level=other.default_risk_level or self.default_risk_level,
            auto_route_rules=[*self.auto_route_rules, *other.auto_route_rules],
            excluded_paths=_unique_extend(
                self.excluded_paths, other.excluded_paths,
            ),
            default_isolation=other.default_isolation or self.default_isolation,
            source_path=other.source_path or self.source_path,
        )

    # ------------------------------------------------------------------
    # Helpers (used by the planner)
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return True when no fields carry any user-provided value."""
        return not any(
            (
                self.default_agents,
                self.default_gates,
                self.default_risk_level,
                self.auto_route_rules,
                self.excluded_paths,
                self.default_isolation,
            )
        )

    def route_agent_for_paths(self, paths: list[str]) -> str | None:
        """Return the first ``auto_route_rules`` agent whose glob matches.

        Iterates *paths* against each rule's ``path_glob`` (using
        :func:`fnmatch.fnmatch`).  The first rule whose glob matches any
        of the supplied paths wins, mirroring ordered-rule semantics.

        Args:
            paths: Step file paths to match against.

        Returns:
            The matching rule's ``agent`` value, or ``None`` when no
            rule matches or no rule exposes an ``agent`` key.
        """
        for rule in self.auto_route_rules:
            glob = rule.get("path_glob")
            agent = rule.get("agent")
            if not glob or not agent:
                continue
            for p in paths:
                if fnmatch.fnmatch(p, glob):
                    return str(agent)
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the config to a JSON-friendly dict (for ``baton config show``)."""
        return {
            "default_agents": dict(self.default_agents),
            "default_gates": list(self.default_gates),
            "default_risk_level": self.default_risk_level,
            "auto_route_rules": [dict(r) for r in self.auto_route_rules],
            "excluded_paths": list(self.excluded_paths),
            "default_isolation": self.default_isolation,
            "source_path": str(self.source_path) if self.source_path else None,
        }
