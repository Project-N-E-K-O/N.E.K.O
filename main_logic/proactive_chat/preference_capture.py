"""Capture recommendation preferences from ordinary chat entirely on device."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
from pathlib import Path

from portalocker.exceptions import LockException

from utils.proactive_preferences.personalization import ChatPreferenceEvidence
from utils.proactive_preferences.preference_model import (
    SCOPES,
    PreferenceEvent,
    normalize,
    valid_feature,
)
from utils.proactive_preferences.search_policy import (
    LOCAL_ONLY_SCOPES,
    SearchPermission,
    may_search_phrase,
)


def capture_chat_source_owner() -> str | None:
    """Read the accepted input's owner without waiting for the credential writer lock."""
    from services.proactive_feedback import host

    try:
        owner = host.owner_from_snapshot(host.credentials.saved_session_snapshot())
        status, account_id = host.credentials.signed_in_account_state()
        # Legacy authenticated sessions without an account ID cannot own evidence.
        if (status == "signed_out" and not owner["user_id"]) or (
            status == "signed_in" and account_id and account_id == owner["user_id"]
        ):
            return owner["key"]
    except (OSError, RuntimeError, ValueError):
        pass
    return None


async def remember_chat_source(
    character: str, source_id: str, text: str, *, owner_key: str, config_manager
) -> None:
    """Persist the provenance of a locally accepted original for later analysis."""
    from main_logic.recommendation_feedback import RecommendationStore
    from services.proactive_feedback import host

    def remember():
        digest = hashlib.sha256(text.strip().encode()).hexdigest()
        path = Path(
            config_manager.get_runtime_config_path("recommendation_feedback.sqlite3")
        )
        with host.attribution_context() as owner:
            if owner["key"] == owner_key:
                RecommendationStore(path).remember_chat_source(owner_key, character, source_id, digest)

    try:
        await asyncio.to_thread(remember)
    except (
        AttributeError,
        OSError,
        sqlite3.Error,
        RuntimeError,
        ValueError,
        LockException,
    ):
        pass


def annotate_chat_sources(
    context: str,
    rows: object,
    *,
    owned: dict[str, str],
    analyzed: frozenset[str],
    start: int = 0,
) -> tuple[str, dict[int, tuple[str, int]]]:
    """Add handles to owned user turns already present in the existing Phase 1 context."""
    if not isinstance(rows, list):
        return context, {}
    now = int(time.time())
    eligible = {}
    for row in rows[-32:]:
        if not isinstance(row, dict):
            continue
        source, at, offset = (row.get(key) for key in ("id", "at", "offset"))
        if (
            isinstance(source, str)
            and source in owned
            and source not in analyzed
            and row.get("digest") == owned[source]
            and type(at) is int
            and now - 7 * 86400 <= at <= now + 60
            and type(offset) is int
            and start <= offset < len(context)
            and (offset == 0 or context[offset - 1] == "\n")
        ):
            eligible[source] = (at, offset)

    latest = sorted(eligible.items(), key=lambda item: item[1][1], reverse=True)[:4]
    refs = {}
    for label, (source, (at, offset)) in enumerate(latest, 1):
        context = context[:offset] + f"[P{label}] " + context[offset:]
        refs[label] = (source, at)
    return context, refs


def _event_key(event_id: str, scope: str, feature: str, kind: str) -> str:
    group = "constraint" if kind in {"exclude", "allow"} else "interest"
    return hashlib.sha256(
        f"{event_id}\0{scope}\0{feature}\0{group}".encode()
    ).hexdigest()


def _semantic_events(
    items: list[dict], event_id: str, at: int
) -> tuple[list[PreferenceEvent], dict[str, SearchPermission]]:
    events = {}
    permissions = {}
    for item in items:
        feature, scope, kind, direction, confidence = (
            item.get(key)
            for key in ("feature", "scope", "kind", "direction", "confidence")
        )
        if (
            not all(isinstance(value, str) for value in (feature, scope, kind))
            or scope not in SCOPES
            or kind not in {"explicit", "inferred", "exclude", "allow"}
            or type(direction) is not int
            or direction not in {-1, 1}
            or (kind == "exclude" and direction != -1)
            or (kind == "allow" and direction != 1)
            or type(confidence) not in {int, float}
            or not (0.8 if kind == "inferred" else 0.65) <= confidence <= 1
        ):
            continue
        feature = normalize(feature)
        if not valid_feature(feature):
            continue
        key = _event_key(event_id, scope, feature, kind)
        events[key] = PreferenceEvent(key, feature, scope, kind, direction, at)
        permissions[key] = SearchPermission(
            item.get("public") is True
            and scope not in LOCAL_ONLY_SCOPES
            and may_search_phrase(feature),
            item.get("game") is True,
        )
    return list(events.values()), permissions


def build_chat_evidence(
    items: object, refs: dict[int, tuple[str, int]]
) -> dict[str, ChatPreferenceEvidence]:
    if not refs or not isinstance(items, list) or len(items) > 8:
        return {}
    by_source: dict[int, list[dict]] = {label: [] for label in refs}
    for item in items:
        if not isinstance(item, dict):
            return {}
        label = item.get("source")
        if type(label) is not int or label not in refs:
            return {}
        by_source[label].append(item)
    evidence = {}
    for label, (source, at) in refs.items():
        events, permissions = _semantic_events(by_source[label], source, at)
        evidence[source] = ChatPreferenceEvidence(at, events, permissions)
    return evidence
