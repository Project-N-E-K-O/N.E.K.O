"""Plugin-owned post-reply decisions, independent of model tool invocation."""
import ast
import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

import main_logic.reply_tail as module
from main_logic.reply_tail import ReplyHookUnavailableError, ReplyTailRegistry
from main_routers import tool_router
from main_routers.cookies_login_router import verify_local_access
from plugin.sdk.shared.core.context import SdkContext
from tests.unit.test_reply_tail import chain as chain
from tests.unit.test_reply_tail import finish, image_part, register

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def drain_hooks(registry):
    await asyncio.gather(*list(registry._hook_tasks), return_exceptions=True)
    assert not registry._hook_tasks


def prepare(chain, dispatch, *, role=None, timeout=15.0, entry="choose_image"):
    assert chain.registry.subscribe("stickers", entry, dispatch, role=role, timeout=timeout)["ok"]
    chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
    chain.owner.reply_tail.text = "final answer"


async def test_plugin_decides_and_registers_after_completion_without_a_tool(chain):
    contexts = []

    async def choose(payload):
        contexts.append(payload["host_reply"])
        assert payload["text"] == "final answer" and payload["user_text"] == "hello"
        assert payload["has_attachments"] is False
        assert (await chain.registry.register(
            payload["host_reply"], "chosen", [image_part("GIF")], plugin_id="stickers",
        ))["status"] == "registered"

    prepare(chain, choose)
    await finish(chain)
    await drain_hooks(chain.registry)
    chain.manager.websocket.send_json.assert_awaited_once()
    frame = chain.manager.websocket.send_json.call_args.args[0]
    assert frame["request_id"] == "request-A"
    assert frame["reply_tail"]["call_id"] == "reply_completed:choose_image"
    assert chain.registry.status(contexts[0], "chosen", plugin_id="stickers")["status"] == "submitted"
    assert (await chain.registry.register(
        contexts[0], "too-late", [image_part()], plugin_id="stickers",
    ))["status"] == "cancelled"


async def test_normal_reply_finish_does_not_wait_for_plugin_selection(chain):
    started, release = asyncio.Event(), asyncio.Event()

    async def choose(payload):
        started.set()
        await release.wait()

    prepare(chain, choose)
    await asyncio.wait_for(finish(chain), 0.5)
    await asyncio.wait_for(started.wait(), 0.5)
    assert chain.registry._hook_tasks
    await finish(chain)
    assert len(chain.registry._hook_tasks) == 1
    release.set()
    await drain_hooks(chain.registry)
    chain.manager.websocket.send_json.assert_not_awaited()


async def test_replacing_subscription_revokes_previous_callback_context(chain):
    started, release = asyncio.Event(), asyncio.Event()
    contexts = []

    async def choose(payload):
        contexts.append(payload["host_reply"])
        started.set()
        await release.wait()

    prepare(chain, choose)
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    replacement = AsyncMock()
    assert chain.registry.subscribe("stickers", "choose_image", replacement)["ok"]
    await drain_hooks(chain.registry)
    assert (await chain.registry.register(
        contexts[0], "late", [image_part()], plugin_id="stickers",
    ))["status"] == "cancelled"
    replacement.assert_not_awaited()
    chain.manager.websocket.send_json.assert_not_awaited()


async def test_return_without_selection_revokes_scope_without_a_model_continuation(chain):
    contexts = []

    async def choose(payload):
        contexts.append(payload["host_reply"])
        return {"text": "This return value is not a conversation reply."}

    prepare(chain, choose)
    await finish(chain)
    await drain_hooks(chain.registry)
    assert (await chain.registry.register(
        contexts[0], "late", [image_part()], plugin_id="stickers",
    ))["status"] == "cancelled"
    chain.manager.websocket.send_json.assert_not_awaited()


@pytest.mark.parametrize("kind", ["exception", "timeout", "unsubscribe"])
async def test_failed_or_removed_hook_revokes_context_and_reclaims_images(chain, kind):
    started, release = asyncio.Event(), asyncio.Event()
    contexts = []

    async def choose(payload):
        contexts.append(payload["host_reply"])
        assert (await chain.registry.register(
            payload["host_reply"], "image", [image_part()], plugin_id="stickers",
        ))["status"] == "registered"
        started.set()
        if kind == "exception":
            raise RuntimeError("private text and credential must never be logged")
        await release.wait()

    prepare(chain, choose, timeout=0.05 if kind == "timeout" else 15)
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    if kind == "unsubscribe":
        assert chain.registry.unsubscribe("stickers") == 1
    await drain_hooks(chain.registry)
    chain.manager.websocket.send_json.assert_not_awaited()
    assert chain.registry._retained_bytes == 0
    assert chain.registry.status(contexts[0], "image", plugin_id="stickers")["status"] == "cancelled"
    assert (("stickers", "choose_image") in chain.registry._hooks) == (kind != "unsubscribe")
    assert (await chain.registry.register(
        contexts[0], "late", [image_part()], plugin_id="stickers",
    ))["status"] == "cancelled"


@pytest.mark.parametrize("reason", ["incomplete", "old_frontend", "wrong_role", "unsubscribed", "oversized"])
async def test_only_eligible_successful_replies_notify(chain, reason):
    callback = AsyncMock()
    if reason == "old_frontend":
        chain.manager.websocket.state.reply_tail_version = 0
    prepare(chain, callback, role="Other" if reason == "wrong_role" else None)
    if reason == "unsubscribed":
        chain.registry.unsubscribe("stickers")
    if reason == "oversized":
        chain.owner.reply_tail.text = "a" * (module.MAX_REPLY_HOOK_TEXT_CHARS + 1)
    chain.owner.reply_tail.completed = reason != "incomplete"
    chain.owner.turn_ended = True
    await chain.registry.finish(chain.owner.reply_tail)
    await drain_hooks(chain.registry)
    callback.assert_not_awaited()


async def test_existing_tool_attachment_is_reported_to_its_plugin(chain):
    callback = AsyncMock()
    prepare(chain, callback)
    await register(chain)
    await finish(chain)
    await drain_hooks(chain.registry)
    assert callback.call_args.args[0]["has_attachments"] is True
    chain.manager.websocket.send_json.assert_awaited_once()


async def test_completed_original_is_not_relabelled_after_new_input(chain):
    started, release = asyncio.Event(), asyncio.Event()

    async def choose(payload):
        started.set()
        await release.wait()
        assert (await chain.registry.register(
            payload["host_reply"], "chosen", [image_part()], plugin_id="stickers",
        ))["status"] == "registered"

    prepare(chain, choose)
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    chain.manager._active_text_request_id = "request-B"
    chain.owner.taken_over = True
    release.set()
    await drain_hooks(chain.registry)
    assert chain.manager.websocket.send_json.call_args.args[0]["request_id"] == "request-A"


async def test_reconnect_during_selection_rejects_old_context(chain):
    started, release = asyncio.Event(), asyncio.Event()
    results = []

    async def choose(payload):
        started.set()
        await release.wait()
        results.append(await chain.registry.register(
            payload["host_reply"], "chosen", [image_part()], plugin_id="stickers",
        ))

    prepare(chain, choose)
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    old_socket = chain.manager.websocket
    chain.manager.websocket = SimpleNamespace(send_json=AsyncMock())
    release.set()
    await drain_hooks(chain.registry)
    assert results[0]["status"] == "cancelled"
    old_socket.send_json.assert_not_awaited()
    chain.manager.websocket.send_json.assert_not_awaited()


async def test_failing_plugin_does_not_cancel_another_plugin(chain):
    async def fail(payload):
        raise ValueError("failed selector")

    async def choose(payload):
        assert (await chain.registry.register(
            payload["host_reply"], "chosen", [image_part()], plugin_id="other",
        ))["status"] == "registered"

    prepare(chain, fail)
    chain.registry.subscribe("other", "choose", choose)
    chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
    await finish(chain)
    await drain_hooks(chain.registry)
    chain.manager.websocket.send_json.assert_awaited_once()
    assert chain.manager.websocket.send_json.call_args.args[0]["metadata"]["source_name"] == "other"


async def test_hook_capacity_and_live_context_cannot_be_evicted(chain, monkeypatch):
    monkeypatch.setattr(module, "MAX_REPLY_HOOKS", 2)
    monkeypatch.setattr(module, "MAX_REPLY_HOOK_TASKS", 1)
    started, release = asyncio.Event(), asyncio.Event()

    async def choose(payload):
        started.set()
        await release.wait()

    prepare(chain, choose)
    assert chain.registry.subscribe("other", "choose", choose)["ok"]
    assert chain.registry.subscribe("overflow", "choose", choose)["reason"] == "capacity"
    chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    assert len(chain.registry._hook_tasks) == 1
    scope = next(iter(chain.registry._hook_tasks.values()))
    monkeypatch.setattr(module, "MAX_SCOPES", len(chain.registry._scopes))
    # The chain's unused tool scope may be reclaimed; a selecting hook may not.
    chain.registry._scopes = {scope.context["token"]: scope}
    monkeypatch.setattr(module, "MAX_SCOPES", 1)
    assert chain.registry._room_for_scope() is False
    release.set()
    await drain_hooks(chain.registry)


async def test_per_plugin_capacity_preserves_room_for_other_plugins_and_allows_replacement(chain):
    callback = AsyncMock()
    for index in range(module.MAX_REPLY_HOOKS_PER_PLUGIN):
        assert chain.registry.subscribe("stickers", f"entry-{index}", callback)["ok"]
    assert chain.registry.subscribe("stickers", "overflow", callback) == {
        "ok": False, "reason": "plugin_capacity",
    }
    assert chain.registry.subscribe("stickers", "entry-0", callback)["ok"]
    for index in range(module.MAX_REPLY_HOOKS - module.MAX_REPLY_HOOKS_PER_PLUGIN):
        assert chain.registry.subscribe(f"other-{index}", "choose", callback)["ok"]
    assert chain.registry.subscribe("global-overflow", "choose", callback) == {
        "ok": False, "reason": "capacity",
    }
    assert chain.registry.subscribe("stickers", "entry-0", callback)["ok"]
    assert chain.registry.unsubscribe("stickers", "entry-0") == 1
    assert chain.registry.subscribe("stickers", "new-entry", callback)["ok"]


async def test_confirmed_missing_plugin_revokes_other_pending_callbacks_and_images(chain):
    started, release = asyncio.Event(), asyncio.Event()
    contexts = []

    async def choose(payload):
        if payload["host_reply"]["request_id"] != "request-A":
            raise ReplyHookUnavailableError
        contexts.append(payload["host_reply"])
        assert (await chain.registry.register(
            payload["host_reply"], "pending", [image_part()], plugin_id="stickers",
        ))["status"] == "registered"
        started.set()
        await release.wait()

    other = AsyncMock()
    prepare(chain, choose)
    assert chain.registry.subscribe("other", "choose", other)["ok"]
    chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    next_owner = SimpleNamespace(
        request_id="request-B", session=chain.manager.session, turn_ended=False, taken_over=False,
    )
    chain.registry.prepare_reply(chain.manager, next_owner, user_text="next input")
    next_owner.reply_tail.text = "next reply"
    next_owner.reply_tail.completed = True
    next_owner.turn_ended = True
    await chain.registry.finish(next_owner.reply_tail)
    await drain_hooks(chain.registry)
    assert ("stickers", "choose_image") not in chain.registry._hooks
    assert ("other", "choose") in chain.registry._hooks
    assert chain.registry.status(contexts[0], "pending", plugin_id="stickers")["status"] == "cancelled"
    assert (await chain.registry.register(
        contexts[0], "late", [image_part()], plugin_id="stickers",
    ))["status"] == "cancelled"
    assert chain.registry._retained_bytes == 0
    chain.manager.websocket.send_json.assert_not_awaited()


async def test_missing_plugin_result_from_old_callback_preserves_replacement(chain):
    started = asyncio.Event()

    async def choose(payload):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ReplyHookUnavailableError from None

    prepare(chain, choose)
    await finish(chain)
    await asyncio.wait_for(started.wait(), 1)
    replacement = AsyncMock()
    assert chain.registry.subscribe("stickers", "choose_image", replacement)["ok"]
    await drain_hooks(chain.registry)
    assert chain.registry._hooks[("stickers", "choose_image")].dispatch is replacement


async def test_multiple_plugin_images_follow_completion_not_subscription_order(chain):
    started, release, fast_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow(payload):
        started.set()
        await release.wait()
        assert (await chain.registry.register(
            payload["host_reply"], "slow", [image_part()], plugin_id="stickers",
        ))["status"] == "registered"

    async def fast(payload):
        await started.wait()
        assert (await chain.registry.register(
            payload["host_reply"], "fast", [image_part()], plugin_id="other",
        ))["status"] == "registered"

    async def sent(frame):
        if frame["metadata"]["source_name"] == "other":
            fast_sent.set()

    chain.manager.websocket.send_json.side_effect = sent
    prepare(chain, slow)
    assert chain.registry.subscribe("other", "choose", fast)["ok"]
    chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
    await finish(chain)
    try:
        await asyncio.wait_for(fast_sent.wait(), 1)
    finally:
        release.set()
        await drain_hooks(chain.registry)
    frames = [call.args[0] for call in chain.manager.websocket.send_json.await_args_list]
    assert [frame["metadata"]["source_name"] for frame in frames] == ["other", "stickers"]
    assert all(frame["request_id"] == "request-A" for frame in frames)


@pytest.mark.parametrize("context", [None, {}, {"host_reply": None}, {"host_reply": {"token": "example"}}])
async def test_documented_callback_handles_missing_context_before_selecting(context):
    source = (Path(__file__).resolve().parents[2] / "docs/plugin-reply-tail.md").read_text(encoding="utf-8")
    example = next(
        block.split("```", 1)[0] for block in source.split("```python")[1:]
        if "async def after_reply(" in block
    )
    function = next(
        node for node in ast.parse(example).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "after_reply"
    )
    function.decorator_list = []
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<documented-hook>", "exec"), namespace)
    selector, register_image = AsyncMock(return_value=b"image"), AsyncMock()
    plugin = SimpleNamespace(
        select_image=selector, ctx=SimpleNamespace(reply_tail=SimpleNamespace(register=register_image)),
    )
    await namespace["after_reply"](plugin, "reply", "input", False, _ctx=context)
    if context and context.get("host_reply") is not None:
        selector.assert_awaited_once_with("input", "reply")
        assert register_image.await_args.args[0] is context["host_reply"]
    else:
        selector.assert_not_awaited()
        register_image.assert_not_awaited()


@pytest.mark.parametrize("version", [0, 1])
async def test_real_plain_text_turn_notifies_without_a_model_tool_call(monkeypatch, version):
    from tests.unit.test_offline_late_completion_turn_ownership import (
        _client, _text, _observe, _wire, _text_turn, _make_callback_media_manager,
    )

    registry = ReplyTailRegistry()
    monkeypatch.setattr(module, "reply_tail_registry", registry)
    callback = AsyncMock()
    registry.subscribe("stickers", "choose", callback)
    client = _client([[_text("plain final answer", "stop")]])
    manager = _observe(_make_callback_media_manager(client))
    manager.websocket.state = SimpleNamespace(reply_tail_version=version)
    _wire(manager, client)
    monkeypatch.setattr("main_logic.core.dispatch_text_user_message", lambda *_: None)
    await asyncio.wait_for(_text_turn(manager, "hello", "original-request"), 5)
    await drain_hooks(registry)
    assert callback.await_count == version
    if version:
        payload = callback.call_args.args[0]
        assert payload["text"] == "plain final answer" and payload["user_text"] == "hello"
        assert payload["host_reply"]["request_id"] == "original-request"
        assert payload["host_reply"]["role"] == manager.lanlan_name


async def test_sdk_subscription_callback_and_tail_registration_use_real_http_routes(chain, monkeypatch):
    from plugin.sdk.shared.core import reply_tail as sdk_module
    from plugin.server.routes import llm_tools as callback_module

    app = FastAPI()
    app.dependency_overrides[verify_local_access] = lambda: None
    app.include_router(tool_router.router)
    app.include_router(callback_module.router)
    monkeypatch.setattr(tool_router, "reply_tail_registry", chain.registry)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49991")
    client_type = httpx.AsyncClient
    monkeypatch.setattr(sdk_module.httpx, "AsyncClient", lambda **kwargs: client_type(
        **kwargs, transport=httpx.ASGITransport(app=app),
    ))
    sdk = SdkContext(SimpleNamespace(plugin_id="stickers")).reply_tail
    calls = []

    async def trigger(entry_id, arguments, timeout):
        calls.append((entry_id, arguments, timeout))
        assert (await sdk.register(
            arguments["_ctx"]["host_reply"], registration_id="chosen", parts=[image_part("GIF")],
        ))["status"] == "registered"
        return {"ignored": "never a model continuation"}

    monkeypatch.setattr(callback_module, "state", SimpleNamespace(
        acquire_plugin_hosts_read_lock=nullcontext,
        plugin_hosts={"stickers": SimpleNamespace(trigger=trigger, is_alive=lambda: True)},
    ))
    async with client_type(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as host_http:
        monkeypatch.setattr(tool_router, "_get_http_client", lambda: host_http)
        assert (await sdk.subscribe("choose", role="Cat"))["ok"]
        chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
        chain.owner.reply_tail.text = "final answer"
        await finish(chain)
        await drain_hooks(chain.registry)
        assert calls[0][0] == "choose"
        assert calls[0][1]["_ctx"]["lanlan_name"] == "Cat"
        chain.manager.websocket.send_json.assert_awaited_once()
        assert (await sdk.unsubscribe("choose"))["removed"] == 1


@pytest.mark.parametrize("condition", ["absent", "dead", "dies_during_dispatch", "live_timeout", "live_error"])
async def test_callback_liveness_controls_automatic_unsubscription(chain, monkeypatch, condition):
    from plugin.server.routes import llm_tools as callback_module

    app = FastAPI()
    app.include_router(callback_module.router)
    trigger = AsyncMock(side_effect=TimeoutError() if condition != "live_error" else RuntimeError())
    alive = Mock(side_effect=[True, False]) if condition == "dies_during_dispatch" else Mock(
        return_value=condition != "dead",
    )
    monkeypatch.setattr(callback_module, "state", SimpleNamespace(
        acquire_plugin_hosts_read_lock=nullcontext,
        plugin_hosts={} if condition == "absent" else {
            "stickers": SimpleNamespace(trigger=trigger, is_alive=alive),
        },
    ))
    monkeypatch.setattr(tool_router, "reply_tail_registry", chain.registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://127.0.0.1",
    ) as client:
        monkeypatch.setattr(tool_router, "_get_http_client", lambda: client)
        assert (await tool_router.subscribe_reply_completed(tool_router.ReplyHookSubscription(
            plugin_id="stickers", entry_id="choose",
            callback_url="http://127.0.0.1/api/llm-tools/reply-completed/stickers/choose",
        )))["ok"]
        chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
        chain.owner.reply_tail.text = "reply"
        await finish(chain)
        await drain_hooks(chain.registry)
    removed = condition in {"absent", "dead", "dies_during_dispatch"}
    assert (("stickers", "choose") not in chain.registry._hooks) == removed
    assert trigger.await_count == (0 if condition in {"absent", "dead"} else 1)
    if removed:
        callback = AsyncMock()
        assert chain.registry.subscribe("stickers", "choose", callback)["ok"]
        next_owner = SimpleNamespace(
            request_id="request-B", session=chain.manager.session, turn_ended=False, taken_over=False,
        )
        chain.registry.prepare_reply(chain.manager, next_owner, user_text="after restart")
        next_owner.reply_tail.text = "reply after restart"
        next_owner.reply_tail.completed = True
        next_owner.turn_ended = True
        await chain.registry.finish(next_owner.reply_tail)
        await drain_hooks(chain.registry)
        callback.assert_awaited_once()
    chain.manager.websocket.send_json.assert_not_awaited()


@pytest.mark.parametrize("body", [
    {"detail": "Not Found"}, {"detail": {"code": "OTHER_ERROR"}}, [], "not json",
])
async def test_unrelated_404_does_not_unsubscribe(chain, monkeypatch, body):
    def respond(request):
        if isinstance(body, str):
            return httpx.Response(404, text=body)
        return httpx.Response(404, json=body)

    monkeypatch.setattr(tool_router, "reply_tail_registry", chain.registry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(tool_router, "_get_http_client", lambda: client)
        await tool_router.subscribe_reply_completed(tool_router.ReplyHookSubscription(
            plugin_id="stickers", entry_id="choose", callback_url="http://127.0.0.1/callback",
        ))
        chain.registry.prepare_reply(chain.manager, chain.owner, user_text="hello")
        chain.owner.reply_tail.text = "reply"
        await finish(chain)
        await drain_hooks(chain.registry)
    assert ("stickers", "choose") in chain.registry._hooks


@pytest.mark.parametrize("callback_url", ["https://example.com/callback", "http://192.168.1.2/callback"])
async def test_subscription_rejects_nonlocal_callbacks(callback_url):
    app = FastAPI()
    app.dependency_overrides[verify_local_access] = lambda: None
    app.include_router(tool_router.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
    ) as client:
        response = await client.post("/api/tools/reply-tail/subscribe", json={
            "plugin_id": "stickers", "entry_id": "choose", "callback_url": callback_url,
        })
    assert response.status_code == 422


async def test_plugin_stop_clears_callbacks_without_registered_tools(chain, monkeypatch):
    callback = AsyncMock()
    prepare(chain, callback)
    monkeypatch.setattr(tool_router, "reply_tail_registry", chain.registry)
    monkeypatch.setattr(tool_router, "_resolve_target_managers", lambda *_: [])
    await tool_router.clear_tools(tool_router.ToolClearRequest(source="plugin:stickers"))
    await finish(chain)
    await drain_hooks(chain.registry)
    callback.assert_not_awaited()


@pytest.mark.parametrize("source", ["plugin", "agent", "avatar-drop"])
async def test_synthetic_text_does_not_notify(monkeypatch, source):
    from main_logic import core
    from tests.unit.test_offline_late_completion_turn_ownership import (
        _client, _text, _observe, _wire, _make_callback_media_manager,
    )

    registry = ReplyTailRegistry()
    monkeypatch.setattr(module, "reply_tail_registry", registry)
    callback = AsyncMock()
    registry.subscribe("stickers", "choose", callback)
    client = _client([[_text("synthetic answer", "stop")]])
    manager = _observe(_make_callback_media_manager(client))
    manager.websocket.state = SimpleNamespace(reply_tail_version=1)
    _wire(manager, client)
    monkeypatch.setattr("main_logic.core.dispatch_text_user_message", lambda *_: None)
    await core.LLMSessionManager._process_stream_data_internal(
        manager, {"input_type": "text", "data": "hello", "request_id": "synthetic", "source": source},
    )
    await drain_hooks(registry)
    callback.assert_not_awaited()


async def test_successful_retry_notifies_once_with_retained_text(chain):
    callback = AsyncMock()
    prepare(chain, callback)
    chain.owner.reply_tail.discard_attempt()
    chain.owner.reply_tail.text = "retained retry"
    await finish(chain)
    await finish(chain)
    await drain_hooks(chain.registry)
    callback.assert_awaited_once()
    assert callback.call_args.args[0]["text"] == "retained retry"
    assert callback.call_args.args[0]["host_reply"]["attempt"] == 1


async def test_hook_error_logs_neither_text_nor_credentials(chain, caplog):
    async def choose(payload):
        raise ValueError("secret conversation: " + payload["host_reply"]["token"])

    prepare(chain, choose)
    await finish(chain)
    scope = next(iter(chain.registry._hook_tasks.values()))
    await drain_hooks(chain.registry)
    assert scope.context["token"] not in caplog.text
    assert "secret conversation" not in caplog.text


async def test_oversized_input_is_skipped_without_clipping_restrictions(chain):
    callback = AsyncMock()
    chain.registry.subscribe("stickers", "choose", callback)
    chain.registry.prepare_reply(
        chain.manager, chain.owner,
        user_text="a" * module.MAX_REPLY_HOOK_TEXT_CHARS + "do not send images",
    )
    chain.owner.reply_tail.text = "answer"
    await finish(chain)
    await drain_hooks(chain.registry)
    callback.assert_not_awaited()
