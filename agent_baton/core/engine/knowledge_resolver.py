"""Knowledge resolver — orchestration point between registry and dispatcher.

Takes a plan step's context and produces KnowledgeAttachment objects with
delivery decisions, running a 4-layer resolution pipeline with deduplication.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from agent_baton.models.knowledge import KnowledgeAttachment, KnowledgeDocument

if TYPE_CHECKING:
    from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
    from agent_baton.core.orchestration.registry import AgentRegistry

logger = logging.getLogger(__name__)

# Delivery thresholds
_DOC_TOKEN_CAP_DEFAULT = 8_000
_STEP_TOKEN_BUDGET_DEFAULT = 32_000
# Default on-disk size limit for inline delivery (bytes).  Documents whose
# source file exceeds this size are forced to reference delivery, regardless
# of their token estimate.  User-explicit attachments (Layer 1) are exempt.
_INLINE_BYTE_THRESHOLD_DEFAULT = 2_048

# Priority ordering: higher index = lower priority
_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}

# Stop-words excluded from keyword extraction
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "that", "this", "these", "those", "it", "its", "not", "no", "if",
    "then", "so", "up", "out", "about", "into", "over", "after", "before",
    "which", "who", "what", "how", "when", "where", "why", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "than",
    "too", "very",
})


def _extract_keywords(text: str, task_type: str | None = None) -> set[str]:
    """Extract meaningful keywords from text and optional task_type.

    Splits on non-alphanumeric characters, lower-cases, removes stop-words
    and single-character tokens. Also includes the task_type value itself.
    """
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    tokens -= _STOP_WORDS
    tokens = {t for t in tokens if len(t) > 1}

    if task_type:
        # task_type may be hyphenated (e.g. "bug-fix") — split on hyphens too
        for part in re.findall(r"[a-z0-9]+", task_type.lower()):
            if len(part) > 1 and part not in _STOP_WORDS:
                tokens.add(part)

    return tokens


def _sort_by_priority(
    docs: list[KnowledgeDocument],
) -> list[KnowledgeDocument]:
    """Return docs sorted high → normal → low by their priority field."""
    return sorted(docs, key=lambda d: _PRIORITY_ORDER.get(d.priority, 1))


def _make_grounding(doc: KnowledgeDocument, pack_name: str | None) -> str:
    """Return the doc's grounding string, or generate a default one."""
    if doc.grounding:
        return doc.grounding
    pname = pack_name or "unknown"
    return f"You are receiving `{doc.name}` from the `{pname}` pack: {doc.description}"


def _doc_path(doc: KnowledgeDocument) -> str:
    """Return a string filesystem path for a document."""
    if doc.source_path is not None:
        return str(doc.source_path)
    return ""


class KnowledgeResolver:
    """Resolve knowledge attachments for a plan step.

    Runs a 4-layer pipeline — explicit, agent-declared, planner-matched (strict),
    planner-matched (relevance fallback) — with deduplication across layers.
    Produces KnowledgeAttachment objects with inline/reference delivery decisions
    governed by a per-step token budget, per-doc token cap, and an on-disk byte
    size threshold.

    Args:
        registry: Loaded KnowledgeRegistry to resolve packs and documents from.
        agent_registry: Optional AgentRegistry used to look up agent-declared
            knowledge_packs. If None, the agent-declared layer is skipped.
        rag_available: When True, reference deliveries get retrieval="mcp-rag"
            instead of "file". Passed in from the planner after startup detection.
        step_token_budget: Total tokens available for inline delivery per step.
            Defaults to 32,000.
        doc_token_cap: Maximum token size for a document to be eligible for
            inline delivery. Documents larger than this are always referenced.
            Defaults to 8,000.
        inline_byte_threshold: Maximum on-disk file size (bytes) for inline
            delivery. Documents whose source file exceeds this size are forced to
            reference delivery regardless of token estimate.  Defaults to 2,048.
            User-explicit attachments (source="explicit", Layer 1) are exempt
            from this threshold so that ``--knowledge`` / ``--knowledge-pack``
            flags always inline regardless of document size.
            Pass ``2**30`` (or any very large value) to restore the old
            behaviour of inlining everything regardless of size.
    """

    def __init__(
        self,
        registry: KnowledgeRegistry,
        *,
        agent_registry: AgentRegistry | None = None,
        rag_available: bool = False,
        step_token_budget: int = _STEP_TOKEN_BUDGET_DEFAULT,
        doc_token_cap: int = _DOC_TOKEN_CAP_DEFAULT,
        inline_byte_threshold: int = _INLINE_BYTE_THRESHOLD_DEFAULT,
        telemetry: KnowledgeTelemetryStore | None = None,
        lifecycle: "object | None" = None,
    ) -> None:
        self._registry = registry
        self._agent_registry = agent_registry
        self._rag_available = rag_available
        self._step_token_budget = step_token_budget
        self._doc_token_cap = doc_token_cap
        self._inline_byte_threshold = inline_byte_threshold
        # Optional side-channel for F0.4 lifecycle telemetry. Failures here
        # MUST never crash production code — telemetry is best-effort only.
        self._telemetry = telemetry
        # Optional KnowledgeLifecycle (K2.3) — when provided, every resolved
        # attachment triggers a record_usage call so the lifecycle table
        # tracks freshness without forcing a hard dependency on storage in
        # tests that build a resolver against a stub registry.
        self._lifecycle = lifecycle

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        *,
        agent_name: str,
        task_description: str,
        task_type: str | None = None,
        risk_level: str = "LOW",
        explicit_packs: list[str] | None = None,
        explicit_docs: list[str] | None = None,
        already_delivered: dict[str, str] | None = None,
        task_id: str = "",
        step_id: str = "",
    ) -> list[KnowledgeAttachment]:
        """Resolve knowledge attachments for a single plan step.

        Runs the 4-layer pipeline in order. Documents encountered in earlier
        layers are skipped in later layers (deduplication by source_path,
        falling back to doc name + pack name).

        Session-level deduplication: if *already_delivered* is provided, any
        doc whose dedup key appears in that dict will be forced to
        ``delivery="reference"`` with a note pointing back to the step where
        it was first inlined — **unless** the doc is from the explicit layer
        (layer 1), which always inlines regardless of prior delivery.

        Args:
            agent_name: Name of the agent being dispatched.
            task_description: Human-readable task description for this step.
            task_type: Optional inferred task type (e.g. "bug-fix", "feature").
            risk_level: Risk classification of the plan (LOW/MEDIUM/HIGH/CRITICAL).
            explicit_packs: Pack names explicitly passed via --knowledge-pack.
            explicit_docs: Document file paths explicitly passed via --knowledge.
            already_delivered: Mapping of doc-key → step_id for docs already
                inlined in this execution run.  Docs in this map (that are not
                from the explicit layer) are downgraded to reference delivery.

        Returns:
            Ordered list of KnowledgeAttachment objects with delivery decisions
            applied. Priority within each layer is high → normal → low.
        """
        prior: dict[str, str] = already_delivered or {}

        # Mutable per-resolve state
        seen: set[str] = set()           # dedup keys: "{pack_name}::{doc_name}"
        remaining_budget = self._step_token_budget
        attachments: list[KnowledgeAttachment] = []

        # --- Layer 1: Explicit -----------------------------------------------
        # Explicit (user-supplied) docs always inline — never downgraded.
        layer1_docs = self._resolve_explicit_layer(
            explicit_packs or [], explicit_docs or []
        )
        for doc, pack_name, source in layer1_docs:
            key = self._dedup_key(doc, pack_name)
            if key in seen:
                continue
            seen.add(key)
            attachment, remaining_budget = self._make_attachment(
                doc, pack_name, source, remaining_budget
            )
            attachments.append(attachment)

        # --- Layer 2: Agent-declared -----------------------------------------
        layer2_docs = self._resolve_agent_declared_layer(agent_name)
        for doc, pack_name, source in layer2_docs:
            key = self._dedup_key(doc, pack_name)
            if key in seen:
                continue
            seen.add(key)
            attachment, remaining_budget = self._make_attachment(
                doc, pack_name, source, remaining_budget,
                prior_step_id=prior.get(key),
            )
            attachments.append(attachment)

        # --- Layer 3: Planner-matched (strict tag) ---------------------------
        keywords = _extract_keywords(task_description, task_type)
        layer3_docs = self._resolve_tag_layer(keywords)
        for doc, pack_name, source in layer3_docs:
            key = self._dedup_key(doc, pack_name)
            if key in seen:
                continue
            seen.add(key)
            attachment, remaining_budget = self._make_attachment(
                doc, pack_name, source, remaining_budget,
                prior_step_id=prior.get(key),
            )
            attachments.append(attachment)

        # --- Layer 4: Planner-matched (relevance fallback) -------------------
        # Only fires when strict tag matching produced nothing at layer 3.
        if not layer3_docs:
            layer4_docs = self._resolve_relevance_layer(
                task_description, task_type
            )
            for doc, pack_name, source in layer4_docs:
                key = self._dedup_key(doc, pack_name)
                if key in seen:
                    continue
                seen.add(key)
                attachment, remaining_budget = self._make_attachment(
                    doc, pack_name, source, remaining_budget,
                    prior_step_id=prior.get(key),
                )
                attachments.append(attachment)

        # F0.4 telemetry: record each resolved attachment as a KnowledgeUsed
        # event.  Best-effort — never raise from a telemetry failure.
        if self._telemetry is not None:
            self._emit_used_events(attachments, task_id=task_id, step_id=step_id)

        # K2.3: bump usage counters for every resolved attachment.  Failures
        # here must never break resolution — lifecycle tracking is best-effort.
        if self._lifecycle is not None:
            for att in attachments:
                pack = getattr(att, "pack_name", "") or ""
                doc = getattr(att, "document_name", "") or ""
                if not doc:
                    continue
                kid = f"{pack}/{doc}" if pack else doc
                try:
                    self._lifecycle.record_usage(kid)
                except Exception as exc:  # noqa: BLE001 — never break resolve
                    logger.debug("record_usage(%s) failed: %s", kid, exc)

        return attachments

    def _emit_used_events(
        self,
        attachments: list[KnowledgeAttachment],
        *,
        task_id: str,
        step_id: str,
    ) -> None:
        """Best-effort emission of KnowledgeUsed telemetry rows.

        Wrapped in a broad exception handler so transient sqlite errors,
        missing tables, or filesystem permission issues never propagate to
        the resolver caller (which is on the dispatch hot path).
        """
        if self._telemetry is None:
            return
        for att in attachments:
            try:
                self._telemetry.record_used(
                    doc_name=att.document_name,
                    pack_name=att.pack_name or "",
                    task_id=task_id,
                    step_id=step_id,
                    delivery=att.delivery,
                )
            except Exception as exc:  # noqa: BLE001 — telemetry must not crash dispatch
                logger.debug(
                    "KnowledgeTelemetry.record_used failed for %s/%s: %s",
                    att.pack_name, att.document_name, exc,
                )

    # ------------------------------------------------------------------
    # Layer implementations
    # ------------------------------------------------------------------

    def _resolve_explicit_layer(
        self,
        explicit_packs: list[str],
        explicit_docs: list[str],
    ) -> list[tuple[KnowledgeDocument, str | None, str]]:
        """Layer 1: resolve user-supplied pack names and doc file paths.

        Explicit packs: resolved by name → all their documents (sorted by priority).
        Explicit docs: resolved by file path — locate the document in the registry
            by matching source_path. If not found in registry, create a minimal stub.

        Returns list of (doc, pack_name, source) triples.
        """
        results: list[tuple[KnowledgeDocument, str | None, str]] = []

        # Explicit packs — all docs in those packs
        for pack_name in explicit_packs:
            pack = self._registry.get_pack(pack_name)
            if pack is None:
                logger.warning(
                    "Explicit pack %r not found in registry — skipping", pack_name
                )
                continue
            for doc in _sort_by_priority(pack.documents):
                results.append((doc, pack.name, "explicit"))

        # Explicit docs — look up by path in the registry, or build stubs
        for doc_path in explicit_docs:
            found = self._find_doc_by_path(doc_path)
            if found is not None:
                doc, pack_name = found
                results.append((doc, pack_name, "explicit"))
            else:
                # Not in registry — build a minimal stub so it still gets attached
                from pathlib import Path as _Path
                stub = KnowledgeDocument(
                    name=_Path(doc_path).stem,
                    description="",
                    source_path=_Path(doc_path),
                    token_estimate=0,
                )
                results.append((stub, None, "explicit"))

        return results

    def _resolve_agent_declared_layer(
        self, agent_name: str
    ) -> list[tuple[KnowledgeDocument, str | None, str]]:
        """Layer 2: resolve packs declared in the agent's frontmatter.

        Looks up the AgentDefinition in the AgentRegistry (if provided), reads
        its knowledge_packs list, and resolves each pack name. Falls back to
        KnowledgeRegistry.packs_for_agent() if agent_registry is None or the
        agent isn't found.
        """
        results: list[tuple[KnowledgeDocument, str | None, str]] = []
        pack_names: list[str] = []

        if self._agent_registry is not None:
            agent_def = self._agent_registry.get(agent_name)
            if agent_def is not None:
                pack_names = list(agent_def.knowledge_packs)
            else:
                logger.debug(
                    "Agent %r not found in agent_registry — skipping agent-declared layer",
                    agent_name,
                )
                return results
        else:
            # No AgentRegistry available — skip this layer
            return results

        for pack_name in pack_names:
            pack = self._registry.get_pack(pack_name)
            if pack is None:
                logger.debug(
                    "Agent-declared pack %r not found in knowledge registry — skipping",
                    pack_name,
                )
                continue
            for doc in _sort_by_priority(pack.documents):
                results.append((doc, pack.name, "agent-declared"))

        return results

    def _resolve_tag_layer(
        self, keywords: set[str]
    ) -> list[tuple[KnowledgeDocument, str | None, str]]:
        """Layer 3: strict tag/keyword matching.

        Uses KnowledgeRegistry.find_by_tags() with the extracted keyword set.
        The source tag is 'planner-matched:tag'.

        Returns list of (doc, pack_name, source) sorted by priority.
        """
        if not keywords:
            return []

        matched_docs = self._registry.find_by_tags(keywords)
        # find_by_tags returns KnowledgeDocument objects — we need pack_name too
        results: list[tuple[KnowledgeDocument, str | None, str]] = []
        for doc in matched_docs:
            pack_name = self._find_pack_for_doc(doc)
            results.append((doc, pack_name, "planner-matched:tag"))

        # Sort by priority before returning
        results.sort(key=lambda t: _PRIORITY_ORDER.get(t[0].priority, 1))
        return results

    def _resolve_relevance_layer(
        self, task_description: str, task_type: str | None
    ) -> list[tuple[KnowledgeDocument, str | None, str]]:
        """Layer 4: relevance fallback — only called when Layer 3 returns nothing.

        When rag_available=True, would query an MCP RAG server (not implemented
        in this version — falls through to TF-IDF). Otherwise calls
        registry.search() with the combined query string.

        Returns list of (doc, pack_name, source) sorted by priority.
        """
        query_parts = [task_description]
        if task_type:
            query_parts.append(task_type)
        query = " ".join(query_parts)

        if self._rag_available:
            # RAG path: retrieval hint will be set to "mcp-rag" in _make_attachment.
            # The actual RAG query is not implemented here — the registry's TF-IDF
            # is used as the offline fallback. Future: replace with MCP call.
            logger.debug(
                "rag_available=True but MCP RAG client not implemented; "
                "falling back to TF-IDF for relevance layer"
            )

        search_results = self._registry.search(query)

        results: list[tuple[KnowledgeDocument, str | None, str]] = []
        for doc, _score in search_results:
            pack_name = self._find_pack_for_doc(doc)
            results.append((doc, pack_name, "planner-matched:relevance"))

        # Sort by priority before returning
        results.sort(key=lambda t: _PRIORITY_ORDER.get(t[0].priority, 1))
        return results

    # ------------------------------------------------------------------
    # Delivery decision
    # ------------------------------------------------------------------

    def _make_attachment(
        self,
        doc: KnowledgeDocument,
        pack_name: str | None,
        source: str,
        remaining_budget: int,
        *,
        prior_step_id: str | None = None,
    ) -> tuple[KnowledgeAttachment, int]:
        """Apply delivery decision and return (attachment, updated_remaining_budget).

        Delivery rules:
        - prior_step_id is set (session dedup, non-explicit layer): reference
          with a note pointing to the prior inline delivery step.
        - token_estimate <= 0:                           reference (unestimated)
        - token_estimate > doc_token_cap:                reference (over per-doc cap)
        - token_estimate <= remaining_budget:            inline (fits budget)
        - otherwise:                                     reference (budget exhausted)

        The retrieval hint is "mcp-rag" for reference deliveries when
        rag_available=True, otherwise "file".
        """
        estimate = doc.token_estimate
        grounding = _make_grounding(doc, pack_name)

        # Session-level deduplication: doc was already inlined in a prior step.
        # Force reference delivery and annotate the grounding with a retrieval note.
        if prior_step_id is not None:
            path_hint = _doc_path(doc)
            dedup_note = (
                f"(previously delivered inline in step {prior_step_id}"
                f" — re-read from: {path_hint})"
            )
            grounding = f"{grounding} {dedup_note}".strip()
            retrieval = "mcp-rag" if self._rag_available else "file"
            return KnowledgeAttachment(
                source=source,
                pack_name=pack_name,
                document_name=doc.name,
                path=path_hint,
                delivery="reference",
                retrieval=retrieval,
                grounding=grounding,
                token_estimate=estimate,
            ), remaining_budget

        # Byte-size threshold check (skipped for explicit/user-forced attachments).
        # We check on-disk file size first — it's a cheap stat() call and catches
        # large docs before the token-budget accounting even runs.
        if source != "explicit" and self._inline_byte_threshold < 2 ** 30:
            _file_size: int | None = None
            if doc.source_path is not None:
                try:
                    _file_size = doc.source_path.stat().st_size
                except OSError:
                    pass
            if _file_size is not None and _file_size > self._inline_byte_threshold:
                retrieval = "mcp-rag" if self._rag_available else "file"
                return KnowledgeAttachment(
                    source=source,
                    pack_name=pack_name,
                    document_name=doc.name,
                    path=_doc_path(doc),
                    delivery="reference",
                    retrieval=retrieval,
                    grounding=grounding,
                    token_estimate=estimate,
                ), remaining_budget

        if estimate <= 0 or estimate > self._doc_token_cap:
            delivery = "reference"
        elif estimate <= remaining_budget:
            delivery = "inline"
            remaining_budget -= estimate
        else:
            delivery = "reference"

        retrieval = "mcp-rag" if (self._rag_available and delivery == "reference") else "file"

        attachment = KnowledgeAttachment(
            source=source,
            pack_name=pack_name,
            document_name=doc.name,
            path=_doc_path(doc),
            delivery=delivery,
            retrieval=retrieval,
            grounding=grounding,
            token_estimate=estimate,
        )
        return attachment, remaining_budget

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dedup_key(self, doc: KnowledgeDocument, pack_name: str | None) -> str:
        """Return a stable deduplication key for a (pack, doc) pair.

        Prefers source_path for precision (two docs with the same name in
        different packs are distinct). Falls back to pack_name::doc_name.
        """
        if doc.source_path is not None:
            return str(doc.source_path)
        return f"{pack_name or ''}::{doc.name}"

    def _find_pack_for_doc(self, doc: KnowledgeDocument) -> str | None:
        """Look up the pack name that contains a given KnowledgeDocument.

        Uses identity comparison (is) so we find the exact object that came
        from the registry, not a copy.
        """
        for pack in self._registry.all_packs.values():
            for pack_doc in pack.documents:
                if pack_doc is doc:
                    return pack.name
        return None

    def _find_doc_by_path(
        self, path_str: str
    ) -> tuple[KnowledgeDocument, str | None] | None:
        """Find a KnowledgeDocument in the registry by its filesystem path.

        Returns (doc, pack_name) if found, otherwise None.
        """
        from pathlib import Path as _Path
        target = _Path(path_str).resolve()
        for pack in self._registry.all_packs.values():
            for doc in pack.documents:
                if doc.source_path is not None:
                    try:
                        if doc.source_path.resolve() == target:
                            return doc, pack.name
                    except OSError:
                        pass
        return None
