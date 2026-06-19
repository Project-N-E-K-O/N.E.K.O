"""topic_sim.py — thin testbench adapter over the deep-topic readiness/units layer.

Scope (UPSTREAM_SYNC_2026-06 Phase 3, RAG item #7 = O)
-----------------------------------------------------
Re-exposes the **semantic-contract** half of the background topic-hook package
(``main_logic.topic``): the deterministic readiness gate + topic-unit
tokenization + language/age formatting, **without** the runtime-mechanism half
(``materials.enrich_topic_materials_online`` = aiohttp web enrichment,
``delivery`` = session-manager callback + TTS/WS, ``pipeline`` = the LLM
candidate call). Those are out-of-scope per Phase 3.0 (and partially overlap
external-proactive B3).

Import note: unlike ``main_logic.cross_server`` (ssl/aiohttp import-time side
effects — which is why avatar-dedupe uses copy+drift), importing
``main_logic.topic.signals`` / ``.common`` was verified side-effect-free and
fast, so this adapter imports them directly (wanted coupling: a contract change
upstream should break the paired smoke). Imports stay lazy inside functions.

Wraps:
  - ``main_logic.topic.common.topic_units / clean_text / is_zh_lang``
  - ``main_logic.topic.signals._is_meaningful_turn / _label_key_for_lang``
  - ``main_logic.topic.signals.TopicSignalStore`` (in-memory, no persistence)
"""
from __future__ import annotations

from typing import Any


def _common():
    from main_logic.topic import common as _mod  # noqa: WPS433 — intentional lazy import
    return _mod


def _signals():
    from main_logic.topic import signals as _mod  # noqa: WPS433
    return _mod


def topic_units(text: str, **kwargs: Any) -> set[str]:
    """Topic keyword/unit set (CJK singles+bigrams + Latin/CJK runs, stop-char
    filtered) — production's ``common.topic_units``."""
    return _common().topic_units(text, **kwargs)


def clean_text(value: Any, *, limit: int | None = 120) -> str:
    """Whitespace-collapse + truncate — production's ``common.clean_text``."""
    return _common().clean_text(value, limit=limit)


def is_zh_lang(lang: str | None) -> bool:
    """Whether ``lang`` is a Chinese variant — production's ``common.is_zh_lang``."""
    return _common().is_zh_lang(lang)


def is_meaningful_turn(text: str) -> bool:
    """Whether a user turn counts toward readiness (non-filler, >=3 signal
    chars) — production's ``signals._is_meaningful_turn``."""
    return _signals()._is_meaningful_turn(text)  # noqa: SLF001


def label_key_for_lang(lang: str | None) -> str:
    """Normalize a language tag to a signal-label key (zh / zh-TW / en / ... ) —
    production's ``signals._label_key_for_lang``."""
    return _signals()._label_key_for_lang(lang)  # noqa: SLF001


def make_store(
    *,
    min_user_turns_for_topic: int = 8,
    retention_seconds: float = 10 ** 9,
):
    """Build an in-memory ``TopicSignalStore`` (no persistence path -> no disk /
    timer / atexit). ``retention_seconds`` defaults huge so count-based
    readiness assertions stay wall-clock-independent.
    """
    return _signals().TopicSignalStore(
        min_user_turns_for_topic=min_user_turns_for_topic,
        retention_seconds=retention_seconds,
        persistence_path=None,
    )


def readiness_from_user_texts(
    texts: list[str],
    *,
    min_user_turns_for_topic: int = 8,
) -> tuple[bool, int]:
    """Feed ``texts`` as user turns into a fresh in-memory store and return
    ``(is_ready, readiness_percent)``. Filler turns won't count toward the gate.
    """
    store = make_store(min_user_turns_for_topic=min_user_turns_for_topic)
    for t in texts:
        store.note_turn("tb", actor="user", text=t)
    return store.is_ready("tb"), store.readiness_percent("tb")


__all__ = [
    "clean_text",
    "is_meaningful_turn",
    "is_zh_lang",
    "label_key_for_lang",
    "make_store",
    "readiness_from_user_texts",
    "topic_units",
]
