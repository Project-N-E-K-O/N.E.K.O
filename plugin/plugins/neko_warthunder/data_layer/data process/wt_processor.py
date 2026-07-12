"""战雷遥测自定义处理层。

把原始遥测（VehicleState + Indicators）加工成前端直接可用的“关键信息 / 告警”，
例如燃油告急、失速、攻角过大、高度过低、加力时间过长等。

设计要点：
    - 阈值按机型配置：从 vehicle_profiles.json 读取，先 _default 再用机型条目覆盖。
      匹配三级（命中即止）：精确机型(source=exact) -> 模糊家族(source=family，仅空军，按
      _families 前缀最长优先) -> 回退默认(source=default)。模糊层用于覆盖未逐一录入的变体
      （如 Fw 190 / Bf 109 各亚型按家族大类套阈值），profile_matched 在精确或家族命中时均为 true。
    - 处理器是有状态的（按时间累计加力时长、估算燃油消耗率），
      因此应由“同一个线程、按固定频率”调用（见 wt_server 的 fast 组）。
      切换载具或离开战局时调用 reset() 清空会话状态。

数据来源与局限：
    - 燃油：state 的 Mfuel / Mfuel0，可算比例并估算剩余时间。
    - 失速：state 的 IAS。
    - 攻角：state 的 AoA。
    - 高度：state 的 H —— 注意是海拔(MSL)，不是离地高度(AGL)，8111 不提供地形高度。
    - 加力：8111 不提供“加力剩余时间”，这里用 indicators.throttle 判定加力状态并自行累计时长，
      配合机型配置的 afterburner_max_sec 近似判断。喷气机在游戏内通常无加力时间限制。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_PROFILES_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "vehicle_profiles.json"
)

# 告警等级（数值用于比较取最高）
LEVEL_ORDER = {"info": 1, "warning": 2, "critical": 3}

# indicators.army -> 载具大类。飞行类告警仅对 air 生效，避免对坦克/海军误报。
_ARMY_TO_CLASS = {
    "air": "air",
    "tank": "ground",
    "ship": "naval",
    "fleet": "naval",
}


@dataclass
class Alert:
    """单条告警。"""

    code: str        # 机器可读代码，如 fuel_critical
    level: str       # info / warning / critical
    message: str     # 给人看的中文描述
    value: float | None = None  # 触发时的相关数值


@dataclass
class ProcessedData:
    """加工后的关键信息，连同原始数据一起送往前端。"""

    vehicle_type: str | None = None
    profile_matched: bool = False  # 是否套用到了机型适配阈值（精确或家族模糊命中均为 true）
    profile_source: str = "default"  # exact=精确命中 / family=模糊家族命中 / default=回退默认
    profile_family: str | None = None  # 模糊命中的家族标签（如 "Fw 190"），仅 source=family 时有值
    army: str | None = None
    vehicle_class: str = "unknown"  # air / ground / naval / unknown

    # 派生量
    fuel_fraction: float | None = None       # 剩余燃油比例 0~1
    fuel_kg: float | None = None
    fuel_burn_rate_kgs: float | None = None   # 估算的瞬时油耗 kg/s
    fuel_remaining_sec: float | None = None   # 估算的剩余飞行时间（秒）
    afterburner_active: bool = False
    afterburner_elapsed_sec: float = 0.0
    afterburner_max_sec: float | None = None
    ias_kmh: float | None = None
    aoa_deg: float | None = None
    altitude_m: float | None = None
    # 过载（基于 Ny，本架次峰值跟踪，适用所有飞机）
    g_now: float | None = None
    g_max: float | None = None
    g_min: float | None = None
    # 地面载具(坦克)派生
    crew_current: float | None = None
    crew_total: float | None = None
    ammo_first_stage: float | None = None
    gun_stabilizer: bool | None = None
    gear_position: int | None = None  # 档位：>0前进档 / 0空挡 / <0倒车档
    gunner_state: float | None = None
    driver_state: float | None = None
    # 直升机派生
    rotor_rpm: float | None = None
    radio_altitude_m: float | None = None
    vario_ms: float | None = None
    # 发动机温度
    water_temp_c: float | None = None    # 水温 ℃（液冷活塞）
    head_temp_c: float | None = None     # 缸头温度 ℃（气冷活塞）
    turbine_temp_c: float | None = None  # 涡轮/排气温度 ℃（喷气）
    oil_temp_c: float | None = None      # 油温 ℃

    alerts: list[Alert] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)  # 各告警代码 -> 是否触发
    level: str = "info"  # 所有告警里的最高等级

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact_name(s: str) -> str:
    """归一化载具代号用于家族前缀比对：转小写、去掉所有分隔符/非字母数字。

    例：'fw-190a-5'->'fw190a5'、'ki_61_1a_otsu_china'->'ki611aotsuchina'、'F-4EJ_Kai'->'f4ejkai'。
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _apply_entry(base: dict[str, Any], profiles: dict[str, Any],
                 entry: dict[str, Any], skip: set[str]) -> None:
    """把一条配置(精确机型或家族)按 class 模板 + 自身字段合并进 base。"""
    cls = entry.get("class")
    if cls:
        template = profiles.get("_classes", {}).get(cls)
        if isinstance(template, dict):
            base.update(template)
    base.update({k: v for k, v in entry.items() if k not in skip})


def _match_family(family_rules: list[dict[str, Any]], compact: str) -> dict[str, Any] | None:
    """模糊家族匹配：family_rules 已按前缀长度【降序】排好，最长前缀优先。

    最长优先是消歧关键：可正确区分 'f4u'(海盗,螺旋桨) 与 'f4'(鬼怪,现代喷气)、
    'ki100' 与 'ki10'、'yak15'(喷气) 与 'yak1'(螺旋桨) 等“短前缀是长前缀子串”的嵌套情形——
    只要更具体的家族也在表中，就一定先于较短的家族被命中。
    """
    if not compact:
        return None
    for fam in family_rules:
        pref = fam.get("_cprefix")
        if pref and compact.startswith(pref):
            return fam
    return None


def _merge_profile(
    profiles: dict[str, Any],
    vehicle_type: str | None,
    army: str | None,
    family_rules: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool, str, str | None]:
    """合并出某机型的有效阈值配置。

    返回 (配置, profile_matched, profile_source, profile_family)。
    匹配顺序（命中即止）：
        1) 精确命中 profiles[vehicle_type]            -> source='exact'
        2) 模糊家族命中（仅空军）_families 前缀匹配     -> source='family'
        3) 回退 _default                              -> source='default'
    合并优先级（后者覆盖前者）：_default < 类别模板(_classes[class]) < 该条目自身字段。

    模糊层动机：机型变体众多难以逐一录入，但同一家族(如 Fw 190 / Bf 109 各亚型)的失速/超速/
    高度等阈值差异不大，可按家族大类套用。仅对空军启用——家族阈值都是固定翼类别，套到误判的
    地面/海面载具上没有意义，故用 army 守卫降低误判面。
    """
    base = dict(profiles.get("_default", {}))
    if not (vehicle_type and not vehicle_type.startswith("_")):
        return base, False, "default", None
    entry = profiles.get(vehicle_type)
    if isinstance(entry, dict):
        _apply_entry(base, profiles, entry, skip={"class"})
        return base, True, "exact", None
    if army == "air":
        fam = _match_family(family_rules, _compact_name(vehicle_type))
        if fam is not None:
            _apply_entry(base, profiles, fam,
                         skip={"class", "prefix", "label", "_cprefix"})
            return base, True, "family", fam.get("label") or fam.get("prefix")
    return base, False, "default", None


class TelemetryProcessor:
    """把原始遥测加工成关键信息 / 告警。"""

    def __init__(self, profiles_path: str | None = None) -> None:
        self.profiles_path = profiles_path or DEFAULT_PROFILES_FILE
        self.profiles: dict[str, Any] = {}
        self.load_profiles()
        self.reset()

    # -- 配置 --------------------------------------------------------------

    def load_profiles(self) -> None:
        """加载机型配置文件；失败时退化为内置最小默认值。"""
        try:
            with open(self.profiles_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("_default"), dict):
                self.profiles = data
                self._build_family_rules()
                return
        except (OSError, json.JSONDecodeError):
            pass
        # 兜底默认值（配置文件缺失/损坏时仍可工作）
        self.profiles = {
            "_default": {
                "fuel_low_fraction": 0.30,
                "fuel_critical_fraction": 0.15,
                "stall_warn_kmh": 300,
                "stall_critical_kmh": 250,
                "overspeed_warn_kmh": 750,
                "overspeed_critical_kmh": 850,
                "aoa_warn_deg": 14,
                "aoa_critical_deg": 18,
                "altitude_warn_m": 200,
                "altitude_critical_m": 100,
                "afterburner_throttle_threshold": 1.0,
                "afterburner_max_sec": 0,
                "suppress_when_gear_down": True,
            }
        }
        self._build_family_rules()

    def _build_family_rules(self) -> None:
        """从 profiles['_families'] 预构建模糊家族规则表：compact 化前缀并按长度降序排序。

        长度降序在加载期一次排好，匹配时按序首中即返回 -> 自动满足“最长前缀优先”消歧，
        作者在 JSON 里的排列顺序无关紧要。"""
        rules: list[dict[str, Any]] = []
        fams = self.profiles.get("_families")
        if isinstance(fams, list):
            for f in fams:
                if isinstance(f, dict) and f.get("prefix"):
                    rule = dict(f)
                    rule["_cprefix"] = _compact_name(str(f["prefix"]))
                    if rule["_cprefix"]:
                        rules.append(rule)
        rules.sort(key=lambda r: len(r["_cprefix"]), reverse=True)
        self._family_rules: list[dict[str, Any]] = rules

    # -- 会话状态 ----------------------------------------------------------

    def reset(self) -> None:
        """清空跨调用的会话状态（切换载具 / 离开战局时调用）。"""
        self._cur_type: str | None = None
        self._cur_class: str | None = None
        self._last_ts: float | None = None
        self._last_fuel: float | None = None      # 上次“燃油发生变化”时的油量
        self._last_fuel_ts: float | None = None    # 对应时间戳
        self._fuel_rate: float | None = None       # kg/s 的指数滑动平均
        self._ab_active = False
        self._ab_elapsed = 0.0
        self._g_max: float | None = None
        self._g_min: float | None = None
        self._reset_ammo_tracking()

    def _reset_ammo_tracking(self) -> None:
        """清空本次出生的一级弹药基线。"""
        self._ammo_max: float | None = None        # 本次出生见过的最大一级弹（满弹估计）
        self._ammo_last_valid: float | None = None
        self._ammo_baseline_seen = False
        self._ammo_empty_latched = False

    # -- 主处理 ------------------------------------------------------------

    def process(self, vehicle: Any, indicators: Any, timestamp: float) -> ProcessedData:
        """根据一帧数据产出 ProcessedData。vehicle/indicators 为对应 dataclass。"""
        vtype = getattr(indicators, "vehicle_type", None)
        army = getattr(indicators, "army", None)
        is_heli = getattr(indicators, "is_helicopter", False)
        vehicle_class = _ARMY_TO_CLASS.get(army or "", "unknown")
        if is_heli:  # army 同为 air，但直升机单独归类，避免套用固定翼告警
            vehicle_class = "heli"

        # 换载具或类别 -> 重置会话状态（避免残留字段让跨域油耗/加力计时串味）
        if vtype != self._cur_type or vehicle_class != self._cur_class:
            self.reset()
            self._cur_type = vtype
            self._cur_class = vehicle_class

        cfg, matched, source, family = _merge_profile(
            self.profiles, vtype, army, self._family_rules
        )

        dt = 0.0
        if self._last_ts is not None:
            dt = max(0.0, timestamp - self._last_ts)
        self._last_ts = timestamp

        result = ProcessedData(
            vehicle_type=vtype,
            profile_matched=matched,
            profile_source=source,
            profile_family=family,
            army=army,
            vehicle_class=vehicle_class,
        )

        if vehicle_class == "heli":
            # 直升机：燃油几乎用不完、过载拉不大、无加力，均禁用；
            # 失速/攻角/高度为固定翼概念不适用；改用涡环状态(VRS)告警。
            self._process_helicopter(vehicle, indicators, cfg, result)
        elif vehicle_class == "air":
            result.fuel_kg = getattr(vehicle, "fuel_kg", None)
            result.ias_kmh = getattr(vehicle, "ias_kmh", None)
            result.aoa_deg = getattr(vehicle, "aoa_deg", None)
            result.altitude_m = getattr(vehicle, "altitude_m", None)
            result.radio_altitude_m = getattr(indicators, "radio_altitude", None)
            result.afterburner_max_sec = cfg.get("afterburner_max_sec")

            self._process_fuel(vehicle, cfg, timestamp, result)
            self._process_afterburner(indicators, cfg, dt, result)
            self._process_gforce(vehicle, cfg, result, enable_alerts=True)
            self._process_engine_temp(indicators, cfg, result)
            # 超速不受起落架抑制（放起落架/襟翼时更易超速撕裂，反而更危险）
            self._process_overspeed(vehicle, cfg, result)
            # 起落架放下 = 起降构型（低空低速是有意为之），抑制失速/迎角/低高度告警，
            # 避免着陆补给/起飞滑跑时刷假警。优先用可靠的 gear_state（指示灯归并），
            # 因为部分机型（实测 J-15T）原始 gears 恒为 0.5，> 0.5 判据永不成立。
            gear_state = getattr(indicators, "gear_state", None)
            gears = getattr(indicators, "gears", None)
            gear_down = bool(cfg.get("suppress_when_gear_down")) and (
                gear_state == "down"
                or (gear_state is None and gears is not None and gears > 0.5)
            )
            if not gear_down:
                self._process_stall(vehicle, cfg, result)
                self._process_aoa(vehicle, cfg, result)
                self._process_altitude(vehicle, cfg, result)
        elif vehicle_class == "ground":
            self._process_ground(indicators, cfg, result)

        # 计算最高等级
        for a in result.alerts:
            if LEVEL_ORDER.get(a.level, 0) > LEVEL_ORDER.get(result.level, 0):
                result.level = a.level
        return result

    # -- 各项规则 ----------------------------------------------------------

    def _add(self, result: ProcessedData, code: str, level: str, msg: str, value: float | None) -> None:
        result.alerts.append(Alert(code=code, level=level, message=msg, value=value))
        result.flags[code] = True

    def _process_fuel(self, vehicle: Any, cfg: dict[str, Any], timestamp: float, result: ProcessedData) -> None:
        fuel = getattr(vehicle, "fuel_kg", None)
        full = getattr(vehicle, "fuel_full_kg", None)
        if fuel is None or not full:
            return
        frac = fuel / full if full > 0 else None
        result.fuel_fraction = round(frac, 4) if frac is not None else None

        # 估算油耗率：燃油是整数 kg，高频采样下大多帧不变，
        # 因此只在“燃油实际下降”时基于上次变化点计算速率（与采样频率解耦）。
        if self._last_fuel is None:
            self._last_fuel = fuel
            self._last_fuel_ts = timestamp
        elif fuel < self._last_fuel:
            burned = self._last_fuel - fuel
            base_ts = self._last_fuel_ts if self._last_fuel_ts is not None else timestamp
            dt = timestamp - base_ts
            if dt > 0 and burned < full:
                inst_rate = burned / dt
                if self._fuel_rate is None:
                    self._fuel_rate = inst_rate
                else:
                    self._fuel_rate = 0.8 * self._fuel_rate + 0.2 * inst_rate
            self._last_fuel = fuel
            self._last_fuel_ts = timestamp
        elif fuel > self._last_fuel:  # 补给/加油 -> 重置速率估计
            self._fuel_rate = None
            self._last_fuel = fuel
            self._last_fuel_ts = timestamp

        if self._fuel_rate and self._fuel_rate > 1e-4:
            result.fuel_burn_rate_kgs = round(self._fuel_rate, 4)
            result.fuel_remaining_sec = round(fuel / self._fuel_rate, 1)

        if frac is not None:
            rem = result.fuel_remaining_sec
            crit = cfg.get("fuel_critical_fraction")
            low = cfg.get("fuel_low_fraction")
            if crit is not None and frac <= crit:
                msg = f"燃油告急：仅剩 {frac*100:.0f}%"
                if rem is not None:
                    msg += f"，约 {rem/60:.0f} 分 {rem%60:.0f} 秒"
                self._add(result, "fuel_critical", "critical", msg, round(frac, 4))
            elif low is not None and frac <= low:
                msg = f"燃油偏低：剩余 {frac*100:.0f}%"
                if rem is not None:
                    msg += f"，约 {rem/60:.0f} 分 {rem%60:.0f} 秒"
                self._add(result, "fuel_low", "warning", msg, round(frac, 4))

    def _process_engine_temp(self, indicators: Any, cfg: dict[str, Any], result: ProcessedData) -> None:
        """发动机过热告警（活塞机/部分早期喷气）。

        主过热指标随发动机类型不同：
          - 液冷活塞机：看水温 water_temperature（约 100~120℃）；
          - 气冷活塞机：无水温，看缸头温度 head_temperature（约 200~260℃），过热常以油温为主；
          - 喷气机：看涡轮/排气温度 turbine_temperature（约数百℃，满功率即可很高），
            数据层已把它从 water_temperature 键拆出，避免被活塞水温阈值误判。
        长时间满油门会持续升温，越线后游戏开始损坏发动机（降功率甚至熄火）。
        阈值随机型差异较大，由 vehicle_profiles 配置。
        """
        water = getattr(indicators, "water_temperature", None)
        head = getattr(indicators, "head_temperature", None)
        turbine = getattr(indicators, "turbine_temperature", None)
        oil = getattr(indicators, "oil_temperature", None)
        result.water_temp_c = round(water, 1) if water is not None else None
        result.head_temp_c = round(head, 1) if head is not None else None
        result.turbine_temp_c = round(turbine, 1) if turbine is not None else None
        result.oil_temp_c = round(oil, 1) if oil is not None else None

        if turbine is not None:
            tc = cfg.get("turbine_temp_critical_c")
            tw = cfg.get("turbine_temp_warn_c")
            if tc is not None and turbine >= tc:
                self._add(result, "engine_overheat_critical", "critical",
                          f"发动机过热：涡轮 {turbine:.0f}℃，即将损坏", round(turbine, 1))
            elif tw is not None and turbine >= tw:
                self._add(result, "engine_overheat", "warning",
                          f"涡轮温度偏高：{turbine:.0f}℃", round(turbine, 1))

        if water is not None:
            wc = cfg.get("water_temp_critical_c")
            ww = cfg.get("water_temp_warn_c")
            if wc is not None and water >= wc:
                self._add(result, "engine_overheat_critical", "critical",
                          f"发动机过热：水温 {water:.0f}℃，即将损坏", round(water, 1))
            elif ww is not None and water >= ww:
                self._add(result, "engine_overheat", "warning",
                          f"水温偏高：{water:.0f}℃", round(water, 1))

        if head is not None:
            hc = cfg.get("head_temp_critical_c")
            hw = cfg.get("head_temp_warn_c")
            if hc is not None and head >= hc:
                self._add(result, "engine_overheat_critical", "critical",
                          f"发动机过热：缸头 {head:.0f}℃，即将损坏", round(head, 1))
            elif hw is not None and head >= hw:
                self._add(result, "engine_overheat", "warning",
                          f"缸头温度偏高：{head:.0f}℃", round(head, 1))

        if oil is not None:
            oc = cfg.get("oil_temp_critical_c")
            ow = cfg.get("oil_temp_warn_c")
            if oc is not None and oil >= oc:
                self._add(result, "oil_overheat_critical", "critical",
                          f"机油过热：油温 {oil:.0f}℃", round(oil, 1))
            elif ow is not None and oil >= ow:
                self._add(result, "oil_overheat", "warning",
                          f"油温偏高：{oil:.0f}℃", round(oil, 1))

    def _process_afterburner(self, indicators: Any, cfg: dict[str, Any], dt: float, result: ProcessedData) -> None:
        thr = getattr(indicators, "throttle", None)
        threshold = cfg.get("afterburner_throttle_threshold", 1.0)
        active = thr is not None and thr > threshold

        if active:
            self._ab_elapsed = (self._ab_elapsed + dt) if self._ab_active else 0.0
            self._ab_active = True
        else:
            self._ab_active = False
            self._ab_elapsed = 0.0

        result.afterburner_active = self._ab_active
        result.afterburner_elapsed_sec = round(self._ab_elapsed, 1)

        max_sec = cfg.get("afterburner_max_sec") or 0
        if active and max_sec > 0:
            if self._ab_elapsed >= max_sec:
                self._add(result, "afterburner_overrun", "critical",
                          f"加力超时：已持续 {self._ab_elapsed:.0f}s（上限 {max_sec:.0f}s）",
                          round(self._ab_elapsed, 1))
            elif self._ab_elapsed >= 0.8 * max_sec:
                self._add(result, "afterburner_warning", "warning",
                          f"加力时间将达上限：{self._ab_elapsed:.0f}/{max_sec:.0f}s",
                          round(self._ab_elapsed, 1))

    def _process_helicopter(
        self, vehicle: Any, indicators: Any, cfg: dict[str, Any], result: ProcessedData
    ) -> None:
        """直升机：不套用固定翼失速/攻角/高度告警；透传旋翼转速等供前端展示。

        实测（z_11wa）表明旋翼转速会随机动大幅波动并超转（悬停约 396、机动峰值
        达 506），且与发动机转速固定绑定、有调速器维持，无法用静态比例阈值可靠
        判断“危险掉转速”——任何固定阈值都会在正常机动时误报。故此处不做旋翼
        告警，仅透传 rotor_rpm / radio_altitude 字段；燃油、过载等通用告警在
        本方法之外照常处理。
        """
        result.rotor_rpm = getattr(indicators, "prop_rpm", None)
        result.radio_altitude_m = getattr(indicators, "radio_altitude", None)

        # 涡环状态(VRS)告警：低速 + 明显垂直下降时，旋翼陷入自身下洗气流，
        # 升力骤降且越拉总距越糟。判据：水平速度很低 且 下降率超过阈值。
        speed = getattr(indicators, "speed", None)          # indicators.speed 单位为 m/s
        vario = getattr(indicators, "vario_ms", None)        # 垂直速度 m/s（负=下降）
        result.vario_ms = vario
        speed_kmh = abs(speed) * 3.6 if speed is not None else None
        if speed_kmh is not None and vario is not None:
            vrs_speed = cfg.get("vrs_speed_kmh", 40)
            vrs_desc = cfg.get("vrs_descent_ms", 5)
            vrs_desc_crit = cfg.get("vrs_descent_critical_ms", 10)
            low_speed = speed_kmh < vrs_speed
            if low_speed and vario <= -vrs_desc_crit:
                self._add(result, "vrs_critical", "critical",
                          f"涡环状态：低速{speed_kmh:.0f}km/h且急速下降{-vario:.0f}m/s，旋翼失力", vario)
            elif low_speed and vario <= -vrs_desc:
                self._add(result, "vrs_warning", "warning",
                          f"涡环风险：低速{speed_kmh:.0f}km/h且下降{-vario:.0f}m/s", vario)

    def _process_ground(self, indicators: Any, cfg: dict[str, Any], result: ProcessedData) -> None:
        """地面载具(坦克)告警：乘员阵亡 / 一级弹药耗尽 / 被激光照射。"""
        crew_cur = getattr(indicators, "crew_current", None)
        crew_tot = getattr(indicators, "crew_total", None)
        ammo = getattr(indicators, "first_stage_ammo", None)
        stab = getattr(indicators, "stabilizer", None)
        lws = getattr(indicators, "lws", None)
        gunner_state = getattr(indicators, "gunner_state", None)
        driver_state = getattr(indicators, "driver_state", None)

        result.crew_current = crew_cur
        result.crew_total = crew_tot
        result.ammo_first_stage = ammo
        result.gun_stabilizer = (stab is not None and stab >= 0.5)
        result.gunner_state = gunner_state
        result.driver_state = driver_state

        # 档位：gear - gear_neutral（>0前进 / 0空挡 / <0倒车）
        gear = getattr(indicators, "gear", None)
        neutral = getattr(indicators, "gear_neutral", None)
        if gear is not None and neutral is not None:
            result.gear_position = int(round(gear - neutral))

        # 乘员阵亡
        if crew_cur is not None and crew_tot:
            if crew_cur <= 1 and crew_tot > 1:
                self._add(result, "crew_critical", "critical",
                          f"乘员仅剩 {crew_cur:.0f}/{crew_tot:.0f}，濒临阵亡", crew_cur)
            elif crew_cur < crew_tot:
                self._add(result, "crew_loss", "warning",
                          f"乘员阵亡 {crew_tot - crew_cur:.0f} 人（{crew_cur:.0f}/{crew_tot:.0f}）", crew_cur)

        # 8111 岗位状态不是布尔值：0=正常，1=无人补位，2=正在补位；3 的语义尚未确认。
        # 1/2 期间岗位均暂不可用，未知值只保留原始数值，不派生告警。
        if gunner_state in (1, 2):
            message = "炮手正在补位，暂时无法开火" if gunner_state == 2 else "炮手失能，暂无乘员补位"
            self._add(result, "gunner_disabled", "warning", message, gunner_state)
        if driver_state in (1, 2):
            message = "驾驶员正在补位，暂时无法机动" if driver_state == 2 else "驾驶员失能，暂无乘员补位"
            self._add(result, "driver_disabled", "warning", message, driver_state)

        # 一级弹药（炮塔待发弹仓）：打空后再开火需从备弹长装填。
        # 各车满弹量不同，用本架次见过的最大值作满弹估计，按比例自适应告警。
        if ammo is None or ammo < 0:
            # -1/缺失是不可用哨兵值。丢失有效性后必须重新观察正数基线。
            self._reset_ammo_tracking()
        elif ammo > 0:
            self._ammo_baseline_seen = True
            self._ammo_empty_latched = False
            if self._ammo_max is None or ammo > self._ammo_max:
                self._ammo_max = ammo
            ratio = cfg.get("ammo_low_ratio", 0.3)
            low_thr = (self._ammo_max or 0) * ratio
            if self._ammo_max and self._ammo_max > 3 and ammo <= low_thr:
                self._add(result, "ammo_low", "info",
                          f"一级弹药偏少：剩 {ammo:.0f}/{self._ammo_max:.0f} 发", ammo)
            self._ammo_last_valid = ammo
        else:
            # 只有本次出生先见过正数，并明确从正数降到 0，才锁存“耗尽”。
            if self._ammo_baseline_seen and self._ammo_last_valid is not None and self._ammo_last_valid > 0:
                self._ammo_empty_latched = True
            if self._ammo_empty_latched:
                self._add(result, "ammo_empty", "warning",
                          "一级弹药耗尽，装填变慢", ammo)
            self._ammo_last_valid = ammo

        # LWS：-1=无设备，0=待机，1=正在告警，2=设备损坏。
        if lws == 1 and cfg.get("laser_warning_enable", True):
            self._add(result, "laser_warning", "critical",
                      "遭激光照射（可能被锁定/测距）", lws)

    def _process_gforce(
        self, vehicle: Any, cfg: dict[str, Any], result: ProcessedData, *, enable_alerts: bool
    ) -> None:
        g = getattr(vehicle, "load_factor", None)
        if g is None:
            return
        self._g_max = g if self._g_max is None else max(self._g_max, g)
        self._g_min = g if self._g_min is None else min(self._g_min, g)
        result.g_now = round(g, 2)
        result.g_max = round(self._g_max, 2)
        result.g_min = round(self._g_min, 2)
        if not enable_alerts:
            return

        def _num(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return abs(float(value))
            except (TypeError, ValueError):
                return None

        def _structure_candidate_limit(empty_key: str, full_key: str) -> float | None:
            empty = _num(cfg.get(empty_key))
            full = _num(cfg.get(full_key))
            if empty is not None and full is not None:
                frac = result.fuel_fraction
                if frac is None:
                    return min(empty, full)
                frac = max(0.0, min(1.0, frac))
                return empty + (full - empty) * frac
            if empty is not None:
                return empty
            if full is not None:
                return full
            return None

        def _spoken_limit(empty_key: str, full_key: str, instructor_key: str) -> float | None:
            instructor_limit = _num(cfg.get(instructor_key))
            if instructor_limit is not None:
                return instructor_limit
            return _structure_candidate_limit(empty_key, full_key)

        pos_limit = _spoken_limit(
            "g_limit_positive_empty_candidate",
            "g_limit_positive_full_fuel_candidate",
            "instructor_g_limit_positive",
        )
        neg_limit = _spoken_limit(
            "g_limit_negative_empty_candidate",
            "g_limit_negative_full_fuel_candidate",
            "instructor_g_limit_negative",
        )
        warn_ratio = cfg.get("g_warn_ratio", 0.85)
        try:
            warn_ratio = float(warn_ratio)
        except (TypeError, ValueError):
            warn_ratio = 0.85
        warn_ratio = max(0.5, min(1.0, warn_ratio))

        if pos_limit is not None and g >= pos_limit:
            self._add(result, "over_g_critical", "critical",
                      f"过载过大：{g:.1f}G，松杆别硬拉", round(g, 2))
        elif pos_limit is not None and g >= pos_limit * warn_ratio:
            self._add(result, "over_g", "warning",
                      f"过载偏大：{g:.1f}G", round(g, 2))
        elif neg_limit is not None and g <= -neg_limit:
            self._add(result, "over_g_critical", "critical",
                      f"负过载过大：{g:.1f}G，回正别反压", round(g, 2))
        elif neg_limit is not None and g <= -(neg_limit * warn_ratio):
            self._add(result, "over_g", "warning",
                      f"负过载偏大：{g:.1f}G", round(g, 2))

    def _process_overspeed(self, vehicle: Any, cfg: dict[str, Any], result: ProcessedData) -> None:
        """超速告警：IAS 超过结构限速，或（喷气机）马赫超过压缩限制。

        两个判据取「或」：任一超限即告警。IAS 阈值对所有飞机通用；马赫阈值仅在
        profile 配了 overspeed_*_mach 时才参与（一般给喷气机）。与失速不同，**放起落架/
        襟翼时更易超速撕裂**，故本告警不受 suppress_when_gear_down 抑制。
        """
        ias = getattr(vehicle, "ias_kmh", None)
        mach = getattr(vehicle, "mach", None)
        ias_crit = cfg.get("overspeed_critical_kmh")
        ias_warn = cfg.get("overspeed_warn_kmh")
        mach_crit = cfg.get("overspeed_critical_mach")
        mach_warn = cfg.get("overspeed_warn_mach")

        def _hit(ias_thr: Any, mach_thr: Any) -> bool:
            by_ias = ias is not None and ias_thr is not None and ias >= ias_thr
            by_mach = mach is not None and mach_thr is not None and mach >= mach_thr
            return bool(by_ias or by_mach)

        def _desc() -> tuple[str, float | None]:
            if ias is not None:
                return f"IAS {ias:.0f} km/h", ias
            if mach is not None:
                return f"M {mach:.2f}", mach
            return "", None

        if _hit(ias_crit, mach_crit):
            txt, val = _desc()
            self._add(result, "overspeed_critical", "critical",
                      f"速度过高，机体可能解体：{txt}", val)
        elif _hit(ias_warn, mach_warn):
            txt, val = _desc()
            self._add(result, "overspeed_warn", "warning",
                      f"速度偏高，注意结构限速：{txt}", val)

    def _process_stall(self, vehicle: Any, cfg: dict[str, Any], result: ProcessedData) -> None:
        ias = getattr(vehicle, "ias_kmh", None)
        if ias is None:
            return
        crit = cfg.get("stall_critical_kmh")
        warn = cfg.get("stall_warn_kmh")
        if crit is not None and ias <= crit:
            self._add(result, "stall_critical", "critical",
                      f"速度过低，濒临失速：IAS {ias:.0f} km/h", ias)
        elif warn is not None and ias <= warn:
            self._add(result, "stall_warning", "warning",
                      f"速度偏低：IAS {ias:.0f} km/h", ias)

    def _process_aoa(self, vehicle: Any, cfg: dict[str, Any], result: ProcessedData) -> None:
        aoa = getattr(vehicle, "aoa_deg", None)
        if aoa is None:
            return
        crit = cfg.get("aoa_critical_deg")
        warn = cfg.get("aoa_warn_deg")
        if crit is not None and aoa >= crit:
            self._add(result, "aoa_critical", "critical",
                      f"攻角过大：AoA {aoa:.1f}°", aoa)
        elif warn is not None and aoa >= warn:
            self._add(result, "aoa_high", "warning",
                      f"攻角偏大：AoA {aoa:.1f}°", aoa)

    def _process_altitude(self, vehicle: Any, cfg: dict[str, Any], result: ProcessedData) -> None:
        alt = result.radio_altitude_m
        if alt is None:
            alt = getattr(vehicle, "altitude_m", None)
        if alt is None:
            return
        crit = cfg.get("altitude_critical_m")
        warn = cfg.get("altitude_warn_m")
        label = "离地" if result.radio_altitude_m is not None else "海拔"
        if crit is not None and alt <= crit:
            self._add(result, "altitude_critical", "critical",
                      f"高度过低：{alt:.0f} m（{label}）", alt)
        elif warn is not None and alt <= warn:
            self._add(result, "altitude_low", "warning",
                      f"高度偏低：{alt:.0f} m（{label}）", alt)
