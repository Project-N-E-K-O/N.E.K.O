import asyncio
import contextlib
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import main_logic.cross_server as cross_server


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_only_marker_allows_turn_end_memory_cache(monkeypatch):
    queue = asyncio.Queue()
    cache_called = asyncio.Event()
    cache_calls = []
    analyze_calls = []

    async def fake_post_memory_server(endpoint, lanlan_name, payload, *, timeout_s):
        cache_calls.append((endpoint, lanlan_name, payload, timeout_s))
        if endpoint == "cache":
            cache_called.set()
        return True, None, None

    async def fake_publish_analyze(**kwargs):
        analyze_calls.append(kwargs)
        return True

    monkeypatch.setattr(cross_server, "_post_memory_server", fake_post_memory_server)
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", fake_publish_analyze)

    task = asyncio.create_task(
        cross_server.run_sync_connector(
            queue,
            "Lan",
            config={"monitor": False, "bullet": False},
        )
    )
    try:
        await queue.put({"type": "user", "data": {"input_type": "screen", "data": "img-b64"}})
        await queue.put({
            "type": "user",
            "data": {
                "input_type": "transcript",
                "data": "",
                "counts_as_user_input": True,
            },
        })
        await queue.put({
            "type": "json",
            "data": {
                "type": "gemini_response",
                "text": "我看到了。",
                "request_id": "req-image",
            },
        })
        await queue.put({"type": "system", "data": "turn end"})

        await asyncio.wait_for(cache_called.wait(), timeout=1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(cache_calls) == 1
    endpoint, lanlan_name, payload, timeout_s = cache_calls[0]
    assert endpoint == "cache"
    assert lanlan_name == "Lan"
    assert timeout_s == 10.0
    assert any(
        item.get("role") == "assistant"
        and (item.get("content") or [{}])[0].get("text", "").endswith("我看到了。")
        for item in payload
    )
    assert analyze_calls
    recent = analyze_calls[0]["messages"]
    assert any(
        item.get("role") == "user" and item.get("attachments")
        for item in recent
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_image_only_marker_is_cleared_on_discard_before_proactive_turn(monkeypatch):
    queue = asyncio.Queue()
    turn_end_seen = asyncio.Event()
    analyze_seen = asyncio.Event()
    cache_calls = []
    analyze_calls = []

    async def fake_post_memory_server(endpoint, lanlan_name, payload, *, timeout_s):
        cache_calls.append((endpoint, lanlan_name, payload, timeout_s))
        return True, None, None

    async def fake_publish_analyze(**kwargs):
        analyze_calls.append(kwargs)
        analyze_seen.set()
        return True

    async def fake_try_send_json(_slot, payload):
        if payload == {"type": "turn end"}:
            turn_end_seen.set()

    monkeypatch.setattr(cross_server, "_post_memory_server", fake_post_memory_server)
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", fake_publish_analyze)
    monkeypatch.setattr(cross_server, "_try_send_json", fake_try_send_json)

    task = asyncio.create_task(
        cross_server.run_sync_connector(
            queue,
            "Lan",
            config={"monitor": False, "bullet": False},
        )
    )
    try:
        await queue.put({"type": "user", "data": {"input_type": "screen", "data": "stale-img"}})
        await queue.put({
            "type": "user",
            "data": {
                "input_type": "transcript",
                "data": "",
                "counts_as_user_input": True,
            },
        })
        await queue.put({"type": "system", "data": "response_discarded_clear"})
        await queue.put({
            "type": "json",
            "data": {
                "type": "gemini_response",
                "text": "主动提醒。",
                "request_id": None,
            },
        })
        await queue.put({"type": "system", "data": "turn end"})

        await asyncio.wait_for(turn_end_seen.wait(), timeout=1)
        await asyncio.wait_for(analyze_seen.wait(), timeout=1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert cache_calls == []
    assert analyze_calls
    assert not any(
        item.get("role") == "user"
        for item in analyze_calls[0]["messages"]
    )
