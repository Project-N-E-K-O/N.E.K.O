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
    raw_text: str | None
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
    scheduling, and async work. Privacy redaction still applies to the
    redacted ``text`` field; sinks that are explicitly allowed to ignore that
    gate can consume ``raw_text``.
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
            raw_text=text,
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
        # Compatibility only: older callers passed the activity privacy checker
        # here. Deep-topic evidence now consumes raw conversation turns and does
        # not consult activity/private state.
        del activity_private_check
        self._pool_factory = pool_factory

    def _pool(self):
        if self._pool_factory is not None:
            return self._pool_factory()
        from main_logic.topic.pipeline import get_topic_hook_pool
        return get_topic_hook_pool()

    def note_turn(self, event: ConversationTurnEvent) -> None:
        pool = self._pool()
        text = event.raw_text
        if not text:
            return
        if event.actor == "user":
            pool.note_user_message(event.lanlan_name, text, lang=event.lang)
        else:
            pool.note_ai_message(event.lanlan_name, text, lang=event.lang)


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
