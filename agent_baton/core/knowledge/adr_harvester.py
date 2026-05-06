"""ADR-to-knowledge harvester.

Walks a source tree (typically ``docs/``) for Architecture Decision Records
and converts them into knowledge documents under
``.claude/knowledge/<pack>/adr-<N>-<slug>.md``. Updates the pack's
``knowledge.yaml`` additively.

ADR detection:
    A markdown file is treated as an ADR when ANY of the following is true:
        - It lives in a directory named ``adr/`` or ``decisions/`` (or any
          directory whose name contains "decision"/"adr").
        - The filename matches ``ADR-<N>*.md`` or ``<NNNN>-<slug>.md``
          (numeric prefix, common MADR convention).
        - The filename matches ``*.adr.md``.

Idempotency:
    Re-running on the same source tree must not duplicate documents. We
    embed a stable ``source_sha256`` in each generated document's YAML
    frontmatter; on re-run, if the harvested file's hash matches the
    existing document's hash we skip the write.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Filename patterns that always signal an ADR.
_ADR_FILENAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^ADR-(?P<num>\d+)(?:[-_](?P<slug>.+))?$", re.IGNORECASE),
    re.compile(r"^(?P<num>\d{3,5})[-_](?P<slug>[A-Za-z0-9._-]+)$"),
    re.compile(r"^(?P<slug>.+)\.adr$", re.IGNORECASE),
)

# Substrings in a parent-directory name that signal "this is an ADR folder".
_ADR_DIR_HINTS: tuple[str, ...] = ("adr", "decision", "decisions")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class HarvestedADR:
    """A single ADR detected on disk."""

    source_path: Path
    number: str | None
    slug: str
    title: str
    body: str

    @property
    def doc_filename(self) -> str:
        """Filename inside the knowledge pack — ``adr-<N>-<slug>.md``.

        If no number was extractable, falls back to the slug alone.
        """
        slug = _slugify(self.slug)
        if self.number:
            return f"adr-{self.number}-{slug}.md"
        return f"adr-{slug}.md"


@dataclass
class HarvestResult:
    """Summary of an ADR harvest run."""

    pack_dir: Path
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    scanned: int = 0


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Cheap ASCII slug: lower, dashes, no consecutive dashes."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-") or "untitled"


def _stem_match(stem: str) -> tuple[str | None, str]:
    """Extract (number, slug) from a filename stem if it matches an ADR pattern.

    Returns (None, "") when no pattern matched.
    """
    for pat in _ADR_FILENAME_PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        num = m.groupdict().get("num")
        slug = m.groupdict().get("slug") or ""
        if num is not None:
            num = num.lstrip("0") or "0"
            return num, slug or stem
        return None, slug or stem
    return None, ""


def _looks_like_adr_dir(path: Path) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if lowered in _ADR_DIR_HINTS:
            return True
    return False


def _is_adr_file(path: Path) -> bool:
    """True if *path* looks like an ADR markdown file.

    Guarded against directory matches and templates/READMEs.
    """
    if path.suffix.lower() != ".md":
        return False
    name = path.name.lower()
    if name in {"readme.md", "index.md", "template.md"}:
        return False
    if name.endswith(".adr.md"):
        return True
    stem = path.stem
    num, _slug = _stem_match(stem)
    if num is not None or _slug:
        # Only treat numeric-prefix matches as ADRs when in an ADR-shaped dir
        # (e.g. ``0042-foo.md`` outside an adr/ folder is ambiguous).
        if num is not None and not _looks_like_adr_dir(path):
            return False
        return True
    if _looks_like_adr_dir(path):
        # A markdown file inside an adr/decisions directory counts even
        # without a numeric prefix (e.g. ``status-pages.md``).
        return True
    return False


def _extract_title(body: str, fallback: str) -> str:
    """Pull the first H1 from a markdown body. Fall back to *fallback*."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_adrs(source_dir: Path) -> list[HarvestedADR]:
    """Walk *source_dir* and return one :class:`HarvestedADR` per ADR file.

    Args:
        source_dir: Root to walk recursively. Non-existent paths return [].
    """
    if not source_dir.is_dir():
        return []

    found: list[HarvestedADR] = []
    for md_path in sorted(source_dir.rglob("*.md")):
        if not _is_adr_file(md_path):
            continue
        try:
            raw = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", md_path, exc)
            continue

        stem = md_path.stem
        # Strip ``.adr`` suffix from stem when present.
        if stem.lower().endswith(".adr"):
            stem = stem[:-4]
        num, slug = _stem_match(stem)
        if not slug:
            slug = stem
        title = _extract_title(raw, fallback=slug.replace("-", " ").replace("_", " ").strip().title())
        found.append(
            HarvestedADR(
                source_path=md_path,
                number=num,
                slug=slug,
                title=title,
                body=raw,
            )
        )
    return found


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _existing_hash(doc_path: Path) -> str | None:
    """Return the ``source_sha256`` recorded in *doc_path*'s frontmatter, if any."""
    if not doc_path.is_file():
        return None
    try:
        existing = doc_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not existing.startswith("---"):
        return None
    parts = existing.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None
    val = meta.get("source_sha256")
    return str(val) if val else None


def _render_doc(adr: HarvestedADR, source_hash: str, source_dir: Path) -> str:
    """Render a knowledge document with frontmatter for *adr*."""
    try:
        rel_source = adr.source_path.resolve().relative_to(source_dir.resolve()).as_posix()
    except ValueError:
        rel_source = adr.source_path.as_posix()
    name = adr.doc_filename[:-3]  # strip .md
    front = {
        "name": name,
        "description": f"ADR{(' ' + adr.number) if adr.number else ''}: {adr.title}",
        "tags": ["adr", "decision", "architecture"],
        "priority": "normal",
        "source_path": rel_source,
        "source_sha256": source_hash,
    }
    if adr.number:
        front["adr_number"] = adr.number

    front_yaml = yaml.safe_dump(front, sort_keys=False).strip()
    return f"---\n{front_yaml}\n---\n\n{adr.body.lstrip()}\n"


def _update_manifest(pack_dir: Path, pack_name: str, doc_names: list[str]) -> None:
    """Add *doc_names* to the pack's ``knowledge.yaml`` ``docs:`` list.

    Creates the manifest if it does not exist. Idempotent: existing entries
    are preserved and de-duplicated.
    """
    manifest_path = pack_dir / "knowledge.yaml"
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Cannot parse %s: %s — recreating", manifest_path, exc)
            manifest = {}

    manifest.setdefault("name", pack_name)
    manifest.setdefault(
        "description",
        "Architecture decision records harvested from project documentation",
    )
    tags = manifest.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    for required in ("adr", "decisions", "architecture"):
        if required not in tags:
            tags.append(required)
    manifest["tags"] = tags
    manifest.setdefault("target_agents", [])
    manifest.setdefault("default_delivery", "reference")

    existing_docs = manifest.get("docs") or []
    if not isinstance(existing_docs, list):
        existing_docs = []
    seen = {str(d) for d in existing_docs}
    for name in doc_names:
        if name not in seen:
            existing_docs.append(name)
            seen.add(name)
    manifest["docs"] = existing_docs

    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def harvest_adrs(
    source_dir: Path,
    *,
    target_pack: str = "decisions",
    knowledge_root: Path | None = None,
) -> HarvestResult:
    """Convert every ADR under *source_dir* into a knowledge document.

    Args:
        source_dir: Root to walk for ADR markdown files.
        target_pack: Pack directory name under ``knowledge_root``.
        knowledge_root: Root knowledge directory (default
            ``.claude/knowledge`` resolved against the cwd).

    Returns:
        :class:`HarvestResult` describing what was written or skipped.
    """
    if knowledge_root is None:
        knowledge_root = (Path(".claude") / "knowledge").resolve()
    pack_dir = knowledge_root / target_pack
    pack_dir.mkdir(parents=True, exist_ok=True)

    adrs = discover_adrs(source_dir)
    result = HarvestResult(pack_dir=pack_dir, scanned=len(adrs))

    written_doc_names: list[str] = []
    for adr in adrs:
        target = pack_dir / adr.doc_filename
        new_hash = _content_hash(adr.body)
        old_hash = _existing_hash(target)
        if old_hash == new_hash:
            result.skipped.append(target)
            continue
        rendered = _render_doc(adr, new_hash, source_dir)
        target.write_text(rendered, encoding="utf-8")
        result.written.append(target)
        written_doc_names.append(target.stem)

    # Always include all current docs in the manifest so re-runs that did
    # not write anything still settle the manifest into a consistent state.
    all_doc_names = sorted(p.stem for p in pack_dir.glob("*.md"))
    _update_manifest(pack_dir, target_pack, all_doc_names)
    return result
