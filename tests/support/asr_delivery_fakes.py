"""Shared fakes and lifecycle helpers for protected ASR delivery tests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from main_logic.asr_client.lifecycle import VoiceInputLifecycleController, VoiceRouteMode
from main_logic.asr_client.provider_policy import resolve_provider_policy
from tests.support.asr_fakes import _Runtime, _ReadyDetector, _selection, DetectorFeedResult, SpeechActivityEvent


def _cold_runtime():
    runtime = _Runtime()
    lifecycle = VoiceInputLifecycleController(provider_policy=resolve_provider_policy("qwen", "provider"), shadow_mode=False)
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle = lifecycle
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_transport_selection = _selection("qwen")
    entered, ready = asyncio.Event(), asyncio.Event()
    sessions = []
    def factory(_selection):
        session = SimpleNamespace(is_ready=False)
        async def connect():
            entered.set(); await ready.wait(); session.is_ready = True
        session.connect = connect; session.close = AsyncMock(); session.stream_audio = AsyncMock(); session.signal_user_activity_end = AsyncMock()
        sessions.append(session); return session
    runtime._asr_session_factory = factory
    detector = _ReadyDetector(); calls = 0
    async def feed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,) if calls == 1 else (), True)
    detector.feed = AsyncMock(side_effect=feed); runtime._asr_detector = detector
    token = runtime._capture_ingress_token()
    from main_logic.voice_turn.contracts import PreserveUnsentPrefix
    prefix = PreserveUnsentPrefix(token, "first", 0)
    return runtime, lifecycle, detector, token, prefix, entered, ready, sessions


async def _close(runtime):
    await runtime._asr_runtime.abort("test_complete")
    await runtime._asr_audio_dispatcher.close()
    await runtime._asr_detector_dispatcher.close()
