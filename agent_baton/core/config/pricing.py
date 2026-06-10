"""Single source of truth for Anthropic model pricing.

Provides a frozen :class:`ModelPrice` dataclass and a :data:`PRICING` dict
covering the four model families used by agent-baton.  Downstream modules
should import from here rather than hardcoding their own price constants.

**Override file** (optional, project-local):
  ``.claude/pricing.json`` — shape::

      {
          "opus": {"input_per_mtok": 5.0, "output_per_mtok": 25.0},
          "haiku": {"input_per_mtok": 1.0, "output_per_mtok": 5.0}
      }

  Tolerate missing / invalid file with a logged warning; never raise.

**Pricing** (USD per million tokens, current as of June 2026):

    haiku:   $1.00 / $5.00
    sonnet:  $3.00 / $15.00
    opus:    $5.00 / $25.00
    fable:  $10.00 / $50.00
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token pricing for one model family.

    Attributes:
        input_per_mtok: USD per million *input* tokens.
        output_per_mtok: USD per million *output* tokens.
    """

    input_per_mtok: float
    output_per_mtok: float


# ---------------------------------------------------------------------------
# Canonical pricing table
# ---------------------------------------------------------------------------

#: Default pricing (USD / 1M tokens).  Keys are canonical family names.
PRICING: dict[str, ModelPrice] = {
    "haiku":  ModelPrice(input_per_mtok=1.00,  output_per_mtok=5.00),
    "sonnet": ModelPrice(input_per_mtok=3.00,  output_per_mtok=15.00),
    "opus":   ModelPrice(input_per_mtok=5.00,  output_per_mtok=25.00),
    "fable":  ModelPrice(input_per_mtok=10.00, output_per_mtok=50.00),
}

# ---------------------------------------------------------------------------
# Family normalisation
# ---------------------------------------------------------------------------

#: Explicit prefix → canonical family mapping.
_FAMILY_PREFIX_MAP: dict[str, str] = {
    # Full vendor IDs take priority over bare names (longer prefix wins).
    "claude-fable":  "fable",
    "claude-opus":   "opus",
    "claude-sonnet": "sonnet",
    "claude-haiku":  "haiku",
    # Bare family names / versioned variants.
    "fable":  "fable",
    "opus":   "opus",
    "sonnet": "sonnet",
    "haiku":  "haiku",
}

_DEFAULT_FAMILY = "sonnet"


def normalise_family(model: str) -> str:
    """Map an arbitrary model string to a canonical family key.

    Resolution rules (mirrors ``cost_estimator.normalise_model``; extended
    with the ``fable`` family):

    1. **Explicit prefix match** (longest prefix wins).  Catches both bare
       names (``"opus"``, ``"fable-5"``) and full vendor IDs
       (``"claude-opus-4-8"``, ``"claude-fable-5"``).
    2. **Token suffix match** for legacy ``claude-N-M-<family>`` IDs
       (e.g. ``"claude-3-5-sonnet"`` → ``"sonnet"``).  Suffix only fires
       when the suffix token is a known family name, so composite IDs like
       ``"opus-via-haiku-router"`` still resolve to the leading ``"opus"``
       family via step 1.
    3. **Fallback** → ``"sonnet"`` with a logged warning.

    Args:
        model: Raw model identifier (e.g. ``"claude-opus-4-8"``, ``"haiku"``).

    Returns:
        One of ``{"haiku", "sonnet", "opus", "fable"}``.

    Examples::

        normalise_family("claude-opus-4-8")   # → "opus"
        normalise_family("claude-haiku-4-5")  # → "haiku"
        normalise_family("fable")             # → "fable"
        normalise_family("claude-fable-5")    # → "fable"
        normalise_family("claude-3-5-sonnet") # → "sonnet" (suffix rule)
        normalise_family("")                  # → "sonnet" (fallback)
    """
    if not model:
        return _DEFAULT_FAMILY
    lower = model.lower()

    # 1. Prefix match — longest first to avoid "haiku" matching
    #    "claude-haiku-…" only at the short key.
    for prefix in sorted(_FAMILY_PREFIX_MAP, key=len, reverse=True):
        if lower.startswith(prefix):
            return _FAMILY_PREFIX_MAP[prefix]

    # 2. Suffix-token lookup for legacy ``claude-N-M-<family>`` IDs.
    tokens = lower.replace("_", "-").split("-")
    if tokens and tokens[-1] in PRICING:
        return tokens[-1]

    # 3. Fallback with warning.
    _log.warning(
        "normalise_family: unrecognised model %r; defaulting to %r pricing",
        model,
        _DEFAULT_FAMILY,
    )
    return _DEFAULT_FAMILY


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_pricing() -> dict[str, ModelPrice]:
    """Return :data:`PRICING` merged with any project-local override.

    Looks for ``.claude/pricing.json`` relative to ``cwd`` (walking up is
    intentionally not performed — the override is project-local, not
    user-global).  On any read / parse error the base :data:`PRICING` is
    returned unmodified and a warning is logged.

    Override format::

        {
            "opus": {"input_per_mtok": 5.0, "output_per_mtok": 25.0}
        }

    Returns:
        A ``dict[str, ModelPrice]`` guaranteed to contain all four canonical
        family keys.  User overrides shadow only the families they specify.
    """
    result = dict(PRICING)  # shallow copy; ModelPrice is frozen, no deep copy needed

    env_override = os.environ.get("BATON_PRICING_OVERRIDE_PATH", "").strip()
    if env_override:
        override_path = Path(env_override)
        if not override_path.is_absolute():
            override_path = Path.cwd() / override_path
    else:
        override_path = Path.cwd() / ".claude" / "pricing.json"

    if not override_path.exists():
        return result

    try:
        raw = json.loads(override_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
        for family, entry in raw.items():
            if not isinstance(entry, dict):
                _log.warning(
                    "pricing override: entry for %r is not a dict — skipping",
                    family,
                )
                continue
            try:
                result[family] = ModelPrice(
                    input_per_mtok=float(entry["input_per_mtok"]),
                    output_per_mtok=float(entry["output_per_mtok"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                _log.warning(
                    "pricing override: invalid entry for %r (%s) — skipping",
                    family,
                    exc,
                )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "pricing override: failed to load %s (%s) — using defaults",
            override_path,
            exc,
        )

    return result


def blended(family: str) -> float:
    """Return the 75/25 input/output blended USD rate per million tokens.

    Formula: ``0.75 * input_per_mtok + 0.25 * output_per_mtok``.

    This matches the historical formula used in ``cost_estimator.MODEL_PRICING``
    (e.g. sonnet: 0.75*3 + 0.25*15 = 6.0).

    Args:
        family: Canonical family key (``"haiku"``, ``"sonnet"``, ``"opus"``,
                ``"fable"``).  Unknown families default to ``"sonnet"``.

    Returns:
        Blended USD per million tokens.

    Examples::

        blended("haiku")   # → 2.0   (0.75*1 + 0.25*5)
        blended("sonnet")  # → 6.0   (0.75*3 + 0.25*15)
        blended("opus")    # → 10.0  (0.75*5 + 0.25*25)
        blended("fable")   # → 20.0  (0.75*10 + 0.25*50)
    """
    pricing = get_pricing()
    price = pricing.get(family, pricing.get(_DEFAULT_FAMILY, PRICING[_DEFAULT_FAMILY]))
    return 0.75 * price.input_per_mtok + 0.25 * price.output_per_mtok


__all__ = [
    "ModelPrice",
    "PRICING",
    "get_pricing",
    "blended",
    "normalise_family",
]
