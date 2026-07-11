"""Background topic hook collection for proactive chat.

Ordinary chat must never wait for topic screening, online enrichment, or
prompt building. This module keeps the synchronous entrypoints tiny: record a
recent turn, optionally schedule a background worker, and return immediately.
The proactive endpoint reads only the prepared pool.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from main_logic.topic.common import ZH_TOPIC_STOP_CHARS, clean_text, topic_units
from main_logic.topic.materials import enrich_topic_materials_online
from main_logic.topic.signals import TopicSignalStore
from utils.file_utils import atomic_write_json
from utils.tokenize import truncate_to_tokens


logger = logging.getLogger("N.E.K.O.Main.topic.pipeline")

Analyzer = Callable[
    ...,
    Awaitable[Iterable[Mapping[str, Any]] | None],
]
TopicTrigger = Callable[
    ...,
    Awaitable[bool],
]
DeliveryAvailable = Callable[..., bool]

_MAX_TEXT_TOKENS = 1000
_TOKEN_PRECAP_CHARS_PER_TOKEN = 8
_TRIGGER_RETRY_DELAY_SECONDS = 60.0
_CANDIDATE_MATURE_SECONDS = 60.0
_MIN_TOPIC_TRIGGER_GAP_SECONDS = 4 * 60 * 60
_MAX_DAILY_TOPIC_TRIGGERS = 2
_USED_TOPIC_RECENT_SECONDS = 48 * 60 * 60
# Quota is weight-based, not count-based: a delivery the user never engages with
# costs only a fraction of the daily budget, so we keep trying until topics
# actually land. A delivered-but-unanswered hook starts at this weight and is
# upgraded to a full 1.0 once the user replies within the response window —
# roughly three ignored deliveries equal one full success.
_UNANSWERED_TOPIC_WEIGHT = 1.0 / 3.0
# A user turn arriving within this window after a delivery counts as a response.
_TOPIC_RESPONSE_WINDOW_SECONDS = 10 * 60
# Tolerance for the daily weight-sum quota comparison (weight units).
_QUOTA_WEIGHT_EPSILON = 1e-6


def _clean_text(value: Any, *, token_limit: int = _MAX_TEXT_TOKENS) -> str:
    precap = max(token_limit, token_limit * _TOKEN_PRECAP_CHARS_PER_TOKEN)
    return truncate_to_tokens(clean_text(value, limit=precap), token_limit)


def _clean_timestamp(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.time()


def _record_weight(record: Mapping[str, Any]) -> float:
    """Quota weight of a used-topic record.

    Records persisted before the weight field existed (or with a malformed
    value) count as a full 1.0 — they were full successes under the old
    count-based quota, so treating them as such is the conservative default.
    """
    try:
        weight = float(record.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if math.isnan(weight):
        return 1.0
    return max(0.0, min(1.0, weight))


def _local_day(value: float) -> date:
    return datetime.fromtimestamp(value).date()


def _clean_material(material: Mapping[str, Any]) -> dict[str, Any] | None:
    interest = _clean_text(material.get("interest"), token_limit=90)
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
        hint_summary = _clean_text(hint.get("summary"), token_limit=100)
    parts = [
        f"relevance={material.get('relevance')}",
        f"risk={material.get('risk')}",
        f"interest={_clean_text(material.get('interest'), token_limit=80)}",
    ]
    if material.get("online_used"):
        parts.append(f"online={_clean_text(material.get('online_angle'), token_limit=100)}")
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
        text = _clean_text(item, token_limit=30)
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


def _topic_fingerprint(value: Any) -> str:
    text = _clean_text(value, token_limit=120).lower()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _topic_fingerprints(values: Iterable[Any]) -> set[str]:
    return {fingerprint for value in values if (fingerprint := _topic_fingerprint(value))}


def _stored_topic_fingerprint(value: Any) -> str:
    text = str(value or "").strip().lower()
    if len(text) == 16 and all(char in "0123456789abcdef" for char in text):
        return text
    return _topic_fingerprint(text)


def _stored_topic_fingerprints(values: Iterable[Any]) -> set[str]:
    return {
        fingerprint
        for value in values
        if (fingerprint := _stored_topic_fingerprint(value))
    }


async def _default_analyzer(*, lang: str, global_signals: str = ""):
    from main_logic.activity.llm_enrichment import call_topic_candidates

    return await call_topic_candidates(lang=lang, global_signals=global_signals)


def _default_signal_store_path():
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        if not config_manager.ensure_local_state_directory():
            return None
        return config_manager.local_state_dir / "topic_signals.json"
    except Exception:
        return None


def _used_topics_path_for_signal_store(path: Any | None) -> Path | None:
    if not path:
        return None
    signal_path = Path(path)
    return signal_path.with_name(f"{signal_path.stem}.used_topics.json")


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
        debounce_seconds: float | None = None,
        candidate_quiet_seconds: float = 0.0,
        trigger_delay_seconds: float = 0.0,
        trigger_retry_delay_seconds: float | None = None,
        min_trigger_gap_seconds: float = _MIN_TOPIC_TRIGGER_GAP_SECONDS,
        min_user_turns_for_topic: int = 8,
        daily_topic_limit: int = _MAX_DAILY_TOPIC_TRIGGERS,
        signal_store_path: Any | None = None,
        delivery_available: DeliveryAvailable | None = None,
    ) -> None:
        self._analyzer = analyzer or _default_analyzer
        self._topic_trigger = topic_trigger
        self._delivery_available = delivery_available
        self._auto_schedule = auto_schedule
        self._enable_online_enrichment = enable_online_enrichment
        self._enable_deep_search = enable_deep_search
        # Legacy knobs are accepted so old callers/tests don't break. Candidate
        # and delivery timing are now owned by the activity heartbeat and the
        # real delivery gate, not by topic-private quiet sleeps.
        self._candidate_quiet_seconds = 0.0
        self._trigger_delay_seconds = 0.0
        _ = debounce_seconds, candidate_quiet_seconds, trigger_delay_seconds
        self._trigger_retry_delay_seconds = max(
            0.0,
            float(
                trigger_retry_delay_seconds
                if trigger_retry_delay_seconds is not None
                else _TRIGGER_RETRY_DELAY_SECONDS
            ),
        )
        self._min_trigger_gap_seconds = max(0.0, float(min_trigger_gap_seconds))
        self._daily_topic_limit = max(0, int(daily_topic_limit))
        self._signal_store = TopicSignalStore(
            min_user_turns_for_topic=min_user_turns_for_topic,
            persistence_path=signal_store_path or None,
        )
        self._used_topics_path = _used_topics_path_for_signal_store(signal_store_path)
        self._langs: dict[str, str] = {}
        self._materials: dict[str, list[dict[str, Any]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._trigger_tasks: dict[str, asyncio.Task] = {}
        # Strong references to fire-and-forget background tasks so they are
        # not garbage-collected before completion.
        self._bg_tasks: set[asyncio.Task] = set()
        self._used_topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._used_topics_lock = threading.RLock()
        self._used_topics_write_lock = threading.Lock()
        # Per-character "a topic was just delivered, awaiting a user reply"
        # marker: {used_at, deadline}. In-memory only — a delivery whose
        # response window straddles a restart simply stays at the unanswered
        # weight (conservative). See note_user_message / _arm_topic_response_window.
        self._awaiting_response: dict[str, dict[str, float]] = {}
        self._load_used_topics()
        # Restored persisted signals are dirty once after startup: they should
        # be eligible for the next heartbeat, then stop unless a new turn
        # arrives. Do not iterate all signal-store names every tick.
        self._dirty: set[str] = set(self._signal_store.names())
        self._seq: dict[str, int] = defaultdict(int)
        self._purge_generation: dict[str, int] = defaultdict(int)

    def _purge_character_state(self, name: str) -> None:
        """Drop all topic state for a character."""
        self._purge_generation[name] += 1
        self._signal_store.clear(name)
        self._materials.pop(name, None)
        self._dirty.discard(name)
        self._awaiting_response.pop(name, None)
        with self._used_topics_lock:
            self._used_topics.pop(name, None)
        self._persist_used_topics()
        self._cancel_trigger(name)

    def _purge_accumulated_signals(self, name: str, *, flush: bool = True) -> bool:
        """Drop accumulated candidate evidence without touching pending delivery material."""
        had_dirty = name in self._dirty
        changed = self._signal_store.clear(name)
        self._dirty.discard(name)
        if changed or had_dirty:
            self._purge_generation[name] += 1
        if changed and flush:
            self._signal_store.flush()
        return changed

    async def _purge_accumulated_signals_async(self, name: str) -> None:
        if self._purge_accumulated_signals(name, flush=False):
            await asyncio.to_thread(self._signal_store.flush)

    def purge_accumulated_signals(self, lanlan_name: str) -> None:
        """Public hook for explicit candidate-evidence cleanup."""
        self._purge_accumulated_signals(str(lanlan_name or "default"))

    async def purge_accumulated_signals_async(self, lanlan_name: str) -> None:
        """Async hook for explicit candidate-evidence cleanup."""
        await self._purge_accumulated_signals_async(str(lanlan_name or "default"))

    def purge_all_accumulated_signals(self) -> None:
        """Drop accumulated candidate evidence for every known character."""
        names = set(self._signal_store.names()) | set(self._dirty)
        for name in names:
            self._purge_accumulated_signals(name)

    async def purge_all_accumulated_signals_async(self) -> None:
        """Async hook for explicit cleanup across every known character."""
        names = set(self._signal_store.names()) | set(self._dirty)
        should_flush = False
        for name in names:
            should_flush = self._purge_accumulated_signals(name, flush=False) or should_flush
        if should_flush:
            await asyncio.to_thread(self._signal_store.flush)

    def _consume_accumulated_signals(self, name: str, *, flush: bool = True) -> None:
        """Consume analyzed evidence for this process, but keep it durable.

        A prepared hook is still only in memory until delivery. Keeping the
        signal window on disk lets a short restart re-arm analysis
        instead of losing the opportunity between candidate generation and
        delivery. Explicit purge remains the destructive path.
        """
        if flush:
            self._signal_store.flush()
        self._dirty.discard(name)

    async def _consume_accumulated_signals_async(self, name: str) -> None:
        self._consume_accumulated_signals(name, flush=False)
        await asyncio.to_thread(self._signal_store.flush)

    def _discard_delivered_signals(
        self,
        name: str,
        material: Mapping[str, Any] | None = None,
        *,
        flush: bool = True,
    ) -> None:
        """Drop durable evidence once the pending hook is done."""
        cutoff = None
        if material is not None:
            cutoff = material.get("_signal_cutoff_at")
        if self._signal_store.clear_until(name, timestamp=cutoff) and flush:
            self._signal_store.flush()

    async def _discard_delivered_signals_async(
        self,
        name: str,
        material: Mapping[str, Any] | None = None,
    ) -> None:
        cutoff = None
        if material is not None:
            cutoff = material.get("_signal_cutoff_at")
        if self._signal_store.clear_until(name, timestamp=cutoff):
            await asyncio.to_thread(self._signal_store.flush)

    def _discard_analyzed_signals(
        self,
        name: str,
        cutoff: float | None,
        *,
        flush: bool = True,
    ) -> None:
        """Drop durable evidence after analysis proves no hook is pending."""
        if self._signal_store.clear_until(name, timestamp=cutoff) and flush:
            self._signal_store.flush()
        self._dirty.discard(name)

    async def _discard_analyzed_signals_async(self, name: str, cutoff: float | None) -> None:
        changed = self._signal_store.clear_until(name, timestamp=cutoff)
        self._dirty.discard(name)
        if changed:
            await asyncio.to_thread(self._signal_store.flush)

    def note_user_message(self, lanlan_name: str, text: Any, *, lang: str = "zh") -> None:
        cleaned = _clean_text(text)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        # A real user turn after a delivery is the response signal: upgrade the
        # delivered hook from the unanswered weight to a full success. The AI's
        # own opener arrives via note_ai_message, so it can never self-upgrade.
        self._maybe_upgrade_topic_response(name)
        self._seq[name] += 1
        self._signal_store.note_turn(name, actor="user", text=cleaned)
        self._langs[name] = lang or self._langs.get(name, "zh")
        self._dirty.add(name)
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
        self._schedule(name)

    def note_turn_timestamp(
        self,
        lanlan_name: str,
        *,
        lang: str = "zh",
        now: float | None = None,
    ) -> None:
        """Refresh last-activity metadata without storing turn text."""
        name = str(lanlan_name or "default")
        del now
        self._langs[name] = lang or self._langs.get(name, "zh")

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
    ) -> None:
        name = str(lanlan_name or "default")
        seen_seq = self._seq.get(name, 0)
        seen_purge_generation = self._purge_generation.get(name, 0)
        if any(item.get("status") == "pending" for item in self._materials.get(name, [])):
            logger.debug("[%s] topic collection deferred: pending material is in delivery phase", name)
            return
        if not self._signal_store.is_ready(name):
            logger.info(
                "[%s] topic collection not ready: %s%%",
                name,
                self._signal_store.readiness_percent(name),
            )
            self._dirty.discard(name)
            return
        stored_lang = self._langs.get(name)
        topic_lang = (
            stored_lang
            if stored_lang and stored_lang != "zh"
            else (lang or stored_lang or "zh")
        )
        signal_cutoff_at = self._signal_store.last_turn_at(name)
        global_signals = self._signal_store.format_global_signals(name, lang=topic_lang)
        raw_materials = await self._analyzer(
            lang=topic_lang,
            global_signals=global_signals,
        )
        if raw_materials is None:
            logger.info("[%s] topic analyzer returned no result; keeping dirty for retry", name)
            return
        if (
            self._seq.get(name, 0) != seen_seq
            or self._purge_generation.get(name, 0) != seen_purge_generation
        ):
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
        cleaned = self._filter_available_materials(name, cleaned)
        for material in cleaned:
            material["_signal_cutoff_at"] = signal_cutoff_at
        self._materials[name] = cleaned
        if cleaned:
            for idx, material in enumerate(cleaned, start=1):
                logger.info(
                    "[%s] topic material ready #%d: %s",
                    name,
                    idx,
                    _material_log_preview(material),
            )
            self._schedule_trigger(name, cleaned[0], topic_lang)
            await self._consume_accumulated_signals_async(name)
        else:
            logger.info("[%s] topic material ready: none", name)
            await self._discard_analyzed_signals_async(name, signal_cutoff_at)

    def _schedule(self, name: str) -> None:
        # Candidate analysis is driven by the activity heartbeat via
        # process_ready_topics(). Keep this method as a compatibility no-op so
        # note_turn stays tiny and no topic-private sleep loop is created.
        return

    async def _run_later(self, name: str) -> None:
        # Deprecated: the private debounce loop was replaced by the activity
        # heartbeat. Kept for compatibility with older tests/imports.
        if name in self._dirty:
            await self.process_now(name)

    async def process_ready_topics(
        self,
        *,
        lanlan_name: str | None = None,
        lang: str | None = None,
        now: float | None = None,
    ) -> None:
        if lanlan_name is not None:
            requested_name = str(lanlan_name or "default")
            names = {requested_name} if requested_name in self._dirty else set()
        else:
            names = set(self._dirty)
        for name in sorted(names):
            last_turn_at = self._signal_store.last_turn_at(name)
            if last_turn_at is None:
                self._dirty.discard(name)
                continue
            current_time = time.time() if now is None else float(now)
            if current_time - float(last_turn_at) < _CANDIDATE_MATURE_SECONDS:
                continue
            try:
                await self.process_now(name, lang=lang)
            except Exception as exc:
                logger.warning("[%s] topic background processing failed: %s", name, exc)

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
    ) -> None:
        if self._topic_trigger is None:
            return
        self._cancel_trigger(name)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._trigger_tasks[name] = loop.create_task(
            self._run_trigger_when_available(
                name,
                deepcopy(dict(material)),
                lang,
            ),
            name=f"topic_trigger_{name}",
        )

    async def _run_trigger_after_retry_delay(
        self,
        name: str,
        material: dict[str, Any],
        lang: str,
    ) -> None:
        delay = self._trigger_retry_delay_seconds
        if delay:
            await asyncio.sleep(delay)
        await self._run_trigger_when_available(name, material, lang)

    async def _run_trigger_after_delay(
        self,
        name: str,
        material: dict[str, Any],
        lang: str,
        delay: float,
    ) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await self._run_trigger_when_available(name, material, lang)

    def _reschedule_trigger_after_delay(
        self,
        name: str,
        material: Mapping[str, Any],
        lang: str,
        delay: float,
    ) -> None:
        if material.get("status") != "pending":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._trigger_tasks[name] = loop.create_task(
            self._run_trigger_after_delay(
                name,
                deepcopy(dict(material)),
                lang,
                max(0.0, float(delay)),
            ),
            name=f"topic_trigger_{name}",
        )

    def _reschedule_trigger_retry(
        self,
        name: str,
        material: Mapping[str, Any],
        lang: str,
    ) -> None:
        if material.get("status") != "pending":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._trigger_tasks[name] = loop.create_task(
            self._run_trigger_after_retry_delay(
                name,
                deepcopy(dict(material)),
                lang,
            ),
            name=f"topic_trigger_{name}",
        )

    async def _run_trigger_when_available(
        self,
        name: str,
        material: dict[str, Any],
        lang: str,
    ) -> None:
        current_material: dict[str, Any] | None = None
        try:
            current = self._materials.get(name) or []
            if not current:
                return
            hook_id = material.get("hook_id")
            current_material = current[0]
            if hook_id and current_material.get("hook_id") != hook_id:
                return
            if current_material.get("status") != "pending":
                return
            if self._daily_quota_reached(name):
                logger.debug("[%s] topic material trigger waiting: daily quota reached", name)
                self._reschedule_trigger_retry(name, current_material, lang)
                return
            if self._topic_was_recently_used(name, current_material):
                current_material["status"] = "skipped"
                self._materials[name] = []
                await self._discard_delivered_signals_async(name, current_material)
                logger.info("[%s] topic material trigger skipped: already used", name)
                return
            trigger_gap_seconds = self._seconds_until_next_topic_trigger(name)
            if trigger_gap_seconds > 0:
                self._reschedule_trigger_after_delay(
                    name,
                    current_material,
                    lang,
                    trigger_gap_seconds,
                )
                return
            # "Search first, then chat": once the delivery bridge looks open,
            # prepare a deeper online lead off the user hot path. A later gate
            # close just keeps the prepared material pending for the next retry.
            if not self._delivery_available_now(name):
                logger.debug("[%s] topic material trigger waiting: delivery gate closed", name)
                self._reschedule_trigger_retry(name, current_material, lang)
                return
            await self._deepen_material(name, current_material, lang)
            if not self._delivery_available_now(name):
                logger.debug("[%s] topic material trigger waiting: delivery gate closed after prepare", name)
                self._reschedule_trigger_retry(name, current_material, lang)
                return
            delivery_material = deepcopy(current_material)
            delivery_material["_topic_release_available"] = (
                lambda _name=name: self._delivery_available_now(
                    _name,
                    include_manager_release=False,
                )
            )
            triggered = await self._topic_trigger(
                lanlan_name=name,
                material=delivery_material,
                lang=lang,
            )
            if not triggered:
                logger.info("[%s] topic material trigger skipped by delivery bridge", name)
                self._reschedule_trigger_retry(
                    name,
                    current_material,
                    lang,
                )
                return
            current_material["status"] = "used"
            current_material["used_at"] = time.time()
            self._mark_topic_used(name, current_material, persist=False)
            self._arm_topic_response_window(name, current_material)
            await asyncio.to_thread(self._persist_used_topics)
            self._materials[name] = []
            await self._discard_delivered_signals_async(name, current_material)
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
        if not self._enable_online_enrichment:
            return
        if material.get("deep_search_done"):
            return
        material["deep_search_done"] = True
        if self._enable_deep_search:
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
                query = ""
            if query:
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

    def _delivery_available_now(
        self,
        name: str,
        *,
        include_manager_release: bool = True,
    ) -> bool:
        if self._delivery_available is None:
            return True
        try:
            return bool(
                self._delivery_available(
                    name,
                    include_manager_release=include_manager_release,
                )
            )
        except TypeError:
            try:
                return bool(self._delivery_available(name))
            except Exception as exc:
                logger.debug("[%s] topic delivery availability check failed: %s", name, exc)
                return False
        except Exception as exc:
            logger.debug("[%s] topic delivery availability check failed: %s", name, exc)
            return False

    def _prune_used_topics(self, name: str, *, now: float | None = None) -> list[dict[str, Any]]:
        current_time = float(now if now is not None else time.time())
        current_day = _local_day(current_time)
        with self._used_topics_lock:
            previous_len = len(self._used_topics.get(name, []))
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
            changed = len(records) != previous_len
        if changed:
            self._request_used_topics_persist()
        return today_records

    def _recent_used_topics(self, name: str, *, now: float | None = None) -> list[dict[str, Any]]:
        current_time = float(now if now is not None else time.time())
        with self._used_topics_lock:
            return [
                record
                for record in self._used_topics.get(name, [])
                if current_time - float(record.get("used_at") or 0.0) < _USED_TOPIC_RECENT_SECONDS
            ]

    def _daily_quota_reached(self, name: str, *, now: float | None = None) -> bool:
        if self._daily_topic_limit <= 0:
            return True
        today = self._prune_used_topics(name, now=now)
        total_weight = sum(_record_weight(record) for record in today)
        return total_weight >= self._daily_topic_limit - _QUOTA_WEIGHT_EPSILON

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

    def _topic_was_recently_used(self, name: str, material: Mapping[str, Any]) -> bool:
        hook_id = str(material.get("hook_id") or "").strip()
        interest = _clean_text(material.get("interest"), token_limit=90)
        keywords = _material_keywords(material)
        bigram_units = _material_bigram_units(material)
        hook_id_hash = _topic_fingerprint(hook_id)
        interest_hash = _topic_fingerprint(interest)
        keyword_hashes = _topic_fingerprints(keywords)
        bigram_hashes = _topic_fingerprints(bigram_units)
        self._prune_used_topics(name)
        for record in self._recent_used_topics(name):
            if hook_id and record.get("hook_id") == hook_id:
                return True
            if hook_id_hash and record.get("hook_id_hash") == hook_id_hash:
                return True
            if interest and record.get("interest") == interest:
                return True
            if interest_hash and record.get("interest_hash") == interest_hash:
                return True
            # Primary: a shared LLM keyword means the same topic.
            record_keywords = set(record.get("keywords") or ())
            if keywords and record_keywords and (keywords & record_keywords):
                return True
            record_keyword_hashes = set(record.get("keyword_hashes") or ())
            if keyword_hashes and record_keyword_hashes and (keyword_hashes & record_keyword_hashes):
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
            record_bigram_hashes = set(record.get("bigram_hashes") or ())
            if bigram_hashes and record_bigram_hashes:
                shared = len(bigram_hashes & record_bigram_hashes)
                if shared >= 2 and _topic_similarity(bigram_hashes, record_bigram_hashes) >= 0.6:
                    logger.info(
                        "[%s] topic dedup veto by persisted ngram fingerprint: shared=%d interest=%s",
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
            if self._topic_was_recently_used(name, material):
                logger.info("[%s] topic material suppressed as already used recently: %s", name, _material_log_preview(material))
                continue
            available.append(dict(material))
        return available

    def _arm_topic_response_window(self, name: str, material: Mapping[str, Any]) -> None:
        """Mark a just-delivered topic as awaiting a user reply.

        The record is persisted at the unanswered weight by ``_mark_topic_used``;
        a user turn inside the window upgrades it to full via
        ``_maybe_upgrade_topic_response``. We hold a direct reference to the
        just-appended record rather than keying on ``used_at`` — coarse clock
        resolution (e.g. ~15 ms on Windows) can give two close deliveries the
        same timestamp, and a value match would then upgrade the wrong record.
        """
        used_at = float(material.get("used_at") or time.time())
        with self._used_topics_lock:
            records = self._used_topics.get(name) or []
            record = records[-1] if records else None
        self._awaiting_response[name] = {
            "deadline": used_at + _TOPIC_RESPONSE_WINDOW_SECONDS,
            "record": record,
        }

    def _maybe_upgrade_topic_response(self, name: str, *, now: float | None = None) -> None:
        pending = self._awaiting_response.get(name)
        if not pending:
            return
        current = float(now if now is not None else time.time())
        # One-shot: clear the marker whether or not it is still in-window, so a
        # later turn can't upgrade a stale delivery.
        self._awaiting_response.pop(name, None)
        if current > float(pending.get("deadline") or 0.0):
            return
        record = pending.get("record")
        if record is None:
            return
        with self._used_topics_lock:
            # Guard against the record having been pruned out of the live list.
            if record not in self._used_topics.get(name, ()):
                return
            record["weight"] = 1.0
        logger.info("[%s] topic response within window: quota weight upgraded to full", name)
        self._request_used_topics_persist()

    def _mark_topic_used(
        self,
        name: str,
        material: Mapping[str, Any],
        *,
        persist: bool = True,
    ) -> None:
        self._prune_used_topics(name)
        with self._used_topics_lock:
            self._used_topics[name].append(
                {
                    "used_at": float(material.get("used_at") or time.time()),
                    "weight": _UNANSWERED_TOPIC_WEIGHT,
                    "hook_id": str(material.get("hook_id") or "").strip(),
                    "hook_id_hash": _topic_fingerprint(material.get("hook_id")),
                    "interest": _clean_text(material.get("interest"), token_limit=90),
                    "interest_hash": _topic_fingerprint(material.get("interest")),
                    "keywords": sorted(_material_keywords(material)),
                    "keyword_hashes": sorted(_topic_fingerprints(_material_keywords(material))),
                    "bigram_units": sorted(_material_bigram_units(material)),
                    "bigram_hashes": sorted(_topic_fingerprints(_material_bigram_units(material))),
                }
            )
        if persist:
            self._request_used_topics_persist()

    def _request_used_topics_persist(self) -> None:
        if self._used_topics_path is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._persist_used_topics()
            return
        task = loop.create_task(asyncio.to_thread(self._persist_used_topics))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _load_used_topics(self) -> None:
        path = self._used_topics_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        characters = payload.get("characters") if isinstance(payload, dict) else None
        if not isinstance(characters, dict):
            return
        for name, entries in characters.items():
            if not isinstance(entries, list):
                continue
            safe_name = str(name or "default")
            loaded: list[dict[str, Any]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                try:
                    used_at = float(entry.get("used_at"))
                except (TypeError, ValueError):
                    continue
                loaded.append(
                    {
                        "used_at": used_at,
                        "weight": _record_weight(entry),
                        "hook_id_hash": _stored_topic_fingerprint(
                            entry.get("hook_id_hash") or entry.get("hook_id")
                        ),
                        "interest_hash": _stored_topic_fingerprint(
                            entry.get("interest_hash") or entry.get("interest")
                        ),
                        "keyword_hashes": sorted(
                            _stored_topic_fingerprints(
                                entry.get("keyword_hashes") or entry.get("keywords") or []
                            )
                        ),
                        "bigram_hashes": sorted(
                            _stored_topic_fingerprints(
                                entry.get("bigram_hashes") or entry.get("bigram_units") or []
                            )
                        ),
                    }
                )
            if loaded:
                with self._used_topics_lock:
                    self._used_topics[safe_name].extend(loaded)
        with self._used_topics_lock:
            names = list(self._used_topics)
        for name in names:
            self._prune_used_topics(name)

    def _persist_used_topics(self) -> None:
        path = self._used_topics_path
        if path is None:
            return
        with self._used_topics_write_lock:
            with self._used_topics_lock:
                used_topics = {
                    name: [dict(record) for record in records]
                    for name, records in self._used_topics.items()
                    if records
                }
            payload = {
                "version": 1,
                "characters": {
                    name: [
                        {
                            "used_at": float(record.get("used_at") or 0.0),
                            "weight": _record_weight(record),
                            "hook_id_hash": _stored_topic_fingerprint(
                                record.get("hook_id_hash") or record.get("hook_id")
                            ),
                            "interest_hash": _stored_topic_fingerprint(
                                record.get("interest_hash") or record.get("interest")
                            ),
                            "keyword_hashes": sorted(
                                _stored_topic_fingerprints(
                                    record.get("keyword_hashes") or record.get("keywords") or []
                                )
                            ),
                            "bigram_hashes": sorted(
                                _stored_topic_fingerprints(
                                    record.get("bigram_hashes") or record.get("bigram_units") or []
                                )
                            ),
                        }
                        for record in records
                        if float(record.get("used_at") or 0.0) > 0
                    ]
                    for name, records in used_topics.items()
                    if records
                },
            }
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, payload, ensure_ascii=False, indent=2)
            except Exception:
                logger.debug("topic used-history persistence failed", exc_info=True)


def _default_topic_trigger():
    from main_logic.topic.delivery import trigger_topic_hook_once

    return trigger_topic_hook_once


def _default_delivery_available():
    from main_logic.topic.delivery import topic_hook_delivery_available

    return topic_hook_delivery_available


_GLOBAL_TOPIC_POOL = TopicHookPool(
    topic_trigger=_default_topic_trigger(),
    signal_store_path=_default_signal_store_path(),
    delivery_available=_default_delivery_available(),
)


def get_topic_hook_pool() -> TopicHookPool:
    return _GLOBAL_TOPIC_POOL
