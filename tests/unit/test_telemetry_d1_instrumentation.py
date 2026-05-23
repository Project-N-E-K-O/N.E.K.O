"""D1 流失诊断埋点：user_message_sent counter + session_turn_count histogram。

验证 token_tracker 的 note_user_message（每条消息）+ _atexit_save 的
session_turn_count（含零消息会话）接线正确。instrument SDK 本身在
tests/test_instrument.py 已覆盖，这里只验 token_tracker 的 wiring。
"""
import types

import pytest


def _make_tracker(tmp_path, monkeypatch):
    """构造一个独立 TokenTracker（patch config_manager 到临时目录，不碰真盘）。"""
    import utils.token_tracker as tk
    fake_cm = types.SimpleNamespace(config_dir=tmp_path)
    monkeypatch.setattr(tk, "get_config_manager", lambda: fake_cm)
    tt = tk.TokenTracker()
    monkeypatch.setattr(tt, "save", lambda *a, **k: None)  # _atexit_save 不打网络
    return tt


def _snapshot():
    import utils.instrument as inst
    return inst.snapshot()


def test_user_message_counter_split_by_input_type(tmp_path, monkeypatch):
    import utils.instrument as inst
    tt = _make_tracker(tmp_path, monkeypatch)
    inst.snapshot()  # drain 之前测试残留

    tt.record_app_start("main_server")  # 重置 _session_msg_count = 0
    tt.note_user_message("text")
    tt.note_user_message("voice")
    tt.note_user_message("text")

    assert tt._session_msg_count == 3
    counters = _snapshot().get("counters", {})
    # 按 input_type 维度切：text 2 次、voice 1 次。求和 = 总轮数 3。
    assert counters.get("user_message_sent|input_type=text") == 2
    assert counters.get("user_message_sent|input_type=voice") == 1


def test_session_turn_count_emitted_on_session_end(tmp_path, monkeypatch):
    import utils.instrument as inst
    tt = _make_tracker(tmp_path, monkeypatch)
    inst.snapshot()

    tt.record_app_start("main_server")
    tt.note_user_message("text")
    tt.note_user_message("text")
    tt._atexit_save()  # session_end → emit session_turn_count（值 2）

    hists = _snapshot().get("histograms", {})
    stc = [v for k, v in hists.items() if k.startswith("session_turn_count")]
    assert stc, "session_end 未 emit session_turn_count"
    assert stc[0]["count"] == 1  # 一次 session_end 一条
    assert stc[0]["sum"] == 2    # 该 session 2 轮


def test_zero_message_session_still_emits_turn_count(tmp_path, monkeypatch):
    """零消息会话（开了 app 一句没聊就走）必须也 emit session_turn_count=0。

    这是 D1 流失最直接的信号，不能因为"没消息"就不打。
    """
    import utils.instrument as inst
    tt = _make_tracker(tmp_path, monkeypatch)
    inst.snapshot()

    tt.record_app_start("main_server")
    # 不发任何消息
    tt._atexit_save()

    snap = _snapshot()
    hists = snap.get("histograms", {})
    stc = [v for k, v in hists.items() if k.startswith("session_turn_count")]
    assert stc and stc[0]["count"] == 1, "零消息会话漏 emit session_turn_count"
    assert stc[0]["sum"] == 0  # 0 轮
    # 没有任何 user_message_sent
    assert not any(k.startswith("user_message_sent") for k in snap.get("counters", {}))


def test_session_turn_count_resets_between_sessions(tmp_path, monkeypatch):
    """record_app_start 必须重置轮数，避免跨 session 累计。"""
    tt = _make_tracker(tmp_path, monkeypatch)
    tt.record_app_start("main_server")
    tt.note_user_message("text")
    tt.note_user_message("text")
    assert tt._session_msg_count == 2
    # 模拟新 session（同进程，record_app_start 有单次锁，直接置位测试重置语义）
    tt._has_recorded_app_start = False
    tt.record_app_start("main_server")
    assert tt._session_msg_count == 0
