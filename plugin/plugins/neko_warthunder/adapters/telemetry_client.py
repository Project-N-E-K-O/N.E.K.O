"""数据层 HTTP 客户端：拉 :8112/api/telemetry → BattleState。

边界：与数据层唯一接口 = HTTP（见 data_layer/data process/后端接口文档.md）。
本模块**只读、只消费**，不重算阈值。轮询在插件的 timer 线程里跑（同步 urllib + 超时），
连不上/非战斗态时安全降级，绝不抛到 timer 循环外。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..core.contracts import BattleState


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_telemetry(base_url: str, timeout: float) -> dict[str, Any] | None:
    """GET /api/telemetry。任何失败（游戏没开/数据层没起/超时/坏 JSON）返回 None。"""
    url = f"{base_url.rstrip('/')}/api/telemetry"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def parse_telemetry(payload: dict[str, Any] | None) -> BattleState:
    """把一帧 /api/telemetry 归一化成 BattleState。payload=None → 离线态。"""
    if not isinstance(payload, dict):
        return BattleState(connected=False, conn_state="offline", in_battle=False)

    processed = payload.get("processed") if isinstance(payload.get("processed"), dict) else {}
    vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
    indicators = payload.get("indicators") if isinstance(payload.get("indicators"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    hud_notices = payload.get("hud_notices") if isinstance(payload.get("hud_notices"), dict) else {}
    proximity = payload.get("proximity") if isinstance(payload.get("proximity"), dict) else {}
    situation = payload.get("situation") if isinstance(payload.get("situation"), dict) else {}
    flags_raw = processed.get("flags") if isinstance(processed.get("flags"), dict) else {}

    fast_meta = meta.get("fast") if isinstance(meta.get("fast"), dict) else {}

    state = BattleState(
        connected=True,
        conn_state=str(payload.get("state") or "offline"),
        in_battle=bool(payload.get("in_battle", False)),
        replay=bool(payload.get("replay", False)),
        dead=bool(payload.get("dead", False)),
        vehicle_valid=bool(vehicle.get("valid", False)),
        indicators_valid=bool(indicators.get("valid", False)),
        has_player=bool(situation.get("has_player") or payload.get("player")),
        domain=str(payload.get("domain") or "unknown"),
        domain_label=(str(payload.get("domain_label")) if payload.get("domain_label") is not None else None),
        vehicle_type=(indicators.get("vehicle_type") or processed.get("vehicle_type")),
        profile_matched=(
            bool(processed.get("profile_matched")) if processed.get("profile_matched") is not None else None
        ),
        profile_source=(str(processed.get("profile_source")) if processed.get("profile_source") is not None else None),
        profile_family=(str(processed.get("profile_family")) if processed.get("profile_family") is not None else None),
        timestamp=_num(payload.get("timestamp")) or 0.0,
        age_seconds=_num(fast_meta.get("age_sec")),
        flags={str(k): bool(v) for k, v in flags_raw.items()},
        level=str(processed.get("level") or "info"),
        ias_kmh=_num(processed.get("ias_kmh")) if processed.get("ias_kmh") is not None else _num(vehicle.get("ias_kmh")),
        aoa_deg=_num(processed.get("aoa_deg")) if processed.get("aoa_deg") is not None else _num(vehicle.get("aoa_deg")),
        altitude_m=_num(processed.get("altitude_m")) if processed.get("altitude_m") is not None else _num(vehicle.get("altitude_m")),
        radio_altitude_m=(
            _num(processed.get("radio_altitude_m"))
            if processed.get("radio_altitude_m") is not None
            else _num(indicators.get("radio_altitude"))
        ),
        climb_ms=_num(vehicle.get("climb_ms")),
        mach=_num(vehicle.get("mach")),
        g_now=_num(processed.get("g_now")) if processed.get("g_now") is not None else _num(vehicle.get("load_factor")),
        fuel_fraction=_num(processed.get("fuel_fraction")),
        fuel_remaining_sec=_num(processed.get("fuel_remaining_sec")),
        water_temp_c=_num(processed.get("water_temp_c")),
        head_temp_c=_num(processed.get("head_temp_c")),
        turbine_temp_c=_num(processed.get("turbine_temp_c")),
        oil_temp_c=_num(processed.get("oil_temp_c")),
        crew_current=_num(processed.get("crew_current")),
        crew_total=_num(processed.get("crew_total")),
        ammo_first_stage=_num(processed.get("ammo_first_stage")),
        gun_stabilizer=(
            bool(processed.get("gun_stabilizer")) if processed.get("gun_stabilizer") is not None else None
        ),
        gear_position=(int(gear) if (gear := _num(processed.get("gear_position"))) is not None else None),
        gunner_state=_num(processed.get("gunner_state")),
        driver_state=_num(processed.get("driver_state")),
        hud_events=list(payload.get("hud_events") or []) if isinstance(payload.get("hud_events"), list) else [],
        chat=list(payload.get("chat") or []) if isinstance(payload.get("chat"), list) else [],
        hud_notices=list(hud_notices.get("feed") or []) if isinstance(hud_notices.get("feed"), list) else [],
        combat=payload.get("combat") if isinstance(payload.get("combat"), dict) else {},
        proximity=proximity,
        proximity_events=list(proximity.get("events") or []) if isinstance(proximity.get("events"), list) else [],
        situation=situation,
        mission_status=(str(payload["mission_status"]) if payload.get("mission_status") is not None else None),
        raw=payload,
    )
    return state


class TelemetryClient:
    """对 :8112 的薄客户端：poll() 拉一帧并解析成 BattleState。"""

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def poll(self) -> BattleState:
        return parse_telemetry(fetch_telemetry(self.base_url, self.timeout))
