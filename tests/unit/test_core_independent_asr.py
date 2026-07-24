import asyncio
import hashlib
import inspect
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from main_logic.core import LLMSessionManager
from main_logic.core.asr_runtime import AsrRuntimeMixin
from main_logic.asr_client.detector_runtime import (
    DetectorFeedResult,
    DetectorRuntime,
    SmartTurnReadiness,
)
from main_logic.asr_client.detector import (
    AsrDetectorDispatcher,
    BoundDetectorTurn,
    CoreDetectorEventEnvelope,
    DetectorCandidateKey,
    DetectorIngressIdentity,
    DetectorPrewarmEvent,
    DetectorTransportPrewarmEvent,
    ProviderCandidateFence,
    DetectorSubmitResult,
    DetectorSubmitStatus,
)
from main_logic.asr_client.lifecycle import (
    AudioDisposition,
    VoiceIngressToken,
    VoiceLifecycleConfig,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
)
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_identity.contracts import (
    SpeakerShadowConfig,
    SpeakerShadowObservation,
)
from main_logic.asr_client.throttle_policy import ThrottleAction
from main_logic.voice_turn.audio_input import VoiceInputAudioPipeline
from main_logic.voice_turn.contracts import (
    AsrSubmitResult,
    AsrSubmitStatus,
    SpeechActivityEvent,
    VoiceTranscriptEvent,
)
from main_logic.voice_turn.contracts import EvaluationStatus, TurnDecision
from main_logic.voice_turn.coordinator import CoordinatorState
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.registry import (
    get_voice_identity_profile_registry,
)
from main_logic.voice_identity.runtime import SpeakerShadowRuntime, VoiceIdentitySession
from main_logic.voice_input import BuiltinVoiceInputConsumer
from main_logic.voice_input.consumers import game as game_consumer_module
import main_logic.core as core_module
from utils import preferences


pytestmark = pytest.mark.asyncio


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


async def test_speaker_shadow_factory_is_private_default_off_and_fail_open() -> None:
    runtime = _Runtime()

    assert runtime._speaker_shadow_factory is None
    assert await runtime._create_speaker_shadow() is None

    sentinel = object()
    factory = MagicMock(return_value=sentinel)
    runtime._asr_runtime._speaker_shadow_factory = factory
    assert await runtime._create_speaker_shadow() is sentinel
    factory.assert_called_once_with()

    runtime._asr_runtime._speaker_shadow_factory = MagicMock(
        side_effect=RuntimeError("missing model")
    )
    assert await runtime._create_speaker_shadow() is None


async def test_active_speaker_profile_uses_formal_core_composition_entry() -> None:
    runtime = _Runtime()
    profile = SpeakerProfile(
        np.eye(4, dtype=np.float32)[0],
        profile_revision=1,
        model_id="test-model",
        model_revision="v1",
        embedding_dimension=4,
    )
    identity_session = type("IdentitySession", (), {})()
    identity_session.set_profile = AsyncMock()
    identity_session.create_runtime = MagicMock()
    identity_session.close = AsyncMock()
    runtime._voice_identity_session = identity_session
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock()

    assert await runtime.set_active_speaker_profile(profile) == 1

    identity_session.set_profile.assert_awaited_once_with(profile)
    assert runtime._speaker_shadow_factory == identity_session.create_runtime
    runtime._asr_runtime.set_speaker_verifier_factory.assert_awaited_once_with(
        identity_session.create_runtime
    )

    assert await runtime.set_active_speaker_profile(None) is None

    identity_session.set_profile.assert_awaited_with(None)
    assert runtime._speaker_shadow_factory is None
    runtime._asr_runtime.set_speaker_verifier_factory.assert_awaited_with(None)


async def test_enrollment_lease_blocks_native_and_independent_pcm_and_clears_state() -> (
    None
):
    registry = get_voice_identity_profile_registry()
    registry.close()
    registry.begin_enrollment("enrollment-a")
    runtime = _Runtime()
    runtime._clear_audio_stream_queue = MagicMock()
    runtime.hot_swap_audio_cache = [b"old-pcm"]
    runtime._asr_runtime.abort = AsyncMock()

    try:
        assert runtime._voice_input_accepts_pcm() is False

        await runtime.prepare_voice_identity_enrollment("enrollment-a")

        runtime._clear_audio_stream_queue.assert_called_once_with(
            "voice_identity_enrollment"
        )
        assert runtime.hot_swap_audio_cache == []
        runtime._asr_runtime.abort.assert_awaited_once_with(
            "voice_identity_enrollment"
        )
    finally:
        registry.end_enrollment("enrollment-a")
        registry.close()

    assert runtime._voice_input_accepts_pcm() is True


async def test_registered_profile_is_synced_without_reapplying_same_revision() -> None:
    registry = get_voice_identity_profile_registry()
    registry.close()
    profile = SpeakerProfile(
        np.eye(4, dtype=np.float32)[0],
        profile_revision=7,
        model_id="test-model",
        model_revision="v1",
        embedding_dimension=4,
    )
    registry.install_profile(profile)
    runtime = _Runtime()
    runtime.set_active_speaker_profile = AsyncMock(return_value=7)

    try:
        await runtime._sync_registered_speaker_profile()
        await runtime._sync_registered_speaker_profile()
    finally:
        profile.close()
        registry.close()

    runtime.set_active_speaker_profile.assert_awaited_once()
    synced = runtime.set_active_speaker_profile.await_args.args[0]
    assert synced.profile_revision == 7
    synced.close()


async def test_speaker_identity_observer_has_a_formal_fail_open_port() -> None:
    runtime = _Runtime()
    first: list[SpeakerShadowObservation] = []
    second: list[SpeakerShadowObservation] = []
    observation = SpeakerShadowObservation((1, 1), 0.82, (), 1_500)

    runtime.set_speaker_identity_observer(first.append)
    await runtime._receive_speaker_shadow_observation(observation)
    runtime.set_speaker_identity_observer(second.append)
    await runtime._receive_speaker_shadow_observation(observation)

    assert first == [observation]
    assert second == [observation]

    async def failing_observer(_observation) -> None:
        raise RuntimeError("observer has no ASR authority")

    runtime.set_speaker_identity_observer(failing_observer)
    await runtime._receive_speaker_shadow_observation(observation)
    runtime.set_speaker_identity_observer(None)
    await runtime._receive_speaker_shadow_observation(observation)

    assert second == [observation]

    runtime.set_speaker_identity_observer(second.append)
    await runtime.close_voice_input_session()
    await runtime._receive_speaker_shadow_observation(observation)

    assert second == [observation]


async def test_formal_profile_entry_reaches_detector_and_emits_observation(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module
    import main_logic.core.asr_runtime as core_asr_module

    observations: list[SpeakerShadowObservation] = []
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime.set_speaker_identity_observer(observations.append)

    class _Backend:
        def load(self) -> bool:
            return True

        def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
            assert len(pcm16) >= 48_000
            assert sample_rate_hz == 16_000
            return 0.82

        def close(self) -> None:
            return None

    def build_runtime(_profile, callback):
        return SpeakerShadowRuntime(
            backend_factory=_Backend,
            config=SpeakerShadowConfig(enabled=True),
            on_observation=callback,
        )

    identity_session = VoiceIdentitySession(
        runtime_builder=build_runtime,
        on_observation=runtime._receive_speaker_shadow_observation,
    )
    create_session = MagicMock(return_value=identity_session)
    monkeypatch.setattr(
        core_asr_module,
        "create_voice_identity_session",
        create_session,
    )
    profile = SpeakerProfile(
        np.eye(4, dtype=np.float32)[0],
        profile_revision=1,
        model_id="test-model",
        model_revision="v1",
        embedding_dimension=4,
    )
    await runtime.set_active_speaker_profile(profile)

    class _Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class _Gate:
        def __init__(self) -> None:
            self.events = (SpeechActivityEvent.SPEECH_STARTED,)

        def feed(self, _pcm16: bytes):
            events, self.events = self.events, ()
            return events

        def reset(self) -> None:
            return None

    gate = _Gate()
    detectors: list[DetectorRuntime] = []

    def create_detector(**kwargs):
        detector = DetectorRuntime(vad=_Vad(), gate=gate, **kwargs)
        detectors.append(detector)
        return detector

    asr = type("Asr", (), {"is_ready": True})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voice_input_resource_optimization_enabled": False,
            }
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("qwen", "provider")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=asr),
    )
    monkeypatch.setattr(runtime_module, "DetectorRuntime", create_detector)

    await runtime._start_independent_asr_if_enabled("audio")

    assert create_session.call_count == 1
    assert len(detectors) == 1
    detector = detectors[0]
    frame = b"\x01\x00" * 1_600
    for _ in range(15):
        await detector.feed(
            frame,
            speech_probability=0.9,
            rnnoise_available=True,
        )
    gate.events = (SpeechActivityEvent.CANDIDATE_PAUSE,)
    await detector.feed(
        frame,
        speech_probability=0.1,
        rnnoise_available=True,
    )
    shadow = detector._speaker_shadow
    assert isinstance(shadow, SpeakerShadowRuntime)
    await shadow.wait_idle()

    assert len(observations) == 1
    assert observations[0].similarity == pytest.approx(0.82)
    assert observations[0].audio_ms == 1_500
    assert asr.stream_audio.await_count == 0

    await runtime.set_active_speaker_profile(None)
    assert detector._speaker_shadow is None
    await runtime.close_voice_input_session()


async def test_real_campplus_uses_formal_core_composition_without_changing_wire_pcm(
    monkeypatch,
) -> None:
    import os
    from pathlib import Path

    import main_logic.asr_client.runtime as runtime_module
    import main_logic.core.asr_runtime as core_asr_module
    from main_logic.voice_identity.campplus import (
        CampPlusAssetError,
        CampPlusEmbeddingModel,
        build_campplus_speaker_profile,
        create_campplus_voice_identity_session,
        resolve_verified_campplus_asset,
    )

    try:
        model_path = resolve_verified_campplus_asset()
    except CampPlusAssetError:
        if os.environ.get("NEKO_RELEASE_BUILD") == "1":
            raise
        pytest.skip("run prepare_speaker_model.py")
    asset_dir = Path(model_path).parent
    samples = np.arange(24_000, dtype=np.float64)
    waveform = (
        0.18 * np.sin(2 * np.pi * 173 * samples / 16_000)
        + 0.07 * np.sin(2 * np.pi * 641 * samples / 16_000)
    )
    enrollment_pcm16 = np.rint(waveform * 32767).astype("<i2").tobytes()
    immutable_frames = tuple(
        enrollment_pcm16[offset : offset + 3_200]
        for offset in range(0, len(enrollment_pcm16), 3_200)
    )
    assert len(immutable_frames) == 15

    enrollment_model = CampPlusEmbeddingModel(asset_dir=asset_dir)
    assert enrollment_model.load()
    try:
        profile = build_campplus_speaker_profile(
            enrollment_model,
            [enrollment_pcm16, enrollment_pcm16, enrollment_pcm16],
            sample_rate_hz=16_000,
            profile_revision=17,
        )
    finally:
        enrollment_model.close()

    model_state = {
        "active": 0,
        "maximum_active": 0,
        "load_count": 0,
        "unload_count": 0,
    }

    class _TrackedCampPlusModel:
        def __init__(self, *, asset_dir: Path | None = None) -> None:
            self._model = CampPlusEmbeddingModel(asset_dir=asset_dir)
            self._loaded = False

        @property
        def model_id(self) -> str:
            return self._model.model_id

        @property
        def model_revision(self) -> str:
            return self._model.model_revision

        def load(self) -> bool:
            loaded = self._model.load()
            if loaded and not self._loaded:
                self._loaded = True
                model_state["active"] += 1
                model_state["load_count"] += 1
                model_state["maximum_active"] = max(
                    model_state["maximum_active"],
                    model_state["active"],
                )
            return loaded

        def embedding_from_pcm16(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> np.ndarray:
            return self._model.embedding_from_pcm16(
                pcm16,
                sample_rate_hz=sample_rate_hz,
            )

        def snapshot(self) -> dict[str, int]:
            return self._model.snapshot()

        def close(self) -> None:
            self._model.close()
            if self._loaded:
                self._loaded = False
                model_state["active"] -= 1
                model_state["unload_count"] += 1

    def create_identity_session(*, on_observation):
        return create_campplus_voice_identity_session(
            asset_dir=asset_dir,
            on_observation=on_observation,
            model_factory=_TrackedCampPlusModel,
        )

    monkeypatch.setattr(
        core_asr_module,
        "create_voice_identity_session",
        create_identity_session,
    )

    class _Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class _Gate:
        def __init__(self) -> None:
            self.events = (SpeechActivityEvent.SPEECH_STARTED,)

        def feed(self, _pcm16: bytes):
            events, self.events = self.events, ()
            return events

        def reset(self) -> None:
            return None

    async def run(*, identity_enabled: bool) -> dict[str, object]:
        runtime = _Runtime()
        runtime.core_api_type = "qwen"
        observations: list[SpeakerShadowObservation] = []
        runtime.set_speaker_identity_observer(observations.append)
        load_count_before_activation = model_state["load_count"]
        if identity_enabled:
            assert await runtime.set_active_speaker_profile(profile) == 17
        assert model_state["load_count"] == load_count_before_activation

        wire_chunks: list[bytes] = []

        async def stream_audio(
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> None:
            assert sample_rate_hz == 16_000
            wire_chunks.append(bytes(pcm16))

        asr = type("Asr", (), {"is_ready": True})()
        asr.connect = AsyncMock()
        asr.close = AsyncMock()
        asr.stream_audio = AsyncMock(side_effect=stream_audio)
        asr.signal_user_activity_end = AsyncMock()
        gate = _Gate()
        detectors: list[DetectorRuntime] = []

        def create_detector(**kwargs):
            detector = DetectorRuntime(vad=_Vad(), gate=gate, **kwargs)
            detectors.append(detector)
            return detector

        monkeypatch.setattr(
            core_module,
            "aload_global_conversation_settings",
            AsyncMock(
                return_value={
                    "independentAsrEnabled": True,
                    "voice_input_resource_optimization_enabled": False,
                }
            ),
        )
        monkeypatch.setattr(
            runtime_module,
            "_resolve_asr_selection",
            MagicMock(return_value=_selection("qwen", "provider")),
        )
        monkeypatch.setattr(
            runtime_module,
            "_create_asr_session_from_selection",
            MagicMock(return_value=asr),
        )
        monkeypatch.setattr(runtime_module, "DetectorRuntime", create_detector)

        await runtime._start_independent_asr_if_enabled("audio")
        assert len(detectors) == 1
        detector = detectors[0]
        for frame in immutable_frames:
            assert await runtime._route_microphone_audio(
                frame,
                sample_rate_hz=16_000,
                speech_probability=0.9,
                rnnoise_available=True,
            )
        gate.events = (SpeechActivityEvent.CANDIDATE_PAUSE,)
        assert await runtime._route_microphone_audio(
            immutable_frames[-1],
            sample_rate_hz=16_000,
            speech_probability=0.1,
            rnnoise_available=True,
        )
        await runtime._asr_detector_dispatcher.wait_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        shadow = detector._speaker_shadow
        if shadow is not None:
            await shadow.wait_idle()

        continuous_pcm = b"".join(wire_chunks)
        result = {
            "provider_pcm": continuous_pcm,
            "provider_sha256": hashlib.sha256(continuous_pcm).hexdigest(),
            "provider_seals": asr.signal_user_activity_end.await_count,
            "lifecycle_state": runtime._asr_lifecycle.snapshot.state.value,
            "detector_state": (
                detector.detector_epoch,
                detector.candidate_open,
                detector._candidate_generation,
                len(detector._completion_fences),
            ),
            "similarities": tuple(
                observation.similarity for observation in observations
            ),
        }
        if observations:
            observation = observations[0]
            assert np.isfinite(observation.similarity)
            assert not hasattr(observation, "pcm16")
            assert not hasattr(observation, "embedding")

        load_count_before_disable = model_state["load_count"]
        assert await runtime.set_active_speaker_profile(None) is None
        assert detector._speaker_shadow is None
        assert model_state["load_count"] == load_count_before_disable
        runtime.set_speaker_identity_observer(None)
        await runtime.close_voice_input_session()
        assert model_state["active"] == 0
        assert runtime._asr_runtime._asr_close_tasks == set()
        assert runtime._asr_runtime._asr_detector_dispatcher._worker is None
        assert runtime._asr_runtime._asr_audio_dispatcher._worker is None
        return result

    try:
        baseline_first = await run(identity_enabled=False)
        identity_first = await run(identity_enabled=True)
        identity_second = await run(identity_enabled=True)
        baseline_second = await run(identity_enabled=False)
    finally:
        profile.close()

    isolation_keys = (
        "provider_pcm",
        "provider_sha256",
        "provider_seals",
        "lifecycle_state",
        "detector_state",
    )
    expected = {key: baseline_first[key] for key in isolation_keys}
    for result in (identity_first, identity_second, baseline_second):
        assert {key: result[key] for key in isolation_keys} == expected
    assert baseline_first["similarities"] == ()
    assert baseline_second["similarities"] == ()
    assert identity_first["similarities"] == pytest.approx((1.0,), abs=1e-5)
    assert identity_second["similarities"] == pytest.approx((1.0,), abs=1e-5)
    assert model_state == {
        "active": 0,
        "maximum_active": 1,
        "load_count": 2,
        "unload_count": 2,
    }


class _TestSmartTurnLease:
    def __init__(self, token) -> None:
        self.token = token
        self.released = False

    async def release(self) -> None:
        self.released = True


class _ReadyDetector:
    def __init__(self, feed_result: DetectorFeedResult | None = None) -> None:
        self._token = None
        self._feed_result = feed_result or DetectorFeedResult((), True)
        self.reset = AsyncMock(side_effect=self._reset)
        self.close = AsyncMock()
        self.release_deferred_turn = AsyncMock()
        self.seal_provider_candidate = AsyncMock(
            return_value=ProviderCandidateFence(0, 0, 0)
        )
        self.complete_provider_candidate = AsyncMock(return_value=False)
        self.discard_provider_successor = AsyncMock(return_value=True)
        self.pin_endpointing_session = AsyncMock(return_value=True)
        self.bind_candidate = AsyncMock(side_effect=self._bind_candidate)

    async def prepare_endpointing(self, token):
        self._token = token
        return _TestSmartTurnLease(token)

    def endpointing_ready(self, token) -> bool:
        return self._token == token

    async def feed(self, _pcm16: bytes, **_kwargs) -> DetectorFeedResult:
        return self._feed_result

    async def _reset(self) -> None:
        self._token = None

    async def _bind_candidate(self, candidate, turn_token):
        return BoundDetectorTurn(candidate, turn_token)


class _FailedSmartTurnDetector(_ReadyDetector):
    async def prepare_endpointing(self, token):
        self._token = None
        return None

    def endpointing_ready(self, token) -> bool:
        return False


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


async def _install_active_smart_turn(runtime: _Runtime, provider: str = "qwen") -> None:
    _install_ready_lifecycle(runtime, provider)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )


async def _start_and_seal_turn(
    runtime: _Runtime,
    provider: str = "qwen",
) -> None:
    if runtime._asr_lifecycle is None:
        _install_ready_lifecycle(runtime, provider)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )
    await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)


async def test_runtime_registers_chat_and_game_with_chat_active_by_default() -> None:
    runtime = _Runtime()

    assert runtime._voice_input_registry.active_identity is not None
    assert runtime._voice_input_registry.active_identity.namespace == "builtin"
    assert (
        runtime._voice_input_registry.active_identity.name
        == BuiltinVoiceInputConsumer.CORE_CHAT.value
    )


async def test_consumer_switch_does_not_reacquire_mic_lease() -> None:
    runtime = _Runtime()
    runtime._voice_lease_generation = 42

    runtime._voice_input_registry.activate(
        runtime._game_voice_input_registration.handle
    )

    assert runtime._voice_lease_generation == 42
    assert (
        runtime._voice_input_registry.active_identity.name
        == BuiltinVoiceInputConsumer.GAME.value
    )


async def test_builtin_game_consumer_routes_final_to_active_game(
    monkeypatch,
) -> None:
    routed = AsyncMock(return_value=True)
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: True,
    )
    monkeypatch.setattr(
        game_consumer_module,
        "route_external_voice_transcript",
        routed,
    )
    runtime = _Runtime()

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    routed.assert_awaited_once()
    assert routed.await_args.args == ("Test", "play")
    assert routed.await_args.kwargs["request_id"].startswith("asr-")
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_game_partial_is_not_leaked_to_chat_preview(monkeypatch) -> None:
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: True,
    )
    runtime = _Runtime()
    websocket = type("WebSocket", (), {"send_json": AsyncMock()})()
    runtime.websocket = websocket
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    await runtime._send_independent_asr_preview(
        "game draft",
        runtime._asr_session_epoch,
    )

    websocket.send_json.assert_not_awaited()


async def test_independent_route_sends_pcm_to_asr_only() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime)

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_audio_bytes == 320
    assert runtime._omni_mic_audio_bytes == 0


async def test_stale_submit_drops_only_current_frame() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.STALE)
    )

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_route_mode == "independent"


async def test_unavailable_submit_blocks_core_route() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
    )

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_route_mode == "blocked"


@pytest.mark.parametrize("provider", ["soniox", "openai"])
async def test_streaming_provider_transport_prewarm_connects_without_wire_pcm(
    provider: str,
) -> None:
    class Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class Gate:
        def feed(self, _pcm16: bytes):
            return ()

        def reset(self) -> None:
            return None

    runtime = _Runtime()
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_transport_selection = _selection(provider, "provider")
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = None

    async def on_event(event) -> None:
        accepted = runtime._asr_detector_dispatcher.submit_nowait(
            CoreDetectorEventEnvelope(
                event=event,
                detector_ref=detector,
                lifecycle_ref=runtime._asr_lifecycle,
                session_epoch=runtime._asr_session_epoch,
            )
        )
        assert accepted is True

    detector = DetectorRuntime(
        vad=Vad(),
        gate=Gate(),
        provider_policy=runtime._asr_lifecycle.provider_policy,
        on_event=on_event,
    )
    runtime._asr_detector = detector
    runtime._asr_runtime._restart_transport = AsyncMock()
    runtime._asr_runtime._schedule_transport_warm_expiry = MagicMock()
    runtime._asr_runtime._ensure_smart_turn_ready = AsyncMock(
        side_effect=AssertionError("transport prewarm must not load SmartTurn")
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    await runtime._asr_detector_dispatcher.wait_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    runtime._asr_runtime._restart_transport.assert_awaited_once_with()
    runtime._asr_runtime._ensure_smart_turn_ready.assert_not_awaited()
    assert runtime._asr_audio_dispatcher.active_turn is None
    assert runtime._asr_audio_bytes == 0
    assert detector.smart_turn_readiness is SmartTurnReadiness.UNLOADED
    runtime._asr_runtime._schedule_transport_warm_expiry.assert_called_once_with(
        runtime._asr_session_epoch,
        ttl_ms=5_000,
    )
    await detector.close()
    await runtime._asr_detector_dispatcher.close()


async def test_provider_final_preserves_unconfirmed_successor_pcm_as_pre_roll() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    detector.complete_provider_candidate.return_value = True
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )
    await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
    successor_pcm = b"\x02\x00" * 160
    assert runtime._asr_lifecycle.accept_audio(
        successor_pcm,
        sample_rate_hz=16_000,
    ).disposition is AudioDisposition.BUFFER

    await runtime._handle_independent_asr_final(
        "first",
        runtime._asr_session_epoch,
        "openai",
    )

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )
    decision = runtime._asr_lifecycle.accept_audio(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert decision.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
    assert decision.pre_roll.startswith(successor_pcm)


async def test_async_detector_orders_pre_roll_before_smart_turn_seal() -> None:
    class Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class Gate:
        def feed(self, _pcm16: bytes):
            return (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.CANDIDATE_PAUSE,
            )

        def reset(self) -> None:
            return None

    class Coordinator:
        state = CoordinatorState.IDLE

        def push_audio(self, _pcm16: bytes) -> None:
            return None

        async def on_activity_event(self, event) -> None:
            self.state = (
                CoordinatorState.PAUSE_CANDIDATE
                if event is SpeechActivityEvent.CANDIDATE_PAUSE
                else CoordinatorState.SPEECH_ACTIVE
            )

        async def evaluate_buffered(self):
            return SimpleNamespace(
                status=EvaluationStatus.OK,
                decision=TurnDecision.COMPLETE,
            )

        async def prepare_predictor(self) -> bool:
            return True

        async def reset(self) -> None:
            self.state = CoordinatorState.IDLE

        async def close(self) -> None:
            self.state = CoordinatorState.CLOSED

        async def unload_predictor(self) -> None:
            return None

    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.signal_user_activity_end = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle = lifecycle
    detector: DetectorRuntime

    async def on_event(event) -> None:
        assert runtime._asr_detector_dispatcher.submit_nowait(
            CoreDetectorEventEnvelope(
                event=event,
                detector_ref=detector,
                lifecycle_ref=lifecycle,
                session_epoch=runtime._asr_session_epoch,
            )
        )

    detector = DetectorRuntime(
        vad=Vad(),
        gate=Gate(),
        provider_policy=resolve_provider_policy("qwen", "manual"),
        coordinator=Coordinator(),
        on_event=on_event,
    )
    runtime._asr_detector = detector
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    for _ in range(200):
        if asr.signal_user_activity_end.await_count:
            break
        await asyncio.sleep(0.001)
    await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(pcm16, sample_rate_hz=16_000)
    asr.signal_user_activity_end.assert_awaited_once()
    assert runtime._omni_mic_audio_bytes == 0
    await detector.close()


async def test_fixed_processed_frames_preserve_full_route_with_shadow_abba() -> None:
    class Processor:
        speech_probability = 0.9
        rnnoise_available = True
        rnnoise_frame_count = 1
        rnnoise_probability_peak = 0.9
        rnnoise_probability_mean = 0.9
        rnnoise_probability_last = 0.9
        rnnoise_probability_ema = 0.9

        def __init__(self) -> None:
            self.process_count = 0

        def process_chunk(self, _pcm16: bytes) -> bytes:
            self.process_count += 1
            return b"\x01\x00" * 160

        def close(self) -> None:
            return None

    processor = Processor()
    pipeline = VoiceInputAudioPipeline(processor_factory=lambda: processor)
    processed_frames = (
        await pipeline.process(b"\x01\x00" * 480, sample_rate_hz=48_000),
    )
    await pipeline.close()
    assert processor.process_count == 1

    class Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class Gate:
        def feed(self, _pcm16: bytes):
            return (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.CANDIDATE_PAUSE,
            )

        def reset(self) -> None:
            return None

    class Coordinator:
        state = CoordinatorState.IDLE

        def push_audio(self, _pcm16: bytes) -> None:
            return None

        async def on_activity_event(self, event) -> None:
            self.state = (
                CoordinatorState.PAUSE_CANDIDATE
                if event is SpeechActivityEvent.CANDIDATE_PAUSE
                else CoordinatorState.SPEECH_ACTIVE
            )

        async def evaluate_buffered(self):
            return SimpleNamespace(
                status=EvaluationStatus.OK,
                decision=TurnDecision.COMPLETE,
            )

        async def prepare_predictor(self) -> bool:
            return True

        async def reset(self) -> None:
            self.state = CoordinatorState.IDLE

        async def close(self) -> None:
            self.state = CoordinatorState.CLOSED

        async def unload_predictor(self) -> None:
            return None

    class Backend:
        def load(self) -> bool:
            return True

        def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
            assert pcm16 == processed_frames[0].pcm16
            assert sample_rate_hz == processed_frames[0].sample_rate_hz
            return 0.75

        def close(self) -> None:
            return None

    async def run(*, shadow_enabled: bool) -> dict[str, object]:
        runtime = _Runtime()
        wire_chunks: list[bytes] = []

        async def stream_audio(
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> None:
            assert sample_rate_hz == 16_000
            wire_chunks.append(bytes(pcm16))

        asr = type("Asr", (), {})()
        asr.is_ready = True
        asr.stream_audio = AsyncMock(side_effect=stream_audio)
        asr.signal_user_activity_end = AsyncMock()
        asr.close = AsyncMock()
        runtime._asr_session = asr
        runtime._asr_provider = "qwen"
        runtime._asr_route_mode = "independent"
        lifecycle = VoiceInputLifecycleController(
            provider_policy=resolve_provider_policy("qwen", "manual"),
            shadow_mode=False,
        )
        lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
        runtime._asr_lifecycle = lifecycle
        observations: list[SpeakerShadowObservation] = []
        shadow = (
            SpeakerShadowRuntime(
                backend_factory=Backend,
                config=SpeakerShadowConfig(
                    enabled=True,
                    minimum_audio_ms=10,
                    maximum_audio_ms=40,
                    idle_unload_seconds=60,
                ),
                on_observation=observations.append,
            )
            if shadow_enabled
            else None
        )
        detector: DetectorRuntime

        async def on_event(event) -> None:
            assert runtime._asr_detector_dispatcher.submit_nowait(
                CoreDetectorEventEnvelope(
                    event=event,
                    detector_ref=detector,
                    lifecycle_ref=lifecycle,
                    session_epoch=runtime._asr_session_epoch,
                )
            )

        detector = DetectorRuntime(
            vad=Vad(),
            gate=Gate(),
            provider_policy=resolve_provider_policy("qwen", "manual"),
            coordinator=Coordinator(),
            speaker_shadow=shadow,
            on_event=on_event,
        )
        runtime._asr_detector = detector

        for frame in processed_frames:
            assert await runtime._route_microphone_audio(
                frame.pcm16,
                sample_rate_hz=frame.sample_rate_hz,
                speech_probability=frame.speech_probability,
                rnnoise_available=frame.rnnoise_available,
            )
        for _ in range(200):
            if asr.signal_user_activity_end.await_count:
                break
            await asyncio.sleep(0.001)
        await runtime._asr_detector_dispatcher.wait_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        if shadow is not None:
            await shadow.wait_idle()
        assert asr.signal_user_activity_end.await_count == 1

        await runtime._handle_independent_asr_final(
            "local-final",
            runtime._asr_session_epoch,
            "qwen",
        )
        await runtime._wait_asr_transcript_dispatch_idle()
        continuous_pcm = b"".join(wire_chunks)
        lifecycle_history = tuple(
            payload["details"]["state"]
            for call in runtime.send_status.await_args_list
            if (
                (payload := json.loads(call.args[0])).get("code")
                == "ASR_LIFECYCLE_STATE"
            )
        )
        detector_state = (
            detector.detector_epoch,
            detector.candidate_open,
            detector._candidate_generation,
            len(detector._completion_fences),
            detector.smart_turn_readiness.value,
        )
        pending_state = (
            runtime._asr_detector_dispatcher._queue.qsize(),
            runtime._asr_audio_dispatcher._queue.qsize(),
            runtime._asr_transcript_dispatcher._queue.qsize(),
            len(runtime._asr_transcript_dispatcher._reservations),
            lifecycle.pre_roll_bytes,
            0
            if shadow is None
            else shadow.snapshot()["buffered_candidate_count"],
            0 if shadow is None else shadow.snapshot()["buffered_audio_bytes"],
        )
        result = {
            "provider_pcm": continuous_pcm,
            "provider_sha256": hashlib.sha256(continuous_pcm).hexdigest(),
            "provider_seals": asr.signal_user_activity_end.await_count,
            "lifecycle_history": lifecycle_history,
            "provider_final_count": runtime.handle_input_transcript.await_count,
            "lifecycle_state": lifecycle.snapshot.state.value,
            "detector_state": detector_state,
            "pending_state": pending_state,
            "similarities": tuple(item.similarity for item in observations),
        }

        await runtime._asr_runtime.close()
        assert runtime._asr_runtime._asr_detector is None
        assert runtime._asr_runtime._asr_close_tasks == set()
        assert runtime._asr_runtime._asr_detector_dispatcher._worker is None
        assert runtime._asr_runtime._asr_audio_dispatcher._worker is None
        assert runtime._asr_runtime._asr_transcript_dispatcher._worker is None
        return result

    baseline_first = await run(shadow_enabled=False)
    shadow_first = await run(shadow_enabled=True)
    shadow_second = await run(shadow_enabled=True)
    baseline_second = await run(shadow_enabled=False)

    isolation_keys = (
        "provider_pcm",
        "provider_sha256",
        "provider_seals",
        "lifecycle_history",
        "provider_final_count",
        "lifecycle_state",
        "detector_state",
        "pending_state",
    )
    expected = {key: baseline_first[key] for key in isolation_keys}
    for result in (shadow_first, shadow_second, baseline_second):
        assert {key: result[key] for key in isolation_keys} == expected
    assert shadow_first["similarities"] == pytest.approx((0.75,))
    assert shadow_second["similarities"] == pytest.approx((0.75,))
    assert baseline_first["similarities"] == ()
    assert baseline_second["similarities"] == ()


@pytest.mark.parametrize(
    "provider",
    ["dummy", "glm", "gemini"],
)
async def test_smart_turn_unavailable_blocks_segmented_provider_before_wire_audio(
    provider: str,
) -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(
            provider,
            "manual",
        ),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _FailedSmartTurnDetector()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize("provider", ["qwen", "grok", "soniox"])
async def test_provider_endpoint_does_not_wait_for_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _FailedSmartTurnDetector(
        DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
    )
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(pcm16, sample_rate_hz=16_000)
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0


async def test_enforced_lifecycle_suppresses_local_silence_upload() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = type(
        "Detector",
        (),
        {"feed": AsyncMock(return_value=DetectorFeedResult((), True))},
    )()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pre_roll_bytes == 320


async def test_local_speech_wake_uploads_pre_roll_to_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(
        side_effect=[
            DetectorFeedResult((), True),
            DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True),
        ]
    )
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(
        (b"\x01\x00" * 160) + (b"\x02\x00" * 160),
        sample_rate_hz=16_000,
    )
    runtime.session.handle_interruption.assert_awaited_once_with()


async def test_detector_failure_fails_open_to_same_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime)
    runtime._asr_detector.feed = AsyncMock(return_value=DetectorFeedResult((), False))

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_route_mode == "independent"


async def test_game_takeover_clears_provider_audio_and_suspends_lifecycle() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = type("Detector", (), {"reset": AsyncMock()})()
    runtime._asr_detector = detector

    await runtime._suspend_independent_voice_input_for_game()

    asr.close.assert_awaited_once_with()
    detector.reset.assert_awaited_once_with()
    assert runtime._asr_lifecycle.snapshot.state.value == "suspended"

    await runtime._resume_independent_voice_input_after_game()
    assert runtime._asr_lifecycle.snapshot.state.value == "local_listen"


async def test_game_takeover_wins_even_if_provider_clear_fails() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock(side_effect=RuntimeError("provider abort failed"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    await runtime._suspend_independent_voice_input_for_game()

    assert runtime._asr_lifecycle.snapshot.state.value == "suspended"


async def test_bound_game_consumer_reuses_smart_turn_asr_without_core(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: True,
    )
    on_final = AsyncMock(return_value=True)
    monkeypatch.setattr(
        game_consumer_module,
        "route_external_voice_transcript",
        on_final,
    )
    runtime = _Runtime()

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True

    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert on_final.await_args.args == ("Test", "play")
    assert on_final.await_args.kwargs["request_id"].startswith("asr-")
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            2,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert (
        runtime._voice_input_registry.active_identity.name
        == BuiltinVoiceInputConsumer.CORE_CHAT.value
    )


async def test_game_consumer_failure_never_falls_back_to_core(monkeypatch) -> None:
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: True,
    )
    on_final = AsyncMock(side_effect=RuntimeError("consumer failed"))
    monkeypatch.setattr(
        game_consumer_module,
        "route_external_voice_transcript",
        on_final,
    )
    runtime = _Runtime()
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    on_final.assert_awaited_once()
    assert on_final.await_args.args == ("Test", "play")
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_game_final_cannot_cross_lease_back_to_core(monkeypatch) -> None:
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: True,
    )
    on_final = AsyncMock(return_value=True)
    monkeypatch.setattr(
        game_consumer_module,
        "route_external_voice_transcript",
        on_final,
    )
    runtime = _Runtime()
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_voice_input_control(
        "lease_sync",
        2,
        owner="core",
        hard_muted=False,
        focus_suppressed=False,
    )
    await runtime._handle_independent_asr_final("stale", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    on_final.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_hard_mute_overrides_bound_game_consumer(monkeypatch) -> None:
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: True,
    )
    runtime = _Runtime()

    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=True,
        focus_suppressed=False,
    )

    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._voice_input_suppression_reasons == {"hard_mute"}
    assert runtime._omni_mic_audio_bytes == 0


async def test_game_owner_without_consumer_remains_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        game_consumer_module,
        "is_game_route_active",
        lambda _lanlan_name: False,
    )
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )

    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
    assert runtime._omni_mic_audio_bytes == 0


async def test_fresh_blocked_route_consumes_pcm_without_omni() -> None:
    runtime = _Runtime()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 0


async def test_native_route_is_sufficient_to_authorize_omni_audio() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_route_mode == "native"
    runtime.session.stream_audio.assert_awaited_once()
    assert not hasattr(runtime._asr_runtime, "_asr_required")


async def test_speech_started_interrupts_and_prepares_turn_once() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    runtime.session.handle_interruption.assert_awaited_once_with()
    runtime.handle_new_message.assert_awaited_once_with()
    assert runtime._asr_turn_prepared is True


async def test_speech_started_prepares_external_voice_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.prepare_external_voice_turn = AsyncMock()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    runtime.session.prepare_external_voice_turn.assert_awaited_once_with()
    runtime.handle_new_message.assert_awaited_once_with()


async def test_turn_endpoint_seals_immediately_before_provider_final() -> None:
    runtime = _Runtime()
    runtime._asr_session = type("Asr", (), {"is_ready": True})()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    await runtime._handle_independent_asr_endpoint(epoch)

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING


async def test_empty_final_completes_turn_without_core_injection() -> None:
    runtime = _Runtime()
    await _start_and_seal_turn(runtime)

    await runtime._handle_independent_asr_final(
        "",
        runtime._asr_session_epoch,
        "qwen",
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_lifecycle.metrics.false_wake_count == 1
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_provider_final_watchdog_blocks_only_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    policy = replace(
        resolve_provider_policy("qwen", "manual"),
        provider_final_timeout_ms=10,
    )
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await _start_and_seal_turn(runtime)
    await asyncio.sleep(0.03)

    assert runtime._asr_route_mode == "blocked"
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_optimization_disabled_continuously_uploads_with_smart_turn() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_detector.endpointing_ready(
        runtime._capture_turn_token(runtime._asr_lifecycle)
    )
    assert runtime._omni_mic_audio_bytes == 0


async def test_warm_idle_buffers_continuous_pcm_until_detector_event_applies() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle = lifecycle

    dispatch_started = asyncio.Event()
    dispatch_release = asyncio.Event()

    async def gated_handler(envelope: CoreDetectorEventEnvelope) -> None:
        dispatch_started.set()
        await dispatch_release.wait()
        await runtime._dispatch_asr_detector_event(envelope)

    runtime._asr_detector_dispatcher = AsrDetectorDispatcher(
        gated_handler,
        on_failure=runtime._handle_asr_detector_dispatcher_failure,
    )
    runtime.restart_transport = AsyncMock(return_value=None)

    async def on_event(event) -> None:
        assert runtime._asr_detector_dispatcher.submit_nowait(
            CoreDetectorEventEnvelope(
                event=event,
                detector_ref=detector,
                lifecycle_ref=lifecycle,
                session_epoch=runtime._asr_session_epoch,
            )
        )

    class ContinuousDetector(_ReadyDetector):
        detector_epoch = 0
        queued_audio_ms = 0
        smart_turn_evaluation_ms = 0
        smart_turn_stale_result_count = 0
        smart_turn_coalesced_evaluation_count = 0
        throttle_shadow_metrics = SimpleNamespace(
            evidence_chunk_count=0,
            incomplete_chunk_count=0,
            rnnoise_trigger_count=0,
            silero_trigger_count=0,
            fusion_trigger_count=0,
            rnnoise_silero_disagreement_count=0,
        )

        async def submit_audio(
            self,
            _pcm16: bytes,
            *,
            ingress_token: VoiceIngressToken,
            **_kwargs,
        ) -> DetectorSubmitResult:
            identity = DetectorIngressIdentity(ingress_token, self.detector_epoch, 1)
            candidate = DetectorCandidateKey(self.detector_epoch, 1)
            await on_event(
                DetectorPrewarmEvent(identity, candidate, "continuous")
            )
            return DetectorSubmitResult(
                DetectorSubmitStatus.ACCEPTED,
                True,
                True,
                identity,
                ThrottleAction.ALLOW_PROVIDER_AUDIO,
                candidate,
                True,
            )

    detector = ContinuousDetector()
    runtime._asr_detector = detector
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await asyncio.wait_for(dispatch_started.wait(), 1)

    asr.stream_audio.assert_not_awaited()
    assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert lifecycle.pre_roll_bytes == len(pcm16)
    assert runtime._asr_route_mode == "independent"

    dispatch_release.set()
    await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()

    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    asr.stream_audio.assert_awaited_once_with(
        pcm16,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_route_mode == "independent"
    await runtime._asr_detector_dispatcher.close()


async def test_throttle_failure_queues_continuous_wake_before_forwarding() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle = lifecycle
    runtime.restart_transport = AsyncMock(return_value=None)

    class FailedThrottleDetector(_ReadyDetector):
        detector_epoch = 0
        queued_audio_ms = 0
        smart_turn_evaluation_ms = 0
        smart_turn_stale_result_count = 0
        smart_turn_coalesced_evaluation_count = 0
        throttle_shadow_metrics = SimpleNamespace(
            evidence_chunk_count=0,
            incomplete_chunk_count=0,
            rnnoise_trigger_count=0,
            silero_trigger_count=0,
            fusion_trigger_count=0,
            rnnoise_silero_disagreement_count=0,
        )

        async def submit_audio(
            self,
            _pcm16: bytes,
            *,
            ingress_token: VoiceIngressToken,
            **_kwargs,
        ) -> DetectorSubmitResult:
            identity = DetectorIngressIdentity(ingress_token, self.detector_epoch, 1)
            candidate = DetectorCandidateKey(self.detector_epoch, 1)
            return DetectorSubmitResult(
                DetectorSubmitStatus.ACCEPTED,
                False,
                True,
                identity,
                ThrottleAction.ALLOW_PROVIDER_AUDIO,
                candidate,
            )

    runtime._asr_detector = FailedThrottleDetector()
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()

    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    asr.stream_audio.assert_awaited_once_with(
        pcm16,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_route_mode == "independent"


@pytest.mark.parametrize("provider", ["qwen", "openai"])
async def test_optimization_disabled_provider_route_never_prepares_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.prepare_endpointing = AsyncMock()
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once()
    detector.prepare_endpointing.assert_not_awaited()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_smart_turn_lease is None
    assert runtime._omni_mic_audio_bytes == 0


async def test_draining_next_speech_waits_for_old_final_then_starts_new_turn() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(return_value=DetectorFeedResult((), True))
    detector.reset = AsyncMock()
    detector.release_deferred_turn = AsyncMock()
    runtime._asr_detector = detector
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    old_turn = runtime._asr_lifecycle.identity.turn_id
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pending_turn_bytes == 320

    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_lifecycle.identity.turn_id == old_turn + 1
    asr.stream_audio.assert_awaited_once_with(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime.handle_new_message.await_count == 2
    detector.reset.assert_not_awaited()
    detector.release_deferred_turn.assert_awaited_once_with()

    runtime.handle_input_transcript.reset_mock()
    await runtime._handle_independent_asr_final("stale-old-turn", epoch, "qwen")
    runtime.handle_input_transcript.assert_not_awaited()


async def test_final_without_observed_pending_preserves_racing_next_onset() -> None:
    runtime = _Runtime()
    await _start_and_seal_turn(runtime, "gemini")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)

    await runtime._handle_independent_asr_final(
        "first",
        runtime._asr_session_epoch,
        "gemini",
    )

    # A next onset may be admitted after final acceptance but before cleanup.
    # Releasing the completed turn preserves that audio; a full reset loses it.
    detector.reset.assert_not_awaited()
    detector.release_deferred_turn.assert_awaited_once_with()


async def test_draining_pending_turn_overflow_discards_candidate_and_reports_backpressure() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    await runtime._route_microphone_audio(
        b"\x01\x00" * (16_000 * 9),
        sample_rate_hz=16_000,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert runtime._asr_sealed_turn_token is not None
    assert runtime._asr_lifecycle.pending_turn_bytes == 0
    assert runtime._asr_lifecycle.has_pending_turn is False
    runtime._asr_detector.reset.assert_awaited_once()
    assert any(
        "ASR_INGRESS_BACKPRESSURE" in call.args[0]
        for call in runtime.send_status.await_args_list
    )
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize("provider", ["soniox", "qwen"])
async def test_draining_provider_overflow_preserves_old_final(
    provider: str,
) -> None:
    class Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class Gate:
        def feed(self, _pcm16: bytes):
            return (SpeechActivityEvent.SPEECH_RESUMED,)

        def reset(self) -> None:
            return None

    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=replace(
            resolve_provider_policy(provider, "provider"),
            provider_final_timeout_ms=200,
        ),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = DetectorRuntime(
        vad=Vad(),
        gate=Gate(),
        provider_policy=runtime._asr_lifecycle.provider_policy,
    )
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._route_microphone_audio(
        b"\x01\x00" * (16_000 * 9),
        sample_rate_hz=16_000,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert runtime._asr_sealed_turn_token is not None
    assert runtime._asr_lifecycle.pending_turn_bytes == 0
    assert runtime._asr_lifecycle.has_pending_turn is False
    assert runtime._asr_provider_candidate_fence is not None

    await runtime._handle_independent_asr_final("first", epoch, provider)
    await runtime._wait_asr_transcript_dispatch_idle()
    await asyncio.sleep(0.25)

    statuses = [json.loads(call.args[0]) for call in runtime.send_status.await_args_list]
    codes = [payload["code"] for payload in statuses]
    assert codes.count("ASR_INGRESS_BACKPRESSURE") == 1
    assert codes.count("ASR_PROVIDER_FINAL_TIMEOUT") == 0
    assert codes.count("ASR_ENDPOINTING_FAILED") == 0
    runtime.handle_input_transcript.assert_awaited_once_with(
        "first",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": provider},
    )
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_session is asr
    assert runtime._asr_sealed_turn_token is None
    assert runtime._asr_provider_candidate_fence is None
    assert runtime._asr_detector._provider_candidate_fence is None
    assert len(runtime._asr_accepted_final_keys) == 1
    assert runtime._omni_mic_audio_bytes == 0
    await runtime._asr_detector.close()


async def test_provider_fence_failure_does_not_accept_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    detector.complete_provider_candidate.return_value = None
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )
    await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)

    await runtime._handle_independent_asr_final(
        "must-not-publish",
        runtime._asr_session_epoch,
        "openai",
    )

    statuses = [json.loads(call.args[0]) for call in runtime.send_status.await_args_list]
    codes = [payload["code"] for payload in statuses]
    assert codes.count("ASR_ENDPOINTING_FAILED") == 1
    assert runtime._asr_accepted_final_keys == {}
    runtime.handle_input_transcript.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"


async def test_provider_successor_discard_failure_fails_closed_once() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    detector.discard_provider_successor.side_effect = RuntimeError("private failure")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )
    await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
    epoch = runtime._asr_session_epoch
    ingress_token = runtime._capture_ingress_token(runtime._asr_lifecycle)

    await runtime._handle_audio_ingress_backpressure(
        ingress_token,
        observed_state=VoiceLifecycleState.DRAINING,
    )

    statuses = [json.loads(call.args[0]) for call in runtime.send_status.await_args_list]
    codes = [payload["code"] for payload in statuses]
    assert codes.count("ASR_ENDPOINTING_FAILED") == 1
    assert codes.count("ASR_INGRESS_BACKPRESSURE") == 0
    assert runtime._asr_session_epoch == epoch + 1
    assert runtime._asr_route_mode == "blocked"
    assert "private failure" not in str(runtime.send_status.await_args_list)


async def test_provider_final_activates_successor_before_overflow_preserves_old_final() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    completion_started = asyncio.Event()
    completion_release = asyncio.Event()

    async def complete_provider_candidate(_fence) -> bool:
        completion_started.set()
        await completion_release.wait()
        return False

    detector.complete_provider_candidate.side_effect = complete_provider_candidate
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    runtime._asr_lifecycle.accept_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    ingress_token = runtime._capture_ingress_token(runtime._asr_lifecycle)

    final_task = asyncio.create_task(
        runtime._handle_independent_asr_final("first", epoch, "openai")
    )
    await completion_started.wait()
    overflow_task = asyncio.create_task(
        runtime._handle_audio_ingress_backpressure(
            ingress_token,
            observed_state=VoiceLifecycleState.DRAINING,
        )
    )
    await asyncio.sleep(0)
    completion_release.set()
    await asyncio.gather(final_task, overflow_task)
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "first",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "openai"},
    )
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert runtime._asr_lifecycle.has_pending_turn is False
    assert runtime._asr_session is None
    asr.close.assert_awaited_once()
    detector.reset.assert_awaited_once()


async def test_provider_final_wins_overflow_race_before_successor_activation() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    completion_started = asyncio.Event()
    completion_release = asyncio.Event()

    async def complete_provider_candidate(_fence) -> bool:
        completion_started.set()
        await completion_release.wait()
        return False

    async def yielding_status(_payload: str) -> None:
        await asyncio.sleep(0)

    detector.complete_provider_candidate.side_effect = complete_provider_candidate
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    runtime._asr_lifecycle.accept_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    ingress_token = runtime._capture_ingress_token(runtime._asr_lifecycle)
    runtime.send_status.side_effect = yielding_status

    final_task = asyncio.create_task(
        runtime._handle_independent_asr_final("first", epoch, "openai")
    )
    await completion_started.wait()
    overflow_task = asyncio.create_task(
        runtime._handle_audio_ingress_backpressure(
            ingress_token,
            observed_state=VoiceLifecycleState.DRAINING,
        )
    )
    await asyncio.sleep(0)
    completion_release.set()
    await asyncio.gather(final_task, overflow_task)
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "first",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "openai"},
    )
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_lifecycle.has_pending_turn is False
    assert runtime._asr_session is asr
    asr.close.assert_not_awaited()
    detector.reset.assert_awaited_once()


@pytest.mark.parametrize("replacement", ["epoch", "lifecycle", "detector"])
async def test_waiting_provider_overflow_does_not_touch_replaced_identity(
    replacement: str,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    ingress_token = runtime._capture_ingress_token(runtime._asr_lifecycle)

    await runtime._asr_final_lock.acquire()
    overflow_task = asyncio.create_task(
        runtime._handle_audio_ingress_backpressure(
            ingress_token,
            observed_state=VoiceLifecycleState.DRAINING,
        )
    )
    await asyncio.sleep(0)
    if replacement == "epoch":
        runtime._asr_session_epoch += 1
    elif replacement == "lifecycle":
        runtime._asr_lifecycle = VoiceInputLifecycleController(
            provider_policy=resolve_provider_policy("openai", "provider"),
            shadow_mode=False,
        )
        runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    else:
        runtime._asr_detector = _ReadyDetector()
    runtime._asr_final_lock.release()

    await overflow_task

    detector.discard_provider_successor.assert_not_awaited()
    assert "ASR_INGRESS_BACKPRESSURE" not in str(runtime.send_status.await_args_list)
    watchdog = runtime._asr_final_watchdog_task
    if watchdog is not None:
        watchdog.cancel()


async def test_transport_only_close_enters_deep_sleep_without_closing_detector() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    detector = type("Detector", (), {"close": AsyncMock()})()
    runtime._asr_detector = detector

    await runtime._close_transport_only()

    assert runtime._asr_session is None
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    assert runtime._asr_detector is detector
    assert runtime._asr_route_mode == "independent"
    asr.close.assert_awaited_once_with()
    detector.close.assert_not_awaited()


async def test_warm_idle_transport_uses_provider_warm_transport_ttl() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    policy = replace(
        resolve_provider_policy("qwen", "manual"),
        warm_transport_ms=10,
    )
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        config=VoiceLifecycleConfig(idle_transport_close_ms=1_000),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)

    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        ttl_ms=runtime._asr_lifecycle.provider_policy.warm_transport_ms,
    )
    await asyncio.sleep(0.03)

    assert runtime._asr_session is None
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    asr.close.assert_awaited_once_with()


async def test_provider_final_honors_zero_warm_transport_ttl() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    policy = replace(
        resolve_provider_policy("qwen", "manual"),
        warm_transport_ms=0,
    )
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        config=VoiceLifecycleConfig(idle_transport_close_ms=1_000),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
    await _start_and_seal_turn(runtime)

    await runtime._handle_independent_asr_final(
        "",
        runtime._asr_session_epoch,
        "qwen",
    )
    async with asyncio.timeout(1):
        while runtime._asr_session is not None:
            await asyncio.sleep(0)

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    asr.close.assert_awaited_once_with()


async def test_warm_expiry_does_not_close_active_transport() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)

    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        ttl_ms=10,
    )
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    await asyncio.sleep(0.03)

    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    asr.close.assert_not_awaited()


async def test_stale_lifecycle_warm_expiry_does_not_close_successor() -> None:
    runtime = _Runtime()
    first = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = first
    runtime._asr_route_mode = "independent"
    original = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    original.open(route_mode=VoiceRouteMode.INDEPENDENT)
    original.transition(VoiceLifecycleEvent.SOFT_WAKE)
    original.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    original.transition(VoiceLifecycleEvent.TURN_SEALED)
    original.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle = original
    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        ttl_ms=10,
    )

    successor = type("Asr", (), {"close": AsyncMock()})()
    replacement = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    replacement.open(route_mode=VoiceRouteMode.INDEPENDENT)
    replacement.transition(VoiceLifecycleEvent.SOFT_WAKE)
    replacement.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    replacement.transition(VoiceLifecycleEvent.TURN_SEALED)
    replacement.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_session = successor
    runtime._asr_lifecycle = replacement
    await asyncio.sleep(0.03)

    assert runtime._asr_session is successor
    assert runtime._asr_lifecycle is replacement
    assert replacement.snapshot.state is VoiceLifecycleState.WARM_IDLE
    first.close.assert_not_awaited()
    successor.close.assert_not_awaited()


async def test_stale_epoch_warm_expiry_does_not_close_current_transport() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        ttl_ms=10,
    )

    runtime._asr_session_epoch += 1
    await asyncio.sleep(0.03)

    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    asr.close.assert_not_awaited()


async def test_unconfirmed_prewarm_closes_cloud_but_keeps_local_detector() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        config=VoiceLifecycleConfig(idle_transport_close_ms=10),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    runtime._asr_detector = detector
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=asr)
    runtime._asr_transport_selection = _selection("qwen")
    ingress = runtime._capture_ingress_token(runtime._asr_lifecycle)
    event = DetectorPrewarmEvent(
        ingress=DetectorIngressIdentity(ingress, detector_epoch=1, sequence_no=1),
        candidate=DetectorCandidateKey(1, 0),
        kind="prewarm",
    )

    await runtime._handle_detector_prewarm_event(
        event,
        detector,
        runtime._asr_lifecycle,
        runtime._asr_session_epoch,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    asr.stream_audio.assert_not_awaited()
    await asyncio.sleep(0.03)
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    assert runtime._asr_session is None
    assert runtime._asr_detector is detector
    assert runtime._asr_lifecycle.pending_connect_bytes == 0
    detector.reset.assert_awaited_once_with()
    asr.close.assert_awaited_once_with()
    assert runtime._omni_mic_audio_bytes == 0

async def test_deep_sleep_speech_reconnects_and_flushes_pending_audio() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(
        return_value=DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
    )
    runtime._asr_detector = detector
    new_asr = type("Asr", (), {})()
    new_asr.is_ready = True
    new_asr.connect = AsyncMock()
    new_asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=new_asr)
    runtime._asr_transport_selection = _selection("qwen")

    await runtime._route_microphone_audio(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_transport_task is not None
    await runtime._asr_transport_task

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    new_asr.connect.assert_awaited_once_with()
    new_asr.stream_audio.assert_awaited_once_with(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )


async def test_hard_mute_is_backend_authoritative_and_rejects_stale_lease_events() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = type("Detector", (), {})()
    detector.reset = AsyncMock()
    detector.feed = AsyncMock(return_value=DetectorFeedResult((), True))
    runtime._asr_detector = detector
    runtime._clear_audio_stream_queue = MagicMock()
    runtime.hot_swap_audio_cache = [b"old-pcm"]
    old_token = runtime._capture_ingress_token(runtime._asr_lifecycle)

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            12,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    asr.close.assert_awaited_once_with()
    runtime._clear_audio_stream_queue.assert_called_once_with("lease_sync")
    assert runtime.hot_swap_audio_cache == []
    assert runtime._ingress_token_matches(old_token) is False
    detector.reset.assert_awaited_once_with()
    detector.feed.assert_not_awaited()
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pre_roll_bytes == 0

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            11,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )
    assert runtime._voice_input_suppressed is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            13,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_suppressed is False


async def test_hard_mute_during_detector_await_invalidates_inflight_pcm() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    feed_started = asyncio.Event()
    release_feed = asyncio.Event()

    class _BlockingDetector(_ReadyDetector):
        async def feed(self, _pcm16: bytes, **_kwargs) -> DetectorFeedResult:
            feed_started.set()
            await release_feed.wait()
            return DetectorFeedResult((), True)

    runtime._asr_detector = _BlockingDetector()
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    route_task = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00" * 160,
            sample_rate_hz=16_000,
        )
    )
    await asyncio.wait_for(feed_started.wait(), 1)
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="core",
        hard_muted=True,
        focus_suppressed=False,
    )
    release_feed.set()

    assert await route_task is True
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 0


async def test_new_websocket_connection_resets_mic_lease_generation_once() -> None:
    runtime = _Runtime()
    runtime._voice_lease_generation = 12

    assert runtime._begin_voice_input_connection("socket-a") is True
    assert runtime._voice_lease_generation == -1
    assert runtime._voice_input_accepts_pcm() is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is False

    assert runtime._begin_voice_input_connection("socket-a") is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )

    assert runtime._begin_voice_input_connection("socket-b") is True
    assert runtime._voice_input_accepts_pcm() is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True


async def test_game_owner_and_hard_mute_remain_simultaneously_authoritative() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )

    assert runtime._voice_lease_owner == "game"
    assert runtime._voice_lease_hard_muted is True
    assert runtime._voice_input_suppression_reasons == {"game", "hard_mute"}
    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED


async def test_accepted_final_is_recorded_and_injected_once() -> None:
    runtime = _Runtime()
    runtime._asr_provider = "glm"
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")

    await asyncio.gather(
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "glm"},
    )
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_identical_text_in_consecutive_turns_is_delivered_twice() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    for _ in range(2):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final("嗯", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["嗯", "嗯"]
    assert [
        call.args[0] for call in runtime.session.create_response.await_args_list
    ] == [
        "嗯",
        "嗯",
    ]


async def test_blocked_core_response_does_not_block_next_asr_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    response_started = asyncio.Event()
    release_response = asyncio.Event()

    async def block_response(_text: str) -> None:
        response_started.set()
        await release_response.wait()

    runtime.session.create_response.side_effect = block_response
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await response_started.wait()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    release_response.set()
    await runtime._wait_asr_transcript_dispatch_idle()


async def test_core_swap_cancels_blocked_old_final_without_touching_new_state() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    old_epoch = runtime._asr_session_epoch
    old_core_session = runtime.session
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()

    async def block_transcript(_text: str, **_kwargs: object) -> bool:
        transcript_started.set()
        await release_transcript.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_transcript
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        old_epoch,
    )
    await runtime._handle_independent_asr_endpoint(old_epoch)
    await runtime._handle_independent_asr_final("old", old_epoch, "qwen")
    await transcript_started.wait()

    await runtime._close_independent_asr(next_route_mode="blocked")
    new_core_session = type("NewCore", (), {})()
    new_core_session.create_response = AsyncMock()
    new_core_session.handle_interruption = AsyncMock()
    runtime.session = new_core_session
    _install_ready_lifecycle(runtime, "qwen")
    new_lifecycle = runtime._asr_lifecycle
    assert new_lifecycle is not None
    expected_state = new_lifecycle.snapshot.state

    release_transcript.set()
    await asyncio.sleep(0)

    old_core_session.create_response.assert_not_awaited()
    new_core_session.create_response.assert_not_awaited()
    assert runtime._asr_lifecycle is new_lifecycle
    assert new_lifecycle.snapshot.state is expected_state
    assert runtime._asr_sealed_turn_token is None


async def test_late_first_final_then_second_final_recovers_in_linear_order() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    events: list[str] = []

    runtime.session.handle_interruption.side_effect = lambda: events.append(
        "interruption"
    )
    runtime.handle_new_message.side_effect = lambda: events.append("prepare")
    runtime.handle_input_transcript.side_effect = lambda text, **_kwargs: (
        events.append(f"transcript:{text}") or True
    )
    runtime.session.create_response.side_effect = lambda text: events.append(
        f"response:{text}"
    )

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first fragment", epoch, "openai")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second fragment", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert events.count("interruption") == 2
    assert events.count("prepare") == 2
    assert [event for event in events if event.startswith("transcript:")] == [
        "transcript:first fragment",
        "transcript:second fragment",
    ]
    assert [event for event in events if event.startswith("response:")] == [
        "response:first fragment",
        "response:second fragment",
    ]


async def test_three_pending_finals_recover_without_request_multiplication() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    for text in ("first", "second", "third"):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(text, epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime.session.handle_interruption.await_count == 3
    assert runtime.handle_new_message.await_count == 3
    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == [
        "first",
        "second",
        "third",
    ]
    assert [
        call.args[0] for call in runtime.session.create_response.await_args_list
    ] == [
        "first",
        "second",
        "third",
    ]


async def test_consumed_or_suppressed_final_does_not_create_response() -> None:
    runtime = _Runtime()
    runtime.handle_input_transcript.return_value = False
    await _start_and_seal_turn(runtime, "gemini")

    await runtime._handle_independent_asr_final(
        "echo",
        runtime._asr_session_epoch,
        "gemini",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.session.create_response.assert_not_awaited()


async def test_close_invalidates_late_final_before_waiting_for_provider() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    old_epoch = runtime._asr_session_epoch

    await runtime._close_independent_asr(next_route_mode="blocked")
    await runtime._handle_independent_asr_final("late", old_epoch, "glm")

    asr.close.assert_awaited_once_with()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"


async def test_close_releases_independent_audio_pipeline() -> None:
    runtime = _Runtime()
    pipeline = type("Pipeline", (), {})()
    pipeline.close = AsyncMock()
    runtime._voice_input_audio_pipeline = pipeline

    await runtime._close_independent_asr(next_route_mode="blocked")

    pipeline.close.assert_awaited_once_with()
    assert runtime._voice_input_audio_pipeline is not pipeline


async def test_close_failure_keeps_the_requested_blocked_route() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock(side_effect=RuntimeError("close failed"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    await runtime._close_independent_asr(next_route_mode="blocked")

    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )


async def test_close_requires_callers_to_declare_the_next_route() -> None:
    parameter = inspect.signature(AsrRuntimeMixin._close_independent_asr).parameters[
        "next_route_mode"
    ]

    assert parameter.default is inspect.Parameter.empty


async def test_asr_stream_failure_never_replays_the_failed_frame_to_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(side_effect=RuntimeError("sensitive provider body"))
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime, "qwen")

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "sensitive provider body" not in str(runtime.send_status.await_args)


async def test_asr_backpressure_reports_specific_blocking_status() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(
        side_effect=RuntimeError("ASR_STREAM_BACKPRESSURE: queue full")
    )
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime, "qwen")

    await runtime._route_microphone_audio(
        b"\x00\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert "ASR_STREAM_BACKPRESSURE" in runtime.send_status.await_args.args[0]
    assert runtime._asr_route_mode == "blocked"


async def test_independent_asr_setting_is_persisted_as_a_boolean() -> None:
    assert "independentAsrEnabled" in preferences._ALLOWED_CONVERSATION_SETTINGS
    assert (
        "voice_input_resource_optimization_enabled"
        in preferences._ALLOWED_CONVERSATION_SETTINGS
    )


async def test_start_uses_current_core_route_only_after_provider_ready(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    speaker_shadow = object()
    speaker_shadow_factory = MagicMock(return_value=speaker_shadow)
    runtime._speaker_shadow_factory = speaker_shadow_factory
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_not_awaited()
    assert runtime._asr_session is None
    await runtime._restart_transport(max_attempts=1)
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_detector._speaker_shadow is speaker_shadow
    speaker_shadow_factory.assert_called_once_with()
    assert factory.call_args.args == ("gemini",)
    assert factory.call_args.kwargs["selection"].provider_key == "gemini"


async def test_core_speaker_shadow_factory_failure_keeps_asr_available(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    runtime._speaker_shadow_factory = MagicMock(
        side_effect=RuntimeError("missing model")
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=asr),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._restart_transport(max_attempts=1)

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_detector._speaker_shadow is None
    runtime._speaker_shadow_factory.assert_called_once_with()


async def test_route_restart_defers_verifier_until_old_cleanup_finishes(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    class _Verifier:
        def __init__(self, *, block_close: bool) -> None:
            self.close_started = asyncio.Event()
            self.close_release = asyncio.Event()
            self.close_calls = 0
            if not block_close:
                self.close_release.set()

        def submit(self, _pcm16: bytes, *, sample_rate_hz: int, candidate) -> bool:
            return bool(sample_rate_hz and candidate is not None)

        def finish_candidate(self, _candidate) -> bool:
            return True

        async def reset(self) -> None:
            return None

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()

        async def wait_closed(self) -> None:
            await self.close_release.wait()

        def snapshot(self) -> dict[str, int]:
            return {}

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    old_verifier = _Verifier(block_close=True)
    new_verifier = _Verifier(block_close=False)
    verifier_factory = MagicMock(side_effect=(old_verifier, new_verifier))
    runtime._speaker_shadow_factory = verifier_factory
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    first_detector = runtime._asr_detector
    assert first_detector._speaker_shadow is old_verifier

    restart = asyncio.create_task(
        runtime._start_independent_asr_if_enabled("audio")
    )
    await asyncio.wait_for(old_verifier.close_started.wait(), 1)
    await asyncio.wait_for(restart, 1)

    second_detector = runtime._asr_detector
    assert second_detector is not first_detector
    assert second_detector._speaker_shadow is None
    assert runtime._asr_route_mode == "independent"
    assert verifier_factory.call_count == 1

    old_verifier.close_release.set()
    async with asyncio.timeout(1):
        while second_detector._speaker_shadow is None:
            await asyncio.sleep(0)

    assert second_detector._speaker_shadow is new_verifier
    assert verifier_factory.call_count == 2
    assert old_verifier.close_calls == 1

    await runtime.close_voice_input_session()
    await runtime._asr_runtime._wait_speaker_verifier_cleanup()
    assert new_verifier.close_calls == 1


async def test_route_restart_keeps_identity_detached_after_cleanup_failure(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    class _Verifier:
        def __init__(self) -> None:
            self.close_started = asyncio.Event()
            self.close_calls = 0

        def submit(self, _pcm16: bytes, *, sample_rate_hz: int, candidate) -> bool:
            return bool(sample_rate_hz and candidate is not None)

        def finish_candidate(self, _candidate) -> bool:
            return True

        async def reset(self) -> None:
            return None

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()

        async def wait_closed(self) -> None:
            raise RuntimeError("cleanup failed")

        def snapshot(self) -> dict[str, int]:
            return {}

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    old_verifier = _Verifier()
    verifier_factory = MagicMock(return_value=old_verifier)
    runtime._speaker_shadow_factory = verifier_factory
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._start_independent_asr_if_enabled("audio")
    await asyncio.wait_for(old_verifier.close_started.wait(), 1)
    await runtime._asr_runtime._wait_speaker_verifier_cleanup()

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_detector is not None
    assert runtime._asr_detector._speaker_shadow is None
    assert runtime._asr_runtime._speaker_verifier_cleanup_failed is True
    assert verifier_factory.call_count == 1

    await runtime.close_voice_input_session()


async def test_route_restart_bounds_stalled_verifier_cleanup(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    class _Verifier:
        def __init__(self) -> None:
            self.close_started = asyncio.Event()
            self.close_calls = 0

        def submit(self, _pcm16: bytes, *, sample_rate_hz: int, candidate) -> bool:
            return bool(sample_rate_hz and candidate is not None)

        def finish_candidate(self, _candidate) -> bool:
            return True

        async def reset(self) -> None:
            return None

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()

        async def wait_closed(self) -> None:
            await asyncio.Event().wait()

        def snapshot(self) -> dict[str, int]:
            return {}

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    old_verifier = _Verifier()
    verifier_factory = MagicMock(return_value=old_verifier)
    runtime._speaker_shadow_factory = verifier_factory
    monkeypatch.setattr(
        runtime_module,
        "_SPEAKER_VERIFIER_CLEANUP_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._start_independent_asr_if_enabled("audio")
    await asyncio.wait_for(old_verifier.close_started.wait(), 1)
    await asyncio.wait_for(
        runtime._asr_runtime._wait_speaker_verifier_cleanup(),
        1,
    )

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_detector is not None
    assert runtime._asr_detector._speaker_shadow is None
    assert runtime._asr_runtime._speaker_verifier_cleanup_tasks == set()
    assert runtime._asr_runtime._speaker_verifier_cleanup_failed is True
    assert verifier_factory.call_count == 1

    await runtime.close_voice_input_session()


async def test_missing_setting_defaults_to_independent_asr_enabled(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_not_awaited()
    assert runtime._asr_session is None
    await runtime._restart_transport(max_attempts=1)
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0


async def test_disabled_optimization_connects_transport_without_inventing_turn_identity(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    detector = _ReadyDetector()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voice_input_resource_optimization_enabled": False,
            }
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=asr),
    )
    monkeypatch.setattr(
        runtime_module,
        "DetectorRuntime",
        MagicMock(return_value=detector),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    detector.pin_endpointing_session.assert_not_awaited()
    asr.connect.assert_awaited_once_with()
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0


async def test_disabled_optimization_streaming_route_never_pins_smart_turn(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    detector = _ReadyDetector()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voice_input_resource_optimization_enabled": False,
            }
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("qwen", "provider")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=asr),
    )
    monkeypatch.setattr(
        runtime_module,
        "DetectorRuntime",
        MagicMock(return_value=detector),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    detector.pin_endpointing_session.assert_not_awaited()
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.provider_policy.endpoint_authority == "provider"
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert runtime._omni_mic_audio_bytes == 0


async def test_runtime_builds_primary_candidate_from_its_single_selection(
    monkeypatch,
) -> None:
    import main_logic.asr_client as asr_client
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    selection = asr_client._AsrSelection(
        provider_key="gemini",
        endpointing_mode="manual",
    )
    resolver = MagicMock(return_value=selection)
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    builder = MagicMock(return_value=asr)

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        builder,
        raising=False,
    )
    assert not hasattr(runtime_module, "create_asr_session")

    await runtime._start_independent_asr_if_enabled("audio")

    resolver.assert_called_once_with("gemini")
    builder.assert_not_called()
    assert runtime._asr_transport_selection is selection
    await runtime._restart_transport(max_attempts=1)
    assert builder.call_args.kwargs["selection"] is selection
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"


async def test_startup_close_window_is_blocked_before_settings_resolution(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class _OldAsr:
        is_ready = True

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()

    runtime._asr_session = _OldAsr()
    runtime._asr_route_mode = "independent"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    start_task = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(close_started.wait(), 1)

    assert runtime._asr_route_mode == "blocked"
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )

    release_close.set()
    await asyncio.wait_for(start_task, 1)
    assert runtime._asr_route_mode == "native"
    assert not hasattr(runtime._asr_runtime, "_asr_required")


async def test_explicit_intl_soniox_is_selected_before_audio(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("soniox", "provider")),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_not_awaited()
    await runtime._restart_transport(max_attempts=1)
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_received_audio is False


async def test_soniox_connect_failure_retries_same_selection_before_audio(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    soniox_selection = _selection("soniox", "provider")
    primary_resolver = MagicMock(return_value=soniox_selection)
    forbidden_core_resolver = MagicMock(
        side_effect=AssertionError("Soniox recovery must not resolve another provider")
    )
    save_settings = MagicMock(
        side_effect=AssertionError("Provider recovery must not rewrite user settings")
    )
    sleep = AsyncMock()
    sessions = []
    for side_effect in (
        RuntimeError("provider detail 1"),
        RuntimeError("provider detail 2"),
        None,
    ):
        session = type("Soniox", (), {})()
        session.connect = AsyncMock(side_effect=side_effect)
        session.close = AsyncMock()
        sessions.append(session)
    built_selections = []
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        primary_resolver,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        forbidden_core_resolver,
        raising=False,
    )
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        preferences,
        "save_global_conversation_settings",
        save_settings,
    )

    def build_candidate(_core_type, *, selection, **_kwargs):
        assert runtime._asr_provider == "soniox"
        built_selections.append(selection)
        assert selection is soniox_selection
        return sessions[len(built_selections) - 1]

    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        build_candidate,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    await runtime._restart_transport(max_attempts=3)
    sessions[0].close.assert_awaited_once_with()
    sessions[1].close.assert_awaited_once_with()
    sessions[2].close.assert_not_awaited()
    for session in sessions:
        session.connect.assert_awaited_once_with()
    primary_resolver.assert_called_once_with("gemini")
    forbidden_core_resolver.assert_not_called()
    save_settings.assert_not_called()
    assert built_selections == [soniox_selection] * 3
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    assert runtime._asr_session is sessions[2]
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_transport_selection is soniox_selection
    assert runtime._asr_lifecycle.provider_policy.endpoint_authority == "provider"
    assert runtime._asr_route_mode == "independent"
    assert "provider detail" not in str(runtime.send_status.await_args_list)
    assert "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE" not in str(
        runtime.send_status.await_args_list
    )


async def test_soniox_connect_retries_exhausted_blocks_without_provider_fallback(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.stream_audio = AsyncMock()
    soniox_selection = _selection("soniox", "provider")
    forbidden_core_resolver = MagicMock(
        side_effect=AssertionError("Soniox recovery must not resolve another provider")
    )
    sleep = AsyncMock()
    sessions = []
    for attempt in range(3):
        session = type("Soniox", (), {})()
        session.connect = AsyncMock(
            side_effect=RuntimeError(f"private provider detail {attempt}")
        )
        session.close = AsyncMock()
        sessions.append(session)
    built_selections = []

    def create_candidate(_core_type, *, selection, **_kwargs):
        built_selections.append(selection)
        assert selection is soniox_selection
        return sessions[len(built_selections) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=soniox_selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        forbidden_core_resolver,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._restart_transport(max_attempts=3)

    consumed = await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    )
    for session in sessions:
        session.connect.assert_awaited_once_with()
        session.close.assert_awaited_once_with()
    forbidden_core_resolver.assert_not_called()
    assert built_selections == [soniox_selection] * 3
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    assert runtime._asr_session is None
    assert runtime._asr_provider is None
    assert runtime._asr_route_mode == "blocked"
    assert consumed is True
    runtime.session.stream_audio.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert statuses[-1] == {
        "code": "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE",
        "details": {"provider": "soniox"},
    }
    assert "private provider detail" not in str(runtime.send_status.await_args_list)


async def test_failed_soniox_candidate_cannot_invalidate_successful_successor(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.websocket = type("WebSocket", (), {"send_json": AsyncMock()})()
    callbacks: list[dict[str, object]] = []

    failed_session = type("Soniox", (), {})()
    failed_session.connect = AsyncMock(side_effect=RuntimeError("provider detail"))
    failed_session.close = AsyncMock()
    successful_session = type("Soniox", (), {})()
    successful_session.connect = AsyncMock()
    successful_session.close = AsyncMock()
    soniox_selection = _selection("soniox", "provider")
    sessions = [failed_session, successful_session]

    def capture_partial(session, callback) -> None:
        session.partial_callback = callback

    def create_candidate(_core_type, *, selection, **kwargs):
        assert selection is soniox_selection
        callbacks.append(kwargs)
        return sessions[len(callbacks) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=soniox_selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        MagicMock(
            side_effect=AssertionError(
                "Soniox recovery must not resolve another provider"
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(runtime_module, "_attach_partial_callback", capture_partial)
    monkeypatch.setattr(runtime_module.asyncio, "sleep", AsyncMock())

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._restart_transport(max_attempts=2)
    adopted_epoch = runtime._asr_session_epoch

    await callbacks[0]["on_input_transcript"]("late soniox final")
    await callbacks[0]["on_speech_activity"](SpeechActivityEvent.SPEECH_STARTED)
    await failed_session.partial_callback("late soniox preview")
    await callbacks[0]["on_connection_error"]("late soniox error")
    await asyncio.sleep(0)

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.handle_interruption.assert_not_awaited()
    runtime.handle_new_message.assert_not_awaited()
    runtime.websocket.send_json.assert_not_awaited()
    successful_session.close.assert_not_awaited()
    assert runtime._asr_session is successful_session
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_session_epoch == adopted_epoch


async def test_selection_failure_is_reported_without_escaping_session_start(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(side_effect=ValueError("invalid provider configuration")),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_required")
    assert runtime._asr_session is None
    assert runtime._asr_provider is None
    assert "ASR_INDEPENDENT_FAILED" in runtime.send_status.await_args.args[0]
    assert "invalid provider configuration" not in str(
        runtime.send_status.await_args_list
    )


async def test_selection_failure_during_core_change_stays_blocked(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._independent_asr_route_key = "openai"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(side_effect=ValueError("invalid region configuration")),
    )

    await runtime._reconcile_independent_asr_after_core_change()

    assert runtime._independent_asr_route_key == "gemini"
    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "ASR_INDEPENDENT_FAILED" in runtime.send_status.await_args.args[0]


@pytest.mark.parametrize("core_type", ["qwen", "qwen_intl"])
async def test_qwen_core_starts_independent_asr_with_external_turn_support(
    monkeypatch,
    core_type: str,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = core_type
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("qwen")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    factory.assert_not_called()
    await runtime._restart_transport(max_attempts=1)
    factory.assert_called_once()
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "qwen"


async def test_websocket_core_submits_one_external_turn_after_local_history() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime.session.submit_external_voice_turn = AsyncMock()
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_independent_asr_final(" hello ", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "qwen"},
    )
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    call = runtime.session.submit_external_voice_turn.await_args
    assert call.args == ("hello",)
    assert call.kwargs["turn_id"].startswith("asr-")
    runtime.session.create_response.assert_not_awaited()


async def test_partial_preview_is_display_only_and_epoch_guarded() -> None:
    runtime = _Runtime()
    websocket = type("WebSocket", (), {})()
    websocket.send_json = AsyncMock()
    runtime.websocket = websocket
    runtime.current_speech_id = "speech-current"
    epoch = runtime._asr_session_epoch
    _install_ready_lifecycle(runtime, "qwen")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    await runtime._send_independent_asr_preview(" draft ", epoch)
    await runtime._send_independent_asr_preview("stale", epoch + 1)

    websocket.send_json.assert_awaited_once_with(
        {
            "type": "user_transcript_preview",
            "text": "draft",
            "turn_id": "speech-current",
        }
    )
    runtime.handle_input_transcript.assert_not_awaited()


async def test_start_failure_blocks_omni_without_leaking_error(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "glm"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock(side_effect=RuntimeError("secret provider response"))
    asr.close = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("glm")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=asr),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._restart_transport(max_attempts=1)

    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "secret provider response" not in str(runtime.send_status.await_args)


async def test_builder_failure_stays_blocked_and_never_sends_audio_to_omni(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(side_effect=RuntimeError("private provider detail")),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._restart_transport(max_attempts=1)
    consumed = await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    )
    if not consumed:
        await runtime.session.stream_audio(b"\x00\x00")

    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert consumed is True
    runtime.session.stream_audio.assert_not_awaited()
    assert "private provider detail" not in str(runtime.send_status.await_args)


async def test_hot_swap_reuses_matching_asr_provider() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_hot_swap_replaces_asr_before_cached_audio_for_new_core() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with("audio")


@pytest.mark.parametrize("core_type", ["openai", "glm", "gemini"])
async def test_hot_swap_starts_independent_asr_after_core_route_change(
    core_type: str,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = core_type
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "blocked"
    runtime._independent_asr_route_key = "free"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with("audio")


async def test_hot_swap_does_not_retry_failed_same_core_route() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "blocked"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_session_activation_resolves_asr_before_frontend_ack() -> None:
    order: list[str] = []
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.input_cache_lock = asyncio.Lock()
    manager.is_active = False
    manager._session_turn_count = 0
    manager.session_start_failure_count = 1
    manager.session_start_last_failure_time = 1.0
    manager._memory_error_retry_after = 1.0
    manager._session_start_circuit_open = True
    manager.pending_agent_callbacks = []
    manager._activity_tracker = type(
        "Tracker", (), {"on_voice_mode": lambda self, value: None}
    )()
    manager.is_goodbye_silent = lambda: False
    manager._drain_pending_context_appends_before_ready = AsyncMock()
    manager._flush_pending_input_data = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager._start_independent_asr_if_enabled = AsyncMock(
        side_effect=lambda _mode: order.append("asr")
    )
    manager.send_session_started = AsyncMock(
        side_effect=lambda _mode: order.append("started")
    )

    stop = asyncio.Event()

    class _Session:
        async def handle_messages(self) -> None:
            await stop.wait()

    manager.session = _Session()

    await LLMSessionManager._start_session_activate(
        manager,
        "audio",
        0,
        time.time(),
    )

    assert order == ["asr", "started"]
    stop.set()
    await manager.message_handler_task


async def test_disabled_or_text_session_never_creates_provider(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    factory = MagicMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._start_independent_asr_if_enabled("text")

    factory.assert_not_called()
    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")


async def test_free_core_reports_unavailable_and_blocks_omni(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "free"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert "ASR_INDEPENDENT_UNAVAILABLE" in runtime.send_status.await_args.args[0]


async def test_provider_error_without_audio_closes_and_blocks_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_error(epoch, "glm")
    await asyncio.sleep(0)

    assert runtime._asr_session_epoch == epoch + 1
    assert runtime._asr_route_mode == "blocked"
    asr.close.assert_awaited_once_with()


async def test_blocked_route_consumes_audio_without_an_asr_or_omni_send() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "blocked"

    assert (
        await runtime._route_microphone_audio(
            b"\x00\x00",
            sample_rate_hz=16_000,
        )
        is True
    )
    assert runtime._asr_route_mode == "blocked"


async def test_independent_route_without_ready_session_blocks_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": False})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )
    assert runtime._asr_route_mode == "blocked"


async def test_settings_read_failure_blocks_omni(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(side_effect=RuntimeError("settings unavailable")),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )


async def test_injection_failure_is_reported_once_without_provider_body() -> None:
    runtime = _Runtime()
    runtime.session.create_response.side_effect = RuntimeError("sensitive response")
    await _start_and_seal_turn(runtime, "gemini")

    await runtime._handle_independent_asr_final(
        "hello",
        runtime._asr_session_epoch,
        "gemini",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    status_payloads = [call.args[0] for call in runtime.send_status.await_args_list]
    assert any("ASR_INDEPENDENT_INJECTION_FAILED" in item for item in status_payloads)
    assert "sensitive response" not in str(status_payloads)
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_session_swap_during_transcript_drops_old_final_injection() -> None:
    runtime = _Runtime()
    old_session = runtime.session
    new_session = type("Omni", (), {"create_response": AsyncMock()})()

    async def swap_session(*_args, **_kwargs) -> bool:
        runtime.session = new_session
        return True

    runtime.handle_input_transcript.side_effect = swap_session
    await _start_and_seal_turn(runtime, "glm")

    await runtime._handle_independent_asr_final(
        "belongs to old role",
        runtime._asr_session_epoch,
        "glm",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    old_session.create_response.assert_not_awaited()
    new_session.create_response.assert_not_awaited()


async def test_status_delivery_failure_never_breaks_audio_runtime() -> None:
    runtime = _Runtime()
    runtime.send_status.side_effect = RuntimeError("socket closed")

    await runtime._send_asr_status("ASR_INDEPENDENT_READY", "glm")
