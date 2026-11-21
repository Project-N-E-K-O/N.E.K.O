# -*- coding: utf-8 -*-
"""A foreground /new_dialog first lets the character's queued memory writes out (#3601).

Those writes may sit behind a summary on the connector's memory lane, so the main server
preempts the summaries, waits (bounded) for the lane to send what was queued, then asks for
/new_dialog, which therefore reads the latest turns.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _Queue:
    """sync_message_queue stand-in; resolves a memory flush after ``delay`` (None: never)."""

    def __init__(self, order, delay):
        self.order = order
        self.delay = delay

    def put(self, item):
        if item.get("data") != "memory flush":
            return
        self.order.append("flush queued")
        if self.delay is None:
            return

        async def resolve():
            await asyncio.sleep(self.delay)
            self.order.append("flush done")
            item["_memory_flush_done"].set_result(None)

        asyncio.get_running_loop().create_task(resolve())


def _manager(monkeypatch, order, delay):
    from main_logic.core.lifecycle import LifecycleMixin
    from utils import internal_http_client

    class Client:
        async def post(self, url, **_kwargs):
            order.append("preempt " + url.rsplit("/", 1)[-1])
            return SimpleNamespace(is_success=True, text="{}")

        async def get(self, url, **kwargs):
            order.append("new_dialog " + kwargs["params"]["preempt_summary"])
            return SimpleNamespace(is_success=True, text="ctx")

    monkeypatch.setattr(internal_http_client, "get_internal_http_client", Client)
    manager = object.__new__(LifecycleMixin)
    manager.user_language = "zh-CN"
    manager._user_language_explicit = False
    manager._conversation_render_language = None
    manager.sync_message_queue = _Queue(order, delay)
    return manager


@pytest.mark.asyncio
async def test_new_dialog_waits_for_queued_memory_writes(monkeypatch):
    order = []
    manager = _manager(monkeypatch, order, delay=0.05)

    assert await manager._start_session_fetch_new_dialog("Neko", 48912) == "ctx"
    assert order == ["preempt Neko", "flush queued", "flush done", "new_dialog true"]


@pytest.mark.asyncio
async def test_a_stuck_flush_only_delays_new_dialog_by_the_bound(monkeypatch):
    from main_logic.core import lifecycle

    monkeypatch.setattr(lifecycle, "_MEMORY_FLUSH_WAIT_S", 0.05)
    order = []
    manager = _manager(monkeypatch, order, delay=None)

    assert await asyncio.wait_for(manager._start_session_fetch_new_dialog("Neko", 48912), 1) == "ctx"
    assert order == ["preempt Neko", "flush queued", "new_dialog true"]
