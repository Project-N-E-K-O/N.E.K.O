"""Controlled registration and utterance-scoped voice-input routing."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from main_logic.voice_turn.contracts import (
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)

from .contracts import (
    BuiltinVoiceInputConsumer,
    VoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputConsumerHandle,
    VoiceInputConsumerIdentity,
    VoiceInputDispatchResult,
    VoiceInputRegistration,
)

if TYPE_CHECKING:
    from .plugin_api import PluginVoiceInputRegistrar


_PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESERVED_PLUGIN_IDS = {consumer.value for consumer in BuiltinVoiceInputConsumer}


class VoiceInputHandleError(RuntimeError):
    """Raised when an activation handle was not issued by this registry."""


@dataclass(frozen=True, slots=True)
class _ConsumerRecord:
    handle: VoiceInputConsumerHandle
    consumer: VoiceInputConsumer
    capabilities: VoiceInputConsumerCapabilities


@dataclass(slots=True)
class _PinnedUtterance:
    record: _ConsumerRecord
    token: VoiceTurnToken
    activation_generation: int
    prepare_callbacks_idle: asyncio.Event = field(
        default_factory=asyncio.Event,
    )
    pending_prepare_callbacks: int = 0


class VoiceInputRegistry:
    """Route each identified ASR utterance to one pinned live consumer."""

    def __init__(self) -> None:
        self._registry_token = object()
        self._records: dict[VoiceInputConsumerIdentity, _ConsumerRecord] = {}
        self._active: _ConsumerRecord | None = None
        self._activation_generation = 0
        self._utterances: dict[VoiceTurnToken, _PinnedUtterance] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._deferred_cancellations: list[
            tuple[_PinnedUtterance, str]
        ] = []
        self._closed = False

    @property
    def active_identity(self) -> VoiceInputConsumerIdentity | None:
        active = self._active
        return active.handle.identity if active is not None else None

    @property
    def active_accepts_input(self) -> bool:
        active = self._active
        return bool(
            not self._closed
            and active is not None
            and active.capabilities.accepts_final
            and self._record_is_available(active)
        )

    def register_builtin(
        self,
        consumer_id: BuiltinVoiceInputConsumer,
        consumer: VoiceInputConsumer,
        *,
        capabilities: VoiceInputConsumerCapabilities | None = None,
    ) -> VoiceInputRegistration:
        if not isinstance(consumer_id, BuiltinVoiceInputConsumer):
            raise TypeError("VOICE_INPUT_BUILTIN_ID_REQUIRED")
        return self._register(
            VoiceInputConsumerIdentity("builtin", consumer_id.value),
            consumer,
            capabilities or VoiceInputConsumerCapabilities(),
        )

    def issue_plugin_registrar(self, plugin_id: str) -> PluginVoiceInputRegistrar:
        """Issue one namespace-bound registrar for a host-validated plugin."""

        normalized = str(plugin_id or "").strip().lower()
        if (
            not _PLUGIN_ID_PATTERN.fullmatch(normalized)
            or normalized in _RESERVED_PLUGIN_IDS
        ):
            raise ValueError("VOICE_INPUT_PLUGIN_ID_INVALID")
        from .registrar import VoiceInputRegistrar

        return VoiceInputRegistrar(
            self,
            VoiceInputConsumerIdentity("plugin", normalized),
        )

    def activate(self, handle: VoiceInputConsumerHandle) -> None:
        record = self._resolve_handle(handle)
        if self._active is record:
            return
        self.invalidate_utterance(reason="consumer_switched")
        self._activation_generation += 1
        self._active = record

    def begin_utterance(self, token: VoiceTurnToken) -> bool:
        if (
            self._closed
            or not isinstance(token, VoiceTurnToken)
            or token in self._utterances
            or not self.active_accepts_input
        ):
            return False
        active = self._active
        if active is None:
            return False
        route = _PinnedUtterance(
            record=active,
            token=token,
            activation_generation=self._activation_generation,
        )
        route.prepare_callbacks_idle.set()
        self._utterances[token] = route
        return True

    async def prepare_utterance(self, token: VoiceTurnToken) -> bool:
        route = self._live_utterance(token)
        if route is None or not self._route_is_available(route):
            return False
        route.pending_prepare_callbacks += 1
        route.prepare_callbacks_idle.clear()
        try:
            try:
                accepted = bool(
                    await route.record.consumer.prepare_turn(token)
                )
            except asyncio.CancelledError:
                if self._utterances.get(token) is route:
                    self._invalidate_route(token, "prepare_cancelled")
                raise
            except Exception:
                accepted = False
        finally:
            route.pending_prepare_callbacks -= 1
            if route.pending_prepare_callbacks == 0:
                route.prepare_callbacks_idle.set()
        if (
            not accepted
            or self._live_utterance(token) is not route
            or not self._route_is_available(route)
        ):
            if self._utterances.get(token) is route:
                consumed = self._consume_route(token)
                if consumed is not None:
                    # A lifecycle may retry the same token immediately after a
                    # transient prepare rejection. Finish this attempt's
                    # terminal callback before returning, otherwise its
                    # delayed cancellation could erase the retry's context.
                    await self._notify_cancelled(
                        consumed,
                        "prepare_rejected",
                    )
            return False
        return True

    async def dispatch_partial(
        self,
        event: VoicePartialEvent,
    ) -> VoiceInputDispatchResult:
        if not isinstance(event, VoicePartialEvent):
            return VoiceInputDispatchResult.REJECTED
        route = self._live_utterance(event.turn_token)
        if route is None or not route.record.capabilities.accepts_partial:
            return VoiceInputDispatchResult.REJECTED
        if not self._route_is_available(route):
            self._invalidate_route(event.turn_token, "consumer_unavailable")
            return VoiceInputDispatchResult.REJECTED
        try:
            await route.record.consumer.on_partial(event)
        except Exception:
            return VoiceInputDispatchResult.CALLBACK_FAILED
        return VoiceInputDispatchResult.DELIVERED

    async def dispatch_final(
        self,
        event: VoiceTranscriptEvent,
    ) -> VoiceInputDispatchResult:
        if not isinstance(event, VoiceTranscriptEvent):
            return VoiceInputDispatchResult.REJECTED
        route = self._live_utterance(event.turn_token)
        if route is None or not route.record.capabilities.accepts_final:
            return VoiceInputDispatchResult.REJECTED
        if not self._route_is_available(route):
            self._invalidate_route(event.turn_token, "consumer_unavailable")
            return VoiceInputDispatchResult.REJECTED

        # Consume before invoking external code. A duplicate final or callback
        # failure can never restore this route or reach the next consumer.
        self._consume_route(route.token)
        if not event.text.strip():
            self._schedule_cancel(route, "empty_final")
            return VoiceInputDispatchResult.EMPTY_CONSUMED
        try:
            await route.record.consumer.on_final(event)
        except Exception:
            return VoiceInputDispatchResult.CALLBACK_FAILED
        return VoiceInputDispatchResult.DELIVERED

    def invalidate_utterance(
        self,
        token: VoiceTurnToken | None = None,
        *,
        reason: str,
    ) -> bool:
        if token is not None:
            return self._invalidate_route(token, reason)
        tokens = tuple(self._utterances)
        for route_token in tokens:
            self._invalidate_route(route_token, reason)
        return bool(tokens)

    async def wait_idle(self) -> None:
        while True:
            deferred, self._deferred_cancellations = (
                self._deferred_cancellations,
                [],
            )
            for route, reason in deferred:
                self._schedule_cancel(route, reason)
            tasks = tuple(self._background_tasks)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        if not self._closed:
            self.invalidate_utterance(reason="registry_closed")
            self._activation_generation += 1
            self._active = None
            self._records.clear()
            self._closed = True
        await self.wait_idle()

    def _register_plugin(
        self,
        identity: VoiceInputConsumerIdentity,
        consumer: VoiceInputConsumer,
        capabilities: VoiceInputConsumerCapabilities,
    ) -> VoiceInputRegistration:
        if identity.namespace != "plugin":
            raise ValueError("VOICE_INPUT_PLUGIN_NAMESPACE_REQUIRED")
        return self._register(identity, consumer, capabilities)

    def _register(
        self,
        identity: VoiceInputConsumerIdentity,
        consumer: VoiceInputConsumer,
        capabilities: VoiceInputConsumerCapabilities,
    ) -> VoiceInputRegistration:
        if self._closed:
            raise RuntimeError("VOICE_INPUT_REGISTRY_CLOSED")
        if identity in self._records:
            raise RuntimeError("VOICE_INPUT_CONSUMER_ALREADY_REGISTERED")
        required = (
            "is_available",
            "prepare_turn",
            "on_partial",
            "on_final",
            "on_cancelled",
        )
        if any(not callable(getattr(consumer, name, None)) for name in required):
            raise TypeError("VOICE_INPUT_CONSUMER_INVALID")
        if not isinstance(capabilities, VoiceInputConsumerCapabilities):
            raise TypeError("VOICE_INPUT_CAPABILITIES_REQUIRED")
        handle = VoiceInputConsumerHandle(
            identity=identity,
            _registry_token=self._registry_token,
            _registration_token=object(),
        )
        record = _ConsumerRecord(handle, consumer, capabilities)
        self._records[identity] = record
        return VoiceInputRegistration(
            handle,
            lambda: self._close_registration(handle),
        )

    def _close_registration(self, handle: VoiceInputConsumerHandle) -> bool:
        try:
            record = self._resolve_handle(handle)
        except VoiceInputHandleError:
            return False
        for token, route in tuple(self._utterances.items()):
            if route.record is record:
                self._invalidate_route(token, "consumer_unregistered")
        if self._active is record:
            self._activation_generation += 1
            self._active = None
        del self._records[record.handle.identity]
        return True

    def _resolve_handle(
        self,
        handle: VoiceInputConsumerHandle,
    ) -> _ConsumerRecord:
        if (
            not isinstance(handle, VoiceInputConsumerHandle)
            or handle._registry_token is not self._registry_token
        ):
            raise VoiceInputHandleError("VOICE_INPUT_HANDLE_FOREIGN")
        record = self._records.get(handle.identity)
        if (
            record is None
            or record.handle._registration_token
            is not handle._registration_token
        ):
            raise VoiceInputHandleError("VOICE_INPUT_HANDLE_STALE")
        return record

    def _live_utterance(
        self,
        token: VoiceTurnToken,
    ) -> _PinnedUtterance | None:
        route = self._utterances.get(token)
        if route is None:
            return None
        record = self._records.get(route.record.handle.identity)
        if (
            record is not route.record
            or self._active is not route.record
            or route.activation_generation != self._activation_generation
        ):
            return None
        return route

    @staticmethod
    def _record_is_available(record: _ConsumerRecord) -> bool:
        try:
            return bool(record.consumer.is_available())
        except Exception:
            return False

    def _route_is_available(self, route: _PinnedUtterance) -> bool:
        return self._record_is_available(route.record)

    def _consume_route(
        self,
        token: VoiceTurnToken,
    ) -> _PinnedUtterance | None:
        return self._utterances.pop(token, None)

    def _invalidate_route(self, token: VoiceTurnToken, reason: str) -> bool:
        route = self._consume_route(token)
        if route is None:
            return False
        self._schedule_cancel(route, str(reason or "cancelled"))
        return True

    def _schedule_cancel(
        self,
        route: _PinnedUtterance,
        reason: str,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._deferred_cancellations.append((route, reason))
            return
        task = loop.create_task(
            self._notify_cancelled(route, reason),
            name="voice-input-consumer-cancel",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    async def _notify_cancelled(
        route: _PinnedUtterance,
        reason: str,
    ) -> None:
        try:
            # A consumer may materialize its turn context only after an
            # awaited prepare callback resumes. Terminal cancellation must
            # therefore run after every prepare already in flight for this
            # pinned route, or the late prepare could recreate state that the
            # earlier cancellation had just cleared.
            await route.prepare_callbacks_idle.wait()
            await route.record.consumer.on_cancelled(route.token, reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Cancellation is terminal and advisory; failure cannot reopen or
            # redirect a consumed route.
            return
