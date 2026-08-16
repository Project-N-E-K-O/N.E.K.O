from __future__ import annotations

import pytest

import main_logic.voice_identity_service.registry as registry
from main_logic.voice_identity_service.registry import (
    VoiceIdentityServiceRegistryError,
    get_voice_identity_service_for_router,
    install_voice_identity_service_for_app,
)
from main_logic.voice_identity_service.service import VoiceIdentityService


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_service", None)


@pytest.mark.unit
def test_registry_requires_app_install() -> None:
    with pytest.raises(VoiceIdentityServiceRegistryError, match="unavailable"):
        get_voice_identity_service_for_router()

    with pytest.raises(TypeError, match="VoiceIdentityService"):
        install_voice_identity_service_for_app(object())  # type: ignore[arg-type]


@pytest.mark.unit
def test_registry_allows_one_idempotent_app_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = object.__new__(VoiceIdentityService)
    second = object.__new__(VoiceIdentityService)

    install_voice_identity_service_for_app(first)
    install_voice_identity_service_for_app(first)
    assert get_voice_identity_service_for_router() is first

    with pytest.raises(VoiceIdentityServiceRegistryError, match="already"):
        install_voice_identity_service_for_app(second)
