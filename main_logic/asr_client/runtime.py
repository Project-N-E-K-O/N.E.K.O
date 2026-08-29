"""Provider-neutral independent-ASR runtime with explicit Core callbacks."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from main_logic.asr_client import (
    _attach_partial_callback,
    _create_asr_session_from_selection,
    _resolve_asr_selection,
)
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitResult,
    AsrSubmitStatus,
    SpeechActivityEvent,
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame

from ._infra import logger, _READY_TIMEOUT_SECONDS
from .audio import AsrAudioDispatcher
from .candidate_control import CandidateRejectionOutcome, CandidateRejectionRequest
from ._registry_meta import AsrProviderAvailability
from .endpointing.detector import (
    AsrDetectorDispatcher,
    CoreDetectorEventEnvelope,
    DetectorActivityEvent,
    DetectorCandidateKey,
    DetectorPrewarmEvent,
    DetectorRuntimeEvent,
    DetectorTransportPrewarmEvent,
    DetectorSubmitStatus,
    DetectorTurnEvent,
    ProviderCandidateFence,
)
from .endpointing.detector_runtime import (
    DetectorCandidateRejectionLease,
    DetectorRuntime,
    SmartTurnLease,
)
from .endpointing.throttle_policy import ThrottleAction
from .lifecycle import (
    AudioDisposition,
    FinalKey,
    VoiceIngressToken,
    VoiceInputLifecycleController,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
    VoiceTransportToken,
)
from .provider_policy import resolve_provider_policy
from .speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowObserver,
)
from .transcript import (
    TranscriptDispatcher,
    TranscriptEnvelope,
)


# The frontend gives a voice start this long before it cancels and fires
# end_session (app-buttons.js, and the automatic-restart path in
# app-websocket.js use the same value). Mirrored here because
# _start_session_activate awaits the ASR connect loop BEFORE sending
# session_started: any retry budget that outlives this deadline cannot produce
# a verdict the client will still be listening for.
_FRONTEND_START_DEADLINE_SECONDS = 15.0

# Aggregate ceiling for the whole connect-and-retry phase. Deliberately under
# the deadline above, leaving room for the rest of the start (the ack send and
# the pending-input flush that follow it) so the fail-closed verdict lands
# BEFORE the client gives up rather than a second after.
_CONNECT_TOTAL_BUDGET_SECONDS = 12.0

# Public alias. The dedupe reroute in core/lifecycle.py runs a whole extra
# connect phase AFTER already spending part of the frontend deadline waiting,
# so it has to know this ceiling to tell whether its verdict can still land
# before the client gives up.
ASR_CONNECT_TOTAL_BUDGET_SECONDS = _CONNECT_TOTAL_BUDGET_SECONDS
_CANDIDATE_REJECTION_WATCHDOG_SECONDS = 10.0
_CANDIDATE_REJECTION_RECOVERY_STEP_TIMEOUT_SECONDS = 1.0
_CANDIDATE_REJECTION_REINSTALL_ATTEMPTS = 2


def _uses_smart_turn_endpointing(provider_policy: Any) -> bool:
    """Honor the endpoint authority independently of transport shape."""

    return bool(provider_policy.endpoint_authority == "smart_turn")


class AsrStartStatus(Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AsrStartResult:
    status: AsrStartStatus
    provider: str | None = None
    failure_code: str | None = None
    session_epoch: int = -1


@dataclass(frozen=True, slots=True)
class AsrRuntimeCallbacks:
    display_name: Callable[[], str]
    on_prepare_turn: Callable[[VoiceTurnToken], Awaitable[bool]]
    on_partial: Callable[[VoicePartialEvent], Awaitable[None]]
    on_final: Callable[[VoiceTranscriptEvent], Awaitable[None]]
    on_turn_abandoned: Callable[[VoiceTurnToken], Awaitable[None]]
    on_failure: Callable[[AsrFailureEvent], Awaitable[None]]
    on_status: Callable[[AsrStatusEvent], Awaitable[None]]
    on_lifecycle: Callable[[AsrLifecycleNotification], Awaitable[None]]


SpeakerShadowFactory = Callable[[], SpeakerShadowObserver | None]


@dataclass(frozen=True, slots=True)
class _AsrRuntimeIdentity:
    start_generation: int
    session_epoch: int
    audio_generation: int
    lifecycle: VoiceInputLifecycleController | None
    transport_generation: int | None
    detector: DetectorRuntime | None
    session: Any
    provider: str | None
    session_factory: Any
    transport_selection: Any
    transport_task: asyncio.Task[None] | None
    ingress_token: VoiceIngressToken | None = None
    turn_token: VoiceTurnToken | None = None


@dataclass(frozen=True, slots=True)
class _CandidateRejectionSuppression:
    request: CandidateRejectionRequest
    turn_token: VoiceTurnToken
    final_key: FinalKey
    lifecycle: VoiceInputLifecycleController
    detector: DetectorRuntime


class IndependentAsrRuntime:
    """Own one independent ASR session without reading Core manager state."""

    def __init__(self, callbacks: AsrRuntimeCallbacks) -> None:
        self._callbacks = callbacks
        self._init_asr_runtime_state()

    @property
    def display_name(self) -> str:
        return self._callbacks.display_name()

    async def close(self) -> None:
        self._ensure_asr_runtime_state()
        close_task = self._asr_runtime_close_task
        if close_task is None:
            # Explicit close owns a different operation from start's detached
            # predecessor cleanup. Invalidate the in-flight start before
            # awaiting either cleanup, then wait for both under one explicit
            # close latch so cancellation/retry retains the same owner.
            operation_generation = self._begin_asr_start_operation()
            predecessor_cleanups = tuple(self._asr_owned_cleanup_tasks)
            cleanup = self._detach_independent_asr(
                operation_generation=operation_generation,
            )
            # Started HERE, not inside the joiner below. The resources this
            # close just detached are its own and independent of any retired
            # teardown; sequencing them behind a predecessor that never
            # returns -- a retired provider stuck in session.close(), say --
            # would keep this generation's detector and session physically
            # open for as long as that lasts.
            cleanup_task = (
                self._schedule_owned_cleanup(
                    cleanup,
                    name="independent-asr-close-detached",
                )
                if cleanup is not None
                else None
            )
            close_task = self._schedule_owned_cleanup(
                self._finish_explicit_asr_close(
                    predecessor_cleanups,
                    cleanup_task,
                ),
                name="independent-asr-close",
            )
            self._asr_runtime_close_task = close_task
        await asyncio.shield(close_task)

    @staticmethod
    async def _finish_explicit_asr_close(
        predecessor_cleanups: tuple[asyncio.Task[Any], ...],
        cleanup_task: "asyncio.Task[Any] | None",
    ) -> None:
        """Join both teardowns; ``cleanup_task`` is already running."""

        if predecessor_cleanups:
            await asyncio.gather(
                *predecessor_cleanups,
                return_exceptions=True,
            )
        if cleanup_task is not None:
            # Awaited last but NOT started last, and awaited bare so its
            # failure still reaches the owned-cleanup logger.
            await cleanup_task

    async def set_speaker_verifier_factory(
        self,
        factory: SpeakerShadowFactory | None,
        *,
        activation_generation: str,
    ) -> bool:
        """Hot-replace Owner verification without restarting independent ASR."""

        if factory is not None and not callable(factory):
            raise TypeError("factory must be callable or None")
        if (
            type(activation_generation) is not str
            or not activation_generation.strip()
        ):
            raise ValueError("activation_generation must be a non-empty string")
        self._ensure_asr_runtime_state()
        async with self._speaker_verifier_lock:
            return await self._set_speaker_verifier_factory_locked(
                factory,
                activation_generation=activation_generation,
            )

    async def _set_speaker_verifier_factory_locked(
        self,
        factory: SpeakerShadowFactory | None,
        *,
        activation_generation: str,
    ) -> bool:
        old_factory = self._speaker_verifier_factory
        if (
            factory is old_factory
            and activation_generation
            == self._speaker_verifier_activation_generation
            and not self._speaker_verifier_degraded
        ):
            return True

        # Revocation is a logical authority barrier, not a cleanup result.
        # Publish it before yielding so every callback from the old observer
        # becomes stale even if physical detector replacement later fails.
        revoking = factory is None
        if revoking:
            self._speaker_verifier_factory = None
            self._speaker_verifier_activation_generation = activation_generation
            if old_factory is not None:
                self._close_speaker_verifier_factory(old_factory)

        detector = self._asr_detector
        if detector is not None:
            new_shadow: SpeakerShadowObserver | None = None
            if factory is not None:
                try:
                    new_shadow = factory()
                except Exception:
                    return False
                if new_shadow is None:
                    return False
            try:
                await detector.replace_speaker_verifier(new_shadow)
            except asyncio.CancelledError:
                await self._close_created_speaker_shadow(new_shadow)
                raise
            except Exception:
                await self._close_created_speaker_shadow(new_shadow)
                return False
            if self._asr_detector is not detector:
                # The detached detector owns and closes ``new_shadow``. Apply
                # the same activation to the replacement, if one appeared.
                replacement = self._asr_detector
                if replacement is not None:
                    replacement_shadow: SpeakerShadowObserver | None = None
                    if factory is not None:
                        try:
                            replacement_shadow = factory()
                        except Exception:
                            return False
                        if replacement_shadow is None:
                            return False
                    try:
                        await replacement.replace_speaker_verifier(
                            replacement_shadow
                        )
                    except asyncio.CancelledError:
                        await self._close_created_speaker_shadow(
                            replacement_shadow
                        )
                        raise
                    except Exception:
                        await self._close_created_speaker_shadow(
                            replacement_shadow
                        )
                        return False
                    if self._asr_detector is not replacement:
                        return False

        if not revoking:
            self._speaker_verifier_factory = factory
            self._speaker_verifier_activation_generation = activation_generation
            if old_factory is not None and old_factory is not factory:
                self._close_speaker_verifier_factory(old_factory)
        self._speaker_verifier_degraded = False
        return True

    def request_speaker_candidate_rejection(
        self,
        candidate: SpeakerShadowCandidateKey,
        *,
        activation_generation: str,
    ) -> bool:
        """Schedule one advisory rejection while retaining task ownership."""

        if (
            type(candidate) is not SpeakerShadowCandidateKey
            or type(activation_generation) is not str
            or not activation_generation.strip()
            or activation_generation
            != self._speaker_verifier_activation_generation
        ):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        task = loop.create_task(
            self._reject_speaker_candidate(
                candidate,
                activation_generation=activation_generation,
            ),
            name="owner-voice-candidate-rejection",
        )
        self._asr_rejection_tasks.add(task)
        task.add_done_callback(self._reap_rejection_task)
        return True

    def _mark_speaker_verifier_degraded(self) -> None:
        """Expose Owner verifier health without changing ASR transport flow."""

        self._ensure_asr_runtime_state()
        self._speaker_verifier_degraded = True

    def _mark_speaker_verifier_healthy(self) -> None:
        """Clear transient Owner verifier health degradation after recovery."""

        self._ensure_asr_runtime_state()
        self._speaker_verifier_degraded = False

    @staticmethod
    def _close_speaker_verifier_factory(factory: SpeakerShadowFactory) -> None:
        close = getattr(factory, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            return

    def _reap_rejection_task(
        self,
        task: asyncio.Task[CandidateRejectionOutcome | None],
    ) -> None:
        self._asr_rejection_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            return

    def _begin_asr_start_operation(self) -> int:
        self._asr_start_generation += 1
        return self._asr_start_generation

    def _asr_start_operation_matches(self, operation_generation: int) -> bool:
        return operation_generation == self._asr_start_generation

    def _invalidate_asr_start(self) -> None:
        self._begin_asr_start_operation()

    def capture_ingress_token(
        self,
        *,
        connection_id: str,
        lease_generation: int,
        route_generation: int,
    ) -> VoiceIngressToken:
        return VoiceIngressToken(
            session_epoch=self._asr_session_epoch,
            connection_id=connection_id,
            lease_generation=lease_generation,
            route_generation=route_generation,
            audio_generation=self._asr_audio_generation,
        )

    async def suspend(self, reason: str) -> None:
        lifecycle = self._asr_lifecycle
        if lifecycle is not None and lifecycle.snapshot.state not in {
            VoiceLifecycleState.OFF,
            VoiceLifecycleState.BLOCKED,
            VoiceLifecycleState.SUSPENDED,
        }:
            lifecycle.transition(VoiceLifecycleEvent.GAME_TAKEOVER)
        await self.abort(reason)

    async def resume(self, reason: str) -> None:
        del reason
        epoch = self._asr_session_epoch
        provider = self._asr_provider or "unknown"
        lifecycle = self._asr_lifecycle
        if lifecycle is not None and (
            lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
        ):
            lifecycle.transition(VoiceLifecycleEvent.GAME_RELEASED)
            identity = self._capture_runtime_identity()
            await self._send_asr_lifecycle_state(
                lifecycle.snapshot.state,
                provider=provider,
                session_epoch=epoch,
                expected_identity=identity,
            )

    def _asr_runtime_refs_match(
        self,
        epoch: int,
        lifecycle: VoiceInputLifecycleController | None,
        detector: DetectorRuntime | None,
    ) -> bool:
        return bool(
            epoch == self._asr_session_epoch
            and self._asr_lifecycle is lifecycle
            and self._asr_detector is detector
        )

    def _capture_runtime_identity(
        self,
        *,
        ingress_token: VoiceIngressToken | None = None,
        turn_token: VoiceTurnToken | None = None,
    ) -> _AsrRuntimeIdentity:
        lifecycle = self._asr_lifecycle
        return _AsrRuntimeIdentity(
            start_generation=self._asr_start_generation,
            session_epoch=self._asr_session_epoch,
            audio_generation=self._asr_audio_generation,
            lifecycle=lifecycle,
            transport_generation=(
                lifecycle.snapshot.transport_generation
                if lifecycle is not None
                else None
            ),
            detector=self._asr_detector,
            session=self._asr_session,
            provider=self._asr_provider,
            session_factory=self._asr_session_factory,
            transport_selection=self._asr_transport_selection,
            transport_task=self._asr_transport_task,
            ingress_token=ingress_token,
            turn_token=turn_token,
        )

    def _runtime_identity_matches(
        self,
        identity: _AsrRuntimeIdentity,
    ) -> bool:
        lifecycle = self._asr_lifecycle
        if (
            identity.start_generation != self._asr_start_generation
            or identity.session_epoch != self._asr_session_epoch
            or identity.audio_generation != self._asr_audio_generation
            or lifecycle is not identity.lifecycle
            or self._asr_detector is not identity.detector
            or self._asr_session is not identity.session
            or self._asr_provider != identity.provider
            or self._asr_session_factory is not identity.session_factory
            or self._asr_transport_selection is not identity.transport_selection
            or self._asr_transport_task is not identity.transport_task
        ):
            return False
        transport_generation = (
            lifecycle.snapshot.transport_generation if lifecycle is not None else None
        )
        if transport_generation != identity.transport_generation:
            return False
        if identity.ingress_token is not None and (
            self._asr_current_ingress_token != identity.ingress_token
            or not self._ingress_token_matches(identity.ingress_token)
        ):
            return False
        if identity.turn_token is not None and (
            lifecycle is None
            or identity.turn_token.ingress != identity.ingress_token
            or lifecycle.snapshot.turn_id != identity.turn_token.turn_id
        ):
            return False
        return True

    async def abort(self, reason: str) -> None:
        if reason == "ingress_backpressure":
            token = self._asr_current_ingress_token
            if token is not None and self._ingress_token_matches(token):
                await self._handle_audio_ingress_backpressure(token)
                return
        epoch = self._asr_session_epoch
        lifecycle = self._asr_lifecycle
        detector = self._asr_detector
        provider = self._asr_provider or "unknown"
        if lifecycle is not None:
            lifecycle.invalidate_audio()
        post_detach = await self._abort_transport(reason)
        if not self._runtime_identity_matches(
            post_detach
        ) or not self._asr_runtime_refs_match(epoch, lifecycle, detector):
            return
        if reason == "ingress_backpressure":
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                provider,
                session_epoch=epoch,
                expected_identity=post_detach,
            )
        if detector is not None:
            try:
                await detector.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed during voice abort",
                    self.display_name,
                )
            if not self._runtime_identity_matches(
                post_detach
            ) or not self._asr_runtime_refs_match(epoch, lifecycle, detector):
                return
        if lifecycle is not None:
            await self._send_asr_lifecycle_state(
                lifecycle.snapshot.state,
                provider=provider,
                session_epoch=epoch,
                expected_identity=post_detach,
            )

    async def wait_transcript_idle(self) -> None:
        await self._asr_transcript_dispatcher.wait_idle()

    def has_pending_transcript_delivery(self) -> bool:
        """Return whether an accepted final has not finished Core dispatch."""

        return self._asr_transcript_dispatcher.has_pending_delivery

    def _init_asr_runtime_state(self) -> None:
        self._asr_session = None
        self._asr_session_epoch = 0
        self._asr_start_generation = 0
        self._asr_provider = None
        self._asr_turn_prepared = False
        self._asr_final_lock = asyncio.Lock()
        self._asr_audio_bytes = 0
        self._asr_received_audio = False
        self._asr_close_tasks: set[asyncio.Task[None]] = set()
        self._asr_owned_cleanup_tasks: set[asyncio.Task[Any]] = set()
        self._asr_runtime_close_task: asyncio.Task[None] | None = None
        self._asr_lifecycle: VoiceInputLifecycleController | None = None
        self._asr_detector: DetectorRuntime | None = None
        self._asr_smart_turn_lease: SmartTurnLease | None = None
        self._asr_smart_turn_prepare_lock = asyncio.Lock()
        self._asr_smart_turn_prepare_scope: tuple[int, int, int] | None = None
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._asr_transport_task: asyncio.Task[None] | None = None
        self._asr_transport_lock = asyncio.Lock()
        self._asr_warm_expiry_task: asyncio.Task[None] | None = None
        self._asr_final_watchdog_task: asyncio.Task[None] | None = None
        self._asr_pending_speech_confirmed = False
        self._asr_pending_speech_onset_at = None
        self._asr_pending_detector_candidate = None
        self._asr_overlap_onset_token: VoiceIngressToken | None = None
        # 重叠发声的真实开口时刻。重放发生在「上一轮延迟 final 到达」之后，比用户
        # 实际开口晚得多；不把这一刻带过去，重放时取到的 onset 会把中间那段全算成
        # 「开口之后」，后继发声在重放前拍的帧就全被排除了。
        self._asr_overlap_onset_at: float | None = None
        self._asr_overlap_completed_token: VoiceIngressToken | None = None
        # 每张 credit 一个开口时刻：多个 onset+pause 周期可以在同一条延迟 final
        # 后面排队，用单个槽位会让所有重放共用最后那个时刻。
        self._asr_overlap_completed_onsets: deque[float] = deque()
        self._asr_overlap_completed_turns = 0
        self._asr_sealed_turn_token: VoiceTransportToken | None = None
        self._asr_provider_candidate_fence: ProviderCandidateFence | None = None
        self._asr_audio_sequence = 0
        self._asr_audio_generation = 0
        self._asr_current_ingress_token: VoiceIngressToken | None = None
        self._asr_partial_turn_token: VoiceTurnToken | None = None
        self._asr_accepted_final_keys: OrderedDict[FinalKey, None] = OrderedDict()
        self._asr_reserved_final_key: FinalKey | None = None
        self._speaker_verifier_factory: SpeakerShadowFactory | None = None
        self._speaker_verifier_activation_generation: str | None = None
        self._speaker_verifier_degraded = False
        self._speaker_verifier_lock = asyncio.Lock()
        self._asr_candidate_rejection: _CandidateRejectionSuppression | None = None
        self._asr_rejection_tasks: set[
            asyncio.Task[CandidateRejectionOutcome | None]
        ] = set()
        self._asr_rejection_watchdog_task: asyncio.Task[None] | None = None
        self._asr_transcript_dispatcher = TranscriptDispatcher(
            self._dispatch_asr_transcript_envelope,
        )
        self._asr_detector_dispatcher = AsrDetectorDispatcher(
            self._dispatch_asr_detector_event,
            on_failure=self._handle_asr_detector_dispatcher_failure,
        )
        self._asr_audio_dispatcher = AsrAudioDispatcher(
            validator=self._asr_audio_command_is_valid,
            on_wire_audio=self._record_asr_dispatcher_wire_audio,
            on_failure=self._handle_asr_audio_dispatcher_failure,
        )
        self._asr_last_provider_wire_audio_ms = 0
        self._asr_turn_audio_started_at: float | None = None
        # 语义上的「用户开口时刻」。与上面那个的区别是**打点位置**：这个钉在
        # SPEECH_CONFIRMED 转换那一行，不跨 _send_asr_lifecycle_state() 的投递
        # await；上面那个在两条路径上是投递完成之后才打的，喂延迟指标够用，但拿
        # 来当视觉所有权的起点会把投递窗口里拍的帧判成"不属于这段发声"。
        self._asr_turn_onset_at: float | None = None
        # 语音已经检测到、但 ASR session 还没就绪（要等重连）时先把这一刻记下来。
        # 真正 SPEECH_CONFIRMED 要等 connect() 成功之后才发得出去，用那时的时钟
        # 当"用户开口时刻"会把整段重连等待算进去，重连期间拍的帧全被判成不属于
        # 这段发声。
        self._asr_pending_speech_onset_at: float | None = None
        # 上一回合还在排空（DRAINING）时用户就接着说了：pending turn 的真实开口时刻
        # 是 mark_pending_turn_speech() 那一刻，不是后面 begin_pending_turn() 激活的
        # 时刻。lifecycle 硬要求 DRAINING 才能标记，所以这个值必然晚于上一轮封口。
        self._asr_pending_turn_onset_at: float | None = None
        self._asr_turn_endpointed_at: float | None = None
        # 与上面那个一样在封口时刻打点，但**不在 PROVIDER_FINAL 时清掉**。Core 要
        # 到 transcript 派发之后才冻结多模态回合，那时上面那个已经是 None 了；
        # 消费方靠"这个时刻是否晚于本回合起点"排除上一轮的残值。
        self._asr_last_turn_endpointed_at: float | None = None
        # 上面那个保留副本**属于哪一轮**。时间戳分不清"上一轮的封口"和"本轮的封
        # 口"：monotonic 在 Windows 上是 ~15ms 粒度，两者都可能与后继 record 的注
        # 册时刻相等，往任一个方向猜都会错（猜"归上一轮"会丢掉本轮自己的截止点，
        # 猜"归本轮"会把上一轮的封口盖到后继头上）。带上身份就不用猜。
        self._asr_last_turn_endpointed_key: str | None = None
        self._asr_first_partial_recorded = False
        self._voice_input_resource_optimization_enabled = True

    def _schedule_owned_cleanup(
        self,
        awaitable: Awaitable[Any],
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        """Keep teardown running when its caller is cancelled."""

        task = asyncio.create_task(awaitable, name=name)
        self._asr_owned_cleanup_tasks.add(task)
        task.add_done_callback(self._owned_cleanup_done)
        return task

    def _owned_cleanup_done(self, task: asyncio.Task[Any]) -> None:
        self._asr_owned_cleanup_tasks.discard(task)
        self._log_asr_background_task_failure(task)

    def _ensure_asr_runtime_state(self) -> None:
        # A number of focused unit tests intentionally construct the manager via
        # __new__. Keep those narrow lifecycle doubles compatible.
        if not hasattr(self, "_asr_session_epoch"):
            self._init_asr_runtime_state()
        elif not hasattr(self, "_asr_transcript_dispatcher"):
            self._asr_transcript_dispatcher = TranscriptDispatcher(
                self._dispatch_asr_transcript_envelope,
            )
        if not hasattr(self, "_asr_detector_dispatcher"):
            self._asr_detector_dispatcher = AsrDetectorDispatcher(
                self._dispatch_asr_detector_event,
                on_failure=self._handle_asr_detector_dispatcher_failure,
            )
        if not hasattr(self, "_asr_audio_dispatcher"):
            self._asr_audio_dispatcher = AsrAudioDispatcher(
                validator=self._asr_audio_command_is_valid,
                on_wire_audio=self._record_asr_dispatcher_wire_audio,
                on_failure=self._handle_asr_audio_dispatcher_failure,
            )
            self._asr_audio_sequence = 0
            self._asr_pending_detector_candidate = None
        if not hasattr(self, "_asr_overlap_onset_token"):
            self._asr_overlap_onset_token = None
        if not hasattr(self, "_asr_overlap_onset_at"):
            self._asr_overlap_onset_at = None
        if not hasattr(self, "_asr_overlap_completed_onsets"):
            self._asr_overlap_completed_onsets = deque()
        if not hasattr(self, "_asr_partial_turn_token"):
            self._asr_partial_turn_token = None
        if not hasattr(self, "_asr_overlap_completed_token"):
            self._asr_overlap_completed_token = None
            self._asr_overlap_completed_turns = 0
        if not hasattr(self, "_asr_start_generation"):
            self._asr_start_generation = 0
        if not hasattr(self, "_asr_provider_candidate_fence"):
            self._asr_provider_candidate_fence = None
        if not hasattr(self, "_asr_owned_cleanup_tasks"):
            self._asr_owned_cleanup_tasks = set()
        if not hasattr(self, "_asr_runtime_close_task"):
            self._asr_runtime_close_task = None
        if not hasattr(self, "_asr_smart_turn_prepare_lock"):
            self._asr_smart_turn_prepare_lock = asyncio.Lock()
            self._asr_smart_turn_prepare_scope = None
        if not hasattr(self, "_speaker_verifier_factory"):
            self._speaker_verifier_factory = None
            self._speaker_verifier_activation_generation = None
            self._speaker_verifier_degraded = False
        elif not hasattr(self, "_speaker_verifier_degraded"):
            self._speaker_verifier_degraded = False
        if not hasattr(self, "_speaker_verifier_lock"):
            self._speaker_verifier_lock = asyncio.Lock()
        if not hasattr(self, "_asr_candidate_rejection"):
            self._asr_candidate_rejection = None
        if not hasattr(self, "_asr_rejection_tasks"):
            self._asr_rejection_tasks = set()
            self._asr_rejection_watchdog_task = None

    def _capture_turn_token(
        self,
        lifecycle: VoiceInputLifecycleController,
    ) -> VoiceTurnToken:
        ingress_token = self._asr_current_ingress_token
        if ingress_token is None or not self._ingress_token_matches(ingress_token):
            raise RuntimeError("ASR_INGRESS_TOKEN_REQUIRED")
        return VoiceTurnToken(
            ingress=ingress_token,
            turn_id=lifecycle.snapshot.turn_id,
        )

    def _capture_transport_token(
        self,
        lifecycle: VoiceInputLifecycleController,
    ) -> VoiceTransportToken:
        return VoiceTransportToken(
            turn=self._capture_turn_token(lifecycle),
            transport_generation=lifecycle.snapshot.transport_generation,
        )

    def _ingress_token_matches(self, token: VoiceIngressToken) -> bool:
        return bool(
            token.session_epoch == self._asr_session_epoch
            and token.audio_generation == self._asr_audio_generation
        )

    def _transport_token_matches(
        self,
        token: VoiceTransportToken,
        lifecycle: VoiceInputLifecycleController,
    ) -> bool:
        snapshot = lifecycle.snapshot
        return bool(
            self._asr_lifecycle is lifecycle
            and self._ingress_token_matches(token.turn.ingress)
            and token.turn.turn_id == snapshot.turn_id
            and token.transport_generation == snapshot.transport_generation
        )

    def _accept_final_key(self, key: FinalKey) -> bool:
        if key in self._asr_accepted_final_keys:
            return False
        self._asr_accepted_final_keys[key] = None
        while len(self._asr_accepted_final_keys) > 256:
            self._asr_accepted_final_keys.popitem(last=False)
        return True

    def _asr_audio_command_is_valid(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
    ) -> bool:
        lifecycle = self._asr_lifecycle
        detector = self._asr_detector
        return bool(
            lifecycle is not None
            and detector is not None
            and self._asr_session is session_ref
            and self._ingress_token_matches(turn_token.ingress)
            and lifecycle.snapshot.turn_id == turn_token.turn_id
            and self._asr_endpointing_ready(lifecycle, detector, turn_token)
        )

    def _asr_endpointing_ready(
        self,
        lifecycle: VoiceInputLifecycleController,
        detector: DetectorRuntime | None,
        turn_token: VoiceTurnToken,
    ) -> bool:
        """Accept provider authority without manufacturing a SmartTurn lease."""

        if detector is None:
            return False
        if not _uses_smart_turn_endpointing(lifecycle.provider_policy):
            return True
        return detector.endpointing_ready(turn_token)

    async def _record_asr_dispatcher_wire_audio(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
        byte_count: int,
    ) -> None:
        if byte_count <= 0:
            return
        self._sync_provider_wire_metrics(
            session_ref,
            fallback_audio_bytes=byte_count,
        )
        if self._asr_session is session_ref:
            self._asr_received_audio = True
            self._asr_audio_bytes += byte_count
            lifecycle = self._asr_lifecycle
            if lifecycle is not None:
                lifecycle.metrics.provider_wire_sequence = (
                    self._asr_audio_dispatcher.provider_wire_sequence
                )
                lifecycle.metrics.asr_audio_command_queue_ms = (
                    self._asr_audio_dispatcher.asr_audio_command_queue_ms
                )

    async def _handle_asr_audio_dispatcher_failure(
        self,
        turn_token: VoiceTurnToken,
        error: BaseException,
    ) -> None:
        if not self._ingress_token_matches(turn_token.ingress):
            return
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        status_code = (
            "ASR_STREAM_BACKPRESSURE"
            if "BACKPRESSURE" in str(error)
            else "ASR_INDEPENDENT_STREAM_FAILED"
        )
        await self._handle_independent_asr_error(
            identity.session_epoch,
            identity.provider or "unknown",
            status_code=status_code,
            expected_identity=identity,
        )

    async def _handle_asr_detector_dispatcher_failure(
        self,
        envelope: CoreDetectorEventEnvelope,
        error: BaseException,
    ) -> None:
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        event = envelope.event
        if (
            envelope.session_epoch != self._asr_session_epoch
            or detector is not envelope.detector_ref
            or lifecycle is not envelope.lifecycle_ref
            or detector is None
            or lifecycle is None
            or event.ingress.detector_epoch != detector.detector_epoch
            or not self._ingress_token_matches(event.ingress.ingress_token)
        ):
            return
        logger.error(
            "[%s] detector event dispatcher failed epoch=%s",
            self.display_name,
            envelope.session_epoch,
            exc_info=(type(error), error, error.__traceback__),
        )
        identity = self._capture_runtime_identity(
            ingress_token=event.ingress.ingress_token,
        )
        await self._handle_independent_asr_error(
            identity.session_epoch,
            identity.provider or "unknown",
            status_code="ASR_ENDPOINTING_FAILED",
            expected_identity=identity,
        )

    def _detector_envelope_is_current(
        self,
        envelope: CoreDetectorEventEnvelope,
    ) -> bool:
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        event = envelope.event
        return bool(
            envelope.session_epoch == self._asr_session_epoch
            and detector is envelope.detector_ref
            and lifecycle is envelope.lifecycle_ref
            and detector is not None
            and lifecycle is not None
            and event.ingress.detector_epoch == detector.detector_epoch
            and self._ingress_token_matches(event.ingress.ingress_token)
        )

    async def _dispatch_asr_detector_event(
        self,
        envelope: CoreDetectorEventEnvelope,
    ) -> None:
        event = envelope.event
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        if not self._detector_envelope_is_current(envelope):
            stale_metrics = getattr(envelope.lifecycle_ref, "metrics", None)
            if stale_metrics is not None:
                stale_metrics.detector_stale_event_count += 1
            return
        assert detector is not None
        assert lifecycle is not None
        lifecycle.metrics.smart_turn_inference_ms = detector.smart_turn_evaluation_ms
        lifecycle.metrics.smart_turn_stale_result_count = (
            detector.smart_turn_stale_result_count
        )
        lifecycle.metrics.smart_turn_coalesced_evaluation_count = (
            detector.smart_turn_coalesced_evaluation_count
        )
        if isinstance(event, DetectorRuntimeEvent):
            identity = self._capture_runtime_identity(
                ingress_token=event.ingress.ingress_token,
            )
            await self._handle_independent_asr_error(
                envelope.session_epoch,
                identity.provider or "unknown",
                status_code=(
                    "ASR_INGRESS_BACKPRESSURE"
                    if event.kind == "audio_backpressure"
                    else "ASR_ENDPOINTING_FAILED"
                ),
                expected_identity=identity,
            )
            return
        if isinstance(event, DetectorTransportPrewarmEvent):
            await self._handle_transport_prewarm_event(
                event,
                detector,
                lifecycle,
                envelope.session_epoch,
            )
            return
        if isinstance(event, DetectorPrewarmEvent):
            await self._handle_detector_prewarm_event(
                event,
                detector,
                lifecycle,
                envelope.session_epoch,
            )
            return
        if isinstance(event, DetectorActivityEvent):
            await self._handle_independent_asr_activity(
                event.activity,
                envelope.session_epoch,
            )
            if not self._detector_envelope_is_current(envelope):
                return
            lifecycle = self._asr_lifecycle
            assert lifecycle is envelope.lifecycle_ref
            if event.activity not in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }:
                return
            if lifecycle.snapshot.state is VoiceLifecycleState.DRAINING:
                self._asr_pending_detector_candidate = event.candidate
                return
            if lifecycle.snapshot.state not in {
                VoiceLifecycleState.PREWARMING,
                VoiceLifecycleState.ACTIVE,
            }:
                return
            turn_token = self._capture_turn_token(lifecycle)
            bound = await detector.bind_candidate(event.candidate, turn_token)
            if bound is None:
                return
            if not self._detector_envelope_is_current(envelope):
                return
            if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
                self._activate_asr_audio_dispatcher(lifecycle, turn_token)
            return
        if not isinstance(event, DetectorTurnEvent):
            return
        turn_token = event.bound_turn.turn_token
        if (
            not self._ingress_token_matches(turn_token.ingress)
            or lifecycle.snapshot.turn_id != turn_token.turn_id
            or not detector.endpointing_ready(turn_token)
        ):
            return
        await self._handle_independent_asr_endpoint(envelope.session_epoch)
        if not self._detector_envelope_is_current(envelope):
            return
        session_ref = self._asr_session
        if session_ref is None:
            return
        if not self._asr_audio_dispatcher.seal(
            turn_token,
            session_ref,
            after_sequence=self._asr_audio_sequence,
        ):
            identity = self._capture_runtime_identity(
                ingress_token=turn_token.ingress,
                turn_token=turn_token,
            )
            await self._handle_independent_asr_error(
                envelope.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_AUDIO_ORDERING_FAILED",
                expected_identity=identity,
            )

    async def _handle_detector_prewarm_event(
        self,
        event: DetectorPrewarmEvent,
        detector: DetectorRuntime,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> None:
        """Prepare segmented endpointing and transport without final authority."""

        # 用户开口的时刻是**进这个处理函数**的时刻，不是底下 prewarm / transport
        # gather 跑完的时刻。视觉所有权拿 onset 当下界，晚打点会把整段 prewarm+
        # 重连等待算成「用户开口之后」，期间拍的帧全被判成不属于这段发声。
        detected_at = time.monotonic()

        def event_is_current() -> bool:
            return bool(
                epoch == self._asr_session_epoch
                and detector is self._asr_detector
                and lifecycle is self._asr_lifecycle
                and event.ingress.detector_epoch == detector.detector_epoch
                and self._ingress_token_matches(event.ingress.ingress_token)
            )

        if not event_is_current():
            return
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            if event.kind == "continuous":
                lifecycle.mark_pending_turn_speech()
                if self._asr_pending_turn_onset_at is None:
                    self._asr_pending_turn_onset_at = detected_at
                self._asr_pending_detector_candidate = event.candidate
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            warm_task = self._asr_warm_expiry_task
            if warm_task is not None:
                warm_task.cancel()
                self._asr_warm_expiry_task = None
            if state is VoiceLifecycleState.WARM_IDLE:
                lifecycle.metrics.warm_hit_count += 1
            lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
            prewarm_identity = self._capture_runtime_identity(
                ingress_token=event.ingress.ingress_token,
            )
            await self._send_asr_lifecycle_state(
                VoiceLifecycleState.PREWARMING,
                provider=prewarm_identity.provider or "unknown",
                session_epoch=prewarm_identity.session_epoch,
                expected_identity=prewarm_identity,
            )
            if not event_is_current():
                return
        if lifecycle.snapshot.state not in {
            VoiceLifecycleState.PREWARMING,
            VoiceLifecycleState.ACTIVE,
        }:
            return

        turn_token = self._capture_turn_token(lifecycle)
        bound = await detector.bind_candidate(event.candidate, turn_token)
        if bound is None or not event_is_current():
            return
        if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
            self._activate_asr_audio_dispatcher(lifecycle, turn_token)
            if event.kind == "continuous":
                await self._prepare_independent_asr_turn(epoch)
            return

        smart_turn_task = asyncio.create_task(
            self._ensure_smart_turn_ready(lifecycle, epoch),
            name="independent-asr-prewarm-smart-turn",
        )
        transport_task = asyncio.create_task(
            self._restart_transport(),
            name="independent-asr-prewarm-transport",
        )
        smart_turn_ready, _transport_result = await asyncio.gather(
            smart_turn_task,
            transport_task,
            return_exceptions=True,
        )
        if (
            smart_turn_ready is not True
            or not event_is_current()
            or lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING
        ):
            return
        if event.kind != "continuous":
            self._schedule_transport_warm_expiry(
                epoch,
                expected_state=VoiceLifecycleState.PREWARMING,
            )
            return
        session_ref = self._asr_session
        if session_ref is None or not getattr(session_ref, "is_ready", True):
            self._asr_pending_speech_confirmed = True
            if self._asr_pending_speech_onset_at is None:
                self._asr_pending_speech_onset_at = detected_at
            return
        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
        # session 先未就绪、随后又 ready 时，真实开口时刻是当初记下的那个
        # pending onset —— 直接用当前时钟会把整段重连等待算成「开口之后」，
        # 期间拍的帧全被排除在本回合外（CodeRabbit Major）。
        self._asr_turn_onset_at = (
            self._asr_pending_speech_onset_at
            if self._asr_pending_speech_onset_at is not None
            else detected_at
        )
        # 直接确认这一路同样要把待确认状态清干净：session 在标记 pending 之后
        # 才 ready 时，直接路径可能先完成确认，旧 flag / 旧 onset 会留到下一轮
        # 被复用（CodeRabbit Major）。
        self._asr_pending_speech_confirmed = False
        self._asr_pending_speech_onset_at = None
        active_identity = self._capture_runtime_identity(
            ingress_token=event.ingress.ingress_token,
        )
        await self._send_asr_lifecycle_state(
            VoiceLifecycleState.ACTIVE,
            provider=active_identity.provider or "unknown",
            session_epoch=active_identity.session_epoch,
            expected_identity=active_identity,
        )
        if not event_is_current():
            return
        self._asr_turn_audio_started_at = time.monotonic()
        self._asr_first_partial_recorded = False
        self._activate_asr_audio_dispatcher(lifecycle, turn_token)
        await self._prepare_independent_asr_turn(epoch)

    async def _handle_transport_prewarm_event(
        self,
        event: DetectorTransportPrewarmEvent,
        detector: DetectorRuntime,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> None:
        """Preconnect a streaming transport without opening a logical turn."""

        def event_is_current() -> bool:
            return bool(
                epoch == self._asr_session_epoch
                and detector is self._asr_detector
                and lifecycle is self._asr_lifecycle
                and event.ingress.detector_epoch == detector.detector_epoch
                and self._ingress_token_matches(event.ingress.ingress_token)
            )

        if not event_is_current():
            return
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            warm_task = self._asr_warm_expiry_task
            if warm_task is not None:
                warm_task.cancel()
                self._asr_warm_expiry_task = None
            if state is VoiceLifecycleState.WARM_IDLE:
                lifecycle.metrics.warm_hit_count += 1
            lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
            prewarm_identity = self._capture_runtime_identity(
                ingress_token=event.ingress.ingress_token,
            )
            await self._send_asr_lifecycle_state(
                VoiceLifecycleState.PREWARMING,
                provider=prewarm_identity.provider or "unknown",
                session_epoch=prewarm_identity.session_epoch,
                expected_identity=prewarm_identity,
            )
            if not event_is_current():
                return
        if lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING:
            return
        session_ref = self._asr_session
        if session_ref is None or not getattr(session_ref, "is_ready", True):
            await self._restart_transport()
        if (
            not event_is_current()
            or lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING
        ):
            return
        self._schedule_transport_warm_expiry(
            epoch,
            expected_state=VoiceLifecycleState.PREWARMING,
        )

    async def _ensure_continuous_provider_wake(
        self,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> bool:
        """Open a provider-owned streaming turn without fabricating VAD activity."""

        # 同 _handle_detector_prewarm_event：onset 取进函数的时刻，不取底下各段
        # await 跑完的时刻。
        detected_at = time.monotonic()
        detector = self._asr_detector
        ingress_token = self._asr_current_ingress_token

        def wake_is_current() -> bool:
            return bool(
                epoch == self._asr_session_epoch
                and lifecycle is self._asr_lifecycle
                and detector is self._asr_detector
                and ingress_token is not None
                and self._ingress_token_matches(ingress_token)
            )

        if not wake_is_current():
            return False
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            lifecycle.mark_pending_turn_speech()
            if self._asr_pending_turn_onset_at is None:
                self._asr_pending_turn_onset_at = detected_at
            return wake_is_current()
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            warm_task = self._asr_warm_expiry_task
            if warm_task is not None:
                warm_task.cancel()
                self._asr_warm_expiry_task = None
            if state is VoiceLifecycleState.WARM_IDLE:
                lifecycle.metrics.warm_hit_count += 1
            lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
            prewarm_identity = self._capture_runtime_identity(
                ingress_token=ingress_token,
            )
            delivered = await self._send_asr_lifecycle_state(
                VoiceLifecycleState.PREWARMING,
                provider=prewarm_identity.provider or "unknown",
                session_epoch=prewarm_identity.session_epoch,
                expected_identity=prewarm_identity,
            )
            if not delivered or not wake_is_current():
                return False
        if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
            return True
        if lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING:
            return False
        session_ref = self._asr_session
        if session_ref is None or not getattr(session_ref, "is_ready", True):
            self._asr_pending_speech_confirmed = True
            if self._asr_pending_speech_onset_at is None:
                self._asr_pending_speech_onset_at = detected_at
            self._ensure_transport_restart_task()
            return wake_is_current()
        turn_token = self._capture_turn_token(lifecycle)
        if not self._asr_endpointing_ready(lifecycle, detector, turn_token):
            identity = self._capture_runtime_identity(
                ingress_token=ingress_token,
                turn_token=turn_token,
            )
            await self._handle_independent_asr_error(
                epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
            )
            return False
        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
        # session 先未就绪、随后又 ready 时，真实开口时刻是当初记下的那个
        # pending onset —— 直接用当前时钟会把整段重连等待算成「开口之后」，
        # 期间拍的帧全被排除在本回合外（CodeRabbit Major）。
        self._asr_turn_onset_at = (
            self._asr_pending_speech_onset_at
            if self._asr_pending_speech_onset_at is not None
            else detected_at
        )
        self._asr_pending_speech_confirmed = False
        self._asr_pending_speech_onset_at = None
        active_identity = self._capture_runtime_identity(
            ingress_token=ingress_token,
            turn_token=turn_token,
        )
        delivered = await self._send_asr_lifecycle_state(
            VoiceLifecycleState.ACTIVE,
            provider=active_identity.provider or "unknown",
            session_epoch=active_identity.session_epoch,
            expected_identity=active_identity,
        )
        if not delivered or not wake_is_current():
            return False
        self._asr_turn_audio_started_at = time.monotonic()
        self._asr_first_partial_recorded = False
        await self._prepare_independent_asr_turn(epoch)
        if not wake_is_current():
            return False
        return self._activate_asr_audio_dispatcher(
            lifecycle,
            turn_token,
        )

    def _activate_asr_audio_dispatcher(
        self,
        lifecycle: VoiceInputLifecycleController,
        turn_token: VoiceTurnToken,
        *,
        buffered_pcm16: bytes | None = None,
    ) -> bool:
        detector = self._asr_detector
        session_ref = self._asr_session
        if (
            session_ref is None
            or detector is None
            or not getattr(session_ref, "is_ready", True)
            or not self._asr_endpointing_ready(lifecycle, detector, turn_token)
        ):
            return False
        if self._asr_audio_dispatcher.active_turn == turn_token:
            return True
        self._asr_audio_sequence = 0
        payload = (
            lifecycle.drain_active_start_audio()
            if buffered_pcm16 is None
            else buffered_pcm16
        )
        activated = self._asr_audio_dispatcher.activate(
            turn_token,
            session_ref,
            payload,
            sample_rate_hz=16_000,
        )
        if activated:
            self._observe_provider_speaker_shadow(
                detector,
                payload,
                sample_rate_hz=16_000,
            )
        return activated

    @staticmethod
    def _observe_provider_speaker_shadow(
        detector: DetectorRuntime,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> None:
        try:
            detector.observe_provider_audio(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )
        except Exception:
            # Observation never participates in ASR acceptance or failure.
            return

    async def _ensure_smart_turn_ready(
        self,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> bool:
        if epoch != self._asr_session_epoch or self._asr_lifecycle is not lifecycle:
            return False
        if not _uses_smart_turn_endpointing(lifecycle.provider_policy):
            return True
        turn_token = self._capture_turn_token(lifecycle)
        detector = self._asr_detector
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        if detector is None:
            await self._handle_independent_asr_error(
                epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
            )
            return False
        prepare_scope = (epoch, id(lifecycle), id(detector))
        if self._asr_smart_turn_prepare_scope != prepare_scope:
            self._asr_smart_turn_prepare_scope = prepare_scope
            self._asr_smart_turn_prepare_lock = asyncio.Lock()
        prepare_lock = self._asr_smart_turn_prepare_lock
        async with prepare_lock:
            if not self._runtime_identity_matches(identity):
                return False
            return await self._ensure_smart_turn_ready_for_identity(
                detector,
                turn_token,
                identity,
                epoch=epoch,
            )

    async def _ensure_smart_turn_ready_for_identity(
        self,
        detector: DetectorRuntime,
        turn_token: VoiceTurnToken,
        identity: _AsrRuntimeIdentity,
        *,
        epoch: int,
    ) -> bool:
        lease = self._asr_smart_turn_lease
        if (
            lease is not None
            and lease.token == turn_token
            and detector.endpointing_ready(turn_token)
        ):
            return True
        if lease is not None:
            await lease.release()
            if self._asr_smart_turn_lease is not lease:
                return False
            self._asr_smart_turn_lease = None
            if not self._runtime_identity_matches(identity):
                return False
        lease = await detector.prepare_endpointing(turn_token)
        if (
            not self._runtime_identity_matches(identity)
            or self._asr_smart_turn_lease is not None
        ):
            if lease is not None:
                await lease.release()
            return False
        if lease is None or not detector.endpointing_ready(turn_token):
            if lease is not None:
                await lease.release()
                if not self._runtime_identity_matches(identity):
                    return False
            await self._handle_independent_asr_error(
                epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
            )
            return False
        self._asr_smart_turn_lease = lease
        return True

    async def _handle_audio_ingress_backpressure(
        self,
        token: VoiceIngressToken,
        *,
        observed_state: VoiceLifecycleState | None = None,
    ) -> None:
        """Invalidate a whole candidate/turn instead of dropping middle PCM."""

        lifecycle = self._asr_lifecycle
        if lifecycle is None or not self._ingress_token_matches(token):
            return
        epoch = self._asr_session_epoch
        detector = self._asr_detector
        provider = self._asr_provider or "unknown"
        state = observed_state or lifecycle.snapshot.state
        if (
            state is VoiceLifecycleState.DRAINING
            and not _uses_smart_turn_endpointing(lifecycle.provider_policy)
        ):
            discard_failed = False
            discard_handled = False
            final_completed_before_discard = False
            async with self._asr_final_lock:
                if (
                    self._asr_lifecycle is not lifecycle
                    or self._asr_detector is not detector
                    or epoch != self._asr_session_epoch
                    or not self._ingress_token_matches(token)
                ):
                    return
                state = lifecycle.snapshot.state
                lifecycle.discard_pending_turn()
                self._asr_pending_turn_onset_at = None
                self._asr_pending_speech_confirmed = False
                self._asr_pending_speech_onset_at = None
                self._asr_pending_detector_candidate = None
                if state is VoiceLifecycleState.DRAINING:
                    sealed_token = self._asr_sealed_turn_token
                    provider_fence = self._asr_provider_candidate_fence
                    if (
                        detector is None
                        or sealed_token is None
                        or provider_fence is None
                        or not self._transport_token_matches(
                            sealed_token,
                            lifecycle,
                        )
                    ):
                        discard_failed = True
                    else:
                        try:
                            discard_handled = (
                                await detector.discard_provider_successor(
                                    provider_fence
                                )
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.warning(
                                "[%s] provider successor discard failed",
                                self.display_name,
                            )
                        discard_failed = not discard_handled
                elif state is VoiceLifecycleState.WARM_IDLE:
                    final_completed_before_discard = True
            if discard_failed:
                identity = self._capture_runtime_identity(ingress_token=token)
                await self._handle_independent_asr_error(
                    epoch,
                    provider,
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )
                return
            if discard_handled:
                identity = self._capture_runtime_identity(ingress_token=token)
                await self._send_asr_status(
                    "ASR_INGRESS_BACKPRESSURE",
                    provider,
                    session_epoch=epoch,
                    expected_identity=identity,
                )
                return
            if final_completed_before_discard:
                if detector is not None and detector is self._asr_detector:
                    try:
                        await detector.reset()
                    except Exception:
                        logger.warning(
                            "[%s] detector reset failed after pending overflow",
                            self.display_name,
                        )
                identity = self._capture_runtime_identity(ingress_token=token)
                await self._send_asr_status(
                    "ASR_INGRESS_BACKPRESSURE",
                    provider,
                    session_epoch=epoch,
                    expected_identity=identity,
                )
                return
            if state is VoiceLifecycleState.ACTIVE:
                await self._asr_transcript_dispatcher.wait_idle()
        if state is VoiceLifecycleState.DRAINING:
            lifecycle.discard_pending_turn()
            self._asr_pending_turn_onset_at = None
            self._asr_pending_speech_confirmed = False
            self._asr_pending_speech_onset_at = None
            self._asr_pending_detector_candidate = None
            if detector is not None:
                identity = self._capture_runtime_identity(ingress_token=token)
                await detector.reset()
                if not self._runtime_identity_matches(
                    identity
                ) or not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle,
                    detector,
                ):
                    return
            identity = self._capture_runtime_identity(ingress_token=token)
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                provider,
                session_epoch=epoch,
                expected_identity=identity,
            )
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            self._asr_audio_generation += 1
            lifecycle.invalidate_audio()
            if detector is not None:
                identity = self._capture_runtime_identity()
                try:
                    await detector.reset()
                except Exception:
                    logger.warning(
                        "[%s] detector reset failed after ingress backpressure",
                        self.display_name,
                    )
                if not self._runtime_identity_matches(
                    identity
                ) or not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle,
                    detector,
                ):
                    return
            identity = self._capture_runtime_identity()
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                provider,
                session_epoch=epoch,
                expected_identity=identity,
            )
            return
        if state in {
            VoiceLifecycleState.PREWARMING,
            VoiceLifecycleState.BACKOFF,
            VoiceLifecycleState.ACTIVE,
        }:
            abandoned_turn = (
                self._capture_turn_token(lifecycle)
                if state is VoiceLifecycleState.ACTIVE and self._asr_turn_prepared
                else None
            )
            try:
                lifecycle.invalidate_audio()
                post_detach = await self._abort_transport(
                    "detector_audio_backpressure"
                )
                if not self._runtime_identity_matches(
                    post_detach
                ) or not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle,
                    detector,
                ):
                    return
                if detector is not None:
                    await detector.reset()
                    if not self._runtime_identity_matches(
                        post_detach
                    ) or not self._asr_runtime_refs_match(
                        epoch,
                        lifecycle,
                        detector,
                    ):
                        return
                await self._send_asr_status(
                    "ASR_INGRESS_BACKPRESSURE",
                    provider,
                    session_epoch=epoch,
                    expected_identity=post_detach,
                )
                if not self._runtime_identity_matches(post_detach):
                    return
                await self._send_asr_lifecycle_state(
                    VoiceLifecycleState.LOCAL_LISTEN,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=post_detach,
                )
                return
            finally:
                if abandoned_turn is not None:
                    await self._notify_asr_turn_abandoned(abandoned_turn)
        identity = self._capture_runtime_identity()
        await self._send_asr_status(
            "ASR_INGRESS_BACKPRESSURE",
            provider,
            session_epoch=epoch,
            expected_identity=identity,
        )

    async def start(
        self,
        *,
        route_key: str,
        resource_optimization_enabled: bool,
        user_language: str | None = None,
        speaker_shadow_factory: SpeakerShadowFactory | None = None,
    ) -> AsrStartResult:
        """Resolve and start one independent-ASR route.

        ``user_language`` is the caller's normalized language preference; the
        session factory maps it onto each provider's accepted hints and falls
        back to automatic detection when it is unknown or unsupported.
        """

        self._ensure_asr_runtime_state()
        # A new start owns a new runtime generation. Any predecessor close task
        # still owns only the resources it already detached; future close calls
        # must target this start instead of re-awaiting that retired teardown.
        self._asr_runtime_close_task = None
        operation_generation = self._begin_asr_start_operation()
        cleanup = self._detach_independent_asr(
            operation_generation=operation_generation,
        )
        if cleanup is not None:
            cleanup_task = self._schedule_owned_cleanup(
                cleanup,
                name="independent-asr-start-predecessor-close",
            )
            await asyncio.shield(cleanup_task)
        if not self._asr_start_operation_matches(operation_generation):
            return AsrStartResult(
                AsrStartStatus.FAILED,
                failure_code="ASR_START_STALE",
            )
        epoch = self._asr_session_epoch
        audio_generation = self._asr_audio_generation

        def operation_is_current() -> bool:
            return bool(
                self._asr_start_operation_matches(operation_generation)
                and epoch == self._asr_session_epoch
                and audio_generation == self._asr_audio_generation
            )

        def stale_result(provider: str | None = None) -> AsrStartResult:
            return AsrStartResult(
                AsrStartStatus.FAILED,
                provider=provider,
                failure_code="ASR_START_STALE",
                session_epoch=epoch,
            )

        self._asr_audio_bytes = 0
        self._voice_input_resource_optimization_enabled = bool(
            resource_optimization_enabled
        )
        core_type = str(route_key or "").strip().lower()

        try:
            # The resolver reads core config synchronously from disk; keep
            # that blocking read off the event loop.
            selection = await asyncio.to_thread(_resolve_asr_selection, core_type)
            selected_provider = getattr(selection, "provider_key", None)
            if not isinstance(selected_provider, str) or not selected_provider.strip():
                raise ValueError("invalid ASR provider selection")
            provider = selected_provider.strip().lower()
            endpointing_mode = getattr(selection, "endpointing_mode", None)
            if endpointing_mode not in {"manual", "provider"}:
                raise ValueError("invalid ASR endpointing selection")
            availability = getattr(
                selection,
                "availability",
                AsrProviderAvailability.IMPLEMENTED,
            )
            if availability is not AsrProviderAvailability.IMPLEMENTED:
                if not operation_is_current():
                    return stale_result(provider)
                failure_code = "ASR_INDEPENDENT_UNAVAILABLE"
                status_identity = self._capture_runtime_identity()
                delivered = await self._send_asr_status(
                    failure_code,
                    provider,
                    session_epoch=epoch,
                    expected_identity=status_identity,
                )
                if not delivered or not operation_is_current():
                    return stale_result(provider)
                return AsrStartResult(
                    AsrStartStatus.UNAVAILABLE,
                    provider=provider,
                    failure_code=failure_code,
                    session_epoch=epoch,
                )
            policy = resolve_provider_policy(provider, endpointing_mode)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Configuration errors must not abort the already-started Core
            # session. Keep the microphone fail-closed and report only the
            # fixed status code/provider category.
            if not operation_is_current():
                return stale_result()
            self._asr_session = None
            self._asr_provider = None
            failure_code = "ASR_INDEPENDENT_FAILED"
            status_identity = self._capture_runtime_identity()
            delivered = await self._send_asr_status(
                failure_code,
                core_type or "unknown",
                session_epoch=epoch,
                expected_identity=status_identity,
            )
            if not delivered or not operation_is_current():
                return stale_result()
            return AsrStartResult(
                AsrStartStatus.FAILED,
                failure_code=failure_code,
                session_epoch=epoch,
            )

        # Provider selection is immutable for this session epoch. Expose the
        # selected provider during connect retries, then clear it only if the
        # startup attempt ultimately fails.
        if not operation_is_current():
            return stale_result(provider)
        self._asr_provider = provider

        def create_candidate(candidate_selection: Any) -> Any:
            """Create one startup candidate with callbacks bound to its identity."""

            candidate_provider = candidate_selection.provider_key
            candidate_endpointing = candidate_selection.endpointing_mode
            candidate_policy = resolve_provider_policy(
                candidate_provider,
                candidate_endpointing,
            )
            candidate_session = None

            def is_adopted_candidate() -> bool:
                return (
                    candidate_session is not None
                    and self._asr_session is candidate_session
                    and epoch == self._asr_session_epoch
                )

            async def on_final(text: str) -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_final(
                    text, epoch, candidate_provider
                )

            async def on_error(_message: str) -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_error(epoch, candidate_provider)

            async def on_status(_message: str) -> None:
                # Provider status strings are intentionally not forwarded verbatim.
                return None

            async def on_activity(event: SpeechActivityEvent) -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_activity(event, epoch)

            async def on_endpoint() -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_endpoint(epoch)

            async def on_partial(text: str) -> None:
                if not is_adopted_candidate():
                    return
                await self._send_independent_asr_preview(text, epoch)

            candidate_session = _create_asr_session_from_selection(
                core_type,
                selection=candidate_selection,
                on_input_transcript=on_final,
                on_connection_error=on_error,
                on_status_message=on_status,
                on_speech_activity=on_activity,
                on_turn_endpointed=on_endpoint,
                external_endpointing_runtime=(
                    _uses_smart_turn_endpointing(candidate_policy)
                ),
                user_language=user_language,
            )
            _attach_partial_callback(candidate_session, on_partial)
            return candidate_session

        asr_session = None
        detector_ref: DetectorRuntime | None = None
        connect_started_at = time.monotonic()
        try:
            max_attempts = policy.connect_max_attempts
            for attempt in range(max_attempts):
                if not operation_is_current():
                    return stale_result(provider)
                asr_session = create_candidate(selection)
                try:
                    await asr_session.connect()
                    if not operation_is_current():
                        await self._close_asr_session(asr_session)
                        asr_session = None
                        return stale_result(provider)
                    break
                except asyncio.CancelledError:
                    try:
                        await asr_session.close()
                    except Exception:
                        pass
                    asr_session = None
                    raise
                except Exception:
                    try:
                        await asr_session.close()
                    except Exception:
                        pass
                    asr_session = None
                    if not operation_is_current():
                        return stale_result(provider)
                    if attempt + 1 >= max_attempts:
                        raise
                    backoff = min(
                        policy.connect_retry_cap_seconds,
                        policy.connect_retry_base_seconds * (2**attempt),
                    )
                    # Aggregate retry budget (Codex P1). Each attempt can burn
                    # _READY_TIMEOUT_SECONDS before ASR_CONNECT_TIMEOUT, and
                    # _start_session_activate awaits this whole loop before it
                    # sends session_started -- while the frontend cancels the
                    # start and fires end_session at
                    # _FRONTEND_START_DEADLINE_SECONDS. So on a sustained
                    # provider outage a second attempt could not finish in time
                    # no matter what: the frontend always tore the session down
                    # mid-retry, and the user saw a generic start timeout
                    # instead of the fail-closed ASR verdict this code exists to
                    # produce. Only start another attempt when its worst case
                    # still fits.
                    elapsed = time.monotonic() - connect_started_at
                    if (
                        elapsed + backoff + _READY_TIMEOUT_SECONDS
                        > _CONNECT_TOTAL_BUDGET_SECONDS
                    ):
                        logger.warning(
                            "[asr] connect retry budget exhausted after %.1fs "
                            "(provider=%s attempt=%d/%d); failing closed so the "
                            "verdict reaches the client before its start deadline",
                            elapsed,
                            provider,
                            attempt + 1,
                            max_attempts,
                        )
                        raise
                    await asyncio.sleep(backoff)
                    if not operation_is_current():
                        return stale_result(provider)
            if asr_session is None:
                raise RuntimeError("ASR_CONNECT_FAILED")
            if not operation_is_current():
                await self._close_asr_session(asr_session)
                return stale_result(provider)
            self._asr_session = asr_session
            self._asr_last_provider_wire_audio_ms = 0
            self._asr_provider = provider
            self._asr_lifecycle = VoiceInputLifecycleController(
                provider_policy=policy,
                shadow_mode=False,
                resource_optimization_enabled=(
                    self._voice_input_resource_optimization_enabled
                ),
            )
            self._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
            self._asr_lifecycle.metrics.connect_latency_ms = int(
                (time.monotonic() - connect_started_at) * 1_000
            )
            lifecycle_ref = self._asr_lifecycle

            async def on_detector_endpointing_failure() -> None:
                if not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle_ref,
                    detector_ref,
                ):
                    return
                identity = self._capture_runtime_identity(
                    ingress_token=self._asr_current_ingress_token,
                )
                await self._handle_independent_asr_error(
                    epoch,
                    provider,
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )

            async def on_detector_event(event) -> None:
                current_lifecycle_ref = self._asr_lifecycle
                if (
                    detector_ref is None
                    or current_lifecycle_ref is None
                    or epoch != self._asr_session_epoch
                ):
                    return
                accepted = self._asr_detector_dispatcher.submit_nowait(
                    CoreDetectorEventEnvelope(
                        event=event,
                        detector_ref=detector_ref,
                        lifecycle_ref=current_lifecycle_ref,
                        session_epoch=epoch,
                    )
                )
                if not accepted:
                    raise RuntimeError("ASR_DETECTOR_CONTROL_BACKPRESSURE")

            async with self._speaker_verifier_lock:
                current_factory = (
                    speaker_shadow_factory
                    if self._speaker_verifier_activation_generation is None
                    else self._speaker_verifier_factory
                )
                speaker_shadow = self._create_speaker_shadow(current_factory)
                try:
                    detector_ref = DetectorRuntime(
                        resource_optimization_enabled=(
                            self._voice_input_resource_optimization_enabled
                        ),
                        provider_policy=policy,
                        on_endpointing_failure=(
                            on_detector_endpointing_failure
                            if _uses_smart_turn_endpointing(policy)
                            else None
                        ),
                        on_event=on_detector_event,
                        speaker_shadow=speaker_shadow,
                    )
                except Exception:
                    await self._close_created_speaker_shadow(speaker_shadow)
                    raise
                self._asr_detector = detector_ref
            self._asr_session_factory = create_candidate
            self._asr_transport_selection = selection
            self._schedule_transport_warm_expiry(
                epoch,
                expected_state=VoiceLifecycleState.LOCAL_LISTEN,
            )
            start_identity = self._capture_runtime_identity()
            delivered = await self._send_asr_lifecycle_state(
                VoiceLifecycleState.LOCAL_LISTEN,
                provider=provider,
                session_epoch=epoch,
                expected_identity=start_identity,
            )
            if (
                not delivered
                or not operation_is_current()
                or not self._runtime_identity_matches(start_identity)
            ):
                return stale_result(provider)
            delivered = await self._send_asr_status(
                "ASR_INDEPENDENT_READY",
                provider,
                session_epoch=epoch,
                expected_identity=start_identity,
            )
            if (
                not delivered
                or not operation_is_current()
                or not self._runtime_identity_matches(start_identity)
            ):
                return stale_result(provider)
            return AsrStartResult(
                AsrStartStatus.READY,
                provider=provider,
                session_epoch=epoch,
            )
        except asyncio.CancelledError:
            if detector_ref is not None and self._asr_detector is detector_ref:
                self._asr_detector = None
                try:
                    await detector_ref.close()
                except Exception:
                    pass
            if asr_session is not None:
                await self._close_asr_session(asr_session)
            raise
        except Exception:
            if detector_ref is not None and self._asr_detector is detector_ref:
                self._asr_detector = None
                try:
                    await detector_ref.close()
                except Exception:
                    pass
            if asr_session is not None:
                await self._close_asr_session(asr_session)
            if operation_is_current():
                self._asr_session = None
                self._asr_provider = None
                failure_code = (
                    "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE"
                    if policy.connect_max_attempts > 1
                    else "ASR_INDEPENDENT_FAILED"
                )
                failure_identity = self._capture_runtime_identity()
                delivered = await self._send_asr_status(
                    failure_code,
                    provider,
                    session_epoch=epoch,
                    expected_identity=failure_identity,
                )
                if not delivered or not operation_is_current():
                    return stale_result(provider)
                return AsrStartResult(
                    AsrStartStatus.UNAVAILABLE
                    if policy.connect_max_attempts > 1
                    else AsrStartStatus.FAILED,
                    provider=provider,
                    failure_code=failure_code,
                    session_epoch=epoch,
                )
            return stale_result(provider)

    def _create_speaker_shadow(
        self,
        factory: SpeakerShadowFactory | None,
    ) -> SpeakerShadowObserver | None:
        """Construct one lightweight observer without risking ASR startup."""

        if factory is None:
            return None
        try:
            # Model/process creation remains lazy inside the observer's first
            # accepted submission.
            shadow = factory()
        except Exception:
            self._speaker_verifier_degraded = True
            logger.warning(
                "[%s] speaker shadow factory failed; continuing without observer",
                self.display_name,
            )
            return None
        if shadow is None:
            self._speaker_verifier_degraded = True
            return None
        return shadow

    @staticmethod
    async def _close_created_speaker_shadow(
        shadow: SpeakerShadowObserver | None,
    ) -> None:
        if shadow is None:
            return
        try:
            await shadow.close()
        except Exception:
            return

    async def _reject_speaker_candidate(
        self,
        shadow_candidate: SpeakerShadowCandidateKey,
        *,
        activation_generation: str,
    ) -> CandidateRejectionOutcome:
        """Prepare authority asynchronously, then commit without awaiting."""

        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        if (
            detector is None
            or lifecycle is None
            or activation_generation
            != self._speaker_verifier_activation_generation
        ):
            return CandidateRejectionOutcome.STALE
        initial_snapshot = lifecycle.snapshot
        initial_session_epoch = self._asr_session_epoch
        initial_audio_generation = self._asr_audio_generation
        try:
            lease = await detector.prepare_candidate_rejection(shadow_candidate)
        except asyncio.CancelledError:
            raise
        except Exception:
            return CandidateRejectionOutcome.STALE
        if lease is None:
            return CandidateRejectionOutcome.STALE

        request = CandidateRejectionRequest(
            session_epoch=initial_session_epoch,
            audio_generation=initial_audio_generation,
            transport_generation=initial_snapshot.transport_generation,
            turn_id=initial_snapshot.turn_id,
            candidate=lease.candidate,
            activation_generation=activation_generation,
        )

        asr_session: Any = None
        smart_turn_lease: SmartTurnLease | None = None
        suppression: _CandidateRejectionSuppression | None = None
        async with self._asr_final_lock:
            lifecycle = self._asr_lifecycle
            detector = self._asr_detector
            if (
                request.session_epoch != self._asr_session_epoch
                or request.audio_generation != self._asr_audio_generation
                or request.activation_generation
                != self._speaker_verifier_activation_generation
                or lifecycle is None
                or detector is None
                or not lease.belongs_to(detector)
                or self._asr_session is None
                or self._asr_candidate_rejection is not None
            ):
                return CandidateRejectionOutcome.STALE
            snapshot = lifecycle.snapshot
            turn_token = lease.turn_token
            final_key = FinalKey.from_turn(turn_token)
            if (
                request.transport_generation != snapshot.transport_generation
                or request.turn_id != snapshot.turn_id
                or snapshot.state is not VoiceLifecycleState.ACTIVE
                or self._asr_sealed_turn_token is not None
                or self._asr_provider_candidate_fence is not None
                or self._asr_partial_turn_token != turn_token
                or not self._ingress_token_matches(turn_token.ingress)
                or not self._asr_turn_prepared
                or self._asr_audio_dispatcher.active_turn != turn_token
                or self._asr_reserved_final_key != final_key
                or final_key in self._asr_accepted_final_keys
            ):
                return CandidateRejectionOutcome.STALE
            smart_turn_lease = self._asr_smart_turn_lease
            if (
                smart_turn_lease is not None
                and smart_turn_lease.token != turn_token
            ):
                return CandidateRejectionOutcome.STALE
            if not lease.commit():
                return CandidateRejectionOutcome.STALE

            self._asr_transcript_dispatcher.release(final_key)
            self._asr_reserved_final_key = None
            lifecycle.invalidate_transport()
            self._asr_audio_dispatcher.abort(turn_token)
            asr_session, self._asr_session = self._asr_session, None
            if smart_turn_lease is not None:
                self._asr_smart_turn_lease = None
            self._asr_turn_prepared = False
            self._asr_received_audio = False
            self._asr_audio_sequence = 0
            self._asr_partial_turn_token = None
            self._asr_sealed_turn_token = None
            self._asr_provider_candidate_fence = None
            self._asr_turn_endpointed_at = None
            final_watchdog = self._asr_final_watchdog_task
            self._asr_final_watchdog_task = None
            if (
                final_watchdog is not None
                and final_watchdog is not asyncio.current_task()
            ):
                final_watchdog.cancel()
            suppression = _CandidateRejectionSuppression(
                request=request,
                turn_token=turn_token,
                final_key=final_key,
                lifecycle=lifecycle,
                detector=detector,
            )
            self._asr_candidate_rejection = suppression
            self._schedule_candidate_rejection_watchdog(suppression)

        cleanup_degraded = False
        try:
            if smart_turn_lease is not None:
                try:
                    await smart_turn_lease.release()
                except Exception:
                    cleanup_degraded = True
            assert asr_session is not None
            try:
                await asr_session.close()
            except Exception:
                cleanup_degraded = True
            async with self._asr_final_lock:
                if self._asr_candidate_rejection is not suppression:
                    return CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
            try:
                await detector.reset()
            except Exception:
                # Keep suppression until the bounded watchdog detaches the
                # verifier and retries recovery. The rejected text remains
                # dropped; only cleanup quality is degraded.
                return CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
            await self._complete_candidate_rejection(suppression)
        except asyncio.CancelledError:
            await asyncio.shield(self._complete_candidate_rejection(suppression))
            raise
        return (
            CandidateRejectionOutcome.APPLIED_CLEANUP_DEGRADED
            if cleanup_degraded
            else CandidateRejectionOutcome.APPLIED
        )

    def _schedule_candidate_rejection_watchdog(
        self,
        suppression: _CandidateRejectionSuppression,
    ) -> None:
        old_watchdog = self._asr_rejection_watchdog_task
        if old_watchdog is not None and old_watchdog is not asyncio.current_task():
            old_watchdog.cancel()

        async def recover() -> None:
            current = asyncio.current_task()

            async def close_shadow(shadow: SpeakerShadowObserver | None) -> None:
                try:
                    await asyncio.wait_for(
                        self._close_created_speaker_shadow(shadow),
                        timeout=_CANDIDATE_REJECTION_RECOVERY_STEP_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    if current is not None and current.cancelling():
                        raise
                except Exception:
                    pass

            try:
                await asyncio.sleep(_CANDIDATE_REJECTION_WATCHDOG_SECONDS)
                if self._asr_candidate_rejection is not suppression:
                    return
                async with self._speaker_verifier_lock:
                    try:
                        await asyncio.wait_for(
                            suppression.detector.replace_speaker_verifier(None),
                            timeout=(
                                _CANDIDATE_REJECTION_RECOVERY_STEP_TIMEOUT_SECONDS
                            ),
                        )
                    except asyncio.CancelledError:
                        if current is not None and current.cancelling():
                            raise
                        logger.warning(
                            "[%s] rejection watchdog verifier detach was "
                            "cancelled by the detector",
                            self.display_name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[%s] rejection watchdog verifier detach failed: %s",
                            self.display_name,
                            exc,
                        )
                    reset_succeeded = False
                    try:
                        await asyncio.wait_for(
                            suppression.detector.reset(),
                            timeout=(
                                _CANDIDATE_REJECTION_RECOVERY_STEP_TIMEOUT_SECONDS
                            ),
                        )
                        reset_succeeded = True
                    except asyncio.CancelledError:
                        if current is not None and current.cancelling():
                            raise
                        logger.warning(
                            "[%s] rejection watchdog reset was cancelled by "
                            "the detector; speaker verification stays detached",
                            self.display_name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[%s] rejection watchdog reset failed; "
                            "speaker verification stays detached: %s",
                            self.display_name,
                            exc,
                        )
                    factory = self._speaker_verifier_factory
                    if (
                        reset_succeeded
                        and factory is not None
                        and self._asr_detector is suppression.detector
                    ):
                        reinstalled = False
                        for _attempt in range(
                            _CANDIDATE_REJECTION_REINSTALL_ATTEMPTS
                        ):
                            shadow = self._create_speaker_shadow(factory)
                            if shadow is None:
                                break
                            try:
                                await asyncio.wait_for(
                                    suppression.detector.replace_speaker_verifier(
                                        shadow
                                    ),
                                    timeout=(
                                        _CANDIDATE_REJECTION_RECOVERY_STEP_TIMEOUT_SECONDS
                                    ),
                                )
                            except asyncio.CancelledError:
                                if current is not None and current.cancelling():
                                    raise
                                await close_shadow(shadow)
                            except Exception as exc:
                                logger.warning(
                                    "[%s] rejection watchdog verifier reinstall "
                                    "failed: %s",
                                    self.display_name,
                                    exc,
                                )
                                await close_shadow(shadow)
                            else:
                                reinstalled = True
                                break
                            await asyncio.sleep(0)
                        self._speaker_verifier_degraded = not reinstalled
                    elif (
                        factory is not None
                        and self._asr_detector is suppression.detector
                    ):
                        self._speaker_verifier_degraded = True
                await self._complete_candidate_rejection(suppression)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(
            recover(),
            name="owner-voice-rejection-watchdog",
        )
        self._asr_rejection_watchdog_task = task
        self._asr_rejection_tasks.add(task)
        task.add_done_callback(self._reap_rejection_task)

    async def _complete_candidate_rejection(
        self,
        suppression: _CandidateRejectionSuppression,
    ) -> bool:
        should_restart = False
        async with self._asr_final_lock:
            if self._asr_candidate_rejection is not suppression:
                return False
            self._asr_candidate_rejection = None
            watchdog = self._asr_rejection_watchdog_task
            self._asr_rejection_watchdog_task = None
            if watchdog is not None and watchdog is not asyncio.current_task():
                watchdog.cancel()
            if (
                suppression.request.session_epoch == self._asr_session_epoch
                and suppression.request.audio_generation
                == self._asr_audio_generation
                and self._asr_lifecycle is suppression.lifecycle
                and self._asr_detector is suppression.detector
            ):
                suppression.lifecycle.invalidate_audio()
                should_restart = True
        await self._notify_asr_turn_abandoned(suppression.turn_token)
        if should_restart:
            self._ensure_transport_restart_task()
        return True

    def _reset_asr_turn_state(self) -> None:
        """Reset per-turn bookkeeping shared by close/abort/error teardown."""

        self._asr_turn_prepared = False
        self._asr_received_audio = False
        self._asr_pending_speech_confirmed = False
        self._asr_pending_speech_onset_at = None
        self._asr_pending_detector_candidate = None
        self._asr_overlap_onset_token = None
        self._asr_overlap_onset_at = None
        self._asr_overlap_completed_token = None
        self._asr_overlap_completed_onsets.clear()
        self._asr_overlap_completed_turns = 0
        self._asr_audio_sequence = 0
        self._asr_current_ingress_token = None
        self._asr_partial_turn_token = None
        self._asr_accepted_final_keys.clear()
        self._asr_reserved_final_key = None
        self._asr_candidate_rejection = None
        self._asr_sealed_turn_token = None
        self._asr_provider_candidate_fence = None
        self._asr_turn_endpointed_at = None
        self._asr_turn_audio_started_at = None
        self._asr_turn_onset_at = None
        self._asr_pending_turn_onset_at = None
        self._asr_first_partial_recorded = False
        watchdog = self._asr_rejection_watchdog_task
        self._asr_rejection_watchdog_task = None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()

    async def _notify_asr_turn_abandoned(
        self,
        turn_token: VoiceTurnToken,
    ) -> None:
        """Release the Core-side pause keyed to an abandoned prepared turn."""

        try:
            await self._callbacks.on_turn_abandoned(turn_token)
        except Exception:
            logger.debug(
                "[%s] independent ASR turn abandonment callback failed",
                self.display_name,
            )

    async def _close_independent_asr(
        self,
        *,
        operation_generation: int | None = None,
    ) -> None:
        """Invalidate callbacks first, then release the detached provider session."""

        cleanup = self._detach_independent_asr(
            operation_generation=operation_generation,
        )
        if cleanup is not None:
            await cleanup

    def _detach_independent_asr(
        self,
        *,
        operation_generation: int | None = None,
    ) -> Awaitable[None] | None:
        """Synchronously seize one generation and return its owned cleanup."""

        self._ensure_asr_runtime_state()
        if operation_generation is None:
            operation_generation = self._begin_asr_start_operation()
        elif not self._asr_start_operation_matches(operation_generation):
            return None
        self._asr_session_epoch += 1
        self._asr_audio_generation += 1
        transcript_dispatcher = self._asr_transcript_dispatcher
        detector_dispatcher = self._asr_detector_dispatcher
        audio_dispatcher = self._asr_audio_dispatcher
        transcript_dispatcher.invalidate_all()
        detector_dispatcher.invalidate_all()
        audio_dispatcher.abort()
        self._asr_transcript_dispatcher = TranscriptDispatcher(
            self._dispatch_asr_transcript_envelope,
        )
        self._asr_detector_dispatcher = AsrDetectorDispatcher(
            self._dispatch_asr_detector_event,
            on_failure=self._handle_asr_detector_dispatcher_failure,
        )
        self._asr_audio_dispatcher = AsrAudioDispatcher(
            validator=self._asr_audio_command_is_valid,
            on_wire_audio=self._record_asr_dispatcher_wire_audio,
            on_failure=self._handle_asr_audio_dispatcher_failure,
        )
        asr_session, self._asr_session = self._asr_session, None
        lifecycle, self._asr_lifecycle = self._asr_lifecycle, None
        detector, self._asr_detector = self._asr_detector, None
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
        detached_tasks: list[asyncio.Task[Any]] = []
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                detached_tasks.append(task)
        close_tasks = tuple(self._asr_close_tasks)
        self._asr_close_tasks = set()
        rejection_tasks = tuple(
            task
            for task in self._asr_rejection_tasks
            if task is not asyncio.current_task()
        )
        self._asr_rejection_tasks = set()
        for task in rejection_tasks:
            task.cancel()
        self._asr_provider = None
        if lifecycle is not None:
            lifecycle.stop()
        self._reset_asr_turn_state()
        self._asr_session_factory = None
        self._asr_transport_selection = None

        async def finish_detached_cleanup() -> None:
            if detector is not None:
                try:
                    await detector.close()
                except Exception:
                    logger.warning(
                        "[%s] detector close failed during ASR close",
                        self.display_name,
                    )
            if lease is not None:
                try:
                    await lease.release()
                except Exception:
                    logger.warning(
                        "[%s] SmartTurn lease release failed during ASR close",
                        self.display_name,
                    )
            if asr_session is not None:
                try:
                    await asr_session.close()
                except Exception:
                    logger.warning(
                        "[%s] independent ASR close failed",
                        self.display_name,
                    )
            wait_tasks = (*detached_tasks, *close_tasks, *rejection_tasks)
            if wait_tasks:
                await asyncio.gather(*wait_tasks, return_exceptions=True)
            await detector_dispatcher.close()
            await audio_dispatcher.close()
            transcript_dispatcher.invalidate_all()

        return finish_detached_cleanup()

    async def submit(
        self,
        frame: ProcessedVoiceFrame,
        *,
        ingress_token: VoiceIngressToken,
    ) -> AsrSubmitResult:
        """Submit one normalized frame to the independent-ASR hard route."""

        self._ensure_asr_runtime_state()
        if self._asr_lifecycle is None:
            return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
        if not self._ingress_token_matches(ingress_token):
            return AsrSubmitResult(AsrSubmitStatus.STALE)
        self._asr_current_ingress_token = ingress_token
        identity = self._capture_runtime_identity(ingress_token=ingress_token)

        pcm16 = frame.pcm16
        sample_rate_hz = frame.sample_rate_hz
        speech_probability = frame.speech_probability
        rnnoise_available = frame.rnnoise_available
        rnnoise_evidence = frame.rnnoise_evidence

        try:
            lifecycle = identity.lifecycle
            detector = identity.detector

            def ingress_is_current() -> bool:
                return self._runtime_identity_matches(identity)

            if lifecycle is not None and detector is not None:
                submit_audio = getattr(detector, "submit_audio", None)
                uses_smart_turn = _uses_smart_turn_endpointing(lifecycle.provider_policy)
                if uses_smart_turn and callable(submit_audio):
                    detector_submit_started_at = time.perf_counter()
                    submitted = await submit_audio(
                        pcm16,
                        ingress_token=ingress_token,
                        sample_rate_hz=sample_rate_hz,
                        speech_probability=speech_probability,
                        rnnoise_available=bool(rnnoise_available),
                        rnnoise_evidence=rnnoise_evidence,
                        allow_baseline_update=(
                            lifecycle.snapshot.state
                            in {
                                VoiceLifecycleState.LOCAL_LISTEN,
                                VoiceLifecycleState.WARM_IDLE,
                            }
                        ),
                    )
                    if not ingress_is_current():
                        return AsrSubmitResult(AsrSubmitStatus.STALE)
                    lifecycle.metrics.detector_submit_latency_ms = int(
                        (time.perf_counter() - detector_submit_started_at) * 1_000
                    )
                    lifecycle.metrics.detector_queue_audio_ms = detector.queued_audio_ms
                    lifecycle.metrics.detector_queue_high_water_ms = max(
                        lifecycle.metrics.detector_queue_high_water_ms,
                        detector.queued_audio_ms,
                    )
                    lifecycle.metrics.smart_turn_inference_ms = (
                        detector.smart_turn_evaluation_ms
                    )
                    lifecycle.metrics.smart_turn_stale_result_count = (
                        detector.smart_turn_stale_result_count
                    )
                    lifecycle.metrics.smart_turn_coalesced_evaluation_count = (
                        detector.smart_turn_coalesced_evaluation_count
                    )
                    if submitted.status is DetectorSubmitStatus.SKIPPED_QUIET:
                        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
                    if submitted.status is DetectorSubmitStatus.BACKPRESSURE:
                        lifecycle.metrics.detector_overflow_count += 1
                        await self._handle_audio_ingress_backpressure(
                            ingress_token,
                            observed_state=lifecycle.snapshot.state,
                        )
                        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
                    if (
                        submitted.status
                        in {DetectorSubmitStatus.CLOSED, DetectorSubmitStatus.FAILED}
                        or not submitted.endpointing_available
                    ):
                        await self._handle_independent_asr_error(
                            identity.session_epoch,
                            identity.provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                            expected_identity=identity,
                        )
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                    if not submitted.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                        if (
                            not submitted.control_event_emitted
                            and submitted.identity is not None
                            and submitted.candidate is not None
                        ):
                            accepted = self._asr_detector_dispatcher.submit_nowait(
                                CoreDetectorEventEnvelope(
                                    event=DetectorPrewarmEvent(
                                        ingress=submitted.identity,
                                        candidate=submitted.candidate,
                                        kind="continuous",
                                    ),
                                    detector_ref=detector,
                                    lifecycle_ref=lifecycle,
                                    session_epoch=identity.session_epoch,
                                )
                            )
                            if not accepted:
                                await self._handle_independent_asr_error(
                                    identity.session_epoch,
                                    identity.provider or "unknown",
                                    status_code="ASR_ENDPOINTING_FAILED",
                                    expected_identity=identity,
                                )
                                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                else:
                    detector_result = await detector.feed(
                        pcm16,
                        speech_probability=speech_probability,
                        rnnoise_available=rnnoise_available,
                        rnnoise_evidence=rnnoise_evidence,
                        ingress_token=ingress_token,
                        allow_baseline_update=(
                            lifecycle.snapshot.state
                            in {
                                VoiceLifecycleState.LOCAL_LISTEN,
                                VoiceLifecycleState.WARM_IDLE,
                            }
                        ),
                    )
                    if not ingress_is_current():
                        return AsrSubmitResult(AsrSubmitStatus.STALE)
                    if not detector_result.endpointing_available:
                        await self._handle_independent_asr_error(
                            identity.session_epoch,
                            identity.provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                            expected_identity=identity,
                        )
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                    if detector_result.throttle_action is ThrottleAction.SKIP_IDLE_PCM:
                        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
                    if not detector_result.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                    else:
                        for event in detector_result.events:
                            await self._handle_independent_asr_activity(
                                event,
                                identity.session_epoch,
                            )
                            if not ingress_is_current():
                                return AsrSubmitResult(AsrSubmitStatus.STALE)
                    if (
                        not detector_result.throttle_available
                        or not self._voice_input_resource_optimization_enabled
                    ) and not await self._ensure_continuous_provider_wake(
                        lifecycle,
                        identity.session_epoch,
                    ):
                        if not ingress_is_current():
                            return AsrSubmitResult(AsrSubmitStatus.STALE)
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            if lifecycle is not None and not ingress_is_current():
                return AsrSubmitResult(AsrSubmitStatus.STALE)
            suppression = self._asr_candidate_rejection
            if (
                suppression is not None
                and identity.session_epoch == suppression.request.session_epoch
                and identity.audio_generation
                == suppression.request.audio_generation
                and identity.lifecycle is suppression.lifecycle
                and identity.detector is suppression.detector
            ):
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            decision = (
                lifecycle.accept_audio(pcm16, sample_rate_hz=sample_rate_hz)
                if lifecycle is not None
                else None
            )
            if decision is not None and decision.disposition is AudioDisposition.BLOCK:
                if decision.backpressure:
                    await self._handle_audio_ingress_backpressure(
                        ingress_token,
                        observed_state=lifecycle.snapshot.state,
                    )
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            if decision is not None and decision.disposition in {
                AudioDisposition.BUFFER,
                AudioDisposition.SUPPRESS,
            }:
                if (
                    lifecycle is not None
                    and lifecycle.snapshot.state
                    in {
                        VoiceLifecycleState.PREWARMING,
                        VoiceLifecycleState.BACKOFF,
                    }
                    and (
                        self._asr_session is None
                        or not getattr(self._asr_session, "is_ready", True)
                    )
                ):
                    self._ensure_transport_restart_task()
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            if lifecycle is None or detector is None:
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                    expected_identity=identity,
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            turn_token = self._capture_turn_token(lifecycle)
            if (
                lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
                or not self._asr_endpointing_ready(lifecycle, detector, turn_token)
            ):
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                    expected_identity=identity,
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            asr_session = self._asr_session
            if asr_session is None or not getattr(asr_session, "is_ready", True):
                self._ensure_transport_restart_task()
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            payload = (
                decision.pre_roll
                if decision is not None
                and decision.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
                else pcm16
            )
            if not payload:
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            if not ingress_is_current():
                return AsrSubmitResult(AsrSubmitStatus.STALE)
            if self._asr_audio_dispatcher.active_turn != turn_token:
                if not self._activate_asr_audio_dispatcher(lifecycle, turn_token):
                    await self._handle_independent_asr_error(
                        identity.session_epoch,
                        identity.provider or "unknown",
                        status_code="ASR_AUDIO_ORDERING_FAILED",
                        expected_identity=identity,
                    )
                    return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            self._asr_audio_sequence += 1
            if not self._asr_audio_dispatcher.enqueue_audio(
                turn_token,
                asr_session,
                payload,
                sample_rate_hz=sample_rate_hz,
                sequence_no=self._asr_audio_sequence,
            ):
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_AUDIO_ORDERING_FAILED",
                    expected_identity=identity,
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            self._observe_provider_speaker_shadow(
                detector,
                payload,
                sample_rate_hz=sample_rate_hz,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._runtime_identity_matches(identity):
                return AsrSubmitResult(AsrSubmitStatus.STALE)
            self._asr_received_audio = True
            status_code = (
                "ASR_STREAM_BACKPRESSURE"
                if str(exc).startswith("ASR_STREAM_BACKPRESSURE:")
                else "ASR_INDEPENDENT_STREAM_FAILED"
            )
            if (
                status_code == "ASR_STREAM_BACKPRESSURE"
                and identity.lifecycle is not None
            ):
                identity.lifecycle.metrics.queue_backpressure_count += 1
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code=status_code,
                expected_identity=identity,
            )
            return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)

        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    def _ensure_transport_restart_task(self) -> None:
        task = self._asr_transport_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._restart_transport(),
            name="independent-asr-transport-restart",
        )
        task.add_done_callback(self._log_asr_background_task_failure)
        self._asr_transport_task = task

    def _log_asr_background_task_failure(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "[%s] independent ASR background task %s failed",
                self.display_name,
                task.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _restart_transport(self, *, max_attempts: int | None = None) -> None:
        if max_attempts is not None and max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        async with self._asr_transport_lock:
            lifecycle = self._asr_lifecycle
            if lifecycle is None:
                return
            existing = self._asr_session
            if existing is not None and getattr(existing, "is_ready", True):
                return
            if existing is not None:
                self._asr_session = None
                detached_identity = self._capture_runtime_identity()
                await self._close_asr_session(existing)
                if not self._runtime_identity_matches(detached_identity):
                    return
            lifecycle = self._asr_lifecycle
            factory = self._asr_session_factory
            selection = self._asr_transport_selection
            identity = self._capture_runtime_identity()
            if factory is None or selection is None or lifecycle is None:
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    expected_identity=identity,
                )
                return
            # Mirror initial startup: the active provider policy decides the
            # attempt budget and backoff ladder unless the caller overrides it.
            policy = lifecycle.provider_policy
            if max_attempts is None:
                max_attempts = policy.connect_max_attempts

            for attempt in range(max_attempts):
                if not self._runtime_identity_matches(identity):
                    return
                if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                    lifecycle.transition(VoiceLifecycleEvent.RETRY)
                    lifecycle.metrics.reconnect_count += 1
                    identity = self._capture_runtime_identity()
                    await self._send_asr_lifecycle_state(
                        VoiceLifecycleState.PREWARMING,
                        provider=identity.provider or "unknown",
                        session_epoch=identity.session_epoch,
                        expected_identity=identity,
                    )
                    if not self._runtime_identity_matches(identity):
                        return
                candidate = None
                try:
                    connect_started_at = time.monotonic()
                    candidate = factory(selection)
                    await candidate.connect()
                    if not self._runtime_identity_matches(identity):
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                        return
                    self._asr_session = candidate
                    self._asr_last_provider_wire_audio_ms = 0
                    lifecycle.invalidate_transport()
                    connected_identity = self._capture_runtime_identity()
                    lifecycle.metrics.connect_latency_ms = int(
                        (time.monotonic() - connect_started_at) * 1_000
                    )
                    if (
                        self._asr_pending_speech_confirmed
                        and lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
                    ):
                        detector = self._asr_detector
                        turn_token = self._capture_turn_token(lifecycle)
                        if detector is None or not self._asr_endpointing_ready(
                            lifecycle,
                            detector,
                            turn_token,
                        ):
                            await self._handle_independent_asr_error(
                                connected_identity.session_epoch,
                                connected_identity.provider or "unknown",
                                status_code="ASR_BLOCKED_ENDPOINTING",
                                expected_identity=connected_identity,
                            )
                            return
                        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
                        self._asr_turn_onset_at = (
                            self._asr_pending_speech_onset_at
                            if self._asr_pending_speech_onset_at is not None
                            else time.monotonic()
                        )
                        self._asr_pending_speech_confirmed = False
                        self._asr_pending_speech_onset_at = None
                        self._asr_turn_audio_started_at = time.monotonic()
                        self._asr_first_partial_recorded = False
                        await self._send_asr_lifecycle_state(
                            VoiceLifecycleState.ACTIVE,
                            provider=connected_identity.provider or "unknown",
                            session_epoch=connected_identity.session_epoch,
                            expected_identity=connected_identity,
                        )
                        if not self._runtime_identity_matches(connected_identity):
                            return
                        payload = lifecycle.drain_active_start_audio()
                        await self._prepare_independent_asr_turn(
                            connected_identity.session_epoch
                        )
                        if not self._runtime_identity_matches(connected_identity):
                            return
                        if not self._activate_asr_audio_dispatcher(
                            lifecycle,
                            turn_token,
                            buffered_pcm16=payload,
                        ):
                            await self._handle_independent_asr_error(
                                connected_identity.session_epoch,
                                connected_identity.provider or "unknown",
                                status_code="ASR_AUDIO_ORDERING_FAILED",
                                expected_identity=connected_identity,
                            )
                            return
                    return
                except asyncio.CancelledError:
                    if candidate is not None and self._asr_session is candidate:
                        adopted_identity = self._capture_runtime_identity()
                        await self._handle_independent_asr_error(
                            adopted_identity.session_epoch,
                            adopted_identity.provider or "unknown",
                            status_code="ASR_INDEPENDENT_FAILED",
                            expected_identity=adopted_identity,
                        )
                    elif candidate is not None:
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                    raise
                except Exception:
                    if candidate is not None and self._asr_session is candidate:
                        adopted_identity = self._capture_runtime_identity()
                        await self._handle_independent_asr_error(
                            adopted_identity.session_epoch,
                            adopted_identity.provider or "unknown",
                            status_code="ASR_INDEPENDENT_FAILED",
                            expected_identity=adopted_identity,
                        )
                        return
                    if candidate is not None:
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                    if not self._runtime_identity_matches(identity):
                        return
                    if lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING:
                        lifecycle.transition(VoiceLifecycleEvent.CONNECT_FAILED)
                        identity = self._capture_runtime_identity()
                        await self._send_asr_lifecycle_state(
                            VoiceLifecycleState.BACKOFF,
                            provider=identity.provider or "unknown",
                            session_epoch=identity.session_epoch,
                            expected_identity=identity,
                        )
                        if not self._runtime_identity_matches(identity):
                            return
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(
                            min(
                                policy.connect_retry_cap_seconds,
                                policy.connect_retry_base_seconds * (2**attempt),
                            )
                        )
                        if not self._runtime_identity_matches(identity):
                            return
                        continue
            if not self._runtime_identity_matches(identity):
                return
            if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                lifecycle.transition(VoiceLifecycleEvent.RETRIES_EXHAUSTED)
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_INDEPENDENT_FAILED",
                expected_identity=identity,
            )

    async def _abort_transport(
        self,
        reason: str,
    ) -> _AsrRuntimeIdentity:
        """Invalidate provider I/O before closing a live transport."""

        self._begin_asr_start_operation()
        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_detector_dispatcher.invalidate_all()
        self._asr_audio_dispatcher.abort()
        self._reset_asr_turn_state()
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        asr_session, self._asr_session = self._asr_session, None
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.metrics.asr_abort_discarded_command_count = (
                self._asr_audio_dispatcher.asr_abort_discarded_command_count
            )
            lifecycle.invalidate_transport()
        post_detach = self._capture_runtime_identity()

        async def finish_abort() -> None:
            try:
                if lease is not None:
                    await lease.release()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "[%s] SmartTurn lease release failed during ASR abort",
                    self.display_name,
                )
            finally:
                if asr_session is not None:
                    try:
                        await asr_session.close()
                    except Exception:
                        logger.warning(
                            "[%s] independent ASR abort failed reason=%s",
                            self.display_name,
                            reason,
                        )

        cleanup_task = self._schedule_owned_cleanup(
            finish_abort(),
            name="independent-asr-abort-transport",
        )
        await asyncio.shield(cleanup_task)
        return post_detach

    async def _close_transport_only(self) -> None:
        """Enter deep sleep while preserving microphone detection."""

        epoch = self._asr_session_epoch
        provider = self._asr_provider or "unknown"
        warm_task = self._asr_warm_expiry_task
        if warm_task is not None and warm_task is not asyncio.current_task():
            warm_task.cancel()
        self._asr_warm_expiry_task = None
        asr_session, self._asr_session = self._asr_session, None
        session_close_task = None
        if asr_session is not None:
            async def close_transport() -> None:
                try:
                    await asr_session.close()
                except Exception:
                    logger.warning(
                        "[%s] independent ASR transport-only close failed",
                        self.display_name,
                    )

            session_close_task = self._schedule_owned_cleanup(
                close_transport(),
                name="independent-asr-transport-close",
            )
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.invalidate_transport()
            if lifecycle.snapshot.state in {
                VoiceLifecycleState.LOCAL_LISTEN,
                VoiceLifecycleState.WARM_IDLE,
            }:
                lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
                identity = self._capture_runtime_identity()
                await self._send_asr_lifecycle_state(
                    VoiceLifecycleState.DEEP_SLEEP,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=identity,
                )
        if session_close_task is not None:
            await asyncio.shield(session_close_task)

    def _schedule_transport_warm_expiry(
        self,
        epoch: int,
        *,
        expected_state: VoiceLifecycleState,
    ) -> None:
        task = self._asr_warm_expiry_task
        if task is not None:
            task.cancel()
        lifecycle = self._asr_lifecycle
        if lifecycle is None or not self._voice_input_resource_optimization_enabled:
            return
        if expected_state is VoiceLifecycleState.WARM_IDLE:
            ttl_ms = lifecycle.provider_policy.warm_transport_ms
        elif expected_state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.PREWARMING,
        }:
            ttl_ms = lifecycle.config.default_warm_transport_ms
        else:
            raise ValueError(
                "transport expiry requires local-listen, prewarming, or warm-idle"
            )
        session_ref = self._asr_session
        detector_ref = self._asr_detector
        transport_generation = lifecycle.snapshot.transport_generation

        def timer_is_current() -> bool:
            return bool(
                epoch == self._asr_session_epoch
                and self._asr_lifecycle is lifecycle
                and self._asr_session is session_ref
                and self._asr_detector is detector_ref
                and lifecycle.snapshot.transport_generation
                == transport_generation
            )

        async def expire() -> None:
            try:
                await asyncio.sleep(ttl_ms / 1_000)
                if (
                    not timer_is_current()
                    or lifecycle.snapshot.state is not expected_state
                ):
                    return
                if expected_state is VoiceLifecycleState.PREWARMING:
                    lease, self._asr_smart_turn_lease = (
                        self._asr_smart_turn_lease,
                        None,
                    )
                    if lease is not None:
                        await lease.release()
                    if not timer_is_current():
                        return
                    if detector_ref is not None:
                        await detector_ref.reset()
                    if (
                        not timer_is_current()
                        or lifecycle.snapshot.state
                        is not VoiceLifecycleState.PREWARMING
                    ):
                        return
                    lifecycle.transition(VoiceLifecycleEvent.PREWARM_EXPIRED)
                    self._asr_pending_speech_confirmed = False
                    self._asr_pending_speech_onset_at = None
                    self._asr_pending_detector_candidate = None
                    identity = self._capture_runtime_identity()
                    delivered = await self._send_asr_lifecycle_state(
                        VoiceLifecycleState.LOCAL_LISTEN,
                        provider=identity.provider or "unknown",
                        session_epoch=identity.session_epoch,
                        expected_identity=identity,
                    )
                    if (
                        not delivered
                        or not timer_is_current()
                        or lifecycle.snapshot.state
                        is not VoiceLifecycleState.LOCAL_LISTEN
                    ):
                        return
                await self._close_transport_only()
            except asyncio.CancelledError:
                return
            finally:
                if self._asr_warm_expiry_task is asyncio.current_task():
                    self._asr_warm_expiry_task = None

        warm_task = asyncio.create_task(
            expire(),
            name="independent-asr-warm-expiry",
        )
        warm_task.add_done_callback(self._log_asr_background_task_failure)
        self._asr_warm_expiry_task = warm_task

    def _schedule_provider_final_watchdog(
        self,
        epoch: int,
        lifecycle: VoiceInputLifecycleController,
        sealed_token: VoiceTransportToken,
    ) -> None:
        task = self._asr_final_watchdog_task
        if task is not None:
            task.cancel()
        timeout_ms = lifecycle.provider_policy.provider_final_timeout_ms

        async def expire() -> None:
            try:
                await asyncio.sleep(timeout_ms / 1_000)
                if (
                    epoch != self._asr_session_epoch
                    or self._asr_lifecycle is not lifecycle
                    or self._asr_sealed_turn_token != sealed_token
                    or lifecycle.snapshot.state is not VoiceLifecycleState.DRAINING
                ):
                    return
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_PROVIDER_FINAL_TIMEOUT",
                )
            except asyncio.CancelledError:
                return

        watchdog_task = asyncio.create_task(
            expire(),
            name="independent-asr-provider-final-watchdog",
        )
        watchdog_task.add_done_callback(self._log_asr_background_task_failure)
        self._asr_final_watchdog_task = watchdog_task

    def _sync_provider_wire_metrics(
        self,
        asr_session: Any,
        *,
        fallback_audio_bytes: int = 0,
    ) -> None:
        lifecycle = self._asr_lifecycle
        if lifecycle is None:
            return
        cumulative_ms = getattr(asr_session, "provider_wire_audio_ms", None)
        if isinstance(cumulative_ms, int) and not isinstance(cumulative_ms, bool):
            delta_ms = max(0, cumulative_ms - self._asr_last_provider_wire_audio_ms)
            self._asr_last_provider_wire_audio_ms = max(
                self._asr_last_provider_wire_audio_ms,
                cumulative_ms,
            )
            if delta_ms:
                lifecycle.record_provider_wire_audio(delta_ms)
            return
        if (
            lifecycle.provider_policy.transport == "streaming"
            and fallback_audio_bytes > 0
        ):
            lifecycle.record_provider_wire_audio(
                fallback_audio_bytes * 1_000 // (16_000 * 2)
            )

    async def _handle_independent_asr_activity(
        self,
        event: SpeechActivityEvent,
        epoch: int,
    ) -> None:
        # 同上：onset 是收到这个语音活动事件的时刻。
        detected_at = time.monotonic()
        if epoch != self._asr_session_epoch:
            return
        provider = self._asr_provider or "unknown"
        lifecycle = self._asr_lifecycle
        if (
            lifecycle is not None
            and lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
            and event
            in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }
        ):
            lifecycle.mark_pending_turn_speech()
            if self._asr_pending_turn_onset_at is None:
                self._asr_pending_turn_onset_at = detected_at
            return
        if (
            lifecycle is not None
            and lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
            and lifecycle.has_pending_turn
            and event
            in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }
        ):
            # The DRAINING path already confirmed this pending turn. Re-marking
            # it after PROVIDER_FINAL reaches WARM_IDLE violates the lifecycle
            # guard and can fail the replacement turn during activation.
            return
        if lifecycle is not None and event in {
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.SPEECH_RESUMED,
        }:
            ingress_token = self._asr_current_ingress_token
            if ingress_token is None or not self._ingress_token_matches(
                ingress_token
            ):
                # An idle ingress-backpressure bump keeps the provider session
                # adopted, so a trailing session-side speech event can still
                # reach this handler with a stale audio generation. The wake
                # path below cannot mint a turn token without a current
                # ingress token, so drop the stale event cleanly instead of
                # raising into the provider adapter. Genuinely new speech
                # re-arms the current token through submit() first.
                return
            previous_state = lifecycle.snapshot.state
            state = previous_state
            if state in {
                VoiceLifecycleState.LOCAL_LISTEN,
                VoiceLifecycleState.DEEP_SLEEP,
                VoiceLifecycleState.WARM_IDLE,
            }:
                warm_task = self._asr_warm_expiry_task
                if warm_task is not None:
                    warm_task.cancel()
                    self._asr_warm_expiry_task = None
                if state is VoiceLifecycleState.WARM_IDLE:
                    lifecycle.metrics.warm_hit_count += 1
                lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
                state = lifecycle.snapshot.state
            if state is VoiceLifecycleState.PREWARMING:
                if not await self._ensure_smart_turn_ready(lifecycle, epoch):
                    return
                asr_session = self._asr_session
                if asr_session is not None and getattr(asr_session, "is_ready", True):
                    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
                    # session 先未就绪、随后又 ready 时，真实开口时刻是当初记下的那个
                    # pending onset —— 直接用当前时钟会把整段重连等待算成「开口之后」，
                    # 期间拍的帧全被排除在本回合外（CodeRabbit Major）。
                    self._asr_turn_onset_at = (
                        self._asr_pending_speech_onset_at
                        if self._asr_pending_speech_onset_at is not None
                        else detected_at
                    )
                    self._asr_pending_speech_confirmed = False
                    self._asr_pending_speech_onset_at = None
                else:
                    self._asr_pending_speech_confirmed = True
                    if self._asr_pending_speech_onset_at is None:
                        self._asr_pending_speech_onset_at = detected_at
            if lifecycle.snapshot.state is not previous_state:
                identity = self._capture_runtime_identity(
                    ingress_token=self._asr_current_ingress_token,
                )
                delivered = await self._send_asr_lifecycle_state(
                    lifecycle.snapshot.state,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=identity,
                )
                if not delivered:
                    return
            if (
                lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
                and previous_state is not VoiceLifecycleState.ACTIVE
            ):
                self._asr_turn_audio_started_at = time.monotonic()
                self._asr_first_partial_recorded = False
        if event not in {
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.SPEECH_RESUMED,
        }:
            if event is SpeechActivityEvent.CANDIDATE_PAUSE:
                # Once local VAD observes a pause, a later provider final may
                # simply be the current utterance ending, so replaying the
                # remembered onset at that final would wake a ghost turn. The
                # onset must not be dropped outright either: when the pause
                # closes a genuine overlapping utterance, its provider endpoint
                # and final are still queued in the ordered FIFO behind the
                # previous turn's final. Convert the onset into a
                # completed-overlap credit; only a provider endpoint arriving
                # in WARM_IDLE proves a queued turn exists and redeems it.
                onset_token = self._asr_overlap_onset_token
                onset_at = self._asr_overlap_onset_at
                self._asr_overlap_onset_token = None
                self._asr_overlap_onset_at = None
                if onset_token is not None:
                    # 一张 credit 配一个时刻，按兑付顺序排队。
                    self._asr_overlap_completed_onsets.append(
                        onset_at if onset_at is not None else detected_at
                    )
                    if onset_token == self._asr_overlap_completed_token:
                        # Each additional onset+pause cycle observed while the
                        # first turn stays ACTIVE queues one more provider
                        # endpoint/final pair, so count credits per cycle.
                        self._asr_overlap_completed_turns += 1
                    else:
                        # 换了 ingress 身份：旧队列作废，只留这一张。
                        last = self._asr_overlap_completed_onsets.pop()
                        self._asr_overlap_completed_onsets.clear()
                        self._asr_overlap_completed_onsets.append(last)
                        self._asr_overlap_completed_token = onset_token
                        self._asr_overlap_completed_turns = 1
            return
        if self._asr_turn_prepared:
            if (
                lifecycle is not None
                and lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
                and lifecycle.provider_policy.endpoint_authority == "provider"
            ):
                # Provider-VAD endpoints ride the ordered callback FIFO right
                # before their own final, so a genuine next-turn onset can
                # reach Core while the previous turn is still ACTIVE and
                # prepared. Remember the onset (ingress-fenced) so the delayed
                # final can replay it instead of dropping the next turn.
                self._asr_overlap_onset_token = self._asr_current_ingress_token
                self._asr_overlap_onset_at = detected_at
            return
        if (
            lifecycle is not None
            and lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
        ):
            return

        await self._prepare_independent_asr_turn(epoch)

    async def _prepare_independent_asr_turn(self, epoch: int) -> None:
        """Prepare an identified turn without deciding its endpoint."""

        if epoch != self._asr_session_epoch or self._asr_turn_prepared:
            return

        lifecycle = self._asr_lifecycle
        if (
            lifecycle is None
            or lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
        ):
            return
        turn_token = self._capture_turn_token(lifecycle)
        final_key = FinalKey.from_turn(turn_token)
        transcript_dispatcher = self._asr_transcript_dispatcher
        if not transcript_dispatcher.try_reserve(final_key):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_CORE_TRANSCRIPT_BACKPRESSURE",
            )
            return
        self._asr_reserved_final_key = final_key
        self._asr_turn_prepared = True
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        try:
            accepted = await self._callbacks.on_prepare_turn(turn_token)
        except Exception:
            accepted = False
            if self._runtime_identity_matches(identity):
                logger.warning(
                    "[%s] independent ASR turn preparation failed",
                    self.display_name,
                )
        if accepted and self._runtime_identity_matches(identity):
            # The provider callback carries text only. Pin the source identity
            # at the ordered prepare boundary; partial delivery later validates
            # this exact token instead of relabeling text with whatever turn
            # happens to be current at callback time.
            self._asr_partial_turn_token = turn_token
            return
        transcript_dispatcher.release(final_key)
        if not self._runtime_identity_matches(identity):
            return
        if (
            self._asr_transcript_dispatcher is transcript_dispatcher
            and self._asr_reserved_final_key == final_key
        ):
            self._asr_reserved_final_key = None
            self._asr_turn_prepared = False
            if self._asr_partial_turn_token == turn_token:
                self._asr_partial_turn_token = None

    def _consume_overlap_completed_credit(self) -> None:
        """Retire one redeemed completed-overlap credit and its onset."""

        self._asr_overlap_completed_turns -= 1
        if self._asr_overlap_completed_onsets:
            self._asr_overlap_completed_onsets.popleft()
        if self._asr_overlap_completed_turns == 0:
            self._asr_overlap_completed_token = None
            self._asr_overlap_completed_onsets.clear()

    async def _handle_independent_asr_endpoint(self, epoch: int) -> None:
        """Seal the current turn immediately at its semantic endpoint."""

        if epoch != self._asr_session_epoch:
            return
        provider = self._asr_provider or "unknown"
        lifecycle = self._asr_lifecycle
        if lifecycle is None:
            return
        if (
            lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
            and self._asr_overlap_completed_turns > 0
        ):
            completed_token = self._asr_overlap_completed_token
            if (
                completed_token is None
                or lifecycle.provider_policy.endpoint_authority != "provider"
                or completed_token != self._asr_current_ingress_token
                or not self._ingress_token_matches(completed_token)
            ):
                # The credit belongs to a superseded ingress generation (hard
                # mute, abort, or route swap rotated the token), so drop it
                # instead of waking a stale replacement turn.
                self._asr_overlap_completed_token = None
                self._asr_overlap_completed_turns = 0
                return
            # A provider endpoint reaching Core in WARM_IDLE means the ordered
            # FIFO holds a turn whose local onset and pause both happened while
            # the previous turn was still ACTIVE (its endpoint was queued
            # behind that turn's delayed final). Redeem one completed-overlap
            # credit: replay the onset so the lifecycle is ACTIVE and prepared,
            # then fall through to seal immediately, letting the queued final
            # right behind this endpoint find a DRAINING turn.
            # ⚠️ 先重放、确认真的醒过来了，**再**记账。重放可能唤不醒这一轮
            # （会话暂时不可用时停在 PREWARMING）；此时若 credit 已经扣掉，这张
            # credit 对应的 endpoint 就再也封不了口，紧随其后的 final 会被整条
            # 丢弃，而被弹出的 onset 还会被更晚的回合继承（拿错视觉窗口）。
            replay_onset_at = (
                self._asr_overlap_completed_onsets[0]
                if self._asr_overlap_completed_onsets
                else None
            )
            # 把真实开口时刻交给重放：直接确认分支会优先取 pending onset，于是
            # SPEECH_CONFIRMED 打上的是用户当初开口的时刻，而不是这次重放的时刻。
            _lent_pending_onset = False
            if (
                replay_onset_at is not None
                and self._asr_pending_speech_onset_at is None
            ):
                self._asr_pending_speech_onset_at = replay_onset_at
                _lent_pending_onset = True
            pending_before = self._asr_pending_speech_confirmed
            credit_consumed = False
            await self._handle_independent_asr_activity(
                SpeechActivityEvent.SPEECH_RESUMED,
                epoch,
            )
            if (
                epoch != self._asr_session_epoch
                or self._asr_lifecycle is not lifecycle
            ):
                return
            if (
                not pending_before
                and self._asr_pending_speech_confirmed
                and lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
            ):
                # 重放被"传输未就绪"挡住了，停在 PREWARMING 并挂起了确认。
                # （provider 权威下 SOFT_WAKE→PREWARMING 之后拦路的就是
                # asr_session.is_ready —— _ensure_smart_turn_ready 在 provider
                # 权威下无 await 直接返回 True；PREWARMING 的 lifecycle 广播没送达
                # 是同态的另一种成因。别在注释里写死"唯一成因"。）
                #
                # 但这一轮**不需要**传输：它的音频早在上一轮还 ACTIVE 时就已经过
                # 线，endpoint 和它自己的 final 已经排在有序 FIFO 里、正要到达。
                # 而且能走到这里就说明老 session 还被认领着——_restart_transport
                # 和 _close_transport_only 都是先把 _asr_session 置 None 再 close，
                # 之后 is_adopted_candidate() 会丢掉它的全部回调——也就是说重连
                # **还没开始**，那条 final 就排在后面。等重连救不回它：重连会换新
                # session，老队列里那条 final 必定在 is_adopted_candidate() 上被
                # 丢掉。就地补完确认，让紧随其后的 final 找到一个 DRAINING 的回合。
                #
                # 这里刻意**不**走 _handle_independent_asr_error：那条出口会 bump
                # epoch、拆掉整个 session、cancel 掉正在跑的重连任务，并把语音路由
                # fail-closed 到本次会话结束——为一句其实救得回来的话把整场语音判
                # 死，违反"绝不丢用户的句子"。真丢的情况（final 始终不来）由下面封
                # 口时装上的 provider-final watchdog 兜底：10s 硬顶，且不受
                # _voice_input_resource_optimization_enabled 开关影响（那个开关会
                # 让 _schedule_transport_warm_expiry 直接 return，所以不能靠它）。
                #
                # 门里 not pending_before 是刻意的：只补偿**这次重放自己造出来的**
                # 那笔挂起确认，不吞别人的。
                #
                # 刻意不做的两件事：不调 _activate_asr_audio_dispatcher /
                # drain_active_start_audio（重连确认分支有，但这一轮的音频早已过
                # 线，本地没有待发缓冲）；不武装 _schedule_transport_warm_expiry
                # （忙窗口的界由上面那个 watchdog 提供）。将来若有人让这条路承接
                # 未发出的 PCM，必须回来补第一条。
                lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
                self._asr_turn_onset_at = (
                    self._asr_pending_speech_onset_at
                    if self._asr_pending_speech_onset_at is not None
                    else time.monotonic()
                )
                # 与 _restart_transport 的补确认块同序：确认一落地就把挂起状态
                # 清掉。真实开口时刻已经装进 _asr_turn_onset_at，下面那个 await
                # 无论怎么返回都不会把它丢掉。
                self._asr_pending_speech_confirmed = False
                self._asr_pending_speech_onset_at = None
                # 这张 credit 就是被这次确认兑走的，账要跟着确认一起落。留到
                # 下面记的话，身份漂移那条 return 会把它跳过：这一轮照常在替换后的
                # 传输上封口，而陈旧的 credit 与 onset 还压在队列里 —— 后面真实的
                # overlap 排在它后面，兑付时拿到错的 onset，多出来的那张还会让某个
                # endpoint 重放到不属于它的回合上，把一条 final 丢掉。
                self._consume_overlap_completed_credit()
                credit_consumed = True
                self._asr_turn_audio_started_at = time.monotonic()
                self._asr_first_partial_recorded = False
                confirm_identity = self._capture_runtime_identity(
                    ingress_token=self._asr_current_ingress_token,
                )
                delivered = await self._send_asr_lifecycle_state(
                    VoiceLifecycleState.ACTIVE,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=confirm_identity,
                )
                if (
                    not delivered
                    or epoch != self._asr_session_epoch
                    or self._asr_lifecycle is not lifecycle
                ):
                    # delivered 为假只可能是运行时身份漂移：_send_asr_lifecycle_state
                    # 吞掉回调异常之后，返回的就是 _runtime_identity_matches。而
                    # _restart_transport / _close_transport_only 换掉 _asr_session 与
                    # transport_generation 时都不走 _reset_asr_turn_state，所以这里留下
                    # 的挂起状态没人回收：上面 transition(SPEECH_CONFIRMED) 已经把它兑付
                    # 进 _asr_turn_onset_at，两个兑付点又都以 PREWARMING 为闸、ACTIVE 下
                    # 一律跳过。残留下去会被后面某个不相干的回合当成自己的开口时刻，还会
                    # 把补偿门 not pending_before 恒假化，让重叠补偿此后静默失效。
                    return
            if lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE:
                # 没唤醒。credit 原样留着等下一次兑付；借出去的 onset 也要收回，
                # 免得它被后面某个不相干的回合当成自己的起点。
                # ⚠️ 只有在**没有**挂起的确认时才收回。session 未就绪时
                # _handle_independent_asr_activity 会停在 PREWARMING、置上
                # _asr_pending_speech_confirmed 并**特意留着**这个 onset 等重连后
                # 的确认去取；此时收回等于让那次确认退回用新的 detected_at，把用户
                # 真实开口以来的帧全排除掉。
                if (
                    _lent_pending_onset
                    and not self._asr_pending_speech_confirmed
                    and self._asr_pending_speech_onset_at == replay_onset_at
                ):
                    self._asr_pending_speech_onset_at = None
                return
            # 确认 ACTIVE 之后才记账（补确认那条路已经在上面记过了）。
            if not credit_consumed:
                self._consume_overlap_completed_credit()
        if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
            if not self._asr_turn_prepared:
                # A rejected preparation keeps the lifecycle ACTIVE so the
                # utterance can retry (SPEECH_RESUMED re-prepares), but Core
                # never ran the interruption/external-turn pause for this
                # turn. Re-prepare before sealing; without a successful
                # preparation the final must never reach Core, so fail
                # closed instead of sealing an unprepared turn.
                await self._prepare_independent_asr_turn(epoch)
                if (
                    epoch != self._asr_session_epoch
                    or self._asr_lifecycle is not lifecycle
                    or lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
                ):
                    return
                if not self._asr_turn_prepared:
                    await self._handle_independent_asr_error(
                        epoch,
                        provider,
                        status_code="ASR_CORE_TURN_REJECTED",
                    )
                    return
            turn_token = self._capture_turn_token(lifecycle)
            detector = self._asr_detector
            if not self._asr_endpointing_ready(lifecycle, detector, turn_token):
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                )
                return
            final_key = FinalKey.from_turn(turn_token)
            transcript_dispatcher = self._asr_transcript_dispatcher
            if not transcript_dispatcher.try_reserve(final_key):
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_CORE_TRANSCRIPT_BACKPRESSURE",
                )
                return
            self._asr_reserved_final_key = final_key
            if not _uses_smart_turn_endpointing(lifecycle.provider_policy):
                endpoint_identity = self._capture_runtime_identity(
                    ingress_token=turn_token.ingress,
                    turn_token=turn_token,
                )
                try:
                    provider_fence = await detector.seal_provider_candidate()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    provider_fence = None
                    logger.warning(
                        "[%s] provider candidate seal failed",
                        self.display_name,
                    )
                if not self._runtime_identity_matches(endpoint_identity):
                    transcript_dispatcher.release(final_key)
                    self._asr_reserved_final_key = None
                    return
                if provider_fence is None:
                    transcript_dispatcher.release(final_key)
                    self._asr_reserved_final_key = None
                    await self._handle_independent_asr_error(
                        epoch,
                        provider,
                        status_code="ASR_ENDPOINTING_FAILED",
                        expected_identity=endpoint_identity,
                    )
                    return
                self._asr_provider_candidate_fence = provider_fence
            lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
            self._asr_sealed_turn_token = self._capture_transport_token(lifecycle)
            self._asr_turn_endpointed_at = time.monotonic()
            self._asr_last_turn_endpointed_at = self._asr_turn_endpointed_at
            # 与 Core 侧 record.turn_id 同构（asr_runtime.py 的
            # external_turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"），
            # 好让冻结时能直接判"这个封口是不是这条 record 的"。
            self._asr_last_turn_endpointed_key = (
                f"asr-{turn_token.ingress.session_epoch}-{turn_token.turn_id}"
            )
            self._schedule_provider_final_watchdog(
                epoch,
                lifecycle,
                self._asr_sealed_turn_token,
            )
            identity = self._capture_runtime_identity(
                ingress_token=turn_token.ingress,
                turn_token=turn_token,
            )
            await self._send_asr_lifecycle_state(
                VoiceLifecycleState.DRAINING,
                provider=provider,
                session_epoch=epoch,
                expected_identity=identity,
            )

    async def _activate_pending_independent_turn(self, epoch: int) -> None:
        """Start the pending turn after the previous final completes."""

        if epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if lifecycle is None or not lifecycle.has_pending_turn:
            # has_pending_turn 还要求 pending buffer 里真有音频：speech 先到、或者
            # 对应 PCM 被丢弃时会走到这里。不清的话这个 onset 会被**下一个**真实
            # pending turn 复用，把那一轮的起点提前到上一轮，视觉帧绑错回合。
            self._asr_pending_turn_onset_at = None
            if lifecycle is not None:
                lifecycle.discard_unconfirmed_pending_audio()
            return
        if lifecycle.snapshot.state is not VoiceLifecycleState.WARM_IDLE:
            lifecycle.discard_pending_turn()
            self._asr_pending_turn_onset_at = None
            self._asr_pending_detector_candidate = None
            return
        payload = lifecycle.begin_pending_turn()
        # begin_pending_turn() 内部完成 SPEECH_CONFIRMED 迁移（lifecycle.py），是第
        # 五个迁移点 —— 之前给另外四处补 onset 打点时漏了它，因为守卫只扫本模块的
        # 字面量。不补的话 _asr_turn_onset_at 还留着**上一轮**的值（它只在
        # close/abort/error 才清），Core 会拿上一轮的 onset 当本回合 started_at，于是
        # 上一轮保留的封口时刻反过来成了本回合的截止点，本回合之后拍的每一帧都被
        # accepts() 拒掉 —— 整轮退化成纯文本。
        self._asr_turn_onset_at = (
            self._asr_pending_turn_onset_at
            if self._asr_pending_turn_onset_at is not None
            else time.monotonic()
        )
        self._asr_pending_turn_onset_at = None
        if not payload:
            return
        turn_token = self._capture_turn_token(lifecycle)
        pending_candidate = self._asr_pending_detector_candidate
        self._asr_pending_detector_candidate = None
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        self._asr_turn_audio_started_at = time.monotonic()
        self._asr_first_partial_recorded = False
        if not await self._ensure_smart_turn_ready(lifecycle, epoch):
            return
        if not self._runtime_identity_matches(identity):
            return
        delivered = await self._send_asr_lifecycle_state(
            VoiceLifecycleState.ACTIVE,
            provider=identity.provider or "unknown",
            session_epoch=epoch,
            expected_identity=identity,
        )
        if not delivered:
            return
        await self._prepare_independent_asr_turn(epoch)
        if not self._runtime_identity_matches(identity):
            return
        asr_session = identity.session
        if asr_session is None or not getattr(asr_session, "is_ready", True):
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                expected_identity=identity,
            )
            return
        detector = identity.detector
        if not self._asr_endpointing_ready(lifecycle, detector, turn_token):
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
            )
            return
        if pending_candidate is not None:
            assert detector is not None
            bound = await detector.bind_candidate(pending_candidate, turn_token)
            if not self._runtime_identity_matches(identity):
                return
            if bound is None:
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )
                return
        elif not self._runtime_identity_matches(identity):
            return
        if not self._activate_asr_audio_dispatcher(
            lifecycle,
            turn_token,
            buffered_pcm16=payload,
        ):
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_AUDIO_ORDERING_FAILED",
                expected_identity=identity,
            )
            return
        if not self._runtime_identity_matches(identity):
            return
        self._asr_received_audio = True
        self._asr_audio_bytes += len(payload)

    async def _send_independent_asr_preview(self, text: str, epoch: int) -> None:
        """Send display-only ASR partials without writing conversation history."""

        clean = str(text or "").strip()
        if not clean or epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        turn_token = self._asr_partial_turn_token
        if (
            lifecycle is None
            or turn_token is None
            or not self._asr_turn_prepared
            or lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
            or not self._ingress_token_matches(turn_token.ingress)
            or lifecycle.snapshot.turn_id != turn_token.turn_id
            or self._asr_audio_dispatcher.active_turn != turn_token
        ):
            return
        if (
            not self._asr_first_partial_recorded
            and self._asr_turn_audio_started_at is not None
        ):
            lifecycle.metrics.first_partial_latency_ms = int(
                (time.monotonic() - self._asr_turn_audio_started_at) * 1_000
            )
            self._asr_first_partial_recorded = True
        try:
            await self._callbacks.on_partial(
                VoicePartialEvent(turn_token=turn_token, text=clean)
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR preview delivery failed",
                self.display_name,
            )

    async def _handle_independent_asr_final(
        self,
        text: str,
        epoch: int,
        provider: str,
    ) -> None:
        clean = str(text or "").strip()
        if epoch != self._asr_session_epoch:
            return

        lifecycle_ref: VoiceInputLifecycleController | None = None
        detector_ref: DetectorRuntime | None = None
        has_pending_turn = False
        envelope: TranscriptEnvelope | None = None
        accepted_turn_token: VoiceTurnToken | None = None
        transcript_dispatcher: TranscriptDispatcher | None = None
        final_key: FinalKey | None = None
        final_identity: _AsrRuntimeIdentity | None = None
        ordering_failure_identity: _AsrRuntimeIdentity | None = None
        provider_failure_identity: _AsrRuntimeIdentity | None = None
        successor_present = False
        async with self._asr_final_lock:
            if epoch != self._asr_session_epoch:
                return
            if self._asr_candidate_rejection is not None:
                return
            asr_session = self._asr_session
            if asr_session is not None:
                # Segmented sessions advance the cumulative wire counter at
                # the seal-time physical-segment commit, which runs after the
                # dispatcher's last per-chunk sample. Re-sample here so the
                # sealed turn's provider wire audio reaches lifecycle metrics;
                # the monotonic delta keeps streaming providers unaffected.
                self._sync_provider_wire_metrics(asr_session)
            lifecycle_ref = self._asr_lifecycle
            sealed_token = self._asr_sealed_turn_token
            if (
                lifecycle_ref is None
                or sealed_token is None
                or lifecycle_ref.snapshot.state is not VoiceLifecycleState.DRAINING
                or not self._transport_token_matches(sealed_token, lifecycle_ref)
            ):
                return
            final_key = FinalKey.from_turn(sealed_token.turn)
            if final_key in self._asr_accepted_final_keys:
                return
            transcript_dispatcher = self._asr_transcript_dispatcher
            if not transcript_dispatcher.try_reserve(final_key):
                ordering_failure_identity = self._capture_runtime_identity(
                    ingress_token=sealed_token.turn.ingress,
                    turn_token=sealed_token.turn,
                )
            if ordering_failure_identity is None:
                has_pending_turn = lifecycle_ref.has_pending_turn
                detector_ref = self._asr_detector
                if not _uses_smart_turn_endpointing(lifecycle_ref.provider_policy):
                    provider_fence = self._asr_provider_candidate_fence
                    if provider_fence is None or detector_ref is None:
                        provider_failure_identity = self._capture_runtime_identity(
                            ingress_token=sealed_token.turn.ingress,
                            turn_token=sealed_token.turn,
                        )
                    else:
                        try:
                            completion = (
                                await detector_ref.complete_provider_candidate(
                                    provider_fence
                                )
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            completion = None
                            logger.warning(
                                "[%s] provider candidate completion failed",
                                self.display_name,
                            )
                        completion_identity = self._capture_runtime_identity(
                            ingress_token=sealed_token.turn.ingress,
                            turn_token=sealed_token.turn,
                        )
                        if (
                            self._asr_lifecycle is not lifecycle_ref
                            or self._asr_detector is not detector_ref
                            or not self._runtime_identity_matches(
                                completion_identity
                            )
                        ):
                            transcript_dispatcher.release(final_key)
                            return
                        if completion is None:
                            provider_failure_identity = completion_identity
                        else:
                            successor_present = completion
                            self._asr_provider_candidate_fence = None
                if provider_failure_identity is None:
                    if not self._accept_final_key(final_key):
                        return
                    if self._asr_turn_endpointed_at is not None:
                        lifecycle_ref.metrics.final_latency_ms = int(
                            (time.monotonic() - self._asr_turn_endpointed_at) * 1_000
                        )
                    accepted_turn_token = sealed_token.turn
                    if self._asr_partial_turn_token == accepted_turn_token:
                        self._asr_partial_turn_token = None
                    lifecycle_ref.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
                    self._asr_turn_prepared = False
                    self._asr_received_audio = False
                    self._asr_sealed_turn_token = None
                    self._asr_provider_candidate_fence = None
                    self._asr_turn_endpointed_at = None
                    self._asr_reserved_final_key = None
                    watchdog = self._asr_final_watchdog_task
                    self._asr_final_watchdog_task = None
                    if watchdog is not None and watchdog is not asyncio.current_task():
                        watchdog.cancel()
                    envelope = TranscriptEnvelope(
                        turn_token=sealed_token.turn,
                        provider=provider,
                        text=clean,
                    )
                    if not clean:
                        lifecycle_ref.metrics.false_wake_count += 1
                    if successor_present and not has_pending_turn:
                        lifecycle_ref.preserve_unconfirmed_pending_audio()
                    if not has_pending_turn:
                        self._schedule_transport_warm_expiry(
                            epoch,
                            expected_state=VoiceLifecycleState.WARM_IDLE,
                        )
                    final_identity = self._capture_runtime_identity(
                        ingress_token=sealed_token.turn.ingress,
                        turn_token=sealed_token.turn,
                    )

        if ordering_failure_identity is not None:
            await self._handle_independent_asr_error(
                ordering_failure_identity.session_epoch,
                ordering_failure_identity.provider or provider,
                status_code="ASR_AUDIO_ORDERING_FAILED",
                expected_identity=ordering_failure_identity,
            )
            return

        if provider_failure_identity is not None:
            assert transcript_dispatcher is not None
            assert final_key is not None
            transcript_dispatcher.release(final_key)
            await self._handle_independent_asr_error(
                provider_failure_identity.session_epoch,
                provider_failure_identity.provider or provider,
                status_code="ASR_ENDPOINTING_FAILED",
                expected_identity=provider_failure_identity,
            )
            return

        assert lifecycle_ref is not None
        assert accepted_turn_token is not None
        assert transcript_dispatcher is not None
        assert final_key is not None
        assert final_identity is not None
        lease = self._asr_smart_turn_lease
        if lease is not None and lease.token == accepted_turn_token:
            self._asr_smart_turn_lease = None
            try:
                await lease.release()
            except Exception:
                # The final is already accepted; a failed release must not
                # skip transcript delivery or pending-turn activation below.
                logger.warning(
                    "[%s] SmartTurn lease release failed after accepted final",
                    self.display_name,
                )
            if not self._runtime_identity_matches(final_identity):
                transcript_dispatcher.release(final_key)
                # The accepted final can no longer be delivered, so release
                # the Core-side pause keyed to this turn.
                await self._notify_asr_turn_abandoned(accepted_turn_token)
                return
        elif not self._runtime_identity_matches(final_identity):
            transcript_dispatcher.release(final_key)
            await self._notify_asr_turn_abandoned(accepted_turn_token)
            return
        if envelope is not None:
            try:
                transcript_dispatcher.submit(envelope)
            except RuntimeError:
                await self._handle_independent_asr_error(
                    final_identity.session_epoch,
                    final_identity.provider or provider,
                    status_code="ASR_AUDIO_ORDERING_FAILED",
                    expected_identity=final_identity,
                )
                return
        delivered = await self._send_asr_lifecycle_state(
            VoiceLifecycleState.WARM_IDLE,
            provider=provider,
            session_epoch=epoch,
            expected_identity=final_identity,
        )
        if not delivered:
            return

        await self._activate_pending_independent_turn(epoch)
        overlap_token = self._asr_overlap_onset_token
        overlap_onset_at = self._asr_overlap_onset_at
        if overlap_token is not None and self._asr_overlap_completed_turns > 0:
            # 单槽 onset 和已兑换成 credit 的旧周期同时存在时，credit 排在前面。
            # provider 的 FIFO 会先送那些周期的 endpoint/final，再轮到这个还活着的
            # onset。此刻**直接**重放的话，先到的那个 endpoint 会把这条 onset 建出
            # 来的记录封掉 —— 旧那轮的 transcript 配上新那轮的视觉窗口；而新那轮的
            # endpoint 再没有 credit 可兑，它的 final 会被整条丢掉。
            #
            # 所以两边都不动：onset 留在单槽里，credit 留在队列里各自按 FIFO 兑付。
            # credit 兑完之后，那条 final 走到这里时 _asr_overlap_completed_turns
            # 已经归零，重放才轮到这个 onset。身份轮换（硬静音 / 中止 / 路由切换）
            # 会由 _reset_asr_turn_state 把单槽和队列一起清掉，不会留下半边。
            overlap_token = None
        else:
            self._asr_overlap_onset_token = None
            self._asr_overlap_onset_at = None
        if (
            detector_ref is not None
            and self._asr_lifecycle is lifecycle_ref
            and self._asr_detector is detector_ref
        ):
            identity = self._capture_runtime_identity(
                ingress_token=self._asr_current_ingress_token,
            )
            try:
                await detector_ref.release_deferred_turn()
            except Exception:
                if not self._runtime_identity_matches(identity):
                    return
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )
                return
            if not self._runtime_identity_matches(identity):
                return
        if (
            overlap_token is None
            or self._asr_lifecycle is not lifecycle_ref
            or lifecycle_ref.snapshot.state is not VoiceLifecycleState.WARM_IDLE
            or overlap_token != self._asr_current_ingress_token
            or not self._ingress_token_matches(overlap_token)
        ):
            return
        # An onset recorded while the finished turn was still ACTIVE means the
        # provider had already ended that turn but its ordered endpoint was
        # delayed behind this final. Replay the onset now that the lifecycle
        # reached WARM_IDLE so the next turn's ordered endpoint and final find
        # an ACTIVE, prepared turn instead of discarding the utterance.
        #
        # 把真实开口时刻交给重放 —— 和 credit 兑付那条路一样。少了这一步，这条
        # **直接**重放会用当前时钟当后继发声的起点，把「上一轮排空 + 延迟 final」
        # 整段算进「开口之后」，用户重新开口以来拍的帧全被排除。
        _lent_pending_onset = False
        if (
            overlap_onset_at is not None
            and self._asr_pending_speech_onset_at is None
        ):
            self._asr_pending_speech_onset_at = overlap_onset_at
            _lent_pending_onset = True
        pending_before = self._asr_pending_speech_confirmed
        await self._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_RESUMED,
            epoch,
        )
        if (
            epoch != self._asr_session_epoch
            or self._asr_lifecycle is not lifecycle_ref
        ):
            return
        if (
            not pending_before
            and self._asr_pending_speech_confirmed
            and lifecycle_ref.snapshot.state is VoiceLifecycleState.PREWARMING
        ):
            # 与 credit 兑付那条路同一处置（见 _handle_independent_asr_endpoint）：
            # 重放被"传输未就绪"挡住停在 PREWARMING 时，就地补完确认。
            #
            # 光留住 onset 不够。这一轮的 provider endpoint / final 已经排在有序
            # FIFO 里正要到达，而 PREWARMING 封不了口、_handle_independent_asr_final
            # 又要求 DRAINING —— 那条 final 会被整条丢弃，且没有任何 watchdog 兜底。
            # 等重连也救不回来：重连换新 session，老队列里的回调全被
            # is_adopted_candidate() 丢掉（_restart_transport / _close_transport_only
            # 都是先把 _asr_session 置 None 再 close）。能走到这里说明老 session
            # 还被认领着，也就是重连还没开始。
            #
            # 这一轮不需要传输：它的音频早在上一轮 ACTIVE 时就已经过线。
            lifecycle_ref.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
            self._asr_turn_onset_at = (
                self._asr_pending_speech_onset_at
                if self._asr_pending_speech_onset_at is not None
                else time.monotonic()
            )
            # 与 credit 兑付那条路同序（见 _handle_independent_asr_endpoint）：
            # 确认一落地就清挂起状态，真实开口时刻已经装进 _asr_turn_onset_at。
            self._asr_pending_speech_confirmed = False
            self._asr_pending_speech_onset_at = None
            self._asr_turn_audio_started_at = time.monotonic()
            self._asr_first_partial_recorded = False
            confirm_identity = self._capture_runtime_identity(
                ingress_token=self._asr_current_ingress_token,
            )
            delivered = await self._send_asr_lifecycle_state(
                VoiceLifecycleState.ACTIVE,
                provider=self._asr_provider or "unknown",
                session_epoch=epoch,
                expected_identity=confirm_identity,
            )
            if (
                not delivered
                or epoch != self._asr_session_epoch
                or self._asr_lifecycle is not lifecycle_ref
            ):
                # delivered 为假只可能是运行时身份漂移：_send_asr_lifecycle_state
                # 吞掉回调异常之后，返回的就是 _runtime_identity_matches。而
                # _restart_transport / _close_transport_only 换掉 _asr_session 与
                # transport_generation 时都不走 _reset_asr_turn_state，所以这里留下
                # 的挂起状态没人回收：上面 transition(SPEECH_CONFIRMED) 已经把它兑付
                # 进 _asr_turn_onset_at，两个兑付点又都以 PREWARMING 为闸、ACTIVE 下
                # 一律跳过。残留下去会被后面某个不相干的回合当成自己的开口时刻，还会
                # 把补偿门 not pending_before 恒假化，让重叠补偿此后静默失效。
                return
        if lifecycle_ref.snapshot.state is not VoiceLifecycleState.ACTIVE:
            # 没醒起来（Smart Turn 租约没就绪，或 lifecycle 广播没送达）。借出去的
            # onset 必须收回：留着的话，后面某个**不相干**的发声会把这个陈旧时刻
            # 当成自己的起点，视觉所有权窗口整个错位——要么把无关的帧折进来，要么
            # 把本轮真正的帧判成过期。
            #
            # ⚠️ 只有在**没有**挂起确认时才收回，判据与 credit 兑付那条路一字不差：
            # session 未就绪时 _handle_independent_asr_activity 会停在 PREWARMING、
            # 置上 _asr_pending_speech_confirmed 并**特意留着**这个 onset 等后续的
            # 确认去取；此时收回等于让那次确认退回用新的 detected_at，把用户真实
            # 开口以来的帧全排除掉。
            if (
                _lent_pending_onset
                and not self._asr_pending_speech_confirmed
                and self._asr_pending_speech_onset_at == overlap_onset_at
            ):
                self._asr_pending_speech_onset_at = None

    async def _dispatch_asr_transcript_envelope(
        self,
        envelope: TranscriptEnvelope,
    ) -> None:
        ingress_token = envelope.turn_token.ingress
        if not self._ingress_token_matches(ingress_token):
            # The envelope was accepted before the audio generation moved on,
            # so neither on_final nor a teardown path will run for this turn.
            # Release the Core-side pause keyed to it instead of leaking the
            # pause until the next turn.
            await self._notify_asr_turn_abandoned(envelope.turn_token)
            return
        identity = self._capture_runtime_identity(
            ingress_token=ingress_token,
            turn_token=envelope.turn_token,
        )
        try:
            await self._callbacks.on_final(
                VoiceTranscriptEvent(
                    turn_token=envelope.turn_token,
                    provider=envelope.provider,
                    text=envelope.text,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._send_asr_status(
                "ASR_INDEPENDENT_INJECTION_FAILED",
                envelope.provider,
                session_epoch=ingress_token.session_epoch,
                expected_identity=identity,
            )

    async def _handle_independent_asr_error(
        self,
        epoch: int,
        provider: str,
        *,
        status_code: str = "ASR_INDEPENDENT_FAILED",
        expected_identity: _AsrRuntimeIdentity | None = None,
    ) -> None:
        if epoch != self._asr_session_epoch or (
            expected_identity is not None
            and not self._runtime_identity_matches(expected_identity)
        ):
            return
        # The provider callback that reported failure must not be allowed to
        # deliver a queued final into the surviving Omni session.
        self._asr_session_epoch += 1
        failure_epoch = self._asr_session_epoch
        self._asr_audio_generation += 1
        transcript_dispatcher = self._asr_transcript_dispatcher
        detector_dispatcher = self._asr_detector_dispatcher
        audio_dispatcher = self._asr_audio_dispatcher
        transcript_dispatcher.invalidate_all()
        detector_dispatcher.invalidate_all()
        audio_dispatcher.abort()
        self._asr_transcript_dispatcher = TranscriptDispatcher(
            self._dispatch_asr_transcript_envelope,
        )
        self._asr_detector_dispatcher = AsrDetectorDispatcher(
            self._dispatch_asr_detector_event,
            on_failure=self._handle_asr_detector_dispatcher_failure,
        )
        self._asr_audio_dispatcher = AsrAudioDispatcher(
            validator=self._asr_audio_command_is_valid,
            on_wire_audio=self._record_asr_dispatcher_wire_audio,
            on_failure=self._handle_asr_audio_dispatcher_failure,
        )
        asr_session, self._asr_session = self._asr_session, None
        lifecycle, self._asr_lifecycle = self._asr_lifecycle, None
        detector, self._asr_detector = self._asr_detector, None
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
        self._asr_provider = None
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._reset_asr_turn_state()
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        if lifecycle is not None:
            lifecycle.stop()
        if detector is not None:
            task = asyncio.create_task(detector.close())
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        if lease is not None:
            task = asyncio.create_task(lease.release())
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        if asr_session is not None:
            task = asyncio.create_task(self._close_asr_session(asr_session))
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        failure_identity = self._capture_runtime_identity()
        try:
            delivered = await self._send_asr_lifecycle_state(
                VoiceLifecycleState.BLOCKED,
                provider=provider,
                session_epoch=failure_epoch,
                expected_identity=failure_identity,
            )
            if not delivered or not self._runtime_identity_matches(failure_identity):
                return
            try:
                await self._callbacks.on_failure(
                    AsrFailureEvent(
                        code=status_code,
                        provider=provider,
                        session_epoch=failure_epoch,
                    )
                )
            except Exception:
                logger.debug(
                    "[%s] independent ASR failure callback failed",
                    self.display_name,
                )
            if not self._runtime_identity_matches(failure_identity):
                return
            await self._send_asr_status(
                status_code,
                provider,
                session_epoch=failure_epoch,
                expected_identity=failure_identity,
            )
        finally:
            # A dispatcher can report its own failure from inside its worker.
            # Let lifecycle/failure/status delivery finish before closing that
            # worker, otherwise close() can cancel the authoritative callback.
            for dispatcher in (detector_dispatcher, audio_dispatcher):
                task = asyncio.create_task(dispatcher.close())
                self._asr_close_tasks.add(task)
                task.add_done_callback(self._asr_close_tasks.discard)

    async def _close_asr_session(self, asr_session: Any) -> None:
        try:
            await asr_session.close()
        except Exception:
            logger.warning(
                "[%s] independent ASR background close failed",
                self.display_name,
            )

    async def _send_asr_status(
        self,
        code: str,
        provider: str,
        *,
        session_epoch: int,
        expected_identity: _AsrRuntimeIdentity,
    ) -> bool:
        if (
            session_epoch != expected_identity.session_epoch
            or not self._runtime_identity_matches(expected_identity)
        ):
            return False
        try:
            await self._callbacks.on_status(
                AsrStatusEvent(
                    code=code,
                    provider=provider,
                    session_epoch=session_epoch,
                )
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR status delivery failed",
                self.display_name,
            )
        return self._runtime_identity_matches(expected_identity)

    async def _send_asr_lifecycle_state(
        self,
        state: VoiceLifecycleState,
        *,
        provider: str,
        session_epoch: int,
        expected_identity: _AsrRuntimeIdentity,
    ) -> bool:
        if (
            session_epoch != expected_identity.session_epoch
            or not self._runtime_identity_matches(expected_identity)
        ):
            return False
        try:
            await self._callbacks.on_lifecycle(
                AsrLifecycleNotification(
                    state=state.value,
                    provider=provider,
                    session_epoch=session_epoch,
                )
            )
        except Exception:
            logger.debug(
                "[%s] ASR lifecycle status delivery failed",
                self.display_name,
            )
        return self._runtime_identity_matches(expected_identity)
