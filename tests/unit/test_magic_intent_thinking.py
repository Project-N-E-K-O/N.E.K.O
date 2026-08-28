# -*- coding: utf-8 -*-
"""Magic-intent classification must run thinking-off.

It sits on the user path behind a 10s timeout (``brain/openclaw_adapter.py``),
so a thinking-default model — official DeepSeek V4, GLM, Kimi, Doubao — would
burn the whole window and fall back to the rule layer on every dispatch. The
regression this guards is a one-word one: ``extra_body=None`` SKIPS the
factory's provider-aware resolution and keeps the model's native behavior,
which is the opposite of what this call site wants.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import brain.openclaw_adapter as adapter_mod


class _FakeConfigManager:
    def __init__(self, model: str, base_url: str):
        self._model = model
        self._base_url = base_url

    async def aget_model_api_config(self, feature: str) -> dict[str, Any]:
        assert feature == "summary"
        return {
            "model": self._model,
            "base_url": self._base_url,
            "api_key": "test-key",
            "provider_type": None,
        }


@pytest.mark.parametrize(
    "model, api_base, expected",
    [
        # thinking.type 方言（GLM / Kimi / Doubao；官方 DeepSeek V4 登记后同此列）
        ("glm-5.2", "https://open.bigmodel.cn/api/paas/v4", {"thinking": {"type": "disabled"}}),
        ("kimi-k3", "https://api.moonshot.cn/v1", {"thinking": {"type": "disabled"}}),
        # enable_thinking 方言（DashScope / SiliconFlow）
        ("qwen3.7-flash", "https://dashscope.aliyuncs.com/compatible-mode/v1", {"enable_thinking": False}),
        # 未登记的模型：没有可下发的方言，保持空（不能瞎猜一个形状发过去）
        ("some-unregistered-model", "https://example.com/v1", {}),
    ],
)
def test_magic_intent_client_disables_thinking(monkeypatch, model, api_base, expected):
    """The client built for magic-intent classification carries thinking-off.

    Deliberately goes through the real ``create_chat_llm_async`` rather than
    stubbing it: the thinking-off dialect is resolved inside the factory from
    the model name, so a stub would only show that the call site passed nothing
    and prove nothing about what actually reaches the wire.
    """
    captured: list[Any] = []

    monkeypatch.setattr(
        adapter_mod, "get_config_manager", lambda: _FakeConfigManager(model, api_base),
    )

    real_factory = adapter_mod.create_chat_llm_async

    async def _capturing_factory(*args: Any, **kwargs: Any):
        llm = await real_factory(*args, **kwargs)
        captured.append(llm)

        class _Resp:
            content = '{"is_magic_intent": false, "command": null}'

        async def _fake_ainvoke(_messages, **_kw):
            return _Resp()

        llm.ainvoke = _fake_ainvoke
        return llm

    monkeypatch.setattr(adapter_mod, "create_chat_llm_async", _capturing_factory)

    adapter = adapter_mod.OpenClawAdapter.__new__(adapter_mod.OpenClawAdapter)
    result = asyncio.run(adapter._classify_magic_intent_with_llm("帮我停下来"))

    assert result == {"is_magic_intent": False, "command": None, "source": "llm"}
    assert len(captured) == 1
    assert captured[0].extra_body == expected
