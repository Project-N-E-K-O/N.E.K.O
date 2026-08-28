"""Narrow application-install/router-read registry for voice identity."""

from __future__ import annotations

import threading

from .service import VoiceIdentityService


class VoiceIdentityServiceRegistryError(RuntimeError):
    """Raised when the application service is absent or replaced."""


_lock = threading.Lock()
_service: VoiceIdentityService | None = None


def install_voice_identity_service_for_app(service: VoiceIdentityService) -> None:
    """Install the application-owned singleton exactly once."""

    if not isinstance(service, VoiceIdentityService):
        raise TypeError("service must be VoiceIdentityService")
    global _service
    with _lock:
        if _service is service:
            return
        if _service is not None:
            raise VoiceIdentityServiceRegistryError(
                "voice identity service is already installed"
            )
        _service = service


def get_voice_identity_service_for_router() -> VoiceIdentityService:
    """Return the installed service to the HTTP control plane."""

    with _lock:
        if _service is None:
            raise VoiceIdentityServiceRegistryError(
                "voice identity service is unavailable"
            )
        return _service
