import multiprocessing
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_identity_service import wake_resources as module
from main_logic.voice_input.wake_word.errors import WakeWordFailureReason, safe_wake_word_reason
from main_logic.voice_identity_service.wake_word_bundle import WakeWordBundleError
from main_logic.voice_input.wake_word.sherpa_backend import (
    SherpaWakeWordConfig, SherpaWakeWordDetector, WakeWordBackendError,
)
from main_logic.voice_identity_service.session_activation_factory import OwnerVoiceSessionActivationFactory
from main_logic.voice_identity_service import session_activation_factory as factory_module
from main_logic.voice_input.activation import ActivationState
from tests.unit.voice_identity_service.test_session_activation_factory import (
    _Scorer, _profile, _generation, _frame,
)


@pytest.mark.parametrize("enabled,explicit,cached,reason,expected", [
    (False, None, "/cached", None, module.WakeWordResources(False)),
    (True, "/explicit", "/cached", None, module.WakeWordResources(True, "/explicit")),
    (True, None, "/cached", None, module.WakeWordResources(True, "/cached")),
    (True, None, None, None, module.WakeWordResources(True, reason="WAKE_WORD_MODEL_MISSING")),
    (False, None, None, "read_failed", module.WakeWordResources(True, reason="WAKE_WORD_PREFERENCE_UNAVAILABLE")),
])
def test_resource_discovery_respects_preference_and_explicit_priority(monkeypatch, enabled, explicit, cached, reason, expected):
    monkeypatch.setattr(module, "wake_word_preference", lambda: {"enabled": enabled, "reason": reason})
    monkeypatch.setattr(module, "wake_word_model_dir", lambda: explicit)
    calls = []

    def discover():
        calls.append("discovered")
        return cached

    monkeypatch.setattr(module, "resolve_cached_model_dir", discover)
    assert module.resolve_wake_word_resources() == expected
    if not enabled or explicit or reason:
        assert not calls


def test_corrupt_cache_has_model_failure_reason(monkeypatch):
    monkeypatch.setattr(module, "wake_word_preference", lambda: {"enabled": True, "reason": None})
    monkeypatch.setattr(module, "wake_word_model_dir", lambda: None)

    def corrupt():
        raise WakeWordBundleError("wake_model_invalid")

    monkeypatch.setattr(module, "resolve_cached_model_dir", corrupt)
    assert module.resolve_wake_word_resources().reason == "WAKE_WORD_MODEL_INVALID"


@pytest.mark.parametrize("reason", list(WakeWordFailureReason))
def test_real_ipc_preserves_only_shared_safe_failure_reason(reason):
    detector = SherpaWakeWordDetector(SherpaWakeWordConfig("unused", ("Y UW1 IY0 @yui",)))
    parent, child = multiprocessing.Pipe()
    try:
        detector._connection = parent
        child.send((False, reason.value))
        with pytest.raises(WakeWordBackendError, match=reason.value):
            detector._exchange(None, 1.0)
    finally:
        detector._connection = None
        parent.close()
        child.close()


def test_worker_reason_does_not_disclose_exception_text():
    assert safe_wake_word_reason(RuntimeError("private-path-and-key")) == "WAKE_WORD_WORKER_FAILED"
    assert safe_wake_word_reason("WAKE_WORD_untrusted") == "WAKE_WORD_WORKER_FAILED"
    assert safe_wake_word_reason(ModuleNotFoundError(name="sherpa_onnx")) == "WAKE_WORD_RUNTIME_MISSING"
    assert safe_wake_word_reason(ModuleNotFoundError(name="other")) == "WAKE_WORD_WORKER_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["WAKE_WORD_MODEL_MISSING", "WAKE_WORD_MODEL_INVALID", "WAKE_WORD_PREFERENCE_UNAVAILABLE"])
async def test_enabled_missing_wake_cannot_fall_through_owner_activation(monkeypatch, reason):
    monkeypatch.setattr(factory_module, "CampPlusActivationScorer", _Scorer)
    profile = _profile()
    output = AsyncMock()
    factory = OwnerVoiceSessionActivationFactory(object(), profile,
        activation_generation="current", enforce=True,
        wake_resources=module.WakeWordResources(True, reason=reason))
    runtime = factory.create(_generation(), output)
    try:
        decision = await runtime.prepare()
        assert decision.state is ActivationState.UNAVAILABLE
        assert decision.reason == reason
        await runtime.feed(_frame(_generation()), voice_activity=True)
        output.assert_not_awaited()
    finally:
        await runtime.close()
        factory.close()
        profile.close()


@pytest.mark.asyncio
async def test_explicitly_disabled_wake_keeps_pure_voiceprint_available(monkeypatch):
    monkeypatch.setattr(factory_module, "CampPlusActivationScorer", _Scorer)
    monkeypatch.setattr(factory_module, "wake_word_model_dir", lambda: "/broken-model")
    profile = _profile()
    factory = OwnerVoiceSessionActivationFactory(object(), profile,
        activation_generation="current", enforce=True,
        wake_resources=module.WakeWordResources(False))
    runtime = factory.create(_generation(), AsyncMock())
    try:
        assert (await runtime.prepare()).state is ActivationState.WAITING
    finally:
        await runtime.close()
        factory.close()
        profile.close()
