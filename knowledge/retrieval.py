"""Read-only retrieval over the local knowledge database."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterable

from .catalog_overrides import (
    CatalogOverrideError,
    entry_key,
    get_catalog_override_path,
    load_disabled_entries,
)
from .filters import folded_exact_surface, make_fts_query, normalize_search_text
from .models import KnowledgeHit
from .store import KnowledgeStore, _entry_from_row


_AUTO_MENTION_MIN_LENGTH = 3
_AUTO_RECOGNITION_MIN_LENGTH = 2
LEXICAL_CANDIDATE_LIMIT = 128
_LEXICAL_CANDIDATE_MINIMUM = 12
_LEXICAL_CANDIDATE_MULTIPLIER = 4
_STALE_USAGE_TAG = "quality:stale-usage"


@dataclass(frozen=True, slots=True)
class MatchPolicy:
    """Trusted matching rules for conversational public knowledge."""

    title_min_length: int = _AUTO_MENTION_MIN_LENGTH
    alias_min_length: int = _AUTO_MENTION_MIN_LENGTH
    recognition_min_length: int = _AUTO_RECOGNITION_MIN_LENGTH
    allowed_source_tags: tuple[str, ...] | None = None
    excluded_entry_tags: tuple[str, ...] = ()
    latin_word_boundaries: bool = False


KNOWLEDGE_MATCH_POLICY = MatchPolicy(
    excluded_entry_tags=(_STALE_USAGE_TAG,),
)


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, int] = field(default_factory=dict)
    entries: list[tuple[object, int]] = field(default_factory=list)


class KnowledgeMentionMatcher:
    """Rebuildable multi-phrase matcher for complete conversational messages.

    This is deliberately a database-derived dictionary, not a list of hand-written
    sentence patterns.  Titles and verified aliases are scanned in one pass, so a
    user never needs to quote a meme for local turn context to find it.
    """

    def __init__(
        self,
        entries: Iterable[object],
        *,
        policy: MatchPolicy = KNOWLEDGE_MATCH_POLICY,
    ) -> None:
        self._policy = policy
        self._nodes = [_TrieNode()]
        self._entry_terms: dict[str, int] = {}
        for entry in entries:
            for term_kind, value in enumerate((entry.title, *entry.aliases)):
                phrase = normalize_search_text(value)
                minimum_length = (
                    policy.title_min_length if term_kind == 0 else policy.alias_min_length
                )
                if len(phrase) >= minimum_length:
                    self._insert(phrase, entry)
            for value in entry.recognition_terms:
                phrase = normalize_search_text(value)
                if len(phrase) < policy.recognition_min_length:
                    continue
                self._insert(phrase, entry)

    def _insert(self, phrase: str, entry: object) -> None:
        node_index = 0
        for character in phrase:
            node = self._nodes[node_index]
            node_index = node.children.setdefault(character, len(self._nodes))
            if node_index == len(self._nodes):
                self._nodes.append(_TrieNode())
        terminal = self._nodes[node_index].entries
        if not any(existing.content_hash == entry.content_hash for existing, _ in terminal):
            terminal.append((entry, len(phrase)))

    def find(self, text: str, *, limit: int) -> list[KnowledgeHit]:
        if limit <= 0:
            return []
        best_by_id: dict[str, tuple[object, int]] = {}
        for start_index in range(len(text)):
            node_index = 0
            for character in text[start_index:]:
                next_index = self._nodes[node_index].children.get(character)
                if next_index is None:
                    break
                node_index = next_index
                for entry, length in self._nodes[node_index].entries:
                    entry_key = entry.content_hash
                    previous = best_by_id.get(entry_key)
                    if previous is None or length > previous[1]:
                        best_by_id[entry_key] = (entry, length)
        hits = [
            KnowledgeHit(entry=entry, score=float(length))
            for entry, length in best_by_id.values()
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.entry.title))
        return hits[:limit]


class KnowledgeRetriever:
    """Retrieve compact, source-attributed candidates without prompt injection."""

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def search(
        self,
        query: str,
        *,
        limit: int = 3,
        allowed_source_tags: tuple[str, ...] | None = None,
        include_disabled: bool = False,
        deadline_monotonic: float | None = None,
        candidate_limit_cap: int = LEXICAL_CANDIDATE_LIMIT,
    ) -> list[KnowledgeHit]:
        query_text = normalize_search_text(query)
        if not query_text or limit <= 0:
            return []
        if allowed_source_tags is not None:
            allowed_source_tags = tuple(dict.fromkeys(allowed_source_tags))
            if not allowed_source_tags:
                return []
        try:
            disabled = (
                frozenset()
                if include_disabled
                else load_disabled_entries(
                    get_catalog_override_path(self.store.database_path)
                )
            )
        except CatalogOverrideError:
            # Automatic retrieval fails closed; management/status endpoints
            # still expose the invalid override as a diagnosable condition.
            return []
        candidate_limit_cap = max(
            int(candidate_limit_cap),
            _LEXICAL_CANDIDATE_MINIMUM,
        )
        candidate_limit = min(
            max(_LEXICAL_CANDIDATE_MINIMUM, limit * _LEXICAL_CANDIDATE_MULTIPLIER),
            candidate_limit_cap,
        )
        rows_by_id: dict[int, object] = {}
        while not _deadline_expired(deadline_monotonic):
            rows = self.store.query_exact_title_or_alias(
                query,
                limit=candidate_limit,
                allowed_source_tags=allowed_source_tags,
            )
            for row in rows:
                rows_by_id.setdefault(row["rowid"], row)
            saturated = len(rows) >= candidate_limit
            if _deadline_expired(deadline_monotonic):
                break

            rows = self.store.query_fts(
                make_fts_query(query),
                limit=candidate_limit,
                allowed_source_tags=allowed_source_tags,
            )
            for row in rows:
                rows_by_id[row["rowid"]] = row
            saturated = saturated or len(rows) >= candidate_limit
            if _deadline_expired(deadline_monotonic):
                break

            rows = self.store.query_like(
                query_text,
                limit=candidate_limit,
                allowed_source_tags=allowed_source_tags,
            )
            for row in rows:
                rows_by_id.setdefault(row["rowid"], row)
            saturated = saturated or len(rows) >= candidate_limit

            hits = _rank_rows(rows_by_id.values(), query, query_text, disabled)
            if (
                len(hits) >= limit
                or not saturated
                or candidate_limit >= candidate_limit_cap
                or _deadline_expired(deadline_monotonic)
            ):
                return hits[:limit]
            candidate_limit = min(candidate_limit * 2, candidate_limit_cap)

        return _rank_rows(rows_by_id.values(), query, query_text, disabled)[:limit]

    def find_mentions(
        self,
        user_text: str,
        *,
        limit: int = 1,
        policy: MatchPolicy = KNOWLEDGE_MATCH_POLICY,
    ) -> list[KnowledgeHit]:
        """Find known phrases anywhere in a normal conversational sentence."""
        normalized_text = normalize_search_text(user_text)
        if len(normalized_text) < 2 or limit <= 0:
            return []
        matcher = _get_cached_mention_matcher(self.store, policy)
        results = matcher.find(normalized_text, limit=max(limit * 2, limit))
        best_by_id: dict[str, KnowledgeHit] = {}
        for hit in results:
            entry_key = hit.entry.content_hash
            previous = best_by_id.get(entry_key)
            if previous is None or hit.score > previous.score:
                best_by_id[entry_key] = hit
        return sorted(best_by_id.values(), key=lambda hit: (-hit.score, hit.entry.title))[:limit]

    def match_turn(
        self,
        user_text: str,
        *,
        policy: MatchPolicy = KNOWLEDGE_MATCH_POLICY,
        limit: int = 1,
    ) -> tuple[str, list[KnowledgeHit]]:
        """Return explicit title, alias, or recognition-term matches."""
        strong = self.find_mentions(user_text, limit=limit, policy=policy)
        if strong:
            return "strong", strong
        return "none", []


def _deadline_expired(deadline_monotonic: float | None) -> bool:
    return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic


def _rank_rows(
    rows: Iterable[object],
    query: str,
    query_text: str,
    disabled: frozenset[tuple[str, str]],
) -> list[KnowledgeHit]:
    hits: list[KnowledgeHit] = []
    for row in rows:
        try:
            entry = _entry_from_row(row)
        except (TypeError, ValueError, json.JSONDecodeError):
            # A damaged row must not make public-knowledge lookup block a
            # conversation.  Later management tooling can report it.
            continue
        if entry_key(entry) in disabled:
            continue
        score = _score(
            entry,
            query_text,
            folded_exact_surface(query),
            float(row["rank"]) if "rank" in row.keys() else 0.0,
        )
        hits.append(KnowledgeHit(entry=entry, score=score))
    hits.sort(key=lambda hit: (-hit.score, hit.entry.title))
    return hits


@dataclass(slots=True)
class _CachedMentionMatcher:
    revision: int
    disabled: frozenset[tuple[str, str]]
    matcher: KnowledgeMentionMatcher


_MENTION_MATCHER_CACHE: dict[tuple[str, MatchPolicy], _CachedMentionMatcher] = {}


def _get_cached_mention_matcher(
    store: KnowledgeStore,
    policy: MatchPolicy = KNOWLEDGE_MATCH_POLICY,
) -> KnowledgeMentionMatcher:
    """Refresh the per-database matcher only after a committed upsert batch."""
    cache_key = (str(store.database_path.resolve()), policy)
    revision = store.entries_revision()
    try:
        disabled = load_disabled_entries(get_catalog_override_path(store.database_path))
    except CatalogOverrideError:
        # Never reuse a matcher built from an override state that is no longer
        # trustworthy. Do not cache this empty matcher so a repaired file takes
        # effect on the next request.
        return KnowledgeMentionMatcher((), policy=policy)
    cached = _MENTION_MATCHER_CACHE.get(cache_key)
    if cached is None or cached.revision != revision or cached.disabled != disabled:
        cached = _CachedMentionMatcher(
            revision=revision,
            disabled=disabled,
            matcher=KnowledgeMentionMatcher(
                (
                    entry
                    for entry in store.list_active_entries()
                    if entry_key(entry) not in disabled
                    and (
                        policy.allowed_source_tags is None
                        or entry.source_tag in policy.allowed_source_tags
                    )
                    and not any(tag in entry.tags for tag in policy.excluded_entry_tags)
                ),
                policy=policy,
            ),
        )
        _MENTION_MATCHER_CACHE[cache_key] = cached
    return cached.matcher


def _score(
    entry,
    normalized_query: str,
    query_surface: str,
    fts_rank: float,
) -> float:
    title = normalize_search_text(entry.title)
    aliases = [normalize_search_text(value) for value in entry.aliases]
    recognition_terms = [normalize_search_text(value) for value in entry.recognition_terms]
    tags = [normalize_search_text(value) for value in entry.tags]
    if query_surface == folded_exact_surface(entry.title):
        return 1_000.0
    if query_surface in {
        folded_exact_surface(value)
        for value in entry.aliases
    }:
        return 950.0
    if normalized_query in recognition_terms:
        return 900.0
    if normalized_query in title:
        return 850.0
    if any(normalized_query in alias for alias in aliases):
        return 800.0
    if any(normalized_query in value for value in recognition_terms):
        return 780.0
    if any(normalized_query in tag for tag in tags):
        return 700.0
    return 100.0 - fts_rank
