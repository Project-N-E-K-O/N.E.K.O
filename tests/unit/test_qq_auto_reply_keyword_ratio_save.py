"""`attention_keyword_boost_ratio` must be declared/forwarded/saved across save_settings.

Previously the save_settings signature in __init__.py and dashboard_service both
lacked this parameter, so the value sent by the frontend was swallowed by **_,
the runtime kept the default 1.8, and the dashboard echoed 1.8.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin


class _DashboardStub:
    def __init__(self):
        self.received: dict = {}

    async def save_settings(self, **kwargs):
        self.received = dict(kwargs)
        return {"persisted": True}


def test_keyword_boost_ratio_forwarded_to_dashboard():
    """The save_settings entry must forward attention_keyword_boost_ratio to dashboard_service."""
    dash = _DashboardStub()
    inst = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    inst.dashboard_service = dash
    inst._qq_settings = {}
    inst._emit_log = lambda *a, **k: None

    asyncio.run(inst.save_settings(attention_keyword_boost_ratio=2.5))

    assert dash.received.get("attention_keyword_boost_ratio") == 2.5


def test_keyword_boost_ratio_passed_with_other_params():
    """It is forwarded alongside other attention params (not swallowed by **_)."""
    dash = _DashboardStub()
    inst = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    inst.dashboard_service = dash
    inst._qq_settings = {}
    inst._emit_log = lambda *a, **k: None

    asyncio.run(inst.save_settings(
        attention_message_boost=0.3,
        attention_keyword_boost_ratio=1.8,
        attention_honeymoon_seconds=60,
    ))

    assert dash.received.get("attention_keyword_boost_ratio") == 1.8
    assert dash.received.get("attention_message_boost") == 0.3
