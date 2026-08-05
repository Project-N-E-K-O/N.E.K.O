"""Source-independent service for local conversational knowledge."""

from __future__ import annotations

import random
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping

from .collection_overrides import (
    clear_collection_auto_context,
    get_collection_override_path,
    load_auto_context_overrides,
    set_collection_auto_context,
)
from .collection_specs import (
    CollectionSpec,
    ResponsePolicy,
    get_reference_details,
    get_tag_value,
)
from .community_collections import (
    CommunityCollectionRecord,
    community_collection_spec,
    get_community_mutation_lock_path,
    load_community_collections,
    new_community_collection,
    write_community_collections,
)
from .engine.catalog_overrides import (
    get_catalog_override_path,
    load_disabled_entries,
    set_entry_disabled,
)
from .engine.models import KnowledgeEntry, KnowledgeHit
from .engine.mutation_lock import mutation_lock
from .engine.retrieval import KnowledgeRetriever, MatchPolicy
from .engine.routing import (
    KnowledgeRoutingState,
    RouteCollection,
    get_routing_state,
    notify_database_changed,
)
from .engine.source_registry import resolve_source
from .engine.store import KnowledgeStore


_PACK_SOURCE_TAG_CACHE_LIMIT = 32


@dataclass(frozen=True, slots=True)
class KnowledgeTurnMatch:
    collection_id: str
    hit: KnowledgeHit
    match_mode: str
    collection_priority: int = 0


@dataclass(frozen=True, slots=True)
class KnowledgeTurnContext:
    text: str = ""
    hit_count: int = 0
    match_mode: str = "none"
    collection_id: str = ""
    entry_title: str = ""
    source_tag: str = ""


class KnowledgeService:
    """Query and manage trusted and data-only community collections."""

    def __init__(
        self,
        knowledge_root: str | Path,
        *,
        collections: Iterable[CollectionSpec] = (),
        database_paths: Mapping[str, str | Path] | None = None,
    ) -> None:
        self.knowledge_root = Path(knowledge_root)
        trusted = tuple(collections)
        self._trusted_ids = frozenset(spec.collection_id for spec in trusted)
        if len(self._trusted_ids) != len(trusted):
            raise ValueError("duplicate trusted knowledge collection")
        try:
            community_records = load_community_collections(self.knowledge_root)
        except ValueError as exc:
            # A newer registry must never be overwritten, but it also must not
            # brick the whole service: open the built-in collections and treat
            # the community registry as empty until it is replaced.
            community_records = {}
        self._community_records = community_records
        self._mark_trusted_collisions()
        community_specs = tuple(
            community_collection_spec(record)
            for record in self._community_records.values()
            if record.status == "active" and record.collection_id not in self._trusted_ids
        )
        self._collections = {
            spec.collection_id: spec for spec in (*trusted, *community_specs)
        }
        self._database_paths = {
            key: Path(value) for key, value in (database_paths or {}).items()
        }
        self._auto_context_overrides = load_auto_context_overrides(
            get_collection_override_path(self.knowledge_root)
        )
        self._routing_state: KnowledgeRoutingState | None = None
        self._pack_source_tag_cache: OrderedDict[str, tuple[str, ...]] = OrderedDict()

    @classmethod
    def from_root(
        cls,
        knowledge_root: str | Path,
        *,
        collections: Iterable[CollectionSpec] = (),
    ) -> "KnowledgeService":
        return cls(knowledge_root, collections=collections)

    @classmethod
    def for_collection(
        cls,
        collection: CollectionSpec,
        database_path: str | Path,
    ) -> "KnowledgeService":
        database_path = Path(database_path)
        return cls(
            database_path.parent.parent,
            collections=(collection,),
            database_paths={collection.collection_id: database_path},
        )

    def search(self, collection_id: str, query: str, *, limit: int = 3) -> list[KnowledgeHit]:
        return self._retriever(collection_id).search(query, limit=limit)

    def search_page(
        self,
        collection_id: str,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
        source_tag: str = "",
        include_disabled: bool = False,
    ) -> tuple[KnowledgeHit, ...]:
        """Return one page plus at most one lookahead hit for pagination."""
        limit = min(max(int(limit), 1), 100)
        offset = min(max(int(offset), 0), 1_000)
        hits = self._retriever(collection_id).search(
            query,
            limit=offset + limit + 1,
            allowed_source_tags=(source_tag,) if source_tag else None,
            include_disabled=include_disabled,
        )
        return tuple(hits[offset : offset + limit + 1])

    def sample_entries(
        self,
        collection_id: str,
        sample_tag: str,
        *,
        limit: int = 1,
    ) -> tuple[KnowledgeEntry, ...]:
        return self._sample_entries(
            collection_id,
            sample_tag,
            limit=limit,
            allowed_source_tags=None,
        )

    def _sample_entries(
        self,
        collection_id: str,
        sample_tag: str,
        *,
        limit: int,
        allowed_source_tags: tuple[str, ...] | None,
    ) -> tuple[KnowledgeEntry, ...]:
        spec = self._spec(collection_id)
        if sample_tag not in spec.sample_tags:
            raise ValueError("sample tag is not enabled for this collection")
        limit = min(max(int(limit), 1), 3)
        hits = self._retriever(collection_id).search(
            sample_tag,
            limit=100,
            allowed_source_tags=allowed_source_tags,
        )
        candidates = [hit.entry for hit in hits if sample_tag in hit.entry.tags]
        return tuple(candidates) if len(candidates) <= limit else tuple(random.sample(candidates, limit))

    def match_turn(
        self,
        collection_id: str,
        user_text: str,
        *,
        limit: int = 1,
    ) -> list[KnowledgeTurnMatch]:
        spec = self._spec(collection_id)
        mode, hits = self._retriever(collection_id).match_turn(
            user_text,
            policy=spec.match_policy,
            limit=limit,
        )
        return [
            KnowledgeTurnMatch(collection_id, hit, mode, spec.priority) for hit in hits
        ]

    def build_turn_context(
        self,
        user_text: str,
        *,
        collection_ids: Iterable[str] | None = None,
        limit: int = 1,
    ) -> KnowledgeTurnContext:
        if limit <= 0:
            return KnowledgeTurnContext()
        allowed = self._context_collections(collection_ids)
        if not allowed:
            return KnowledgeTurnContext()
        state = self._get_routing_state()
        route_match = state.match(user_text, allowed_collections=allowed)
        if route_match is None:
            return KnowledgeTurnContext()
        entry = state.get_card(route_match)
        if entry is None:
            return KnowledgeTurnContext()
        selected = KnowledgeTurnMatch(
            route_match.record.collection_id,
            KnowledgeHit(entry=entry, score=route_match.score),
            route_match.match_mode,
            route_match.record.priority,
        )
        policy = self._spec(selected.collection_id).response_policy
        if policy is None:
            return KnowledgeTurnContext()
        return KnowledgeTurnContext(
            text=self._render_turn_context(selected, policy),
            hit_count=1,
            match_mode=selected.match_mode,
            collection_id=selected.collection_id,
            entry_title=entry.title,
            source_tag=entry.source_tag,
        )

    def build_conversation_context(
        self,
        user_text: str,
        *,
        collection_ids: Iterable[str] | None = None,
        limit: int = 1,
    ) -> KnowledgeTurnContext:
        direct = self.build_turn_context(
            user_text,
            collection_ids=collection_ids,
            limit=limit,
        )
        if direct.hit_count or limit <= 0:
            return direct
        allowed = frozenset(self._collections if collection_ids is None else collection_ids)
        self._reject_unknown(allowed)
        normalized = unicodedata.normalize("NFKC", str(user_text)).casefold()
        for spec in sorted(
            (self._spec(value) for value in allowed),
            key=lambda value: (-value.priority, value.collection_id),
        ):
            if not self._auto_context_enabled(spec) or spec.response_policy is None:
                continue
            route = next(
                (
                    candidate
                    for candidate in spec.material_routes
                    if any(term in normalized for term in candidate.topic_terms)
                    and any(term in normalized for term in candidate.request_terms)
                ),
                None,
            )
            if route is None:
                continue
            entries = self._sample_entries(
                spec.collection_id,
                route.sample_tag,
                limit=1,
                allowed_source_tags=self._effective_match_policy(spec).allowed_source_tags,
            )
            if entries:
                selected = KnowledgeTurnMatch(
                    spec.collection_id,
                    KnowledgeHit(entry=entries[0], score=0.0),
                    "material_sample",
                    spec.priority,
                )
                return KnowledgeTurnContext(
                    text=self._render_turn_context(selected, spec.response_policy),
                    hit_count=1,
                    match_mode="material_sample",
                    collection_id=spec.collection_id,
                    entry_title=entries[0].title,
                    source_tag=entries[0].source_tag,
                )
        return KnowledgeTurnContext()

    def list_entries(
        self,
        collection_id: str,
        *,
        source_tag: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[KnowledgeEntry, ...]:
        return self._store(collection_id).list_entries(
            source_tag=source_tag,
            limit=limit,
            offset=offset,
        )

    def get_entry(
        self,
        collection_id: str,
        *,
        source_tag: str,
        title: str,
    ) -> KnowledgeEntry | None:
        return self._store(collection_id).get_entry(source_tag, title)

    def list_disabled_entries(
        self,
        collection_id: str,
    ) -> frozenset[tuple[str, str]]:
        """Return the bounded local override keys for management views."""
        database_path = self.database_path(collection_id)
        return load_disabled_entries(get_catalog_override_path(database_path))

    def get_source_metadata(self, collection_id: str, source_tag: str) -> dict:
        """Resolve bounded display metadata for a trusted or installed source."""
        spec = self._spec(collection_id)
        source = resolve_source(
            source_tag,
            registered_sources=spec.sources,
            database_path=self.database_path(collection_id),
        )
        return {
            "tag": source.tag,
            "name": source.name,
            "homepage": source.homepage,
            "license": source.license,
        }

    def set_entry_disabled(
        self,
        collection_id: str,
        *,
        source_tag: str,
        title: str,
        disabled: bool,
    ) -> int:
        database_path = self.database_path(collection_id)
        count = set_entry_disabled(
            get_catalog_override_path(database_path),
            source_tag=source_tag,
            title=title,
            disabled=disabled,
        )
        notify_database_changed(database_path)
        if self._routing_state is not None:
            self._routing_state.refresh()
        return count

    def get_status(self, collection_id: str) -> dict:
        """Return detailed collection diagnostics, including SQLite integrity."""
        return self._collection_status(collection_id, check_integrity=True)

    def _collection_status(
        self,
        collection_id: str,
        *,
        check_integrity: bool,
    ) -> dict:
        spec = self._spec(collection_id)
        database_path = self.database_path(collection_id)
        store = self._store(collection_id) if database_path.is_file() else None
        disabled = load_disabled_entries(get_catalog_override_path(database_path))
        return {
            "collection_id": collection_id,
            "name": spec.display_name or collection_id,
            "storage_directory": spec.storage_directory,
            "priority": spec.priority,
            "entries": store.count() if store is not None else 0,
            "integrity_ok": (
                store.integrity_ok() if store is not None and check_integrity else None
            ),
            "auto_context": self._auto_context_enabled(spec),
            "disabled_entries": len(disabled),
            "sources": store.count_by_source_tags() if store is not None else (),
            "packs": len(self.list_packs(collection_id)),
        }

    def list_collections(self) -> tuple[dict, ...]:
        results: list[dict] = []
        for collection_id in sorted(self._collections):
            try:
                payload = self._collection_status(collection_id, check_integrity=False)
                status = (
                    "ready" if self.database_path(collection_id).is_file() else "degraded"
                )
                results.append({"status": status, **payload})
            except Exception as exc:
                spec = self._spec(collection_id)
                results.append(
                    {
                        "collection_id": collection_id,
                        "name": spec.display_name or collection_id,
                        "storage_directory": spec.storage_directory,
                        "priority": spec.priority,
                        "entries": 0,
                        "status": "degraded",
                        "integrity_ok": False,
                        "error_type": type(exc).__name__,
                        "auto_context": self._auto_context_enabled(spec),
                        "disabled_entries": 0,
                        "sources": (),
                        "packs": 0,
                    }
                )
        return tuple(results)

    def set_collection_auto_context(self, collection_id: str, *, enabled: bool) -> None:
        self._spec(collection_id)
        set_collection_auto_context(
            get_collection_override_path(self.knowledge_root),
            collection_id=collection_id,
            enabled=enabled,
        )
        self._auto_context_overrides[collection_id] = bool(enabled)
        self._invalidate_pack_source_tags(collection_id)
        self._routing_state = None

    def install_pack(self, pack, *, subscription=None):
        """Install a pack and create its community collection atomically."""
        from .packs import capture_pack_storage, install_pack, restore_pack_storage

        with mutation_lock(get_community_mutation_lock_path(self.knowledge_root)):
            existing = self._collections.get(pack.collection_id)
            record = self._community_records.get(pack.collection_id)
            if pack.collection_id in self._trusted_ids:
                if pack.collection is not None:
                    raise ValueError("trusted collections cannot be redefined by a pack")
                database_path = self.database_path(pack.collection_id)
                new_record = None
            elif existing is None:
                if record is not None and record.status != "active":
                    raise ValueError("community collection is unavailable")
                if pack.collection is None:
                    raise ValueError("new community collection requires collection.display_name")
                new_record = new_community_collection(
                    pack.collection_id,
                    pack.collection.display_name,
                    created_by_pack=pack.pack_id,
                )
                database_path = (
                    self.knowledge_root
                    / new_record.storage_directory
                    / "knowledge.db"
                )
            else:
                if record is None or record.status != "active":
                    raise ValueError("community collection is unavailable")
                if pack.collection is not None and pack.collection.display_name != record.display_name:
                    raise ValueError("community collection display name cannot change")
                new_record = None
                database_path = self.database_path(pack.collection_id)

            snapshot = capture_pack_storage(database_path, pack.pack_id)
            result = install_pack(
                database_path,
                pack,
                subscription=subscription,
            )
            if new_record is not None:
                records = {**self._community_records, pack.collection_id: new_record}
                try:
                    write_community_collections(self.knowledge_root, records)
                except Exception:
                    restore_pack_storage(database_path, pack.pack_id, snapshot)
                    raise
                self._community_records = records
                self._collections[pack.collection_id] = community_collection_spec(new_record)
            self._invalidate_pack_source_tags(pack.collection_id)
            self._routing_state = None
        self.refresh_routing_index(background=True)
        return result

    def import_pack(self, path: str | Path):
        from .packs import load_pack

        return self.install_pack(load_pack(path))

    def remove_pack(self, collection_id: str, pack_id: str) -> int:
        from .packs import capture_pack_storage, remove_pack, restore_pack_storage

        spec = self._spec(collection_id)
        database_path = self.database_path(collection_id)
        with mutation_lock(get_community_mutation_lock_path(self.knowledge_root)):
            snapshot = capture_pack_storage(database_path, pack_id)
            removed = remove_pack(database_path, pack_id)
            last_community_pack = (
                spec.community_managed and not self.list_packs(collection_id)
            )
            if last_community_pack:
                records = dict(self._community_records)
                records.pop(collection_id, None)
                registry_written = False
                try:
                    write_community_collections(self.knowledge_root, records)
                    registry_written = True
                    clear_collection_auto_context(
                        get_collection_override_path(self.knowledge_root),
                        collection_id=collection_id,
                    )
                except Exception:
                    restore_pack_storage(database_path, pack_id, snapshot)
                    if registry_written:
                        write_community_collections(
                            self.knowledge_root,
                            self._community_records,
                        )
                    raise
                self._community_records = records
                self._collections.pop(collection_id, None)
                self._auto_context_overrides.pop(collection_id, None)
            self._invalidate_pack_source_tags(collection_id)
            self._routing_state = None
        if collection_id in self._collections:
            self.refresh_routing_index(background=True)
        return removed

    def list_packs(self, collection_id: str) -> tuple[dict, ...]:
        from .packs import list_installed_packs

        return list_installed_packs(self.database_path(collection_id))

    def set_pack_auto_context(
        self,
        collection_id: str,
        pack_id: str,
        *,
        enabled: bool,
    ) -> None:
        from .packs import set_pack_auto_context

        set_pack_auto_context(self.database_path(collection_id), pack_id, enabled=enabled)
        self._invalidate_pack_source_tags(collection_id)
        self._routing_state = None
        self.refresh_routing_index(background=True)

    def count_entries(self, collection_id: str, *, source_tag: str = "") -> int:
        store = self._store(collection_id)
        return store.count_by_source_tag(source_tag) if source_tag else store.count()

    def refresh_routing_index(self, *, background: bool = False) -> None:
        state = self._get_routing_state()
        state.refresh_in_background() if background else state.refresh()

    def database_path(self, collection_id: str) -> Path:
        if collection_id in self._database_paths:
            return self._database_paths[collection_id]
        spec = self._spec(collection_id)
        return self.knowledge_root / spec.storage_directory / spec.database_filename

    def _spec(self, collection_id: str) -> CollectionSpec:
        try:
            return self._collections[collection_id]
        except KeyError as exc:
            raise ValueError(f"unknown knowledge collection: {collection_id}") from exc

    def _store(self, collection_id: str) -> KnowledgeStore:
        return KnowledgeStore(self.database_path(collection_id))

    def _retriever(self, collection_id: str) -> KnowledgeRetriever:
        return KnowledgeRetriever(self._store(collection_id))

    def _get_routing_state(self) -> KnowledgeRoutingState:
        if self._routing_state is None:
            collections = tuple(
                RouteCollection(
                    spec.collection_id,
                    self.database_path(spec.collection_id),
                    spec.priority,
                    self._effective_match_policy(spec),
                    spec.context_hints,
                )
                for spec in self._collections.values()
                if spec.response_policy is not None
            )
            self._routing_state = get_routing_state(collections)
        return self._routing_state

    def _context_collections(
        self,
        collection_ids: Iterable[str] | None,
    ) -> frozenset[str]:
        if collection_ids is None:
            return frozenset(
                spec.collection_id
                for spec in self._collections.values()
                if self._auto_context_enabled(spec) and spec.response_policy is not None
            )
        allowed = frozenset(collection_ids)
        self._reject_unknown(allowed)
        return frozenset(
            value for value in allowed if self._spec(value).response_policy is not None
        )

    def _reject_unknown(self, collection_ids: frozenset[str]) -> None:
        unknown = collection_ids.difference(self._collections)
        if unknown:
            raise ValueError(f"unknown knowledge collection: {sorted(unknown)[0]}")

    def _auto_context_enabled(self, spec: CollectionSpec) -> bool:
        return self._auto_context_overrides.get(spec.collection_id, spec.auto_context_enabled)

    def _effective_match_policy(self, spec: CollectionSpec) -> MatchPolicy:
        if not spec.restrict_auto_context_to_registered_sources:
            return spec.match_policy
        allowed_sources = tuple(
            sorted(
                {
                    *spec.auto_context_source_tags,
                    *(source.tag for source in spec.sources),
                    *self._enabled_pack_source_tags(spec.collection_id),
                }
            )
        )
        return replace(spec.match_policy, allowed_source_tags=allowed_sources)

    def _enabled_pack_source_tags(self, collection_id: str) -> tuple[str, ...]:
        cached = self._pack_source_tag_cache.get(collection_id)
        if cached is not None:
            self._pack_source_tag_cache.move_to_end(collection_id)
            return cached
        from .packs import enabled_pack_source_tags

        source_tags = enabled_pack_source_tags(self.database_path(collection_id))
        self._pack_source_tag_cache[collection_id] = source_tags
        self._pack_source_tag_cache.move_to_end(collection_id)
        while len(self._pack_source_tag_cache) > _PACK_SOURCE_TAG_CACHE_LIMIT:
            self._pack_source_tag_cache.popitem(last=False)
        return source_tags

    def _invalidate_pack_source_tags(self, collection_id: str) -> None:
        self._pack_source_tag_cache.pop(collection_id, None)

    def _render_turn_context(self, match: KnowledgeTurnMatch, policy: ResponsePolicy) -> str:
        entry = match.hit.entry
        if match.match_mode == "weak_short":
            lines = [policy.weak_header, policy.weak_preamble, policy.task_instruction]
        elif match.match_mode == "material_sample":
            lines = [
                policy.confirmed_header,
                policy.sample_preamble or policy.confirmed_preamble,
                policy.task_instruction,
            ]
        else:
            lines = [policy.confirmed_header, policy.confirmed_preamble, policy.task_instruction]
        meaning = (
            (entry.summary or entry.content)
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()[:280]
        )
        term = entry.title.replace("\r", " ").replace("\n", " ").strip()[:500]
        classification = get_tag_value(entry, policy.classification_tag_prefix)
        details = get_reference_details(entry, policy.detail_line_prefixes, max_chars=420)
        lines.extend((f"{policy.term_label}: {term}\n", f"{policy.summary_label}: {meaning}\n"))
        if classification:
            lines.append(f"{policy.classification_label}: {classification}\n")
        if details:
            lines.append(f"{policy.detail_label}: {details}\n")
        posture = policy.type_postures.get(classification, policy.default_posture)
        spec = self._spec(match.collection_id)
        source = resolve_source(
            entry.source_tag,
            registered_sources=spec.sources,
            database_path=self.database_path(match.collection_id),
        )
        lines.extend((f"Response posture: {posture}\n", f"Source: {source.name}\n", "=========================================================="))
        return "".join(lines)

    def _mark_trusted_collisions(self) -> None:
        changed = False
        records = dict(self._community_records)
        for collection_id in self._trusted_ids.intersection(records):
            record = records[collection_id]
            if record.status != "conflict":
                records[collection_id] = replace(record, status="conflict")
                changed = True
        if changed:
            try:
                with mutation_lock(get_community_mutation_lock_path(self.knowledge_root)):
                    write_community_collections(self.knowledge_root, records)
            except OSError:
                # A damaged community registry must not block a trusted
                # collection with the same identifier from opening.
                pass
            self._community_records = records
