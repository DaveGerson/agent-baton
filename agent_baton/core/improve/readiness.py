"""Org-readiness assessment -- score how prepared a project is to delegate
work to baton-orchestrated agents.

H3.6 (bd-0dea): pure read-side assessment with no friction added.

The assessor inspects a project root and computes a 0--10 raw score for
each of eight readiness dimensions, then normalises to a 0--100 total
with a tier label and per-dimension recommendations.

Dimensions
----------

1. **Spec discipline** -- does the project document what it builds?
   Checks for ``CLAUDE.md``, ``docs/`` design docs, and recent specs
   under ``docs/superpowers/specs/`` or ``specs/``.
2. **Test coverage** -- does the project have a tests directory, a
   meaningful number of tests, and coverage tooling configured?
3. **Conventions documented** -- does ``CLAUDE.md`` describe imports/
   conventions/style?  Are pre-commit hooks present?  ``.editorconfig``?
4. **Knowledge stocked** -- is ``.claude/knowledge/`` populated with at
   least one ``knowledge.yaml`` pack containing >=3 docs?
5. **Agent roster** -- does ``.claude/agents/`` have >=10 agents and
   cover engineer/reviewer/test/architect roles?
6. **Audit chain** -- does ``compliance-audit.jsonl`` exist with >=10
   entries (verify-clean is treated as a half-credit signal)?
7. **Bead memory** -- does ``baton.db`` exist with >=5 beads and at
   least one closed bead with a summary?
8. **CI integration** -- is there a CI workflow that references baton
   or pytest or similar?

Scoring is intentionally simple and stdlib-only so the assessment runs
in well under two seconds on a typical repo.

Public API
----------

``ReadinessAssessor.assess(project_root) -> ReadinessReport``

The report can be rendered to ``markdown`` or serialised to ``json``
via :meth:`ReadinessReport.to_markdown` and :meth:`ReadinessReport.to_dict`.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DimensionScore:
    """Score for a single readiness dimension.

    Attributes:
        name: Short identifier (e.g. ``"spec_discipline"``).
        title: Human-readable title (e.g. ``"Spec discipline"``).
        score: Raw score 0--10.
        max_score: Maximum possible score (always 10 today; kept as a
            field so callers can render a denominator without hard-coding
            it).
        signals: Human-readable list of which checks fired.
        recommendation: Concrete next step if ``score < 8``; ``None``
            otherwise.
    """

    name: str
    title: str
    score: int
    max_score: int
    signals: list[str] = field(default_factory=list)
    recommendation: str | None = None


@dataclass
class ReadinessReport:
    """Aggregated readiness report for a project.

    Attributes:
        project_root: Absolute path to the assessed project.
        total: Normalised total 0--100.
        tier: Tier label (e.g. ``"Maturing -- delegate but verify"``).
        dimensions: Per-dimension scores in declaration order.
        recommendations: Concrete next steps for under-scoring dimensions.
    """

    project_root: str
    total: int
    tier: str
    dimensions: list[DimensionScore]
    recommendations: list[str]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Render the report as a JSON-serialisable dict."""
        return {
            "project_root": self.project_root,
            "total": self.total,
            "tier": self.tier,
            "dimensions": [asdict(d) for d in self.dimensions],
            "recommendations": list(self.recommendations),
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Render the report as a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_markdown(self) -> str:
        """Render the report as a human-friendly markdown document."""
        lines: list[str] = []
        lines.append("# Org Readiness Assessment")
        lines.append("")
        lines.append(f"**Project:** `{self.project_root}`")
        lines.append("")
        lines.append(f"**Total:** {self.total} / 100  --  _{self.tier}_")
        lines.append("")
        lines.append("## Dimension breakdown")
        lines.append("")
        lines.append("| # | Dimension | Score | Signals |")
        lines.append("|---|-----------|-------|---------|")
        for idx, dim in enumerate(self.dimensions, start=1):
            signals = "; ".join(dim.signals) if dim.signals else "_none_"
            lines.append(
                f"| {idx} | {dim.title} | {dim.score}/{dim.max_score} | {signals} |"
            )
        lines.append("")
        if self.recommendations:
            lines.append("## Recommended next steps")
            lines.append("")
            for rec in self.recommendations:
                lines.append(f"- {rec}")
            lines.append("")
        else:
            lines.append("## Recommended next steps")
            lines.append("")
            lines.append("_All dimensions are strong (>=8). No actions required._")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier mapping
# ---------------------------------------------------------------------------


_TIERS: list[tuple[int, str]] = [
    (90, "Production-ready -- full delegation"),
    (75, "Strong -- delegate with light review"),
    (50, "Maturing -- delegate but verify"),
    (25, "Early -- pilot small tasks first"),
    (0, "Not ready -- invest in foundation"),
]


def _tier_for(total: int) -> str:
    for threshold, label in _TIERS:
        if total >= threshold:
            return label
    return _TIERS[-1][1]


# ---------------------------------------------------------------------------
# Assessor
# ---------------------------------------------------------------------------


class ReadinessAssessor:
    """Compute org-readiness scores for a project root.

    All checks are stdlib-only and read-only.  The assessor never writes
    to the project filesystem and never executes project code.
    """

    # Roles we expect to see covered in a healthy roster.
    _ROSTER_ROLES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("engineer", ("engineer", "developer", "coder")),
        ("reviewer", ("reviewer", "review")),
        ("test", ("test-engineer", "tester", "qa")),
        ("architect", ("architect", "designer")),
    )

    # CI files we recognise.
    _CI_PATHS: tuple[str, ...] = (
        ".github/workflows",
        ".gitlab-ci.yml",
        ".circleci/config.yml",
        "azure-pipelines.yml",
        "Jenkinsfile",
        ".drone.yml",
        "bitbucket-pipelines.yml",
    )

    # Test runners we recognise inside CI configs.
    _TEST_RUNNERS: tuple[str, ...] = (
        "baton",
        "pytest",
        "jest",
        "vitest",
        "mocha",
        "go test",
        "cargo test",
        "rspec",
        "phpunit",
        "tox",
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assess(self, project_root: Path) -> ReadinessReport:
        """Return a :class:`ReadinessReport` for ``project_root``."""
        root = Path(project_root).resolve()

        dimensions: list[DimensionScore] = [
            self._score_spec_discipline(root),
            self._score_test_coverage(root),
            self._score_conventions(root),
            self._score_knowledge(root),
            self._score_agent_roster(root),
            self._score_audit_chain(root),
            self._score_bead_memory(root),
            self._score_ci_integration(root),
        ]

        # Total is sum of raw scores (max 80) normalised to 0--100.
        raw_total = sum(d.score for d in dimensions)
        max_total = sum(d.max_score for d in dimensions)
        total = round(raw_total * 100 / max_total) if max_total else 0

        tier = _tier_for(total)

        recommendations = [
            d.recommendation for d in dimensions if d.recommendation
        ]

        return ReadinessReport(
            project_root=str(root),
            total=total,
            tier=tier,
            dimensions=dimensions,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Dimension checks
    # ------------------------------------------------------------------

    def _score_spec_discipline(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        if (root / "CLAUDE.md").is_file():
            score += 4
            signals.append("CLAUDE.md present")

        # docs/architecture or docs/design or just docs/ with .md content
        docs_dir = root / "docs"
        if docs_dir.is_dir():
            arch = docs_dir / "architecture"
            design = docs_dir / "design"
            if arch.is_dir() and any(arch.glob("*.md")):
                score += 3
                signals.append("docs/architecture/ design docs")
            elif design.is_dir() and any(design.glob("*.md")):
                score += 3
                signals.append("docs/design/ design docs")
            elif any(docs_dir.glob("*.md")):
                # Half credit for any docs/ markdown
                score += 1
                signals.append("docs/ markdown present (no design subfolder)")

        # Specs under docs/superpowers/specs/ or specs/
        spec_dirs = [
            root / "docs" / "superpowers" / "specs",
            root / "specs",
        ]
        for sd in spec_dirs:
            if sd.is_dir() and any(sd.rglob("*.md")):
                score += 3
                signals.append(f"specs in {sd.relative_to(root)}/")
                break

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Spec discipline: add a top-level CLAUDE.md and write design "
                "docs under docs/architecture/ to anchor agent context."
            )
        return DimensionScore(
            name="spec_discipline",
            title="Spec discipline",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _score_test_coverage(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        tests_dir = root / "tests"
        test_dir_alt = root / "test"
        chosen: Path | None = None
        if tests_dir.is_dir():
            chosen = tests_dir
            signals.append("tests/ directory present")
            score += 3
        elif test_dir_alt.is_dir():
            chosen = test_dir_alt
            signals.append("test/ directory present")
            score += 3

        if chosen is not None:
            count = sum(1 for _ in chosen.rglob("test_*.py")) + sum(
                1 for _ in chosen.rglob("*_test.py")
            ) + sum(1 for _ in chosen.rglob("*.spec.ts")) + sum(
                1 for _ in chosen.rglob("*.test.ts")
            )
            if count >= 100:
                score += 4
                signals.append(f"{count} test files (>=100)")
            elif count >= 10:
                score += 2
                signals.append(f"{count} test files (>=10)")
            elif count > 0:
                score += 1
                signals.append(f"{count} test files")

        # Coverage configuration (pyproject, setup.cfg, package.json)
        coverage_signals = self._has_coverage_config(root)
        if coverage_signals:
            score += 3
            signals.append(f"coverage config: {coverage_signals}")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Test coverage: add a tests/ directory with >=100 test files "
                "and enable coverage (pytest --cov / jest --coverage)."
            )
        return DimensionScore(
            name="test_coverage",
            title="Test coverage",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _has_coverage_config(self, root: Path) -> str:
        candidates: list[tuple[Path, tuple[str, ...]]] = [
            (root / "pyproject.toml", ("--cov", "[tool.coverage", "coverage.run")),
            (root / "setup.cfg", ("--cov", "[coverage:")),
            (root / "tox.ini", ("--cov", "[coverage:")),
            (root / ".coveragerc", ("",)),
            (root / "package.json", ("--coverage", "\"coverage\"", "jest.coverage")),
            (root / "pytest.ini", ("--cov",)),
        ]
        hits: list[str] = []
        for path, needles in candidates:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if path.name == ".coveragerc":
                hits.append(path.name)
                continue
            if any(n and n in text for n in needles):
                hits.append(path.name)
        return ", ".join(hits)

    def _score_conventions(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        claude = root / "CLAUDE.md"
        if claude.is_file():
            try:
                text = claude.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                text = ""
            keywords = ("import", "convention", "style", "lint", "format")
            hits = [k for k in keywords if k in text]
            if len(hits) >= 2:
                score += 4
                signals.append(f"CLAUDE.md mentions {','.join(hits)}")
            elif hits:
                score += 2
                signals.append(f"CLAUDE.md mentions {hits[0]}")

        if (root / ".pre-commit-config.yaml").is_file() or (
            root / ".pre-commit-config.yml"
        ).is_file():
            score += 3
            signals.append("pre-commit hooks configured")

        if (root / ".editorconfig").is_file():
            score += 3
            signals.append(".editorconfig present")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Conventions: document import/style rules in CLAUDE.md and "
                "add a .pre-commit-config.yaml + .editorconfig."
            )
        return DimensionScore(
            name="conventions_documented",
            title="Conventions documented",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _score_knowledge(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        knowledge_dir = root / ".claude" / "knowledge"
        if knowledge_dir.is_dir():
            score += 3
            signals.append(".claude/knowledge/ exists")
            packs = list(knowledge_dir.rglob("knowledge.yaml"))
            if packs:
                score += 3
                signals.append(f"{len(packs)} knowledge pack(s)")
                # Half-credit boost: at least one pack with >=3 docs.
                # We don't parse YAML (stdlib only); use sibling .md / .txt
                # / .yaml docs in the pack directory as a proxy.
                rich_packs = 0
                for pack in packs:
                    pack_dir = pack.parent
                    docs = [
                        p
                        for p in pack_dir.iterdir()
                        if p.is_file() and p.name != "knowledge.yaml"
                    ]
                    if len(docs) >= 3:
                        rich_packs += 1
                if rich_packs:
                    score += 4
                    signals.append(f"{rich_packs} pack(s) with >=3 docs")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Knowledge: run `baton knowledge brief --save` to seed a "
                "knowledge pack under .claude/knowledge/."
            )
        return DimensionScore(
            name="knowledge_stocked",
            title="Knowledge stocked",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _score_agent_roster(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        agents_dir = root / ".claude" / "agents"
        if not agents_dir.is_dir():
            rec = (
                "Agent roster: install agents with `baton install --scope project` "
                "to populate .claude/agents/."
            )
            return DimensionScore(
                name="agent_roster",
                title="Agent roster",
                score=0,
                max_score=10,
                signals=["no .claude/agents/ directory"],
                recommendation=rec,
            )

        agents = [p for p in agents_dir.glob("*.md") if p.is_file()]
        n = len(agents)
        if n >= 10:
            score += 5
            signals.append(f"{n} agent definitions (>=10)")
        elif n >= 5:
            score += 3
            signals.append(f"{n} agent definitions (>=5)")
        elif n > 0:
            score += 1
            signals.append(f"{n} agent definitions")

        # Role coverage.
        names_lower = " ".join(p.stem.lower() for p in agents)
        roles_covered: list[str] = []
        for label, needles in self._ROSTER_ROLES:
            if any(n in names_lower for n in needles):
                roles_covered.append(label)
        if len(roles_covered) >= 4:
            score += 5
            signals.append(f"covers {','.join(roles_covered)}")
        elif len(roles_covered) >= 2:
            score += 3
            signals.append(f"covers {','.join(roles_covered)}")
        elif roles_covered:
            score += 1
            signals.append(f"covers {','.join(roles_covered)}")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Agent roster: install at least 10 agents covering engineer, "
                "reviewer, test, and architect roles."
            )
        return DimensionScore(
            name="agent_roster",
            title="Agent roster",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _score_audit_chain(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        candidates = [
            root / ".claude" / "team-context" / "compliance-audit.jsonl",
            root / "compliance-audit.jsonl",
        ]
        chain: Path | None = next((p for p in candidates if p.is_file()), None)

        if chain is None:
            rec = (
                "Audit chain: enable compliance auditing so "
                ".claude/team-context/compliance-audit.jsonl is populated."
            )
            return DimensionScore(
                name="audit_chain",
                title="Audit chain",
                score=0,
                max_score=10,
                signals=["no compliance-audit.jsonl"],
                recommendation=rec,
            )

        score += 3
        signals.append(f"chain at {chain.relative_to(root)}")

        try:
            with chain.open("r", encoding="utf-8", errors="replace") as fh:
                entries = sum(1 for line in fh if line.strip())
        except OSError:
            entries = 0

        if entries >= 100:
            score += 5
            signals.append(f"{entries} entries (>=100)")
        elif entries >= 10:
            score += 4
            signals.append(f"{entries} entries (>=10)")
        elif entries > 0:
            score += 2
            signals.append(f"{entries} entries")

        # Half-credit for verify-clean signal: presence of a .verified marker
        # or "verify" mention in the same directory.  We treat any sibling
        # ``*.verified`` file or ``audit-verify.log`` as a clean signal.
        chain_dir = chain.parent
        if any(chain_dir.glob("*.verified")) or (chain_dir / "audit-verify.log").is_file():
            score += 2
            signals.append("verify signal present")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Audit chain: accumulate >=10 compliance-audit.jsonl entries "
                "and run `baton compliance verify` to record a clean signal."
            )
        return DimensionScore(
            name="audit_chain",
            title="Audit chain",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _score_bead_memory(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        candidates = [
            root / ".claude" / "team-context" / "baton.db",
            root / "baton.db",
        ]
        db: Path | None = next((p for p in candidates if p.is_file()), None)

        if db is None:
            rec = (
                "Bead memory: run `baton beads create` to start recording "
                "decisions in baton.db."
            )
            return DimensionScore(
                name="bead_memory",
                title="Bead memory",
                score=0,
                max_score=10,
                signals=["no baton.db"],
                recommendation=rec,
            )

        score += 2
        signals.append(f"baton.db at {db.relative_to(root)}")

        total_beads = 0
        closed_with_summary = 0
        try:
            # Open read-only via URI to avoid creating WAL/journal sidecars.
            uri = f"file:{db.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=1.0)
            try:
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='beads'"
                )
                if cur.fetchone():
                    total_beads = conn.execute(
                        "SELECT COUNT(*) FROM beads"
                    ).fetchone()[0]
                    closed_with_summary = conn.execute(
                        "SELECT COUNT(*) FROM beads "
                        "WHERE status IN ('closed', 'archived') "
                        "AND summary IS NOT NULL AND summary != ''"
                    ).fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error:
            pass

        if total_beads >= 50:
            score += 5
            signals.append(f"{total_beads} beads (>=50)")
        elif total_beads >= 5:
            score += 4
            signals.append(f"{total_beads} beads (>=5)")
        elif total_beads > 0:
            score += 2
            signals.append(f"{total_beads} beads")

        if closed_with_summary >= 1:
            score += 3
            signals.append(f"{closed_with_summary} closed bead(s) with summary")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "Bead memory: record >=5 beads (`baton beads create`) and "
                "close at least one with a summary."
            )
        return DimensionScore(
            name="bead_memory",
            title="Bead memory",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )

    def _score_ci_integration(self, root: Path) -> DimensionScore:
        score = 0
        signals: list[str] = []

        ci_files: list[Path] = []
        for rel in self._CI_PATHS:
            p = root / rel
            if p.is_dir():
                ci_files.extend(q for q in p.rglob("*") if q.is_file())
            elif p.is_file():
                ci_files.append(p)

        if ci_files:
            score += 5
            signals.append(f"{len(ci_files)} CI config file(s)")

            joined = ""
            for cf in ci_files[:20]:  # cap to avoid pathological repos
                try:
                    joined += cf.read_text(encoding="utf-8", errors="replace").lower()
                except OSError:
                    continue
            runners = [r for r in self._TEST_RUNNERS if r in joined]
            if runners:
                score += 5
                signals.append(f"runs {','.join(runners)}")

        score = min(score, 10)
        rec = None
        if score < 8:
            rec = (
                "CI integration: add a CI workflow (e.g. .github/workflows/) "
                "that runs your test suite or `baton` checks on every PR."
            )
        return DimensionScore(
            name="ci_integration",
            title="CI integration",
            score=score,
            max_score=10,
            signals=signals,
            recommendation=rec,
        )


__all__ = [
    "DimensionScore",
    "ReadinessReport",
    "ReadinessAssessor",
]
