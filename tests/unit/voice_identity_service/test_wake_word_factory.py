from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS, wake_word_model_dir
from main_logic.voice_identity_service import session_activation_factory as factory_module
from tests.unit.voice_identity_service.test_session_activation_factory import (
    _generation,
    _profile,
    fake_scorer,
)


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_unconfigured_wake_words_remain_optional(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("NEKO_WAKE_WORD_MODEL_DIR", raising=False)
    else:
        monkeypatch.setenv("NEKO_WAKE_WORD_MODEL_DIR", configured)
    assert wake_word_model_dir() is None


@pytest.mark.asyncio
async def test_factory_without_model_does_not_create_detector(monkeypatch, fake_scorer):
    monkeypatch.delenv("NEKO_WAKE_WORD_MODEL_DIR", raising=False)
    monkeypatch.setattr(
        factory_module, "SherpaWakeWordDetector",
        lambda *_: pytest.fail("unconfigured wake detector was instantiated"),
    )
    profile = _profile()
    factory = factory_module.OwnerVoiceSessionActivationFactory(
        object(), profile, activation_generation="test", enforce=True,
    )
    runtime = factory.create(_generation(), AsyncMock())
    await runtime.close()
    factory.close()
    profile.close()


@pytest.mark.asyncio
async def test_factory_owns_one_detector_per_microphone_runtime(monkeypatch, fake_scorer):
    monkeypatch.setenv("NEKO_WAKE_WORD_MODEL_DIR", " C:/models/kws ")
    detectors = []

    class Detector:
        def __init__(self, config):
            self.config = config
            self.close = AsyncMock()
            detectors.append(self)

    monkeypatch.setattr(factory_module, "SherpaWakeWordDetector", Detector)
    profile = _profile()
    factory = factory_module.OwnerVoiceSessionActivationFactory(
        object(), profile, activation_generation="test", enforce=True,
    )
    first = factory.create(_generation(route=1), AsyncMock())
    second = factory.create(_generation(route=2), AsyncMock())
    assert len(detectors) == 2
    assert detectors[0] is not detectors[1]
    assert str(detectors[0].config.model_dir) == "C:/models/kws"
    assert detectors[0].config.keywords == DEFAULT_WAKE_WORD_KEYWORDS
    await first.close()
    detectors[0].close.assert_awaited_once()
    detectors[1].close.assert_not_awaited()
    await second.close()
    detectors[1].close.assert_awaited_once()
    factory.close()
    profile.close()


@pytest.mark.asyncio
async def test_disabled_activation_never_constructs_wake_detector(monkeypatch, fake_scorer):
    monkeypatch.setenv("NEKO_WAKE_WORD_MODEL_DIR", "C:/models/kws")
    monkeypatch.setattr(
        factory_module, "SherpaWakeWordDetector",
        lambda *_: pytest.fail("disabled activation constructed wake detector"),
    )
    profile = _profile()
    factory = factory_module.OwnerVoiceSessionActivationFactory(
        object(), profile, activation_generation="test", enforce=False,
    )
    runtime = factory.create(_generation(), AsyncMock())
    await runtime.prepare()
    await runtime.close()
    factory.close()
    profile.close()
