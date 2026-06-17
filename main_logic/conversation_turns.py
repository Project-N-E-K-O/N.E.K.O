"""Fan out user/AI conversation turns to independent runtime consumers."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol


logger = logging.getLogger("N.E.K.O.Main.conversation_turns")

TurnActor = Literal["user", "ai"]


def _privacy_mode_active() -> bool:
    try:
        from utils.preferences import is_privacy_mode_enabled
        return is_privacy_mode_enabled()
    except Exception:
        return True


def normalize_turn_language(language: str | None = None) -> str:
    try:
        from utils.language_utils import get_global_language, normalize_language_code
        source = language if language else get_global_language()
        return normalize_language_code(source, format='full') or 'en'
    except Exception:
        return 'en'


@dataclass(frozen=True)
class ConversationTurnEvent:
    lanlan_name: str
    actor: TurnActor
    text: str | None
    lang: str
    timestamp: float
    text_allowed: bool
    had_text: bool = False


class ConversationTurnSink(Protocol):
    def note_turn(self, event: ConversationTurnEvent) -> None:
        """Consume one conversation turn without blocking the chat path."""


class ConversationTurnDispatcher:
    """Small synchronous fanout for per-turn side consumers.

    The chat path owns turn timing; individual consumers own their storage,
    scheduling, and async work. Privacy redaction happens before fanout so raw
    text does not reach topic/memory-like consumers when privacy mode is on.
    """

    def __init__(
        self,
        lanlan_name: str,
        *,
        language: str | None = None,
        privacy_check: Callable[[], bool] = _privacy_mode_active,
    ) -> None:
        self.lanlan_name = lanlan_name
        self._language = normalize_turn_language(language)
        self._privacy_check = privacy_check
        self._sinks: list[ConversationTurnSink] = []

    def set_language(self, language: str | None) -> None:
        self._language = normalize_turn_language(language)

    def current_language(self) -> str:
        return self._language

    def add_sink(self, sink: ConversationTurnSink) -> None:
        self._sinks.append(sink)

    def note_user_message(self, *, text: str | None = None, now: float | None = None) -> None:
        self._emit("user", text=text, now=now)

    def note_ai_message(self, *, text: str | None = None, now: float | None = None) -> None:
        self._emit("ai", text=text, now=now)

    def _emit(self, actor: TurnActor, *, text: str | None, now: float | None) -> None:
        ts = now if now is not None else time.time()
        privacy_on = True
        if text:
            try:
                privacy_on = self._privacy_check()
            except Exception:
                logger.debug(
                    "[%s] privacy check failed; fallback to redacted turn",
                    self.lanlan_name,
                    exc_info=True,
                )
        text_allowed = bool(text) and not privacy_on
        event = ConversationTurnEvent(
            lanlan_name=self.lanlan_name,
            actor=actor,
            text=text if text_allowed else None,
            lang=self._language,
            timestamp=ts,
            text_allowed=text_allowed,
            had_text=bool(text),
        )
        for sink in list(self._sinks):
            try:
                sink.note_turn(event)
            except Exception:
                logger.debug(
                    "[%s] conversation turn sink failed: %s",
                    self.lanlan_name,
                    type(sink).__name__,
                    exc_info=True,
                )


class ActivityTrackerTurnSink:
    def __init__(self, tracker) -> None:
        self._tracker = tracker

    def note_turn(self, event: ConversationTurnEvent) -> None:
        if event.actor == "user":
            self._tracker.on_user_message(text=event.text, now=event.timestamp)
        else:
            self._tracker.on_ai_message(text=event.text, now=event.timestamp)


class TopicHookTurnSink:
    def __init__(
        self,
        pool_factory: Callable[[], object] | None = None,
        *,
        activity_private_check: Callable[[], bool] | None = None,
    ) -> None:
        self._pool_factory = pool_factory
        self._activity_private_check = activity_private_check

    def _pool(self):
        if self._pool_factory is not None:
            return self._pool_factory()
        from main_logic.topic.pipeline import get_topic_hook_pool
        return get_topic_hook_pool()

    def _activity_private(self) -> bool:
        if self._activity_private_check is None:
            return False
        try:
            return bool(self._activity_private_check())
        except Exception:
            return True

    @staticmethod
    def _purge_and_mark_turn(
        pool,
        event: ConversationTurnEvent,
        *,
        all_characters: bool,
    ) -> None:
        if all_characters:
            purge_all_accumulated_signals = getattr(
                pool,
                "purge_all_accumulated_signals",
                None,
            )
            if purge_all_accumulated_signals is not None:
                purge_all_accumulated_signals()
            else:
                purge_accumulated_signals = getattr(
                    pool,
                    "purge_accumulated_signals",
                    None,
                )
                if purge_accumulated_signals is not None:
                    purge_accumulated_signals(event.lanlan_name)
        else:
            purge_accumulated_signals = getattr(
                pool,
                "purge_accumulated_signals",
                None,
            )
            if purge_accumulated_signals is not None:
                purge_accumulated_signals(event.lanlan_name)
        note_turn_timestamp = getattr(pool, "note_turn_timestamp", None)
        if note_turn_timestamp is not None:
            note_turn_timestamp(
                event.lanlan_name,
                lang=event.lang,
                now=event.timestamp,
            )

    def note_turn(self, event: ConversationTurnEvent) -> None:
        pool = self._pool()
        if event.text_allowed and event.had_text and self._activity_private():
            self._purge_and_mark_turn(pool, event, all_characters=False)
            return
        if not event.text_allowed or not event.text:
            if event.had_text:
                self._purge_and_mark_turn(pool, event, all_characters=True)
            return
        if event.actor == "user":
            pool.note_user_message(event.lanlan_name, event.text, lang=event.lang)
        else:
            pool.note_ai_message(event.lanlan_name, event.text, lang=event.lang)


def create_default_turn_dispatcher(
    lanlan_name: str,
    activity_tracker,
    *,
    language: str | None = None,
) -> ConversationTurnDispatcher:
    dispatcher = ConversationTurnDispatcher(lanlan_name, language=language)
    dispatcher.add_sink(ActivityTrackerTurnSink(activity_tracker))
    dispatcher.add_sink(
        TopicHookTurnSink(
            activity_private_check=getattr(
                activity_tracker,
                "is_private_activity_active",
                None,
            )
        )
    )
    return dispatcher
