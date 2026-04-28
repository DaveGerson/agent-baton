"""Stdlib-only Prometheus text exposition helpers.

Implements just enough of the Prometheus text exposition format
(version 0.0.4) to satisfy a Prometheus scraper without depending on
``prometheus_client``.  The tradeoff is intentional: this layer must
add zero new runtime dependencies, since "zero new deps for o11y" is
the whole appeal of velocity-zero observability.

Output format reference:
    https://prometheus.io/docs/instrumenting/exposition_formats/

A :class:`MetricFamily` carries a metric name, type
(``counter`` / ``gauge``), help text, and a list of
:class:`MetricSample` instances.  Each sample is a
``(labels, value)`` pair.

Example::

    family = MetricFamily(
        name="baton_plans_total",
        type="counter",
        help_text="Total plans grouped by status.",
        samples=[
            MetricSample(labels={"status": "complete"}, value=42),
            MetricSample(labels={"status": "running"}, value=3),
        ],
    )
    text = to_text_exposition([family])

The exposition format is plain ``text/plain; version=0.0.4`` and the
caller is responsible for wiring the right ``Content-Type`` header.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

MetricType = Literal["counter", "gauge", "histogram", "summary", "untyped"]


@dataclass(frozen=True)
class MetricSample:
    """One labelled observation belonging to a :class:`MetricFamily`."""

    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0


@dataclass
class MetricFamily:
    """A metric name + type + help text plus its labelled samples."""

    name: str
    type: MetricType
    help_text: str
    samples: list[MetricSample] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Exposition rendering
# ---------------------------------------------------------------------------


def _escape_help(text: str) -> str:
    """Escape backslashes and newlines per exposition spec.

    Per spec, in HELP lines: ``\\`` and ``\\n`` are escape sequences;
    no other escaping is required.
    """
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label_value(value: str) -> str:
    """Escape a label value per the exposition spec.

    Backslash, double-quote and newline are escaped.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _format_labels(labels: dict[str, str]) -> str:
    """Render a label dict as ``{k="v",k2="v2"}`` (sorted for determinism)."""
    if not labels:
        return ""
    parts = [
        f'{key}="{_escape_label_value(str(val))}"'
        for key, val in sorted(labels.items())
    ]
    return "{" + ",".join(parts) + "}"


def _format_value(value: float) -> str:
    """Render a numeric metric value for Prometheus.

    Integers render without a trailing ``.0`` to match what
    ``prometheus_client`` emits; non-integer floats use ``repr`` so
    precision is preserved.
    """
    if isinstance(value, bool):  # bool is a subclass of int — guard explicitly
        return "1" if value else "0"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def to_text_exposition(families: Iterable[MetricFamily]) -> str:
    """Render a sequence of :class:`MetricFamily` to Prometheus 0.0.4 text.

    Each family produces:

    - ``# HELP <name> <help>`` line
    - ``# TYPE <name> <type>`` line
    - one line per sample: ``<name>{labels} <value>``

    Families with zero samples still emit the HELP/TYPE preamble so the
    scraper sees the metric is declared.

    A trailing newline is included per spec.
    """
    lines: list[str] = []
    for fam in families:
        lines.append(f"# HELP {fam.name} {_escape_help(fam.help_text)}")
        lines.append(f"# TYPE {fam.name} {fam.type}")
        for sample in fam.samples:
            label_str = _format_labels(sample.labels)
            value_str = _format_value(sample.value)
            lines.append(f"{fam.name}{label_str} {value_str}")
    return "\n".join(lines) + "\n"
