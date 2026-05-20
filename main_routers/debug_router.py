# -*- coding: utf-8 -*-
"""诊断观测：长跑健康指标采集。

背景
----
现场偶发有用户反馈「N.E.K.O 开了两三天后 CPU 慢慢涨到 30%+」。这类「多日
累积、静置触发」的 leak 仅凭静态读代码命中率个位数，必须**复现时拿到运行时
counter 曲线**才能定位。这个 router 干两件事：

1. ``GET /api/debug/health``：返回当前关键 counter 的快照（asyncio 任务数、
   各 lanlan core 的对话历史长度、agent_event_bus._ack_waiters 大小、
   proactive_chat_history 大小、进程 RSS、uptime）。
2. 启动一个 5-min 周期的后台 watchdog 任务，把同样的快照写进一个内存
   ring buffer（保留最近 ~16 小时 = 200 条）。当 ``NEKO_DEBUG_HEALTH_LOG=1``
   时还落盘到 ``<user_data>/debug_health.jsonl``，方便用户把文件发回来画曲线。

设计原则
--------
- **零侵入**：所有 counter 都用 getattr / try-except 容错，本模块挂了不影响主功能。
- **默认开**：endpoint + 内存 ring buffer 永远在跑，单次代价 ~ms 级；文件落盘默认关，
  靠 env 显式开启。后续用户报问题时不需要再发新版本——已有数据可直接捞。
- **不抓隐私**：snapshot 只数大小不读内容；jsonl 里没有任何对话原文（遵循 CLAUDE.md
  「原始对话只能 print 不能 logger」的规则）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 模块级状态
# ---------------------------------------------------------------------------

_PROCESS_START_MONO = time.monotonic()
# Ring buffer：~16 小时（5min × 200）。重启即丢，这是诊断而非审计，可接受。
_HEALTH_RING: deque[dict[str, Any]] = deque(maxlen=200)
_WATCHDOG_TASK: asyncio.Task | None = None
_WATCHDOG_INTERVAL_SECONDS = 5 * 60  # 5 分钟


# ---------------------------------------------------------------------------
# Snapshot 采集
# ---------------------------------------------------------------------------

def _safe_rss_mb() -> float | None:
    """读取当前进程**当前** RSS（MB）；只在 psutil 可用时返回，否则 None。

    历史里曾用 ``resource.getrusage(...).ru_maxrss`` 做 fallback，但那是
    **lifetime peak**——一旦上去就不下降。用来画 leak 趋势会把一次性内存
    高峰永久误读成 leak，比没有这个字段还误导。所以**宁可返回 None**也不
    走 ru_maxrss。打包发行版默认就带 psutil，源码模式 ``uv sync`` 也会装。"""
    try:
        import psutil  # type: ignore
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        # 故意吞：拿不到 RSS 不应拖垮诊断功能。曲线上看 rss_mb=null 就知道是
        # 环境缺 psutil，不会和「真有 leak」混淆。
        return None


def _safe_conv_history_lengths() -> dict[str, int]:
    """枚举所有 lanlan core 的 _conversation_history 长度。

    任何一个 lanlan 抓不到都跳过——shared_state 在启动早期可能还没 ready。"""
    out: dict[str, int] = {}
    try:
        from main_routers.shared_state import get_session_manager
        session_manager = get_session_manager()
        # session_manager 是 _RoleStateFieldView，dict-like
        for name in list(session_manager.keys()):
            try:
                core = session_manager.get(name)
                session = getattr(core, "session", None)
                history = getattr(session, "_conversation_history", None)
                if history is not None:
                    out[name] = len(history)
            except Exception:
                # 单 lanlan 失败不影响其他：可能正在 end_session / hot-swap，
                # core / session 暂态为 None，下一轮自然恢复。
                continue
    except Exception:
        # shared_state 启动早期可能还没 ready；故意吞，零侵入。
        return out
    return out


def _safe_ack_waiters_size() -> int | None:
    try:
        from main_logic.agent_event_bus import _ack_waiters
        return len(_ack_waiters)
    except Exception:
        # 故意吞：agent_event_bus 模块未加载 / 重构改名都允许优雅降级。
        return None


def _safe_proactive_history_size() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        from main_routers.system_router import _proactive_chat_history
        for name, dq in list(_proactive_chat_history.items()):
            try:
                out[name] = len(dq)
            except Exception:
                # 单条 deque 取 len 失败极小概率：跳过不让整轮废。
                continue
    except Exception:
        # 故意吞：system_router 未加载 / 内部命名变更都允许优雅降级。
        return out
    return out


def _collect_snapshot() -> dict[str, Any]:
    """单次快照采集。每个字段独立 try 过——任意一项炸了不影响其他。"""
    snap: dict[str, Any] = {
        "ts": time.time(),
        "uptime_sec": time.monotonic() - _PROCESS_START_MONO,
    }
    try:
        snap["asyncio_tasks"] = len(asyncio.all_tasks())
    except Exception:
        snap["asyncio_tasks"] = None
    snap["rss_mb"] = _safe_rss_mb()
    snap["conv_history"] = _safe_conv_history_lengths()
    snap["ack_waiters"] = _safe_ack_waiters_size()
    snap["proactive_history"] = _safe_proactive_history_size()
    return snap


# ---------------------------------------------------------------------------
# 文件落盘（默认关）
# ---------------------------------------------------------------------------

def _resolve_log_path() -> Path | None:
    """返回 jsonl 落盘路径；未启用时返回 None。

    启用条件：env ``NEKO_DEBUG_HEALTH_LOG`` 为真值。
    路径：config_manager 提供的用户配置目录 / ``debug_health.jsonl``；
    拿不到 config_manager 时退到 sys.executable 同目录。"""
    if os.environ.get("NEKO_DEBUG_HEALTH_LOG", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    try:
        from main_routers.shared_state import get_config_manager
        cm = get_config_manager()
        config_dir = getattr(cm, "config_dir", None)
        if config_dir:
            return Path(config_dir) / "debug_health.jsonl"
    except Exception:
        # shared_state 没 ready / config_manager 未注入：落到下面 sys.argv[0]
        # 兜底路径。本身就是诊断文件，写哪里都比不写好。
        pass
    # 兜底：launcher 旁
    try:
        return Path(sys.argv[0]).resolve().parent / "debug_health.jsonl"
    except Exception:
        return None


# 单文件大小上限。超过则 rotate 到 .1（覆盖旧 .1），总占用硬封 ~20MB。
# 算下来：3 个 lanlan + client merged 行 ≈ 500B，10MB ≈ 21000 行 ≈ 73 天数据，
# 触发 rotation 后还能再写 73 天到新文件——对「报完问题忘关 env」场景完全够用。
_LOG_ROTATE_BYTES = 10 * 1024 * 1024


def _append_to_log(snap: dict[str, Any]) -> None:
    path = _resolve_log_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate：超阈值则 os.replace 到 .1。os.replace 在 Windows / POSIX 都
        # 原子，且会原子覆盖已有 .1——所以总占用 = current + .1 ≤ 2×10MB。
        # 用 path.name + ".1" 而不是 with_suffix('.1')——后者会把 .jsonl 替成
        # .1 得到 debug_health.1，丢了 .jsonl 后缀。
        try:
            if path.exists() and path.stat().st_size > _LOG_ROTATE_BYTES:
                os.replace(path, path.parent / (path.name + ".1"))
        except OSError as e:
            # rotation 失败不挂主路径，让 append 照旧写——大不了文件继续涨
            # 一阵子，下次 tick 还会再试。
            logger.debug("debug_health: rotate failed: %s", e)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    except Exception as e:
        # 文件写失败不抛——诊断功能不能拖垮主程序
        logger.debug("debug_health: append jsonl failed: %s", e)


# ---------------------------------------------------------------------------
# Watchdog 后台任务
# ---------------------------------------------------------------------------

def _absorb_recent_client_payload(server_snap: dict[str, Any]) -> None:
    """Server tick 时回头吸收最近一条 client-only entry（如果在窗口内）。

    时序约束：debug-health.js 首次 POST 在 t=30s，watchdog 首次 tick 在 t=60s，
    之后两边都按 5min 节奏跑——所以 client POST 通常落在「下一个」server tick
    **之前** 30s 左右。client POST 端选择 append client-only entry 暂存；server
    tick 此处主动吸收：把暂存的 client payload merge 到当前 server snapshot，
    并把暂存条目从 ring 里 pop 掉（避免砍半 ring 保留期）。

    多条 client-only：同一周期内可能有多条 client POST（多标签页 / beforeunload
    补发 + 定时上报）。while 循环把尾部连续 client-only 全部 pop，避免落下孤儿；
    最终只保留**最新**一条 payload merge 进 server snapshot——最新 = 最后到达
    = 最贴近 server tick 时点，诊断价值最高。

    若 client 没启用（用户没开 localStorage），这里啥也不做，ring 全是
    server-only entry，~200 条 ≈ 16 小时不变。"""
    absorbed_client: dict[str, Any] | None = None
    server_ts = float(server_snap.get("ts") or 0)
    # 倒序消化尾部连续 client-only 条目；保留最新（第一次循环取到的）payload。
    while _HEALTH_RING:
        last = _HEALTH_RING[-1]
        # 「client-only」标识：缺 asyncio_tasks 键（server snapshot 永远有）
        if "asyncio_tasks" in last:
            break
        # 超出吸收窗口的属于「上上轮残留」，不动它（也不吸收 payload）让 ring
        # 自然按 maxlen 排出，避免强行 pop 破坏历史顺序。
        if server_ts - float(last.get("ts") or 0) > _WATCHDOG_INTERVAL_SECONDS:
            break
        if absorbed_client is None and last.get("client") is not None:
            absorbed_client = last["client"]
        _HEALTH_RING.pop()
    if absorbed_client is not None:
        server_snap["client"] = absorbed_client


async def _watchdog_loop() -> None:
    """5-min 周期采样。任何单轮异常吞掉继续——多日跑下来不能因为一次失败掉队。"""
    # 启动后先睡一段，避开冷启动 noise（asyncio task 数在 startup 阶段会高一下）。
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        return
    while True:
        try:
            snap = _collect_snapshot()
            _absorb_recent_client_payload(snap)
            _HEALTH_RING.append(snap)
            _append_to_log(snap)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("debug_health watchdog single tick error: %s", e)
        try:
            await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return


def start_watchdog() -> None:
    """由 main_server startup 调用。幂等：重复调用不会创建第二个 task。"""
    global _WATCHDOG_TASK
    if _WATCHDOG_TASK is not None and not _WATCHDOG_TASK.done():
        return
    try:
        _WATCHDOG_TASK = asyncio.create_task(_watchdog_loop(), name="debug_health_watchdog")
        logger.info("debug_health watchdog started (interval=%ds, log_file=%s)",
                    _WATCHDOG_INTERVAL_SECONDS, _resolve_log_path())
    except RuntimeError:
        # 没有 running loop——startup 路径不该走到这里
        logger.warning("debug_health: no running loop, watchdog NOT started")


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------

@router.get("/api/debug/health")
async def debug_health() -> dict[str, Any]:
    """返回当前快照 + 最近 ring buffer。

    Ring buffer 让用户不用等到下一个 5-min tick——任意时刻请求都能拿到
    最近 16 小时的曲线，刷新即用。"""
    current = _collect_snapshot()
    return {
        "current": current,
        "ring": list(_HEALTH_RING),
        "ring_capacity": _HEALTH_RING.maxlen,
        "watchdog_interval_sec": _WATCHDOG_INTERVAL_SECONDS,
        "log_path": str(_resolve_log_path()) if _resolve_log_path() else None,
    }


# 端点接受的客户端 payload 白名单。HTTP 边界做这层约束有两个理由：
# (1) 协议契约——「只记计数」必须在边界强制而不是依赖前端自觉，否则任何调用方
#     都能往 ring/jsonl 写入大对象或敏感字段；
# (2) 文件占用——单次 payload bound 住，长跑 jsonl 不会被异常调用爆出 GB 级。
# 字段名跟 static/debug-health.js 的 ``collectSnapshot()`` 同步。新增字段时
# 两边一起改，未在白名单的会被静默丢弃。
_CLIENT_NUMERIC_FIELDS = frozenset({
    "ts", "live_intervals", "live_timeouts", "raf_fps_60s",
    "dom_nodes", "js_heap_mb", "ws_state",
    "proactive_backoff_level", "agent_task_map_size",
})
_CLIENT_BOOL_FIELDS = frozenset({"proactive_running", "is_recording"})
_CLIENT_STRING_FIELDS_WITH_CAP = {"location": 128}


def _sanitize_client_payload(raw: Any) -> dict[str, Any]:
    """裁剪客户端 payload 到白名单字段；非预期类型 / 超长字符串丢弃或截断。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k in _CLIENT_NUMERIC_FIELDS:
            if v is None:
                out[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                # 拒 NaN / Infinity / 1e10000：stdlib json 默认会输出 "NaN" /
                # "Infinity" 字面量（非标准 JSON），前端 JSON.parse / 第三方
                # jsonl 工具直接挂。try-except 同时兜超大 int（10**500 等）：
                # math.isfinite 内部把 int 转 float 时会 OverflowError，那种
                # 数字在「只记计数」语义里也不该出现，一并丢。
                try:
                    if math.isfinite(v):
                        out[k] = v
                except (OverflowError, TypeError):
                    # 故意丢：超大 int / 异常 numeric subtype 进不来 isfinite。
                    # 「只记计数」语义里本来不该出现，丢弃即可，不影响其他字段。
                    pass
        elif k in _CLIENT_BOOL_FIELDS:
            if isinstance(v, bool):
                out[k] = v
        elif k in _CLIENT_STRING_FIELDS_WITH_CAP:
            if isinstance(v, str):
                cap = _CLIENT_STRING_FIELDS_WITH_CAP[k]
                out[k] = v[:cap]
        # 其余字段静默丢弃——「只记计数」契约由这里强制
    return out


@router.post("/api/debug/health/client")
async def debug_health_client(payload: dict[str, Any]) -> dict[str, Any]:
    """前端 ``debug-health.js`` POST 上来的浏览器侧快照。

    流程：
    - 边界白名单裁剪（``_sanitize_client_payload``），强制「只记计数」契约。
    - Append client-only entry 到内存 ring 暂存——**不**立刻写 jsonl，避免
      下次 server tick 吸收时 jsonl 出现「暂存 + 合并」双行污染时间轴。
    - 写 jsonl 的责任完全交给 watchdog：吸收成功就一条 merged 行，吸收
      不到（窗口外或 server 未启）也会被 ring 自然 drop。

    错配修复历史：曾尝试在这里倒序找 server entry merge，被 codex 指出会
    把 client sample 绑到 4.5min 前的旧 snapshot，时间轴错位 270s。所以
    改成「server tick 反向吸收」，详见 ``_absorb_recent_client_payload``。"""
    try:
        sanitized = _sanitize_client_payload(payload)
        entry = {"ts": time.time(), "client": sanitized}
        _HEALTH_RING.append(entry)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
