"""Original ownership, cancellation races and display-only image delivery."""
import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from starlette.websockets import WebSocketState

from main_logic.core._shared import _ReplyTurn
from main_logic.core.turn import TurnMixin
from main_logic.reply_tail import ReplyTailRegistry
from main_routers import tool_router
from plugin.sdk.shared.core.context import SdkContext

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def image_part(kind="PNG"):
    out = BytesIO()
    image = Image.new("RGB", (2, 2), "red")
    if kind == "GIF":
        image.save(out, format=kind, save_all=True,
                   append_images=[Image.new("RGB", (2, 2), "green")], duration=80, loop=0)
    else:
        image.save(out, format=kind)
    return {
        "type": "image", "binary_base64": base64.b64encode(out.getvalue()).decode(),
        "mime": "image/" + kind.lower(),
    }


@pytest.fixture
def chain():
    registry = ReplyTailRegistry()
    websocket = SimpleNamespace(
        state=SimpleNamespace(reply_tail_version=1), client_state=WebSocketState.CONNECTED,
        send_json=AsyncMock(),
    )
    session = object()
    owner = _ReplyTurn("speech-A", "request-A", session=session)
    manager = SimpleNamespace(
        lanlan_name="Cat", session=session, websocket=websocket,
        _active_text_request_id="request-A", current_speech_id="speech-A",
    )
    manager.render_chat_blocks = TurnMixin.render_chat_blocks.__get__(manager)
    context = registry.tool_context(manager, owner, "call-A", "plugin:stickers")
    return SimpleNamespace(registry=registry, manager=manager, owner=owner, context=context)


async def register(chain, kind="PNG", registration_id="image-A"):
    return await chain.registry.register(
        chain.context, registration_id, [image_part(kind)], plugin_id="stickers",
    )


async def finish(chain):
    chain.owner.reply_tail.completed = True
    chain.owner.turn_ended = True
    await chain.registry.finish(chain.owner.reply_tail)


@pytest.mark.parametrize("kind", ["PNG", "GIF", "WEBP", "JPEG"])
async def test_waits_for_original_completion_preserving_image_bytes(chain, kind):
    part = image_part(kind)
    result = await chain.registry.register(chain.context, "image-A", [part], plugin_id="stickers")
    assert result["status"] == "registered"
    chain.manager.websocket.send_json.assert_not_awaited()
    await finish(chain)
    await finish(chain)
    frame = chain.manager.websocket.send_json.call_args.args[0]
    assert frame["type"] == "chat_blocks"
    assert frame["request_id"] == "request-A"
    assert frame["reply_tail"]["reply_id"] == chain.context["reply_id"]
    assert frame["blocks"][0]["url"].split(",", 1)[1] == part["binary_base64"]
    assert frame["metadata"]["source_name"] == "stickers"
    chain.manager.websocket.send_json.assert_awaited_once()
    assert chain.registry.status(chain.context, "image-A", plugin_id="stickers")["status"] == "submitted"
    assert chain.registry._retained_bytes == 0


@pytest.mark.parametrize("changed", ["next_request", "renewal", "takeover_after_completion"])
async def test_completed_original_survives_later_input_and_normal_renewal(chain, changed):
    await register(chain)
    if changed == "next_request":
        chain.manager._active_text_request_id = "request-B"
    elif changed == "renewal":
        chain.owner.speech_id = chain.manager.current_speech_id = "speech-renewed"
        chain.manager.session = object()
    else:
        chain.owner.taken_over = True
    await finish(chain)
    assert chain.manager.websocket.send_json.call_args.args[0]["request_id"] == "request-A"


async def test_actual_reconnect_never_replays_to_new_connection(chain):
    await register(chain)
    old = chain.manager.websocket
    chain.manager.websocket = SimpleNamespace(send_json=AsyncMock())
    await finish(chain)
    old.send_json.assert_not_awaited()
    chain.manager.websocket.send_json.assert_not_awaited()
    assert chain.registry.status(chain.context, "image-A", plugin_id="stickers")["status"] == "failed"


@pytest.mark.parametrize("ended", [False, True])
async def test_turn_end_without_success_is_not_completion(chain, ended):
    await register(chain)
    chain.owner.turn_ended = ended
    await chain.registry.finish(chain.owner.reply_tail)
    assert chain.registry.status(chain.context, "image-A", plugin_id="stickers")["status"] == "cancelled"
    chain.manager.websocket.send_json.assert_not_awaited()
    assert chain.registry._retained_bytes == 0


async def test_retry_cancels_only_old_attempt(chain):
    await register(chain)
    chain.owner.reply_tail.discard_attempt()
    assert chain.registry.status(chain.context, "image-A", plugin_id="stickers")["status"] == "cancelled"
    new = chain.registry.tool_context(chain.manager, chain.owner, "call-B", "plugin:stickers")
    assert new["reply_id"] == chain.context["reply_id"] and new["attempt"] != chain.context["attempt"]
    assert (await register(chain, registration_id="stale"))["status"] == "cancelled"
    chain.context = new
    await register(chain, registration_id="fresh")
    await finish(chain)
    assert chain.manager.websocket.send_json.call_args.args[0]["reply_tail"]["registration_id"] == "fresh"


async def test_duplicate_registration_and_cancel_after_submission(chain):
    await register(chain)
    assert (await register(chain))["duplicate"]
    assert len(chain.registry._registrations) == 1
    await finish(chain)
    result = chain.registry.cancel(chain.context, "image-A", plugin_id="stickers")
    assert result["status"] == "submitted" and result["submitted_at"] > 0
    assert (await register(chain))["status"] == "submitted"


async def test_cancel_before_registration_leaves_tombstone(chain):
    assert chain.registry.cancel(chain.context, "image-A", plugin_id="stickers")["status"] == "cancelled"
    assert (await register(chain))["status"] == "cancelled"
    await finish(chain)
    chain.manager.websocket.send_json.assert_not_awaited()


async def test_cancel_during_decode_wins(chain, monkeypatch):
    import app.main_server.character_runtime as runtime

    validator = runtime._build_plugin_image_chat_blocks
    started, release = __import__("threading").Event(), __import__("threading").Event()

    def slow(parts):
        started.set()
        assert release.wait(5)
        return validator(parts)

    monkeypatch.setattr(runtime, "_build_plugin_image_chat_blocks", slow)
    task = asyncio.create_task(register(chain))
    assert await asyncio.to_thread(started.wait, 5)
    chain.registry.cancel(chain.context, "image-A", plugin_id="stickers")
    release.set()
    assert (await task)["status"] == "cancelled"
    assert chain.registry._retained_bytes == 0


async def test_cancel_while_transport_pending_reports_uncertainty_then_truth(chain):
    await register(chain)
    started, release = asyncio.Event(), asyncio.Event()

    async def send(_payload):
        started.set()
        await release.wait()

    chain.manager.websocket.send_json.side_effect = send
    task = asyncio.create_task(finish(chain))
    await started.wait()
    assert chain.registry.cancel(chain.context, "image-A", plugin_id="stickers")["status"] == "uncertain"
    release.set()
    await task
    assert chain.registry.cancel(chain.context, "image-A", plugin_id="stickers")["status"] == "submitted"


@pytest.mark.parametrize("field,value", [
    ("token", "wrong"), ("role", "Other"), ("request_id", "new"), ("source", "plugin:other"),
])
async def test_context_is_bound_to_original_plugin_and_reply(chain, field, value):
    chain.context = {**chain.context, field: value}
    assert (await register(chain))["reason"] == "invalid_context"


async def test_cross_plugin_invalid_images_and_read_behavior_rejected(chain):
    assert (await chain.registry.register(
        chain.context, "x", [image_part()], plugin_id="other",
    ))["reason"] == "invalid_context"
    for parts in ([{"type": "text", "text": "fake reply"}],
                  [{"type": "image", "url": "https://example.com/remote.png"}],
                  [{"type": "image", "binary_base64": "broken", "mime": "image/png"}]):
        assert not (await chain.registry.register(
            chain.context, "bad", parts, plugin_id="stickers",
        ))["accepted"]
    assert (await chain.registry.register(
        chain.context, "read", [image_part()], plugin_id="stickers", ai_behavior="read",
    ))["reason"] == "invalid_attachment"


async def test_bounds_and_expiry_never_trigger_send(chain, monkeypatch):
    import main_logic.reply_tail as module

    monkeypatch.setattr(module, "MAX_RETAINED_BYTES", 1)
    assert (await register(chain))["reason"] == "capacity"
    monkeypatch.setattr(module, "MAX_RETAINED_BYTES", 100000)
    await register(chain)
    for scope in chain.registry._scopes.values():
        scope.expires = 0
    assert chain.registry.status(chain.context, "image-A", plugin_id="stickers")["reason"] == "invalid_context"
    assert chain.registry._retained_bytes == 0
    chain.manager.websocket.send_json.assert_not_awaited()


async def test_old_frontend_and_unbound_or_closed_reply_get_no_context(chain):
    for changes in (
        {"turn_ended": True}, {"taken_over": True}, {"request_id": None}, {"session": None},
    ):
        owner = _ReplyTurn("s", "r", session=object())
        for key, value in changes.items():
            setattr(owner, key, value)
        assert chain.registry.tool_context(chain.manager, owner, "c", "plugin:stickers") is None
    chain.manager.websocket.state.reply_tail_version = 0
    assert chain.registry.tool_context(chain.manager, chain.owner, "c", "plugin:stickers") is None


async def test_real_sdk_facade_and_router_round_trip(chain, monkeypatch):
    from main_routers.cookies_login_router import verify_local_access
    from plugin.sdk.shared.core import reply_tail as sdk
    import config

    app = FastAPI()
    app.dependency_overrides[verify_local_access] = lambda: None
    app.include_router(tool_router.router)
    monkeypatch.setattr(tool_router, "reply_tail_registry", chain.registry)
    monkeypatch.setattr(config, "MAIN_SERVER_PORT", 48911)
    original = httpx.AsyncClient
    monkeypatch.setattr(sdk.httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.ASGITransport(app=app),
    ))
    ctx = SdkContext(SimpleNamespace(plugin_id="stickers"))
    part = image_part("GIF")
    raw = base64.b64decode(part.pop("binary_base64"))
    part["data"] = raw
    result = await ctx.reply_tail.register(chain.context, registration_id="a", parts=[part])
    assert result["status"] == "registered"
    assert part["data"] == raw
    stored = next(iter(chain.registry._registrations.values()))
    assert base64.b64decode(stored.blocks[0]["url"].split(",", 1)[1]) == raw
    assert (await ctx.reply_tail.status(chain.context, registration_id="a"))["status"] == "registered"
    assert (await ctx.reply_tail.cancel(chain.context, registration_id="a"))["status"] == "cancelled"
    assert (await ctx.reply_tail.register(
        {**chain.context, "version": 999}, registration_id="a", parts=[image_part()],
    ))["reason"] == "invalid_context"


async def test_router_inherits_actual_access_guard(chain, monkeypatch):
    app = FastAPI()
    app.include_router(tool_router.router)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "a" * 40)
    monkeypatch.setenv("NEKO_REMOTE_DEPLOYMENT", "1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.2", 1234)),
        base_url="http://remote.example",
    ) as client:
        response = await client.post("/api/tools/reply-tail/status", json={
            "context": chain.context, "plugin_id": "stickers", "registration_id": "a",
        })
    assert response.status_code in {401, 403}


@pytest.mark.parametrize("opt_in", [False, True])
async def test_real_tool_loop_and_core_completion_keep_old_tools_unchanged(monkeypatch, opt_in):
    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.tool_calling import ToolDefinition, ToolRegistry, ToolResult
    from tests.unit.test_offline_late_completion_turn_ownership import (
        _client, _text, _tool_call, _observe, _wire, _text_turn,
        _make_callback_media_manager,
    )
    import main_logic.reply_tail as module

    registry = ReplyTailRegistry()
    monkeypatch.setattr(module, "reply_tail_registry", registry)
    client = _client([
        [_text("first sentence"), _tool_call("call-A")],
        [_text("last sentence", "stop")],
    ])
    mgr = _observe(_make_callback_media_manager(client))
    mgr.websocket.state = SimpleNamespace(reply_tail_version=1)
    _wire(mgr, client)
    captured = []

    async def dispatch(call, metadata):
        captured.append(call)
        if opt_in:
            assert call.host_reply["request_id"] == "original-request"
            assert (await registry.register(
                call.host_reply, "image-A", [image_part("GIF")], plugin_id="stickers",
            ))["status"] == "registered"
        else:
            assert call.host_reply is None
        return ToolResult(call.call_id, call.name, {})

    mgr.tool_registry = ToolRegistry(remote_dispatcher=dispatch)
    mgr.tool_registry.register(ToolDefinition(
        "lookup", "", metadata={"source": "plugin:stickers", "reply_tail": opt_in},
    ))
    client.on_tool_call = ToolCallingMixin._make_tool_call_handler(mgr, client)
    monkeypatch.setattr("main_logic.core.dispatch_text_user_message", lambda *_: None)
    await _text_turn(mgr, "choose", "original-request")
    assert len(captured) == 1 and captured[0].reply_owner.request_id == "original-request"
    tails = [frame for frame in mgr.websocket.sent if "reply_tail" in frame]
    assert len(tails) == int(opt_in)
    if opt_in:
        end_index = next(i for i, frame in enumerate(mgr.websocket.sent) if frame.get("data") == "turn end")
        assert mgr.websocket.sent.index(tails[0]) > end_index
        assert registry._retained_bytes == 0


async def test_decorator_opt_in_is_additive_and_not_a_model_parameter():
    from plugin.sdk.plugin.llm_tool import llm_tool, LLM_TOOL_META_ATTR

    @llm_tool(name="old")
    async def old():
        return {}

    @llm_tool(name="new", reply_tail=True)
    async def new(_ctx=None):
        return {}

    old_payload = getattr(old, LLM_TOOL_META_ATTR).to_ipc_payload(plugin_id="stickers")
    new_payload = getattr(new, LLM_TOOL_META_ATTR).to_ipc_payload(plugin_id="stickers")
    assert "reply_tail" not in old_payload
    assert new_payload["reply_tail"] is True
    assert "_ctx" not in new_payload["parameters"]["properties"]


@pytest.mark.parametrize("opt_in", [False, True])
async def test_registration_transport_preserves_explicit_opt_in(monkeypatch, opt_in):
    from plugin.server.messaging import llm_tool_registry as bridge
    from main_logic.tool_calling import ToolRegistry

    manager = SimpleNamespace(lanlan_name="Cat", tool_registry=ToolRegistry())

    async def register_tool(definition, *, replace=False):
        manager.tool_registry.register(definition)
        return True

    manager.register_tool_and_sync = register_tool
    monkeypatch.setattr(tool_router, "get_session_manager", lambda: {"Cat": manager})
    monkeypatch.setattr(tool_router, "_ensure_dispatcher_bound", lambda *_: None)
    monkeypatch.setattr(tool_router, "_remote_tool_ledger", {})
    app = FastAPI()
    from main_routers.cookies_login_router import verify_local_access
    app.dependency_overrides[verify_local_access] = lambda: None
    app.include_router(tool_router.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        monkeypatch.setattr(bridge, "_get_http_client", lambda: client)
        result = await bridge.register_remote_tool(
            plugin_id="tail_transport_test", name="tail_transport_tool", description="",
            parameters={"type": "object", "properties": {}},
            timeout_seconds=20, role="Cat", reply_tail=opt_in,
        )
    assert result["ok"]
    metadata = manager.tool_registry.get("tail_transport_tool").metadata
    assert ("reply_tail" in metadata) == opt_in
    if opt_in:
        assert metadata["reply_tail"] is True
    bridge._plugin_tools.pop("tail_transport_test", None)


@pytest.mark.parametrize("mode", ["original", "spoofed", "wrong_plugin", "legacy"])
async def test_callback_reserved_context_and_old_arguments(chain, monkeypatch, mode):
    from plugin.server.routes import llm_tools as callback
    from contextlib import nullcontext

    trigger = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(callback, "state", SimpleNamespace(
        acquire_plugin_hosts_read_lock=nullcontext,
        plugin_hosts={"stickers": SimpleNamespace(trigger=trigger)},
    ))
    monkeypatch.setattr(callback, "has_plugin_tool", lambda *_: True)
    monkeypatch.setattr(callback, "get_plugin_tool_timeout", lambda *_: 20)
    arguments = {"sticker": "wave"}
    body = {"arguments": arguments, "call_id": "call-A"}
    if mode != "legacy":
        arguments["_ctx"] = {"host_reply": {"token": "model-spoof"}, "lanlan_name": "new-role"}
    if mode == "original":
        body["host_reply"] = chain.context
    elif mode == "wrong_plugin":
        body["host_reply"] = {**chain.context, "source": "plugin:other"}
    result = await callback.llm_tool_callback("stickers", "send", body)
    assert not result["is_error"]
    actual = trigger.call_args.args[1]
    if mode == "original":
        assert actual["_ctx"] == {"host_reply": chain.context, "lanlan_name": "Cat"}
        assert arguments["_ctx"]["host_reply"] == {"token": "model-spoof"}
    elif mode != "legacy":
        assert "host_reply" not in actual["_ctx"]
    else:
        assert actual == {"sticker": "wave"}


async def test_remote_dispatch_does_not_add_context_to_legacy_wire(chain, monkeypatch):
    from main_logic.tool_calling import ToolCall

    client = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(200, json={"output": {}})))
    monkeypatch.setattr(tool_router, "_get_http_client", lambda: client)
    monkeypatch.setattr(tool_router, "_note_dispatch_outcome", lambda *_args, **_kwargs: None)
    metadata = {"callback_url": "http://127.0.0.1:48916/callback", "source": "plugin:stickers"}
    call = ToolCall(name="send", arguments={"sticker": "wave"}, call_id="call-A")
    await tool_router._remote_dispatch(call, metadata)
    assert "host_reply" not in client.post.call_args.kwargs["json"]
    call.host_reply = chain.context
    await tool_router._remote_dispatch(call, metadata)
    assert client.post.call_args.kwargs["json"]["host_reply"] == chain.context


async def test_interrupted_submission_settles_other_unsent_images(chain):
    await register(chain, registration_id="first")
    await register(chain, registration_id="second")
    chain.manager.websocket.send_json.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await finish(chain)
    assert chain.registry.status(chain.context, "first", plugin_id="stickers")["reason"] == "submission_uncertain"
    assert chain.registry.status(chain.context, "second", plugin_id="stickers")["status"] == "cancelled"
    assert chain.registry._retained_bytes == 0


async def test_validation_capacity_survives_caller_cancellation(chain, monkeypatch):
    import main_logic.reply_tail as module
    import app.main_server.character_runtime as runtime
    import threading

    monkeypatch.setattr(module, "MAX_VALIDATIONS", 1)
    original = runtime._build_plugin_image_chat_blocks
    started, release = threading.Event(), threading.Event()

    def parked(parts):
        started.set()
        assert release.wait(5)
        return original(parts)

    monkeypatch.setattr(runtime, "_build_plugin_image_chat_blocks", parked)
    task = asyncio.create_task(register(chain))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert chain.registry._validations == 1
    assert (await register(chain, registration_id="other"))["reason"] == "capacity"
    release.set()
    for _ in range(100):
        if chain.registry._validations == 0:
            break
        await asyncio.sleep(0.01)
    assert chain.registry._validations == 0
    assert chain.registry._retained_bytes == 0
