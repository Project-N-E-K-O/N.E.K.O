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


def _append_to_log(snap: dict[str, Any]) -> None:
    path = _resolve_log_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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

    若 client 没启用（用户没开 localStorage），这里啥也不做，ring 全是
    server-only entry，~200 条 ≈ 16 小时不变。"""
    if not _HEALTH_RING:
        return
    last = _HEALTH_RING[-1]
    # 「client-only」标识：缺 asyncio_tasks 键（server snapshot 永远有）
    if "asyncio_tasks" in last:
        return
    client_payload = last.get("client")
    if client_payload is None:
        return
    # 吸收窗口：一个 watchdog 间隔。本应只有 ~30s 距离，但容忍 client 启动晚
    # / 浏览器 throttle 等导致的偏移。再远就放过，避免吸收上上轮的残留。
    if float(server_snap.get("ts") or 0) - float(last.get("ts") or 0) > _WATCHDOG_INTERVAL_SECONDS:
        return
    server_snap["client"] = client_payload
    _HEALTH_RING.pop()


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


@router.post("/api/debug/health/client")
async def debug_health_client(payload: dict[str, Any]) -> dict[str, Any]:
    """前端 ``debug-health.js`` POST 上来的浏览器侧快照。

    简单 append client-only entry——**不要**往前往回 merge 已存在的 server
    snapshot（曾尝试过，被 codex 指出会把 client sample 绑到 4.5 分钟之前的
    server tick，时间轴错位）。正确语义：client POST 暂存条目，等下一次
    server tick 在 _absorb_recent_client_payload 里把它吸收 + pop，合成一条
    完整的 server+client entry。这样既不错配时间，也不砍 ring 保留期。"""
    try:
        entry = {"ts": time.time(), "client": payload}
        _HEALTH_RING.append(entry)
        _append_to_log(entry)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
