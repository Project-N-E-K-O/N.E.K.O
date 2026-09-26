"""Rebuildable exact-term routing for automatic public-knowledge context."""

from __future__ import annotations

import re
import random
import sqlite3
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .catalog_overrides import (
    entry_key,
    get_catalog_override_path,
    load_disabled_entries,
)
from .filters import normalize_search_text
from .models import KnowledgeEntry
from .retrieval import MatchPolicy
from .store import KnowledgeStore


_CARD_CACHE_LIMIT = 256
_STATE_CACHE_LIMIT = 16
_REFRESH_RETRY_INITIAL_SECONDS = 0.25
_REFRESH_RETRY_MAX_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    database_path: Path
    policy: MatchPolicy


@dataclass(frozen=True, slots=True)
class RouteRecord:
    database_path: Path
    source_tag: str
    title: str
    strong_terms: tuple[str, ...]
    boundary_terms: tuple[str, ...]
    revision: int

    @property
    def key(self) -> tuple[str, str]:
        return self.source_tag, self.title


@dataclass(frozen=True, slots=True)
class RouteMatch:
    record: RouteRecord
    match_mode: str
    score: float


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, int] = field(default_factory=dict)
    terminals: list[tuple[RouteRecord, str, int]] = field(default_factory=list)


class RoutingSnapshot:
    """Immutable title/alias/recognition matcher for one knowledge database."""

    def __init__(self, records: Iterable[RouteRecord]) -> None:
        self._nodes = [_TrieNode()]
        for record in records:
            for phrase in record.strong_terms:
                self._insert(phrase, record, "strong")
            for phrase in record.boundary_terms:
                self._insert(phrase, record, "strong")

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
        excluded_source_tags: frozenset[str] = frozenset(),
    ) -> RouteMatch | None:
        normalized = normalize_search_text(user_text)
        if len(normalized) < 2:
            return None
        boundary_text = _normalize_latin_boundary_text(user_text)
        candidates = self._scan(normalized)
        if boundary_text:
            candidates.extend(self._scan(boundary_text))
        if not candidates:
            return None
        best: dict[tuple[str, str], RouteMatch] = {}
        for candidate in candidates:
            if candidate.record.source_tag in excluded_source_tags:
                continue
            previous = best.get(candidate.record.key)
            if previous is None or _match_sort_key(candidate) < _match_sort_key(previous):
                best[candidate.record.key] = candidate
        return min(best.values(), key=_match_sort_key) if best else None

    def _scan(self, text: str) -> list[RouteMatch]:
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


def _match_sort_key(match: RouteMatch) -> tuple[int, float, str, str]:
    return (
        0,
        -match.score,
        match.record.title,
        match.record.source_tag,
    )


class KnowledgeRoutingState:
    """One atomic route snapshot plus a bounded cache of complete entries."""

    def __init__(self, config: RoutingConfig) -> None:
        self.config = config
        self._snapshot: RoutingSnapshot | None = None
        self._dirty = True
        self._generation = 0
        self._cards: OrderedDict[
            tuple[str, str, int], KnowledgeEntry
        ] = OrderedDict()
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._refresh_thread: threading.Thread | None = None
        self._retry_at = 0.0
        self._retry_delay = _REFRESH_RETRY_INITIAL_SECONDS
        self._hidden_source_tags: set[str] = set()

    def mark_database_dirty(
        self,
        database_path: str | Path,
        *,
        hidden_source_tags: Iterable[str] = (),
    ) -> None:
        if Path(database_path).resolve() != self.config.database_path.resolve():
            return
        with self._lock:
            self._dirty = True
            self._generation += 1
            self._cards.clear()
            self._hidden_source_tags.update(
                str(source_tag) for source_tag in hidden_source_tags if source_tag
            )

    def refresh(self) -> None:
        with self._refresh_lock:
            with self._lock:
                generation = self._generation
                dirty = self._dirty
            if not dirty and self._snapshot is not None:
                return
            records = _safe_load_records(self.config)
            if records is None:
                with self._lock:
                    if self._generation == generation:
                        jitter = random.uniform(0.9, 1.1)
                        self._retry_at = time.monotonic() + self._retry_delay * jitter
                        self._retry_delay = min(
                            self._retry_delay * 2,
                            _REFRESH_RETRY_MAX_SECONDS,
                        )
                return
            snapshot = RoutingSnapshot(records)
            with self._lock:
                # Validate generation before publication. A concurrent mutation
                # makes this complete result stale; a later single-flight retry
                # will load the newer generation without spinning here.
                if self._generation != generation:
                    return
                self._snapshot = snapshot
                self._dirty = False
                self._retry_at = 0.0
                self._retry_delay = _REFRESH_RETRY_INITIAL_SECONDS
                self._hidden_source_tags.clear()

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
        finally:
            with self._lock:
                if self._refresh_thread is threading.current_thread():
                    self._refresh_thread = None

    def match(self, user_text: str) -> RouteMatch | None:
        with self._lock:
            snapshot = self._snapshot
            dirty = self._dirty
            refresh_running = (
                self._refresh_thread is not None and self._refresh_thread.is_alive()
            )
            retry_ready = time.monotonic() >= self._retry_at
            hidden_source_tags = frozenset(self._hidden_source_tags)
        if snapshot is None:
            self.refresh()
            with self._lock:
                snapshot = self._snapshot
                hidden_source_tags = frozenset(self._hidden_source_tags)
        elif dirty and not refresh_running and retry_ready:
            self.refresh_in_background()
        return (
            snapshot.find(
                user_text,
                excluded_source_tags=hidden_source_tags,
            )
            if snapshot is not None
            else None
        )

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


def _load_records(config: RoutingConfig) -> tuple[RouteRecord, ...]:
    store = KnowledgeStore(config.database_path)
    revision, entries = store.load_routing_entries()
    disabled = load_disabled_entries(get_catalog_override_path(config.database_path))
    records: list[RouteRecord] = []
    for entry in entries:
        if entry_key(entry) in disabled:
            continue
        policy = config.policy
        if (
            policy.allowed_source_tags is not None
            and entry.source_tag not in policy.allowed_source_tags
        ):
            continue
        if any(tag in entry.tags for tag in policy.excluded_entry_tags):
            continue
        strong: list[str] = []
        boundary: list[str] = []
        for index, value in enumerate((entry.title, *entry.aliases)):
            phrase = normalize_search_text(value)
            minimum = policy.title_min_length if index == 0 else policy.alias_min_length
            if len(phrase) >= minimum:
                if policy.latin_word_boundaries and _contains_latin(value):
                    boundary.append(_normalize_latin_boundary_text(value))
                else:
                    strong.append(phrase)
        for value in entry.recognition_terms:
            phrase = normalize_search_text(value)
            if len(phrase) >= policy.recognition_min_length:
                if policy.latin_word_boundaries and _contains_latin(value):
                    boundary.append(_normalize_latin_boundary_text(value))
                else:
                    strong.append(phrase)
        if strong or boundary:
            records.append(
                RouteRecord(
                    database_path=config.database_path,
                    source_tag=entry.source_tag,
                    title=entry.title,
                    strong_terms=tuple(dict.fromkeys(strong)),
                    boundary_terms=tuple(dict.fromkeys(boundary)),
                    revision=revision,
                )
            )
    return tuple(records)


def _safe_load_records(config: RoutingConfig) -> tuple[RouteRecord, ...] | None:
    """Return records, or ``None`` when the load failed.

    ``None`` and ``()`` are different: an empty database legitimately routes
    nothing, while a failure must not be cached as a clean empty snapshot.
    """
    try:
        return _load_records(config)
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
        return None


_LATIN_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _contains_latin(value: str) -> bool:
    return any("a" <= character <= "z" for character in value.casefold())


def _normalize_latin_boundary_text(value: str) -> str:
    tokens = _LATIN_TOKEN_RE.findall(unicodedata.normalize("NFKC", str(value)).casefold())
    return "\0" + "\0".join(tokens) + "\0" if tokens else ""


_STATES: OrderedDict[RoutingConfig, KnowledgeRoutingState] = OrderedDict()
_STATES_LOCK = threading.Lock()


def get_routing_state(config: RoutingConfig) -> KnowledgeRoutingState:
    with _STATES_LOCK:
        state = _STATES.get(config)
        if state is None:
            state = KnowledgeRoutingState(config)
            _STATES[config] = state
            while len(_STATES) > _STATE_CACHE_LIMIT:
                _STATES.popitem(last=False)
        else:
            _STATES.move_to_end(config)
        return state


def notify_database_changed(
    database_path: str | Path,
    *,
    hidden_source_tags: Iterable[str] = (),
) -> None:
    with _STATES_LOCK:
        states = tuple(_STATES.values())
    for state in states:
        state.mark_database_dirty(
            database_path,
            hidden_source_tags=hidden_source_tags,
        )
