"""canonical identity 身份聚合 storage 层测试。

覆盖 device⟷steam / device⟷device 边构建、union-find 连通分量、代表元确定性、
denylist 防复活、归一化类型守卫、canonical 口径去重。

telemetry_server 用扁平 import（from storage import ...），这里把它的目录插进
sys.path 后直接 import storage，用临时文件库跑，不碰生产。
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_SRV_DIR = Path(__file__).resolve().parents[2] / "local_server" / "telemetry_server"
sys.path.insert(0, str(_SRV_DIR))

import storage as st  # noqa: E402
from storage import TelemetryStorage, normalize_steam_id  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return TelemetryStorage(tmp_path / "t.db")


def _report(store, device_id, *, steam=None, legacy=None, day=None):
    """模拟一次上报：events.payload 带 steam_user_id / device_id_legacy。"""
    payload = {"device_id": device_id}
    if steam is not None:
        payload["steam_user_id"] = steam
    if legacy is not None:
        payload["device_id_legacy"] = legacy
    daily = {}
    if day:
        daily = {day: {"call_count": 1, "total_tokens": 1}}
    store.store_event(
        device_id=device_id,
        app_version="1.0",
        payload_json=json.dumps(payload),
        daily_stats=daily,
        steam_user_id=(steam or ""),
    )


def _canon_of(store, device_id):
    row = store._get_conn().execute(
        "SELECT canonical_id FROM canonical_map WHERE entity_type='device' AND entity_id=?",
        (device_id,),
    ).fetchone()
    return row["canonical_id"] if row else None


# ---------------- normalize ----------------

@pytest.mark.parametrize("raw,expected", [
    ("76561198000000001", "76561198000000001"),
    ("0076561198000000001", "76561198000000001"),  # 去前导零
    ("0", ""),            # 哨兵
    ("00", ""),
    ("", ""),
    ("abc", ""),
    ("123abc", ""),
    ("9" * 21, ""),       # 超长 DoS guard
    ("99999999999999999999", ""),  # 超 u64
    (123, ""),            # 非字符串：number
    (None, ""),           # 非字符串：null
    (["x"], ""),          # 非字符串：list
])
def test_normalize(raw, expected):
    assert normalize_steam_id(raw) == expected


# ---------------- 边构建 + union-find ----------------

def test_two_devices_one_steam_merge(store):
    """两 device 登同一 steam → 同一 canonical。"""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceAAAAAAAAAAAA") == _canon_of(store, "deviceBBBBBBBBBBBB")


def test_device_legacy_alias_merge(store):
    """device_id_legacy → device⟷device 别名边 → 同一 canonical。"""
    _report(store, "deviceNEWNEWNEWNEW", legacy="deviceOLDOLDOLDOLD")
    # 老 device 也单独上报过（这样它在 devices 表里有行）
    _report(store, "deviceOLDOLDOLDOLD")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceNEWNEWNEWNEW") == _canon_of(store, "deviceOLDOLDOLDOLD")


def test_multihop_union(store):
    """A-X、B-X、B-Y：A B X Y 全归一个 canonical（多对多连通分量）。"""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")  # A-X
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001")  # B-X
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000002")  # B-Y
    store.build_edges_from_events()
    store.recompute_canonical()
    ca = _canon_of(store, "deviceAAAAAAAAAAAA")
    cb = _canon_of(store, "deviceBBBBBBBBBBBB")
    assert ca == cb
    # 代表元应是最小 steam 节点
    assert ca == "s:76561198000000001"


def test_canonical_id_deterministic(store):
    """重算两次 canonical_id 不抖（确定性代表元）。"""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000009")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000009")
    store.build_edges_from_events()
    store.recompute_canonical()
    first = _canon_of(store, "deviceAAAAAAAAAAAA")
    store.recompute_canonical()
    assert _canon_of(store, "deviceAAAAAAAAAAAA") == first


def test_device_only_is_own_canonical(store):
    """没登录过 Steam 的 device 自成一个 canonical（指标全量覆盖）。"""
    _report(store, "deviceLONELYXXXXXX")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceLONELYXXXXXX") == "d:deviceLONELYXXXXXX"


# ---------------- denylist 防复活 ----------------

def test_denylist_blocks_resurrection(store):
    """删号后重新扫 events（payload 里仍有该 steam）不得再产边。"""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceAAAAAAAAAAAA") == _canon_of(store, "deviceBBBBBBBBBBBB")

    # 删号
    store.add_steam_id_to_denylist("76561198000000001")
    # 边被删
    cnt = store._get_conn().execute(
        "SELECT COUNT(*) c FROM device_steam_edges WHERE steam_user_id='76561198000000001'"
    ).fetchone()["c"]
    assert cnt == 0
    # 游标重置 + 重新全量扫 events（payload 仍含被删 ID），denylist 必须挡住
    store._get_conn().execute("UPDATE edge_build_cursor SET last_event_id=0")
    store._get_conn().commit()
    store.build_edges_from_events()
    cnt2 = store._get_conn().execute(
        "SELECT COUNT(*) c FROM device_steam_edges WHERE steam_user_id='76561198000000001'"
    ).fetchone()["c"]
    assert cnt2 == 0, "denylist 未挡住回填复活"
    store.recompute_canonical()
    # 两 device 不再因该 steam 相连
    assert _canon_of(store, "deviceAAAAAAAAAAAA") != _canon_of(store, "deviceBBBBBBBBBBBB")


def test_malformed_payload_does_not_crash(store):
    """伪造/异常 payload（steam_user_id 是 number/null）不能让边构建崩。"""
    store.store_event(
        device_id="deviceWEIRDXXXXXX",
        app_version="1.0",
        payload_json=json.dumps({"device_id": "deviceWEIRDXXXXXX", "steam_user_id": 123}),
        daily_stats={},
    )
    store.store_event(
        device_id="deviceWEIRD2XXXXX",
        app_version="1.0",
        payload_json='{"device_id": "deviceWEIRD2XXXXX", "steam_user_id": null}',
        daily_stats={},
    )
    store.store_event(
        device_id="deviceWEIRD3XXXXX",
        app_version="1.0",
        payload_json="not even json",
        daily_stats={},
    )
    # 不抛异常，且没产出垃圾边
    n = store.build_edges_from_events()
    assert n == 3
    edges = store._get_conn().execute("SELECT COUNT(*) c FROM device_steam_edges").fetchone()["c"]
    assert edges == 0


# ---------------- canonical 口径指标去重 ----------------

def test_canonical_metrics_dedup(store):
    """同一真人两 device 同日活跃：device DAU=2，canonical DAU=1。"""
    today = date.today().isoformat()
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001", day=today)
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000001", day=today)
    store.build_edges_from_events()
    store.recompute_canonical()

    device_m = store.get_user_metrics(days=30)
    canon_m = store.get_canonical_metrics(days=30)
    assert device_m["dau_today"] == 2
    assert canon_m["canonical_dau_today"] == 1
    assert canon_m["total_canonical"] == 1


def test_edge_uses_event_time_not_now(store):
    """边 first_seen 取 events.received_at，不是 job 墙上时间。"""
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    # 手动把事件 received_at 改成一个月前，模拟历史回填
    old = (date.today() - timedelta(days=30)).isoformat() + "T00:00:00.000+08:00"
    conn = store._get_conn()
    conn.execute("UPDATE events SET received_at=?", (old,))
    conn.commit()
    store.build_edges_from_events()
    fs = conn.execute(
        "SELECT first_seen FROM device_steam_edges WHERE device_id='deviceAAAAAAAAAAAA'"
    ).fetchone()["first_seen"]
    assert fs == old, "边时间戳应取事件观测时间，而非回填运行时刻"
