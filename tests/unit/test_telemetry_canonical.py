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
    ("²", ""),       # Unicode 上标 '²'：isdigit()=True 但 int() 抛 ValueError
    ("１２３", ""),   # 全角数字
    ("١٢٣", ""),   # 阿拉伯-印度数字
])
def test_normalize(raw, expected):
    # 关键：非法输入只能返回 ''，绝不抛异常（否则 ingest 500 / build_edges 卡死）
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


def test_drain_loop_catches_up(store):
    """build_all_pending_edges 必须 drain 全部，不能只吃一页（游标落后 bug）。"""
    for i in range(12):
        _report(store, f"device{i:012d}", steam=f"765611980000000{i:02d}")
    # 单页只吃 5 条，但 build_all_pending_edges 应循环到追平 12 条
    total = store.build_all_pending_edges(batch_limit=5)
    assert total == 12
    cursor = store._get_conn().execute(
        "SELECT last_event_id FROM edge_build_cursor WHERE id=1"
    ).fetchone()["last_event_id"]
    max_event = store._get_conn().execute("SELECT MAX(id) m FROM events").fetchone()["m"]
    assert cursor == max_event
    edges = store._get_conn().execute("SELECT COUNT(*) c FROM device_steam_edges").fetchone()["c"]
    assert edges == 12


def test_build_edges_normalizes_payload(store):
    """events.payload 里的原始未归一化 steam_user_id 必须归一化后再建边。

    '0076561198000000001' 与 '76561198000000001' 应归并到同一 canonical；
    哨兵 '0' 不产边。这是身份拆分/污染的回归点。
    """
    _report(store, "deviceLEADINGZERO0", steam="0076561198000000001")  # 前导零
    _report(store, "deviceNORMALXXXXXX", steam="76561198000000001")     # 规范形
    _report(store, "deviceSENTINELXXXX", steam="0")                     # 哨兵
    store.build_edges_from_events()
    store.recompute_canonical()
    assert _canon_of(store, "deviceLEADINGZERO0") == _canon_of(store, "deviceNORMALXXXXXX")
    # 哨兵不产边 → 自成 canonical
    assert _canon_of(store, "deviceSENTINELXXXX") == "d:deviceSENTINELXXXX"
    edges = store._get_conn().execute(
        "SELECT DISTINCT steam_user_id FROM device_steam_edges"
    ).fetchall()
    assert [r["steam_user_id"] for r in edges] == ["76561198000000001"]


def test_denylist_collapses_in_store_event(store):
    """删号后该 Steam64 不得经后续上报写回 devices 列（脱敏不被覆盖）。"""
    store.add_steam_id_to_denylist("76561198000000001")
    _report(store, "deviceXXXXXXXXXXXX", steam="76561198000000001")
    val = store._get_conn().execute(
        "SELECT steam_user_id FROM devices WHERE device_id='deviceXXXXXXXXXXXX'"
    ).fetchone()["steam_user_id"]
    assert val == "", "denylisted ID 被上报写回 devices 列"


def test_denylist_atomic_guard_on_insert(store):
    """denylist 已有的 ID，即使 events 里有，build 也不得插入（原子 WHERE NOT EXISTS）。"""
    # 先把某 steam 加入 denylist
    store.add_steam_id_to_denylist("76561198000000001")
    # 再来一批含该 ID 的 events（模拟删号后又上报 / 回填扫到旧事件）
    _report(store, "deviceAAAAAAAAAAAA", steam="76561198000000001")
    _report(store, "deviceBBBBBBBBBBBB", steam="76561198000000002")
    store.build_edges_from_events()
    rows = store._get_conn().execute(
        "SELECT steam_user_id FROM device_steam_edges ORDER BY steam_user_id"
    ).fetchall()
    sids = [r["steam_user_id"] for r in rows]
    assert "76561198000000001" not in sids, "denylist ID 被插回"
    assert "76561198000000002" in sids


def test_device_legacy_length_validated(store):
    """device_id_legacy 绕过 Pydantic，超长/超短串不得进 device_alias_edges。"""
    dev = "deviceMAINXXXXXXX"
    # 超长 legacy（>128）应被拒
    _report(store, dev, legacy="x" * 200)
    # 太短 legacy（<16）应被拒
    _report(store, "deviceMAIN2XXXXXX", legacy="short")
    # 合法长度 legacy 应建边
    _report(store, "deviceMAIN3XXXXXX", legacy="deviceLEGACYOKXXX")
    store.build_edges_from_events()
    rows = store._get_conn().execute(
        "SELECT dev_lo, dev_hi FROM device_alias_edges"
    ).fetchall()
    pairs = [(r["dev_lo"], r["dev_hi"]) for r in rows]
    # 只有合法那条建了边
    assert pairs == [tuple(sorted(("deviceMAIN3XXXXXX", "deviceLEGACYOKXXX")))]


def test_alias_repointed_when_canonical_removed(store):
    """删号删掉某 canonical 后，指向它的旧 alias 不得悬空，要重指/删除。

    Codex 序列：d1、d2 先各自成 canonical，再都登 S 合并到 s:S（产生 alias
    d:d1->s:S、d:d2->s:S）；denylist S 后 d1/d2 退回各自 canonical，
    resolve_canonical('d:d1') 必须回到 d:d1，绝不能返回已死的 s:S。
    """
    # 阶段 1：d1、d2 各自先成独立 canonical
    _report(store, "deviceD1XXXXXXXXX")
    _report(store, "deviceD2XXXXXXXXX")
    store.build_edges_from_events()
    store.recompute_canonical()
    # 阶段 2：都登 S → 合并到 s:S，旧 device canonical 写 alias 指向 s:S
    _report(store, "deviceD1XXXXXXXXX", steam="76561198000000001")
    _report(store, "deviceD2XXXXXXXXX", steam="76561198000000001")
    store.build_edges_from_events()
    store.recompute_canonical()
    assert store.resolve_canonical("d:deviceD1XXXXXXXXX") == "s:76561198000000001"
    # 阶段 3：删号 S → 分量炸开，d1/d2 复活为各自 canonical
    store.add_steam_id_to_denylist("76561198000000001")
    store.recompute_canonical()
    assert store.resolve_canonical("d:deviceD1XXXXXXXXX") == "d:deviceD1XXXXXXXXX"
    assert store.resolve_canonical("d:deviceD2XXXXXXXXX") == "d:deviceD2XXXXXXXXX"
    assert _canon_of(store, "deviceD1XXXXXXXXX") == "d:deviceD1XXXXXXXXX"
    # 不应残留任何指向已删 s:S 的 alias
    dangling = store._get_conn().execute(
        "SELECT COUNT(*) c FROM canonical_alias WHERE new_canonical_id='s:76561198000000001'"
    ).fetchone()["c"]
    assert dangling == 0


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
