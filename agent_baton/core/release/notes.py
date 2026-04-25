"""Auto-generated release notes (R3.3).

Aggregates spec summaries, retrospectives, and commit subjects across a
release range into a human-readable document. Supports markdown, HTML,
and JSON output.

The builder degrades gracefully when R3.1's :class:`Release` entity is
not yet available -- callers can still pass ``--from REF --to REF`` (or
default to ``master..HEAD``) and get useful notes.

Stdlib only. All git interactions go through ``subprocess.run`` against
the local ``git`` binary so we do not take a runtime dependency on
GitPython.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Conventional-commit prefix detection
# ---------------------------------------------------------------------------
#
# We accept the standard conventional-commit grammar:
#     type(scope)!: subject
# where ``type`` is the leading category we key off (feat, fix, perf, ...),
# ``scope`` is optional, and a trailing ``!`` denotes a breaking change.
#
# The regex below matches at the start of a single subject line.
_PREFIX_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)"          # type
    r"(?:\((?P<scope>[^)]+)\))?"     # optional scope
    r"(?P<bang>!)?"                  # optional breaking marker
    r":\s*(?P<subject>.+)$"          # subject after the colon
)

# Hard sentinel for the BREAKING CHANGE footer.
_BREAKING_FOOTER_RE = re.compile(
    r"^BREAKING CHANGE:\s*(?P<explanation>.+)$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class CommitInfo:
    """One git commit, parsed for release-notes purposes."""

    sha: str
    subject: str
    body: str
    author: str
    date: str
    type: str | None = None
    scope: str | None = None
    breaking: bool = False
    breaking_explanation: str | None = None


@dataclass
class Statistics:
    commit_count: int = 0
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    tests_added: int = 0


@dataclass
class ReleaseNotes:
    """Aggregated release notes for a commit range."""

    range_from: str
    range_to: str
    release_id: str | None = None
    release_name: str | None = None
    release_date: str | None = None
    release_status: str | None = None
    highlights: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    perf: list[str] = field(default_factory=list)
    specs: list[tuple[str, str]] = field(default_factory=list)  # (path, headline)
    retros: list[tuple[str, str]] = field(default_factory=list)  # (path, headline)
    breaking: list[str] = field(default_factory=list)
    stats: Statistics = field(default_factory=Statistics)
    generated_at: str = field(
        default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        # tuples lose their structure through asdict -> they become lists already
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def to_markdown(self) -> str:
        lines: list[str] = []
        title = self.release_name or f"Release {self.range_from}..{self.range_to}"
        lines.append(f"# {title}")
        lines.append("")
        meta_bits: list[str] = []
        if self.release_id:
            meta_bits.append(f"**ID:** {self.release_id}")
        if self.release_date:
            meta_bits.append(f"**Date:** {self.release_date}")
        if self.release_status:
            meta_bits.append(f"**Status:** {self.release_status}")
        meta_bits.append(f"**Range:** `{self.range_from}..{self.range_to}`")
        meta_bits.append(f"**Generated:** {self.generated_at}")
        lines.append(" | ".join(meta_bits))
        lines.append("")

        def _section(heading: str, items: list[str]) -> None:
            if not items:
                return
            lines.append(f"## {heading}")
            lines.append("")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

        _section("Highlights", self.highlights)
        _section("Fixes", self.fixes)
        _section("Performance / DX", self.perf)

        if self.specs:
            lines.append("## Specs delivered")
            lines.append("")
            for path, headline in self.specs:
                lines.append(f"- **{headline}** — `{path}`")
            lines.append("")

        if self.retros:
            lines.append("## Retros")
            lines.append("")
            for path, headline in self.retros:
                lines.append(f"- {headline} (`{path}`)")
            lines.append("")

        if self.breaking:
            lines.append("## Breaking changes")
            lines.append("")
            for note in self.breaking:
                lines.append(f"- {note}")
            lines.append("")

        lines.append("## Statistics")
        lines.append("")
        lines.append(f"- Commits: {self.stats.commit_count}")
        lines.append(f"- Files changed: {self.stats.files_changed}")
        lines.append(f"- Lines: +{self.stats.insertions} / -{self.stats.deletions}")
        lines.append(f"- Tests added: {self.stats.tests_added}")
        lines.append("")
        return "\n".join(lines)

    def to_html(self) -> str:
        e = html.escape  # local alias

        def _section(heading: str, items: list[str]) -> str:
            if not items:
                return ""
            li = "\n".join(f"    <li>{e(it)}</li>" for it in items)
            return f"  <h2>{e(heading)}</h2>\n  <ul>\n{li}\n  </ul>\n"

        title = self.release_name or f"Release {self.range_from}..{self.range_to}"
        parts: list[str] = []
        parts.append("<!doctype html>")
        parts.append('<html lang="en">')
        parts.append("<head>")
        parts.append('  <meta charset="utf-8">')
        parts.append(f"  <title>{e(title)}</title>")
        parts.append("</head>")
        parts.append("<body>")
        parts.append(f"  <h1>{e(title)}</h1>")

        meta_bits: list[str] = []
        if self.release_id:
            meta_bits.append(f"<strong>ID:</strong> {e(self.release_id)}")
        if self.release_date:
            meta_bits.append(f"<strong>Date:</strong> {e(self.release_date)}")
        if self.release_status:
            meta_bits.append(f"<strong>Status:</strong> {e(self.release_status)}")
        meta_bits.append(
            f"<strong>Range:</strong> <code>{e(self.range_from)}..{e(self.range_to)}</code>"
        )
        meta_bits.append(f"<strong>Generated:</strong> {e(self.generated_at)}")
        parts.append("  <p>" + " | ".join(meta_bits) + "</p>")

        parts.append(_section("Highlights", self.highlights))
        parts.append(_section("Fixes", self.fixes))
        parts.append(_section("Performance / DX", self.perf))

        if self.specs:
            parts.append("  <h2>Specs delivered</h2>")
            parts.append("  <ul>")
            for path, headline in self.specs:
                parts.append(
                    f"    <li><strong>{e(headline)}</strong> — <code>{e(path)}</code></li>"
                )
            parts.append("  </ul>")

        if self.retros:
            parts.append("  <h2>Retros</h2>")
            parts.append("  <ul>")
            for path, headline in self.retros:
                parts.append(f"    <li>{e(headline)} (<code>{e(path)}</code>)</li>")
            parts.append("  </ul>")

        if self.breaking:
            parts.append(_section("Breaking changes", self.breaking))

        parts.append("  <h2>Statistics</h2>")
        parts.append("  <ul>")
        parts.append(f"    <li>Commits: {self.stats.commit_count}</li>")
        parts.append(f"    <li>Files changed: {self.stats.files_changed}</li>")
        parts.append(
            f"    <li>Lines: +{self.stats.insertions} / -{self.stats.deletions}</li>"
        )
        parts.append(f"    <li>Tests added: {self.stats.tests_added}</li>")
        parts.append("  </ul>")
        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
class ReleaseNotesBuilder:
    """Build :class:`ReleaseNotes` for a commit range or release entity.

    Usage::

        notes = ReleaseNotesBuilder(repo_root).build(from_ref="master", to_ref="HEAD")
        print(notes.to_markdown())

    Pass ``release_id`` to attempt loading the R3.1 ``Release`` entity. If
    R3.1 is not available, the loader silently falls back to commit-only
    mode -- this keeps the command useful in worktrees that branched
    before R3.1 landed.
    """

    # Commit field separator that is extremely unlikely to appear in
    # commit subjects/bodies. Used for ``git log --pretty=format:`` parsing.
    _RECORD_SEP = "\x1e"  # ASCII RS
    _UNIT_SEP = "\x1f"    # ASCII US

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def build(
        self,
        *,
        release_id: str | None = None,
        from_ref: str | None = None,
        to_ref: str | None = None,
    ) -> ReleaseNotes:
        release_meta = self._load_release_meta(release_id) if release_id else {}

        # Resolve range. Order: explicit from/to > release entity refs > defaults.
        from_ref = from_ref or release_meta.get("from_ref") or "master"
        to_ref = to_ref or release_meta.get("to_ref") or "HEAD"

        commits = self._collect_commits(from_ref, to_ref)
        stats = self._collect_stats(from_ref, to_ref, commits)
        specs = self._collect_changed_files(
            from_ref,
            to_ref,
            prefix="docs/superpowers/specs/",
            suffix=".md",
        )
        spec_pairs = [(p, self._extract_headline(p)) for p in specs]

        retros = self._collect_retros(from_ref, to_ref)

        notes = ReleaseNotes(
            range_from=from_ref,
            range_to=to_ref,
            release_id=release_id,
            release_name=release_meta.get("name"),
            release_date=release_meta.get("target_date"),
            release_status=release_meta.get("status"),
            specs=spec_pairs,
            retros=retros,
            stats=stats,
        )

        seen_high: set[str] = set()
        seen_fix: set[str] = set()
        seen_perf: set[str] = set()

        for commit in commits:
            full_subject = commit.subject.strip()
            if not full_subject:
                continue

            ctype = (commit.type or "").lower()
            scope = (commit.scope or "").lower()
            # Strip the conventional-commit prefix from the displayed line so
            # the resulting bullet is human-readable ("add widget" rather
            # than "feat: add widget"). When parsing failed, fall back to
            # the raw subject.
            display = self._clean_subject(full_subject)

            if ctype == "feat" and scope != "dx":
                if display not in seen_high:
                    notes.highlights.append(display)
                    seen_high.add(display)
            elif ctype == "fix":
                if display not in seen_fix:
                    notes.fixes.append(display)
                    seen_fix.add(display)
            elif ctype == "perf" or (ctype == "feat" and scope == "dx"):
                if display not in seen_perf:
                    notes.perf.append(display)
                    seen_perf.add(display)

            if commit.breaking:
                explanation = commit.breaking_explanation or display
                notes.breaking.append(f"{commit.sha[:8]}: {explanation}")

        return notes

    # ------------------------------------------------------------------
    # Release entity (R3.1) -- optional
    # ------------------------------------------------------------------
    def _load_release_meta(self, release_id: str) -> dict:
        """Try to load metadata from R3.1's Release entity.

        Returns an empty dict if the entity isn't available. We keep the
        import lazy so this module imports cleanly when R3.1 hasn't
        landed yet.
        """
        try:  # pragma: no cover - exercised only when R3.1 is merged
            from agent_baton.core.release.entity import Release  # type: ignore
        except Exception:
            return {}
        try:  # pragma: no cover - depends on R3.1 schema
            release = Release.load(release_id)
            return {
                "name": getattr(release, "name", None),
                "target_date": getattr(release, "target_date", None),
                "status": getattr(release, "status", None),
                "from_ref": getattr(release, "from_ref", None),
                "to_ref": getattr(release, "to_ref", None),
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------
    def _git(self, *args: str) -> str:
        """Run a git command and return stdout. Raises on non-zero exit."""
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {proc.stderr.strip()}"
            )
        return proc.stdout

    def _git_safe(self, *args: str) -> str:
        """Like :meth:`_git` but returns empty string on failure."""
        try:
            return self._git(*args)
        except RuntimeError:
            return ""

    # ------------------------------------------------------------------
    # Commit collection
    # ------------------------------------------------------------------
    def _collect_commits(self, from_ref: str, to_ref: str) -> list[CommitInfo]:
        rev_range = f"{from_ref}..{to_ref}" if from_ref else to_ref
        # Field order: sha, author, ISO date, subject, body
        fmt = self._UNIT_SEP.join(["%H", "%an", "%aI", "%s", "%b"]) + self._RECORD_SEP
        out = self._git_safe("log", rev_range, f"--pretty=format:{fmt}")
        if not out:
            return []
        commits: list[CommitInfo] = []
        # Split on the record separator. The last record may be empty.
        for record in out.split(self._RECORD_SEP):
            record = record.strip("\n")
            if not record:
                continue
            parts = record.split(self._UNIT_SEP)
            if len(parts) < 5:
                # Malformed line; skip rather than fail the whole report.
                continue
            sha, author, date, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]

            ctype: str | None = None
            scope: str | None = None
            breaking = False
            breaking_explanation: str | None = None

            m = _PREFIX_RE.match(subject)
            if m:
                ctype = m.group("type")
                scope = m.group("scope")
                if m.group("bang"):
                    breaking = True

            footer_match = _BREAKING_FOOTER_RE.search(body)
            if footer_match:
                breaking = True
                breaking_explanation = footer_match.group("explanation").strip()

            commits.append(
                CommitInfo(
                    sha=sha,
                    subject=subject,
                    body=body,
                    author=author,
                    date=date,
                    type=ctype,
                    scope=scope,
                    breaking=breaking,
                    breaking_explanation=breaking_explanation,
                )
            )
        return commits

    @staticmethod
    def _clean_subject(subject: str) -> str:
        """Strip the conventional-commit prefix from a subject line.

        ``"feat(api)!: rename endpoint"`` -> ``"rename endpoint"``.
        Falls back to the original subject when no prefix is detected so
        non-conventional commits still render meaningfully.
        """
        m = _PREFIX_RE.match(subject)
        if not m:
            return subject
        return m.group("subject").strip()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    def _collect_stats(
        self,
        from_ref: str,
        to_ref: str,
        commits: list[CommitInfo],
    ) -> Statistics:
        rev_range = f"{from_ref}..{to_ref}"
        # ``--shortstat`` per-commit; sum manually to avoid depending on
        # ``--stat`` summary parsing quirks.
        out = self._git_safe(
            "log", rev_range, "--shortstat", "--pretty=format:%H"
        )
        files = ins = dele = 0
        # Pattern lines look like:
        #   " 3 files changed, 25 insertions(+), 1 deletion(-)"
        stat_re = re.compile(
            r"(?P<files>\d+) files? changed"
            r"(?:, (?P<ins>\d+) insertions?\(\+\))?"
            r"(?:, (?P<del>\d+) deletions?\(-\))?"
        )
        for line in out.splitlines():
            m = stat_re.search(line)
            if not m:
                continue
            files += int(m.group("files") or 0)
            ins += int(m.group("ins") or 0)
            dele += int(m.group("del") or 0)

        # Tests added: count commits that touch tests/ as a rough proxy.
        # We use ``--diff-filter=A`` to count actually-added test files.
        added = self._git_safe(
            "log",
            rev_range,
            "--name-only",
            "--diff-filter=A",
            "--pretty=format:",
        )
        tests_added = sum(
            1
            for line in added.splitlines()
            if line.startswith("tests/") and line.endswith(".py")
        )

        return Statistics(
            commit_count=len(commits),
            files_changed=files,
            insertions=ins,
            deletions=dele,
            tests_added=tests_added,
        )

    # ------------------------------------------------------------------
    # Spec / retro collection
    # ------------------------------------------------------------------
    def _collect_changed_files(
        self,
        from_ref: str,
        to_ref: str,
        *,
        prefix: str,
        suffix: str = "",
    ) -> list[str]:
        rev_range = f"{from_ref}..{to_ref}"
        out = self._git_safe(
            "log",
            rev_range,
            "--name-only",
            "--diff-filter=A",
            "--pretty=format:",
        )
        seen: list[str] = []
        seen_set: set[str] = set()
        for line in out.splitlines():
            line = line.strip()
            if not line or not line.startswith(prefix):
                continue
            if suffix and not line.endswith(suffix):
                continue
            if line in seen_set:
                continue
            seen_set.add(line)
            seen.append(line)
        return seen

    def _collect_retros(self, from_ref: str, to_ref: str) -> list[tuple[str, str]]:
        """Pick up retro files added in the range, then any matching files on disk.

        Retros are usually committed alongside the work; the typical flow
        adds a markdown file under ``.claude/team-context/retrospectives/``.
        Some installations may keep retros uncommitted, so as a fallback
        we surface any markdown retros that touch the range's date span
        when present on disk.
        """
        added = self._collect_changed_files(
            from_ref,
            to_ref,
            prefix=".claude/team-context/retrospectives/",
            suffix=".md",
        )
        results: list[tuple[str, str]] = [
            (p, self._extract_headline(p)) for p in added
        ]

        # Fallback: scan on-disk retros if none were committed in-range.
        if not results:
            retros_dir = self.repo_root / ".claude" / "team-context" / "retrospectives"
            if retros_dir.is_dir():
                for path in sorted(retros_dir.glob("*.md")):
                    rel = str(path.relative_to(self.repo_root))
                    results.append((rel, self._extract_headline(rel)))
        return results

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------
    def _extract_headline(self, rel_path: str) -> str:
        """Return the first ``# heading`` of a markdown file, or its basename."""
        path = self.repo_root / rel_path
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        return stripped.lstrip("# ").strip()
                    # Allow a few non-empty lines before giving up.
                    if stripped and not stripped.startswith("---"):
                        return stripped
        except OSError:
            pass
        return Path(rel_path).stem


__all__ = [
    "CommitInfo",
    "ReleaseNotes",
    "ReleaseNotesBuilder",
    "Statistics",
]
