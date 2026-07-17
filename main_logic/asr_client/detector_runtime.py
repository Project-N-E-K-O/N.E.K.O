"""Session-level local activity detector kept alive across ASR transport idle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from main_logic.voice_turn.contracts import SmartTurnConfig, SpeechActivityEvent
from main_logic.voice_turn.coordinator import TurnCoordinator
from main_logic.voice_turn.silero_vad import SileroActivityGate, SileroVad
from main_logic.voice_turn.smart_turn_v3 import SmartTurnV3

from ._voice_turn import _VoiceTurnAdapter
from .lifecycle_contracts import VoiceTurnToken
from .provider_policy import AsrProviderPolicy


@dataclass(frozen=True, slots=True)
class DetectorFeedResult:
    events: tuple[SpeechActivityEvent, ...]
    throttle_available: bool
    endpointing_available: bool = True


class SmartTurnReadiness(Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    UNLOADING = "unloading"


@dataclass(slots=True)
class SmartTurnLease:
    token: VoiceTurnToken
    _runtime: "DetectorRuntime"
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._runtime.release_endpointing(self.token)


class DetectorRuntime:
    """Serialize Silero loading and inference without owning an ASR session."""

    def __init__(
        self,
        *,
        vad: SileroVad | None = None,
        gate: SileroActivityGate | None = None,
        rnnoise_onset_probability: float = 0.35,
        provider_policy: AsrProviderPolicy | None = None,
        coordinator: TurnCoordinator | None = None,
        on_turn_complete: Callable[[], Awaitable[None]] | None = None,
        on_endpointing_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not 0.0 <= rnnoise_onset_probability <= 1.0:
            raise ValueError("RNNoise onset probability must be within [0, 1]")
        if vad is None:
            config = SmartTurnConfig(enabled=True)
            vad = SileroVad(
                enabled=True,
                inference_error_limit=config.inference_error_limit,
            )
            gate = SileroActivityGate(vad, config)
        if gate is None:
            raise ValueError("DetectorRuntime gate is required with a custom VAD")
        self._vad = vad
        self._gate = gate
        self._lock = asyncio.Lock()
        self._load_attempted = False
        self._available = True
        self._closed = False
        self._rnnoise_onset_probability = rnnoise_onset_probability
        self._speech_active = False
        self._events: list[SpeechActivityEvent] = []
        self._semantic_adapter: _VoiceTurnAdapter | None = None
        self._semantic_coordinator: TurnCoordinator | None = None
        self._semantic_started = False
        self._semantic_generation = 0
        self._semantic_turn_id = 1
        self._on_endpointing_failure = on_endpointing_failure
        self._on_turn_complete = on_turn_complete
        self._defer_turn_complete = False
        self._deferred_turn_complete = False
        self._failure_watch_task: asyncio.Task[None] | None = None
        self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
        self._smart_turn_token: VoiceTurnToken | None = None
        if provider_policy is not None and provider_policy.endpoint_authority == "smart_turn":
            if on_turn_complete is None:
                raise ValueError("SmartTurn DetectorRuntime requires on_turn_complete")
            config = SmartTurnConfig(enabled=True)
            semantic_coordinator = coordinator or TurnCoordinator(
                SmartTurnV3(
                    enabled=True,
                    inference_error_limit=config.inference_error_limit,
                ),
                config,
            )
            self._semantic_coordinator = semantic_coordinator

            async def commit(_generation: int, _buffer_epoch: int, _turn_id: int) -> None:
                if self._defer_turn_complete:
                    self._deferred_turn_complete = True
                    return
                # 当前轮 seal 后立即把检测身份推进到下一轮。旧 provider final
                # 到达前，新语音只做本地语义判断，完成信号延迟发布。
                self._defer_turn_complete = True
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                adapter = self._semantic_adapter
                if adapter is not None:
                    await adapter.reset(
                        generation=self._semantic_generation,
                        buffer_epoch=0,
                        utterance_id=self._semantic_turn_id,
                    )
                await on_turn_complete()

            async def activity(event: SpeechActivityEvent) -> None:
                self._events.append(event)

            self._semantic_adapter = _VoiceTurnAdapter(
                vad=self._vad,
                gate=self._gate,
                coordinator=semantic_coordinator,
                on_commit=commit,
                on_activity=activity,
                smart_turn_required=True,
            )

    @property
    def smart_turn_readiness(self) -> SmartTurnReadiness:
        return self._smart_turn_readiness

    async def prepare_endpointing(
        self,
        token: VoiceTurnToken,
    ) -> SmartTurnLease | None:
        """Load and pin SmartTurn before any provider wire audio is allowed."""

        async with self._lock:
            adapter = self._semantic_adapter
            coordinator = self._semantic_coordinator
            if self._closed or adapter is None or coordinator is None:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
                return None
            if (
                self._smart_turn_token == token
                and self._smart_turn_readiness is SmartTurnReadiness.READY
                and not adapter.failed
            ):
                return SmartTurnLease(token, self)
            if self._smart_turn_token is not None:
                return None
            self._smart_turn_readiness = SmartTurnReadiness.LOADING
            if not self._semantic_started:
                await adapter.start()
                self._semantic_started = True
                self._failure_watch_task = asyncio.create_task(
                    self._watch_semantic_failure(adapter),
                    name="detector-runtime-smart-turn-watch",
                )
            adapter.pin_smart_turn()
            loaded = await coordinator.prepare_predictor()
            if not loaded or adapter.failed:
                adapter.unpin_smart_turn()
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
                return None
            self._smart_turn_token = token
            self._smart_turn_readiness = SmartTurnReadiness.READY
            return SmartTurnLease(token, self)

    def endpointing_ready(self, token: VoiceTurnToken) -> bool:
        adapter = self._semantic_adapter
        return bool(
            not self._closed
            and adapter is not None
            and not adapter.failed
            and self._smart_turn_readiness is SmartTurnReadiness.READY
            and self._smart_turn_token == token
        )

    async def release_endpointing(self, token: VoiceTurnToken) -> None:
        async with self._lock:
            if self._smart_turn_token != token:
                return
            self._smart_turn_token = None
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()

    async def invalidate(self, token: VoiceTurnToken) -> None:
        async with self._lock:
            if self._smart_turn_token != token:
                return
            self._smart_turn_token = None
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED

    async def feed(
        self,
        pcm16: bytes,
        *,
        speech_probability: float | None = None,
    ) -> DetectorFeedResult:
        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DetectorRuntime requires complete PCM16 bytes")
        if not pcm16:
            return DetectorFeedResult((), self._available)
        if speech_probability is not None and not 0.0 <= speech_probability <= 1.0:
            raise ValueError("speech_probability must be within [0, 1]")
        async with self._lock:
            if self._closed or not self._available:
                return DetectorFeedResult((), False)
            if (
                speech_probability is not None
                and not self._speech_active
                and speech_probability < self._rnnoise_onset_probability
            ):
                return DetectorFeedResult((), True)
            adapter = self._semantic_adapter
            if adapter is not None:
                if not self._semantic_started:
                    await adapter.start()
                    self._semantic_started = True
                    self._failure_watch_task = asyncio.create_task(
                        self._watch_semantic_failure(adapter),
                        name="detector-runtime-smart-turn-watch",
                    )
                self._events.clear()
                await adapter.push_audio(
                    generation=self._semantic_generation,
                    buffer_epoch=0,
                    utterance_id=self._semantic_turn_id,
                    pcm16=pcm16,
                )
                await adapter.wait_idle()
                if adapter.failed:
                    failure = adapter.failure
                    endpointing_available = getattr(failure, "stage", None) not in {
                        "smart_turn",
                        "consumer",
                    }
                    return DetectorFeedResult(
                        (),
                        False,
                        endpointing_available=endpointing_available,
                    )
                events = tuple(self._events)
                if any(
                    event
                    in {
                        SpeechActivityEvent.SPEECH_STARTED,
                        SpeechActivityEvent.SPEECH_RESUMED,
                    }
                    for event in events
                ):
                    self._speech_active = True
                return DetectorFeedResult(events, True)
            if not self._load_attempted:
                self._load_attempted = True
                try:
                    self._available = bool(await asyncio.to_thread(self._vad.load))
                except Exception:
                    self._available = False
                if not self._available:
                    return DetectorFeedResult((), False)
            # PC 48k 已经过 RNNoise：低概率环境音在尚未进入说话态时
            # 不唤醒 Silero；移动端 16k 没有该概率，仍完整运行 Silero。
            try:
                events = tuple(await asyncio.to_thread(self._gate.feed, pcm16))
            except Exception:
                self._available = False
                return DetectorFeedResult((), False)
            if any(
                event
                in {
                    SpeechActivityEvent.SPEECH_STARTED,
                    SpeechActivityEvent.SPEECH_RESUMED,
                }
                for event in events
            ):
                self._speech_active = True
        return DetectorFeedResult(events, True)

    async def reset(self) -> None:
        async with self._lock:
            if self._closed:
                return
            if self._semantic_adapter is not None and self._semantic_started:
                if self._smart_turn_token is not None:
                    self._smart_turn_token = None
                    self._semantic_adapter.unpin_smart_turn()
                self._defer_turn_complete = False
                self._deferred_turn_complete = False
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                await self._semantic_adapter.reset(
                    generation=self._semantic_generation,
                    buffer_epoch=0,
                    utterance_id=self._semantic_turn_id,
                )
                self._speech_active = False
                return
            await asyncio.to_thread(self._gate.reset)
            self._speech_active = False

    async def release_deferred_turn(self) -> None:
        """Release a deferred SmartTurn completion after the prior final."""

        callback: Callable[[], Awaitable[None]] | None = None
        async with self._lock:
            if self._closed or self._semantic_adapter is None:
                return
            self._defer_turn_complete = False
            if self._deferred_turn_complete:
                self._deferred_turn_complete = False
                self._defer_turn_complete = True
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                await self._semantic_adapter.reset(
                    generation=self._semantic_generation,
                    buffer_epoch=0,
                    utterance_id=self._semantic_turn_id,
                )
                callback = self._on_turn_complete
        if callback is not None:
            # 不持有 detector lock 调用 Core，避免 Core 清理时反向 reset 死锁。
            await callback()

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            watch_task, self._failure_watch_task = self._failure_watch_task, None
            if watch_task is not None:
                watch_task.cancel()
            if self._semantic_adapter is not None:
                self._smart_turn_token = None
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADING
                await self._semantic_adapter.close()
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
                return
            await asyncio.to_thread(self._vad.close)

    async def _watch_semantic_failure(self, adapter: _VoiceTurnAdapter) -> None:
        try:
            failure = await adapter.wait_failure()
            if getattr(failure, "stage", None) in {"vad_load", "vad_feed"}:
                self._available = False
                return
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            callback = self._on_endpointing_failure
            if callback is not None and not self._closed:
                await callback()
        except asyncio.CancelledError:
            return
