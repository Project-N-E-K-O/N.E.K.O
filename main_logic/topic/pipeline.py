"""Background topic hook collection for proactive chat.

Ordinary chat must never wait for topic screening, online enrichment, or
prompt building. This module keeps the synchronous entrypoints tiny: record a
recent turn, optionally schedule a background worker, and return immediately.
The proactive endpoint reads only the prepared pool.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from main_logic.topic.common import ZH_TOPIC_STOP_CHARS, clean_text, topic_units
from main_logic.topic.materials import enrich_topic_materials_online
from main_logic.topic.signals import TopicSignalStore


logger = logging.getLogger("N.E.K.O.Main.topic.pipeline")

Analyzer = Callable[
    ...,
    Awaitable[Iterable[Mapping[str, Any]] | None],
]
TopicTrigger = Callable[
    ...,
    Awaitable[bool],
]

_MAX_TEXT_CHARS = 1000
_PROCESS_DEBOUNCE_SECONDS = 45.0
_TRIGGER_AFTER_QUIET_SECONDS = 60.0
_MIN_TOPIC_TRIGGER_GAP_SECONDS = 4 * 60 * 60
_MAX_DAILY_TOPIC_TRIGGERS = 2
_USED_TOPIC_RECENT_SECONDS = 24 * 60 * 60


def _clean_text(value: Any, *, limit: int = _MAX_TEXT_CHARS) -> str:
    return clean_text(value, limit=limit)


def _clean_media_intent(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        raw_items = list(value)
    else:
        raw_items = []
    intents: list[str] = []
    for item in raw_items:
        text = _clean_text(item, limit=30).lower()
        if text and text not in intents:
            intents.append(text)
    return (intents or ["news"])[:2]


def _clean_timestamp(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.time()


def _local_day(value: float) -> date:
    return datetime.fromtimestamp(value).date()


def _clean_material(material: Mapping[str, Any]) -> dict[str, Any] | None:
    interest = _clean_text(material.get("interest"), limit=90)
    if not interest:
        return None
    try:
        relevance = int(material.get("relevance", 70))
    except (TypeError, ValueError):
        relevance = 70
    try:
        risk = int(material.get("risk", 20))
    except (TypeError, ValueError):
        risk = 20
    return {
        "hook_id": str(material.get("hook_id") or ""),
        "source": "background_topic_pool",
        "interest": interest,
        "media_intent": _clean_media_intent(material.get("media_intent")),
        "keywords": _clean_keywords(material.get("keywords")),
        "relevance": max(0, min(100, relevance)),
        "risk": max(0, min(100, risk)),
        "status": "pending",
        "created_at": _clean_timestamp(material.get("created_at")),
    }


def _material_is_ready(material: Mapping[str, Any]) -> bool:
    try:
        relevance = int(material.get("relevance", 0))
        risk = int(material.get("risk", 20))
    except (TypeError, ValueError):
        return False
    return relevance >= 70 and risk <= 65


def _material_log_preview(material: Mapping[str, Any]) -> str:
    hint = material.get("material_hint")
    hint_summary = ""
    if isinstance(hint, Mapping):
        hint_summary = _clean_text(hint.get("summary"), limit=100)
    parts = [
        f"relevance={material.get('relevance')}",
        f"risk={material.get('risk')}",
        f"interest={_clean_text(material.get('interest'), limit=80)}",
    ]
    if material.get("online_used"):
        parts.append(f"online={_clean_text(material.get('online_angle'), limit=100)}")
    if hint_summary:
        parts.append(f"hint={hint_summary}")
    return " | ".join(parts)


def _topic_units(text: str) -> set[str]:
    return topic_units(text, limit=240, stop_chars=ZH_TOPIC_STOP_CHARS)


def _material_topic_units(material: Mapping[str, Any]) -> set[str]:
    return _topic_units(
        " ".join(
            str(material.get(key) or "")
            for key in ("interest", "online_query", "online_angle")
        )
    )


def _topic_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return overlap / max(1, min(len(left), len(right)))


def _clean_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        raw_items = list(value)
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        text = _clean_text(item, limit=30)
        if text and text not in out:
            out.append(text)
        if len(out) >= 6:
            break
    return out


def _material_keywords(material: Mapping[str, Any]) -> set[str]:
    return {
        kw.strip().lower()
        for kw in (material.get("keywords") or [])
        if isinstance(kw, str) and kw.strip()
    }


def _material_bigram_units(material: Mapping[str, Any]) -> set[str]:
    return {unit for unit in _material_topic_units(material) if len(unit) >= 2}


async def _default_analyzer(*, lang: str, global_signals: str = ""):
    from main_logic.activity.llm_enrichment import call_topic_candidates

    return await call_topic_candidates(lang=lang, global_signals=global_signals)


def _privacy_mode_active() -> bool:
    try:
        from utils.preferences import is_privacy_mode_enabled
        return is_privacy_mode_enabled()
    except Exception:
        return True


class TopicHookPool:
    """In-memory per-character topic pool prepared by background work."""

    def __init__(
        self,
        *,
        analyzer: Analyzer | None = None,
        topic_trigger: TopicTrigger | None = None,
        auto_schedule: bool = True,
        enable_online_enrichment: bool = True,
        enable_deep_search: bool = True,
        debounce_seconds: float = _PROCESS_DEBOUNCE_SECONDS,
        trigger_delay_seconds: float = _TRIGGER_AFTER_QUIET_SECONDS,
        min_trigger_gap_seconds: float = _MIN_TOPIC_TRIGGER_GAP_SECONDS,
        min_user_turns_for_topic: int = 4,
        daily_topic_limit: int = _MAX_DAILY_TOPIC_TRIGGERS,
    ) -> None:
        self._analyzer = analyzer or _default_analyzer
        self._topic_trigger = topic_trigger
        self._auto_schedule = auto_schedule
        self._enable_online_enrichment = enable_online_enrichment
        self._enable_deep_search = enable_deep_search
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._trigger_delay_seconds = max(0.0, float(trigger_delay_seconds))
        self._min_trigger_gap_seconds = max(0.0, float(min_trigger_gap_seconds))
        self._daily_topic_limit = max(0, int(daily_topic_limit))
        self._signal_store = TopicSignalStore(
            min_user_turns_for_topic=min_user_turns_for_topic,
        )
        self._langs: dict[str, str] = {}
        self._materials: dict[str, list[dict[str, Any]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._trigger_tasks: dict[str, asyncio.Task] = {}
        self._used_topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._dirty: set[str] = set()
        self._seq: dict[str, int] = defaultdict(int)

    def _purge_character_state(self, name: str) -> None:
        """Drop all accumulated topic state for a character (privacy wipe)."""
        self._signal_store.clear(name)
        self._materials.pop(name, None)
        self._dirty.discard(name)

    def note_user_message(self, lanlan_name: str, text: Any, *, lang: str = "zh") -> None:
        cleaned = _clean_text(text)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        self._seq[name] += 1
        self._signal_store.note_turn(name, actor="user", text=cleaned)
        self._langs[name] = lang or self._langs.get(name, "zh")
        self._dirty.add(name)
        self._cancel_trigger(name)
        self._schedule(name)

    def note_ai_message(self, lanlan_name: str, text: Any, *, lang: str = "zh") -> None:
        cleaned = _clean_text(text)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        self._seq[name] += 1
        self._signal_store.note_turn(name, actor="ai", text=cleaned)
        self._langs[name] = lang or self._langs.get(name, "zh")
        self._dirty.add(name)
        self._cancel_trigger(name)
        self._schedule(name)

    def get_ready_materials(self, lanlan_name: str, *, max_items: int = 2) -> list[dict[str, Any]]:
        name = str(lanlan_name or "default")
        materials = sorted(
            [
                item
                for item in self._materials.get(name, [])
                if item.get("status") == "pending"
            ],
            key=lambda item: int(item.get("relevance", 0)),
            reverse=True,
        )
        return deepcopy(materials[:max_items])

    async def process_now(
        self,
        lanlan_name: str,
        *,
        lang: str | None = None,
        enrich_online: bool | None = None,
    ) -> None:
        name = str(lanlan_name or "default")
        if _privacy_mode_active():
            self._purge_character_state(name)
            return
        seen_seq = self._seq.get(name, 0)
        if self._daily_quota_reached(name):
            logger.info("[%s] topic collection paused: daily topic quota reached", name)
            self._materials.pop(name, None)
            self._dirty.discard(name)
            return
        if not self._signal_store.is_ready(name):
            logger.info(
                "[%s] topic collection not ready: %s%%",
                name,
                self._signal_store.readiness_percent(name),
            )
            self._materials.pop(name, None)
            self._dirty.discard(name)
            return
        topic_lang = lang or self._langs.get(name, "zh")
        global_signals = self._signal_store.format_global_signals(name, lang=topic_lang)
        raw_materials = await self._analyzer(
            lang=topic_lang,
            global_signals=global_signals,
        )
        if raw_materials is None:
            logger.info("[%s] topic analyzer returned no result; keeping dirty for retry", name)
            return
        if self._seq.get(name, 0) != seen_seq:
            return
        cleaned = [
            material
            for material in (_clean_material(item) for item in raw_materials)
            if material is not None and _material_is_ready(material)
        ]
        cleaned = sorted(
            cleaned,
            key=lambda item: int(item.get("relevance", 0)),
            reverse=True,
        )[:2]
        if cleaned and (self._enable_online_enrichment if enrich_online is None else enrich_online):
            cleaned = await enrich_topic_materials_online(cleaned, lang=topic_lang, max_materials=1)
        if self._seq.get(name, 0) != seen_seq:
            return
        if _privacy_mode_active():
            # Privacy may have toggled on during the analyzer / enrichment
            # awaits above; the start-of-call wipe already passed. Re-check
            # before storing, else material collected across a privacy interval
            # would survive and could deliver if privacy toggles off again
            # before the quiet-window trigger.
            self._purge_character_state(name)
            logger.info("[%s] topic material discarded: privacy turned on during analysis", name)
            return
        cleaned = self._filter_available_materials(name, cleaned)
        if self._daily_quota_reached(name):
            cleaned = []
        self._materials[name] = cleaned
        if cleaned:
            for idx, material in enumerate(cleaned, start=1):
                logger.info(
                    "[%s] topic material ready #%d: %s",
                    name,
                    idx,
                    _material_log_preview(material),
                )
            self._schedule_trigger(name, cleaned[0], topic_lang, expected_seq=self._seq.get(name, 0))
        else:
            logger.info("[%s] topic material ready: none", name)
        self._dirty.discard(name)

    def _schedule(self, name: str) -> None:
        if not self._auto_schedule:
            return
        task = self._tasks.get(name)
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tasks[name] = loop.create_task(self._run_later(name), name=f"topic_pool_{name}")

    async def _run_later(self, name: str) -> None:
        try:
            if self._debounce_seconds:
                await asyncio.sleep(self._debounce_seconds)
            if name in self._dirty:
                await self.process_now(name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[%s] topic background processing failed: %s", name, exc)
        finally:
            task = self._tasks.get(name)
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                current_task = None
            if task is current_task:
                self._tasks.pop(name, None)
            if name in self._dirty:
                self._schedule(name)

    def _cancel_trigger(self, name: str) -> None:
        task = self._trigger_tasks.pop(name, None)
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        if task is not None and task is not current_task and not task.done():
            task.cancel()

    def _schedule_trigger(
        self,
        name: str,
        material: Mapping[str, Any],
        lang: str,
        *,
        expected_seq: int,
    ) -> None:
        if self._topic_trigger is None:
            return
        self._cancel_trigger(name)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._trigger_tasks[name] = loop.create_task(
            self._run_trigger_after_quiet_window(
                name,
                deepcopy(dict(material)),
                lang,
                expected_seq=expected_seq,
            ),
            name=f"topic_trigger_{name}",
        )

    def _reschedule_trigger_retry(
        self,
        name: str,
        material: Mapping[str, Any],
        lang: str,
        *,
        expected_seq: int,
    ) -> None:
        if self._seq.get(name, 0) != expected_seq or material.get("status") != "pending":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._trigger_tasks[name] = loop.create_task(
            self._run_trigger_after_quiet_window(
                name,
                deepcopy(dict(material)),
                lang,
                expected_seq=expected_seq,
            ),
            name=f"topic_trigger_{name}",
        )

    async def _run_trigger_after_quiet_window(
        self,
        name: str,
        material: dict[str, Any],
        lang: str,
        *,
        expected_seq: int,
    ) -> None:
        current_material: dict[str, Any] | None = None
        try:
            wait_seconds = max(
                self._trigger_delay_seconds,
                self._seconds_until_next_topic_trigger(name),
            )
            if wait_seconds:
                await asyncio.sleep(wait_seconds)
            if _privacy_mode_active():
                self._purge_character_state(name)
                logger.info("[%s] topic material trigger cancelled: privacy mode active", name)
                return
            if self._seq.get(name, 0) != expected_seq:
                return
            current = self._materials.get(name) or []
            if not current:
                return
            hook_id = material.get("hook_id")
            current_material = current[0]
            if hook_id and current_material.get("hook_id") != hook_id:
                return
            if current_material.get("status") != "pending":
                return
            if self._daily_quota_reached(name) or self._topic_was_used_today(name, current_material):
                current_material["status"] = "skipped"
                self._materials[name] = []
                logger.info("[%s] topic material trigger skipped: already used or daily quota reached", name)
                return
            # "Search first, then chat": prepare a deeper, big-model-derived
            # online lead before attempting to open. This runs in the trigger
            # task (off the user hot path) and caches onto current_material, so
            # a reschedule reuses it instead of re-searching. The actual open is
            # re-gated by the delivery bridge AFTER this prepare completes.
            await self._deepen_material(name, current_material, lang)
            triggered = await self._topic_trigger(
                lanlan_name=name,
                material=deepcopy(current_material),
                lang=lang,
            )
            if not triggered:
                logger.info("[%s] topic material trigger skipped by delivery bridge", name)
                self._reschedule_trigger_retry(
                    name,
                    current_material,
                    lang,
                    expected_seq=expected_seq,
                )
                return
            current_material["status"] = "used"
            current_material["used_at"] = time.time()
            self._mark_topic_used(name, current_material)
            logger.info(
                "[%s] topic material triggered once: %s",
                name,
                _material_log_preview(current_material),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[%s] topic material trigger failed: %s", name, exc)
            if current_material is not None:
                self._reschedule_trigger_retry(
                    name,
                    current_material,
                    lang,
                    expected_seq=expected_seq,
                )
        finally:
            task = self._trigger_tasks.get(name)
            if task is asyncio.current_task():
                self._trigger_tasks.pop(name, None)

    async def _deepen_material(
        self, name: str, material: dict[str, Any], lang: str
    ) -> None:
        """Delivery-time deep search: derive a focused query and re-enrich.

        Idempotent per material (``deep_search_done``) so a rescheduled trigger
        reuses the prepared lead instead of re-searching. Any failure leaves the
        cheap keyword floor hint intact, and ``deep_search_done`` is set up front
        so a flaky derivation is not retried on every reschedule.
        """
        if not self._enable_deep_search:
            return
        if material.get("deep_search_done"):
            return
        material["deep_search_done"] = True
        try:
            from main_logic.activity.llm_enrichment import derive_deep_search_query
            query = await derive_deep_search_query(
                interest=str(material.get("interest") or ""),
                keywords=list(material.get("keywords") or []),
                floor_angle=str(material.get("online_angle") or ""),
                lang=lang,
            )
        except Exception as exc:
            logger.debug("[%s] deep search query derivation failed: %s", name, exc)
            return
        if not query:
            return
        material["deep_query"] = query
        # Re-run online enrichment with the derived query. Clear the floor hint
        # so the deeper result can replace it; restore the floor if the deep
        # fetch turns up nothing.
        floor_hint = material.get("material_hint")
        material.pop("material_hint", None)
        try:
            enriched = await enrich_topic_materials_online(
                [material], lang=lang, max_materials=1
            )
        except Exception as exc:
            logger.debug("[%s] deep search enrichment failed: %s", name, exc)
            enriched = None
        deep = enriched[0] if enriched else None
        if isinstance(deep, Mapping) and deep.get("material_hint"):
            for key in ("material_hint", "online_used", "online_query", "online_angle"):
                if key in deep:
                    material[key] = deep[key]
        elif floor_hint is not None:
            material["material_hint"] = floor_hint

    def _prune_used_topics(self, name: str, *, now: float | None = None) -> list[dict[str, Any]]:
        current_time = float(now if now is not None else time.time())
        current_day = _local_day(current_time)
        records = []
        today_records = []
        for record in self._used_topics.get(name, []):
            used_at = float(record.get("used_at") or 0.0)
            used_day = _local_day(used_at)
            if used_day == current_day or current_time - used_at < _USED_TOPIC_RECENT_SECONDS:
                records.append(record)
            if used_day == current_day:
                today_records.append(record)
        if records:
            self._used_topics[name] = records
        else:
            self._used_topics.pop(name, None)
        return today_records

    def _recent_used_topics(self, name: str, *, now: float | None = None) -> list[dict[str, Any]]:
        current_time = float(now if now is not None else time.time())
        return [
            record
            for record in self._used_topics.get(name, [])
            if current_time - float(record.get("used_at") or 0.0) < _USED_TOPIC_RECENT_SECONDS
        ]

    def _daily_quota_reached(self, name: str, *, now: float | None = None) -> bool:
        if self._daily_topic_limit <= 0:
            return True
        return len(self._prune_used_topics(name, now=now)) >= self._daily_topic_limit

    def _seconds_until_next_topic_trigger(self, name: str, *, now: float | None = None) -> float:
        if self._min_trigger_gap_seconds <= 0:
            return 0.0
        records = self._recent_used_topics(name, now=now)
        if not records:
            return 0.0
        latest_used_at = max(float(record.get("used_at") or 0.0) for record in records)
        current_time = float(now if now is not None else time.time())
        elapsed = max(0.0, current_time - latest_used_at)
        return max(0.0, self._min_trigger_gap_seconds - elapsed)

    def _topic_was_used_today(self, name: str, material: Mapping[str, Any]) -> bool:
        hook_id = str(material.get("hook_id") or "").strip()
        interest = _clean_text(material.get("interest"), limit=90)
        keywords = _material_keywords(material)
        bigram_units = _material_bigram_units(material)
        for record in self._prune_used_topics(name):
            if hook_id and record.get("hook_id") == hook_id:
                return True
            if interest and record.get("interest") == interest:
                return True
            # Primary: a shared LLM keyword means the same topic.
            record_keywords = set(record.get("keywords") or ())
            if keywords and record_keywords and (keywords & record_keywords):
                return True
            # Parallel ngram veto: runs even when keywords miss. The ngram view
            # is noisy, so it only vetoes on a strict AND — sim >= 0.6 AND >= 2
            # shared 2gram units — and single CJK chars are excluded upstream.
            record_bigrams = set(record.get("bigram_units") or ())
            if bigram_units and record_bigrams:
                shared = len(bigram_units & record_bigrams)
                if shared >= 2 and _topic_similarity(bigram_units, record_bigrams) >= 0.6:
                    logger.info(
                        "[%s] topic dedup veto by ngram fallback (keyword miss): shared=%d interest=%s",
                        name, shared, interest,
                    )
                    return True
        return False

    def _filter_available_materials(
        self,
        name: str,
        materials: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        available: list[dict[str, Any]] = []
        for material in materials:
            if self._daily_quota_reached(name):
                break
            if self._topic_was_used_today(name, material):
                logger.info("[%s] topic material suppressed as already used today: %s", name, _material_log_preview(material))
                continue
            available.append(dict(material))
        return available

    def _mark_topic_used(self, name: str, material: Mapping[str, Any]) -> None:
        self._prune_used_topics(name)
        self._used_topics[name].append(
            {
                "used_at": float(material.get("used_at") or time.time()),
                "hook_id": str(material.get("hook_id") or "").strip(),
                "interest": _clean_text(material.get("interest"), limit=90),
                "keywords": sorted(_material_keywords(material)),
                "bigram_units": sorted(_material_bigram_units(material)),
            }
        )


def _default_topic_trigger():
    from main_logic.topic.delivery import trigger_topic_hook_once

    return trigger_topic_hook_once


_GLOBAL_TOPIC_POOL = TopicHookPool(topic_trigger=_default_topic_trigger())


def get_topic_hook_pool() -> TopicHookPool:
    return _GLOBAL_TOPIC_POOL
