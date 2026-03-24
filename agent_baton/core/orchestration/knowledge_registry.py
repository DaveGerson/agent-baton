"""Knowledge registry — loads and queries knowledge packs from disk."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path

import yaml

from agent_baton.models.knowledge import KnowledgeDocument, KnowledgePack
from agent_baton.utils.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)

# Characters-per-token heuristic used at index time (no model tokeniser needed).
_CHARS_PER_TOKEN = 4


def _estimate_tokens(path: Path) -> int:
    """Estimate token count for a file by reading its byte length.

    Uses character count ÷ 4 as a fast, dependency-free heuristic.
    Returns 0 if the file cannot be read (silently — caller decides).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return max(1, len(text) // _CHARS_PER_TOKEN)
    except OSError:
        return 0


def _normalise_tags(raw: object) -> list[str]:
    """Coerce a YAML tags value to a flat list of strings."""
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if t]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _normalise_list_of_strings(raw: object) -> list[str]:
    """Coerce a YAML list-or-string field to a list of strings."""
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if v]
    if isinstance(raw, str):
        return [v.strip() for v in raw.split(",") if v.strip()]
    return []


# ---------------------------------------------------------------------------
# TF-IDF helpers
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list[str]:
    """Split text into lowercase alphanumeric tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_corpus_text(pack: KnowledgePack, doc: KnowledgeDocument) -> str:
    """Build the metadata corpus string for a (pack, doc) pair."""
    parts = [
        pack.name,
        pack.description,
        " ".join(pack.tags),
        doc.name,
        doc.description,
        " ".join(doc.tags),
    ]
    return " ".join(p for p in parts if p)


class _TFIDFIndex:
    """Minimal TF-IDF index over (pack, doc) corpus entries.

    Built once when the registry is loaded. No external dependencies.
    Uses log-normalised IDF: idf(t) = log(N / df(t)) + 1.
    """

    def __init__(self) -> None:
        # List of (pack, doc, Counter{term: count}) entries
        self._entries: list[tuple[KnowledgePack, KnowledgeDocument, Counter]] = []
        self._idf: dict[str, float] = {}
        self._dirty = True

    def add(self, pack: KnowledgePack, doc: KnowledgeDocument) -> None:
        corpus_text = _build_corpus_text(pack, doc)
        tokens = _tokenise(corpus_text)
        self._entries.append((pack, doc, Counter(tokens)))
        self._dirty = True

    def _rebuild_idf(self) -> None:
        n = len(self._entries)
        if n == 0:
            self._idf = {}
            self._dirty = False
            return
        df: Counter = Counter()
        for _, _, term_counts in self._entries:
            for term in term_counts:
                df[term] += 1
        self._idf = {
            term: math.log(n / count) + 1.0
            for term, count in df.items()
        }
        self._dirty = False

    def search(
        self, query: str, *, limit: int = 10, threshold: float = 0.3
    ) -> list[tuple[KnowledgeDocument, float]]:
        if self._dirty:
            self._rebuild_idf()

        if not self._entries:
            return []

        query_tokens = _tokenise(query)
        if not query_tokens:
            return []

        query_tf: Counter = Counter(query_tokens)

        scored: list[tuple[KnowledgeDocument, float]] = []
        for pack, doc, doc_tf in self._entries:
            doc_len = sum(doc_tf.values()) or 1
            score = 0.0
            for token, qcount in query_tf.items():
                if token not in doc_tf:
                    continue
                tf = doc_tf[token] / doc_len
                idf = self._idf.get(token, 1.0)
                score += (qcount * tf * idf)

            if score >= threshold:
                scored.append((doc, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]


# ---------------------------------------------------------------------------
# KnowledgeRegistry
# ---------------------------------------------------------------------------

class KnowledgeRegistry:
    """Load, index, and query knowledge packs from directory trees.

    Scans both project-level (.claude/knowledge/) and global
    (~/.claude/knowledge/) directories. Project packs override global packs
    with the same name — identical precedence model to AgentRegistry.

    Document content is NOT loaded at index time (lazy, on-demand via
    get_document() or direct KnowledgeDocument.source_path reads).
    """

    def __init__(self) -> None:
        self._packs: dict[str, KnowledgePack] = {}
        self._tfidf = _TFIDFIndex()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def all_packs(self) -> dict[str, KnowledgePack]:
        """Return a copy of the current pack index."""
        return dict(self._packs)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_directory(self, directory: Path, *, override: bool = False) -> int:
        """Load all knowledge packs from a directory.

        Each immediate sub-directory of *directory* is treated as a potential
        knowledge pack. A pack directory may contain:
        - ``knowledge.yaml`` — pack manifest (optional but recommended)
        - ``*.md`` files — knowledge documents

        Args:
            directory: Root knowledge directory to scan (e.g. ``.claude/knowledge/``).
            override: If True, packs from this directory replace existing packs
                with the same name (used for project-level overrides).

        Returns:
            Number of packs loaded (including overrides).
        """
        if not directory.is_dir():
            return 0

        count = 0
        for pack_dir in sorted(directory.iterdir()):
            if not pack_dir.is_dir():
                continue
            pack = self._load_pack(pack_dir)
            if pack is None:
                continue
            if override or pack.name not in self._packs:
                # Remove stale TF-IDF entries for the overridden pack, if any.
                # Simplest approach: rebuild index entries after each override.
                self._packs[pack.name] = pack
                count += 1

        # Rebuild TF-IDF index from scratch whenever new packs are added.
        self._rebuild_tfidf()
        return count

    def load_default_paths(self) -> int:
        """Load packs from standard locations (global then project override).

        Mirrors AgentRegistry.load_default_paths():
        - Global: ``~/.claude/knowledge/``
        - Project: ``.claude/knowledge/`` (relative to cwd, resolved)

        Returns:
            Total number of packs loaded.
        """
        global_dir = Path.home() / ".claude" / "knowledge"
        project_dir = (Path(".claude") / "knowledge").resolve()

        count = self.load_directory(global_dir)
        count += self.load_directory(project_dir, override=True)
        return count

    # ------------------------------------------------------------------
    # Exact lookups
    # ------------------------------------------------------------------

    def get_pack(self, name: str) -> KnowledgePack | None:
        """Look up a pack by exact name."""
        return self._packs.get(name)

    def get_document(self, pack_name: str, doc_name: str) -> KnowledgeDocument | None:
        """Look up a specific document within a named pack.

        Returns None if either the pack or document does not exist.
        Content is NOT loaded here — access ``doc.source_path`` and read
        the file if you need the body.
        """
        pack = self._packs.get(pack_name)
        if pack is None:
            return None
        for doc in pack.documents:
            if doc.name == doc_name:
                return doc
        return None

    # ------------------------------------------------------------------
    # Queries — strict matching
    # ------------------------------------------------------------------

    def packs_for_agent(self, agent_name: str) -> list[KnowledgePack]:
        """Return all packs that list *agent_name* in their ``target_agents``.

        Exact string match. An agent's base name (without flavor) is also
        checked so that ``backend-engineer`` matches
        ``backend-engineer--python``.
        """
        base_name = agent_name.split("--")[0] if "--" in agent_name else agent_name
        result = []
        for pack in self._packs.values():
            if not pack.target_agents:
                continue
            if agent_name in pack.target_agents or base_name in pack.target_agents:
                result.append(pack)
        return result

    def find_by_tags(self, tags: set[str]) -> list[KnowledgeDocument]:
        """Return all documents whose tags overlap with *tags*.

        Intersection match — a document matches if it shares at least one
        tag with the query set. Case-insensitive.
        """
        lower_tags = {t.lower() for t in tags}
        results: list[KnowledgeDocument] = []
        for pack in self._packs.values():
            for doc in pack.documents:
                doc_tags = {t.lower() for t in doc.tags}
                # Also check pack-level tags as a fallback signal
                pack_tags = {t.lower() for t in pack.tags}
                if lower_tags & (doc_tags | pack_tags):
                    results.append(doc)
        return results

    # ------------------------------------------------------------------
    # Query — relevance fallback
    # ------------------------------------------------------------------

    def search(
        self, query: str, *, limit: int = 10
    ) -> list[tuple[KnowledgeDocument, float]]:
        """TF-IDF relevance search over the metadata corpus.

        Scores documents using term-frequency × inverse-document-frequency
        over pack name + description + tags + doc name + description + tags.
        Built using ``collections.Counter`` only — no external dependencies.

        Returns ``(doc, score)`` tuples above the 0.3 threshold, sorted
        descending by score. Only called when strict matching returns nothing
        — the resolver controls this fallback.
        """
        return self._tfidf.search(query, limit=limit, threshold=0.3)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_pack(self, pack_dir: Path) -> KnowledgePack | None:
        """Parse a single pack directory into a KnowledgePack.

        Graceful degradation:
        - Missing ``knowledge.yaml`` → name from directory, empty metadata,
          warning logged.
        - Missing ``name`` in manifest → name from directory.
        - Docs without frontmatter → name from filename, empty metadata.
        """
        manifest_path = pack_dir / "knowledge.yaml"
        if manifest_path.is_file():
            try:
                raw = manifest_path.read_text(encoding="utf-8")
                manifest = yaml.safe_load(raw) or {}
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("Failed to parse %s: %s", manifest_path, exc)
                manifest = {}
        else:
            logger.warning(
                "Pack directory %s has no knowledge.yaml — loading with degraded discoverability",
                pack_dir,
            )
            manifest = {}

        pack_name = str(manifest.get("name") or "").strip() or pack_dir.name
        description = str(manifest.get("description") or "").strip()
        tags = _normalise_tags(manifest.get("tags"))
        target_agents = _normalise_list_of_strings(manifest.get("target_agents"))
        default_delivery = str(manifest.get("default_delivery") or "reference").strip()

        pack = KnowledgePack(
            name=pack_name,
            description=description,
            source_path=pack_dir,
            tags=tags,
            target_agents=target_agents,
            default_delivery=default_delivery,
        )

        # Load documents — any .md file in the pack directory
        for md_path in sorted(pack_dir.glob("*.md")):
            doc = self._load_document(md_path, pack)
            if doc is not None:
                pack.documents.append(doc)

        return pack

    def _load_document(
        self, path: Path, pack: KnowledgePack
    ) -> KnowledgeDocument | None:
        """Parse a single .md file into a KnowledgeDocument.

        Content is NOT stored — only metadata from frontmatter is indexed.
        token_estimate is computed from the file size at this point.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read document %s: %s", path, exc)
            return None

        metadata, _body = parse_frontmatter(raw)

        if not metadata:
            logger.warning(
                "Document %s has no frontmatter — loading with empty metadata",
                path,
            )

        doc_name = str(metadata.get("name") or "").strip() or path.stem
        description = str(metadata.get("description") or "").strip()
        tags = _normalise_tags(metadata.get("tags"))
        grounding = str(metadata.get("grounding") or "").strip()
        priority = str(metadata.get("priority") or "normal").strip()
        token_estimate = _estimate_tokens(path)

        return KnowledgeDocument(
            name=doc_name,
            description=description,
            source_path=path,
            content="",          # lazy — not loaded at index time
            tags=tags,
            grounding=grounding,
            priority=priority,
            token_estimate=token_estimate,
        )

    def _rebuild_tfidf(self) -> None:
        """Rebuild the TF-IDF index from scratch using current packs."""
        self._tfidf = _TFIDFIndex()
        for pack in self._packs.values():
            for doc in pack.documents:
                self._tfidf.add(pack, doc)
