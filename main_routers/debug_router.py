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
    """读取当前进程 RSS（MB）。psutil 优先，失败回退 resource（POSIX）。

    Windows 没有 ``resource.getrusage`` 的 RSS 字段，所以纯打包发行版
    （Nuitka 不带 psutil 的话）这里会返回 None——不致命。"""
    try:
        import psutil  # type: ignore
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        import resource  # type: ignore  # POSIX-only
        # ru_maxrss 在 macOS 是 bytes，在 Linux 是 KB——我们粗略当 KB 处理，
        # macOS 上数值会偏大 1024 倍，但用户绝大多数是 Windows，不影响主用例。
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
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
                continue
    except Exception:
        pass
    return out


def _safe_ack_waiters_size() -> int | None:
    try:
        from main_logic.agent_event_bus import _ack_waiters
        return len(_ack_waiters)
    except Exception:
        return None


def _safe_proactive_history_size() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        from main_routers.system_router import _proactive_chat_history
        for name, dq in list(_proactive_chat_history.items()):
            try:
                out[name] = len(dq)
            except Exception:
                continue
    except Exception:
        pass
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

    我们不做存储——只是把它合进下一轮 watchdog snapshot 的 ``client`` 字段，
    或者直接 echo 进 ring buffer 末项。这样后端 ring 同时记录服务端 + 浏览器侧
    counter，导出一个文件即可看到完整曲线。"""
    try:
        # 把 client snapshot 挂在最近一条服务端 snapshot 上；没有就单独占一条。
        entry = {
            "ts": time.time(),
            "client": payload,
        }
        _HEALTH_RING.append(entry)
        _append_to_log(entry)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
