# -*- coding: utf-8 -*-
"""Cancel-proxy status classification.

The HUD's local cancel fallback keys off the proxy status code: it may only
apply the terminal state when the request provably reached the tool server
(2xx, 404 pass-through, 504 timeout-after-forwarding). Connect-level failures
must stay 502/500 so the HUD does not hide tasks the tool server is still
running.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    from main_routers import agent_router

    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    yield agent_router


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()

    async def _post(*args, **kwargs):
        raise exc

    client.post = _post
    return client


def _client_responding(status_code: int) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.is_success = 200 <= status_code < 300
    response.status_code = status_code
    response.json = MagicMock(return_value={"success": response.is_success})

    async def _post(*args, **kwargs):
        return response

    client.post = _post
    return client


@pytest.mark.parametrize(
    "exc, expected",
    [
        (httpx.ConnectError("refused"), 502),
        (httpx.ConnectTimeout("connect timed out"), 502),
        (httpx.ReadTimeout("read timed out"), 504),
    ],
)
def test_task_cancel_proxy_classifies_failures(router, monkeypatch, exc, expected):
    monkeypatch.setattr(router, "_get_http_client", lambda: _client_raising(exc))
    resp = asyncio.run(router.proxy_task_cancel("abc"))
    assert resp.status_code == expected


def test_task_cancel_proxy_passes_through_tool_server_status(router, monkeypatch):
    monkeypatch.setattr(router, "_get_http_client", lambda: _client_responding(404))
    resp = asyncio.run(router.proxy_task_cancel("abc"))
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "exc, expected",
    [
        (httpx.ConnectError("refused"), 500),
        (httpx.ConnectTimeout("connect timed out"), 500),
        (httpx.ReadTimeout("read timed out"), 504),
    ],
)
def test_admin_control_proxy_classifies_failures(router, monkeypatch, exc, expected):
    monkeypatch.setattr(router, "_get_http_client", lambda: _client_raising(exc))
    resp = asyncio.run(router.proxy_admin_control({"action": "end_all"}))
    assert resp.status_code == expected
