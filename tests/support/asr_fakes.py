import ast
import asyncio
import hashlib
import inspect
import json
import logging
import textwrap
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch
import pytest
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.core import LLMSessionManager
from main_logic.core.asr_runtime import (
    AsrRuntimeMixin,
    _HotSwapAudioFrame,
    _ONSET_TRUST_WINDOW_S,
)
from main_logic.core.multimodal_turn import (
    _MAX_LIVE_TURN_RECORDS,
    _MAX_PRERECORD_VISUAL_VALIDATIONS,
)
from main_logic.asr_client.runtime import (
    AsrRuntimeCallbacks,
    AsrStartResult,
    AsrStartStatus,
    IndependentAsrRuntime,
)
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult, DetectorRuntime
from main_logic.voice_input import VoiceInputDispatchResult
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_input.consumers import CoreChatTurnContext
from main_logic.asr_client.lifecycle import (
    AudioDisposition,
    VoiceLifecycleConfig,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceTurnToken,
    VoiceRouteMode,
)
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_identity_service.activation_runtime import (
    VoiceSessionActivationRuntime,
)
from main_logic.voice_identity_service.activation_scoring import (
    ActivationScoreResult,
    ActivationScoreStatus,
)
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitResult,
    AsrSubmitStatus,
    SpeechActivityEvent,
    VoiceIngressToken,
    VoicePartialEvent,
    VoiceTranscriptEvent,
)
from main_logic.voice_turn.contracts import EvaluationStatus, TurnDecision
from main_logic.asr_client.endpointing.coordinator import CoordinatorState
from main_logic.asr_client.endpointing.detector import (
    BoundDetectorTurn,
    CoreDetectorEventEnvelope,
    DetectorCandidateKey,
    DetectorIngressIdentity,
    ProviderCandidateFence,
    DetectorRuntimeEvent,
    DetectorTurnEvent,
    DetectorSubmitResult,
    DetectorSubmitStatus,
)
import main_logic.core.asr_runtime as core_asr_runtime_module
import main_logic.core as core_module
import main_logic.voice_turn.audio_input as audio_input_module
from utils import preferences


class _TestSmartTurnLease:
    """Minimal endpointing lease used by fake detectors in runtime tests."""

    def __init__(self, token) -> None:
        self.token = token
        self.released = False

    async def release(self) -> None:
        self.released = True


class _Runtime(AsrRuntimeMixin):
    def __init__(self) -> None:
        self._init_asr_runtime_state()
        self._voice_lease_synchronized = True
        self._voice_lease_owner = "core"
        self._voice_input_suppressed = False
        self.lanlan_name = "Test"
        self.session = type("Omni", (), {})()
        self.session.create_response = AsyncMock()
        self.session.handle_interruption = AsyncMock()
        self.handle_new_message = AsyncMock()
        self.handle_input_transcript = AsyncMock(return_value=True)
        self.send_status = AsyncMock()

    def __getattr__(self, name: str):
        component = self.__dict__.get("_asr_runtime")
        if component is not None and hasattr(component, name):
            return getattr(component, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        component = self.__dict__.get("_asr_runtime")
        if name in {
            "_asr_route_mode",
            # Keep the operation generation on the instance so reads observe
            # bumps, matching production (writes routed to the component
            # would freeze it at its initial value for every read).
            "_asr_route_operation_generation",
            "_microphone_route_generation",
            "_independent_asr_provider",
            "_independent_asr_route_key",
            "_voice_input_audio_pipeline",
        }:
            object.__setattr__(self, name, value)
            return
        if component is not None and (
            name.startswith("_asr_")
            or name
            in {
                "_voice_input_resource_optimization_enabled",
            }
        ):
            setattr(component, name, value)
            if name == "_asr_lifecycle" and value is not None:
                component._asr_current_ingress_token = self._capture_ingress_token()
            return
        object.__setattr__(self, name, value)
def _selection(provider_key: str, endpointing_mode: str = "manual"):
    return type(
        "Selection",
        (),
        {
            "provider_key": provider_key,
            "endpointing_mode": endpointing_mode,
            "soniox_region": None,
        },
    )()
def _install_ready_lifecycle(
    runtime: _Runtime,
    provider: str = "qwen",
) -> None:
    if runtime._asr_session is None:
        runtime._asr_session = type("Asr", (), {"is_ready": True})()
    runtime._asr_provider = provider
    runtime._set_microphone_route("independent")
    endpointing_mode = "provider" if provider == "openai" else "manual"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, endpointing_mode),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
class _CoreActivationScorer:
    profile_generation = "profile"
    scorer_generation = 1

    def __init__(self, *, similarity: float = 0.8) -> None:
        self.similarity = similarity
        self.calls = 0
        self.closed = False

    async def prepare(self) -> ActivationScoreStatus:
        return ActivationScoreStatus.READY

    async def score(self, identity, pcm16: bytes, *, sample_rate_hz: int):
        assert pcm16
        assert sample_rate_hz == 16_000
        self.calls += 1
        return ActivationScoreResult(
            identity,
            ActivationScoreStatus.READY,
            self.similarity,
        )

    async def close(self) -> None:
        self.closed = True
class _CoreActivationFactory:
    activation_generation = "profile"

    def __init__(self, *, similarity: float = 0.8) -> None:
        self.similarity = similarity
        self.runtimes: list[VoiceSessionActivationRuntime] = []
        self.scorers: list[_CoreActivationScorer] = []
        self.closed = False

    def create(self, generation, output, *, status_callback=None):
        scorer = _CoreActivationScorer(similarity=self.similarity)
        scorer.profile_generation = self.activation_generation
        runtime = VoiceSessionActivationRuntime(
            generation,
            scorer,  # type: ignore[arg-type]
            output,
            status_callback=status_callback,
        )
        self.scorers.append(scorer)
        self.runtimes.append(runtime)
        return runtime

    def close(self) -> None:
        self.closed = True

class _ReadyDetector:
    def __init__(self, feed_result: DetectorFeedResult | None = None) -> None:
        self.detector_epoch = 1
        self._token = None
        self._feed_result = feed_result or DetectorFeedResult((), True)
        self.bind_candidate = AsyncMock(return_value=object())
        self.reset = AsyncMock(side_effect=self._reset)
        self.close = AsyncMock()
        self.release_deferred_turn = AsyncMock()
        self.seal_provider_candidate = AsyncMock(
            return_value=ProviderCandidateFence(1, 0, 0)
        )
        self.complete_provider_candidate = AsyncMock(return_value=False)
        self.discard_provider_successor = AsyncMock(return_value=True)
        self.observe_provider_audio = MagicMock()

    async def prepare_endpointing(self, token):
        self._token = token
        return _TestSmartTurnLease(token)

    def endpointing_ready(self, token) -> bool:
        return self._token == token

    async def feed(self, _pcm16: bytes, **_kwargs) -> DetectorFeedResult:
        return self._feed_result

    async def _reset(self) -> None:
        self._token = None

class _FailedSmartTurnDetector(_ReadyDetector):
    async def prepare_endpointing(self, token):
        self._token = None
        return None

    def endpointing_ready(self, token) -> bool:
        return False

class _QueuedSmartTurnDetector(_ReadyDetector):
    def __init__(self) -> None:
        super().__init__()
        self.queued_audio_ms = 0
        self.smart_turn_evaluation_ms = 0
        self.smart_turn_stale_result_count = 0
        self.smart_turn_coalesced_evaluation_count = 0
        self.force_speech_started = AsyncMock(return_value=True)
        self._sequence_no = 0

    async def submit_audio(
        self,
        _pcm16: bytes,
        *,
        ingress_token,
        **_kwargs,
    ) -> DetectorSubmitResult:
        self._sequence_no += 1
        return DetectorSubmitResult(
            status=DetectorSubmitStatus.ACCEPTED,
            throttle_available=False,
            endpointing_available=True,
            identity=DetectorIngressIdentity(
                ingress_token=ingress_token,
                detector_epoch=1,
                sequence_no=self._sequence_no,
            ),
            candidate=DetectorCandidateKey(1, 0),
        )
