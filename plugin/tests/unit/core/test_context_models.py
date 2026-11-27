from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.core.context import PluginContext
from plugin.sdk.shared.core.context import SdkContext
from plugin.sdk.shared.models.exceptions import CapabilityUnavailableError


pytestmark = pytest.mark.plugin_unit


def _context(tmp_path: Path) -> PluginContext:
    return PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=None,
        status_queue=None,
        _model_gateway_base_url="http://127.0.0.1:48916/api/models/v1",
        _model_gateway_token="private-instance-token",
    )


def test_gateway_details_are_not_in_context_repr(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert "private-instance-token" not in repr(context)
    assert "48916" not in repr(context)
    assert context.models is context.models
    assert SdkContext(context).models is context.models
    context.close()


def test_context_close_before_first_client_disables_gateway(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context._models is None
    context.close()
    with pytest.raises(CapabilityUnavailableError):
        asyncio.run(context.models.get_client())


@pytest.mark.asyncio
async def test_context_close_releases_client_and_blocks_new_requests(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    client = await context.models.get_client()
    context.close()
    await context.models.aclose()
    assert client.is_closed()
    with pytest.raises(CapabilityUnavailableError):
        await context.models.get_client()
    context.close()


_GATEWAY_INSTANCE_KEY = "test-only-gateway-signing-key-" + "k" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode_env",
    ["NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE"],
    ids=["behind-proxy", "activity-tracker-remote"],
)
async def test_gateway_client_is_admitted_by_instance_access_in_proxy_and_remote_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode_env: str,
) -> None:
    """ctx.models must sign its loopback hop: the Bearer gateway token alone is 401 in proxy/remote mode."""
    import httpx
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse

    from config import USER_PLUGIN_SERVER_PORT
    from plugin.sdk.shared.core import models as models_module
    from utils.instance_access import INTERNAL_SERVICE_HEADER, InstanceAccessMiddleware

    for env_key in ("NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", _GATEWAY_INSTANCE_KEY)
    monkeypatch.setenv(mode_env, "true")

    seen: list[dict[str, str]] = []
    gateway = Starlette()

    async def chat_completions(request):
        seen.append(dict(request.headers))
        assert request.headers.get("authorization") == "Bearer private-instance-token"
        return JSONResponse({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "demo",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "pong"},
                    "finish_reason": "stop",
                }
            ],
        })

    gateway.add_route("/api/models/v1/chat/completions", chat_completions, methods=["POST"])
    gateway.add_middleware(InstanceAccessMiddleware)
    transport = httpx.ASGITransport(app=gateway, client=("127.0.0.1", 50000))
    base_url = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/api/models/v1"

    # Control: the gateway token without the internal-service proof is refused
    # before the route runs, which is what plugin model calls hit before the fix.
    async with httpx.AsyncClient(transport=transport, trust_env=False) as unsigned:
        refused = await unsigned.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": "Bearer private-instance-token"},
            json={"model": "demo", "messages": []},
        )
    assert refused.status_code == 401
    assert "instance_authorization_required" in refused.text
    assert seen == []

    original_init = models_module._GatewayHttpClient.__init__

    def _init_with_asgi_transport(self, **kwargs):
        kwargs["transport"] = transport
        original_init(self, **kwargs)

    monkeypatch.setattr(models_module._GatewayHttpClient, "__init__", _init_with_asgi_transport)

    context = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=None,
        status_queue=None,
        _model_gateway_base_url=base_url,
        _model_gateway_token="private-instance-token",
    )
    try:
        client = await context.models.get_client()
        completion = await client.chat.completions.create(
            model="demo",
            messages=[{"role": "user", "content": "ping"}],
        )
        assert completion.choices[0].message.content == "pong"
        assert len(seen) == 1
        assert seen[0].get(INTERNAL_SERVICE_HEADER.lower())
    finally:
        await context.models.aclose()
        context.close()
