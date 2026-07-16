"""Tests for LiveDanmaku.from_danmaku DANMU_MSG parsing.

回归保护：历史 bug 把 ``info[7]``（大航海等级，普通 int）当作可下标列表，
任意一条正常弹幕都会在 ``len(info[7])`` 抛 TypeError 并被 _dispatch_message 的
except 吞掉，导致 on_event("DANMU_MSG") 永不触发（计数恒为 0）。同时 admin 只判了
外层 len、未判内层长度，短 info[2] 会 IndexError。这些测试锁住真实结构解析。
"""

from __future__ import annotations

import asyncio
import json
import sys

from plugin.plugins.neko_roast.modules.bili_live_ingest.danmaku_core import (
    PROTOCOL_VERSION_BROTLI,
    DanmakuListener,
    WS_FALLBACK_URLS,
    WS_MAIN_URL,
    _decompress,
)
from plugin.plugins.neko_roast.modules.bili_live_ingest.livedanmaku import (
    LiveDanmaku,
    MessageType,
)


def _danmu(info: list, room_id: int = 81004) -> dict:
    return {"cmd": "DANMU_MSG", "room_id": room_id, "info": info}


def test_from_danmaku_full_payload_parses_without_error():
    """完整正常弹幕（info[7] 为 int 大航海等级）不再抛 TypeError，且字段正确。"""
    ld = LiveDanmaku.from_danmaku(
        _danmu(
            [
                [0, 1, 25, 16777215, 1700000000000, 0, 0, "", 0, 0, 0],
                "great stream!",
                # 用户数组：admin=1, vip=0, svip=0
                [123456, "CaptainUser", 1, 0, 0, 10000, 1, "#ff0000"],
                # 粉丝牌：level, name, up_name, anchor_roomid, color
                [21, "MyMedal", "UpName", 22333, 6126494],
                [25],  # user_level
                ["", ""],
                0,
                3,  # info[7] = guard_level (int) = 舰长
                {"ts": 1700000000},
            ]
        )
    )
    assert ld.msg_type == MessageType.MSG_DANMAKU
    assert ld.text == "great stream!"
    assert ld.uid == 123456
    assert ld.nickname == "CaptainUser"
    assert ld.admin is True
    assert ld.vip is False
    assert ld.svip is False
    assert ld.guard_level == 3
    assert ld.user_level == 25
    assert ld.medal is not None
    assert ld.medal.name == "MyMedal"
    assert ld.medal.level == 21
    assert ld.medal.up_name == "UpName"
    assert ld.medal.anchor_roomid == 22333
    assert ld.medal.color == 6126494
    assert ld.fans_medal_name == "MyMedal"
    assert ld.fans_medal_level == 21


def test_from_danmaku_guard_level_is_plain_int():
    """info[7] 是普通 int（大航海等级），不能当作列表下标。"""
    for guard in (0, 1, 2, 3):
        ld = LiveDanmaku.from_danmaku(
            _danmu(
                [
                    [0, 1, 25],
                    "hi",
                    [111, "U", 0, 0, 0],
                    [],
                    [10],
                    ["", ""],
                    0,
                    guard,
                ]
            )
        )
        assert ld.guard_level == guard


def test_from_danmaku_short_user_array_no_index_error():
    """短 info[2]（仅 uid+uname，无 admin 位）不应 IndexError，admin 安全降级 False。"""
    ld = LiveDanmaku.from_danmaku(
        _danmu(
            [
                [0, 1, 25, 16777215, 1700000000000],
                "short danmaku",
                [654321, "ShortUser"],  # 只有 2 个元素
                [],
                [25, 0, 0, 0, 0],
                ["", ""],
                0,
                0,
            ]
        )
    )
    assert ld.text == "short danmaku"
    assert ld.uid == 654321
    assert ld.nickname == "ShortUser"
    assert ld.admin is False
    assert ld.guard_level == 0


def test_from_danmaku_missing_index7_defaults_guard_zero():
    """缺少 info[7]（稀疏 payload）时 guard_level 默认 0，不抛异常。"""
    ld = LiveDanmaku.from_danmaku(
        _danmu(
            [
                [0, 1, 25],
                "no index7",
                [777, "NoSeven", 0],
            ]
        )
    )
    assert ld.text == "no index7"
    assert ld.uid == 777
    assert ld.guard_level == 0
    assert ld.admin is False


def test_from_danmaku_vip_svip_from_user_array():
    """vip/svip 来自用户数组 info[2][3]/[4]，而非 info[7]。"""
    ld = LiveDanmaku.from_danmaku(
        _danmu(
            [
                [0, 1, 25],
                "vip msg",
                [222, "VipUser", 0, 1, 1],  # vip=1, svip=1
                [],
                [30],
                ["", ""],
                0,
                0,
            ]
        )
    )
    assert ld.vip is True
    assert ld.svip is True
    assert ld.admin is False


def test_from_danmaku_face_url_empty():
    """弹幕 payload 不携带头像 URL，face_url 必须为空（头像由 bili_identity 按 UID 抓取）。"""
    ld = LiveDanmaku.from_danmaku(
        _danmu(
            [
                [0, 1, 25],
                "msg",
                [333, "User", 0, 0, 0, 1, 1, "#abcdef"],
                [],
                [5],
                ["", ""],
                0,
                0,
            ]
        )
    )
    assert ld.face_url == ""


def test_from_raw_json_routes_danmu_msg():
    """from_raw_json 兜底入口能正确路由 DANMU_MSG 到 from_danmaku。"""
    raw = json.dumps(
        _danmu(
            [
                [0, 1, 25],
                "routed",
                [444, "RoutedUser", 0, 0, 0],
                [],
                [12],
                ["", ""],
                0,
                1,
            ]
        )
    )
    ld = LiveDanmaku.from_raw_json(raw)
    assert ld is not None
    assert ld.text == "routed"
    assert ld.uid == 444
    assert ld.guard_level == 1


def test_from_danmaku_empty_info_is_safe():
    """空 info 不抛异常，产出空文本的弹幕对象。"""
    ld = LiveDanmaku.from_danmaku({"cmd": "DANMU_MSG", "info": []})
    assert ld.text == ""
    assert ld.uid == 0
    assert ld.guard_level == 0


def test_from_danmaku_score_reflects_guard_and_admin():
    """get_score 应反映正确解析出的 guard_level 与 admin（验证字段确实进了打分）。"""
    captain = LiveDanmaku.from_danmaku(
        _danmu([[0, 1, 25], "x", [1, "Cap", 1, 0, 0], [], [50], ["", ""], 0, 3])
    )
    plain = LiveDanmaku.from_danmaku(
        _danmu([[0, 1, 25], "x", [2, "Plain", 0, 0, 0], [], [1], ["", ""], 0, 0])
    )
    # 舰长(1000) + admin(500) + 高用户等级 应远高于普通观众
    assert captain.get_score() > plain.get_score()
    assert captain.guard_level == 3
    assert captain.admin is True


def test_fallback_urls_only_repeat_main_as_final_fallback():
    """Main WebSocket URL should only appear once in the fallback list."""
    assert WS_FALLBACK_URLS.count(WS_MAIN_URL) == 1
    assert WS_FALLBACK_URLS[-1] == WS_MAIN_URL


def test_preparing_marks_live_ended():
    """PREPARING should stop outer reconnect attempts after the room goes offline."""
    seen: list[str] = []
    listener = DanmakuListener(
        room_id=1,
        callbacks={"on_preparing": lambda: seen.append("preparing")},
    )

    asyncio.run(listener._dispatch_message("PREPARING", {"cmd": "PREPARING"}))

    assert listener._live_ended is True
    assert seen == ["preparing"]


def test_support_callbacks_publish_rich_event_before_lightweight_fallback():
    for cmd, fallback_callback, payload in (
        (
            "SEND_GIFT",
            "on_gift",
            {"data": {"uid": 9, "uname": "GiftUser", "giftName": "Heart", "num": 1}},
        ),
        (
            "SUPER_CHAT_MESSAGE",
            "on_sc",
            {"data": {"uid": 10, "user_info": {"uname": "SCUser"}, "message": "hi", "price": 30}},
        ),
    ):
        seen: list[tuple[str, object]] = []
        listener = DanmakuListener(
            room_id=1,
            callbacks={
                "on_event": lambda _cmd, event: seen.append(("on_event", event)),
                fallback_callback: lambda event, name=fallback_callback: seen.append((name, event)),
            },
        )

        asyncio.run(listener._dispatch_message(cmd, {"cmd": cmd, **payload}))

        assert [name for name, _event in seen] == ["on_event", fallback_callback]
        assert isinstance(seen[0][1], LiveDanmaku)


def test_bili_gift_packet_preserves_safe_provider_metadata_on_rich_event():
    seen: list[LiveDanmaku] = []
    listener = DanmakuListener(
        room_id=1,
        callbacks={"on_event": lambda _cmd, event: seen.append(event)},
    )
    packet = {
        "cmd": "SEND_GIFT",
        "data": {
            "uid": 9,
            "uname": "GiftUser",
            "giftName": "Heart",
            "num": 2,
            "coin_type": "gold",
            "total_coin": 2000,
            "tid": "evt-42",
            "timestamp": 1_720_000_000,
            "batch_combo_id": "combo-7",
            "combo_num": 2,
            "combo_end": False,
        },
    }

    asyncio.run(listener._dispatch_message("SEND_GIFT", packet))

    event = seen[0]
    assert event.provider_event_id == "evt-42"
    assert event.provider_timestamp_ms == 1_720_000_000_000
    assert event.combo_id == "combo-7"
    assert event.combo_count == 2
    assert event.combo_end is False


def test_bili_super_chat_packet_preserves_safe_provider_metadata_on_rich_event():
    seen: list[LiveDanmaku] = []
    listener = DanmakuListener(
        room_id=1,
        callbacks={"on_event": lambda _cmd, event: seen.append(event)},
    )
    packet = {
        "cmd": "SUPER_CHAT_MESSAGE",
        "data": {
            "uid": 10,
            "user_info": {"uname": "SCUser"},
            "message": "hello",
            "price": 30,
            "id": "sc-42",
            "ts": 1_720_000_000,
        },
    }

    asyncio.run(listener._dispatch_message("SUPER_CHAT_MESSAGE", packet))

    event = seen[0]
    assert event.provider_event_id == "sc-42"
    assert event.provider_timestamp_ms == 1_720_000_000_000


def test_bili_guard_packet_preserves_safe_provider_metadata_on_rich_event():
    seen: list[LiveDanmaku] = []
    listener = DanmakuListener(
        room_id=1,
        callbacks={"on_event": lambda _cmd, event: seen.append(event)},
    )
    packet = {
        "cmd": "GUARD_BUY",
        "data": {
            "uid": 11,
            "username": "GuardUser",
            "gift_name": "Captain",
            "num": 1,
            "price": 19_800_000,
            "msg_id": "guard-42",
            "timestamp": 1_720_000_001,
        },
    }

    asyncio.run(listener._dispatch_message("GUARD_BUY", packet))

    event = seen[0]
    assert event.provider_event_id == "guard-42"
    assert event.provider_timestamp_ms == 1_720_000_001_000


def test_bili_combo_packet_preserves_combo_identity_on_rich_event():
    seen: list[LiveDanmaku] = []
    listener = DanmakuListener(
        room_id=1,
        callbacks={"on_event": lambda _cmd, event: seen.append(event)},
    )
    packet = {
        "cmd": "COMBO_SEND",
        "data": {
            "uid": 9,
            "uname": "GiftUser",
            "gift_name": "Heart",
            "combo_num": 3,
            "coin_type": "gold",
            "total_coin": 3000,
            "msg_id": "evt-43",
            "batch_combo_id": "combo-7",
        },
    }

    asyncio.run(listener._dispatch_message("COMBO_SEND", packet))

    event = seen[0]
    assert event.provider_event_id == "evt-43"
    assert event.combo_id == "combo-7"
    assert event.combo_count == 3
    assert event.gift is not None
    assert event.gift.coin_type == "gold"


def test_enhanced_cmd_handler_table_keeps_static_handlers_callable():
    """Class-level enhanced handlers should stay callable from _CMD_HANDLERS."""
    handler = DanmakuListener._CMD_HANDLERS["SUPER_CHAT_MESSAGE_JPN"]

    ld = handler({"cmd": "SUPER_CHAT_MESSAGE_JPN", "data": {"uid": 9, "user_info": {"uname": "JP"}, "message": "hi"}})

    assert ld.msg_type == MessageType.MSG_SUPER_CHAT
    assert ld.uid == 9
    assert ld.nickname == "JP"
    assert ld.text == "hi"


def test_fallback_support_gift_rejects_generic_medal_or_toast_packets():
    medal = {"data": {"uid": 9, "name": "普通勋章", "message": "勋章升级"}}
    toast = {"data": {"uid": 9, "name": "普通提示", "message": "欢迎回来"}}

    assert DanmakuListener._fallback_support_gift_payload("FANS_MEDAL_CHANGE", medal) is None
    assert DanmakuListener._fallback_support_gift_payload("ROOM_TOAST_MESSAGE", toast) is None


def test_fallback_support_gift_rejects_user_toast_fans_medal_text():
    toast = {"data": {"uid": 9, "toast_msg": "\u70b9\u4eae\u7c89\u4e1d\u724c"}}

    assert DanmakuListener._fallback_support_gift_payload("USER_TOAST_MSG", toast) is None


def test_fallback_support_gift_rejects_bare_nested_name():
    packet = {
        "data": {
            "uid": 9,
            "gift": {"name": "灯牌"},
            "message": "点亮粉丝牌成功",
        }
    }

    assert DanmakuListener._fallback_support_gift_payload("ROOM_RANK_UPDATE", packet) is None


def test_fallback_support_gift_rejects_generic_nested_id_and_name():
    packet = {
        "data": {
            "uid": 9,
            "gift_info": {"id": 1, "name": "灯牌"},
            "message": "点亮粉丝牌成功",
        }
    }

    assert DanmakuListener._fallback_support_gift_payload("ROOM_RANK_UPDATE", packet) is None


def test_fallback_support_gift_rejects_nested_gift_id_without_gift_evidence():
    packet = {
        "data": {
            "uid": 9,
            "gift_info": {"gift_id": 1, "name": "灯牌"},
            "message": "点亮粉丝牌成功",
        }
    }

    assert DanmakuListener._fallback_support_gift_payload("ROOM_RANK_UPDATE", packet) is None


def test_fallback_support_gift_rejects_fans_medal_activation_toast():
    packet = {
        "data": {
            "uid": 42,
            "uname": "Viewer",
            "toast_msg": "点亮粉丝牌成功",
            "gift_info": {"gift_id": 1, "name": "灯牌"},
        }
    }

    assert DanmakuListener._fallback_support_gift_payload("USER_TOAST_MSG", packet) is None


def test_fallback_support_gift_rejects_generic_medal_name_even_with_transfer_text():
    packet = {
        "data": {
            "uid": 42,
            "uname": "Viewer",
            "toast_msg": "赠送粉丝牌成功",
            "gift_info": {"gift_id": 1, "name": "灯牌"},
        }
    }

    assert DanmakuListener._fallback_support_gift_payload("USER_TOAST_MSG", packet) is None


def test_fallback_support_gift_rejects_explicit_medal_gift_name():
    packet = {
        "data": {
            "uid": 42,
            "uname": "Viewer",
            "toast_msg": "赠送粉丝牌成功",
            "gift_info": {"gift_id": 1, "gift_name": "灯牌"},
        }
    }

    assert DanmakuListener._fallback_support_gift_payload("USER_TOAST_MSG", packet) is None


def test_fallback_support_gift_rejects_unknown_command_even_with_gift_fields():
    payload = DanmakuListener._fallback_support_gift_payload(
        "UNKNOWN_SUPPORT_PACKET",
        {
            "data": {
                "uid": 9,
                "uname": "GiftUser",
                "gift_id": 42,
                "gift_name": "小心心",
                "num": 2,
                "total_coin": 200,
            }
        },
    )

    assert payload is None


def test_brotli_missing_uses_supplied_log_callback(monkeypatch):
    """Decompression logging must be scoped to the listener/callback, not module state."""
    monkeypatch.setitem(sys.modules, "brotli", None)
    logs: list[tuple[str, str]] = []

    result = _decompress(b"", PROTOCOL_VERSION_BROTLI, lambda msg, level: logs.append((msg, level)))

    assert result == b""
    assert logs == [("brotli 库未安装，无法解压 brotli 数据包，跳过", "warning")]
