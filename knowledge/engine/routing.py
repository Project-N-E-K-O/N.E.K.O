"""Rebuildable cross-collection routing for automatic conversational context."""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .catalog_overrides import (
    entry_key,
    get_catalog_override_path,
    load_disabled_entries,
)
from .models import KnowledgeEntry
from .retrieval import AUTO_SCAN_MAX_CHARS, MatchPolicy
from .store import KnowledgeStore, register_database_change_listener


_CARD_CACHE_LIMIT = 256
# refresh() converges one dirty generation per round; bound the rounds so a
# fast writer cannot starve the request thread inside match().
_MAX_REFRESH_ROUNDS = 4
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextHint:
    """Non-triggering vocabulary used only to break equal cross-library matches."""

    required_tags: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteCollection:
    collection_id: str
    database_path: Path
    priority: int
    policy: MatchPolicy
    context_hints: tuple[ContextHint, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteRecord:
    collection_id: str
    database_path: Path
    priority: int
    source_tag: str
    title: str
    strong_terms: tuple[str, ...]
    boundary_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]
    context_terms: tuple[str, ...]
    boundary_context_terms: tuple[str, ...]
    revision: int

    @property
    def key(self) -> tuple[str, str, str]:
        return self.collection_id, self.source_tag, self.title


@dataclass(frozen=True, slots=True)
class RouteMatch:
    record: RouteRecord
    match_mode: str
    score: float
    context_score: int = 0


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, int] = field(default_factory=dict)
    terminals: list[tuple[RouteRecord, str, int]] = field(default_factory=list)


class RoutingSnapshot:
    """Immutable matcher for one collection's lightweight route records."""

    def __init__(self, records: Iterable[RouteRecord], policy: MatchPolicy) -> None:
        self._policy = policy
        self._nodes = [_TrieNode()]
        for record in records:
            for phrase in record.strong_terms:
                self._insert(phrase, record, "strong")
            for phrase in record.boundary_terms:
                self._insert(phrase, record, "strong")
            for phrase in record.weak_terms:
                self._insert(phrase, record, "weak_short")

    def _insert(self, phrase: str, record: RouteRecord, mode: str) -> None:
        node_index = 0
        for character in phrase:
            node = self._nodes[node_index]
            node_index = node.children.setdefault(character, len(self._nodes))
            if node_index == len(self._nodes):
                self._nodes.append(_TrieNode())
        terminal = self._nodes[node_index].terminals
        value = (record, mode, len(phrase.replace("\0", "")))
        if value not in terminal:
            terminal.append(value)

    def find(
        self,
        user_text: str,
        *,
        normalized: str | None = None,
        boundary_text: str | None = None,
    ) -> RouteMatch | None:
        normalized = (
            self._policy.normalizer(user_text)
            if normalized is None
            else normalized
        )[:AUTO_SCAN_MAX_CHARS]
        if len(normalized) < 2:
            return None
        boundary_text = (
            _normalize_latin_boundary_text(user_text)
            if boundary_text is None
            else boundary_text
        )[:AUTO_SCAN_MAX_CHARS]
        candidates = self._scan(normalized)
        if boundary_text:
            candidates.extend(self._scan(boundary_text))
        best: dict[tuple[str, str, str], RouteMatch] = {}
        for candidate in candidates:
            previous = best.get(candidate.record.key)
            if previous is None or _match_sort_key(candidate) < _match_sort_key(previous):
                best[candidate.record.key] = candidate
        if not best:
            return None
        selected = min(best.values(), key=_match_sort_key)
        return RouteMatch(
            selected.record,
            selected.match_mode,
            selected.score,
            _context_evidence_score(selected.record, normalized, boundary_text),
        )

    @property
    def normalizer(self):
        return self._policy.normalizer

    def _scan(
        self,
        text: str,
    ) -> list[RouteMatch]:
        results: list[RouteMatch] = []
        for start_index in range(len(text)):
            node_index = 0
            for character in text[start_index:]:
                next_index = self._nodes[node_index].children.get(character)
                if next_index is None:
                    break
                node_index = next_index
                for record, mode, length in self._nodes[node_index].terminals:
                    results.append(RouteMatch(record, mode, float(length)))
        return results


class SegmentedRoutingSnapshot:
    """One logical router backed by independently replaceable collection tries."""

    def __init__(self, matchers: dict[str, RoutingSnapshot]) -> None:
        self._matchers = matchers

    def find(
        self,
        user_text: str,
        *,
        allowed_collections: frozenset[str],
    ) -> RouteMatch | None:
        boundary_text = _normalize_latin_boundary_text(user_text)
        normalized_by_policy: dict[object, str] = {}
        candidates: list[RouteMatch] = []
        for collection_id, matcher in self._matchers.items():
            if collection_id not in allowed_collections:
                continue
            normalizer = matcher.normalizer
            if normalizer not in normalized_by_policy:
                normalized_by_policy[normalizer] = normalizer(user_text)
            normalized = normalized_by_policy[normalizer]
            match = matcher.find(
                user_text,
                normalized=normalized,
                boundary_text=boundary_text,
            )
            if match is not None:
                candidates.append(match)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        evidence = {match.record.key: match.context_score for match in candidates}
        selected = min(
            candidates,
            key=lambda match: _cross_collection_sort_key(match, evidence[match.record.key]),
        )
        quality_peers = [
            match
            for match in candidates
            if match.match_mode == selected.match_mode and match.score == selected.score
        ]
        peer_scores = [evidence[match.record.key] for match in quality_peers]
        best_evidence = max(peer_scores, default=0)
        resolved_by_hint = (
            best_evidence > 0
            and evidence[selected.record.key] == best_evidence
            and peer_scores.count(best_evidence) == 1
        )
        resolution = (
            "match_quality"
            if len(quality_peers) == 1
            else "context_hint" if resolved_by_hint else "collection_priority"
        )
        logger.debug(
            "[public-knowledge] route conflict candidates=%d resolution=%s collection=%s",
            len(candidates),
            resolution,
            selected.record.collection_id,
        )
        return selected


def _match_sort_key(match: RouteMatch) -> tuple[int, float, int, str, str]:
    return (
        0 if match.match_mode == "strong" else 1,
        -match.score,
        -match.record.priority,
        match.record.title,
        match.record.collection_id,
    )


def _cross_collection_sort_key(
    match: RouteMatch,
    context_score: int,
) -> tuple[int, float, int, int, str, str]:
    return (
        0 if match.match_mode == "strong" else 1,
        -match.score,
        -context_score,
        -match.record.priority,
        match.record.title,
        match.record.collection_id,
    )


def _context_evidence_score(
    record: RouteRecord,
    normalized: str,
    boundary_text: str,
) -> int:
    ordinary = max(
        (len(term) for term in record.context_terms if term in normalized),
        default=0,
    )
    bounded = max(
        (
            len(term.replace("\0", ""))
            for term in record.boundary_context_terms
            if term in boundary_text
        ),
        default=0,
    )
    return max(ordinary, bounded)


class KnowledgeRoutingState:
    """One atomic route snapshot plus a bounded cache of complete cards."""

    def __init__(self, collections: tuple[RouteCollection, ...]) -> None:
        self.collections = collections
        self._segments: dict[str, tuple[RouteRecord, ...]] = {}
        self._matchers: dict[str, RoutingSnapshot] = {}
        self._snapshot: SegmentedRoutingSnapshot | None = None
        self._dirty = {collection.collection_id for collection in collections}
        self._dirty_generation = {
            collection.collection_id: 0 for collection in collections
        }
        self._cards: OrderedDict[
            tuple[str, str, str, int], KnowledgeEntry
        ] = OrderedDict()
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._refresh_thread: threading.Thread | None = None

    def mark_database_dirty(self, database_path: str | Path) -> None:
        resolved = Path(database_path).resolve()
        changed = {
            collection.collection_id
            for collection in self.collections
            if collection.database_path.resolve() == resolved
        }
        with self._lock:
            self._dirty.update(changed)
            for collection_id in changed:
                self._dirty_generation[collection_id] += 1
            if changed:
                self._cards = OrderedDict(
                    (key, value)
                    for key, value in self._cards.items()
                    if key[0] not in changed
                )

    def refresh(self) -> None:
        with self._refresh_lock:
            for _ in range(_MAX_REFRESH_ROUNDS):
                with self._lock:
                    dirty = {
                        collection_id: self._dirty_generation[collection_id]
                        for collection_id in self._dirty
                    }
                if not dirty and self._snapshot is not None:
                    return
                configs = {
                    collection.collection_id: collection for collection in self.collections
                }
                replacements = {
                    collection_id: _safe_load_segment(configs[collection_id])
                    for collection_id in dirty
                    if collection_id in configs
                }
                with self._lock:
                    segments = dict(self._segments)
                    segments.update(replacements)
                    matchers = dict(self._matchers)
                    matchers.update(
                        (
                            collection_id,
                            RoutingSnapshot(
                                replacements[collection_id],
                                configs[collection_id].policy,
                            ),
                        )
                        for collection_id in replacements
                    )
                    snapshot = SegmentedRoutingSnapshot(matchers)
                    self._segments = segments
                    self._matchers = matchers
                    self._snapshot = snapshot
                    for collection_id, generation in dirty.items():
                        if self._dirty_generation[collection_id] == generation:
                            self._dirty.discard(collection_id)
            # A continuously changing database remains dirty for the next caller
            # instead of keeping this refresh call alive indefinitely.

    def refresh_in_background(self) -> None:
        with self._lock:
            if self._refresh_thread is not None and self._refresh_thread.is_alive():
                return
            thread = threading.Thread(
                target=self._background_refresh,
                name="knowledge-routing-refresh",
                daemon=True,
            )
            self._refresh_thread = thread
            thread.start()

    def _background_refresh(self) -> None:
        try:
            self.refresh()
        except Exception as exc:
            logger.warning(
                "[public-knowledge] background route refresh failed type=%s",
                type(exc).__name__,
            )
        finally:
            with self._lock:
                if self._refresh_thread is threading.current_thread():
                    self._refresh_thread = None

    def match(
        self,
        user_text: str,
        *,
        allowed_collections: frozenset[str],
    ) -> RouteMatch | None:
        with self._lock:
            snapshot = self._snapshot
            dirty = bool(self._dirty)
            refresh_running = (
                self._refresh_thread is not None
                and self._refresh_thread.is_alive()
            )
        if snapshot is None or (dirty and not refresh_running):
            self.refresh()
            with self._lock:
                snapshot = self._snapshot
        return snapshot.find(
            user_text,
            allowed_collections=allowed_collections,
        ) if snapshot is not None else None

    def get_card(self, match: RouteMatch) -> KnowledgeEntry | None:
        record = match.record
        key = (*record.key, record.revision)
        with self._lock:
            cached = self._cards.get(key)
            if cached is not None:
                self._cards.move_to_end(key)
                return cached
        entry = KnowledgeStore(record.database_path).get_entry(
            record.source_tag,
            record.title,
        )
        if entry is None:
            self.mark_database_dirty(record.database_path)
            return None
        with self._lock:
            self._cards[key] = entry
            self._cards.move_to_end(key)
            while len(self._cards) > _CARD_CACHE_LIMIT:
                self._cards.popitem(last=False)
        return entry

    def cache_size(self) -> int:
        with self._lock:
            return len(self._cards)


def _load_segment(collection: RouteCollection) -> tuple[RouteRecord, ...]:
    store = KnowledgeStore(collection.database_path)
    revision, entries = store.load_routing_entries()
    disabled = load_disabled_entries(get_catalog_override_path(collection.database_path))
    records: list[RouteRecord] = []
    for entry in entries:
        if entry_key(entry) in disabled:
            continue
        policy = collection.policy
        if (
            policy.allowed_source_tags is not None
            and entry.source_tag not in policy.allowed_source_tags
        ):
            continue
        if any(tag in entry.tags for tag in policy.excluded_entry_tags):
            continue
        context_terms, boundary_context_terms = _entry_context_terms(
            entry,
            collection.context_hints,
            policy,
        )
        strong: list[str] = []
        boundary: list[str] = []
        weak: list[str] = []
        for index, value in enumerate((entry.title, *entry.aliases)):
            phrase = policy.normalizer(value)
            minimum = policy.title_min_length if index == 0 else policy.alias_min_length
            if len(phrase) >= minimum:
                if policy.latin_word_boundaries and _is_ascii_word_phrase(value):
                    boundary_phrase = _normalize_latin_boundary_text(value)
                    if len(boundary_phrase.replace("\0", "")) >= minimum:
                        boundary.append(boundary_phrase)
                else:
                    strong.append(phrase)
            elif (
                policy.weak_term_length > 0
                and len(phrase) == policy.weak_term_length
                and _weak_entry_is_eligible(entry, policy)
            ):
                weak.append(phrase)
        for value in entry.recognition_terms:
            phrase = policy.normalizer(value)
            if len(phrase) >= policy.recognition_min_length:
                if policy.latin_word_boundaries and _is_ascii_word_phrase(value):
                    boundary_phrase = _normalize_latin_boundary_text(value)
                    if (
                        len(boundary_phrase.replace("\0", ""))
                        >= policy.recognition_min_length
                    ):
                        boundary.append(boundary_phrase)
                else:
                    strong.append(phrase)
        if strong or boundary or weak:
            records.append(RouteRecord(
                collection_id=collection.collection_id,
                database_path=collection.database_path,
                priority=collection.priority,
                source_tag=entry.source_tag,
                title=entry.title,
                strong_terms=tuple(dict.fromkeys(strong)),
                boundary_terms=tuple(dict.fromkeys(boundary)),
                weak_terms=tuple(dict.fromkeys(weak)),
                context_terms=context_terms,
                boundary_context_terms=boundary_context_terms,
                revision=revision,
            ))
    return tuple(records)


_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _is_ascii_word_phrase(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return normalized.isascii() and any(character.isalnum() for character in normalized)


def _normalize_latin_boundary_text(value: str) -> str:
    tokens = _LATIN_TOKEN_RE.findall(unicodedata.normalize("NFKC", str(value)).casefold())
    return "\0" + "\0".join(tokens) + "\0" if tokens else ""


def _entry_context_terms(
    entry: KnowledgeEntry,
    hints: tuple[ContextHint, ...],
    policy: MatchPolicy,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ordinary: list[str] = []
    bounded: list[str] = []
    tags = frozenset(entry.tags)
    for hint in hints:
        if not set(hint.required_tags).issubset(tags):
            continue
        for value in hint.terms:
            normalized = policy.normalizer(value)
            if not normalized:
                continue
            if _is_ascii_word_phrase(value):
                boundary = _normalize_latin_boundary_text(value)
                if boundary:
                    bounded.append(boundary)
            else:
                ordinary.append(normalized)
    return tuple(dict.fromkeys(ordinary)), tuple(dict.fromkeys(bounded))


def _safe_load_segment(collection: RouteCollection) -> tuple[RouteRecord, ...]:
    try:
        return _load_segment(collection)
    except (OSError, RuntimeError, TypeError, ValueError):
        return ()


def _weak_entry_is_eligible(entry: KnowledgeEntry, policy: MatchPolicy) -> bool:
    tags = entry.tags
    if any(tag not in tags for tag in policy.weak_required_tags):
        return False
    if any(tag in tags for tag in policy.weak_excluded_tags):
        return False
    for prefix in policy.weak_required_tag_prefixes:
        if not any(tag.startswith(prefix) and tag.removeprefix(prefix).strip() for tag in tags):
            return False
    if policy.weak_content_line_prefix:
        return any(
            line.strip().startswith(policy.weak_content_line_prefix)
            for line in entry.content.splitlines()
        )
    return True


_STATE_CACHE_LIMIT = 16
_STATES: OrderedDict[
    tuple[RouteCollection, ...], KnowledgeRoutingState
] = OrderedDict()
_LIVE_STATES: weakref.WeakSet[KnowledgeRoutingState] = weakref.WeakSet()
_STATES_LOCK = threading.Lock()


def get_routing_state(
    collections: tuple[RouteCollection, ...],
) -> KnowledgeRoutingState:
    with _STATES_LOCK:
        state = _STATES.get(collections)
        if state is None:
            state = KnowledgeRoutingState(collections)
            _STATES[collections] = state
            _LIVE_STATES.add(state)
            while len(_STATES) > _STATE_CACHE_LIMIT:
                _STATES.popitem(last=False)
        else:
            _STATES.move_to_end(collections)
        return state


def notify_database_changed(database_path: str | Path) -> None:
    with _STATES_LOCK:
        states = tuple(_LIVE_STATES)
    for state in states:
        state.mark_database_dirty(database_path)


register_database_change_listener(notify_database_changed)
