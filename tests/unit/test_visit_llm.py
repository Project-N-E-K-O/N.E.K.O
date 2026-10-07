"""Visit one-shot LLM helper: client shutdown stays within the caller's timeout."""

from __future__ import annotations

import asyncio

import pytest

from main_routers.visit_router import llm as visit_llm


class _HangingClient:
    async def ainvoke(self, _messages):
        await asyncio.Event().wait()

    async def aclose(self):
        await asyncio.Event().wait()


class _Config:
    async def aget_model_api_config(self, _model_type):
        return {"model": "m", "base_url": "http://llm.test", "api_key": "k"}


@pytest.fixture
def hanging_client(monkeypatch):
    import utils.config_manager as config_manager
    import utils.llm_client as llm_client

    async def create(*_a, **_k):
        return _HangingClient()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(llm_client, "create_chat_llm_async", create)
    monkeypatch.setattr(visit_llm, "_ACLOSE_TIMEOUT_S", 0.05)


async def test_a_stalled_close_does_not_outlive_the_call_timeout(hanging_client):
    call = visit_llm.one_shot("hi", max_tokens=10, timeout=1.0)
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(asyncio.TimeoutError):
        # 调用超时被取消后，卡住的 aclose 也要在限时内放手（外层 2 s 只是防测试挂死）
        await asyncio.wait_for(asyncio.wait_for(call, 0.05), 2.0)
    assert loop.time() - started < 1.0
