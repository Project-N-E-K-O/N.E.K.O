# -*- coding: utf-8 -*-
"""Regression tests for ChatOpenAI defensive response reads.

Background: free-agent-model 上游会返回 HTTP 200 + choices 非空，但
choices[0].message 是 None 的合法响应。原来 ainvoke/invoke 直接
.message.content 会触发 'NoneType' object has no attribute 'content'，
连通性预检随之失败。这里固定该场景下不再崩溃、content 退化为 ""。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.llm_client import ChatOpenAI


def _make_client_with_response(resp) -> ChatOpenAI:
    """Construct a ChatOpenAI and stub both sync/async create() to return resp."""
    client = ChatOpenAI(model="free-agent-model", base_url="https://example.com/v1", api_key="free-access")
    client._aclient = MagicMock()
    client._aclient.chat = MagicMock()
    client._aclient.chat.completions = MagicMock()
    client._aclient.chat.completions.create = AsyncMock(return_value=resp)
    client._client = MagicMock()
    client._client.chat = MagicMock()
    client._client.chat.completions = MagicMock()
    client._client.chat.completions.create = MagicMock(return_value=resp)
    return client


def _resp_with_none_message():
    """choices=[choice], choice.message is None — what free-agent-model returns."""
    resp = MagicMock()
    choice = MagicMock()
    choice.message = None
    resp.choices = [choice]
    resp.usage = None
    return resp


def _resp_with_empty_choices():
    resp = MagicMock()
    resp.choices = []
    resp.usage = None
    return resp


def _resp_with_none_content():
    resp = MagicMock()
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = None
    resp.choices = [choice]
    resp.usage = None
    return resp


class TestAinvokeDefensiveRead:
    @pytest.mark.asyncio
    async def test_none_message_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_message())
        out = await client.ainvoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    @pytest.mark.asyncio
    async def test_empty_choices_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_empty_choices())
        out = await client.ainvoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    @pytest.mark.asyncio
    async def test_none_content_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_content())
        out = await client.ainvoke([{"role": "user", "content": "hi"}])
        assert out.content == ""


class TestInvokeDefensiveRead:
    def test_none_message_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_message())
        out = client.invoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    def test_empty_choices_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_empty_choices())
        out = client.invoke([{"role": "user", "content": "hi"}])
        assert out.content == ""

    def test_none_content_returns_empty_string(self):
        client = _make_client_with_response(_resp_with_none_content())
        out = client.invoke([{"role": "user", "content": "hi"}])
        assert out.content == ""
