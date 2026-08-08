"""Read-only retrieval over a local knowledge database."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Iterable

from .catalog_overrides import entry_key, get_catalog_override_path, load_disabled_entries
from .filters import make_fts_query, normalize_search_text
from .models import KnowledgeHit
from .store import KnowledgeStore, _entry_from_row


_AUTO_MENTION_MIN_LENGTH = 3
_AUTO_RECOGNITION_MIN_LENGTH = 2
@dataclass(frozen=True, slots=True)
class MatchPolicy:
    """Trusted matching rules shared by all conversational collections."""

    title_min_length: int = _AUTO_MENTION_MIN_LENGTH
    alias_min_length: int = _AUTO_MENTION_MIN_LENGTH
    recognition_min_length: int = _AUTO_RECOGNITION_MIN_LENGTH
    allowed_source_tags: tuple[str, ...] | None = None
    excluded_entry_tags: tuple[str, ...] = ()
    weak_term_length: int = 0
    weak_required_tags: tuple[str, ...] = ()
    weak_required_tag_prefixes: tuple[str, ...] = ()
    weak_excluded_tags: tuple[str, ...] = ()
    weak_content_line_prefix: str = ""
    latin_word_boundaries: bool = False
    normalizer: Callable[[str], str] = normalize_search_text


DEFAULT_MATCH_POLICY = MatchPolicy()


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, int] = field(default_factory=dict)
    entries: list[tuple[object, int]] = field(default_factory=list)


class KnowledgeMentionMatcher:
    """Rebuildable multi-phrase matcher for complete conversational messages.

    This is deliberately a database-derived dictionary, not a list of hand-written
    sentence patterns.  Titles and verified aliases are scanned in one pass, so a
    user never needs to quote a known term for local turn context to find it.
    """

    def __init__(
        self,
        entries: Iterable[object],
        *,
        policy: MatchPolicy = DEFAULT_MATCH_POLICY,
    ) -> None:
        self._policy = policy
        self._nodes = [_TrieNode()]
        self._entry_terms: dict[str, int] = {}
        self._weak_short_terms: list[tuple[str, object, int]] = []
        for entry in entries:
            for term_kind, value in enumerate((entry.title, *entry.aliases)):
                phrase = policy.normalizer(value)
                minimum_length = (
                    policy.title_min_length if term_kind == 0 else policy.alias_min_length
                )
                if len(phrase) >= minimum_length:
                    self._insert(phrase, entry)
                elif (
                    policy.weak_term_length > 0
                    and len(phrase) == policy.weak_term_length
                    and _is_weak_entry(entry, policy)
                ):
                    # Title wins over aliases at the same position.  All aliases
                    # share the same secondary priority because their stored order
                    # is not semantic evidence.
                    self._weak_short_terms.append((phrase, entry, min(term_kind, 1)))
            for value in entry.recognition_terms:
                phrase = policy.normalizer(value)
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

    def find_weak_short(self, text: str, *, limit: int) -> list[KnowledgeHit]:
        """Find policy-approved short terms by exact continuous text."""
        if limit <= 0:
            return []
        best_by_id: dict[str, tuple[int, int, str, object]] = {}
        for phrase, entry, term_priority in self._weak_short_terms:
            position = text.find(phrase)
            if position < 0:
                continue
            candidate = (position, term_priority, entry.title, entry)
            previous = best_by_id.get(entry.content_hash)
            if previous is None or candidate[:3] < previous[:3]:
                best_by_id[entry.content_hash] = candidate
        ordered = sorted(best_by_id.values(), key=lambda value: value[:3])
        return [
            KnowledgeHit(entry=entry, score=float(self._policy.weak_term_length))
            for _, _, _, entry in ordered[:limit]
        ]

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
    ) -> list[KnowledgeHit]:
        query_text = normalize_search_text(query)
        if not query_text or limit <= 0:
            return []
        if allowed_source_tags is not None:
            allowed_source_tags = tuple(dict.fromkeys(allowed_source_tags))
            if not allowed_source_tags:
                return []
        candidate_limit = max(12, limit * 4)
        rows_by_id = {}
        for row in self.store.query_fts(
            make_fts_query(query),
            limit=candidate_limit,
            allowed_source_tags=allowed_source_tags,
        ):
            rows_by_id[row["rowid"]] = row
        for row in self.store.query_like(
            query_text,
            limit=candidate_limit,
            allowed_source_tags=allowed_source_tags,
        ):
            rows_by_id.setdefault(row["rowid"], row)

        disabled = (
            frozenset()
            if include_disabled
            else load_disabled_entries(
                get_catalog_override_path(self.store.database_path)
            )
        )
        hits: list[KnowledgeHit] = []
        for row in rows_by_id.values():
            try:
                entry = _entry_from_row(row)
            except (TypeError, ValueError, json.JSONDecodeError):
                # A damaged row must not make public-knowledge lookup block a
                # conversation.  Later management tooling can report it.
                continue
            if entry_key(entry) in disabled:
                continue
            score = _score(entry, query_text, float(row["rank"]) if "rank" in row.keys() else 0.0)
            hits.append(KnowledgeHit(entry=entry, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.entry.title))
        return hits[:limit]

    def find_mentions(
        self,
        user_text: str,
        *,
        limit: int = 1,
        policy: MatchPolicy = DEFAULT_MATCH_POLICY,
    ) -> list[KnowledgeHit]:
        """Find known phrases anywhere in a normal conversational sentence."""
        normalized_text = policy.normalizer(user_text)
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

    def find_weak_short_mentions(
        self,
        user_text: str,
        *,
        limit: int = 1,
        policy: MatchPolicy = DEFAULT_MATCH_POLICY,
    ) -> list[KnowledgeHit]:
        """Find cautious two-character candidates after strong matching misses."""
        normalized_text = policy.normalizer(user_text)
        if policy.weak_term_length <= 0 or len(normalized_text) < policy.weak_term_length or limit <= 0:
            return []
        return _get_cached_mention_matcher(self.store, policy).find_weak_short(
            normalized_text,
            limit=limit,
        )

    def match_turn(
        self,
        user_text: str,
        *,
        policy: MatchPolicy = DEFAULT_MATCH_POLICY,
        limit: int = 1,
    ) -> tuple[str, list[KnowledgeHit]]:
        """Return strong matches first, then policy-approved weak matches."""
        strong = self.find_mentions(user_text, limit=limit, policy=policy)
        if strong:
            return "strong", strong
        weak = self.find_weak_short_mentions(user_text, limit=limit, policy=policy)
        if weak:
            return "weak_short", weak
        return "none", []

@dataclass(slots=True)
class _CachedMentionMatcher:
    revision: int
    disabled: frozenset[tuple[str, str]]
    matcher: KnowledgeMentionMatcher


_MENTION_MATCHER_CACHE_LIMIT = 16
_MENTION_MATCHER_CACHE: OrderedDict[
    tuple[str, MatchPolicy], _CachedMentionMatcher
] = OrderedDict()
_MENTION_MATCHER_CACHE_LOCK = threading.Lock()


def _get_cached_mention_matcher(
    store: KnowledgeStore,
    policy: MatchPolicy = DEFAULT_MATCH_POLICY,
) -> KnowledgeMentionMatcher:
    """Refresh the per-database matcher only after a committed upsert batch."""
    cache_key = (str(store.database_path.resolve()), policy)
    override_path = get_catalog_override_path(store.database_path)
    while True:
        revision = store.entries_revision()
        disabled = load_disabled_entries(override_path)
        with _MENTION_MATCHER_CACHE_LOCK:
            observed = _MENTION_MATCHER_CACHE.get(cache_key)
            if (
                observed is not None
                and observed.revision == revision
                and observed.disabled == disabled
            ):
                _MENTION_MATCHER_CACHE.move_to_end(cache_key)
                return observed.matcher

        candidate = _CachedMentionMatcher(
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
        if (
            store.entries_revision() != revision
            or load_disabled_entries(override_path) != disabled
        ):
            continue

        with _MENTION_MATCHER_CACHE_LOCK:
            current = _MENTION_MATCHER_CACHE.get(cache_key)
            if current is not observed:
                if (
                    current is not None
                    and current.revision == revision
                    and current.disabled == disabled
                ):
                    _MENTION_MATCHER_CACHE.move_to_end(cache_key)
                    return current.matcher
                continue
            _MENTION_MATCHER_CACHE[cache_key] = candidate
            _MENTION_MATCHER_CACHE.move_to_end(cache_key)
            while len(_MENTION_MATCHER_CACHE) > _MENTION_MATCHER_CACHE_LIMIT:
                _MENTION_MATCHER_CACHE.popitem(last=False)
            return candidate.matcher


def _is_weak_entry(entry: object, policy: MatchPolicy) -> bool:
    """Evaluate a weak hint using trusted collection data, not source code hooks."""
    tags = tuple(entry.tags)
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


def _score(entry, normalized_query: str, fts_rank: float) -> float:
    title = normalize_search_text(entry.title)
    aliases = [normalize_search_text(value) for value in entry.aliases]
    recognition_terms = [normalize_search_text(value) for value in entry.recognition_terms]
    tags = [normalize_search_text(value) for value in entry.tags]
    if normalized_query == title:
        return 1_000.0
    if normalized_query in aliases:
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
