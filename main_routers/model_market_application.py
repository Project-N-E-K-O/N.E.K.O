"""Apply an installed selection through the original character-save handler."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import unquote

from starlette.requests import Request

from utils.config_manager import get_reserved

from services.model_workshop.market import Launch
from services.model_workshop.paths import WorkshopError
from services.model_workshop.runtime import installed_selection
from services.model_workshop.tasks import _drain_sync


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

    def check_role(characters):
        # The original save calls this inside its lock. A no-op selection must
        # not write configuration, reset model settings or refresh the runtime.
        avatar = get_reserved(characters["猫娘"][launch.name], "avatar", default={})
        active = avatar.get("model_type") == ("live2d" if kind == "live2d" else "live3d")
        active = active and (kind == "live2d" or avatar.get("live3d_sub_type") == kind)
        current_path = avatar.get(kind, {}).get("model_path")
        # Live2D persistence deliberately strips the URL prefix; use its existing
        # catalog normalization while retaining the source check against name collisions.
        if kind == "live2d":
            normalized = models._normalize_live2d_catalog_path(current_path)
            if isinstance(current_path, str) and (current_path.startswith("/") or "://" in current_path):
                normalized = unquote(normalized)
            # The existing API also persists a bare managed directory identity.
            same_path = normalized in {result["name"], unquote(models._normalize_live2d_catalog_path(result["url"]))}
        else:
            same_path = current_path == result["url"]
        if active and avatar.get("asset_source") == "local_imported" and same_path:
            launch.result = {**result, "already_active": True}
            launch.phase = "completed"
            return True
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
        return False

    async def guard(actual, characters):
        if actual is not config:
            raise WorkshopError("model_market_target_changed")

        async def commit_gate():
            launch.check(config, characters)
            return check_role(characters)

        loop = asyncio.get_running_loop()

        def verify():
            # The worker owns the lifecycle lock through the small Main gate;
            # release it before the original asynchronous character save.
            with installed_selection(config, result):
                return asyncio.run_coroutine_threadsafe(commit_gate(), loop).result()

        return await _drain_sync(verify)

    raw = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request({"type": "http", "method": "PUT", "path": "/",
                       "query_string": b"", "headers": [],
                       "state": {"model_workshop_guard": guard,
                                 "model_workshop_saved": lambda: launch.saved(config)}}, receive)
    response = await models.update_catgirl_l2d(launch.name, request)
    data = json.loads(response.body)
    if not data.get("success"):
        if launch.phase == "saved":
            # Runtime refresh failed after persistence; never claim rollback or
            # overwrite a newer binding by trying to "undo" this real save.
            launch.error = "model_market_refresh_failed"
            return launch.public()
        code = data.get("error_code") or "model_market_apply_failed"
        if code != "market_auth_pending":
            launch.phase = "blocked"
        raise WorkshopError(code)
    if launch.phase != "completed":
        launch.phase = "saved"
    return launch.public()
