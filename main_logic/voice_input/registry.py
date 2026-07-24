"""Controlled registration and utterance-scoped voice-input routing."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _PinnedUtterance:
    record: _ConsumerRecord
    token: VoiceTurnToken
    activation_generation: int


class VoiceInputRegistry:
    """Route one ASR utterance to one live, registry-issued consumer."""

    def __init__(self) -> None:
        self._registry_token = object()
        self._records: dict[VoiceInputConsumerIdentity, _ConsumerRecord] = {}
        self._active: _ConsumerRecord | None = None
        self._activation_generation = 0
        self._utterances: dict[VoiceTurnToken, _PinnedUtterance] = {}
        self._current_utterance_token: VoiceTurnToken | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def active_identity(self) -> VoiceInputConsumerIdentity | None:
        active = self._active
        return active.handle.identity if active is not None else None

    @property
    def active_accepts_input(self) -> bool:
        active = self._active
        if active is None or not active.capabilities.accepts_final:
            return False
        try:
            return bool(active.consumer.is_available())
        except Exception:
            return False

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
        self.invalidate_utterance("consumer_switched")
        self._activation_generation += 1
        self._active = record

    def begin_utterance(self, token: VoiceTurnToken) -> bool:
        if not isinstance(token, VoiceTurnToken) or not self.active_accepts_input:
            return False
        active = self._active
        if active is None:
            return False
        if token in self._utterances:
            return False
        self._utterances[token] = _PinnedUtterance(
            record=active,
            token=token,
            activation_generation=self._activation_generation,
        )
        self._current_utterance_token = token
        return True

    async def prepare_utterance(self) -> bool:
        route = self._live_utterance(self._current_utterance_token)
        if route is None:
            return False
        try:
            accepted = bool(await route.record.consumer.prepare_turn(route.token))
        except asyncio.CancelledError:
            raise
        except Exception:
            accepted = False
        if not accepted or self._live_utterance(route.token) is not route:
            if self._utterances.get(route.token) is route:
                self._invalidate_route(route.token, "prepare_rejected")
            return False
        return True

    async def dispatch_partial(self, event: VoicePartialEvent) -> bool:
        route = self._live_utterance(self._current_utterance_token)
        if route is None or not route.record.capabilities.accepts_partial:
            return False
        await route.record.consumer.on_partial(event)
        return self._live_utterance(route.token) is route

    async def dispatch_final(self, event: VoiceTranscriptEvent) -> bool:
        route = self._live_utterance(event.turn_token)
        if (
            route is None
            or route.token != event.turn_token
            or not route.record.capabilities.accepts_final
        ):
            return False
        # Consume the route before invoking external code. A duplicate final or
        # callback failure can never fall through to the next active consumer.
        del self._utterances[route.token]
        if self._current_utterance_token == route.token:
            self._current_utterance_token = None
        await route.record.consumer.on_final(event)
        return True

    def invalidate_utterance(self, reason: str) -> bool:
        tokens = tuple(self._utterances)
        for token in tokens:
            self._invalidate_route(token, reason)
        return bool(tokens)

    async def wait_idle(self) -> None:
        tasks = tuple(self._background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self.invalidate_utterance("registry_closed")
        self._activation_generation += 1
        self._active = None
        self._records.clear()
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

    def _resolve_handle(self, handle: VoiceInputConsumerHandle) -> _ConsumerRecord:
        if (
            not isinstance(handle, VoiceInputConsumerHandle)
            or handle._registry_token is not self._registry_token
        ):
            raise VoiceInputHandleError("VOICE_INPUT_HANDLE_FOREIGN")
        record = self._records.get(handle.identity)
        if (
            record is None
            or record.handle._registration_token is not handle._registration_token
        ):
            raise VoiceInputHandleError("VOICE_INPUT_HANDLE_STALE")
        return record

    def _live_utterance(
        self,
        token: VoiceTurnToken | None,
    ) -> _PinnedUtterance | None:
        route = self._utterances.get(token) if token is not None else None
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

    def _invalidate_route(self, token: VoiceTurnToken, reason: str) -> bool:
        route = self._utterances.pop(token, None)
        if route is None:
            return False
        if self._current_utterance_token == token:
            self._current_utterance_token = None
        self._schedule_cancel(route, reason)
        return True

    def _schedule_cancel(self, route: _PinnedUtterance, reason: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._notify_cancelled(route, str(reason or "cancelled")),
            name="voice-input-consumer-cancel",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    async def _notify_cancelled(route: _PinnedUtterance, reason: str) -> None:
        try:
            await route.record.consumer.on_cancelled(route.token, reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Cancellation is advisory and must not reopen or redirect a route.
            return
