"""router 五步短路（场景/查询/开关/动作/属性控制）分支测试。

使用 mock devices_cache.json 内容：卧室灯 / 客厅空调 / 书房电视。
"""

from plugin.plugins.mijia.nlp.router import route

# 模拟 devices_cache.json 内容
MOCK_DEVICES = [
    {
        "did": "bedroom-light-1",
        "name": "卧室灯",
        "model": "light.wyze",
        "room_name": "卧室",
        "is_online": True,
        "alias": "床头灯",
        "properties": [
            {"siid": 1, "piid": 1, "name": "Switch Status", "access": "read_write", "type": "bool"},
            {"siid": 2, "piid": 1, "name": "Brightness", "access": "read_write", "type": "uint8", "value_range": [0, 100, 1]},
        ],
        "actions": [],
    },
    {
        "did": "living-ac-1",
        "name": "客厅空调",
        "model": "ac.midea.fz",
        "room_name": "客厅",
        "is_online": True,
        "alias": "空调",
        "properties": [
            {"siid": 1, "piid": 1, "name": "Switch Status", "access": "read_write", "type": "bool"},
            {"siid": 2, "piid": 1, "name": "Target Temperature", "access": "read_write", "type": "float", "value_range": [16, 30, 1]},
        ],
        "actions": [],
    },
    {
        "did": "study-tv-1",
        "name": "书房电视",
        "model": "miot.tv.v2",
        "room_name": "书房",
        "is_online": False,
        "properties": [
            {"siid": 1, "piid": 2, "name": "Power", "access": "read_write", "type": "bool"},
        ],
        "actions": [],
    },
    {
        "did": "robot-vacuum-1",
        "name": "扫地机",
        "model": "roborock.vacuum.a10",
        "room_name": "客厅",
        "is_online": True,
        "properties": [],
        "actions": [
            {"siid": 2, "aiid": 1, "name": "Start Sweep"},
        ],
    },
    {
        "did": "dryer-1",
        "name": "烘干机",
        "model": "midea.dryer.d10",
        "room_name": "阳台",
        "is_online": True,
        "properties": [
            {"siid": 2, "piid": 1, "name": "Temperature", "access": "read_write", "type": "uint8", "value_range": [30, 90, 5]},
        ],
        "actions": [],
    },
]


async def test_scene_branch():
    result = await route("执行回家模式", MOCK_DEVICES)
    assert result.branch == "scene"
    assert result.scene_name == "回家模式"


async def test_query_branch():
    result = await route("空调多少度", MOCK_DEVICES)
    assert result.branch == "query"
    assert result.device_hint == "空调"


async def test_switch_branch():
    result = await route("关卧室灯", MOCK_DEVICES)
    assert result.branch == "switch"
    assert result.parsed is not None
    assert result.parsed.action == "switch"
    assert result.parsed.value is False
    assert result.match is not None
    assert result.match.status == "ok"
    assert result.match.devices[0]["did"] == "bedroom-light-1"


async def test_switch_branch_not_hijacked_by_query():
    # "关闭卧室灯怎么样" 同时命中开关标记与查询词，应走开关分支而非查询
    result = await route("关闭卧室灯怎么样", MOCK_DEVICES)
    assert result.branch == "switch"
    assert result.parsed is not None
    assert result.parsed.action == "switch"
    assert result.parsed.value is False


async def test_action_branch():
    result = await route("开始扫地", MOCK_DEVICES)
    assert result.branch == "action"
    assert result.verb == "开始"


async def test_action_verb_prefix_falls_to_control():
    # 设备名以动作动词开头："烘干机温度50度" 应走 control（温度=50），
    # 而不是被动作分支误拆成 verb="烘干" + device="机温度50度"
    result = await route("烘干机温度50度", MOCK_DEVICES)
    assert result.branch == "control"
    assert result.parsed is not None
    assert result.parsed.prop == "温度"
    assert result.parsed.value == 50
    assert result.match is not None
    assert result.match.status == "ok"
    assert result.match.devices[0]["did"] == "dryer-1"


async def test_control_branch():
    result = await route("空调26度", MOCK_DEVICES)
    assert result.branch == "control"
    assert result.parsed is not None
    assert result.parsed.prop == "温度"
    assert result.parsed.value == 26
    assert result.match is not None
    assert result.match.status == "ok"
    assert result.match.devices[0]["did"] == "living-ac-1"


async def test_unknown_branch():
    result = await route("把窗帘拉开", MOCK_DEVICES)
    assert result.branch == "unknown"
