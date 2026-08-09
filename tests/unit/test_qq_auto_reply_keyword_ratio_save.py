"""`attention_keyword_boost_ratio` 必须在 save_settings 全链路声明/透传/保存。

之前 __init__.py 的 save_settings 签名和 dashboard_service 都缺这个参数，
前端发的值被 **_ 吞掉，运行时仍用默认 1.8，dashboard 也回显 1.8。
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
    """save_settings entry 必须把 attention_keyword_boost_ratio 透传给 dashboard_service。"""
    dash = _DashboardStub()
    inst = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    inst.dashboard_service = dash
    inst._qq_settings = {}
    inst._emit_log = lambda *a, **k: None

    asyncio.run(inst.save_settings(attention_keyword_boost_ratio=2.5))

    assert dash.received.get("attention_keyword_boost_ratio") == 2.5


def test_keyword_boost_ratio_passed_with_other_params():
    """与其它注意力参数同传时也透传（不是被 **_ 吞掉）。"""
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
