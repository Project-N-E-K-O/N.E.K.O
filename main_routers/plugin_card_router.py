"""Same-origin action forwarding for chat windows, including remote browsers."""
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.responses import Response

from config.network import resolve_user_plugin_base
from utils.http.internal_client import get_internal_http_client
from main_routers.system_router._shared import _validate_local_mutation_request


class _PluginCardMutationRoute(APIRoute):
    """Reject cross-site card actions before FastAPI consumes the request body.

    The main server is a browser-facing proxy.  A request forwarded from here
    becomes an originless loopback request at the plugin server, so guarding
    only the downstream route would leave this ingress as a CSRF bypass.
    """

    def get_route_handler(self):
        downstream = super().get_route_handler()

        async def guarded(request: Request):
            rejected = _validate_local_mutation_request(request)
            if rejected is not None:
                return rejected
            return await downstream(request)

        return guarded



router = APIRouter(tags=["plugin-cards"], route_class=_PluginCardMutationRoute)


class CardActionRequest(BaseModel):
    card_id: str = Field(min_length=1)
    target_lanlan: str = Field(min_length=1)
    args: dict = Field(default_factory=dict)
    locale: str | None = None
    presentation: Literal["chat", "agent"] = "chat"


@router.post("/api/plugin-cards/{plugin_id}/action/{action_id}")
async def card_action(plugin_id: str, action_id: str, request: CardActionRequest):
    base = resolve_user_plugin_base().rstrip("/")
    path = f"/plugin/{quote(plugin_id, safe='')}/chat-card/action/{quote(action_id, safe='')}"
    try:
        response = await get_internal_http_client().post(
            base + path, json=request.model_dump(), timeout=30.0,
        )
    except httpx.TimeoutException:
        raise HTTPException(504, {"code": "plugin_card_action_timeout"}) from None
    except httpx.HTTPError:
        raise HTTPException(502, {"code": "plugin_card_server_unavailable"}) from None
    return Response(response.content, status_code=response.status_code, media_type="application/json")
