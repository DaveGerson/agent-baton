"""Incident response management -- phased workflows driven by severity templates.

Provides pre-built incident response templates for four severity levels:

* **P1 (Critical)** -- all-hands response with triage, investigation, fix,
  verification, and post-incident report phases. Involves orchestrator,
  auditor, devops, and backend agents.
* **P2 (Significant)** -- immediate investigation with investigation, fix,
  verification, and report phases.
* **P3 (Minor)** -- scheduled investigation with investigation, fix, and
  verification phases.
* **P4 (Cosmetic)** -- backlog fix with fix and verification phases.

Each phase specifies which agents are involved and what gate check must
pass before advancing. Incident documents are persisted as markdown files
under ``.claude/team-context/incidents/``.

**Status: Experimental** -- built and tested but not yet validated with real
usage data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IncidentPhase:
    """A single phase in an incident response workflow.

    Attributes:
        name: Phase name (e.g. ``"Triage"``, ``"Investigate"``, ``"Fix"``).
        description: What should happen during this phase.
        agents: List of agent names that participate in this phase.
        gate: Name of the gate check that must pass before the phase
            is considered complete (e.g. ``"root_cause_identified"``).
    """

    name: str
    description: str
    agents: list[str] = field(default_factory=list)
    gate: str = ""


@dataclass
class IncidentTemplate:
    """Pre-built phased template for incident response.

    Each template defines a sequence of phases appropriate for its
    severity level. Templates are immutable once created and serve as
    blueprints for ``IncidentManager.create_incident()``.

    Attributes:
        name: Human-readable template name (e.g. ``"Critical Production
            Outage"``).
        description: Explanation of when this template applies.
        severity: Priority level: ``"P1"`` (critical) through ``"P4"``
            (cosmetic/low-priority).
        phases: Ordered list of ``IncidentPhase`` objects defining the
            response workflow.
    """

    name: str
    description: str
    severity: str = "P2"  # P1, P2, P3, P4
    phases: list[IncidentPhase] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"# Incident Template: {self.name}",
            "",
            f"**Severity:** {self.severity}",
            f"**Description:** {self.description}",
            "",
            "## Phases",
            "",
        ]
        for i, phase in enumerate(self.phases, start=1):
            lines.append(f"### Phase {i}: {phase.name}")
            lines.append("")
            lines.append(phase.description)
            if phase.agents:
                lines.append(f"**Agents:** {', '.join(phase.agents)}")
            if phase.gate:
                lines.append(f"**Gate:** {phase.gate}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Baked-in standard templates
# ---------------------------------------------------------------------------

def _p1_template() -> IncidentTemplate:
    """P1: Critical production outage — all hands."""
    return IncidentTemplate(
        name="Critical Production Outage",
        description="P1 — all-hands response for a critical production outage.",
        severity="P1",
        phases=[
            IncidentPhase(
                name="Triage",
                description="Immediate assessment of scope, blast radius, and customer impact.",
                agents=["orchestrator", "auditor", "devops-engineer"],
                gate="triage_complete",
            ),
            IncidentPhase(
                name="Investigate",
                description="Root cause analysis. Collect logs, traces, and metrics.",
                agents=["backend-engineer", "data-engineer", "devops-engineer"],
                gate="root_cause_identified",
            ),
            IncidentPhase(
                name="Fix",
                description="Implement the targeted fix. Deploy to production with approval.",
                agents=["backend-engineer", "devops-engineer"],
                gate="fix_deployed",
            ),
            IncidentPhase(
                name="Verify",
                description="Confirm the fix resolves the issue. Monitor key metrics.",
                agents=["auditor", "devops-engineer"],
                gate="verification_passed",
            ),
            IncidentPhase(
                name="Post-Incident Report",
                description="Document timeline, root cause, fix, and follow-up actions.",
                agents=["orchestrator", "auditor"],
                gate="report_approved",
            ),
        ],
    )


def _p2_template() -> IncidentTemplate:
    """P2: Significant issue — immediate investigation."""
    return IncidentTemplate(
        name="Significant Issue",
        description="P2 — significant issue requiring immediate investigation.",
        severity="P2",
        phases=[
            IncidentPhase(
                name="Investigate",
                description="Root cause analysis. Collect relevant logs and metrics.",
                agents=["backend-engineer", "devops-engineer"],
                gate="root_cause_identified",
            ),
            IncidentPhase(
                name="Fix",
                description="Implement and deploy the fix.",
                agents=["backend-engineer", "devops-engineer"],
                gate="fix_deployed",
            ),
            IncidentPhase(
                name="Verify",
                description="Confirm fix resolves the issue and no regressions introduced.",
                agents=["auditor", "devops-engineer"],
                gate="verification_passed",
            ),
            IncidentPhase(
                name="Report",
                description="Document the incident, fix, and any follow-up actions.",
                agents=["orchestrator"],
                gate="report_filed",
            ),
        ],
    )


def _p3_template() -> IncidentTemplate:
    """P3: Minor issue — scheduled investigation."""
    return IncidentTemplate(
        name="Minor Issue",
        description="P3 — minor issue for scheduled investigation.",
        severity="P3",
        phases=[
            IncidentPhase(
                name="Investigate",
                description="Identify root cause during the next scheduled review window.",
                agents=["backend-engineer"],
                gate="root_cause_identified",
            ),
            IncidentPhase(
                name="Fix",
                description="Implement the fix in the normal development flow.",
                agents=["backend-engineer"],
                gate="fix_deployed",
            ),
            IncidentPhase(
                name="Verify",
                description="Confirm the fix in staging or production.",
                agents=["auditor"],
                gate="verification_passed",
            ),
        ],
    )


def _p4_template() -> IncidentTemplate:
    """P4: Cosmetic / low-priority — backlog."""
    return IncidentTemplate(
        name="Cosmetic / Low-Priority",
        description="P4 — cosmetic or low-priority issue tracked in the backlog.",
        severity="P4",
        phases=[
            IncidentPhase(
                name="Fix",
                description="Implement the fix when capacity allows.",
                agents=["backend-engineer"],
                gate="fix_deployed",
            ),
            IncidentPhase(
                name="Verify",
                description="Light verification that the change is correct.",
                agents=["auditor"],
                gate="verification_passed",
            ),
        ],
    )


_TEMPLATES: dict[str, IncidentTemplate] = {
    "P1": _p1_template(),
    "P2": _p2_template(),
    "P3": _p3_template(),
    "P4": _p4_template(),
}


# ---------------------------------------------------------------------------
# IncidentManager
# ---------------------------------------------------------------------------

class IncidentManager:
    """Manage incident response workflows using severity-based templates.

    Creates, lists, and loads incident response documents. Each incident
    is generated from a pre-built template matching its severity level
    (P1--P4) and persisted as a markdown file under
    ``.claude/team-context/incidents/`` (configurable).
    """

    _DEFAULT_INCIDENTS_DIR = Path(".claude/team-context/incidents")

    def __init__(self, incidents_dir: Path | None = None) -> None:
        self._dir = (incidents_dir or self._DEFAULT_INCIDENTS_DIR).resolve()

    @property
    def incidents_dir(self) -> Path:
        return self._dir

    # ── Templates ──────────────────────────────────────────────────────────

    def get_template(self, severity: str = "P2") -> IncidentTemplate:
        """Return the incident response template for the given severity level.

        Valid severity levels: P1, P2, P3, P4.
        Defaults to P2 for unknown severity values.
        """
        return _TEMPLATES.get(severity.upper(), _TEMPLATES["P2"])

    # ── Incident lifecycle ─────────────────────────────────────────────────

    def create_incident(
        self, incident_id: str, severity: str, description: str
    ) -> Path:
        """Create an incident response document from the matching template.

        Selects the template for the given severity, renders it as markdown
        with the incident metadata, and writes the file to disk.

        Args:
            incident_id: Unique identifier for the incident (used in the
                filename with unsafe characters replaced by hyphens).
            severity: Priority level (``"P1"`` through ``"P4"``).
                Unknown values default to the P2 template.
            description: Human-readable description of the incident.

        Returns:
            Path to the written markdown file.
        """
        template = self.get_template(severity)
        self._dir.mkdir(parents=True, exist_ok=True)

        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '-', incident_id)
        path = self._dir / f"{safe_id}.md"

        lines = [
            f"# Incident: {incident_id}",
            "",
            f"**Severity:** {severity.upper()}",
            f"**Description:** {description}",
            "",
            "---",
            "",
            template.to_markdown(),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def list_incidents(self) -> list[Path]:
        """List all incident document file paths, sorted by name.

        Returns:
            A sorted list of ``Path`` objects pointing to ``*.md`` files
            in the incidents directory. Returns an empty list if the
            directory does not exist.
        """
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.md"))

    def load_incident(self, incident_id: str) -> str | None:
        """Read an incident document by ID.

        Args:
            incident_id: Identifier of the incident to load.

        Returns:
            The raw markdown content of the incident document, or ``None``
            if no document exists for the given ID.
        """
        safe_id = re.sub(r'[^a-zA-Z0-9_.-]', '-', incident_id)
        path = self._dir / f"{safe_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None
