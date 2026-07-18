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
from .detector_contracts import (
    BoundDetectorTurn,
    DetectorActivityEvent,
    DetectorCandidateKey,
    DetectorEvent,
    DetectorIngressIdentity,
    DetectorSubmitResult,
    DetectorSubmitStatus,
    DetectorTurnEvent,
)
from .lifecycle_contracts import VoiceIngressToken, VoiceTurnToken
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
        await self._runtime.release_endpointing(self.token)
        self._released = True


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
        on_event: Callable[[DetectorEvent], Awaitable[None]] | None = None,
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
        self._on_event = on_event
        self._defer_turn_complete = False
        self._deferred_turn_complete = False
        self._failure_watch_task: asyncio.Task[None] | None = None
        self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
        self._smart_turn_token: VoiceTurnToken | None = None
        self._prepare_task: asyncio.Task[bool] | None = None
        self._prepare_token: VoiceTurnToken | None = None
        self._prepare_epoch: int | None = None
        self._detector_epoch = 0
        self._sequence_no = 0
        self._ingress_token: VoiceIngressToken | None = None
        self._candidate_open = False
        self._candidate_generation = 0
        self._bound_turns: dict[DetectorCandidateKey, BoundDetectorTurn] = {}
        self._deferred_completions: dict[
            DetectorCandidateKey, DetectorIngressIdentity
        ] = {}
        if provider_policy is not None and provider_policy.endpoint_authority == "smart_turn":
            if on_turn_complete is None and on_event is None:
                raise ValueError(
                    "SmartTurn DetectorRuntime requires a completion consumer"
                )
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
                self._candidate_open = False
                if self._defer_turn_complete:
                    self._deferred_turn_complete = True
                    return
                # 当前轮 seal 后立即把检测身份推进到下一轮。旧 provider final
                # 到达前，新语音只做本地语义判断，完成信号延迟发布。
                self._defer_turn_complete = True
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                self._candidate_generation += 1
                adapter = self._semantic_adapter
                if adapter is not None:
                    await adapter.reset(
                        generation=self._semantic_generation,
                        buffer_epoch=0,
                        utterance_id=self._semantic_turn_id,
                    )
                if on_turn_complete is not None:
                    await on_turn_complete()

            async def activity(event: SpeechActivityEvent) -> None:
                self._events.append(event)

            async def scoped_activity(
                event: SpeechActivityEvent,
                identity: DetectorIngressIdentity,
            ) -> None:
                if self._on_event is None or identity.detector_epoch != self._detector_epoch:
                    return
                await self._on_event(
                    DetectorActivityEvent(
                        ingress=identity,
                        candidate=DetectorCandidateKey(
                            identity.detector_epoch,
                            self._candidate_generation,
                        ),
                        activity=event,
                    )
                )

            async def scoped_commit(
                _generation: int,
                _buffer_epoch: int,
                _turn_id: int,
                identity: DetectorIngressIdentity,
            ) -> None:
                if self._on_event is None or identity.detector_epoch != self._detector_epoch:
                    return
                candidate = DetectorCandidateKey(
                    identity.detector_epoch,
                    self._candidate_generation,
                )
                bound_turn = self._bound_turns.get(candidate)
                if bound_turn is None:
                    self._deferred_completions[candidate] = identity
                    return
                await self._on_event(
                    DetectorTurnEvent(
                        ingress=identity,
                        bound_turn=bound_turn,
                        kind="complete",
                    )
                )

            self._semantic_adapter = _VoiceTurnAdapter(
                vad=self._vad,
                gate=self._gate,
                coordinator=semantic_coordinator,
                on_commit=commit,
                on_activity=activity,
                on_scoped_commit=scoped_commit,
                on_scoped_activity=scoped_activity,
                smart_turn_required=True,
            )

    @property
    def smart_turn_readiness(self) -> SmartTurnReadiness:
        return self._smart_turn_readiness

    @property
    def detector_epoch(self) -> int:
        return self._detector_epoch

    @property
    def candidate_open(self) -> bool:
        return self._candidate_open

    @property
    def queued_audio_ms(self) -> int:
        adapter = self._semantic_adapter
        return adapter.queued_audio_ms if adapter is not None else 0

    @property
    def smart_turn_evaluation_ms(self) -> int:
        adapter = self._semantic_adapter
        return adapter.smart_turn_evaluation_ms if adapter is not None else 0

    @property
    def smart_turn_stale_result_count(self) -> int:
        adapter = self._semantic_adapter
        return adapter.smart_turn_stale_result_count if adapter is not None else 0

    @property
    def smart_turn_coalesced_evaluation_count(self) -> int:
        adapter = self._semantic_adapter
        return (
            adapter.smart_turn_coalesced_evaluation_count
            if adapter is not None
            else 0
        )

    async def bind_candidate(
        self,
        candidate: DetectorCandidateKey,
        turn_token: VoiceTurnToken,
    ) -> BoundDetectorTurn | None:
        if (
            self._closed
            or candidate.detector_epoch != self._detector_epoch
            or (
                candidate.candidate_generation != self._candidate_generation
                and candidate not in self._deferred_completions
            )
        ):
            return None
        existing = self._bound_turns.get(candidate)
        if existing is not None:
            return existing if existing.turn_token == turn_token else None
        bound = BoundDetectorTurn(candidate, turn_token)
        self._bound_turns[candidate] = bound
        deferred = self._deferred_completions.pop(candidate, None)
        if deferred is not None and self._on_event is not None:
            await self._on_event(
                DetectorTurnEvent(
                    ingress=deferred,
                    bound_turn=bound,
                    kind="complete",
                )
            )
        return bound

    async def force_speech_started(
        self,
        identity: DetectorIngressIdentity,
    ) -> bool:
        """Open continuous-upload mode without changing SmartTurn authority."""

        if (
            self._on_event is None
            or self._closed
            or identity.detector_epoch != self._detector_epoch
        ):
            return False
        await self._on_event(
            DetectorActivityEvent(
                ingress=identity,
                candidate=DetectorCandidateKey(
                    identity.detector_epoch,
                    self._candidate_generation,
                ),
                activity=SpeechActivityEvent.SPEECH_STARTED,
            )
        )
        return True

    async def prepare_endpointing(
        self,
        token: VoiceTurnToken,
    ) -> SmartTurnLease | None:
        """Load and pin SmartTurn before any provider wire audio is allowed."""

        adapter = self._semantic_adapter
        coordinator = self._semantic_coordinator
        if self._closed or adapter is None or coordinator is None:
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            return None
        await self._ensure_semantic_started(adapter)
        prepare_task: asyncio.Task[bool] | None = None
        async with self._lock:
            if self._closed or adapter.failed:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
                return None
            if (
                self._smart_turn_token == token
                and self._smart_turn_readiness is SmartTurnReadiness.READY
            ):
                return SmartTurnLease(token, self)
            if self._smart_turn_token is not None:
                return None
            if (
                self._smart_turn_readiness is SmartTurnReadiness.READY
                and self._prepare_task is None
            ):
                adapter.pin_smart_turn()
                self._smart_turn_token = token
                return SmartTurnLease(token, self)
            if self._prepare_task is not None:
                if self._prepare_token != token:
                    return None
                prepare_task = self._prepare_task
            else:
                self._smart_turn_readiness = SmartTurnReadiness.LOADING
                self._prepare_token = token
                self._prepare_epoch = self._detector_epoch
                adapter.pin_smart_turn()
                prepare_task = asyncio.create_task(
                    self._prepare_endpointing_task(
                        adapter,
                        coordinator,
                        token,
                        self._detector_epoch,
                    ),
                    name="detector-runtime-smart-turn-prepare",
                )
                self._prepare_task = prepare_task
        if prepare_task is None or not await asyncio.shield(prepare_task):
            return None
        if self.endpointing_ready(token):
            return SmartTurnLease(token, self)
        return None

    async def _prepare_endpointing_task(
        self,
        adapter: _VoiceTurnAdapter,
        coordinator: TurnCoordinator,
        token: VoiceTurnToken,
        detector_epoch: int,
    ) -> bool:
        loaded = await coordinator.prepare_predictor()
        async with self._lock:
            if self._prepare_task is asyncio.current_task():
                self._prepare_task = None
            valid = bool(
                not self._closed
                and not adapter.failed
                and self._prepare_token == token
                and self._prepare_epoch == detector_epoch
                and self._detector_epoch == detector_epoch
            )
            self._prepare_token = None
            self._prepare_epoch = None
            if valid and loaded:
                self._smart_turn_token = token
                self._smart_turn_readiness = SmartTurnReadiness.READY
                return True
            adapter.unpin_smart_turn()
            if valid:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
            return False

    async def _ensure_semantic_started(self, adapter: _VoiceTurnAdapter) -> None:
        if self._semantic_started:
            return
        await adapter.start()
        self._semantic_started = True
        self._failure_watch_task = asyncio.create_task(
            self._watch_semantic_failure(adapter),
            name="detector-runtime-smart-turn-watch",
        )

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
            if self._smart_turn_token != token and self._prepare_token != token:
                return
            self._smart_turn_token = None
            self._prepare_token = None
            self._prepare_epoch = None
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()

    async def invalidate(self, token: VoiceTurnToken) -> None:
        async with self._lock:
            if self._smart_turn_token != token and self._prepare_token != token:
                return
            self._smart_turn_token = None
            self._prepare_token = None
            self._prepare_epoch = None
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED

    async def feed(
        self,
        pcm16: bytes,
        *,
        speech_probability: float | None = None,
        rnnoise_available: bool | None = None,
    ) -> DetectorFeedResult:
        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DetectorRuntime requires complete PCM16 bytes")
        if not pcm16:
            return DetectorFeedResult((), self._available)
        if speech_probability is not None and not 0.0 <= speech_probability <= 1.0:
            raise ValueError("speech_probability must be within [0, 1]")
        if rnnoise_available is None:
            rnnoise_available = speech_probability is not None
        adapter = self._semantic_adapter
        if adapter is not None:
            self._events.clear()
            ingress_token = self._ingress_token or VoiceIngressToken(
                session_epoch=0,
                connection_id="detector-feed-compat",
                lease_generation=0,
                route_generation=0,
                audio_generation=0,
            )
            submitted = await self.submit_audio(
                pcm16,
                ingress_token=ingress_token,
                sample_rate_hz=16_000,
                speech_probability=speech_probability,
                rnnoise_available=rnnoise_available,
            )
            if submitted.status is DetectorSubmitStatus.SKIPPED_QUIET:
                return DetectorFeedResult((), submitted.throttle_available)
            if submitted.status is not DetectorSubmitStatus.ACCEPTED:
                return DetectorFeedResult(
                    (),
                    submitted.throttle_available,
                    endpointing_available=submitted.endpointing_available,
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
            return DetectorFeedResult(events, adapter.throttle_available)
        async with self._lock:
            if self._closed or not self._available:
                return DetectorFeedResult((), False)
            if (
                rnnoise_available
                and speech_probability is not None
                and not self._speech_active
                and speech_probability < self._rnnoise_onset_probability
            ):
                return DetectorFeedResult((), True)
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

    async def submit_audio(
        self,
        pcm16: bytes,
        *,
        ingress_token: VoiceIngressToken,
        sample_rate_hz: int,
        speech_probability: float | None,
        rnnoise_available: bool,
    ) -> DetectorSubmitResult:
        """Validate and enqueue one frame without waiting for detector inference."""

        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DetectorRuntime requires complete PCM16 bytes")
        if sample_rate_hz <= 0:
            raise ValueError("DetectorRuntime sample rate must be positive")
        if speech_probability is not None and not 0.0 <= speech_probability <= 1.0:
            raise ValueError("speech_probability must be within [0, 1]")
        adapter = self._semantic_adapter
        if self._closed:
            return DetectorSubmitResult(
                DetectorSubmitStatus.CLOSED,
                False,
                False,
                None,
            )
        if adapter is None or adapter.failed:
            return DetectorSubmitResult(
                DetectorSubmitStatus.FAILED,
                False,
                False,
                None,
            )
        if not pcm16:
            return DetectorSubmitResult(
                DetectorSubmitStatus.SKIPPED_QUIET,
                adapter.throttle_available,
                True,
                None,
            )
        if self._ingress_token is None:
            self._ingress_token = ingress_token
        elif self._ingress_token != ingress_token:
            return DetectorSubmitResult(
                DetectorSubmitStatus.FAILED,
                adapter.throttle_available,
                True,
                None,
            )
        if (
            rnnoise_available
            and speech_probability is not None
            and not self._candidate_open
            and speech_probability < self._rnnoise_onset_probability
        ):
            return DetectorSubmitResult(
                DetectorSubmitStatus.SKIPPED_QUIET,
                adapter.throttle_available,
                True,
                None,
            )
        self._candidate_open = True
        await self._ensure_semantic_started(adapter)
        next_sequence = self._sequence_no + 1
        identity = DetectorIngressIdentity(
            ingress_token=ingress_token,
            detector_epoch=self._detector_epoch,
            sequence_no=next_sequence,
        )
        try:
            await adapter.push_audio(
                generation=self._semantic_generation,
                buffer_epoch=0,
                utterance_id=self._semantic_turn_id,
                pcm16=pcm16,
                sample_rate_hz=sample_rate_hz,
                detector_identity=identity,
            )
        except asyncio.QueueFull:
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._semantic_generation += 1
            self._semantic_turn_id += 1
            asyncio.create_task(
                adapter.reset(
                    generation=self._semantic_generation,
                    buffer_epoch=0,
                    utterance_id=self._semantic_turn_id,
                ),
                name="detector-runtime-overflow-reset",
            )
            return DetectorSubmitResult(
                DetectorSubmitStatus.BACKPRESSURE,
                adapter.throttle_available,
                True,
                None,
            )
        self._sequence_no = next_sequence
        return DetectorSubmitResult(
            DetectorSubmitStatus.ACCEPTED,
            adapter.throttle_available,
            True,
            identity,
        )

    async def reset(self) -> None:
        adapter: _VoiceTurnAdapter | None = None
        semantic_identity: tuple[int, int, int] | None = None
        async with self._lock:
            if self._closed:
                return
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._sequence_no = 0
            self._ingress_token = None
            self._candidate_open = False
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._speech_active = False
            self._prepare_token = None
            self._prepare_epoch = None
            if self._semantic_adapter is not None and self._semantic_started:
                if self._smart_turn_token is not None:
                    self._smart_turn_token = None
                    self._semantic_adapter.unpin_smart_turn()
                self._defer_turn_complete = False
                self._deferred_turn_complete = False
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                adapter = self._semantic_adapter
                semantic_identity = (
                    self._semantic_generation,
                    0,
                    self._semantic_turn_id,
                )
        if adapter is not None and semantic_identity is not None:
            await adapter.reset(
                generation=semantic_identity[0],
                buffer_epoch=semantic_identity[1],
                utterance_id=semantic_identity[2],
            )
            return
        await asyncio.to_thread(self._gate.reset)

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
        adapter: _VoiceTurnAdapter | None = None
        vad = None
        prepare_task: asyncio.Task[bool] | None = None
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            watch_task, self._failure_watch_task = self._failure_watch_task, None
            if watch_task is not None:
                watch_task.cancel()
            if self._semantic_adapter is not None:
                self._smart_turn_token = None
                self._prepare_token = None
                self._prepare_epoch = None
                prepare_task, self._prepare_task = self._prepare_task, None
                if prepare_task is not None:
                    prepare_task.cancel()
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADING
                adapter = self._semantic_adapter
            else:
                vad = self._vad
        if adapter is not None:
            await adapter.close()
            if prepare_task is not None:
                await asyncio.gather(prepare_task, return_exceptions=True)
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
            return
        await asyncio.to_thread(vad.close)

    async def _watch_semantic_failure(self, adapter: _VoiceTurnAdapter) -> None:
        try:
            failure = await adapter.wait_failure()
            if getattr(failure, "stage", None) in {"vad_load", "vad_feed"}:
                self._available = False
                return
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            callback = self._on_endpointing_failure
            if callback is not None and not self._closed:
                await callback()
        except asyncio.CancelledError:
            return
