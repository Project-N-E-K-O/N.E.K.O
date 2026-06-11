"""退役实验 proactive_interval_20s 的一次性偏好回滚。

该实验把海外用户首启的 proactiveChatInterval 默认从 15s 覆写成 20s，下线后需要把落盘
的 20s 拉回 15s。回滚绑定在 `_get_telemetry_branch` 的「退役分支判非法 → 重抽」choke
point 上：

- 只回滚 .telemetry_branch 落盘值正是 proactive_interval_20s 的 install（实验组确凿
  信号），不误伤普通手选 20s / 国内用户；
- 只回滚仍停在覆写值 20s 的偏好，用户改过的值保留；
- 重抽即天然幂等标记，跨重启不会重复回滚，也不会压制用户日后再手选 20s。
"""
from unittest.mock import patch

import utils.token_tracker as tt


def _seed_branch(config_dir, value):
    (config_dir / tt._TELEMETRY_BRANCH_FILE).write_text(value, encoding="utf-8")


def _read_branch(config_dir):
    return (config_dir / tt._TELEMETRY_BRANCH_FILE).read_text(encoding="utf-8").strip()


def test_retired_interval_branch_rolls_back_overwritten_value(tmp_path):
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "proactive_interval_20s")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings", return_value=True
    ) as mock_save:
        branch = tt._get_telemetry_branch(tmp_path)

    # 退役值被重抽进现池子
    assert branch in tt._TELEMETRY_BRANCHES
    assert branch != "proactive_interval_20s"
    # 偏好被拉回控制组 15s
    mock_save.assert_called_once_with({"proactiveChatInterval": 15})
    # branch 文件已被重抽覆盖（天然幂等标记）
    assert _read_branch(tmp_path) == branch


def test_user_changed_interval_is_preserved(tmp_path):
    """实验组用户首启后又手动改成别的值（!=20）的，保留其选择，不回滚。"""
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "proactive_interval_20s")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 30},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save:
        tt._get_telemetry_branch(tmp_path)

    mock_save.assert_not_called()


def test_non_experiment_branch_value_20_is_not_touched(tmp_path):
    """普通手选 20s（branch 不是退役实验）不被回滚——这正是「不误伤手选用户」的保证。"""
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "corrupted_or_unknown_value")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save:
        branch = tt._get_telemetry_branch(tmp_path)

    assert branch in tt._TELEMETRY_BRANCHES
    mock_save.assert_not_called()


def test_valid_main_branch_does_not_trigger_rollback(tmp_path):
    """落盘合法分支走 fast path，根本不进重抽 choke point，不回滚。"""
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "main")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save:
        branch = tt._get_telemetry_branch(tmp_path)

    assert branch == "main"
    mock_save.assert_not_called()


def test_rollback_is_idempotent_across_restarts(tmp_path):
    tt._telemetry_branch_cache.clear()
    _seed_branch(tmp_path, "proactive_interval_20s")

    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings", return_value=True
    ) as mock_save:
        tt._get_telemetry_branch(tmp_path)
    assert mock_save.call_count == 1

    # 模拟下一次启动（新进程：清进程级缓存）。branch 已被重抽成合法值，fast path 命中。
    tt._telemetry_branch_cache.clear()
    with patch(
        "utils.preferences.load_global_conversation_settings",
        return_value={"proactiveChatInterval": 20},
    ), patch(
        "utils.preferences.save_global_conversation_settings"
    ) as mock_save_2:
        tt._get_telemetry_branch(tmp_path)
    mock_save_2.assert_not_called()
