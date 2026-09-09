import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from plugin.core.state import state
from plugin.server.application.plugins import ui_query_service as queries
from plugin.server.messaging.proactive_bridge import ProactiveBridge
from plugin.server.routes.plugin_ui import router


@pytest.mark.parametrize("presentation", ["chat", "agent"])
def test_card_only_push_and_update_reach_the_event_bus(presentation):
    events = []
    sock = SimpleNamespace(send_json=lambda event, *args: events.append(event))
    bridge = ProactiveBridge()
    operations = [("create", {"html": "Hi", "summary": "Hi"}), ("update", {"summary": "New"})]
    if presentation == "agent":
        operations[0][1]["title"] = "Job"
        operations.append(("close", {}))
    for op, fields in operations:
        if presentation == "agent":
            fields = {**fields, "presentation": "agent"}
        bridge._dispatch({"schema": "push_message.v2", "plugin_id": "demo", "visibility": ["chat"],
                          "ai_behavior": "blind", "target_lanlan": "Alice", "parts": [
                              {"type": "html_card", "card_id": "one", "operation": op, **fields}]}, sock)
    assert [event["event_type"] for event in events] == ["plugin_card"] * len(operations)
    assert events[1]["card"]["summary"] == "New"
    assert events[0]["plugin_id"] == "demo"
    if presentation == "agent":
        assert all(event["card"]["presentation"] == "agent" for event in events)
        assert events[-1]["card"]["operation"] == "close"


@pytest.mark.parametrize("presentation", ["chat", "agent"])
def test_card_http_action_reuses_entry_checks_without_requiring_a_panel(monkeypatch, presentation):
    calls = []

    class Host:
        def is_alive(self):
            return True

        async def get_ui_context(self, context_id):
            assert context_id == "__chat_card__"
            return {"actions": [{"id": "play", "entry_id": "play_track"}]}

        async def trigger(self, entry_id, args, timeout):
            calls.append((entry_id, args))
            return {"message": "Playing"}

    monkeypatch.setattr(queries, "_get_plugin_meta_sync", lambda _: {"entries": [{"id": "play_track"}, {"id": "private"}]})
    monkeypatch.setattr(queries, "_resolve_hosted_entry_timeout", lambda *_: 30)
    monkeypatch.setitem(state.plugin_hosts, "demo", Host())
    app = FastAPI()
    app.include_router(router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            body = {"card_id": "one", "target_lanlan": "Alice", "args": {"track_id": "123", "_ctx": {"lanlan_name": "Wrong", "view_id": "forged"}}}
            if presentation == "agent":
                body["presentation"] = "agent"
            result = await client.post("/plugin/demo/chat-card/action/play", json=body)
            assert result.status_code == 200
            assert result.json()["result"]["message"] == "Playing"
            denied = await client.post("/plugin/demo/chat-card/action/private", json=body)
            assert denied.status_code == 403
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0][0] == "play_track"
    assert calls[0][1]["_ctx"]["card_id"] == "one"
    assert calls[0][1]["_ctx"]["lanlan_name"] == "Alice"
    assert calls[0][1]["_ctx"]["run_id"]
    if presentation == "agent":
        from plugin.sdk.shared.core.context import SdkContext

        sent = []
        ctx = SdkContext(SimpleNamespace(_current_lanlan="Wrong",
                         push_message=lambda **kw: sent.append(kw) or {"submitted": True}))
        action_context = calls[0][1]["_ctx"]
        view = ctx.get_view(action_context["view_id"], target_lanlan=action_context["lanlan_name"])
        asyncio.run(view.update(html="Playing"))
        assert sent[0]["target_lanlan"] == "Alice"
        assert sent[0]["parts"][0]["presentation"] == "agent"
        assert sent[0]["parts"][0]["card_id"] == "one"
    else:
        assert "view_id" not in calls[0][1]["_ctx"]


def test_invalid_action_presentation_is_rejected_before_dispatch():
    app = FastAPI()
    app.include_router(router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/plugin/demo/chat-card/action/play", json={
                "card_id": "one", "target_lanlan": "Alice", "presentation": "panel",
            })
            assert response.status_code == 422

    asyncio.run(run())


def test_view_action_through_same_origin_proxy_survives_outer_disconnect(monkeypatch):
    from main_routers import plugin_card_router as proxy
    from plugin.sdk.shared.core.context import SdkContext

    plugin_app = FastAPI()
    plugin_app.include_router(router)
    main_app = FastAPI()
    main_app.include_router(proxy.router)
    sent = []
    monkeypatch.setattr(queries, "_get_plugin_meta_sync", lambda _: {"entries": [{"id": "finish"}]})
    monkeypatch.setattr(queries, "_resolve_hosted_entry_timeout", lambda *_: 30)
    monkeypatch.setattr(proxy, "resolve_user_plugin_base", lambda: "http://plugin")

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        class Host:
            def is_alive(self):
                return True

            async def get_ui_context(self, context_id):
                assert context_id == "__chat_card__"
                return {"actions": [{"id": "finish", "entry_id": "finish"}]}

            async def trigger(self, entry_id, args, timeout):
                started.set()
                await release.wait()
                action_context = args["_ctx"]
                ctx = SdkContext(SimpleNamespace(push_message=lambda **kw: sent.append(kw) or {"submitted": True}))
                view = ctx.get_view(action_context["view_id"], target_lanlan=action_context["lanlan_name"])
                await view.update(html="Finished")
                return {"message": "Finished"}

        monkeypatch.setitem(state.plugin_hosts, "demo", Host())
        incoming = asyncio.Queue()
        body = json.dumps({"card_id": "one", "target_lanlan": "Alice", "presentation": "agent"}).encode()
        await incoming.put({"type": "http.request", "body": body, "more_body": False})
        response_messages = []

        async def send(message):
            response_messages.append(message)

        path = "/api/plugin-cards/demo/action/finish"
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "headers": [(b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 12345), "server": ("test", 80)}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(plugin_app), base_url="http://plugin") as upstream:
            monkeypatch.setattr(proxy, "get_internal_http_client", lambda: upstream)
            request = asyncio.create_task(main_app(scope, incoming.get, send))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                # Closing the content UI disconnects only the outer request.
                await incoming.put({"type": "http.disconnect"})
                await asyncio.sleep(0)
                release.set()
                await asyncio.wait_for(request, timeout=2)
            finally:
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)
        assert next(message["status"] for message in response_messages if message["type"] == "http.response.start") == 200

    asyncio.run(run())
    assert len(sent) == 1
    assert sent[0]["target_lanlan"] == "Alice"
    assert sent[0]["parts"][0] == {"type": "html_card", "presentation": "agent", "card_id": "one",
                                   "operation": "update", "html": "Finished"}


def test_real_plugin_host_lists_actions_without_running_context(tmp_path):
    from plugin.core.host import PluginProcessHost

    async def run():
        config = tmp_path / "plugin.toml"
        config.write_text("[plugin]\nname='card_fixture'\n")
        host = PluginProcessHost(plugin_id="card_fixture",
            entry_point="tests.fixtures.plugin_test_ui_context_fixture:HangingUiContextFixturePlugin",
            config_path=config)
        try:
            await host.start(message_target_queue=asyncio.Queue())
            context = await host.get_ui_context("__chat_card__", timeout=5)
            assert [a["id"] for a in context["actions"]] == ["ping"]
            assert not context.get("context_error")
            assert (await host.trigger("ping", {}))["ok"] is True
        finally:
            await host.shutdown(timeout=2)
    asyncio.run(run())
