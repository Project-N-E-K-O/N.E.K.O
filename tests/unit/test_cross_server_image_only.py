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
    assert payload[0]["role"] == "assistant"
    assert payload[0]["content"][0]["text"].endswith("我看到了。")
    assert analyze_calls
    recent = analyze_calls[0]["messages"]
    assert any(
        item.get("role") == "user" and item.get("attachments")
        for item in recent
    )
