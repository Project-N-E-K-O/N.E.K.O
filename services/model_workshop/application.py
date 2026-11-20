"""Apply an installed selection through the original character-save handler."""

from __future__ import annotations

import json

from starlette.requests import Request

from .market import Launch
from .paths import WorkshopError


async def apply_installed(config, launch: Launch):
    """A private in-process guard shares the normal save's actual mutation lock.

    The browser never supplies this guard or a role name/path. Ordinary saves
    have no guard and retain their existing behavior and settings semantics.
    """
    from main_routers.characters_router import live2d_models as models
    from main_routers.shared_state import get_session_manager
    from main_routers.characters_router.voice_registry import (
        _is_current_catgirl_voice_session_starting,
    )

    if launch.phase != "installed" or not launch.result:
        raise WorkshopError("model_market_not_installed")
    result = launch.result
    kind = result["model_format"]
    payload = ({"model_type": "live2d", "live2d": result["name"]}
               if kind == "live2d" else {"model_type": "live3d", kind: result["url"]})

    def guard(actual, characters):
        if actual is not config:
            raise WorkshopError("model_market_target_changed")
        launch.check(config, characters)
        managers = get_session_manager()
        manager = managers.get(launch.name)
        # Use the same voice/start predicates as the existing voice-status API.
        from main_logic.omni_realtime_client import OmniRealtimeClient
        if (_is_current_catgirl_voice_session_starting(launch.name, characters, managers)
                or (manager and manager.is_active and (
                    manager.input_mode == "audio" or isinstance(manager.session, OmniRealtimeClient)))):
            raise WorkshopError("model_market_voice_busy")
        # This is the role commit gate, distinct from the installer's file gate.
        # Cancellation after this point reports the real save result, not rollback.
        launch.phase = "applying"

    raw = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request({"type": "http", "method": "PUT", "path": "/",
                       "query_string": b"", "headers": [],
                       "state": {"model_workshop_guard": guard,
                                 "model_workshop_saved": lambda: setattr(launch, "phase", "saved")}}, receive)
    response = await models.update_catgirl_l2d(launch.name, request)
    data = json.loads(response.body)
    if not data.get("success"):
        if launch.phase == "saved":
            # Runtime refresh failed after persistence; never claim rollback or
            # overwrite a newer binding by trying to "undo" this real save.
            launch.error = "model_market_refresh_failed"
            return launch.public()
        launch.phase = "blocked"
        raise WorkshopError(data.get("error_code") or "model_market_apply_failed")
    launch.phase = "saved"
    return launch.public()
