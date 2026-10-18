"""Regression tests for frontend notifications when the free Agent quota is exhausted.

Guard two properties:
  1. Throttling: exhausted-quota signals invoke the callback at most once per `_quota_notify_interval_s` window,
     then may invoke it again after that window (at most one notice every 10 seconds).
  2. Gate wiring: `consume_agent_daily_quota` invokes the registered callback when the free quota is exhausted.

Background: exhausted-quota notices originally used app-agent.js's modal (maybeShowAgentQuotaExceededModal),
with frontend matching of human-readable phrases; after the backend switched to machine code AGENT_QUOTA_EXCEEDED, matching failed and notices vanished.
Now the backend triggers throttled notifications directly by code at the single gate → status toast, avoiding fragile text matching.
"""

from datetime import date
import json
import time

import pytest

from utils import config_manager as cm_module
from utils.config_manager import ConfigManager


@pytest.fixture(autouse=True)
def _isolate_quota_notify_state():
    """Isolate class-level throttle/callback state to prevent interference between tests or with real runtime."""
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
    """Throttling: repeated hits within a window notify once; after the window, notify once again."""
    calls = []
    ConfigManager.register_quota_exceeded_notifier(lambda used, limit: calls.append((used, limit)))
    cm = ConfigManager.__new__(ConfigManager)  # Only class-level state is used; config_dir is unnecessary

    for _ in range(5):
        cm._maybe_notify_quota_exceeded(300, 300)
    assert calls == [(300, 300)], "窗口内应只通知一次"

    # Move the last-notification time outside the throttle window to simulate an elapsed interval
    ConfigManager._quota_notify_last_monotonic = time.monotonic() - (ConfigManager._quota_notify_interval_s + 1)
    cm._maybe_notify_quota_exceeded(300, 300)
    assert calls == [(300, 300), (300, 300)], "过窗口后应再通知一次"

    cm._maybe_notify_quota_exceeded(300, 300)
    assert len(calls) == 2, "新窗口内再撞不应重复通知"


def test_quota_notifier_noop_without_registration():
    """Without a registered callback, return quietly without raising."""
    cm = ConfigManager.__new__(ConfigManager)
    cm._maybe_notify_quota_exceeded(300, 300)  # Must not raise


def test_quota_notifier_swallows_callback_error():
    """Exceptions in the callback must not propagate into quota consumption."""
    def _boom(used, limit):
        raise RuntimeError("notifier boom")

    ConfigManager.register_quota_exceeded_notifier(_boom)
    cm = ConfigManager.__new__(ConfigManager)
    cm._maybe_notify_quota_exceeded(300, 300)  # Must not raise


def test_consume_agent_daily_quota_triggers_notifier_on_exhaustion(tmp_path, monkeypatch):
    """Gate regression: when the actual Agent model is free-agent-model and quota is exhausted, consume_agent_daily_quota invokes the notifier once."""
    calls = []
    ConfigManager.register_quota_exceeded_notifier(lambda used, limit: calls.append((used, limit)))

    cm = ConfigManager.__new__(ConfigManager)
    cm.config_dir = tmp_path
    cm.project_config_dir = tmp_path
    cm.app_name = "N.E.K.O"
    cm._verbose = False
    monkeypatch.setattr(cm, "get_model_api_config", lambda model_type: {"model": "free-agent-model"})
    monkeypatch.setattr(cm, "ensure_config_directory", lambda: None)
    monkeypatch.setattr(ConfigManager, "_free_agent_daily_limit", 0)

    ok, info = cm.consume_agent_daily_quota(source="regression_test")

    assert ok is False, "limit=0 时任何一次消费都应耗尽"
    assert info["limited"] is True
    assert calls == [(0, 0)], "耗尽应触发通知器一次，携带 (used, limit)"


def test_consume_non_free_agent_model_never_notifies(tmp_path, monkeypatch):
    """When the actual Agent model is not free-agent-model (paid/custom), do not count quota or notify (early-return path).

    Guard this fix: even if core/assist still use a free provider, an actual agent model
    that is paid/custom must not be blocked by this free-trial quota.
    """
    calls = []
    ConfigManager.register_quota_exceeded_notifier(lambda used, limit: calls.append((used, limit)))

    cm = ConfigManager.__new__(ConfigManager)
    cm.config_dir = tmp_path
    cm.project_config_dir = tmp_path
    cm.app_name = "N.E.K.O"
    cm._verbose = False
    monkeypatch.setattr(cm, "get_model_api_config", lambda model_type: {"model": "qwen3.6-plus-2026-04-02"})
    monkeypatch.setattr(ConfigManager, "_free_agent_daily_limit", 0)

    ok, info = cm.consume_agent_daily_quota(source="regression_test")

    assert ok is True, "自费/自定义 agent model 应放行"
    assert calls == [], "非 free-agent-model 不应触发通知器"


def test_reserve_agent_quota_atomically_keeps_required_final_unit(
    tmp_path, monkeypatch
):
    cm = ConfigManager.__new__(ConfigManager)
    cm.config_dir = tmp_path
    cm.project_config_dir = tmp_path
    cm.app_name = "N.E.K.O"
    cm._verbose = False
    monkeypatch.setattr(
        cm, "get_model_api_config", lambda model_type: {"model": "free-agent-model"}
    )
    monkeypatch.setattr(cm, "ensure_config_directory", lambda: None)
    monkeypatch.setattr(ConfigManager, "_free_agent_daily_limit", 2)
    (tmp_path / "agent_quota.json").write_text(
        json.dumps({"date": date.today().isoformat(), "used": 1}),
        encoding="utf-8",
    )

    reserved, info = cm.reserve_agent_daily_quota(
        source="study_companion:knowledge_semantic_route",
        units=2,
        minimum_units=1,
    )

    assert reserved == 1
    assert info["used"] == 2
    assert info["remaining"] == 0
    persisted = json.loads(
        (tmp_path / "agent_quota.json").read_text(encoding="utf-8")
    )
    assert persisted == {"date": date.today().isoformat(), "used": 2}
