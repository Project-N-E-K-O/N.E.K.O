"""device_matcher 设备匹配优先级测试（精确别名 > 精确名 > 区域拆分 > 模糊）。

mock 设备含 power 属性位于 siid=1/piid=2（不在默认 2/1），
确保匹配结果与属性位置无关。
"""

from plugin.plugins.mijia.nlp.device_matcher import match_devices

MOCK_DEVICES = [
    {
        "did": "bedroom-light-1",
        "name": "卧室灯",
        "model": "light.wyze",
        "room_name": "卧室",
        "is_online": True,
        "alias": "床头灯,夜灯",
        "properties": [
            {"siid": 1, "piid": 1, "name": "Switch Status", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
    {
        "did": "living-ac-1",
        "name": "空调",
        "model": "ac.midea.fz",
        "room_name": "客厅",
        "is_online": True,
        "alias": "",
        "properties": [],
        "actions": [],
    },
    {
        "did": "study-tv-1",
        "name": "电视",
        "model": "miot.tv.v2",
        "room_name": "书房",
        "is_online": False,
        # power 属性刻意放在 siid=1/piid=2（非默认 2/1），匹配不应依赖属性位置
        "properties": [
            {"siid": 1, "piid": 2, "name": "Power", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
]


def test_exact_name_match():
    result = match_devices("卧室灯", MOCK_DEVICES)
    assert result.status == "ok"
    assert result.device["did"] == "bedroom-light-1"


def test_alias_match():
    result = match_devices("床头灯", MOCK_DEVICES)
    assert result.status == "ok"
    assert result.device["did"] == "bedroom-light-1"


def test_room_prefix_split():
    # 设备名是"空调"（非"客厅空调"），"客厅空调"只能靠房间前缀拆分命中
    # （精确名/别名都不匹配），真正走到区域+设备名拆分分支
    result = match_devices("客厅空调", MOCK_DEVICES)
    assert result.status == "ok"
    assert result.device["did"] == "living-ac-1"


def test_room_split_partial_alias():
    # 房间限定 + 设备部分只是别名的子串："卧室床尾" → 设备部分"床尾" ⊆ 别名"床尾台灯"
    devices = MOCK_DEVICES + [{
        "did": "bedroom-lamp-3",
        "name": "台灯",
        "model": "light.wyze",
        "room_name": "卧室",
        "is_online": True,
        "alias": "床尾台灯",
        "properties": [],
        "actions": [],
    }]
    result = match_devices("卧室床尾", devices)
    assert result.status == "ok"
    assert result.device["did"] == "bedroom-lamp-3"


def test_fuzzy_tv_match():
    # "电视机" 通过模糊子串命中 "电视"（did 不含属性位置假设）
    result = match_devices("电视机", MOCK_DEVICES)
    assert result.status == "ok"
    assert result.device["did"] == "study-tv-1"
    # power 属性不在默认 2/1
    assert result.device["properties"][0]["siid"] == 1
    assert result.device["properties"][0]["piid"] == 2


def test_not_found_lists_devices():
    result = match_devices("冰箱", MOCK_DEVICES)
    assert result.status == "not_found"
    assert "当前设备列表" in result.message
    assert "卧室灯" in result.message


def test_empty_devices():
    result = match_devices("灯", [])
    assert result.status == "not_found"
    assert "设备列表为空" in result.message


def test_ambiguous_room_device():
    # 两个同房设备（卧室里两台"电视"），"卧室电视"走房间拆分后歧义
    devices = MOCK_DEVICES + [
        {
            "did": "bedroom-tv-2",
            "name": "电视",
            "model": "miot.tv.v2",
            "room_name": "卧室",
            "is_online": True,
            "properties": [],
            "actions": [],
        },
        {
            "did": "bedroom-tv-3",
            "name": "电视",
            "model": "miot.tv.v2",
            "room_name": "卧室",
            "is_online": True,
            "properties": [],
            "actions": [],
        },
    ]
    result = match_devices("卧室电视", devices)
    assert result.status == "ambiguous"
    assert "找到 2 个匹配" in result.message
