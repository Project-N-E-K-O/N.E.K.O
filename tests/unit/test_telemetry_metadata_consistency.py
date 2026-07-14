"""Smoke test：telemetry distribution / steam_user_id 同源一致性。

回归 ``_get_telemetry_metadata()`` 修复的 race —— 原本
``_get_telemetry_distribution()`` 和 ``_get_telemetry_steam_user_id()`` 各调
一次 ``Users.GetSteamID()``，Steamworks 异步 init 时两次调用可能跨越 ready
边界，产出 ``distribution='release'`` + 非空 Steam64 的矛盾态。合并成一次
调用后，两个字段从同一次观测派生，矛盾态在源头消除。

核心不变量（绝不能违反）：
  steam_user_id 非空  ⟹  distribution == 'steam'
等价说法：不可能出现 release/source + 非空 Steam64。
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.token_tracker as tt
import utils.token_tracker.reporting as reporting
import utils.token_tracker.telemetry as telemetry


@contextmanager
def _patched_env(*, is_release, steam_id, workshop_subs, workshop_file_exists):
    """搭一套可控的 Steamworks / config / build 环境。

    steam_id 为 None 表示 ``get_steamworks()`` 返回 None（SDK 没起来）。
    返回 GetSteamID 的 MagicMock，供断言"只调一次"。
    """
    get_steam_id = MagicMock(return_value=steam_id if steam_id is not None else 0)

    if steam_id is None:
        sw = None
    else:
        sw = SimpleNamespace(
            Users=SimpleNamespace(GetSteamID=get_steam_id),
            Workshop=SimpleNamespace(
                GetNumSubscribedItems=MagicMock(return_value=workshop_subs)
            ),
        )

    fake_config_dir = MagicMock()
    fake_config_dir.__truediv__ = lambda self, other: SimpleNamespace(
        exists=lambda: workshop_file_exists
    )
    cm = SimpleNamespace(config_dir=fake_config_dir)

    with patch.object(telemetry, "_is_release_build", return_value=is_release), \
         patch("utils.steam_state.get_steamworks", return_value=sw), \
         patch("utils.config_manager.get_config_manager", return_value=cm):
        yield get_steam_id


# (is_release, steam_id, workshop_subs, workshop_file_exists) -> (distribution, steam_user_id)
_CASES = [
    # 源码模式：永远 source + 空，哪怕 Steam 在跑
    (False, 76561198000000000, 5, True, ("source", "")),
    (False, None, 0, False, ("source", "")),
    # release + 拿到 Steam64 → steam + ID
    (True, 76561198000000000, 0, False, ("steam", "76561198000000000")),
    # release + SDK 起来但没登录用户(0)，订阅过工坊 → steam + 空（合法尾部）
    (True, 0, 3, False, ("steam", "")),
    # release + SDK 起来但 0、无订阅、有 workshop_config.json 兜底 → steam + 空
    (True, 0, 0, True, ("steam", "")),
    # release + SDK 没起来(None)、有 workshop 文件兜底 → steam + 空
    (True, None, 0, True, ("steam", "")),
    # release + 无任何 Steam 信号 → release + 空
    (True, 0, 0, False, ("release", "")),
    (True, None, 0, False, ("release", "")),
]


@pytest.mark.parametrize("is_release,steam_id,subs,file_exists,expected", _CASES)
def test_metadata_mapping(is_release, steam_id, subs, file_exists, expected):
    with _patched_env(
        is_release=is_release,
        steam_id=steam_id,
        workshop_subs=subs,
        workshop_file_exists=file_exists,
    ):
        assert tt._get_telemetry_metadata() == expected


@pytest.mark.parametrize("is_release,steam_id,subs,file_exists,expected", _CASES)
def test_invariant_no_id_without_steam(is_release, steam_id, subs, file_exists, expected):
    """核心不变量：非空 steam_user_id ⟹ distribution == 'steam'。"""
    with _patched_env(
        is_release=is_release,
        steam_id=steam_id,
        workshop_subs=subs,
        workshop_file_exists=file_exists,
    ):
        distribution, steam_user_id = tt._get_telemetry_metadata()
    if steam_user_id != "":
        assert distribution == "steam", (
            f"矛盾态：distribution={distribution!r} 带非空 steam_user_id={steam_user_id!r}"
        )


def test_get_steam_id_called_at_most_once():
    """证明 race 被修：单次上报内 GetSteamID() 最多调用一次。

    原 bug 是两个函数各调一次，第二次跨越 ready 边界拿到不同结果。合并后
    distribution 和 steam_user_id 必须来自同一次观测。
    """
    with _patched_env(
        is_release=True,
        steam_id=76561198000000000,
        workshop_subs=0,
        workshop_file_exists=False,
    ) as get_steam_id:
        tt._get_telemetry_metadata()
    assert get_steam_id.call_count <= 1, (
        f"GetSteamID() 被调用 {get_steam_id.call_count} 次，race 风险未消除"
    )


def test_exceptions_swallowed():
    """埋点不能抛：Steamworks 调用炸了也要安全降级，不冒泡。"""
    boom = MagicMock(side_effect=RuntimeError("steam boom"))
    sw = SimpleNamespace(
        Users=SimpleNamespace(GetSteamID=boom),
        Workshop=SimpleNamespace(GetNumSubscribedItems=boom),
    )
    fake_config_dir = MagicMock()
    fake_config_dir.__truediv__ = lambda self, other: SimpleNamespace(
        exists=MagicMock(side_effect=OSError("disk boom"))
    )
    cm = SimpleNamespace(config_dir=fake_config_dir)

    with patch.object(telemetry, "_is_release_build", return_value=True), \
         patch("utils.steam_state.get_steamworks", return_value=sw), \
         patch("utils.config_manager.get_config_manager", return_value=cm):
        # 全程异常仍应安全返回 release + 空，绝不抛
        assert tt._get_telemetry_metadata() == ("release", "")


def test_facade_telemetry_url_remains_live(monkeypatch):
    """Facade writes and owner-module rebinds must observe the same URL state."""
    monkeypatch.setattr(tt, "_TELEMETRY_SERVER_URL", "http://127.0.0.1:18099")
    assert reporting._TELEMETRY_SERVER_URL == "http://127.0.0.1:18099"

    reporting._TELEMETRY_SERVER_URL = ""
    assert tt._TELEMETRY_SERVER_URL == ""
