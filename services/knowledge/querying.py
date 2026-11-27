# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Tool and passive knowledge queries over the service's current snapshots.

QueryEngine owns candidate selection and reference rendering. KnowledgeService
continues to own the registry, vectors, embedder, calibration, thread pools and
query leases. Access to that owner is explicit: no snapshot is copied into the
engine and no second lock, worker or persistent state is introduced here.
Embedding admission and snapshot compatibility stay with the runtime; this
module only requests work after candidate eligibility and applies its budget.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

from config.turn_context_settings import TURN_CONTEXT_ITEM_MAX_TOKENS, TURN_CONTEXT_QUERY_MAX_CHARS
from .chunking import MAX_HEADER_CHARS, chunk_bodies
from .models import MATERIAL_TYPES
from .passive import PASSIVE_SEMANTIC_CANDIDATES, PassiveHit, select_hits, thresholds_for
from .registry import Registry
from .render import RenderCard, render_reference_block, render_turn_cards
from .retrieval import LEXICAL_CANDIDATES, RankedHit, SemanticMatch, best_excerpt_index, fuse, semantic_candidates
from .store import StoredEntry, VectorSnapshot, normalize_vector
from .text import title_key

if TYPE_CHECKING:
    from .service import KnowledgeService

logger = logging.getLogger("N.E.K.O.Knowledge")

DEFAULT_QUERY_BUDGET_MS = 1_500
MAX_QUERY_BUDGET_MS = 5_000
QUERY_RENDER_RESERVE_SECONDS = 0.15
MAX_QUERY_EMBEDDINGS = 2
MAX_QUERY_CHARS = 2_000
MAX_QUERY_LIMIT = 10
PASSIVE_DIAGNOSTIC_RESULTS = frozenset({"matched", "timeout", "busy", "error"})


def _disabled_in(entry: StoredEntry, registry: Registry) -> bool:
    """Disabled per the index or per the query's registry snapshot.

    A toggle writes the index before the registry; whichever says "disabled"
    wins, so re-enabling never shows an entry before the registry agrees.
    """
    record = registry.packs.get(entry.pack_id)
    return entry.disabled or (record is not None and title_key(entry.title) in record.disabled_titles)


class QueryEngine:
    """Read one runtime owner while each request retains its registry snapshot."""

    def __init__(self, runtime: KnowledgeService) -> None:
        self._runtime = runtime

    def _allowed_pack_ids(self, registry: Registry, material_type: str) -> list[str]:
        wanted = MATERIAL_TYPES if material_type in ("", "auto", "all") else (material_type,)
        return [
            record.pack_id
            for record in registry.packs.values()
            if record.enabled and record.effective_material_type in wanted and record.pack_id not in self._runtime._broken_packs
        ]

    async def query(
        self,
        *,
        query: str,
        mode: str = "lookup",
        material_type: str = "auto",
        limit: int = 3,
        budget_ms: int = DEFAULT_QUERY_BUDGET_MS,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Look up or sample entries; the result never raises.

        ``result`` is one of matched / miss / timeout / busy / error / disabled
        / unavailable, and ``context`` is the fenced reference block (empty
        unless matched).
        """
        started = time.monotonic()
        mode = mode if mode in ("lookup", "sample") else "lookup"
        query = str(query or "").strip()[:MAX_QUERY_CHARS]
        limit = min(max(int(limit or 1), 1), MAX_QUERY_LIMIT)
        budget = min(max(int(budget_ms or DEFAULT_QUERY_BUDGET_MS), 50), MAX_QUERY_BUDGET_MS) / 1000

        def finish(result: str, **extra: Any) -> dict[str, Any]:
            elapsed = int((time.monotonic() - started) * 1000)
            hits = extra.get("hits") or []
            self._runtime.diagnostics.record_query(
                mode=mode,
                result=result,
                retrieval_mode=extra.get("retrieval_mode", ""),
                hits=len(hits),
                # Imported entry titles can contain original filenames. Keep
                # diagnostics to structural counts and opaque pack identities.
                entry_title="",
                pack_id=hits[0]["pack_id"] if hits else "",
                elapsed_ms=elapsed,
                error_type=extra.get("error_type", ""),
            )
            return {
                "ok": True,
                "result": result,
                "context": extra.get("context", ""),
                "hits": hits,
                "card_ids": [f"{hit['pack_id']}/{title_key(hit['title'])}" for hit in hits],
                "retrieval_mode": extra.get("retrieval_mode", ""),
                "elapsed_ms": elapsed,
            }

        if self._runtime._state != "ready":
            return finish("unavailable")
        if not self._runtime._registry.enabled:
            return finish("disabled")
        if not query:
            return finish("miss")
        # One registry snapshot decides eligibility, vector policy and the
        # labels in the rendered cards, even if a policy changes mid-query.
        registry = self._runtime._registry
        allowed = self._allowed_pack_ids(registry, str(material_type or "auto"))
        if not allowed:
            return finish("miss")
        if self._runtime._query_slots.locked():
            return finish("busy")
        async with self._runtime._query_lease():
            deadline = started + budget
            try:
                async with asyncio.timeout(max(deadline - time.monotonic(), 0.01)):
                    # A pack being replaced (rows newer than this snapshot)
                    # would take sample picks or candidate slots and then be
                    # dropped at rendering, hiding matches of other packs.
                    current = await self._runtime._query_thread(self._runtime._current_packs, allowed, registry)
                    allowed = [pack_id for pack_id in allowed if pack_id in current]
                    if not allowed:
                        return finish("miss")
                    if mode == "sample":
                        ranked = await self._sample(query, allowed, limit, registry)
                        retrieval_mode = "sample"
                    else:
                        ranked, retrieval_mode = await self._lookup(
                            query, allowed, limit, deadline, registry
                        )
                    if not ranked:
                        return finish("miss", retrieval_mode=retrieval_mode)
                    excerpt_query = query if mode == "lookup" else ""
                    hits, context = await self._runtime._query_thread(
                        self._render, ranked, excerpt_query, language, registry
                    )
            except TimeoutError:
                return finish("timeout")
            except Exception as exc:
                logger.warning("[Knowledge] query failed: %s", type(exc).__name__)
                return finish("error", error_type=type(exc).__name__)
        if not hits:
            return finish("miss", retrieval_mode=retrieval_mode)
        return finish("matched", hits=hits, context=context, retrieval_mode=retrieval_mode)

    async def turn_context(
        self,
        *,
        text: str,
        exclude_card_ids: Sequence[str] = (),
        budget_ms: int = DEFAULT_QUERY_BUDGET_MS,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Cards a chat message brings in unasked (passive turn context).

        Only packs whose ``auto_context`` is on take part, under the global
        switch. Knowledge and corpus are chosen separately (``passive``) and
        returned as two sections of bare cards for Main to wrap:
        ``{"knowledge": {"context", "card_ids", "tokens"}, "corpus": {...}}``.
        ``exclude_card_ids`` are cards the session still carries; they are
        left out before anything is ranked. Nothing about the session is kept
        here. The result never raises.
        """
        started = time.monotonic()
        text = str(text or "").strip()[:TURN_CONTEXT_QUERY_MAX_CHARS]
        budget = min(max(int(budget_ms or DEFAULT_QUERY_BUDGET_MS), 50), MAX_QUERY_BUDGET_MS) / 1000
        excluded = {str(card_id) for card_id in exclude_card_ids or ()}

        def finish(result: str, **extra: Any) -> dict[str, Any]:
            elapsed = int((time.monotonic() - started) * 1000)
            sections = {
                kind: extra.get(kind) or {"context": "", "card_ids": [], "tokens": 0}
                for kind in MATERIAL_TYPES
            }
            hits = sum(len(section["card_ids"]) for section in sections.values())
            # Runs on every text turn: only outcomes worth a look are kept,
            # so routine misses do not push tool lookups out of the log.
            if result in PASSIVE_DIAGNOSTIC_RESULTS:
                self._runtime.diagnostics.record_query(
                    mode="passive",
                    result=result,
                    retrieval_mode=extra.get("retrieval_mode", ""),
                    hits=hits,
                    entry_title="",
                    pack_id="",
                    elapsed_ms=elapsed,
                    error_type=extra.get("error_type", ""),
                )
            return {
                "ok": True,
                "result": result,
                **sections,
                "retrieval_mode": extra.get("retrieval_mode", ""),
                "elapsed_ms": elapsed,
            }

        if self._runtime._state != "ready":
            return finish("unavailable")
        if not self._runtime._registry.enabled:
            return finish("disabled")
        if not text:
            return finish("miss")
        registry = self._runtime._registry
        allowed = [
            pack_id
            for pack_id in self._allowed_pack_ids(registry, "auto")
            if (record := registry.packs.get(pack_id)) is not None and record.auto_context
        ]
        if not allowed:
            return finish("off")
        if self._runtime._query_slots.locked():
            return finish("busy")
        async with self._runtime._query_lease():
            deadline = started + budget
            try:
                async with asyncio.timeout(max(deadline - time.monotonic(), 0.01)):
                    current = await self._runtime._query_thread(self._runtime._current_packs, allowed, registry)
                    allowed = [pack_id for pack_id in allowed if pack_id in current]
                    if not allowed:
                        return finish("miss")
                    picked, retrieval_mode = await self._passive_pick(
                        text, allowed, deadline, registry, excluded
                    )
                    if not any(picked.values()):
                        return finish("miss", retrieval_mode=retrieval_mode)
                    sections = await self._runtime._query_thread(
                        self._render_turn_sections, picked, text, language, registry
                    )
            except TimeoutError:
                return finish("timeout")
            except Exception as exc:
                logger.warning("[Knowledge] turn context failed: %s", type(exc).__name__)
                return finish("error", error_type=type(exc).__name__)
        if not any(section["card_ids"] for section in sections.values()):
            return finish("miss", retrieval_mode=retrieval_mode)
        return finish("matched", retrieval_mode=retrieval_mode, **sections)

    async def _passive_pick(
        self,
        text: str,
        allowed: list[str],
        deadline: float,
        registry: Registry,
        excluded: set[str],
    ) -> tuple[dict[str, list[PassiveHit]], str]:
        """Candidates from all three signals, judged per material type."""
        model_id = self._runtime._current_model_id()
        # Read together, with no await between: the generation names this
        # very snapshot in the calibration cache.
        snapshot, snapshot_key = self._runtime._vector_snapshot()
        snapshot = await self._runtime._compatible_vector_snapshot(snapshot)
        vector_packs = [
            pack_id
            for pack_id in allowed
            if (record := registry.packs.get(pack_id)) is not None
            and record.local_embedding
            and (
                snapshot is None
                or snapshot.pack_versions is None
                or snapshot.pack_versions.get(pack_id, record.pack_sha256) == record.pack_sha256
            )
        ]
        type_packs = {
            material_type: [
                pack_id
                for pack_id in allowed
                if registry.packs[pack_id].effective_material_type == material_type
            ]
            for material_type in MATERIAL_TYPES
        }
        embed_task: asyncio.Task[Any] | None = None
        if (
            model_id is not None
            and snapshot is not None
            and snapshot.model_id == model_id
            and set(vector_packs) & set(snapshot.pack_ids)
        ):
            embed_task = self._runtime._start_query_embedding(
                text, limit=MAX_QUERY_EMBEDDINGS, probe_model_id=model_id,
            )
        # Cards the session still carries are left out before any cut, so
        # they cannot take the slots of the next-best cards. Disabled cards
        # stay out of the vector cut and the calibration background too.
        excluded_ids, disabled_ids, exact_ids, lexical_ids, named_ids = await self._runtime._query_thread(
            self._passive_lexical_search, text, allowed, registry, excluded
        )
        query_vector: np.ndarray | None = None
        if embed_task is not None:
            remaining = deadline - time.monotonic()
            done, _pending = await asyncio.wait(
                {embed_task}, timeout=max(remaining - QUERY_RENDER_RESERVE_SECONDS, 0)
            )
            if embed_task in done and embed_task.exception() is None and embed_task.result():
                blob = normalize_vector(embed_task.result())
                if blob is not None:
                    query_vector = np.frombuffer(blob, dtype="<f4")
        # Each material type gets its own candidate cut, so many close cards
        # of one type never push the other type's leader out.
        semantic_by_type: dict[str, list[SemanticMatch]] = {}
        if query_vector is not None:
            vector_excluded = excluded_ids | disabled_ids
            # One hop per type: a query cancelled or timed out between them
            # does not go on scanning the other type while holding its slot.
            for material_type, packs in type_packs.items():
                type_vector_packs = [pack_id for pack_id in vector_packs if pack_id in packs]
                if type_vector_packs:
                    semantic_by_type[material_type] = await self._runtime._query_thread(
                        self._passive_semantic_search, snapshot, query_vector,
                        type_vector_packs, vector_excluded,
                    )
        semantic = [match for matches in semantic_by_type.values() for match in matches]
        candidate_ids = list(dict.fromkeys(
            [*exact_ids, *named_ids, *lexical_ids, *(m.entry_id for m in semantic)]
        ))
        entries = await self._runtime._query_thread(self._runtime._store.fetch_entries, candidate_ids)
        allowed_set = await self._runtime._query_thread(self._runtime._current_packs, allowed, registry)
        picked: dict[str, list[PassiveHit]] = {}
        for material_type in MATERIAL_TYPES:
            type_set = {pack_id for pack_id in type_packs[material_type] if pack_id in allowed_set}
            if not type_set:
                picked[material_type] = []
                continue
            usable = {
                entry_id
                for entry_id, entry in entries.items()
                if entry.pack_id in type_set
                and not _disabled_in(entry, registry)
                and f"{entry.pack_id}/{title_key(entry.title)}" not in excluded
            }
            type_vector_packs = [pack_id for pack_id in vector_packs if pack_id in type_set]
            background = (
                self._runtime._calibration.background(
                    snapshot, snapshot_key, type_vector_packs, material_type, self._runtime._thread,
                    excluded_entry_ids=disabled_ids,
                )
                if query_vector is not None and type_vector_packs
                else None
            )
            picked[material_type] = select_hits(
                text,
                entries=entries,
                usable=usable,
                exact_ids=[entry_id for entry_id in exact_ids if entry_id in usable],
                named_ids=[entry_id for entry_id in named_ids if entry_id in usable],
                lexical_ids=[entry_id for entry_id in lexical_ids if entry_id in usable],
                semantic=[
                    match for match in semantic_by_type.get(material_type, ())
                    if match.entry_id in usable
                ],
                thresholds=thresholds_for(material_type, background),
            )
        return picked, ("hybrid" if query_vector is not None else "bm25")

    def _excluded_entry_ids(self, card_ids: set[str]) -> set[int]:
        """Entry ids of ``pack_id/title_key`` card ids."""
        titles: dict[str, list[str]] = {}
        for card_id in card_ids:
            pack_id, _, key = card_id.partition("/")
            if pack_id and key:
                titles.setdefault(pack_id, []).append(key)
        return self._runtime._store.entry_ids_by_title(titles) if titles else set()

    def _passive_lexical_search(
        self, text: str, pack_ids: list[str], registry: Registry, excluded_cards: set[str]
    ) -> tuple[set[int], set[int], list[int], list[int], list[int]]:
        """Exclusions and keyword candidates in one worker-thread hop.

        Returns the entry ids of ``excluded_cards``, the disabled entry ids of
        ``pack_ids`` (index and registry snapshot), then the exact, keyword
        and named candidates.
        """
        excluded_ids = self._excluded_entry_ids(excluded_cards)
        snapshot_disabled = self._snapshot_only_disabled(pack_ids, registry)
        disabled_ids = self._runtime._store.disabled_entry_ids(pack_ids) | snapshot_disabled
        # Disabled index rows are filtered in SQL already.
        excluded = excluded_ids | snapshot_disabled
        exact_ids, lexical_ids = self._runtime._store.lexical_candidates(
            text, pack_ids=pack_ids, limit=LEXICAL_CANDIDATES, exclude_ids=excluded,
        )
        named_ids = self._runtime._store.named_candidates(text, pack_ids=pack_ids, exclude_ids=excluded)
        return excluded_ids, disabled_ids, exact_ids, lexical_ids, named_ids

    def _passive_semantic_search(
        self,
        snapshot: VectorSnapshot | None,
        query_vector: np.ndarray,
        pack_ids: list[str],
        excluded_ids: set[int],
    ) -> list[SemanticMatch]:
        """Vector candidates of one material type, with its own cut so many
        close cards of one type never push the other type's leader out."""
        return semantic_candidates(
            snapshot,
            query_vector,
            allowed_pack_ids=pack_ids,
            exclude_entry_ids=excluded_ids,
            limit=PASSIVE_SEMANTIC_CANDIDATES,
            threshold=-1.0,
        )

    def _render_turn_sections(
        self,
        picked: dict[str, list[PassiveHit]],
        text: str,
        language: str | None,
        registry: Registry,
    ) -> dict[str, dict[str, Any]]:
        ids = [hit.entry_id for hits in picked.values() for hit in hits]
        entries = self._runtime._store.fetch_entries(ids)
        current = self._runtime._current_packs([entry.pack_id for entry in entries.values()], registry)
        sections: dict[str, dict[str, Any]] = {}
        for material_type in MATERIAL_TYPES:
            cards: list[RenderCard] = []
            card_ids: list[str] = []
            for hit in picked.get(material_type, ()):
                entry = entries.get(hit.entry_id)
                record = registry.packs.get(entry.pack_id) if entry is not None else None
                if entry is None or record is None or _disabled_in(entry, registry) or entry.pack_id not in current:
                    continue
                bodies = self._excerpt_bodies(entry, hit.semantic_chunk)
                index = best_excerpt_index(text, bodies, hit.semantic_chunk)
                excerpt = bodies[index] if index == 0 else f"... {bodies[index]}"
                cards.append(
                    RenderCard(
                        title=entry.title,
                        material_type=material_type,
                        summary=entry.summary,
                        content=excerpt,
                        source_name=record.source.name,
                        source_license=record.source.license,
                    )
                )
                card_ids.append(f"{entry.pack_id}/{title_key(entry.title)}")
            context, tokens = render_turn_cards(
                cards, language=language, max_tokens_per_card=TURN_CONTEXT_ITEM_MAX_TOKENS
            )
            sections[material_type] = {"context": context, "card_ids": card_ids, "tokens": tokens}
        return sections

    async def _sample(
        self, tag: str, allowed: list[str], limit: int, registry: Registry
    ) -> list[RankedHit]:
        rows = await self._runtime._query_thread(self._runtime._store.entries_with_tag, tag, allowed)
        # Read the versions after the rows: a pack replaced since the query's
        # version check has its new rows here, which would take picks and then
        # be dropped at rendering. Choose among unchanged packs only.
        current = await self._runtime._query_thread(self._runtime._current_packs, allowed, registry)
        # Drop entries the query's registry snapshot disables before choosing,
        # or one of them could take a slot and then be rejected at rendering.
        ids = [
            entry_id
            for entry_id, pack_id, title in rows
            if pack_id in current
            and (
                (record := registry.packs.get(pack_id)) is None
                or title_key(title) not in record.disabled_titles
            )
        ]
        return [
            RankedHit(entry_id=i, score=0.0, exact=False, lexical_rank=None, semantic_score=None)
            for i in random.sample(ids, min(limit, len(ids)))
        ]

    async def _lookup(
        self, query: str, allowed: list[str], limit: int, deadline: float, registry: Registry
    ) -> tuple[list[RankedHit], str]:
        embed_task: asyncio.Task[Any] | None = None
        model_id = self._runtime._current_model_id()
        snapshot, _snapshot_key = self._runtime._vector_snapshot()
        snapshot = await self._runtime._compatible_vector_snapshot(snapshot)
        # A pack with local vectors turned off is keyword-only, even if
        # vectors from before the switch are still in the snapshot; when no
        # eligible pack has vectors, the query is not embedded at all.
        vector_packs = [
            pack_id
            for pack_id in allowed
            if (record := registry.packs.get(pack_id)) is not None
            and record.local_embedding
            # Vectors read for another version of the pack (it was replaced
            # and the snapshot is still being reloaded) point at entry ids
            # that no longer exist: they would fill the semantic cutoff and
            # then be dropped. Until the reload the pack is keyword-only.
            and (
                snapshot is None
                or snapshot.pack_versions is None
                or snapshot.pack_versions.get(pack_id, record.pack_sha256) == record.pack_sha256
            )
        ]
        if (
            model_id is not None
            and snapshot is not None
            and snapshot.model_id == model_id
            and set(vector_packs) & set(snapshot.pack_ids)
        ):
            # The runtime retains timed-out embeddings and enforces this cap.
            embed_task = self._runtime._start_query_embedding(query, limit=MAX_QUERY_EMBEDDINGS)
        exact_ids, lexical_ids = await self._runtime._query_thread(
            self._lexical_search, query, allowed, registry
        )
        query_vector: np.ndarray | None = None
        if embed_task is not None:
            remaining = deadline - time.monotonic()
            # Leave room for fetching and rendering the cards; a late vector
            # is dropped and the lookup goes on with BM25 alone.
            done, _pending = await asyncio.wait(
                {embed_task}, timeout=max(remaining - QUERY_RENDER_RESERVE_SECONDS, 0)
            )
            if embed_task in done and embed_task.exception() is None and embed_task.result():
                blob = normalize_vector(embed_task.result())
                if blob is not None:
                    query_vector = np.frombuffer(blob, dtype="<f4")
        for attempt in range(2):
            if attempt:
                exact_ids, lexical_ids = await self._runtime._query_thread(
                    self._lexical_search, query, allowed, registry
                )
            semantic = (
                await self._runtime._query_thread(
                    self._semantic_search, snapshot, query_vector, vector_packs, registry
                )
                if query_vector is not None and vector_packs
                else []
            )
            candidate_ids = list(
                dict.fromkeys([*exact_ids, *lexical_ids, *(m.entry_id for m in semantic)])
            )
            entries = await self._runtime._query_thread(self._runtime._store.fetch_entries, candidate_ids)
            # Read the versions after the rows: rows of a pack replaced in
            # between then show a newer version and are left out, never
            # mislabelled.
            allowed_set = await self._runtime._query_thread(self._runtime._current_packs, allowed, registry)
            if attempt or not allowed_set or set(allowed) <= allowed_set:
                break
            # A pack was replaced while candidates were chosen: its rows (or
            # its stale vectors) may have pushed valid matches of other packs
            # below the cutoffs. Choose once more without it.
            allowed = [pack_id for pack_id in allowed if pack_id in allowed_set]
            vector_packs = [pack_id for pack_id in vector_packs if pack_id in allowed_set]
        usable = {
            entry_id
            for entry_id, entry in entries.items()
            if not _disabled_in(entry, registry) and entry.pack_id in allowed_set
        }
        # Coverage scoring scans entry text: keep it off the event loop.
        ranked = await self._runtime._query_thread(
            fuse,
            query,
            exact_ids=exact_ids,
            lexical_ids=lexical_ids,
            semantic=semantic,
            entries=entries,
            usable=usable,
            limit=limit,
        )
        return ranked, ("hybrid" if query_vector is not None else "bm25")

    def _snapshot_only_disabled(self, pack_ids: Sequence[str], registry: Registry) -> set[int]:
        """Entries the registry snapshot disables but the index has enabled.

        A re-enable writes the index first, so for a moment the snapshot a
        query holds can still disable entries the index already serves.
        Disabled index rows are filtered in SQL anyway; these few are not.
        """
        return self._runtime._store.entry_ids_by_title(
            {
                pack_id: record.disabled_titles
                for pack_id in pack_ids
                if (record := registry.packs.get(pack_id)) is not None and record.disabled_titles
            },
            enabled_only=True,
        )

    def _lexical_search(
        self, query: str, pack_ids: list[str], registry: Registry
    ) -> tuple[list[int], list[int]]:
        return self._runtime._store.lexical_candidates(
            query,
            pack_ids=pack_ids,
            limit=LEXICAL_CANDIDATES,
            exclude_ids=self._snapshot_only_disabled(pack_ids, registry),
        )

    def _semantic_search(
        self,
        snapshot: VectorSnapshot | None,
        query_vector: np.ndarray,
        pack_ids: list[str],
        registry: Registry,
    ) -> list[SemanticMatch]:
        # Disabled now in the index, or in the query's registry snapshot (a
        # re-enable writes the index first): neither may take a slot.
        excluded = self._runtime._store.disabled_entry_ids(pack_ids) | self._snapshot_only_disabled(
            pack_ids, registry
        )
        return semantic_candidates(
            snapshot, query_vector, allowed_pack_ids=pack_ids, exclude_entry_ids=excluded
        )

    def _render(
        self, ranked: list[RankedHit], query: str, language: str | None, registry: Registry
    ) -> tuple[list[dict[str, Any]], str]:
        entries = self._runtime._store.fetch_entries([hit.entry_id for hit in ranked])
        current = self._runtime._current_packs([entry.pack_id for entry in entries.values()], registry)
        cards: list[RenderCard] = []
        hits: list[dict[str, Any]] = []
        for ranked_hit in ranked:
            entry = entries.get(ranked_hit.entry_id)
            record = registry.packs.get(entry.pack_id) if entry is not None else None
            if entry is None or record is None or _disabled_in(entry, registry) or entry.pack_id not in current:
                continue
            # Show the passage that matched, not just the start of the entry.
            bodies = self._excerpt_bodies(entry, ranked_hit.semantic_chunk)
            index = best_excerpt_index(query, bodies, ranked_hit.semantic_chunk)
            excerpt = bodies[index] if index == 0 else f"... {bodies[index]}"
            cards.append(
                RenderCard(
                    title=entry.title,
                    material_type=record.effective_material_type,
                    summary=entry.summary,
                    content=excerpt,
                    source_name=record.source.name,
                    source_license=record.source.license,
                )
            )
            hits.append(
                {
                    "pack_id": entry.pack_id,
                    "title": entry.title,
                    "material_type": record.effective_material_type,
                }
            )
        return hits, render_reference_block(cards, language=language)

    def _excerpt_bodies(self, entry: StoredEntry, semantic_chunk: int | None) -> list[str]:
        """Render the actual token-fitted chunk instead of a different char split."""
        if semantic_chunk is not None:
            inputs = self._runtime._store.entry_embedding_inputs(entry.entry_id)
            bodies = []
            for index, text in enumerate(inputs):
                header = entry.title + ("\n" + entry.summary if index == 0 and entry.summary else "")
                prefix = header[:MAX_HEADER_CHARS] + "\n"
                if not text.startswith(prefix):
                    break
                bodies.append(text[len(prefix):])
            else:
                if bodies:
                    return bodies
        return chunk_bodies(entry.content) or [entry.content]
