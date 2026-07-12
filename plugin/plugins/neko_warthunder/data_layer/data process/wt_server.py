"""战雷遥测后台服务（分频轮询版）。

职责：
    1) 按“不同数据用不同频率”的策略，分组在独立线程里轮询游戏的 8111 接口；
    2) 线程安全地缓存各组最新数据 + 最新小地图；
    3) 自身另开一个 HTTP 端口（默认 8112），把处理好的数据以 JSON / 图片对外提供。

说明：8111 是游戏自己开的服务器，本服务是它的客户端；对外服务端口与 8111 不同。

分频策略（每组一个线程，互不阻塞）：
    fast   (state + indicators)          高频，飞行姿态/仪表变化最快   默认 0.1s (10Hz)
    map    (map_obj)                      中频，地图态势               默认 0.5s (2Hz)
    events (mission + hudmsg + gamechat)  低频 + 增量，击杀/聊天事件   默认 1.0s
    mapimg (map_info + map.img)           极低频 + 按版本变化才取底图  默认 5.0s

其中 fast 组兼任“在线/战局”状态探针：只有它判定为 IN_BATTLE 时，其余各组才会真正发起请求；
离开战局时自动清空与本局相关的缓存（地图、HUD、聊天等），避免前端读到过期数据。

回放(战斗录像回放)降级：回放时 8111 仍报 IN_BATTLE，但镜头在各载具间切换、击杀会随
时间轴跳转被重复上报、mission 直接给终局结果——数据语义不可靠。fast 组据此自动识别回放
（game_time_sec 倒退 或 进局后 mission 始终非 running），一旦命中即整局降级：所有接口仅
返回 {"replay": true, ...}，停掉告警/战绩/态势/嘉奖等全部派生上报，直到离开战局复位。

阵亡待命态：玩家被击杀后（可重生模式重生前、或转观战他人直到终局），8111 座舱遥测会冻结
在“死车残骸”上（速度 0/坠机/减员），processor 仍当活载具而持续刷失速/低高度/乘员损失等
假警；观战他人时地图“自身”坐标还会漂到被观战者身上、令态势失真。fast 组据此识别阵亡待命
（combat.my.deaths 增加进入；先见载具静止再恢复运动/满员退出），其间抑制告警、置空态势/接近，
快照与 /health 带 dead 标志；战绩(K/D)不受影响照常上报。

对外接口（GET）：
    /                  健康检查 + 各组刷新状态
    /api/telemetry     最新完整快照（JSON）
    /api/state         载具仪表状态
    /api/indicators    座舱原始仪表
    /api/map_objects   地图物体数组
    /api/map_info      地图坐标换算参数
    /api/hud           累积的最近 HUD 事件（原始）
    /api/notices       自机技术通知（油温过高/襟翼非对称/发动机过热，结构化）
    /api/awards        战斗嘉奖（一血/双杀/三杀/连续无伤歼敌等；含 is_mine/notable）
    /api/chat          累积的最近聊天
    /api/map.jpg       最新小地图底图（图片）
    /api/record        数据转存调试开关（?on=1 开 / ?on=0 关 / 无参查状态），见 wt_recorder.py

运行：
    python wt_server.py
    python wt_server.py --port 9000 --fast-interval 0.05 --save-map
    python wt_server.py --record --record-interval 0.5   # 启动即转存(长对局数据收集)
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# set_player_name 的“无请求”哨兵（None 是合法值=清除昵称，故需独立哨兵）
_UNSET = object()

from dataclasses import asdict

from wt_events import AwardTracker, KillTracker, NoticeTracker
from wt_geo import analyze_situation
from wt_processor import TelemetryProcessor
from wt_recorder import SessionRecorder
from wt_proximity import ProximityTracker, resolve_proximity_thresholds
from wt_telemetry import DEFAULT_PORT as WT_PORT
from wt_telemetry import (
    ConnectionState,
    Indicators,
    MapInfo,
    Telemetry,
    VehicleState,
    WarThunderClient,
    detect_domain,
)

_CONTENT_TYPE_BY_EXT = {"jpg": "image/jpeg", "png": "image/png"}
DEFAULT_BIND_HOST = "127.0.0.1"

_HUD_BUFFER = 200   # HUD 事件累积上限
_CHAT_BUFFER = 200  # 聊天累积上限
_PROXIMITY_BUFFER = 100  # 接近告警累积上限
# 进入战局后的告警抑制窗口（秒）：air RB 空中生成的低速/低高度瞬态会误报失速等，
# 刚进局这段时间没有真实紧急，统一抑制告警以免开局刷屏（不影响派生量/数值）。
_SPAWN_SUPPRESS_SEC = 10.0

# 回放(战斗录像回放)检测：回放里 8111 仍报 in_battle，但数据语义与实战完全不同——
# 观战镜头在各载具间切换(载具/速度/油量都不属于单一玩家)、击杀会因时间跳转被重复上报、
# mission 一进场就是终局结果。这类数据喂给前端只会制造混乱，故一旦判定为回放，
# 整局降级为“仅上报回放模式”，停掉告警/战绩/态势/嘉奖等一切派生上报。
# 判据(任一命中即锁定本局为回放，直到离开战局复位)：
#   1) game_time_sec 在同一局内明显倒退（时间轴往回跳）——回放独有，实战恒单调递增；
#   2) 进局 grace 秒后 mission_status 始终未出现 'running'，却已是终局/未定义态。
_REPLAY_TIME_BACK_SEC = 5.0
_REPLAY_MISSION_GRACE_SEC = 8.0

# 阵亡待命态检测（玩家被击杀后→重生/观战窗口）：实测玩家死亡后，8111 的座舱遥测会
# 冻结在“死车残骸”上（速度=0、坠机后高度不变、乘员减员），而 processor 仍把它当活载具，
# 于是持续刷失速/低高度/乘员损失等假警；观战他人时地图“自身”坐标还会漂到被观战者身上，
# 令态势(敌距/方位/接近)失真。故一旦判定玩家阵亡待命，就抑制告警 + 标记态势不可靠。
# 进入：combat.my.deaths 增加（解析到 is_my_death 新事件）。
# 退出：必须先看到载具“静止/残骸化”(_dead_inert_seen)，再恢复运动或满员——以此区分
#       “死亡俯冲(高速但已死)”与“重生起飞/行驶”。死亡俯冲时 inert 尚未出现，不会误退出。
_DEAD_INERT_IAS_KMH = 40.0    # 视为静止(残骸)的 IAS 上限
_DEAD_INERT_SPEED_MS = 3.0    # 视为静止(残骸)的地面速度上限
_DEAD_ALIVE_IAS_KMH = 150.0   # 视为重新升空的 IAS 下限
_DEAD_ALIVE_SPEED_MS = 5.0    # 视为重新行驶的地面速度下限


# ---------------------------------------------------------------------------
# 后台采集服务：分频多线程轮询 + 缓存
# ---------------------------------------------------------------------------


class TelemetryService:
    """按数据组分频轮询 8111，并缓存最新数据。"""

    def __init__(
        self,
        client: WarThunderClient,
        fast_interval: float = 0.1,
        map_interval: float = 0.5,
        event_interval: float = 1.0,
        mapimg_interval: float = 5.0,
        save_map: bool = False,
        map_dir: str = "maps",
        profiles_path: str | None = None,
        player_name: str | None = None,
        recorder: SessionRecorder | None = None,
    ) -> None:
        self.client = client
        self.save_map = save_map
        self.map_dir = map_dir
        self.processor = TelemetryProcessor(profiles_path)
        self.tracker = KillTracker(player_name=player_name)
        self.notices = NoticeTracker()
        self.awards = AwardTracker()  # 战斗嘉奖（一血/连杀等高光/情报）
        self.proximity = ProximityTracker()
        # 会话录制器（调试开关，默认关闭；为 None 时建一个未启动的）
        self.recorder = recorder or SessionRecorder()

        # 各数据组的轮询间隔（秒）
        self.intervals = {
            "fast": max(0.02, fast_interval),
            "map": max(0.05, map_interval),
            "events": max(0.1, event_interval),
            "mapimg": max(0.5, mapimg_interval),
        }

        self._lock = threading.Lock()

        # -- 缓存（均为整体替换，读取时拷贝引用即可） --
        self._state = ConnectionState.OFFLINE
        self._fast_ts = 0.0
        self._indicators = Indicators(valid=False)
        self._vehicle = VehicleState(valid=False)
        self._map_objects: list[Any] = []
        self._map_info = MapInfo(valid=False)
        self._mission_status: str | None = None
        self._mission_objectives: Any = None
        self._hud_events: deque = deque(maxlen=_HUD_BUFFER)
        self._chat: deque = deque(maxlen=_CHAT_BUFFER)
        self._processed: dict[str, Any] | None = None  # 加工后的关键信息/告警
        self._situation: dict[str, Any] | None = None   # 态势(最近敌机/距离方位)
        self._combat: dict[str, Any] | None = None       # 战绩(击杀流/K-D)
        self._notices: dict[str, Any] | None = None       # 自机技术通知(油温/襟翼等)
        self._awards: dict[str, Any] | None = None         # 战斗嘉奖(一血/连杀等)
        self._proximity_events: deque = deque(maxlen=_PROXIMITY_BUFFER)  # 敌军接近告警流
        self._proximity_threshold: dict[str, Any] | None = None  # 当前接近距离{vs_air,vs_ground}

        # 最新地图（内存）
        self._map_bytes: bytes | None = None
        self._map_ext: str | None = None
        self._map_gen: int | None = None

        # 每组运行统计
        self._meta = {
            name: {"count": 0, "last": 0.0} for name in self.intervals
        }

        # 运行时设置玩家昵称的待处理请求（由 HTTP 线程写、events 线程取用，避免跨线程改 tracker）
        self._name_req: Any = _UNSET
        # 进入对局待排空 hud 积压标志（fast 线程置位、events 线程消费）：
        # 服务(重)启后游标为 0，进局首拉会带回 8111 跨局缓冲的上一局残留，需先丢弃。
        # 初值 True：覆盖“工具启动时已在对局中”的冷启动场景。
        self._hud_drain_pending = True
        # 进入战局的时间戳（用于开局告警抑制窗口）；离开战局清空。
        self._battle_entry_ts: float | None = None
        # 本次出生的时间戳；同局重生时刷新，用于重新开启出生告警抑制。
        self._life_entry_ts: float | None = None
        # 回放检测：本局是否判定为录像回放（锁定式，进/出战局复位）。
        self._replay = False
        self._last_game_time: float | None = None  # 上一帧游戏内时间(秒)，用于倒退检测
        self._mission_running_seen = False          # 本局 mission 是否曾出现 'running'
        # 阵亡待命态：玩家被击杀后→重生/观战窗口（进/出战局复位）。
        self._dead = False
        self._dead_since: float | None = None
        self._dead_inert_seen = False               # 死后是否已见载具静止(残骸/观战冻结)
        self._last_deaths = 0                        # 上次见到的 combat.my.deaths（检测增量=新阵亡）

        self._running = False
        self._threads: list[threading.Thread] = []
        self._battle_generation = 0

    # -- 生命周期 ----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        workers: list[tuple[str, Callable[..., None], bool]] = [
            ("fast", self._poll_fast, False),     # 状态探针，始终运行
            ("map", self._poll_map, True),
            ("events", self._poll_events, True),
            ("mapimg", self._poll_mapimg, True),
        ]
        for name, fn, require_battle in workers:
            th = threading.Thread(
                target=self._worker,
                args=(name, fn, require_battle),
                name=f"wt-{name}",
                daemon=True,
            )
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        self._running = False
        for th in self._threads:
            th.join(timeout=2.0)
        if self.recorder.recording:
            self.recorder.stop()

    # -- 通用轮询循环 ------------------------------------------------------

    def _worker(self, name: str, fn: Callable[[], None], require_battle: bool) -> None:
        interval = self.intervals[name]
        while self._running:
            t0 = time.time()
            try:
                if (not require_battle) or self._state is ConnectionState.IN_BATTLE:
                    with self._lock:
                        generation = self._battle_generation
                    if require_battle:
                        fn(generation)
                    else:
                        fn()
                    with self._lock:
                        if (not require_battle) or generation == self._battle_generation:
                            meta = self._meta[name]
                            meta["count"] += 1
                            meta["last"] = time.time()
            except Exception as exc:  # 单组异常不影响其它组与整体循环
                print(f"[{name}] 轮询出错（已忽略）：{exc!r}", file=sys.stderr)
            elapsed = time.time() - t0
            time.sleep(max(0.0, interval - elapsed))

    # -- 各组采集（网络 IO 在锁外，仅缓存更新在锁内） ----------------------

    def _poll_fast(self) -> None:
        # 探针同时取 indicators + map_info；战局判定以 map_info.valid 为准
        # （主界面/机库的 indicators/state/mission 都可能“像在战局”）
        state, ind, minfo = self.client.get_indicators()
        now = time.time()
        if state is ConnectionState.IN_BATTLE:
            vehicle = self.client.get_state()
            processed = self.processor.process(vehicle, ind, now).to_dict()
        else:
            vehicle = VehicleState(valid=False)
            processed = None
            self.processor.reset()
        with self._lock:
            prev = self._state
            self._state = state
            if state is not prev:
                self._battle_generation += 1
            self._indicators = ind
            self._vehicle = vehicle
            self._map_info = minfo  # grid 参数随 fast 实时刷新，供态势换算
            self._fast_ts = now
            # 离开战局 -> 清空本局缓存
            if state is not ConnectionState.IN_BATTLE and prev is ConnectionState.IN_BATTLE:
                self._reset_battle_cache_locked()
                self._battle_entry_ts = None
                self._life_entry_ts = None
            # 进入战局 -> 标记需排空 hud 积压（丢弃上一局/连接前的残留事件）+ 记录进局时刻
            if state is ConnectionState.IN_BATTLE and prev is not ConnectionState.IN_BATTLE:
                self._hud_drain_pending = True
                self._battle_entry_ts = now
                self._life_entry_ts = now
                self._replay = False
                self._last_game_time = None
                self._mission_running_seen = False
                self._dead = False
                self._dead_since = None
                self._dead_inert_seen = False
                self._last_deaths = 0
            # 回放检测（仅战局内；锁定式，命中后保持到离开战局）
            if state is ConnectionState.IN_BATTLE and not self._replay:
                self._detect_replay_locked(ind, now)
            # 阵亡待命态检测（仅战局内）
            respawned = False
            if state is ConnectionState.IN_BATTLE:
                respawned = self._update_dead_state_locked(ind, processed, now)
            if respawned:
                # 当前 processed 在复活判定前生成，仍可能携带上一条命的弹药基线。
                # 立即重置处理器并压掉当前帧；下一帧从新载具重新建立基线。
                self.processor.reset()
                self._life_entry_ts = now
                processed = None
            # 开局抑制窗口：进局前 _SPAWN_SUPPRESS_SEC 秒清空告警（保留派生量/数值），
            # 压掉 air RB 空中生成的失速/低高度等瞬态假警。
            # 阵亡待命态同样抑制告警（死车残骸/观战冻结会刷失速/乘员损失等假警）。
            if (processed is not None and (
                    self._dead
                    or (self._life_entry_ts is not None
                        and now - self._life_entry_ts < _SPAWN_SUPPRESS_SEC))):
                processed = {**processed, "alerts": [], "flags": {}}
            self._processed = processed
        # 录制（调试开关）：按记录间隔转存一帧快照；未开启录制时近乎零开销
        self.recorder.offer_frame(self._build_record_frame)

    def _poll_map(self, generation: int) -> None:
        objs = self.client.get_map_objects()
        # 态势分析依赖 map_info（由 mapimg 组维护），grid 参数基本不变可直接用缓存
        situation = analyze_situation(objs, self._map_info)
        # 敌军接近告警：阈值随【我方兵种×敌方类型】变化
        ind = self._indicators
        domain = detect_domain(ind, True, objs)
        thr_air, thr_ground = resolve_proximity_thresholds(
            self.processor.profiles,
            domain,
            getattr(ind, "vehicle_type", None),
            getattr(self.processor, "_family_rules", []),
        )
        now = time.time()
        # 阵亡待命态：地图“自身”坐标会漂到被观战者身上，敌距/接近全部失真，不再生成接近告警。
        with self._lock:
            if generation != self._battle_generation:
                return
            if self._dead:
                prox_events = []
            else:
                prox_events = self.proximity.update(
                    situation.get("enemies", []), thr_air, thr_ground, now
                )
            self._map_objects = objs
            self._situation = situation
            self._proximity_threshold = {"vs_air": thr_air, "vs_ground": thr_ground}
            for ev in prox_events:
                self._proximity_events.append(ev)
        # 录制：接近边沿事件增量落盘
        if prox_events:
            self.recorder.write_events("proximity", list(prox_events))

    def _poll_events(self, generation: int) -> None:
        # 先应用待处理的昵称设置（仅本线程改 tracker，避免与 HTTP 线程竞争）
        with self._lock:
            req = self._name_req
            self._name_req = _UNSET
            drain = self._hud_drain_pending
            self._hud_drain_pending = False
        if req is not _UNSET:
            self.tracker.set_player_name(req)
        # 进入对局首次轮询：排空 8111 跨局缓冲的旧事件（推进游标但不计入战绩），
        # 并清空 tracker/notices，确保本局从干净状态起算；本周期不再继续喂入。
        if drain:
            dropped = self.client.drain_hud()
            self.tracker.reset()
            self.notices.reset()
            self.awards.reset()
            self.recorder.mark({"_event": "hud_drain", "dropped": dropped})
            return

        status, objectives = self.client.get_mission()
        hud = self.client.get_hud()
        chat = self.client.get_chat()
        with self._lock:
            if generation != self._battle_generation:
                return
            self.tracker.feed(hud)  # 解析击杀事件并累积战绩
            combat = self.tracker.get_summary()
            self.notices.feed(hud)  # 解析自机技术通知(油温过高/襟翼非对称/发动机过热)
            notices = self.notices.get_summary()
            self.awards.feed(hud)   # 解析战斗嘉奖(一血/双杀/三杀/连续无伤歼敌等)
            awards = self.awards.get_summary(combat.get("player_name"))
            self._mission_status = status
            self._mission_objectives = objectives
            self._combat = combat
            self._notices = notices
            self._awards = awards
            for ev in hud:
                self._hud_events.append(ev)
            for msg in chat:
                self._chat.append(msg)
        # 录制：HUD/聊天增量落盘（击杀/通知可离线从 hudmsg 再解析）
        if hud:
            self.recorder.write_events("hudmsg", [asdict(ev) for ev in hud])
        if chat:
            self.recorder.write_events("chat", list(chat))

    def _poll_mapimg(self, generation: int) -> None:
        # map_info 已由 fast 组实时缓存，这里只负责按 generation 拉取底图
        with self._lock:
            info = self._map_info
        new_map: tuple[bytes, str, int | None] | None = None
        if info.valid and (self._map_bytes is None or info.map_generation != self._map_gen):
            data, ext = self.client.fetch_map_image()
            if data and ext:
                new_map = (data, ext, info.map_generation)
        with self._lock:
            if generation != self._battle_generation:
                return
            if new_map is not None:
                self._map_bytes, self._map_ext, self._map_gen = new_map
        if new_map is not None and self.save_map:
            self._write_map(*new_map)

    def _detect_replay_locked(self, ind: Indicators, now: float) -> None:
        """判定本局是否为录像回放（调用方需已持锁，且仅在战局内调用）。

        命中任一判据即把 self._replay 置真（锁定到离开战局）：
          1) game_time_sec 较上一帧明显倒退——回放拖动时间轴往回跳，实战恒增；
          2) 进局 grace 秒后仍未见过 mission_status=='running'，却已是终局/未定义态
             ——回放一进场 mission 就直接返回终局结果，从不经历 running。
        """
        gt = getattr(ind, "game_time_sec", None)
        if (gt is not None and self._last_game_time is not None
                and gt < self._last_game_time - _REPLAY_TIME_BACK_SEC):
            self._replay = True
        if gt is not None:
            self._last_game_time = gt
        if self._mission_status == "running":
            self._mission_running_seen = True
        if (not self._replay and self._battle_entry_ts is not None
                and now - self._battle_entry_ts > _REPLAY_MISSION_GRACE_SEC
                and not self._mission_running_seen
                and self._mission_status in ("success", "fail", "undefined")):
            self._replay = True

    def _update_dead_state_locked(self, ind: Indicators, processed: dict[str, Any] | None,
                                  now: float) -> bool:
        """更新阵亡待命态（调用方需已持锁，且仅在战局内调用）。

        进入：combat.my.deaths 较上次增加（解析到本人新阵亡）。
        退出：先见载具静止/残骸化（_dead_inert_seen），再满足以下任一“复活”信号：
              - 恢复运动（空中 IAS>阈值 / 地面速度>阈值）= 重生起飞/行驶；
              - 乘员恢复满员（地面坦克 crew_total>=2 且 crew_current>=crew_total）= 新车。
        “先静止再活跃”的两段式可正确区分“死亡俯冲(高速但已死)”与“重生”，避免在
        坠落途中误判复活而提前解除抑制。

        返回 True 表示本帧确认由阵亡态进入新一次出生，调用方需重置处理器状态。
        """
        combat = self._combat
        deaths = 0
        if isinstance(combat, dict):
            deaths = (combat.get("my") or {}).get("deaths") or 0
        if deaths > self._last_deaths:
            self._dead = True
            self._dead_since = now
            self._dead_inert_seen = False
        self._last_deaths = deaths
        if not self._dead:
            return False
        ias = processed.get("ias_kmh") if isinstance(processed, dict) else None
        gspeed = getattr(ind, "speed", None)
        inert = ((ias is None or ias < _DEAD_INERT_IAS_KMH)
                 and (gspeed is None or abs(gspeed) < _DEAD_INERT_SPEED_MS))
        if inert:
            self._dead_inert_seen = True
        moving = ((ias is not None and ias > _DEAD_ALIVE_IAS_KMH)
                  or (gspeed is not None and abs(gspeed) > _DEAD_ALIVE_SPEED_MS))
        crew = getattr(ind, "crew_current", None)
        crew_total = getattr(ind, "crew_total", None)
        crew_full = (crew is not None and crew_total is not None
                     and crew_total >= 2 and crew >= crew_total)
        if self._dead_inert_seen and (moving or crew_full):
            self._dead = False
            self._dead_since = None
            return True
        return False

    def _reset_battle_cache_locked(self) -> None:
        """离开战局时清空本局相关缓存（调用方需已持锁）。"""
        # 录制标记：一次会话可跨多局，靠此标记供离线工具按局切分
        self.recorder.mark({"_event": "battle_reset"})
        self._map_objects = []
        self._map_info = MapInfo(valid=False)
        self._mission_status = None
        self._mission_objectives = None
        self._hud_events.clear()
        self._chat.clear()
        self._processed = None
        self._situation = None
        self._combat = None
        self._notices = None
        self._awards = None
        self._proximity_events.clear()
        self._proximity_threshold = None
        self.tracker.reset()
        self.notices.reset()
        self.awards.reset()
        self.proximity.reset()
        self._map_bytes = None
        self._map_ext = None
        self._map_gen = None
        self._replay = False
        self._life_entry_ts = None
        self._last_game_time = None
        self._mission_running_seen = False
        self._dead = False
        self._dead_since = None
        self._dead_inert_seen = False
        self._last_deaths = 0

    def _write_map(self, data: bytes, ext: str, gen: int | None) -> None:
        try:
            os.makedirs(self.map_dir, exist_ok=True)
            name = f"map_{gen}.{ext}" if gen is not None else f"map.{ext}"
            with open(os.path.join(self.map_dir, name), "wb") as fh:
                fh.write(data)
        except OSError as exc:
            print(f"[mapimg] 保存地图失败：{exc!r}", file=sys.stderr)

    # -- 线程安全读取 ------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            # 回放模式：整局降级——只告诉前端“现在是回放”，不上报任何派生数据，
            # 避免镜头切换/时间跳转造成的载具/速度/油量错位与击杀重复计数误导前端。
            if self._replay:
                return {
                    "state": self._state.value,
                    "timestamp": self._fast_ts,
                    "in_battle": self._state is ConnectionState.IN_BATTLE,
                    "replay": True,
                    "note": "回放模式：当前为战斗录像回放，数据语义不可靠，已暂停上报告警/战绩/态势/嘉奖等",
                    "meta": self._meta_locked(),
                }
            snap = Telemetry(
                state=self._state,
                timestamp=self._fast_ts,
                in_battle=self._state is ConnectionState.IN_BATTLE,
                vehicle=self._vehicle,
                indicators=self._indicators,
                map_objects=list(self._map_objects),
                map_info=self._map_info,
                mission_status=self._mission_status,
                mission_objectives=self._mission_objectives,
                hud_events=list(self._hud_events),
                chat=list(self._chat),
            )
            data = snap.to_dict()
            data["replay"] = False
            # 阵亡待命态：玩家被击杀后→重生/观战窗口。告警已在 _poll_fast 抑制；这里再把
            # 依赖“自身位置”的态势/接近置空（观战时坐标漂到被观战者，数据失真）。战绩保留
            # （HUD 带全局名字戳，不会被污染，前端仍可展示最终 K/D / 谁击杀了你）。
            data["dead"] = self._dead
            data["processed"] = self._processed
            data["situation"] = None if self._dead else self._situation
            data["combat"] = self._combat
            data["hud_notices"] = self._notices
            data["awards"] = self._awards
            data["proximity"] = {
                "thresholds_m": self._proximity_threshold,
                "events": [] if self._dead else list(self._proximity_events),
            }
            data["meta"] = self._meta_locked()
        return data

    def get_part(self, key: str) -> Any:
        return self.get_snapshot().get(key)

    def _build_record_frame(self) -> dict[str, Any]:
        """构造一帧录制快照：在完整快照基础上剔除累积型数组（它们另走增量流），
        避免每帧重复转存导致文件 O(n²) 膨胀。"""
        snap = self.get_snapshot()
        for k in ("hud_events", "chat", "hud_notices"):
            snap.pop(k, None)
        combat = snap.get("combat")
        if isinstance(combat, dict):
            combat = {k: v for k, v in combat.items() if k != "feed"}
            snap["combat"] = combat
        prox = snap.get("proximity")
        if isinstance(prox, dict):
            prox = {k: v for k, v in prox.items() if k != "events"}
            snap["proximity"] = prox
        return snap

    def set_player_name(self, name: str | None) -> None:
        """请求设置/清除玩家昵称（在下一次 events 轮询时应用，≤1 个 event-interval 生效）。"""
        with self._lock:
            self._name_req = (name or "").strip() or None

    def get_map(self) -> tuple[bytes | None, str | None]:
        with self._lock:
            return self._map_bytes, self._map_ext

    def _meta_locked(self) -> dict[str, Any]:
        now = time.time()
        out: dict[str, Any] = {}
        for name, m in self._meta.items():
            out[name] = {
                "interval": self.intervals[name],
                "count": m["count"],
                "age_sec": round(now - m["last"], 3) if m["last"] else None,
            }
        return out

    def get_health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "service": "wt-telemetry",
                "state": self._state.value,
                "replay": self._replay,
                "dead": self._dead,
                "updated_at": self._fast_ts,
                "has_map": self._map_bytes is not None,
                "map_generation": self._map_gen,
                "groups": self._meta_locked(),
            }


# ---------------------------------------------------------------------------
# HTTP 请求处理
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "WTTelemetry/2.0"

    @property
    def service(self) -> TelemetryService:
        return self.server.service  # type: ignore[attr-defined]

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def _send_json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path in ("/", "/health", "/api/health"):
            self._send_json(self.service.get_health())
            return

        if path == "/api/telemetry":
            self._send_json(self.service.get_snapshot())
            return

        if path == "/api/processed":
            self._send_json(self.service.get_part("processed"))
            return

        if path == "/api/situation":
            self._send_json(self.service.get_part("situation"))
            return

        if path in ("/api/kills", "/api/combat"):
            self._send_json(self.service.get_part("combat"))
            return

        if path == "/api/identity":
            # 查看/设置“自己是谁”。?name=昵称 设手动昵称(权威)；?clear=1 清除回退自动。
            q = parse_qs(urlparse(self.path).query)
            requested: Any = "(unchanged)"
            if "clear" in q:
                self.service.set_player_name(None)
                requested = None
            elif q.get("name"):
                requested = q["name"][0]
                self.service.set_player_name(requested)
            combat = self.service.get_part("combat") or {}
            self._send_json({
                "requested": requested,
                "note": "设置将在下一次 events 轮询（≤event-interval）后体现在 self 中",
                "self": combat.get("self"),
                "player_name": combat.get("player_name"),
            })
            return

        if path == "/api/notices":
            self._send_json(self.service.get_part("hud_notices"))
            return

        if path == "/api/awards":
            # 战斗嘉奖（一血/双杀/三杀/连续无伤歼敌等）；?notable=1 仅返回高光子集
            q = parse_qs(urlparse(self.path).query)
            awards = self.service.get_part("awards") or {}
            if q.get("notable"):
                awards = {**awards, "feed": awards.get("notable", [])}
            self._send_json(awards)
            return

        if path == "/api/record":
            # 调试开关：?on=1 开始转存 / ?on=0 停止 / 无参=查状态
            q = parse_qs(urlparse(self.path).query)
            rec = self.service.recorder
            if "on" in q:
                want = q["on"][0].strip().lower() in ("1", "true", "yes", "on")
                status = rec.start() if want else rec.stop()
            else:
                status = rec.status()
            self._send_json(status)
            return

        if path == "/api/proximity":
            self._send_json(self.service.get_part("proximity"))
            return

        if path == "/api/alerts":
            processed = self.service.get_part("processed")
            alerts = processed.get("alerts", []) if isinstance(processed, dict) else []
            level = processed.get("level") if isinstance(processed, dict) else None
            self._send_json({"level": level, "alerts": alerts})
            return

        if path in ("/api/map.jpg", "/api/map"):
            data, ext = self.service.get_map()
            if not data:
                self._send_json({"error": "no map available"}, 404)
                return
            ctype = _CONTENT_TYPE_BY_EXT.get(ext or "", "application/octet-stream")
            self._send_bytes(data, ctype)
            return

        subset_keys = {
            "/api/state": "vehicle",
            "/api/indicators": "indicators",
            "/api/map_objects": "map_objects",
            "/api/map_info": "map_info",
            "/api/hud": "hud_events",
            "/api/chat": "chat",
        }
        if path in subset_keys:
            self._send_json(self.service.get_part(subset_keys[path]))
            return

        self._send_json({"error": "not found", "path": path}, 404)

    def log_message(self, *args: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def create_http_server(host: str, port: int):
    server_class = ThreadingHTTPServer
    if ":" in host:
        class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server_class = IPv6ThreadingHTTPServer
    return server_class((host, port), _Handler)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="战雷遥测后台服务（分频轮询）")
    parser.add_argument("--host", default=DEFAULT_BIND_HOST, help="服务监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8112, help="对外服务端口（默认 8112）")
    parser.add_argument("--wt-host", default="127.0.0.1", help="游戏 8111 地址")
    parser.add_argument("--wt-port", type=int, default=WT_PORT, help="游戏遥测端口（默认 8111）")
    parser.add_argument("--fast-interval", type=float, default=0.1, help="姿态/仪表轮询间隔（默认 0.1s）")
    parser.add_argument("--map-interval", type=float, default=0.5, help="地图物体轮询间隔（默认 0.5s）")
    parser.add_argument("--event-interval", type=float, default=1.0, help="任务/HUD/聊天轮询间隔（默认 1.0s）")
    parser.add_argument("--mapimg-interval", type=float, default=5.0, help="地图底图检查间隔（默认 5.0s）")
    parser.add_argument("--save-map", action="store_true", help="地图变化时落盘保存")
    parser.add_argument("--map-dir", default="maps", help="地图保存目录")
    parser.add_argument("--profiles", default=None, help="机型告警配置文件路径")
    parser.add_argument("--player-name", default=None,
                        help="玩家名(不含战队标签)的初始权威值；留空则自动识别，"
                             "也可运行时用 GET /api/identity?name=xxx 设置")
    parser.add_argument("--record", action="store_true",
                        help="启动即开启数据转存（调试开关；也可运行时 GET /api/record?on=1 切换）")
    parser.add_argument("--record-dir", default="records", help="转存数据根目录（默认 records）")
    parser.add_argument("--record-interval", type=float, default=1.0,
                        help="快照转存间隔（秒，默认 1.0；抓超速/失速等快瞬变可设 0.2）")
    parser.add_argument("--record-segment-mb", type=float, default=32.0,
                        help="frames 明文段滚动压缩阈值（MB，默认 32；写满即后台 gzip 留存）")
    args = parser.parse_args()

    recorder = SessionRecorder(
        root_dir=args.record_dir,
        interval=args.record_interval,
        segment_bytes=int(args.record_segment_mb * 1024 * 1024),
        server_version=_Handler.server_version,
    )

    client = WarThunderClient(host=args.wt_host, port=args.wt_port)
    service = TelemetryService(
        client,
        fast_interval=args.fast_interval,
        map_interval=args.map_interval,
        event_interval=args.event_interval,
        mapimg_interval=args.mapimg_interval,
        save_map=args.save_map,
        map_dir=args.map_dir,
        profiles_path=args.profiles,
        player_name=args.player_name,
        recorder=recorder,
    )
    if args.record:
        st = recorder.start()
        print(f"  [录制] 已开启 -> {st['session_dir']}")
    service.start()

    httpd = create_http_server(args.host, args.port)
    httpd.service = service  # type: ignore[attr-defined]

    print(f"战雷遥测服务已启动：http://{args.host}:{args.port}")
    print(f"  数据源：http://{args.wt_host}:{args.wt_port}")
    print("  分频轮询：")
    print(f"    fast(state+indicators) {args.fast_interval}s")
    print(f"    map(map_obj)           {args.map_interval}s")
    print(f"    events(mission+hud+chat) {args.event_interval}s")
    print(f"    mapimg(map_info+map.img) {args.mapimg_interval}s")
    print("  接口： /  /api/telemetry  /api/state  /api/map_objects  /api/map.jpg")
    print("        /api/processed  /api/alerts  （自定义告警）")
    print("        /api/situation （态势）  /api/kills （战绩，含自我识别/涉我标记）")
    print("        /api/identity （查看/设置玩家昵称：?name=xxx / ?clear=1）")
    print("        /api/notices （自机技术通知：油温/襟翼/过热）")
    print("        /api/awards （战斗嘉奖：一血/双杀/三杀/连续无伤歼敌；?notable=1 仅高光）")
    print("        /api/proximity （敌军接近告警，边沿触发）")
    print("        /api/record （数据转存调试开关：?on=1 开 / ?on=0 关 / 无参查状态）")
    if args.record:
        print(f"  数据转存：开启（间隔 {args.record_interval}s，目录 {args.record_dir}）")
    print("  Ctrl+C 退出\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭…")
    finally:
        httpd.shutdown()
        service.stop()
        print("已停止。")


if __name__ == "__main__":
    main()
