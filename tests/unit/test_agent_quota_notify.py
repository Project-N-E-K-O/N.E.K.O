"""免费版 Agent 配额耗尽 → 前端提示通知器的回归测试。

锁住两件事：
  1. 节流语义：配额耗尽信号在 `_quota_notify_interval_s` 窗口内最多触发一次回调，
     过窗口后可再触发一次（对应"每 10 秒最多给一次提示"）。
  2. 卡点接线：免费版配额耗尽时 `consume_agent_daily_quota` 会触发已注册的回调。

历史背景：配额耗尽的"提示"原本走 app-agent.js 的 modal（maybeShowAgentQuotaExceededModal），
依赖前端文本匹配人类可读短语；后端把信号退化成机器码 AGENT_QUOTA_EXCEEDED 后匹配不上、提示消失。
现改为后端在唯一卡点直接按 code 触发节流通知 → status toast，不再依赖脆弱的文本匹配。
"""

import time

import pytest

from utils import config_manager as cm_module
from utils.config_manager import ConfigManager


@pytest.fixture(autouse=True)
def _isolate_quota_notify_state():
    """隔离类级节流/回调状态，避免测试间（以及与真实运行时）串扰。"""
    saved_notifier = ConfigManager._quota_exceeded_notifier
    saved_last = ConfigManager._quota_notify_last_monotonic
    ConfigManager._quota_exceeded_notifier = None
    ConfigManager._quota_notify_last_monotonic = 0.0
    try:
        yield
    finally:
        ConfigManager._quota_exceeded_notifier = saved_notifier
        ConfigManager._quota_notify_last_monotonic = saved_last


def test_quota_notifier_throttled_to_one_per_interval():
    """节流：窗口内连撞多次只通知 1 次；过窗口后再通知 1 次。"""
    calls = []
    ConfigManager.register_quota_exceeded_notifier(lambda used, limit: calls.append((used, limit)))
    cm = ConfigManager.__new__(ConfigManager)  # 只用到类级状态，无需 config_dir

    for _ in range(5):
        cm._maybe_notify_quota_exceeded(300, 300)
    assert calls == [(300, 300)], "窗口内应只通知一次"

    # 把"上次通知时间"推到节流窗口之外，模拟过了 interval
    ConfigManager._quota_notify_last_monotonic = time.monotonic() - (ConfigManager._quota_notify_interval_s + 1)
    cm._maybe_notify_quota_exceeded(300, 300)
    assert calls == [(300, 300), (300, 300)], "过窗口后应再通知一次"

    cm._maybe_notify_quota_exceeded(300, 300)
    assert len(calls) == 2, "新窗口内再撞不应重复通知"


def test_quota_notifier_noop_without_registration():
    """没注册回调时应安静返回、不抛错。"""
    cm = ConfigManager.__new__(ConfigManager)
    cm._maybe_notify_quota_exceeded(300, 300)  # 不应抛异常


def test_quota_notifier_swallows_callback_error():
    """回调自身抛错不应冒泡到配额消费路径。"""
    def _boom(used, limit):
        raise RuntimeError("notifier boom")

    ConfigManager.register_quota_exceeded_notifier(_boom)
    cm = ConfigManager.__new__(ConfigManager)
    cm._maybe_notify_quota_exceeded(300, 300)  # 不应抛异常


def test_consume_agent_daily_quota_triggers_notifier_on_exhaustion(tmp_path, monkeypatch):
    """卡点回归：免费版配额耗尽时 consume_agent_daily_quota 触发通知器一次。"""
    calls = []
    ConfigManager.register_quota_exceeded_notifier(lambda used, limit: calls.append((used, limit)))

    cm = ConfigManager.__new__(ConfigManager)
    cm.config_dir = tmp_path
    cm.project_config_dir = tmp_path
    cm.app_name = "N.E.K.O"
    cm._verbose = False
    monkeypatch.setattr(cm, "is_free_version", lambda: True)
    monkeypatch.setattr(cm, "ensure_config_directory", lambda: None)
    monkeypatch.setattr(ConfigManager, "_free_agent_daily_limit", 0)

    ok, info = cm.consume_agent_daily_quota(source="regression_test")

    assert ok is False, "limit=0 时任何一次消费都应耗尽"
    assert info["limited"] is True
    assert calls == [(0, 0)], "耗尽应触发通知器一次，携带 (used, limit)"


def test_consume_non_free_version_never_notifies(tmp_path, monkeypatch):
    """非免费版不计配额、不通知（早退路径）。"""
    calls = []
    ConfigManager.register_quota_exceeded_notifier(lambda used, limit: calls.append((used, limit)))

    cm = ConfigManager.__new__(ConfigManager)
    cm.config_dir = tmp_path
    cm.project_config_dir = tmp_path
    cm.app_name = "N.E.K.O"
    cm._verbose = False
    monkeypatch.setattr(cm, "is_free_version", lambda: False)
    monkeypatch.setattr(ConfigManager, "_free_agent_daily_limit", 0)

    ok, info = cm.consume_agent_daily_quota(source="regression_test")

    assert ok is True, "非免费版应放行"
    assert calls == [], "非免费版不应触发通知器"
