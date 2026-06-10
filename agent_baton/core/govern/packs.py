"""Assurance Packs — org-authored domain governance units.

An Assurance Pack is a directory under ``.claude/packs/<name>/`` that bundles
a PolicySet, classification signals, a rubric, gate commands, and evidence
requirements into a single distributable unit.  Organisations author packs for
each regulated domain they operate in (HIPAA, OWASP, SOC2, …) and baton ships
the format: schema, loader, validator, and CLI.

Pack directory layout (each file is optional except where marked required*)::

    .claude/packs/<name>/
    ├── pack.json       *  Manifest: name, version, description (required keys)
    ├── policy.json     *  PolicySet JSON; name must be "pack:<dirname>"
    ├── signals.json    *  Classification signals; preset_name must be "pack:<name>"
    ├── rubric.md       *  Review checklist; must have ≥1 ## heading + ≥1 - [ ] item
    ├── gates.json         Gate definitions; each entry requires id/description/command
    ├── evidence.json      Evidence requirements; each artifact requires id/description
    └── knowledge/         Optional Markdown reference docs (Layer-2 convention)

Public API
----------
- ``load_packs(project_root)`` — scan ``.claude/packs/``, return valid packs.
- ``validate_pack(path)`` — return a list of ``PackError`` objects (empty = valid).
- ``register_pack_policies(packs)`` — register loaded packs into the in-process
  policy registry so ``load_preset("pack:name")`` resolves correctly.
- ``get_pack_policy(name)`` — retrieve a registered ``PolicySet`` by preset name.
- ``make_classifier_for_packs(packs)`` — build a ``DataClassifier`` that merges
  pack signals with base signals.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid signal categories (from spec)
# ---------------------------------------------------------------------------

_VALID_SIGNAL_CATEGORIES: frozenset[str] = frozenset(
    {"regulated", "pii", "security", "infrastructure", "database"}
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PackError:
    """A validation error found inside a pack.

    Attributes:
        pack_name: The directory name of the pack (or ``"<unknown>"``).
        file: The file within the pack where the error was found.
        message: Human-readable description of the error.
    """

    pack_name: str
    file: str
    message: str

    def __str__(self) -> str:
        return f"[ERROR] {self.pack_name}/{self.file}: {self.message}"


@dataclass
class PackManifest:
    """Contents of ``pack.json``.

    Attributes:
        name: Machine-readable pack name (must match directory name).
        version: Semantic version string (e.g. ``"1.0.0"``).
        description: Human-readable summary of the pack's purpose.
        domain: Optional domain tag (e.g. ``"healthcare"``).
        risk_level: Default risk level (``"HIGH"`` if unset).
        author: Optional author identifier.
        baton_min_version: Optional minimum baton version required.
    """

    name: str
    version: str
    description: str
    domain: str = ""
    risk_level: str = "HIGH"
    author: str = ""
    baton_min_version: str = ""


@dataclass
class Pack:
    """A fully loaded and validated Assurance Pack.

    Attributes:
        manifest: Parsed ``pack.json`` content.
        path: Absolute path to the pack directory.
        policy_set: Parsed ``PolicySet`` from ``policy.json``.
        signals: Parsed ``signals.json`` dict (raw).
        gates: Parsed ``gates.json`` dict (raw), or ``None`` if absent.
        evidence: Parsed ``evidence.json`` dict (raw), or ``None`` if absent.
    """

    manifest: PackManifest
    path: Path
    policy_set: Any  # PolicySet — typed as Any to avoid circular imports
    signals: dict
    gates: dict | None = None
    evidence: dict | None = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def preset_name(self) -> str:
        return f"pack:{self.name}"


# ---------------------------------------------------------------------------
# In-process policy registry
# ---------------------------------------------------------------------------

_PACK_POLICY_REGISTRY: dict[str, Any] = {}  # preset_name → PolicySet


def register_pack_policies(packs: list[Pack]) -> None:
    """Register pack PolicySets so ``load_preset("pack:name")`` resolves them.

    Call this once at the entry point (plan_cmd, classify handler) after
    calling ``load_packs()``.  Idempotent — re-registering the same pack
    simply overwrites the previous entry.

    Args:
        packs: List of validated :class:`Pack` objects from :func:`load_packs`.
    """
    for pack in packs:
        _PACK_POLICY_REGISTRY[pack.preset_name] = pack.policy_set
    if packs:
        logger.debug("Registered %d pack policy sets", len(packs))


def get_pack_policy(name: str) -> Any | None:
    """Return the registered PolicySet for *name* (e.g. ``"pack:phi-hipaa"``).

    Returns:
        The ``PolicySet`` if found; ``None`` if not registered.
    """
    return _PACK_POLICY_REGISTRY.get(name)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_pack(path: Path) -> list[PackError]:
    """Validate a pack directory against all 7 spec checks.

    Does not raise.  Returns a list of ``PackError`` objects.  An empty list
    means the pack is fully valid.

    Checks performed:
        1. Required files (``pack.json``, ``policy.json``, ``signals.json``,
           ``rubric.md``) must exist.
        2. ``pack.json`` must parse and have ``name``, ``version``,
           ``description``; ``name`` must match the directory name.
        3. ``policy.json`` must parse via ``PolicySet.from_dict`` and
           ``name`` must be ``"pack:<dirname>"``.
        4. ``signals.json`` categories must be a subset of
           ``{regulated, pii, security, infrastructure, database}``;
           ``path_patterns`` must be a list; ``preset_name`` must be present.
        5. ``rubric.md`` must contain ≥1 ``## `` heading and ≥1 ``- [ ]``
           checkbox.
        6. ``gates.json`` (if present) entries must have ``id``, ``description``,
           and ``command``.
        7. ``evidence.json`` (if present) ``required_artifacts`` entries must
           have ``id`` and ``description``.

    Args:
        path: Absolute path to the pack directory (the ``<name>/`` directory).

    Returns:
        List of :class:`PackError` instances.  Empty → valid.
    """
    errors: list[PackError] = []
    dirname = path.name

    # ── 1. Required files ─────────────────────────────────────────────────
    required_files = ["pack.json", "policy.json", "signals.json", "rubric.md"]
    for fname in required_files:
        if not (path / fname).exists():
            errors.append(PackError(dirname, fname, f"required file '{fname}' is missing"))

    if errors:
        # Cannot continue without the required files.
        return errors

    # ── 2. pack.json — keys + name==dirname ───────────────────────────────
    pack_json_path = path / "pack.json"
    pack_data: dict = {}
    try:
        pack_data = json.loads(pack_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(PackError(dirname, "pack.json", f"JSON parse error: {exc}"))
        return errors  # cannot proceed without manifest

    for req_key in ("name", "version", "description"):
        if not pack_data.get(req_key):
            errors.append(
                PackError(dirname, "pack.json", f"required key '{req_key}' is missing or empty")
            )
    if pack_data.get("name") and pack_data["name"] != dirname:
        errors.append(
            PackError(
                dirname,
                "pack.json",
                f"name '{pack_data['name']}' does not match directory name '{dirname}'",
            )
        )

    # ── 3. policy.json — PolicySet parse + name=="pack:<dir>" ─────────────
    policy_path = path / "policy.json"
    try:
        from agent_baton.core.govern.policy import PolicySet

        policy_data = json.loads(policy_path.read_text(encoding="utf-8"))
        ps = PolicySet.from_dict(policy_data)
        expected_name = f"pack:{dirname}"
        if ps.name != expected_name:
            errors.append(
                PackError(
                    dirname,
                    "policy.json",
                    f"PolicySet name '{ps.name}' must be '{expected_name}'",
                )
            )
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(PackError(dirname, "policy.json", f"JSON parse error: {exc}"))
    except Exception as exc:
        errors.append(PackError(dirname, "policy.json", f"PolicySet parse error: {exc}"))

    # ── 4. signals.json — categories, path_patterns, preset_name ──────────
    signals_path = path / "signals.json"
    try:
        sig_data = json.loads(signals_path.read_text(encoding="utf-8"))
        keywords = sig_data.get("keywords", {})
        if isinstance(keywords, dict):
            for cat in keywords:
                if cat not in _VALID_SIGNAL_CATEGORIES:
                    errors.append(
                        PackError(
                            dirname,
                            "signals.json",
                            f"unknown keyword category '{cat}'; valid categories: "
                            + ", ".join(sorted(_VALID_SIGNAL_CATEGORIES)),
                        )
                    )
        path_patterns = sig_data.get("path_patterns")
        if path_patterns is not None and not isinstance(path_patterns, list):
            errors.append(
                PackError(dirname, "signals.json", "'path_patterns' must be a list")
            )
        if not sig_data.get("preset_name"):
            errors.append(
                PackError(dirname, "signals.json", "'preset_name' is missing or empty")
            )
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(PackError(dirname, "signals.json", f"JSON parse error: {exc}"))

    # ── 5. rubric.md — ≥1 ## heading + ≥1 - [ ] checkbox ─────────────────
    rubric_path = path / "rubric.md"
    try:
        rubric_text = rubric_path.read_text(encoding="utf-8")
        heading_lines = [l for l in rubric_text.splitlines() if l.startswith("## ")]
        checkbox_lines = [l for l in rubric_text.splitlines() if "- [ ]" in l]
        if not heading_lines:
            errors.append(
                PackError(dirname, "rubric.md", "must contain at least one '## ' heading")
            )
        if not checkbox_lines:
            errors.append(
                PackError(dirname, "rubric.md", "must contain at least one '- [ ]' checkbox")
            )
    except OSError as exc:
        errors.append(PackError(dirname, "rubric.md", f"read error: {exc}"))

    # ── 6. gates.json — each entry needs id, description, command ─────────
    gates_path = path / "gates.json"
    if gates_path.exists():
        try:
            gates_data = json.loads(gates_path.read_text(encoding="utf-8"))
            for i, gate in enumerate(gates_data.get("gates", [])):
                for key in ("id", "description", "command"):
                    if not gate.get(key):
                        errors.append(
                            PackError(
                                dirname,
                                "gates.json",
                                f"gate[{i}] missing required key '{key}'",
                            )
                        )
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(PackError(dirname, "gates.json", f"JSON parse error: {exc}"))

    # ── 7. evidence.json — required_artifacts need id + description ────────
    evidence_path = path / "evidence.json"
    if evidence_path.exists():
        try:
            ev_data = json.loads(evidence_path.read_text(encoding="utf-8"))
            for i, artifact in enumerate(ev_data.get("required_artifacts", [])):
                for key in ("id", "description"):
                    if not artifact.get(key):
                        errors.append(
                            PackError(
                                dirname,
                                "evidence.json",
                                f"required_artifacts[{i}] missing required key '{key}'",
                            )
                        )
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(PackError(dirname, "evidence.json", f"JSON parse error: {exc}"))

    return errors


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_packs(project_root: Path) -> list[Pack]:
    """Scan ``.claude/packs/`` and return all valid packs.

    Invalid packs are skipped with a warning log; they do not cause an error.
    Returns an empty list when the packs directory does not exist or is empty.

    Args:
        project_root: Root directory of the project (contains ``.claude/``).

    Returns:
        List of :class:`Pack` objects, one per valid pack directory.
    """
    packs_dir = project_root / ".claude" / "packs"
    if not packs_dir.is_dir():
        return []

    from agent_baton.core.govern.policy import PolicySet

    packs: list[Pack] = []
    for entry in sorted(packs_dir.iterdir()):
        if not entry.is_dir():
            continue
        errors = validate_pack(entry)
        if errors:
            for err in errors:
                logger.warning("Skipping invalid pack '%s': %s", entry.name, err.message)
            continue
        try:
            manifest_data = json.loads((entry / "pack.json").read_text(encoding="utf-8"))
            manifest = PackManifest(
                name=manifest_data["name"],
                version=manifest_data["version"],
                description=manifest_data["description"],
                domain=manifest_data.get("domain", ""),
                risk_level=manifest_data.get("risk_level", "HIGH"),
                author=manifest_data.get("author", ""),
                baton_min_version=manifest_data.get("baton_min_version", ""),
            )
            policy_data = json.loads((entry / "policy.json").read_text(encoding="utf-8"))
            policy_set = PolicySet.from_dict(policy_data)
            sig_data = json.loads((entry / "signals.json").read_text(encoding="utf-8"))
            gates_data: dict | None = None
            gates_path = entry / "gates.json"
            if gates_path.exists():
                gates_data = json.loads(gates_path.read_text(encoding="utf-8"))
            evidence_data: dict | None = None
            ev_path = entry / "evidence.json"
            if ev_path.exists():
                evidence_data = json.loads(ev_path.read_text(encoding="utf-8"))

            packs.append(
                Pack(
                    manifest=manifest,
                    path=entry,
                    policy_set=policy_set,
                    signals=sig_data,
                    gates=gates_data,
                    evidence=evidence_data,
                )
            )
        except Exception as exc:
            logger.warning("Failed to load pack '%s': %s", entry.name, exc)

    return packs


# ---------------------------------------------------------------------------
# Classifier factory
# ---------------------------------------------------------------------------


def make_classifier_for_packs(packs: list[Pack]) -> "DataClassifier":
    """Build a :class:`~agent_baton.core.govern.classifier.DataClassifier`
    that merges base signals with pack-specific signals.

    When *packs* is empty, returns a plain ``DataClassifier()`` with default
    behavior (backward-compatible).

    The returned classifier:
    * Processes all built-in base signals first (regulated, PII, security,
      infrastructure, database).
    * For each pack, checks the pack's keyword signals (merged into the
      appropriate category) and path patterns.
    * Pack path-pattern matches set ``guardrail_preset`` to
      ``"pack:<name>"`` and risk to the pack's declared risk level.
    * When multiple packs match, the highest risk wins; ties are broken
      alphabetically by pack name.

    Args:
        packs: List of :class:`Pack` objects from :func:`load_packs`.

    Returns:
        A configured :class:`~agent_baton.core.govern.classifier.DataClassifier`
        instance.
    """
    from agent_baton.core.govern.classifier import DataClassifier

    if not packs:
        return DataClassifier()

    extra_signals: dict[str, list[str]] = {}
    extra_path_patterns: list[tuple[str, str, str]] = []  # (pattern, preset_name, risk_level)
    pack_preset_overrides: dict[str, str] = {}

    for pack in packs:
        sig = pack.signals
        keywords = sig.get("keywords", {})
        for cat, kw_list in keywords.items():
            if cat not in _VALID_SIGNAL_CATEGORIES:
                continue
            if cat not in extra_signals:
                extra_signals[cat] = []
            extra_signals[cat].extend(kw_list)
        path_patterns = sig.get("path_patterns", [])
        risk_level = pack.manifest.risk_level or "HIGH"
        for pp in path_patterns:
            extra_path_patterns.append((pp, pack.preset_name, risk_level))
        pack_preset_overrides[pack.preset_name] = pack.manifest.risk_level

    return DataClassifier(
        extra_signals=extra_signals,
        extra_path_patterns=extra_path_patterns,
        pack_preset_overrides=pack_preset_overrides,
    )
