"""reply-completed callbacks dispatch only to a plugin's registered entries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from plugin.core import registry as registry_module
from plugin.core.state import GlobalState
from plugin.sdk.plugin import NekoPluginBase, lifecycle, plugin_entry
from plugin.server.routes import llm_tools as callback_module

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PLUGIN = "stickers"


class StickerPlugin(NekoPluginBase):
    @plugin_entry(id="choose", kind="hook")
    async def choose(self, text: str, user_text: str, has_attachments: bool, _ctx=None):
        return None

    @plugin_entry(id="_hidden")
    async def _hidden(self, **_):
        return None

    @lifecycle(id="startup")
    async def startup(self, **_):
        return None

    async def helper(self, **_):
        return None


@pytest.fixture
def host(monkeypatch):
    """A fresh host registry populated by the real static scanner."""
    fresh = GlobalState()
    monkeypatch.setattr(registry_module, "state", fresh)
    monkeypatch.setattr(callback_module, "state", fresh)
    monkeypatch.setattr(registry_module, "plugin_entry_method_map", {})
    registry_module.scan_static_metadata(PLUGIN, StickerPlugin, {}, {})
    process = SimpleNamespace(trigger=AsyncMock(return_value=None), is_alive=lambda: True)
    fresh.plugin_hosts[PLUGIN] = process
    return SimpleNamespace(state=fresh, trigger=process.trigger)


def body_for(plugin_id: str, entry_id: str) -> dict:
    # The body main_server sends: a host_reply scope whose fields match the URL.
    return {
        "text": "reply", "user_text": "hello", "has_attachments": False, "timeout_seconds": 15.0,
        "host_reply": {
            "version": 1, "source": "plugin:" + plugin_id,
            "call_id": "reply_completed:" + entry_id, "role": "Cat",
        },
    }


async def post(plugin_id: str, entry_id: str, **kwargs) -> httpx.Response:
    app = FastAPI()
    app.include_router(callback_module.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
    ) as client:
        return await client.post(
            f"/api/llm-tools/reply-completed/{plugin_id}/{entry_id}",
            json=kwargs.pop("json", body_for(plugin_id, entry_id)), **kwargs,
        )


async def test_registered_entry_receives_callback(host):
    response = await post(PLUGIN, "choose")
    assert response.status_code == 200 and response.json() == {"ok": True}
    host.trigger.assert_awaited_once()
    entry_id, arguments, timeout = host.trigger.await_args.args
    assert entry_id == "choose" and timeout == 15.0
    assert arguments["text"] == "reply" and arguments["user_text"] == "hello"
    assert arguments["_ctx"]["lanlan_name"] == "Cat"


async def test_dynamic_entry_registered_at_runtime_receives_callback(host):
    from plugin._types.events import EventHandler, EventMeta

    host.state.register_event_handler(PLUGIN, EventHandler(
        meta=EventMeta(event_type="plugin_entry", id="late", name="late", dynamic=True),
        handler=lambda *a, **k: None,
    ))
    assert (await post(PLUGIN, "late")).status_code == 200
    assert host.trigger.await_args.args[0] == "late"
    host.state.unregister_event_handler(PLUGIN, "late")
    host.trigger.reset_mock()
    assert (await post(PLUGIN, "late")).status_code == 404
    host.trigger.assert_not_awaited()


@pytest.mark.parametrize("entry_id", [
    "helper",              # coroutine method that is not a registered entry
    "_hidden",             # private name, even though decorated
    "__llm_tool__choose",  # reserved dynamic LLM-tool namespace
    "startup",             # lifecycle handler
    "missing",             # nothing by that name
    "choose.extra",        # only the exact registered id counts
])
async def test_unregistered_entries_are_rejected_without_dispatch(host, entry_id):
    # The body is self-consistent with the URL, exactly like a real callback.
    response = await post(PLUGIN, entry_id)
    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "ENTRY_NOT_REGISTERED"}
    host.trigger.assert_not_awaited()


async def test_entry_of_another_plugin_is_rejected(host):
    other = SimpleNamespace(trigger=AsyncMock(), is_alive=lambda: True)
    host.state.plugin_hosts["other"] = other
    response = await post("other", "choose")
    assert response.status_code == 404
    other.trigger.assert_not_awaited()
    host.trigger.assert_not_awaited()


async def test_unknown_plugin_is_rejected(host):
    response = await post("nobody", "choose")
    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "PLUGIN_NOT_RUNNING"}
    host.trigger.assert_not_awaited()


async def test_rejection_does_not_depend_on_request_content_type(host):
    import json

    payload = json.dumps(body_for(PLUGIN, "helper"))
    for content_type in ("application/json", "text/plain", "application/json; charset=utf-8"):
        response = await post(PLUGIN, "helper", json=None, content=payload,
                              headers={"content-type": content_type})
        assert 400 <= response.status_code < 500
    host.trigger.assert_not_awaited()


async def test_endpoint_itself_rejects_without_framework_validation(host):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await callback_module.reply_completed_callback(PLUGIN, "helper", body_for(PLUGIN, "helper"))
    assert raised.value.status_code == 404
    host.trigger.assert_not_awaited()
    result = await callback_module.reply_completed_callback(PLUGIN, "choose", body_for(PLUGIN, "choose"))
    assert result == {"ok": True}


async def test_entry_check_reads_only_plugin_entry_handlers(host):
    # The scanner did register these keys; the check still refuses them.
    assert f"{PLUGIN}:plugin_entry:_hidden" in host.state.event_handlers
    assert f"{PLUGIN}:lifecycle:startup" in host.state.event_handlers
    assert callback_module._is_reply_hook_entry(PLUGIN, "choose")
    for entry_id in ("startup", "helper", "_hidden", "", "x" * 129, "a/b", "a b"):
        assert not callback_module._is_reply_hook_entry(PLUGIN, entry_id)
