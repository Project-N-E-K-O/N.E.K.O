"""战雷（War Thunder）本地遥测采集模块。

游戏运行且进入战局后，会在本机 8111 端口提供一组 JSON 接口。
本模块负责拉取这些接口、做容错，并把原始 JSON 解析成结构化的 Python 变量（dataclass）。

三种需要处理的异常场景：
    1) 游戏没开 / 8111 连不上     -> 连接超时或被拒绝       -> ConnectionState.OFFLINE
    2) 在机库 / 菜单 / 战局外      -> 接口返回 {"valid": false} -> ConnectionState.NOT_IN_BATTLE
    3) 数据为空                    -> 空数组 / 空字段 / null    -> 字段给安全默认值，不抛异常

直接运行本文件会以固定频率实时打印一份遥测快照，方便肉眼验证。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8111
DEFAULT_TIMEOUT = 1.5  # 秒；游戏没开时尽快失败，避免卡住轮询


# 图片魔数 -> 扩展名（map.img 实际多为 JPEG，少数情况可能是 PNG）
_IMAGE_MAGIC: dict[bytes, str] = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
}


def _detect_image_ext(data: bytes) -> str | None:
    """通过文件头魔数判断图片格式，非图片返回 None。"""
    for magic, ext in _IMAGE_MAGIC.items():
        if data.startswith(magic):
            return ext
    return None


class ConnectionState(Enum):
    """整体连接 / 战局状态。"""

    OFFLINE = "offline"            # 端口连不上：游戏没开，或 8111 未监听
    NOT_IN_BATTLE = "not_in_battle"  # 能连上但 valid=false：在机库 / 菜单 / 加载中
    IN_BATTLE = "in_battle"          # 正常：已进入战局，数据有效


# ---------------------------------------------------------------------------
# 结构化数据定义
# ---------------------------------------------------------------------------


@dataclass
class EngineState:
    """单台发动机的状态（飞机可能有多台）。"""

    index: int
    throttle_pct: float | None = None
    power_hp: float | None = None
    rpm: float | None = None
    manifold_pressure_atm: float | None = None
    oil_temp_c: float | None = None
    thrust_kgs: float | None = None
    efficiency_pct: float | None = None


@dataclass
class VehicleState:
    """来自 /state 的载具仪表状态（以飞机字段为主，键名随载具变化）。"""

    valid: bool = False
    altitude_m: float | None = None      # H, m
    tas_kmh: float | None = None         # 真空速 TAS
    ias_kmh: float | None = None         # 表速 IAS
    mach: float | None = None            # M
    aoa_deg: float | None = None         # 迎角
    aos_deg: float | None = None         # 侧滑角
    load_factor: float | None = None     # Ny 过载
    climb_ms: float | None = None        # Vy, m/s 垂直速度
    roll_rate_dps: float | None = None   # Wx, deg/s 滚转角速度
    fuel_kg: float | None = None         # Mfuel, kg
    fuel_full_kg: float | None = None    # Mfuel0, kg
    # 控制面位置（%）
    aileron_pct: float | None = None
    elevator_pct: float | None = None
    rudder_pct: float | None = None
    airbrake_pct: float | None = None
    flaps_pct: float | None = None
    gear_pct: float | None = None
    engines: list[EngineState] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # 原始 JSON，陆战/其它字段从这里取


@dataclass
class Indicators:
    """来自 /indicators 的座舱原始数据。"""

    valid: bool = False
    army: str | None = None      # "air" / "tank" ...
    vehicle_type: str | None = None  # 机型 / 车型代号，如 "j_15t"
    speed: float | None = None
    heading_deg: float | None = None     # compass
    roll_deg: float | None = None        # aviahorizon_roll
    pitch_deg: float | None = None       # aviahorizon_pitch
    bank_deg: float | None = None        # bank 倾侧
    vario_ms: float | None = None        # vario 升降速率（部分机型有）
    g_meter: float | None = None         # 过载表当前值（部分机型有）
    g_meter_min: float | None = None     # 过载表最小（机内记录）
    g_meter_max: float | None = None     # 过载表最大（机内记录）
    throttle: float | None = None
    flaps: float | None = None
    gears: float | None = None
    airbrake: float | None = None        # airbrake_lever
    # 地面载具(坦克)字段；空中载具为 None
    gear: float | None = None            # 档位（整数；配合 gear_neutral 判断前进/空挡/倒挡）
    gear_neutral: float | None = None    # 空挡所在的档位编号
    rpm: float | None = None             # 发动机转速
    stabilizer: float | None = None      # 火炮稳定器（1=启用）
    first_stage_ammo: float | None = None  # 一级待发弹数量
    crew_total: float | None = None      # 乘员总数
    crew_current: float | None = None    # 存活乘员数
    cruise_control: float | None = None  # 定速巡航档
    lws: float | None = None             # 激光告警：-1无设备 / 0待机 / 1告警 / 2损坏
    ircm: float | None = None            # 红外对抗
    has_speed_warning: float | None = None  # 超速/转向告警
    gunner_state: float | None = None    # 炮手：0正常 / 1无人补位 / 2补位中 / 3未确认
    driver_state: float | None = None    # 驾驶员：0正常 / 1无人补位 / 2补位中 / 3未确认
    # 直升机字段；固定翼/地面为 None
    is_helicopter: bool = False          # 是否直升机（运行时按字段特征判定）
    prop_rpm: float | None = None        # 旋翼转速（直升机）/ 螺旋桨转速
    radio_altitude: float | None = None  # 无线电高度（离地高度；8111 提供时固定翼/直升机均可用）
    # 发动机类型：喷气机(涡轮)与活塞机的温度语义不同，需区分
    is_jet: bool = False                     # 是否喷气发动机（运行时按字段特征判定）
    # 发动机温度（多发取各发动机有效值的最大；无效占位 -273.15 已剔除）
    water_temperature: float | None = None   # 水温 ℃（仅液冷活塞机；气冷/喷气为 None）
    head_temperature: float | None = None    # 缸头温度 ℃（仅气冷活塞机）
    turbine_temperature: float | None = None # 涡轮/排气温度 ℃（仅喷气机；游戏放在 water_temperature 键里，量级数百度）
    oil_temperature: float | None = None     # 油温 ℃（活塞机；喷气机一般也有）
    # 开火状态：扫描所有 weaponN 扳机键，任一触发即开火中。仅空军有该信号；
    # 陆/海战 indicators 无 weaponN 键，恒为 None（不适用）。
    weapon_firing: bool | None = None        # True=开火中 / False=空军未开火 / None=该载具无开火信号
    # 游戏内时间（座舱时钟）
    game_time: str | None = None             # "HH:MM:SS"（当日时刻）
    game_time_sec: int | None = None         # 当日秒数（0~86399）
    # 起落架状态（由三个指示灯 gear_lamp_down/up/off 归并的离散状态）
    gear_state: str | None = None            # down=锁定放下 / up=锁定收起 / moving=运动中 / None=无此设备
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MapObject:
    """/map_obj.json 中的单个物体。坐标均为 0~1 归一化值。"""

    type: str                       # aircraft / ground_model / airfield / bombing_point ...
    icon: str = "none"              # Player / Fighter / LightTank / SPAA ...
    faction: str = "other"          # self / ally / enemy / other（由颜色推断）
    color_hex: str = ""
    color_rgb: tuple[int, int, int] = (0, 0, 0)
    blink: int = 0
    # 点目标坐标
    x: float | None = None
    y: float | None = None
    # 朝向单位向量（飞机才有）
    dx: float | None = None
    dy: float | None = None
    # 线段坐标（机场跑道等）
    sx: float | None = None
    sy: float | None = None
    ex: float | None = None
    ey: float | None = None


# 计入敌我统计的作战单位类型（其余如 bombing_point/airfield 是任务/场景标记，不算单位）
_UNIT_TYPES = {"aircraft", "ground_model"}


@dataclass
class MapInfo:
    """/map_info.json：归一化坐标 -> 真实米坐标的换算参数。"""

    valid: bool = False
    map_generation: int | None = None  # 地图版本号，换图时递增，可用于去重保存
    grid_size: tuple[float, float] | None = None
    grid_steps: tuple[float, float] | None = None
    grid_zero: tuple[float, float] | None = None
    map_min: tuple[float, float] | None = None
    map_max: tuple[float, float] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HudMessage:
    """/hudmsg 中的单条事件（击杀 / 受损 / 提示）。"""

    id: int
    kind: str  # "event" 或 "damage"
    msg: str = ""
    sender: str = ""
    enemy: bool = False
    mode: str = ""
    time: int | None = None


@dataclass
class Telemetry:
    """一次完整轮询得到的遥测快照。"""

    state: ConnectionState
    timestamp: float
    in_battle: bool = False
    vehicle: VehicleState = field(default_factory=VehicleState)
    indicators: Indicators = field(default_factory=Indicators)
    map_objects: list[MapObject] = field(default_factory=list)
    map_info: MapInfo = field(default_factory=MapInfo)
    mission_status: str | None = None
    mission_objectives: Any | None = None
    hud_events: list[HudMessage] = field(default_factory=list)
    chat: list[dict[str, Any]] = field(default_factory=list)

    @property
    def player(self) -> MapObject | None:
        """地图上的“自己”这个图标（如果存在）。"""
        for obj in self.map_objects:
            if obj.icon == "Player":
                return obj
        return None

    @property
    def enemies(self) -> list[MapObject]:
        # 仅作战单位（飞机/地面载具）计入敌军；bombing_point 等任务标记是红色但非单位
        return [
            o for o in self.map_objects
            if o.faction == "enemy" and o.type in _UNIT_TYPES
        ]

    @property
    def allies(self) -> list[MapObject]:
        return [
            o for o in self.map_objects
            if o.faction == "ally" and o.type in _UNIT_TYPES
        ]

    @property
    def domain(self) -> str:
        """当前载具域：air / ground / naval / unknown。"""
        return detect_domain(self.indicators, self.in_battle, self.map_objects)

    def to_dict(self) -> dict[str, Any]:
        """转成可直接 json.dumps 的纯字典（枚举转字符串，附带常用派生字段）。"""
        d = asdict(self)
        d["state"] = self.state.value
        d["player"] = asdict(self.player) if self.player else None
        d["enemy_count"] = len(self.enemies)
        d["ally_count"] = len(self.allies)
        dom = self.domain
        d["domain"] = dom
        d["domain_label"] = _DOMAIN_LABELS.get(dom, dom)
        return d


# ---------------------------------------------------------------------------
# 载具域判断
# ---------------------------------------------------------------------------

_DOMAIN_LABELS = {
    "menu": "主界面/机库",
    "air": "空军",
    "heli": "直升机",
    "ground": "陆军",
    "naval": "海军",
    "unknown": "未知",
}


def detect_domain(
    indicators: "Indicators",
    in_battle: bool,
    map_objects: "list[MapObject] | None" = None,
) -> str:
    """判断当前载具域：menu / air / heli / ground / naval。

    判定依据（基于实测的可用数据）：
      - 不在战局（in_battle=False）-> menu（主界面/机库）。
        注意：主界面里 indicators/state/mission 都可能“像在战局”
        （valid=true、status=running、展示选中的载具），唯有 map_info 必为 false，
        因此战局与否必须由调用方据 map_info.valid 判定后通过 in_battle 传入，
        本函数务必先处理 in_battle 再看 indicators，避免把机库选中的飞机误判为空战。
      - 在战局且 indicators 有效：army=="air" 时再分直升机(heli)/固定翼(air)；
        其它（tank 等）-> ground
      - 在战局但 indicators 无效：海军模式特征（游戏不填充海军 indicators，
        海军玩家在 map_obj 中与陆军同为 ground_model，无法靠图标区分）-> naval
        兜底：若 map_obj 玩家图标 type 为 aircraft，则判 air

    注意：游戏 API 不提供街机/真实/全真（AB/RB/SB）的对战类型，无法判断。
    """
    if not in_battle:
        return "menu"
    army = getattr(indicators, "army", None)
    if getattr(indicators, "valid", False) and army:
        if army == "air":
            return "heli" if getattr(indicators, "is_helicopter", False) else "air"
        return "ground"
    if map_objects:
        for o in map_objects:
            if getattr(o, "icon", "") == "Player":
                if getattr(o, "type", "") == "aircraft":
                    return "air"
                break
    return "naval"


# ---------------------------------------------------------------------------
# 解析辅助函数
# ---------------------------------------------------------------------------


def _num(d: dict[str, Any], *names: str) -> float | None:
    """从 dict 中按候选键名顺序取第一个存在的数值，取不到返回 None。"""
    for name in names:
        if name in d and d[name] is not None:
            try:
                return float(d[name])
            except (TypeError, ValueError):
                return None
    return None


def _max_engine_temp(d: dict[str, Any], base: str) -> float | None:
    """多发飞机的温度取各发动机有效值的最大。

    温度键命名随机型不同：
      - 单发战斗机：无后缀，如 water_temperature；
      - 多发/轰炸机：每发一个仪表盘，带编号且为指针式命名，如
        water_temperature1_hour / water_temperature2_hour …（_hour 即完整读数，
        实测 B-52H 各发 599.99℃）。
    因此每个发动机编号都尝试「无后缀」和「_hour 后缀」两种键。
    不存在的发动机用占位值 -273.15(绝对零度)填充，需剔除。返回有效最大值；全无效则 None。
    """
    best: float | None = None
    for i in range(0, 9):  # 0 表示无编号；覆盖最多 8 发
        suffix = "" if i == 0 else str(i)
        for key in (f"{base}{suffix}", f"{base}{suffix}_hour"):
            v = _num(d, key)
            if v is None or v <= -200:  # 剔除 -273.15 占位
                continue
            if best is None or v > best:
                best = v
    return best


def _weapon_firing(d: dict[str, Any]) -> bool | None:
    """开火状态：扫描所有 weaponN 扳机键，任一 >0.5 视为开火中。

    不同机型把主/副武器映射到不同槽位（实测某喷气机用 weapon2/weapon4，无 weapon1/3），故不写死编号。
    陆/海战载具的 indicators 没有任何 weaponN 键 -> 返回 None（不适用），以区别于空军“没开火”的 False。
    """
    found = False
    firing = False
    for k, v in d.items():
        if k.startswith("weapon") and k[6:].isdigit():
            found = True
            try:
                if float(v) > 0.5:
                    firing = True
                    break
            except (TypeError, ValueError):
                continue
    return firing if found else None


def _parse_game_clock(d: dict[str, Any]) -> tuple[str | None, int | None]:
    """座舱时钟 -> ("HH:MM:SS", 当日秒数)。

    clock_hour 含小数（=小时 + 分钟/60，如 7.25 即 07:15），故只取其整数部分作小时，
    分秒分别用 clock_min / clock_sec。三者都缺时返回 (None, None)。
    """
    h = _num(d, "clock_hour")
    m = _num(d, "clock_min")
    s = _num(d, "clock_sec")
    if h is None and m is None and s is None:
        return None, None
    hh = int(h) % 24 if h is not None else 0
    mm = int(m) % 60 if m is not None else 0
    ss = int(s) % 60 if s is not None else 0
    return f"{hh:02d}:{mm:02d}:{ss:02d}", hh * 3600 + mm * 60 + ss


def _gear_state_from_lamps(d: dict[str, Any]) -> str | None:
    """三盏起落架指示灯 -> 离散状态。

    gear_lamp_down=1 表示锁定放下；gear_lamp_up=1 锁定收起；gear_lamp_off=1 运动中(红灯)。
    固定起落架机型一般无此三键 -> 返回 None。
    """
    down = _num(d, "gear_lamp_down")
    up = _num(d, "gear_lamp_up")
    off = _num(d, "gear_lamp_off")
    if down is not None and down > 0.5:
        return "down"
    if up is not None and up > 0.5:
        return "up"
    if off is not None and off > 0.5:
        return "moving"
    return None


def _pair(value: Any) -> tuple[float, float] | None:
    """把形如 [a, b] 的列表安全转成二元组。"""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _parse_engines(state: dict[str, Any]) -> list[EngineState]:
    """从 /state 中按编号提取每台发动机的数据。"""
    # 找出最大发动机编号：键名形如 "throttle 1, %"、"RPM 2" ...
    max_idx = 0
    for key in state:
        for prefix in ("throttle ", "RPM ", "power ", "thrust "):
            if key.startswith(prefix):
                token = key[len(prefix):].split(",")[0].strip()
                if token.isdigit():
                    max_idx = max(max_idx, int(token))
    engines: list[EngineState] = []
    for i in range(1, max_idx + 1):
        engines.append(
            EngineState(
                index=i,
                throttle_pct=_num(state, f"throttle {i}, %"),
                power_hp=_num(state, f"power {i}, hp"),
                rpm=_num(state, f"RPM {i}"),
                manifold_pressure_atm=_num(state, f"manifold pressure {i}, atm"),
                oil_temp_c=_num(state, f"oil temp {i}, C"),
                thrust_kgs=_num(state, f"thrust {i}, kgs"),
                efficiency_pct=_num(state, f"efficiency {i}, %"),
            )
        )
    return engines


def _classify_faction(icon: str, rgb: tuple[int, int, int]) -> str:
    """根据图标和颜色推断阵营：self / ally / enemy / other。"""
    if icon == "Player":
        return "self"
    r, g, b = rgb
    # 战雷约定：自己=黄、敌方=红、友方=蓝（队伍色）。
    if r > 180 and g < 130 and b < 130:
        return "enemy"
    if b > 170 and r < 130:
        return "ally"
    if r > 200 and g > 170 and b < 120:
        return "self"  # 黄色（自己/友军单位常见色）
    return "other"


def _parse_map_objects(items: Any) -> list[MapObject]:
    """解析 /map_obj.json 数组；非列表或空时返回空列表。"""
    if not isinstance(items, list):
        return []
    objects: list[MapObject] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rgb_raw = it.get("color[]") or [0, 0, 0]
        try:
            rgb = (int(rgb_raw[0]), int(rgb_raw[1]), int(rgb_raw[2]))
        except (TypeError, ValueError, IndexError):
            rgb = (0, 0, 0)
        icon = it.get("icon", "none")
        objects.append(
            MapObject(
                type=it.get("type", "unknown"),
                icon=icon,
                faction=_classify_faction(icon, rgb),
                color_hex=it.get("color", ""),
                color_rgb=rgb,
                blink=int(it.get("blink", 0) or 0),
                x=_num(it, "x"),
                y=_num(it, "y"),
                dx=_num(it, "dx"),
                dy=_num(it, "dy"),
                sx=_num(it, "sx"),
                sy=_num(it, "sy"),
                ex=_num(it, "ex"),
                ey=_num(it, "ey"),
            )
        )
    return objects


# ---------------------------------------------------------------------------
# 采集客户端
# ---------------------------------------------------------------------------


class WarThunderClient:
    """战雷 8111 遥测客户端。

    用法::

        client = WarThunderClient()
        snap = client.poll()
        if snap.in_battle:
            print(snap.vehicle.altitude_m, len(snap.enemies))
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        # hudmsg / gamechat 是增量接口，记录已读到的最大 id
        self._last_evt = 0
        self._last_dmg = 0
        self._last_chat = 0
        # 已保存过的地图版本号，用于避免重复保存同一张小地图
        self._last_map_gen: int | None = None

    # -- 底层请求 ----------------------------------------------------------

    def _fetch(self, path: str) -> tuple[bool, Any]:
        """请求一个接口。

        返回 (connected, data)：
            connected=False -> 端口连不上（游戏没开 / 网络拒绝 / 超时）
            connected=True  -> data 是解析后的 JSON；解析失败时 data=None
        """
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
            return False, None
        except Exception:
            return False, None

        if not raw:
            return True, None
        try:
            return True, json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return True, None

    def _fetch_bytes(self, path: str) -> tuple[bool, bytes | None]:
        """请求一个返回二进制内容的接口（如 /map.img）。

        返回 (connected, data)：连不上返回 (False, None)，连上但无内容返回 (True, None)。
        """
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = resp.read()
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
            return False, None
        except Exception:
            return False, None
        return True, (data or None)

    # -- 单接口解析 --------------------------------------------------------

    def _parse_state(self, data: Any) -> VehicleState:
        if not isinstance(data, dict) or not data.get("valid", False):
            return VehicleState(valid=False, raw=data if isinstance(data, dict) else {})
        return VehicleState(
            valid=True,
            altitude_m=_num(data, "H, m"),
            tas_kmh=_num(data, "TAS, km/h"),
            ias_kmh=_num(data, "IAS, km/h"),
            mach=_num(data, "M"),
            aoa_deg=_num(data, "AoA, deg"),
            aos_deg=_num(data, "AoS, deg"),
            load_factor=_num(data, "Ny"),
            climb_ms=_num(data, "Vy, m/s"),
            roll_rate_dps=_num(data, "Wx, deg/s"),
            fuel_kg=_num(data, "Mfuel, kg"),
            fuel_full_kg=_num(data, "Mfuel0, kg"),
            aileron_pct=_num(data, "aileron, %"),
            elevator_pct=_num(data, "elevator, %"),
            rudder_pct=_num(data, "rudder, %"),
            airbrake_pct=_num(data, "airbrake, %"),
            flaps_pct=_num(data, "flaps, %"),
            gear_pct=_num(data, "gear, %"),
            engines=_parse_engines(data),
            raw=data,
        )

    def _parse_indicators(self, data: Any) -> Indicators:
        if not isinstance(data, dict) or not data.get("valid", False):
            return Indicators(valid=False, raw=data if isinstance(data, dict) else {})
        # 机型代号规范化：坦克为 "tankModels/cn_al_khalid_1"，取最后一段统一成裸名
        raw_type = data.get("type")
        vtype = raw_type.split("/")[-1] if isinstance(raw_type, str) else raw_type
        army = data.get("army")
        # 直升机识别：army 同样是 "air"；实测 Ka-50 会暴露 gears=0.5 占位，
        # 因此不能用 gears 是否存在判定。以旋翼转速、无线电高度、无襟翼字段区分固定翼。
        is_heli = (
            army == "air"
            and "prop_rpm" in data
            and "radio_altitude" in data
            and "flaps" not in data
            and "flaps1" not in data
            and "flaps_indicator" not in data
        )
        # 喷气机识别：活塞机必有混合比/进气压(mixture/manifold_pressure)，喷气机没有。
        # 喷气机的 water_temperature 键实为涡轮/排气温度(量级数百度)，需与活塞水温区分，
        # 否则会被活塞水温阈值(~115℃)误判为过热。
        is_jet = (
            army == "air"
            and not is_heli
            and "mixture" not in data
            and "manifold_pressure" not in data
        )
        water_raw = _max_engine_temp(data, "water_temperature")
        head_raw = _max_engine_temp(data, "head_temperature")
        # 开火状态：扫描所有 weaponN 扳机键（槽位随机型不同，实测喷气机用 weapon2/weapon4）；
        # 陆/海战 indicators 无 weaponN 键 -> 返回 None（不适用，区别于“没开火”的 False）
        firing = _weapon_firing(data)
        # 游戏内时间：clock_hour 含小数(=时+分/60)，取整数小时 + clock_min/sec
        game_time, game_time_sec = _parse_game_clock(data)
        # 起落架：三盏指示灯 down/up/off
        gear_state = _gear_state_from_lamps(data)
        return Indicators(
            valid=True,
            army=army,
            vehicle_type=vtype,
            is_helicopter=is_heli,
            is_jet=is_jet,
            prop_rpm=_num(data, "prop_rpm"),
            radio_altitude=_num(data, "radio_altitude"),
            speed=_num(data, "speed"),
            heading_deg=_num(data, "compass"),
            roll_deg=_num(data, "aviahorizon_roll"),
            pitch_deg=_num(data, "aviahorizon_pitch"),
            bank_deg=_num(data, "bank"),
            vario_ms=_num(data, "vario"),
            g_meter=_num(data, "g_meter"),
            g_meter_min=_num(data, "g_meter_min"),
            g_meter_max=_num(data, "g_meter_max"),
            throttle=_num(data, "throttle"),
            flaps=_num(data, "flaps"),
            gears=_num(data, "gears"),
            airbrake=_num(data, "airbrake_lever"),
            gear=_num(data, "gear"),
            gear_neutral=_num(data, "gear_neutral"),
            rpm=_num(data, "rpm"),
            stabilizer=_num(data, "stabilizer"),
            first_stage_ammo=_num(data, "first_stage_ammo"),
            crew_total=_num(data, "crew_total"),
            crew_current=_num(data, "crew_current"),
            cruise_control=_num(data, "cruise_control"),
            lws=_num(data, "lws"),
            ircm=_num(data, "ircm"),
            has_speed_warning=_num(data, "has_speed_warning"),
            gunner_state=_num(data, "gunner_state"),
            driver_state=_num(data, "driver_state"),
            water_temperature=None if is_jet else water_raw,
            head_temperature=None if is_jet else head_raw,
            turbine_temperature=water_raw if is_jet else None,
            oil_temperature=_max_engine_temp(data, "oil_temperature"),
            weapon_firing=firing,
            game_time=game_time,
            game_time_sec=game_time_sec,
            gear_state=gear_state,
            raw=data,
        )

    def _parse_map_info(self, data: Any) -> MapInfo:
        if not isinstance(data, dict) or not data.get("valid", False):
            return MapInfo(valid=False, raw=data if isinstance(data, dict) else {})
        gen = data.get("map_generation")
        return MapInfo(
            valid=True,
            map_generation=int(gen) if isinstance(gen, (int, float)) else None,
            grid_size=_pair(data.get("grid_size")),
            grid_steps=_pair(data.get("grid_steps")),
            grid_zero=_pair(data.get("grid_zero")),
            map_min=_pair(data.get("map_min")),
            map_max=_pair(data.get("map_max")),
            raw=data,
        )

    def _parse_hudmsg(self, data: Any) -> list[HudMessage]:
        if not isinstance(data, dict):
            return []
        out: list[HudMessage] = []
        for ev in data.get("events", []) or []:
            if not isinstance(ev, dict):
                continue
            eid = int(ev.get("id", 0) or 0)
            self._last_evt = max(self._last_evt, eid)
            out.append(
                HudMessage(
                    id=eid,
                    kind="event",
                    msg=ev.get("msg", ""),
                    mode=ev.get("mode", ""),
                    time=ev.get("time"),
                )
            )
        for dm in data.get("damage", []) or []:
            if not isinstance(dm, dict):
                continue
            did = int(dm.get("id", 0) or 0)
            self._last_dmg = max(self._last_dmg, did)
            out.append(
                HudMessage(
                    id=did,
                    kind="damage",
                    msg=dm.get("msg", ""),
                    sender=dm.get("sender", ""),
                    enemy=bool(dm.get("enemy", False)),
                    mode=dm.get("mode", ""),
                    time=dm.get("time"),
                )
            )
        return out

    # -- 细粒度采集（供分频轮询使用） --------------------------------------

    def get_indicators(self) -> tuple[ConnectionState, Indicators, MapInfo]:
        """拉取 /indicators + /map_info，作为状态探针。

        战局判定以 **map_info.valid** 为准：游戏主界面/机库同样通过 8111 吐数据，
        其 /indicators、/state 可能 valid（展示选中的载具）、/mission 可能 running，
        唯独 /map_info 必为 {"valid":false}。因此不能用 indicators/mission 判战局，
        必须以 map_info.valid 为唯一可靠标志（真实空/陆/海战局均填充 grid 参数）。

        返回 (状态, indicators, map_info)；map_info 一并返回供调用方缓存复用。
        """
        connected, data = self._fetch("/indicators")
        if not connected:
            return ConnectionState.OFFLINE, Indicators(valid=False), MapInfo(valid=False)
        ind = self._parse_indicators(data)
        minfo = self.get_map_info()
        state = (
            ConnectionState.IN_BATTLE if minfo.valid else ConnectionState.NOT_IN_BATTLE
        )
        return state, ind, minfo

    def get_state(self) -> VehicleState:
        """拉取 /state（载具仪表）。"""
        _, data = self._fetch("/state")
        return self._parse_state(data)

    def get_map_objects(self) -> list[MapObject]:
        """拉取 /map_obj.json（地图物体）。"""
        _, data = self._fetch("/map_obj.json")
        return _parse_map_objects(data)

    def get_map_info(self) -> MapInfo:
        """拉取 /map_info.json（坐标换算参数）。"""
        _, data = self._fetch("/map_info.json")
        return self._parse_map_info(data)

    def get_mission(self) -> tuple[str | None, Any]:
        """拉取 /mission.json，返回 (status, objectives)。"""
        _, data = self._fetch("/mission.json")
        if isinstance(data, dict):
            return data.get("status"), data.get("objectives")
        return None, None

    def get_hud(self) -> list[HudMessage]:
        """增量拉取 /hudmsg（击杀/受损事件），自动推进 lastEvt/lastDmg 游标。"""
        _, data = self._fetch(
            f"/hudmsg?lastEvt={self._last_evt}&lastDmg={self._last_dmg}"
        )
        return self._parse_hudmsg(data)

    def drain_hud(self) -> int:
        """排空当前 hudmsg 积压：把 lastEvt/lastDmg 游标推进到最新但丢弃事件。

        用途：8111 的 /hudmsg 是跨对局保留的滚动缓冲，且 id 不随换局归零。
        服务(重)启/重连后游标为 0，进入对局时首拉会把上一局残留整批返回，
        若直接喂给 KillTracker 会把别人/上一局的击杀阵亡错算进本局。
        进入对局时调用本方法先排空积压（这些都早于本局开始，必为旧事件），
        之后再正常增量拉取即可。返回被丢弃的事件条数（便于日志/记录）。
        """
        return len(self.get_hud())

    def get_chat(self) -> list[dict[str, Any]]:
        """增量拉取 /gamechat，自动推进 lastId 游标。"""
        _, data = self._fetch(f"/gamechat?lastId={self._last_chat}")
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for msg in data:
            if isinstance(msg, dict):
                self._last_chat = max(self._last_chat, int(msg.get("id", 0) or 0))
                out.append(msg)
        return out

    # -- 对外主方法 --------------------------------------------------------

    def poll(self, *, with_map: bool = True, with_hud: bool = True) -> Telemetry:
        """拉取一次完整快照并返回结构化的 Telemetry 变量。

        无论游戏是否在线、是否在战局，本方法都不会抛异常，
        而是通过 Telemetry.state 反映当前状态。
        """
        now = time.time()

        state, indicators, map_info = self.get_indicators()
        if state is ConnectionState.OFFLINE:
            return Telemetry(state=ConnectionState.OFFLINE, timestamp=now)
        if state is ConnectionState.NOT_IN_BATTLE:
            # 能连上但 map_info 无效 -> 在主界面 / 机库 / 菜单（非真实战局）
            return Telemetry(
                state=ConnectionState.NOT_IN_BATTLE,
                timestamp=now,
                indicators=indicators,
            )

        snap = Telemetry(
            state=ConnectionState.IN_BATTLE,
            timestamp=now,
            in_battle=True,
            vehicle=self.get_state(),
            indicators=indicators,
            map_info=map_info,
        )
        snap.mission_status, snap.mission_objectives = self.get_mission()
        if with_map:
            snap.map_objects = self.get_map_objects()
        if with_hud:
            snap.hud_events = self.get_hud()
            snap.chat = self.get_chat()
        return snap

    # -- 小地图底图 --------------------------------------------------------

    def fetch_map_image(self) -> tuple[bytes | None, str | None]:
        """获取小地图底图（/map.img）。

        返回 (data, ext)：
            - 离线 / 战局外 / 返回的不是有效图片时，返回 (None, None)。
            - 正常时 data 是图片字节，ext 是按魔数识别出的扩展名（jpg / png）。
        """
        connected, data = self._fetch_bytes("/map.img")
        if not connected or not data:
            return None, None
        ext = _detect_image_ext(data)
        if ext is None:
            # 战局外有时会返回空白 / 占位内容，不当作有效地图。
            return None, None
        return data, ext

    def save_map_image(
        self,
        directory: str = "maps",
        filename: str | None = None,
        only_if_changed: bool = True,
    ) -> str | None:
        """保存小地图底图到磁盘。

        参数：
            directory       保存目录，不存在会自动创建。
            filename        文件名；缺省时按地图版本号命名为 map_<gen>.<ext>。
            only_if_changed True 时，地图版本号未变化则跳过保存（避免重复写盘）。

        返回保存后的文件路径；离线 / 战局外 / 地图未变化 / 无有效图片时返回 None。
        """
        # 先用 map_info 判断是否在战局，并取地图版本号。
        _, info_data = self._fetch("/map_info.json")
        gen: int | None = None
        if isinstance(info_data, dict):
            if not info_data.get("valid", False):
                return None  # 战局外
            raw_gen = info_data.get("map_generation")
            gen = int(raw_gen) if isinstance(raw_gen, (int, float)) else None

        if only_if_changed and gen is not None and gen == self._last_map_gen:
            return None  # 地图没变，跳过

        data, ext = self.fetch_map_image()
        if data is None:
            return None

        os.makedirs(directory, exist_ok=True)
        if filename is None:
            filename = f"map_{gen}.{ext}" if gen is not None else f"map.{ext}"
        path = os.path.join(directory, filename)
        with open(path, "wb") as fh:
            fh.write(data)

        self._last_map_gen = gen
        return path

    # -- 坐标换算工具 ------------------------------------------------------

    @staticmethod
    def to_meters(
        nx: float, ny: float, map_info: MapInfo
    ) -> tuple[float, float] | None:
        """把 0~1 归一化坐标换算成真实米坐标，缺少 grid_size 时返回 None。"""
        if map_info.grid_size is None:
            return None
        return (nx * map_info.grid_size[0], ny * map_info.grid_size[1])


# ---------------------------------------------------------------------------
# 直接运行：实时打印快照，便于验证
# ---------------------------------------------------------------------------


def _format_snapshot(snap: Telemetry) -> str:
    if snap.state is ConnectionState.OFFLINE:
        return "[离线] 8111 端口连不上 —— 游戏未运行或本地服务未开启。"
    if snap.state is ConnectionState.NOT_IN_BATTLE:
        return "[战局外] 已连上 8111，但当前在机库 / 菜单 / 加载中，暂无有效遥测。"

    v = snap.vehicle
    ind = snap.indicators
    lines = [
        f"[战局中] 载具={ind.vehicle_type}  阵营={ind.army}",
        f"  高度={v.altitude_m} m  TAS={v.tas_kmh} km/h  IAS={v.ias_kmh} km/h  "
        f"M={v.mach}  过载Ny={v.load_factor}  爬升={v.climb_ms} m/s",
        f"  航向={ind.heading_deg}°  滚转={ind.roll_deg}°  俯仰={ind.pitch_deg}°  "
        f"油门={ind.throttle}  发动机数={len(v.engines)}",
        f"  任务状态={snap.mission_status}",
        f"  地图物体={len(snap.map_objects)}  敌方={len(snap.enemies)}  "
        f"友方={len(snap.allies)}  自己={'有' if snap.player else '无'}",
    ]
    if snap.hud_events:
        lines.append(f"  新HUD事件 {len(snap.hud_events)} 条：")
        for ev in snap.hud_events[:5]:
            lines.append(f"    - [{ev.kind}] {ev.msg or ev.sender}")
    if snap.chat:
        lines.append(f"  新聊天 {len(snap.chat)} 条")
    return "\n".join(lines)


def main() -> None:
    import argparse
    import sys

    # Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="战雷 8111 遥测实时监控")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interval", type=float, default=1.0, help="轮询间隔（秒）")
    parser.add_argument("--once", action="store_true", help="只拉取一次后退出")
    parser.add_argument(
        "--save-map",
        action="store_true",
        help="战局中自动保存小地图底图（地图变化时才写盘）",
    )
    parser.add_argument(
        "--map-dir", default="maps", help="小地图保存目录（默认 maps）"
    )
    args = parser.parse_args()

    client = WarThunderClient(host=args.host, port=args.port)
    print(f"开始轮询 {client.base_url} （Ctrl+C 退出）\n")
    try:
        while True:
            snap = client.poll()
            ts = time.strftime("%H:%M:%S", time.localtime(snap.timestamp))
            print(f"=== {ts} ===")
            print(_format_snapshot(snap))
            if args.save_map and snap.in_battle:
                saved = client.save_map_image(directory=args.map_dir)
                if saved:
                    print(f"  [地图] 已保存底图 -> {saved}")
            print()
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
