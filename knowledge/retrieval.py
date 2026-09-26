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
