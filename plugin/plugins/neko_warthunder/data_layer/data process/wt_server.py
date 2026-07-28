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
（本人死亡事件增加，或陆战可信乘员数降至无法继续作战时进入；先见载具静止再恢复运动/满员退出），
其间抑制告警、置空态势/接近，
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
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# set_player_name 的“无请求”哨兵（None 是合法值=清除昵称，故需独立哨兵）
_UNSET = object()
_ACTION_HEADER = "X-Neko-Warthunder-Action"

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
# 超过半天的"倒退"按座舱时钟午夜回绕处理，不判回放（见 _detect_replay_locked）。
_MIDNIGHT_WRAP_MIN_BACK_SEC = 43200.0
_REPLAY_MISSION_GRACE_SEC = 8.0
_WORKER_JOIN_TIMEOUT_SECONDS = 2.0
_TERMINAL_MISSION_STATUSES = frozenset({
    "win", "won", "victory", "success",
    "fail", "failed", "lost", "defeat",
    "left", "ended", "finished",
})

# 8111/本地 HTTP 单次延迟不等于离开战局。战局内探针失败在此窗口内保留上一帧，
# 只有持续失败才切到 offline；真实的 map_info.valid=false 响应仍立即判定离局。
_PROBE_FAILURE_GRACE_SEC = 5.0

# 阵亡待命态检测（玩家被击杀后→重生/观战窗口）：实测玩家死亡后，8111 的座舱遥测会
# 冻结在“死车残骸”上（速度=0、坠机后高度不变、乘员减员），而 processor 仍把它当活载具，
# 于是持续刷失速/低高度/乘员损失等假警；观战他人时地图“自身”坐标还会漂到被观战者身上，
# 令态势(敌距/方位/接近)失真。故一旦判定玩家阵亡待命，就抑制告警 + 标记态势不可靠。
# 进入：combat.my.deaths 增加（解析到 is_my_death 新事件）。
# 退出：必须先看到载具“静止/残骸化”(_dead_inert_seen)，再恢复运动；陆战满员恢复还要求
#       阵亡后曾见过减员帧。以此区分“死亡俯冲/旧满员帧”与“重生起飞/行驶”。
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
        # 终局 mission 先于最终 HUD 到达时，先私下暂存；若 HUD 随后恢复则与最终 K/D
        # 原子发布，若快速探针先确认离局则作为终局交接保留到下一局开始，避免胜负边沿丢失。
        self._pending_terminal_status: str | None = None
        self._pending_terminal_objectives: Any = None
        self._terminal_handoff_active = False
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
        # 手动昵称是跨战局配置，不能依赖会在离局时清空的 _combat 缓存来报告。
        self._manual_player_name: str | None = (player_name or "").strip() or None
        # 进入对局待排空 hud 积压标志（fast 线程置位、events 线程消费）：
        # 服务(重)启后游标为 0，进局首拉会带回 8111 跨局缓冲的上一局残留，需先丢弃。
        # 初值 True：覆盖“工具启动时已在对局中”的冷启动场景。
        self._hud_drain_pending = True
        self._chat_drain_pending = True
        # 首次 HUD 排空失败时保存进局前游标；重试从该边界读取失败窗口内的本局事件，
        # 而不是再次从 0 排空并永久丢失击杀/阵亡。
        self._hud_recovery_cursor: dict[str, int] | None = None
        self._probe_failure_since: float | None = None
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
        self._dead_crew_depleted_seen = False       # 死后是否已见陆战乘员未满（满员恢复的前置边沿）
        self._dead_source: str | None = None         # hud_event / ground_crew
        self._last_deaths = 0                        # 上次见到的 combat.my.deaths（检测增量=新阵亡）

        self._running = False
        # 关停信号：worker 用 wait() 代替 sleep()，stop() 才能在毫秒级唤醒线程，
        # 而不是等到最长 5s(mapimg) 的睡眠自然结束——否则 join(2s) 必然超时返回，
        # "stop() 已返回但线程还在跑"会让后续任何清理逻辑踩到竞态。
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._battle_generation = 0
        # 8111 不提供官方 match/session id。每次确认 false->true 进入战局时生成本地 ID；
        # 同局死亡、观战和重生都保持不变，只有确认离局后再次进入才换 ID。
        self._battle_id: str | None = None
        self._life_index: int | None = None

    # -- 生命周期 ----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        alive = [thread for thread in self._threads if thread.is_alive()]
        if alive:
            names = ", ".join(sorted({thread.name for thread in alive}))
            raise RuntimeError(f"telemetry_workers_still_stopping: {names}")
        self._threads = []
        # 每一代 worker 捕获自己的停止事件。即使 stop() 在阻塞 I/O 上等待超时，
        # 后续 start 也不能 clear 旧事件把遗留 worker 重新唤醒。
        self._stop_event = threading.Event()
        stop_event = self._stop_event
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
                args=(name, fn, require_battle, stop_event),
                name=f"wt-{name}",
                daemon=True,
            )
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        for th in self._threads:
            th.join(timeout=_WORKER_JOIN_TIMEOUT_SECONDS)
        # 保留尚未退出的线程引用，start() 会拒绝与它们重叠运行；线程从阻塞
        # I/O 返回后会看到自己那一代已 set 的 Event 并自然退出。
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        if self.recorder.recording:
            self.recorder.stop()

    # -- 通用轮询循环 ------------------------------------------------------

    def _worker(
        self,
        name: str,
        fn: Callable[[], None],
        require_battle: bool,
        stop_event: threading.Event,
    ) -> None:
        interval = self.intervals[name]
        while not stop_event.is_set():
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
            # 可中断等待：stop() set 事件后立刻返回，不必等满 interval。
            if stop_event.wait(max(0.0, interval - elapsed)):
                return

    # -- 各组采集（网络 IO 在锁外，仅缓存更新在锁内） ----------------------

    def _poll_fast(self) -> None:
        # 探针同时取 indicators + map_info；战局判定以 map_info.valid 为准
        # （主界面/机库的 indicators/state/mission 都可能“像在战局”）
        probe_ok, state, ind, minfo = self.client.get_indicators_with_status()
        now = time.time()
        if not probe_ok:
            with self._lock:
                if self._probe_failure_since is None:
                    self._probe_failure_since = now
                    probe_failure_started = True
                else:
                    probe_failure_started = False
                keep_previous = (
                    self._state is ConnectionState.IN_BATTLE
                    and now - self._probe_failure_since < _PROBE_FAILURE_GRACE_SEC
                )
            if probe_failure_started:
                self.recorder.mark({"_event": "probe_failure_started"})
            if keep_previous:
                return
            state = ConnectionState.OFFLINE
            ind = Indicators(valid=False)
            minfo = MapInfo(valid=False)
        else:
            with self._lock:
                failure_since = self._probe_failure_since
                self._probe_failure_since = None
            if failure_since is not None:
                self.recorder.mark({
                    "_event": "probe_recovered",
                    "duration_seconds": round(max(0.0, now - failure_since), 3),
                })
        if state is ConnectionState.IN_BATTLE:
            state_ok, vehicle = self.client.get_state_with_status()
            if state_ok:
                processed = self.processor.process(vehicle, ind, now).to_dict()
            else:
                # 单帧 /state 失败时保留上一帧，避免空战被误判死亡后又假出生。
                with self._lock:
                    vehicle = self._vehicle
                    processed = self._processed
                self.recorder.mark({"_event": "vehicle_state_poll_failed"})
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
                self._reset_battle_cache_locked(preserve_terminal_handoff=True)
                self._battle_entry_ts = None
                self._life_entry_ts = None
            # 进入战局 -> 标记需排空 hud 积压（丢弃上一局/连接前的残留事件）+ 记录进局时刻
            if state is ConnectionState.IN_BATTLE and prev is not ConnectionState.IN_BATTLE:
                # 终局交接只服务上一局离局后的检测窗口；新局状态必须从空基线开始。
                if self._terminal_handoff_active:
                    self._mission_status = None
                    self._mission_objectives = None
                    self._combat = None
                    self._terminal_handoff_active = False
                # 游标清零和排空由 events 线程独占执行，避免旧请求晚返回覆盖新局游标。
                self._hud_drain_pending = True
                self._chat_drain_pending = True
                self._hud_recovery_cursor = None
                self._battle_entry_ts = now
                self._life_entry_ts = now
                self._battle_id = uuid.uuid4().hex
                self._life_index = 1
                self._replay = False
                self._last_game_time = None
                self._mission_running_seen = False
                self._dead = False
                self._dead_since = None
                self._dead_inert_seen = False
                self._dead_crew_depleted_seen = False
                self._dead_source = None
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
                # level 由 alerts 的最高等级派生，清空 alerts 时必须一并降级；
                # 否则 /api/processed、/api/alerts 会返回 {"level": "critical", "alerts": []}，
                # 按 level 判断紧急程度的下游会读到被抑制掉的假警等级。
                processed = {**processed, "alerts": [], "flags": {}, "level": "info"}
            self._processed = processed
        # 录制（调试开关）：按记录间隔转存一帧快照；未开启录制时近乎零开销
        self.recorder.offer_frame(self._build_record_frame)

    def _poll_map(self, generation: int) -> None:
        map_ok, objs = self.client.get_map_objects_with_status()
        if not map_ok:
            # 传输失败不是“地图上没有敌人”；保留上一帧态势与轨迹基线。
            self.recorder.mark({"_event": "map_objects_poll_failed"})
            return
        # 态势分析依赖 map_info（由 mapimg 组维护），grid 参数基本不变可直接用缓存
        situation = analyze_situation(objs, self._map_info)
        # 敌军接近告警：阈值随【我方兵种×敌方类型】变化
        ind = self._indicators
        domain = detect_domain(ind, True, objs)
        thr_air, thr_ground = resolve_proximity_thresholds(
            self.processor.profiles,
            domain,
            getattr(ind, "vehicle_type", None),
            self.processor.resolve_profile,
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
            if generation != self._battle_generation:
                return
            req = self._name_req
            self._name_req = _UNSET
            drain_hud = self._hud_drain_pending
            drain_chat = self._chat_drain_pending
            hud_recovery_cursor = self._hud_recovery_cursor
            self._hud_drain_pending = False
            self._chat_drain_pending = False
        if req is not _UNSET:
            self.tracker.set_player_name(req)
        # 进入对局首次轮询：游标仅由 events 线程清零并排空旧缓冲。HUD 与聊天分别重试，
        # 避免任一接口短暂失败导致另一接口反复清零、吞掉本局新事件。
        poll_hud = True
        poll_chat = True
        recovered_hud = []
        if drain_hud or drain_chat:
            cursors_before = self.client.incremental_cursor_state()
            hud_ok, dropped_hud = True, 0
            chat_ok, dropped_chat = True, 0
            if drain_hud:
                if hud_recovery_cursor is None:
                    self.client.reset_hud_cursors()
                hud_ok, drained_hud = self.client.get_hud_with_status()
                if hud_recovery_cursor is None:
                    dropped_hud = len(drained_hud)
                else:
                    recovered_hud = drained_hud
            if drain_chat:
                self.client.reset_chat_cursor()
                chat_ok, old_chat = self.client.get_chat_with_status()
                dropped_chat = len(old_chat)
            with self._lock:
                if generation != self._battle_generation:
                    return
                if drain_hud and not hud_ok:
                    self._hud_drain_pending = True
                    poll_hud = False
                    if hud_recovery_cursor is None:
                        self.client.restore_hud_cursors(cursors_before)
                        last_evt = cursors_before.get("last_evt", 0)
                        last_dmg = cursors_before.get("last_dmg", 0)
                        # 冷启动（服务在对局中途启动）时客户端游标仍是初始 0，不构成
                        # 可信的"进局前边界"。此时若保存 {0, 0} 作为恢复游标，重试会从
                        # 8111 跨局滚动缓冲的最开头读取，把上一局残留当本局击杀/阵亡喂入
                        # ——正是 drain 机制要消除的污染。没有可信边界就退回普通 drain。
                        if last_evt or last_dmg:
                            self._hud_recovery_cursor = {"last_evt": last_evt, "last_dmg": last_dmg}
                        else:
                            self._hud_recovery_cursor = None
                elif drain_hud:
                    self._hud_recovery_cursor = None
                if drain_chat and not chat_ok:
                    self._chat_drain_pending = True
                    poll_chat = False
            if drain_hud and hud_ok:
                self.tracker.reset()
                self.notices.reset()
                self.awards.reset()
            self.recorder.mark({
                # 保留既有事件名，避免录制回放/分析工具因标记改名而失配。
                "_event": "hud_drain",
                "dropped": dropped_hud,
                "dropped_hud": dropped_hud,
                "dropped_chat": dropped_chat,
                "recovered_hud": len(recovered_hud),
                "hud_ok": hud_ok,
                "chat_ok": chat_ok,
                "cursors_before": cursors_before,
                "cursors_after": self.client.incremental_cursor_state(),
            })
            if not poll_hud and not poll_chat:
                return

        status, objectives = self.client.get_mission()
        cursors_before = self.client.incremental_cursor_state()
        hud_ok, hud = self.client.get_hud_with_status() if poll_hud else (False, [])
        chat_ok, chat = self.client.get_chat_with_status() if poll_chat else (False, [])
        if recovered_hud:
            hud = [*recovered_hud, *hud]
        terminal_status = str(status or "").strip().lower() in _TERMINAL_MISSION_STATUSES
        with self._lock:
            if generation != self._battle_generation:
                return
            if poll_hud:
                self.tracker.feed(hud)  # 解析击杀事件并累积战绩
                combat = self.tracker.get_summary()
                self.notices.feed(hud)  # 解析自机技术通知(油温过高/襟翼非对称/发动机过热)
                notices = self.notices.get_summary()
                self.awards.feed(hud)   # 解析战斗嘉奖(一血/双杀/三杀/连续无伤歼敌等)
                awards = self.awards.get_summary(combat.get("player_name"))
                self._combat = combat
                self._notices = notices
                self._awards = awards
            # battle_end is edge-triggered and consumes the current K/D once. Keep a
            # terminal mission result private until the matching HUD channel succeeds,
            # so the result and final combat summary become visible atomically. If the
            # player leaves before HUD recovers, _reset_battle_cache_locked publishes
            # the pending result without an unverifiable combat summary as a fallback.
            if terminal_status and not hud_ok:
                self._pending_terminal_status = status
                self._pending_terminal_objectives = objectives
            else:
                self._mission_status = status
                self._mission_objectives = objectives
                self._pending_terminal_status = None
                self._pending_terminal_objectives = None
            for ev in hud:
                self._hud_events.append(ev)
            for msg in chat:
                self._chat.append(msg)
        self.recorder.mark({
            "_event": "incremental_poll",
            "hud_ok": hud_ok,
            "chat_ok": chat_ok,
            "hud_count": len(hud),
            "chat_count": len(chat),
            "cursors_before": cursors_before,
            "cursors_after": self.client.incremental_cursor_state(),
        })
        # 录制：仅 HUD 增量落盘（击杀/通知可离线从 hudmsg 再解析）。
        # 原始聊天只保留在受限内存缓冲中，严禁写入持久化录制。
        if hud:
            self.recorder.write_events("hudmsg", [asdict(ev) for ev in hud])

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
        if gt is not None and self._last_game_time is not None:
            # game_time_sec 是座舱时钟换算的"当日秒数"(0~86399)，不是单调计时器：
            # 夜战跨越 00:00 时它会从 ~86399 跳回 0。只有"半天以内的倒退"才可能是
            # 回放拖时间轴；接近一整天的倒退是午夜回绕，等价于正常前进。
            backwards = self._last_game_time - gt
            if _REPLAY_TIME_BACK_SEC < backwards < _MIDNIGHT_WRAP_MIN_BACK_SEC:
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

        进入：combat.my.deaths 较上次增加（解析到本人新阵亡）；或陆战中可信的乘员
              数降至 1（crew_total>=2 且 crew_current<=1，载具已无法继续作战）。
        退出：在更早的帧先见载具静止/残骸化（_dead_inert_seen），再满足以下任一“复活”信号：
              - 恢复运动（空中 IAS>阈值 / 地面速度>阈值）= 重生起飞/行驶；
              - 阵亡后曾见陆战乘员未满，随后恢复满员 = 新车。
        “先静止再活跃”的两段式可正确区分“死亡俯冲(高速但已死)”与“重生”，避免在
        坠落途中误判复活而提前解除抑制。

        返回 True 表示本帧确认由阵亡态进入新一次出生，调用方需重置处理器状态。
        """
        combat = self._combat
        deaths = 0
        if isinstance(combat, dict):
            deaths = (combat.get("my") or {}).get("deaths") or 0
        army = str(getattr(ind, "army", "") or "").strip().lower()
        crew = getattr(ind, "crew_current", None)
        crew_total = getattr(ind, "crew_total", None)
        ground_crew_knockout = (
            army in {"tank", "ground"}
            and crew is not None
            and crew_total is not None
            and crew_total >= 2
            and crew <= 1
        )
        entered_dead = False
        if not self._dead and (deaths > self._last_deaths or ground_crew_knockout):
            self._dead = True
            self._dead_since = now
            self._dead_inert_seen = False
            self._dead_crew_depleted_seen = False
            self._dead_source = "hud_event" if deaths > self._last_deaths else "ground_crew"
            entered_dead = True
        self._last_deaths = deaths
        if not self._dead:
            return False
        inert_seen_before = self._dead_inert_seen
        ias = processed.get("ias_kmh") if isinstance(processed, dict) else None
        gspeed = getattr(ind, "speed", None)
        inert = ((ias is None or ias < _DEAD_INERT_IAS_KMH)
                 and (gspeed is None or abs(gspeed) < _DEAD_INERT_SPEED_MS))
        if inert:
            self._dead_inert_seen = True
        moving = ((ias is not None and ias > _DEAD_ALIVE_IAS_KMH)
                  or (gspeed is not None and abs(gspeed) > _DEAD_ALIVE_SPEED_MS))
        crew_full = (crew is not None and crew_total is not None
                     and crew_total >= 2 and crew >= crew_total)
        if (
            army in {"tank", "ground"}
            and crew is not None
            and crew_total is not None
            and crew_total >= 2
            and crew < crew_total
        ):
            self._dead_crew_depleted_seen = True
        crew_recovered = (
            army in {"tank", "ground"}
            and self._dead_crew_depleted_seen
            and crew_full
        )
        if not entered_dead and inert_seen_before and (moving or crew_recovered):
            self._dead = False
            self._dead_since = None
            self._dead_inert_seen = False
            self._dead_crew_depleted_seen = False
            self._dead_source = None
            self._life_index = max(1, self._life_index or 1) + 1
            return True
        return False

    def _reset_battle_cache_locked(self, *, preserve_terminal_handoff: bool = False) -> None:
        """离开战局时清空本局相关缓存（调用方需已持锁）。"""
        terminal_status: str | None = None
        terminal_objectives: Any = None
        terminal_combat: dict[str, Any] | None = None
        if preserve_terminal_handoff:
            pending_is_terminal = (
                str(self._pending_terminal_status or "").strip().lower()
                in _TERMINAL_MISSION_STATUSES
            )
            visible_is_terminal = (
                str(self._mission_status or "").strip().lower()
                in _TERMINAL_MISSION_STATUSES
            )
            if pending_is_terminal:
                terminal_status = self._pending_terminal_status
                terminal_objectives = self._pending_terminal_objectives
            elif visible_is_terminal:
                terminal_status = self._mission_status
                terminal_objectives = self._mission_objectives
                if isinstance(self._combat, dict):
                    terminal_combat = self._combat
        # 录制标记：一次会话可跨多局，靠此标记供离线工具按局切分
        self.recorder.mark({"_event": "battle_reset"})
        self._map_objects = []
        self._map_info = MapInfo(valid=False)
        self._mission_status = None
        self._mission_objectives = None
        self._pending_terminal_status = None
        self._pending_terminal_objectives = None
        self._terminal_handoff_active = False
        self._hud_recovery_cursor = None
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
        self._dead_crew_depleted_seen = False
        self._dead_source = None
        self._last_deaths = 0
        self._battle_id = None
        self._life_index = None
        if terminal_status is not None:
            self._mission_status = terminal_status
            self._mission_objectives = terminal_objectives
            self._combat = terminal_combat
            self._terminal_handoff_active = True

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
            data["battle_id"] = self._battle_id
            data["battle_started_at"] = self._battle_entry_ts
            data["life_index"] = self._life_index
            data["confirmed_respawns"] = max(0, (self._life_index or 1) - 1)
            # 阵亡待命态：玩家被击杀后→重生/观战窗口。告警已在 _poll_fast 抑制；这里再把
            # 依赖“自身位置”的态势/接近置空（观战时坐标漂到被观战者，数据失真）。战绩保留
            # （HUD 带全局名字戳，不会被污染，前端仍可展示最终 K/D / 谁击杀了你）。
            data["dead"] = self._dead
            data["dead_source"] = self._dead_source
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
            requested = (name or "").strip() or None
            self._manual_player_name = requested
            self._name_req = requested

    def get_identity(self) -> dict[str, Any]:
        """返回身份状态；手动昵称跨局保留，不依赖本局战绩缓存。"""
        with self._lock:
            if self._manual_player_name:
                identity = {
                    "name": self._manual_player_name,
                    "source": "manual",
                    "confidence": 1.0,
                }
                return {"self": identity, "player_name": self._manual_player_name}
            combat = self._combat if isinstance(self._combat, dict) else {}
            return {
                "self": combat.get("self"),
                "player_name": combat.get("player_name"),
            }

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
                "dead_source": self._dead_source,
                "battle_id": self._battle_id,
                "battle_started_at": self._battle_entry_ts,
                "life_index": self._life_index,
                "confirmed_respawns": max(0, (self._life_index or 1) - 1),
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
        origin = str(self.headers.get("Origin") or "").strip()
        server = getattr(self, "server", None)
        allowed_origins = getattr(server, "cors_origins", frozenset()) if server else frozenset()
        if origin and origin in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", _ACTION_HEADER)
            self.send_header("Vary", "Origin")

    def _host_allowed(self) -> bool:
        bind_host = str(getattr(self.server, "bind_host", "")).strip().lower()
        if not _is_loopback_host(bind_host):
            return True
        try:
            request_host = urlparse(f"//{self.headers.get('Host', '')}").hostname or ""
        except ValueError:
            return False
        return _is_loopback_host(request_host)

    def _action_allowed(self) -> bool:
        if self.headers.get(_ACTION_HEADER) == "1":
            return True
        self._send_json({"error": "action_header_required"}, 403)
        return False

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
        if not self._host_allowed():
            self._send_json({"error": "host_not_allowed"}, 403)
            return
        origin = str(self.headers.get("Origin") or "").strip()
        allowed_origins = getattr(self.server, "cors_origins", frozenset())
        if origin and origin not in allowed_origins:
            self._send_json({"error": "origin_not_allowed"}, 403)
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._send_json({"error": "host_not_allowed"}, 403)
            return
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
            if ("clear" in q or q.get("name")) and not self._action_allowed():
                return
            requested: Any = "(unchanged)"
            if "clear" in q:
                self.service.set_player_name(None)
                requested = None
            elif q.get("name"):
                requested = q["name"][0]
                self.service.set_player_name(requested)
            identity = self.service.get_identity()
            self._send_json({
                "requested": requested,
                "note": "手动身份立即持久显示；战绩归属将在下一次 events 轮询（≤event-interval）后应用",
                **identity,
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
            if "on" in q and not self._action_allowed():
                return
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


def _is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def create_http_server(host: str, port: int, *, cors_origins: list[str] | tuple[str, ...] = ()):
    server_class = ThreadingHTTPServer
    if ":" in host:
        class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server_class = IPv6ThreadingHTTPServer
    server = server_class((host, port), _Handler)
    server.bind_host = host  # type: ignore[attr-defined]
    server.cors_origins = frozenset(  # type: ignore[attr-defined]
        str(origin).strip() for origin in cors_origins if str(origin).strip()
    )
    return server


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="战雷遥测后台服务（分频轮询）")
    parser.add_argument("--host", default=DEFAULT_BIND_HOST, help="服务监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8112, help="对外服务端口（默认 8112）")
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help="允许读取遥测的浏览器 Origin；可重复指定，默认不允许跨域",
    )
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
                             "也可运行时用 GET /api/identity?name=xxx 设置，修改请求需携带 "
                             "X-Neko-Warthunder-Action: 1")
    parser.add_argument("--record", action="store_true",
                        help="启动即开启数据转存（调试开关；也可运行时 GET /api/record?on=1 切换，"
                             "修改请求需携带 X-Neko-Warthunder-Action: 1）")
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

    httpd = create_http_server(args.host, args.port, cors_origins=args.cors_origin)
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
        httpd.server_close()
        service.stop()
        print("已停止。")


if __name__ == "__main__":
    main()
