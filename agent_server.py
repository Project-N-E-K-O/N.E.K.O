# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mimetypes
import json
mimetypes.add_type("application/javascript", ".js")
import asyncio
import uuid
import logging
import time
import hashlib
from typing import Dict, Any, Optional, ClassVar, List
from datetime import datetime, timezone
import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from utils.logger_config import setup_logging, ThrottledLogger

# Configure logging as early as possible so import-time failures are persisted.
logger, log_config = setup_logging(service_name="Agent", log_level=logging.INFO)

from config import TOOL_SERVER_PORT, USER_PLUGIN_SERVER_PORT, OPENFANG_BASE_URL
from utils.config_manager import get_config_manager
from main_logic.agent_event_bus import AgentServerEventBridge
try:
    from brain.computer_use import ComputerUseAdapter
    from brain.browser_use_adapter import BrowserUseAdapter
    from brain.openclaw_adapter import OpenClawAdapter
    from brain.openfang_adapter import OpenFangAdapter
    from brain.deduper import TaskDeduper
    from brain.task_executor import DirectTaskExecutor
    from brain.agent_session import get_session_manager
    from brain.result_parser import (
        parse_computer_use_result,
        parse_browser_use_result,
        parse_plugin_result,
        _phrase as _rp_phrase,
        _get_lang as _rp_lang,
    )
except Exception as e:
    logger.exception(f"[Agent] Module import failed during startup: {e}")
    raise


app = FastAPI(title="N.E.K.O Tool Server")

class Modules:
    computer_use: ComputerUseAdapter | None = None
    browser_use: BrowserUseAdapter | None = None
    openclaw: OpenClawAdapter | None = None
    openfang: OpenFangAdapter | None = None
    deduper: TaskDeduper | None = None
    task_executor: DirectTaskExecutor | None = None
    user_plugin_app: FastAPI | None = None
    user_plugin_http_server: Any = None
    user_plugin_http_task: Any = None  # threading.Thread (imported after class def)
    _plugin_server_loop: Any = None
    plugin_lifecycle_started: bool = False
    _plugin_lifecycle_lock: Optional[asyncio.Lock] = None
    # Task tracking
    task_registry: Dict[str, Dict[str, Any]] = {}
    executor_reset_needed: bool = False
    analyzer_enabled: bool = False
    analyzer_profile: Dict[str, Any] = {}
    # Computer-use exclusivity and scheduling
    computer_use_queue: Optional[asyncio.Queue] = None
    computer_use_running: bool = False
    active_computer_use_task_id: Optional[str] = None
    active_computer_use_async_task: Optional[asyncio.Task] = None
    # Browser-use task tracking
    active_browser_use_task_id: Optional[str] = None
    active_browser_use_bg_task: Optional[asyncio.Task] = None
    # Agent feature flags (controlled by UI)
    agent_flags: Dict[str, Any] = {
        "computer_use_enabled": False,
        "browser_use_enabled": False,
        "user_plugin_enabled": False,
        "openclaw_enabled": False,
        "openfang_enabled": False,
    }
    # Notification queue for frontend (one-time messages)
    notification: Optional[str] = None
    # 使用统一的速率限制日志记录器（业务逻辑层面）
    throttled_logger: "ThrottledLogger" = None  # 延迟初始化
    agent_bridge: AgentServerEventBridge | None = None
    state_revision: int = 0
    # Serialize analysis+dispatch to prevent duplicate tasks from concurrent analyze_request events
    analyze_lock: Optional[asyncio.Lock] = None
    # Per-lanlan fingerprint of latest user-turn payload already consumed by analyzer
    last_user_turn_fingerprint: ClassVar[Dict[str, str]] = {}
    capability_cache: Dict[str, Dict[str, Any]] = {
        "computer_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "browser_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "user_plugin": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "openclaw": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "openfang": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
    }
    _background_tasks: ClassVar[set] = set()
    _persistent_tasks: ClassVar[set] = set()
    # Cancellable background task handles by logical task_id
    task_async_handles: ClassVar[Dict[str, asyncio.Task]] = {}


# 插件名称缓存（避免频繁 HTTP 调用）
import threading
_plugin_name_cache: Dict[str, str] = {}
_plugin_name_cache_time: float = 0.0
_plugin_name_cache_lock = threading.Lock()
PLUGIN_NAME_CACHE_TTL: float = 30.0  # 缓存 30 秒
TASK_REGISTRY_CLEANUP_TTL: float = 300.0  # 已完成任务保留 5 分钟
DEFERRED_TASK_TIMEOUT: float = 3600.0  # deferred 任务超时 1 小时
_task_registry_last_cleanup: float = 0.0


def _default_openclaw_task_description() -> str:
    return _rp_phrase('openclaw_processing', _rp_lang(None))


def _cleanup_task_registry() -> List[Dict[str, Any]]:
    """清理 task_registry 中超过 5 分钟的已完成/失败/取消任务，防止内存泄漏；同时检查 deferred 任务超时

    返回超时的 deferred 任务列表（需要发送 task_update 通知前端）
    """
    global _task_registry_last_cleanup
    now = time.time()
    timed_out: List[Dict[str, Any]] = []
    if now - _task_registry_last_cleanup < 60:  # 最多每 60 秒清理一次
        return timed_out
    _task_registry_last_cleanup = now
    to_remove = []
    for tid, info in Modules.task_registry.items():
        st = info.get("status")

        # 检查 deferred 任务是否超时（防止绑定失败导致任务永远卡在 running）
        if st == "running" and info.get("deferred_timeout"):
            if now > info.get("deferred_timeout", float('inf')):
                logger.warning("[TaskRegistry] Deferred task %s timed out, marking as failed", tid)
                info["status"] = "failed"
                info["end_time"] = _now_iso()
                info["error"] = "Deferred task timeout (callback not received)"
                # 收集超时任务，需要通知前端
                timed_out.append({
                    "id": tid,
                    "status": "failed",
                    "type": info.get("type"),
                    "start_time": info.get("start_time"),
                    "end_time": info.get("end_time"),
                    "error": info.get("error"),
                    "params": info.get("params", {}),
                    "lanlan_name": info.get("lanlan_name"),
                })
                continue

        if st not in ("completed", "failed", "cancelled"):
            continue
        end_time_str = info.get("end_time")
        if end_time_str:
            try:
                end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - end_dt).total_seconds()
                if age > TASK_REGISTRY_CLEANUP_TTL:
                    to_remove.append(tid)
            except Exception:
                to_remove.append(tid)  # 解析失败的旧条目直接清理
        else:
            # 没有 end_time 的终态任务，用 start_time 估算
            start_str = info.get("start_time", "")
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - start_dt).total_seconds()
                if age > TASK_REGISTRY_CLEANUP_TTL * 2:  # 宽松一点
                    to_remove.append(tid)
            except Exception:
                pass
    for tid in to_remove:
        del Modules.task_registry[tid]
    if to_remove:
        logger.debug("[TaskRegistry] Cleaned up %d completed tasks", len(to_remove))
    return timed_out


def _bind_deferred_task(plugin_id: str, reminder_id: str, agent_task_id: str) -> None:
    """通过插件服务将 agent_task_id 关联到提醒记录，供 daemon 触发时回调使用。
    bind_task 是快速操作（只写文件），触发 run 后短暂轮询等待完成。"""
    try:
        import time as _time
        with httpx.Client(timeout=5.0, proxy=None, trust_env=False) as client:
            # 1. 触发 bind_task entry
            resp = client.post(
                f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs",
                json={
                    "plugin_id": plugin_id,
                    "entry_id": "bind_task",
                    "args": {"reminder_id": reminder_id, "agent_task_id": agent_task_id},
                },
            )
            if resp.status_code != 200:
                logger.warning("[Deferred] bind_task start HTTP %s", resp.status_code)
                return
            run_id = resp.json().get("run_id")
            if not run_id:
                return
            # 2. 短暂轮询等待完成（bind_task 应在 <1s 内完成）
            for _ in range(20):
                _time.sleep(0.1)
                r = client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/runs/{run_id}")
                if r.status_code == 200:
                    if r.json().get("status", "") in ("succeeded", "failed", "canceled", "timeout"):
                        break
            logger.info("[Deferred] bind_task done: plugin=%s reminder=%s agent_task=%s", plugin_id, reminder_id, agent_task_id)
    except Exception as e:
        logger.warning("[Deferred] bind failed: plugin=%s reminder=%s error=%s", plugin_id, reminder_id, e)


def _get_plugin_friendly_name(plugin_id: str) -> str | None:
    """获取插件的友好名称（用于 HUD 显示）

    通过 HTTP 调用嵌入式插件服务的 /plugins 端点获取插件列表，
    并使用缓存减少请求次数。使用线程锁保证多线程安全。
    """
    global _plugin_name_cache, _plugin_name_cache_time

    # 检查缓存（加锁读取）
    now = time.time()
    with _plugin_name_cache_lock:
        if _plugin_name_cache and (now - _plugin_name_cache_time) < PLUGIN_NAME_CACHE_TTL:
            return _plugin_name_cache.get(plugin_id)

    # 缓存过期或为空，从嵌入式插件服务获取
    new_cache = {}
    cache_time = now
    try:
        with httpx.Client(timeout=1.0, proxy=None, trust_env=False) as client:
            resp = client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
            if resp.status_code == 200:
                data = resp.json()
                plugins = data.get("plugins", [])
                # 构建新缓存
                for p in plugins:
                    if isinstance(p, dict):
                        pid = p.get("id")
                        pname = p.get("name")
                        if pid and pname:
                            new_cache[pid] = pname
                        elif pid:
                            new_cache[pid] = pid
                # 更新全局缓存（加锁写入）
                with _plugin_name_cache_lock:
                    _plugin_name_cache = new_cache
                    _plugin_name_cache_time = cache_time
                return new_cache.get(plugin_id)
    except Exception as e:
        logger.warning("[AgentServer] Failed to fetch plugin names from port %s: %s", USER_PLUGIN_SERVER_PORT, e)

    # HTTP 调用失败，尝试本地 state（兼容某些部署场景）
    try:
        from plugin.core.state import state
        with state.acquire_plugins_read_lock():
            meta = state.plugins.get(plugin_id)
            if isinstance(meta, dict):
                return meta.get("name") or meta.get("id")
    except Exception:
        pass

    return None


def _rewire_computer_use_dependents() -> None:
    """Keep task_executor in sync after computer_use adapter refresh."""
    try:
        if Modules.task_executor is not None and hasattr(Modules.task_executor, "computer_use"):
            Modules.task_executor.computer_use = Modules.computer_use
    except Exception:
        pass


def _try_refresh_computer_use_adapter(force: bool = False) -> bool:
    """
    Best-effort refresh for computer-use adapter.
    Useful when API key/model settings were fixed after agent_server startup.
    Does NOT block on LLM connectivity — call ``_fire_agent_llm_connectivity_check``
    afterwards to probe the endpoint asynchronously.
    """
    current = Modules.computer_use
    if not force and current is not None and getattr(current, "init_ok", False):
        return True
    try:
        refreshed = ComputerUseAdapter()
        Modules.computer_use = refreshed
        _rewire_computer_use_dependents()
        logger.info("[Agent] ComputerUse adapter rebuilt (connectivity pending)")
        return True
    except Exception as e:
        logger.warning(f"[Agent] ComputerUse adapter refresh failed: {e}")
        return False


def _get_throttled_logger() -> ThrottledLogger:
    throttled = Modules.throttled_logger
    if throttled is None:
        throttled = ThrottledLogger(logger, interval=30.0)
        Modules.throttled_logger = throttled
    return throttled


async def _start_embedded_user_plugin_server() -> None:
    """Start the plugin HTTP server in a dedicated thread with its own event loop.

    This isolates plugin HTTP handling from the agent's main event loop so that
    heavy agent work (LLM calls, task execution, ZMQ) cannot starve plugin
    requests and vice-versa.
    """
    if Modules.user_plugin_http_server is not None:
        return

    _plugin_package_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin")
    if _plugin_package_root not in sys.path:
        sys.path.insert(1, _plugin_package_root)

    try:
        from plugin.server.http_app import build_plugin_server_app
        import uvicorn
    except Exception as exc:
        raise RuntimeError(f"failed to import embedded user plugin server: {exc}") from exc

    if Modules.user_plugin_app is None:
        Modules.user_plugin_app = build_plugin_server_app()

    config = uvicorn.Config(
        Modules.user_plugin_app,
        host="127.0.0.1",
        port=USER_PLUGIN_SERVER_PORT,
        log_config=None,
        backlog=4096,
        timeout_keep_alive=30,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    Modules.user_plugin_http_server = server

    ready = threading.Event()
    startup_error: list[BaseException] = []

    def _run_in_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        Modules._plugin_server_loop = loop

        async def _serve_and_signal():
            task = asyncio.ensure_future(server.serve())
            while not getattr(server, "started", False) and not task.done():
                await asyncio.sleep(0.05)
            if getattr(server, "started", False):
                ready.set()
            await task

        try:
            loop.run_until_complete(_serve_and_signal())
        except Exception as exc:
            startup_error.append(exc)
            logger.warning("[Agent] Embedded plugin server thread exited: %s", exc)
        finally:
            ready.set()  # unblock waiter even on failure
            loop.close()

    t = threading.Thread(target=_run_in_thread, name="plugin-server", daemon=True)
    t.start()
    Modules.user_plugin_http_task = t

    started = await asyncio.to_thread(ready.wait, 10.0)
    if not started or startup_error or not getattr(server, "started", False):
        server.should_exit = True
        detail = str(startup_error[0]) if startup_error else "timeout or server not started"
        raise RuntimeError(f"embedded user plugin server failed: {detail}")

    logger.info("[Agent] Embedded user plugin server started on 127.0.0.1:%s (isolated thread)", USER_PLUGIN_SERVER_PORT)


async def _stop_embedded_user_plugin_server() -> None:
    """Stop the plugin HTTP server running in its dedicated thread."""
    server = Modules.user_plugin_http_server
    thread = Modules.user_plugin_http_task
    Modules.user_plugin_http_server = None
    Modules.user_plugin_http_task = None

    if server is not None:
        server.should_exit = True

    if thread is None:
        return

    await asyncio.to_thread(thread.join, 10.0)
    if thread.is_alive():
        logger.warning("[Agent] Embedded user plugin server thread did not exit in time")
        if server is not None:
            server.force_exit = True


async def _ensure_plugin_lifecycle_started() -> bool:
    """Start the plugin lifecycle (load & run plugins). Returns True on success."""
    if Modules.plugin_lifecycle_started:
        return True
    if Modules._plugin_lifecycle_lock is None:
        Modules._plugin_lifecycle_lock = asyncio.Lock()
    async with Modules._plugin_lifecycle_lock:
        if Modules.plugin_lifecycle_started:
            return True
        try:
            from plugin.server.lifecycle import startup as plugin_lifecycle_startup
            await plugin_lifecycle_startup()
            Modules.plugin_lifecycle_started = True
            logger.info("[Agent] Plugin lifecycle started")
            return True
        except Exception as exc:
            logger.error("[Agent] Plugin lifecycle startup failed: %s", exc)
            return False


async def _ensure_plugin_lifecycle_stopped() -> None:
    """Stop the plugin lifecycle (stop plugin processes, cleanup)."""
    if not Modules.plugin_lifecycle_started:
        return
    if Modules._plugin_lifecycle_lock is None:
        Modules._plugin_lifecycle_lock = asyncio.Lock()
    async with Modules._plugin_lifecycle_lock:
        if not Modules.plugin_lifecycle_started:
            return
        try:
            from plugin.server.lifecycle import shutdown as plugin_lifecycle_shutdown
            await plugin_lifecycle_shutdown()
            logger.info("[Agent] Plugin lifecycle stopped")
        except Exception as exc:
            logger.warning("[Agent] Plugin lifecycle shutdown error: %s", exc)
        finally:
            Modules.plugin_lifecycle_started = False


async def _fire_user_plugin_capability_check() -> None:
    """Probe the user plugin server to determine if user_plugin capability is ready."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0), proxy=None, trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
            if r.status_code == 200:
                data = r.json()
                plugins = data.get("plugins", []) if isinstance(data, dict) else []
                if plugins:
                    _set_capability("user_plugin", True, "")
                    logger.debug("[Agent] UserPlugin capability check passed (%d plugins)", len(plugins))
                else:
                    _set_capability("user_plugin", False, "AGENT_NO_PLUGINS_FOUND")
                    logger.debug("[Agent] UserPlugin capability check: no plugins found")
            else:
                _set_capability("user_plugin", False, "AGENT_PLUGIN_SERVER_ERROR")
                _get_throttled_logger().warning(
                    "user_plugin_capability_check_failed",
                    "[Agent] UserPlugin capability check failed: status %s",
                    r.status_code,
                )
    except Exception as e:
        _set_capability("user_plugin", False, "AGENT_PLUGIN_SERVER_ERROR")
        logger.debug("[Agent] UserPlugin capability check error: %s", e)


_llm_check_lock = asyncio.Lock()


async def _fire_agent_llm_connectivity_check() -> None:
    """Probe the shared Agent-LLM endpoint in a background thread.

    Both ComputerUse and BrowserUse rely on the same ``agent`` model config,
    so a single connectivity check covers both capabilities.  Updates
    ``init_ok`` on the CUA adapter and refreshes the capability cache for
    *both* computer_use and browser_use.

    Uses a lock to prevent concurrent probes from racing.
    """
    if _llm_check_lock.locked():
        return

    async with _llm_check_lock:
        adapter = Modules.computer_use
        if adapter is None:
            return

        def _probe():
            return adapter.check_connectivity()

        try:
            ok = await asyncio.get_event_loop().run_in_executor(None, _probe)
            reason = "" if ok else "AGENT_LLM_UNREACHABLE"
            _set_capability("computer_use", ok, reason)
            bu = Modules.browser_use
            if bu is not None:
                if not ok:
                    _set_capability("browser_use", False, reason)
                elif not getattr(bu, "_ready_import", False):
                    _set_capability("browser_use", False, "AGENT_BROWSER_USE_NOT_INSTALLED")
                else:
                    _set_capability("browser_use", True, "")

            if ok:
                logger.info("[Agent] Agent-LLM connectivity check passed")
            else:
                logger.warning("[Agent] Agent-LLM connectivity check failed: %s", reason)
                if Modules.agent_flags.get("computer_use_enabled"):
                    Modules.agent_flags["computer_use_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_AUTO_DISABLED_COMPUTER", "details": {"reason_code": reason}})
                if Modules.agent_flags.get("browser_use_enabled"):
                    Modules.agent_flags["browser_use_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_AUTO_DISABLED_BROWSER", "details": {"reason_code": reason}})

            _bump_state_revision()
            await _emit_agent_status_update()
        except Exception as e:
            logger.warning("[Agent] Agent-LLM connectivity check error: %s", e)
            _set_capability("computer_use", False, "AGENT_LLM_UNREACHABLE")
            _set_capability("browser_use", False, "AGENT_LLM_UNREACHABLE")
            if Modules.agent_flags.get("computer_use_enabled"):
                Modules.agent_flags["computer_use_enabled"] = False
            if Modules.agent_flags.get("browser_use_enabled"):
                Modules.agent_flags["browser_use_enabled"] = False
            Modules.notification = json.dumps({"code": "AGENT_LLM_CHECK_ERROR"})
            _bump_state_revision()
            await _emit_agent_status_update()


def _bump_state_revision() -> int:
    Modules.state_revision += 1
    return Modules.state_revision


def _set_capability(name: str, ready: bool, reason: str = "") -> None:
    def _normalize_precheck_reason(raw_reason: str) -> str:
        text = str(raw_reason or "").strip()
        if not text:
            return ""
        if text.startswith("AGENT_"):
            return text

        lower = text.lower()
        # Normalize legacy Chinese/English free-text reasons into stable i18n codes.
        if "未检查" in text or "not checked" in lower or "pending" in lower:
            return "AGENT_PRECHECK_PENDING"
        if "模型未配置" in text or "model not configured" in lower:
            return "AGENT_MODEL_NOT_CONFIGURED"
        if "api url 未配置" in lower or "url not configured" in lower:
            return "AGENT_URL_NOT_CONFIGURED"
        if "api key 未配置" in lower or "key not configured" in lower:
            return "AGENT_KEY_NOT_CONFIGURED"
        if "endpoint not configured" in lower or "api 未配置" in lower:
            return "AGENT_ENDPOINT_NOT_CONFIGURED"
        if "pyautogui" in lower and ("not installed" in lower or "未安装" in text):
            return "AGENT_PYAUTOGUI_NOT_INSTALLED"
        if "browser-use" in lower and ("not installed" in lower or "未安装" in text):
            return "AGENT_BROWSER_USE_NOT_INSTALLED"
        if "not initialized" in lower or "初始化失败" in text:
            return "AGENT_NOT_INITIALIZED"
        if "未发现可用插件" in text or "no plugins" in lower:
            return "AGENT_NO_PLUGINS_FOUND"
        if "plugin server" in lower or "插件服务" in text or "user_plugin server responded" in lower:
            return "AGENT_PLUGIN_SERVER_ERROR"
        if "openfang" in lower or "daemon" in lower:
            return "AGENT_OPENFANG_DAEMON_UNREACHABLE"
        if "unreachable" in lower or "连接失败" in text or "connectivity" in lower:
            return "AGENT_LLM_UNREACHABLE"
        return "AGENT_LLM_UNREACHABLE"

    prev = Modules.capability_cache.get(name, {})
    normalized_reason = _normalize_precheck_reason(reason)
    Modules.capability_cache[name] = {"ready": bool(ready), "reason": normalized_reason}
    if prev.get("ready") != bool(ready) or prev.get("reason", "") != normalized_reason:
        _bump_state_revision()


def _collect_existing_task_descriptions(lanlan_name: Optional[str] = None) -> list[tuple[str, str]]:
    """Return list of (task_id, description) for queued/running tasks, optionally filtered by lanlan_name."""
    items: list[tuple[str, str]] = []
    for tid, info in Modules.task_registry.items():
        try:
            if info.get("status") in ("queued", "running"):
                if lanlan_name and info.get("lanlan_name") not in (None, lanlan_name):
                    continue
                params = info.get("params") or {}
                desc = params.get("query") or params.get("instruction") or ""
                if desc:
                    items.append((tid, desc))
        except Exception:
            continue
    return items


async def _is_duplicate_task(query: str, lanlan_name: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Use LLM to judge if query duplicates any existing queued/running task."""
    try:
        if not Modules.deduper:
            return False, None
        candidates = _collect_existing_task_descriptions(lanlan_name)
        res = await Modules.deduper.judge(query, candidates)
        return bool(res.get("duplicate")), res.get("matched_id")
    except Exception as e:
        logger.warning(f"[Agent] Deduper judge failed: {e}")
        return False, None


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


async def _emit_task_result(
    lanlan_name: Optional[str],
    *,
    channel: str,
    task_id: str,
    success: bool,
    summary: str,
    detail: str = "",
    error_message: str = "",
    direct_reply: bool = False,
) -> None:
    """Emit a structured task_result event to main_server."""
    if success:
        status = "completed"
    elif detail:
        status = "partial"
    else:
        status = "failed"
    _SUMMARY_LIMIT = 500
    _DETAIL_LIMIT = 1500
    _ERROR_LIMIT = 500
    await _emit_main_event(
        "task_result",
        lanlan_name,
        text=summary[:_SUMMARY_LIMIT],
        task_id=task_id,
        channel=channel,
        status=status,
        success=success,
        summary=summary[:_SUMMARY_LIMIT],
        detail=detail[:_DETAIL_LIMIT] if detail else "",
        error_message=error_message[:_ERROR_LIMIT] if error_message else "",
        direct_reply=direct_reply,
        timestamp=_now_iso(),
    )


def _lookup_llm_result_fields(plugin_id: str, entry_id: Optional[str]) -> Optional[list]:
    """从 plugin_list 中查找指定 entry 的 llm_result_fields 声明。"""
    try:
        plugins = getattr(Modules.task_executor, "plugin_list", None) or []
        for p in plugins:
            if not isinstance(p, dict) or p.get("id") != plugin_id:
                continue
            for e in p.get("entries") or []:
                if not isinstance(e, dict):
                    continue
                if e.get("id") == entry_id:
                    fields = e.get("llm_result_fields")
                    return list(fields) if isinstance(fields, list) else None
            break
    except Exception as e:
        logger.debug("_lookup_llm_result_fields failed: plugin_id=%s entry_id=%s error=%s", plugin_id, entry_id, e)
    return None


def _is_reply_suppressed(result: Optional[Dict]) -> bool:
    """检查插件是否通过 meta.agent.reply=False 显式抑制回复。"""
    if not isinstance(result, dict):
        return False
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return False
    agent = meta.get("agent")
    if not isinstance(agent, dict):
        return False
    return agent.get("reply") is False

def _check_agent_api_gate() -> Dict[str, Any]:
    """统一 Agent API 门槛检查。"""
    try:
        cm = get_config_manager()
        ok, reasons = cm.is_agent_api_ready()
        return {"ready": ok, "reasons": reasons, "is_free_version": cm.is_free_version()}
    except Exception as e:
        return {"ready": False, "reasons": [f"Agent API check failed: {e}"], "is_free_version": False}


def _get_plugin_display_id(plugin_id: str) -> str:
    return _get_plugin_friendly_name(plugin_id) or plugin_id


async def _emit_main_event(event_type: str, lanlan_name: Optional[str], **payload) -> None:
    event = {"event_type": event_type, "lanlan_name": lanlan_name, **payload}
    if Modules.agent_bridge:
        try:
            sent = await Modules.agent_bridge.emit_to_main(event)
            if sent:
                return
            logger.debug("[Agent] _emit_main_event not sent: type=%s lanlan=%s (bridge returned False)", event_type, lanlan_name)
        except Exception as e:
            logger.warning("[Agent] _emit_main_event failed: type=%s lanlan=%s error=%s", event_type, lanlan_name, e)
    else:
        logger.debug("[Agent] _emit_main_event skipped: no agent_bridge, type=%s", event_type)


def _collect_agent_status_snapshot() -> Dict[str, Any]:
    gate = _check_agent_api_gate()
    flags = dict(Modules.agent_flags or {})
    capabilities = dict(Modules.capability_cache or {})
    # Periodic cleanup of completed tasks to prevent memory leak
    # Note: _emit_agent_status_update also calls this and handles timed_out tasks
    _cleanup_task_registry()
    # Include active (queued/running) tasks so frontend can restore after page refresh
    active_tasks = []
    for tid, info in Modules.task_registry.items():
        try:
            st = info.get("status")
            if st in ("queued", "running"):
                active_tasks.append({
                    "id": tid,
                    "status": st,
                    "type": info.get("type"),
                    "start_time": info.get("start_time"),
                    "params": info.get("params", {}),
                    "session_id": info.get("session_id"),
                    "lanlan_name": info.get("lanlan_name"),
                })
        except Exception:
            continue
    note = Modules.notification
    if Modules.notification:
        Modules.notification = None
    return {
        "revision": Modules.state_revision,
        "server_online": True,
        "analyzer_enabled": bool(Modules.analyzer_enabled),
        "flags": flags,
        "gate": gate,
        "capabilities": capabilities,
        "active_tasks": active_tasks,
        "notification": note,
        "updated_at": _now_iso(),
    }


def _normalize_lanlan_key(lanlan_name: Optional[str]) -> str:
    name = (lanlan_name or "").strip()
    return name or "__default__"


def _build_user_turn_fingerprint(messages: Any) -> Optional[str]:
    """
    Build a stable fingerprint from user-role messages only.
    Used to ensure analyzer consumes each user turn once.

    Only the message *text* is hashed.  Timestamps and message IDs are
    intentionally excluded because frontends may update these metadata
    fields on re-render, which would produce a different fingerprint for
    the same logical user turn and cause duplicate analysis.
    """
    if not isinstance(messages, list):
        return None
    user_parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        text = str(m.get("text") or m.get("content") or "").strip()
        if text:
            user_parts.append(text)
    if not user_parts:
        return None
    payload = "\n".join(user_parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()


async def _emit_agent_status_update(lanlan_name: Optional[str] = None) -> None:
    try:
        # 先检查超时的 deferred 任务并发送 task_update 通知
        timed_out = _cleanup_task_registry()
        for task_info in timed_out:
            try:
                await _emit_main_event(
                    "task_update",
                    task_info.get("lanlan_name"),
                    task={
                        "id": task_info.get("id"),
                        "status": "failed",
                        "type": task_info.get("type"),
                        "start_time": task_info.get("start_time"),
                        "end_time": task_info.get("end_time"),
                        "error": task_info.get("error"),
                        "params": task_info.get("params", {}),
                    },
                )
            except Exception as e:
                logger.warning("[Agent] Failed to emit task_update for timed-out task %s: %s", task_info.get("id"), e)

        snapshot = _collect_agent_status_snapshot()
        await _emit_main_event(
            "agent_status_update",
            lanlan_name,
            snapshot=snapshot,
        )
    except Exception:
        pass


async def _on_session_event(event: Dict[str, Any]) -> None:
    if (event or {}).get("event_type") == "analyze_request":
        messages = event.get("messages", [])
        lanlan_name = event.get("lanlan_name")
        event_id = event.get("event_id")
        logger.info("[AgentAnalyze] analyze_request received: trigger=%s lanlan=%s messages=%d", event.get("trigger"), lanlan_name, len(messages) if isinstance(messages, list) else 0)
        if event_id:
            ack_task = asyncio.create_task(_emit_main_event("analyze_ack", lanlan_name, event_id=event_id))
            Modules._background_tasks.add(ack_task)
            ack_task.add_done_callback(Modules._background_tasks.discard)
        if not Modules.analyzer_enabled:
            logger.info("[AgentAnalyze] skip: analyzer disabled (master switch off)")
            return
        if isinstance(messages, list) and messages:
            # Consume only new user turn. Assistant turn_end without new user input should be ignored.
            lanlan_key = _normalize_lanlan_key(lanlan_name)
            fp = _build_user_turn_fingerprint(messages)
            if fp is None:
                logger.info("[AgentAnalyze] skip analyze: no user message found (trigger=%s lanlan=%s)", event.get("trigger"), lanlan_name)
                return
            if Modules.last_user_turn_fingerprint.get(lanlan_key) == fp:
                logger.info("[AgentAnalyze] skip analyze: no new user turn (trigger=%s lanlan=%s)", event.get("trigger"), lanlan_name)
                return
            Modules.last_user_turn_fingerprint[lanlan_key] = fp
            conversation_id = event.get("conversation_id")
            task = asyncio.create_task(_background_analyze_and_plan(messages, lanlan_name, conversation_id=conversation_id))
            Modules._background_tasks.add(task)
            task.add_done_callback(Modules._background_tasks.discard)



def _spawn_task(kind: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """生成 computer_use 任务条目并入队等待独占执行。"""
    task_id = str(uuid.uuid4())
    info = {
        "id": task_id,
        "type": kind,
        "status": "queued",
        "start_time": _now_iso(),
        "params": args,
        "result": None,
        "error": None,
    }
    if kind == "computer_use":
        Modules.task_registry[task_id] = info
        if Modules.computer_use_queue is None:
            Modules.computer_use_queue = asyncio.Queue()
        Modules.computer_use_queue.put_nowait({
            "task_id": task_id,
            "instruction": args.get("instruction", ""),
        })
        return info
    else:
        raise ValueError(f"Unknown task kind: {kind}")


async def _run_computer_use_task(
    task_id: str,
    instruction: str,
) -> None:
    """Run a computer-use task in a thread pool; emit results directly via ZeroMQ."""
    info = Modules.task_registry.get(task_id, {})
    lanlan_name = info.get("lanlan_name")

    # Mark running
    info["status"] = "running"
    info["start_time"] = _now_iso()
    Modules.computer_use_running = True
    Modules.active_computer_use_task_id = task_id

    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={
                "id": task_id, "status": "running", "type": "computer_use",
                "start_time": info["start_time"], "params": info.get("params", {}),
            },
        )
    except Exception as e:
        logger.debug("[ComputerUse] emit task_update(running) failed: task_id=%s error=%s", task_id, e)

    # Execute in thread pool (run_instruction is synchronous/blocking)
    success = False
    cu_detail = ""
    loop = asyncio.get_running_loop()

    try:
        if Modules.computer_use is None or not hasattr(Modules.computer_use, "run_instruction"):
            success = False
            cu_detail = "ComputerUse adapter is inactive or invalid (e.g., reset)"
            info["error"] = cu_detail
            logger.error("[ComputerUse] Task %s aborted: %s", task_id, cu_detail)
        else:
            session_id = info.get("session_id")
            future = loop.run_in_executor(None, Modules.computer_use.run_instruction, instruction, session_id)
            res = await future
            if res is None:
                logger.debug("[ComputerUse] run_instruction returned None, treating as success")
                res = {"success": True}
            elif isinstance(res, dict) and "success" not in res:
                res["success"] = True
            success = bool(res.get("success", False))
            info["result"] = res
            _cu_ok, cu_detail = parse_computer_use_result(res)
    except asyncio.CancelledError:
        info["error"] = "Task was cancelled"
        logger.info("[ComputerUse] Task %s was cancelled", task_id)
        # The underlying thread may still be running — wait for it to finish
        # so we don't start a new task while pyautogui is still active.
        cu = Modules.computer_use
        if cu is not None and hasattr(cu, "wait_for_completion"):
            finished = await loop.run_in_executor(None, cu.wait_for_completion, 15.0)
            if not finished:
                logger.warning("[ComputerUse] Thread did not stop within 15s after cancel")
    except Exception as e:
        info["error"] = str(e)
        logger.error("[ComputerUse] Task %s failed: %s", task_id, e)
    finally:
        info["status"] = "cancelled" if info.get("error") == "Task was cancelled" else ("completed" if success else "failed")
        info["end_time"] = _now_iso()
        # 失败时将解析后的 cu_detail 写入 info["error"]（仅在非异常路径下补全）
        if not success and not info.get("error") and cu_detail:
            info["error"] = cu_detail[:500]
        Modules.computer_use_running = False
        Modules.active_computer_use_task_id = None
        Modules.active_computer_use_async_task = None

        # Emit task_update (terminal state)
        try:
            task_obj = asyncio.create_task(_emit_main_event(
                "task_update", lanlan_name,
                task={
                    "id": task_id, "status": info["status"], "type": "computer_use",
                    "start_time": info.get("start_time"), "end_time": _now_iso(),
                    "error": info.get("error") if not success else None,
                },
            ))
            Modules._background_tasks.add(task_obj)
            task_obj.add_done_callback(Modules._background_tasks.discard)
        except Exception as e:
            logger.debug("[ComputerUse] emit task_update(terminal) failed: task_id=%s error=%s", task_id, e)

        # Emit structured task_result
        try:
            _lang = _rp_lang(None)
            _done = _rp_phrase('cu_status_done', _lang) if success else _rp_phrase('cu_status_ended', _lang)
            params = info.get("params") or {}
            desc = params.get("query") or params.get("instruction") or ""
            if cu_detail and desc:
                summary = _rp_phrase('cu_task_done', _lang, desc=desc, status=_done, detail=cu_detail)
            elif cu_detail:
                summary = _rp_phrase('cu_task_done_no_desc', _lang, status=_done, detail=cu_detail)
            elif desc:
                summary = _rp_phrase('cu_task_desc_only', _lang, desc=desc, status=_done)
            else:
                summary = _rp_phrase('cu_done', _lang) if success else _rp_phrase('cu_fail', _lang)
            task_obj = asyncio.create_task(_emit_task_result(
                lanlan_name,
                channel="computer_use",
                task_id=task_id,
                success=success,
                summary=summary,
                detail=cu_detail if success else "",
                error_message=cu_detail if not success else "",
            ))
            Modules._background_tasks.add(task_obj)
            task_obj.add_done_callback(Modules._background_tasks.discard)
        except Exception as e:
            logger.debug("[ComputerUse] emit task_result failed: task_id=%s error=%s", task_id, e)

async def _computer_use_scheduler_loop():
    """Ensure only one computer-use task runs at a time by scheduling queued tasks."""
    if Modules.computer_use_queue is None:
        Modules.computer_use_queue = asyncio.Queue()
    while True:
        try:
            await asyncio.sleep(0.05)
            if Modules.computer_use_running:
                continue
            if Modules.computer_use_queue.empty():
                continue
            if not Modules.analyzer_enabled or not Modules.agent_flags.get("computer_use_enabled", False):
                while not Modules.computer_use_queue.empty():
                    try:
                        Modules.computer_use_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                continue
            next_task = await Modules.computer_use_queue.get()
            tid = next_task.get("task_id")
            if not tid or tid not in Modules.task_registry:
                continue
            Modules.active_computer_use_async_task = asyncio.create_task(_run_computer_use_task(
                tid, next_task.get("instruction", ""),
            ))
        except Exception:
            # Never crash the scheduler
            await asyncio.sleep(0.1)


async def _background_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str], conversation_id: Optional[str] = None):
    """
    [简化版] 使用 DirectTaskExecutor 一步完成：分析对话 + 判断执行方式 + 执行任务
    
    简化链条:
    - 旧: Analyzer(LLM#1) → Planner(LLM#2) → 子进程Processor(LLM#3) → MCP调用
    - 新: DirectTaskExecutor(LLM#1) → MCP调用

    Args:
        messages: 对话消息列表
        lanlan_name: 角色名
        conversation_id: 对话ID，用于关联触发事件和对话上下文

    Uses analyze_lock to serialize concurrent calls.  Without this, two
    near-simultaneous analyze_request events can both pass the dedup
    check before either spawns a task, resulting in duplicate execution.
    """
    if not Modules.task_executor:
        logger.warning("[TaskExecutor] task_executor not initialized, skipping")
        return

    # Lazy-init the lock (must happen inside the event loop)
    if Modules.analyze_lock is None:
        Modules.analyze_lock = asyncio.Lock()

    async with Modules.analyze_lock:
        await _do_analyze_and_plan(messages, lanlan_name, conversation_id=conversation_id)


async def _do_analyze_and_plan(messages: list[dict[str, Any]], lanlan_name: Optional[str], conversation_id: Optional[str] = None):
    """Inner implementation, always called under analyze_lock."""
    try:
        if not Modules.analyzer_enabled:
            logger.info("[TaskExecutor] Skipping analysis: analyzer disabled (master switch off)")
            return
        logger.info("[AgentAnalyze] background analyze start: lanlan=%s messages=%d flags=%s analyzer_enabled=%s",
                    lanlan_name, len(messages), Modules.agent_flags, Modules.analyzer_enabled)

        # 一步完成：分析 + 执行
        result = await Modules.task_executor.analyze_and_execute(
            messages=messages,
            lanlan_name=lanlan_name,
            agent_flags=Modules.agent_flags,
            conversation_id=conversation_id
        )

        if result is None:
            return
        
        if not result.has_task:
            reason = getattr(result, "reason", "") or ""
            if "error" in reason.lower() or "timed out" in reason.lower() or "failed" in reason.lower():
                logger.warning("[TaskExecutor] Assessment failed: %s", reason)
                await _emit_main_event(
                    "agent_notification", lanlan_name,
                    text=f"⚠️ Agent评估失败: {reason[:200]}",
                    source="brain",
                    status="error",
                    error_message=reason[:500],
                )
            else:
                logger.debug("[TaskExecutor] No actionable task found")
            return

        if not Modules.analyzer_enabled:
            logger.info("[TaskExecutor] Skipping dispatch: analyzer disabled during analysis")
            return
        
        logger.info(
            "[TaskExecutor] Task: desc='%s', method=%s, tool=%s, entry=%s, reason=%s",
            (result.task_description or "")[:80],
            result.execution_method,
            getattr(result, "tool_name", None),
            getattr(result, "entry_id", None),
            (getattr(result, "reason", "") or "")[:120],
        )
        
        # 处理 MCP 任务（已在 DirectTaskExecutor 中执行完成）
        if result.execution_method == 'mcp':
            if result.success:
                # MCP 任务已成功执行，通知 main_server
                summary = f'你的任务"{result.task_description}"已完成'
                mcp_detail = ""
                if result.result:
                    try:
                        if isinstance(result.result, dict):
                            detail = result.result.get('content', [])
                            if detail and isinstance(detail, list):
                                text_parts = [item.get('text', '') for item in detail if isinstance(item, dict)]
                                mcp_detail = ' '.join(text_parts)
                                if mcp_detail:
                                    summary = f'你的任务"{result.task_description}"已完成：{mcp_detail}'
                        elif isinstance(result.result, str):
                            mcp_detail = result.result
                            summary = f'你的任务"{result.task_description}"已完成：{mcp_detail}'
                    except Exception:
                        pass
                
                try:
                    await _emit_task_result(
                        lanlan_name,
                        channel="mcp",
                        task_id=str(getattr(result, "task_id", "") or ""),
                        success=True,
                        summary=summary,
                        detail=mcp_detail,
                    )
                    logger.info(f"[TaskExecutor] ✅ MCP task completed and notified: {result.task_description}")
                except Exception as e:
                    logger.warning(f"[TaskExecutor] Failed to notify main_server: {e}")
            else:
                logger.error(f"[TaskExecutor] ❌ MCP task failed: {result.error}")
        
        # 处理 ComputerUse 任务（需要通过子进程调度）
        elif result.execution_method == 'computer_use':
            if Modules.agent_flags.get("computer_use_enabled", False):
                # 检查重复
                dup, matched = await _is_duplicate_task(result.task_description, lanlan_name)
                if not dup:
                    # Session management for multi-turn CUA tasks
                    sm = get_session_manager()
                    cu_session = sm.get_or_create(None, "cua")
                    cu_session.add_task(result.task_description)

                    ti = _spawn_task("computer_use", {"instruction": result.task_description, "screenshot": None})
                    ti["lanlan_name"] = lanlan_name
                    ti["session_id"] = cu_session.session_id
                    logger.info(f"[ComputerUse] Scheduled task {ti['id']} (session={cu_session.session_id[:8]}): {result.task_description[:50]}...")
                    try:
                        await _emit_main_event(
                            "task_update",
                            lanlan_name,
                            task={
                                "id": ti.get("id"),
                                "status": ti.get("status"),
                                "type": ti.get("type"),
                                "start_time": ti.get("start_time"),
                                "params": ti.get("params", {}),
                                "session_id": cu_session.session_id,
                            },
                        )
                    except Exception as e:
                        logger.debug("[ComputerUse] emit task_update(running) failed: task_id=%s error=%s", ti.get('id'), e)
                else:
                    logger.info(f"[ComputerUse] Duplicate task detected, matched with {matched}")
            else:
                logger.warning("[ComputerUse] ⚠️ Task requires ComputerUse but it's disabled")

        elif result.execution_method == 'user_plugin':
            # Dispatch: 与 CU/BU 一致，由 agent_server 统一调度执行
            if Modules.agent_flags.get("user_plugin_enabled", False) and Modules.task_executor:
                plugin_id = result.tool_name
                plugin_args = result.tool_args or {}
                entry_id = result.entry_id
                up_start = _now_iso()
                # 获取插件友好名称（用于 HUD 显示）
                plugin_name = _get_plugin_friendly_name(plugin_id)
                logger.info(
                    "[TaskExecutor] Dispatching UserPlugin: plugin_id=%s, entry_id=%s, plugin_name=%s",
                    plugin_id, entry_id, plugin_name,
                )
                # 构建任务参数（包含友好名称）
                task_params = {"plugin_id": plugin_id, "entry_id": entry_id}
                if plugin_name:
                    task_params["plugin_name"] = plugin_name
                if result.task_description:
                    task_params["description"] = result.task_description
                # Register in task_registry (mirrors CU _spawn_task) so GET /tasks can recover on refresh
                Modules.task_registry[result.task_id] = {
                    "id": result.task_id,
                    "type": "user_plugin",
                    "status": "running",
                    "start_time": up_start,
                    "params": task_params,
                    "lanlan_name": lanlan_name,
                    "result": None,
                    "error": None,
                }
                # Emit task_update (running) so AgentHUD shows a running card
                try:
                    _initial_task_payload: Dict[str, Any] = {
                        "id": result.task_id, "status": "running", "type": "user_plugin",
                        "start_time": up_start,
                        "params": task_params,
                    }
                    await _emit_main_event("task_update", lanlan_name, task=_initial_task_payload)
                except Exception as emit_err:
                    logger.debug("[TaskExecutor] emit task_update(running) failed: task_id=%s plugin_id=%s error=%s", result.task_id, plugin_id, emit_err)
                async def _on_plugin_progress(
                    *, progress=None, stage=None, message=None, step=None, step_total=None,
                ):
                    """Forward run progress updates to NEKO frontend via task_update."""
                    task_payload: Dict[str, Any] = {
                        "id": result.task_id, "status": "running", "type": "user_plugin",
                        "start_time": up_start,
                        "params": task_params,
                    }
                    if progress is not None:
                        task_payload["progress"] = progress
                    if stage is not None:
                        task_payload["stage"] = stage
                    if message is not None:
                        task_payload["message"] = message
                    if step is not None:
                        task_payload["step"] = step
                    if step_total is not None:
                        task_payload["step_total"] = step_total
                    await _emit_main_event("task_update", lanlan_name, task=task_payload)

                async def _run_user_plugin_dispatch():
                    try:
                        up_result = await Modules.task_executor._execute_user_plugin(
                            task_id=result.task_id,
                            plugin_id=plugin_id,
                            plugin_args=plugin_args if isinstance(plugin_args, dict) else None,
                            entry_id=entry_id,
                            task_description=result.task_description,
                            reason=result.reason,
                            lanlan_name=lanlan_name,
                            conversation_id=conversation_id,
                            on_progress=_on_plugin_progress,
                        )
                        up_terminal = "completed" if up_result.success else "failed"
                        run_data = up_result.result.get("run_data") if isinstance(up_result.result, dict) else None
                        run_error = up_result.result.get("run_error") if isinstance(up_result.result, dict) else None
                        _llm_fields = _lookup_llm_result_fields(plugin_id, entry_id)
                        _plugin_msg = str(up_result.result.get("message") or "") if isinstance(up_result.result, dict) else ""
                        _error_to_pass = (run_error or up_result.error) if not up_result.success else None
                        detail = parse_plugin_result(
                            run_data,
                            llm_result_fields=_llm_fields,
                            plugin_message=_plugin_msg,
                            error=_error_to_pass,
                        )
                        # 检查插件是否通过 meta.agent.reply=False 抑制回复
                        _suppress_reply = _is_reply_suppressed(up_result.result if isinstance(up_result.result, dict) else None)
                        # 检查插件是否返回 deferred 标志（如备忘提醒：调度成功但提醒尚未触发）
                        is_deferred = isinstance(run_data, dict) and run_data.get("deferred") is True
                        # Update task_registry（deferred 任务保持 running，不写 terminal 状态）
                        _reg = Modules.task_registry.get(result.task_id)
                        if _reg and not (up_result.success and is_deferred):
                            _reg["status"] = up_terminal
                            _reg["end_time"] = _now_iso()
                            _reg["result"] = up_result.result
                            if not up_result.success:
                                _reg["error"] = (detail or str(up_result.error or ""))[:500]
                        if up_result.success and is_deferred:
                            # 保持任务为 running 状态，等待 daemon 触发后回调完成
                            reminder_id = run_data.get("reminder_id") if isinstance(run_data, dict) else None
                            logger.info("[Deferred] Task %s kept running, reminder_id=%s", result.task_id, reminder_id)
                            # 设置超时，防止绑定失败导致任务永远卡在 running
                            if _reg:
                                _reg["deferred_timeout"] = time.time() + DEFERRED_TASK_TIMEOUT
                            if reminder_id:
                                # 在线程中执行（含 HTTP 轮询，避免阻塞事件循环）
                                loop = asyncio.get_event_loop()
                                loop.run_in_executor(None, _bind_deferred_task, plugin_id, reminder_id, result.task_id)
                            # 不进入后续 completed/failed 流程
                        elif up_result.success:
                            logger.info(f"[TaskExecutor] ✅ UserPlugin completed: {plugin_id}")
                            if not _suppress_reply:
                                _lang = _rp_lang(None)
                                display_id = _get_plugin_display_id(plugin_id)
                                summary = _rp_phrase('plugin_done_with', _lang, id=display_id, detail=detail) if detail else _rp_phrase('plugin_done', _lang, id=display_id)
                                try:
                                    await _emit_task_result(
                                        lanlan_name,
                                        channel="user_plugin",
                                        task_id=str(up_result.task_id or ""),
                                        success=True,
                                        summary=summary[:500],
                                        detail=detail,
                                        direct_reply=False,
                                    )
                                except Exception as emit_err:
                                    logger.debug("[TaskExecutor] emit task_result(success) failed: task_id=%s plugin_id=%s error=%s", up_result.task_id, plugin_id, emit_err)
                        else:
                            logger.warning(f"[TaskExecutor] ❌ UserPlugin failed: {up_result.error}")
                            if not _suppress_reply:
                                _lang = _rp_lang(None)
                                try:
                                    display_id = _get_plugin_display_id(plugin_id)
                                    _fail_summary = _rp_phrase('plugin_failed_with', _lang, id=display_id, detail=detail) if detail else _rp_phrase('plugin_failed', _lang, id=display_id)
                                    await _emit_task_result(
                                        lanlan_name,
                                        channel="user_plugin",
                                        task_id=str(up_result.task_id or ""),
                                        success=False,
                                        summary=_fail_summary[:500],
                                        error_message=(detail or str(up_result.error or ""))[:500],
                                    )
                                except Exception as emit_err:
                                    logger.debug("[TaskExecutor] emit task_result(failed) failed: task_id=%s plugin_id=%s error=%s", up_result.task_id, plugin_id, emit_err)
                        # Emit task_update (terminal) — deferred 任务跳过，保持 running
                        if not (up_result.success and is_deferred):
                            try:
                                await _emit_main_event(
                                    "task_update", lanlan_name,
                                    task={"id": result.task_id, "status": up_terminal, "type": "user_plugin",
                                          "start_time": up_start, "end_time": _now_iso(),
                                          "params": task_params,
                                          "error": (detail or str(up_result.error or ""))[:500] if not up_result.success else None},
                                )
                            except Exception as emit_err:
                                logger.debug("[TaskExecutor] emit task_update(terminal) failed: task_id=%s plugin_id=%s error=%s", result.task_id, plugin_id, emit_err)
                    except asyncio.CancelledError as e:
                        cancel_msg = str(e)[:500] if str(e) else "cancelled"
                        _reg = Modules.task_registry.get(result.task_id)
                        if _reg:
                            _reg["status"] = "cancelled"
                            _reg["error"] = cancel_msg
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="user_plugin",
                                task_id=str(result.task_id or ""),
                                success=False,
                                summary=_rp_phrase('plugin_cancelled', _rp_lang(None)),
                                error_message=cancel_msg,
                            )
                        except Exception as emit_err:
                            logger.debug("[TaskExecutor] emit task_result(cancelled) failed: task_id=%s error=%s", result.task_id, emit_err)
                        try:
                            await _emit_main_event(
                                "task_update", lanlan_name,
                                task={"id": result.task_id, "status": "cancelled", "type": "user_plugin",
                                      "start_time": up_start, "end_time": _now_iso(),
                                      "params": task_params,
                                      "error": cancel_msg},
                            )
                        except Exception as emit_err:
                            logger.debug("[TaskExecutor] emit task_update(cancelled) failed: task_id=%s error=%s", result.task_id, emit_err)
                        raise
                    except Exception as e:
                        logger.exception("[TaskExecutor] UserPlugin dispatch failed: %s", e)
                        _reg = Modules.task_registry.get(result.task_id)
                        if _reg:
                            _reg["status"] = "failed"
                            _reg["error"] = str(e)[:500]
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="user_plugin",
                                task_id=str(result.task_id or ""),
                                success=False,
                                summary='插件任务分发失败',
                                error_message=str(e)[:500],
                            )
                        except Exception as emit_err:
                            logger.debug("[TaskExecutor] emit task_result(dispatch_failed) failed: task_id=%s error=%s", result.task_id, emit_err)
                        try:
                            await _emit_main_event(
                                "task_update", lanlan_name,
                                task={"id": result.task_id, "status": "failed", "type": "user_plugin",
                                      "start_time": up_start, "end_time": _now_iso(),
                                      "params": task_params,
                                      "error": str(e)[:500]},
                            )
                        except Exception as emit_err:
                            logger.debug("[TaskExecutor] emit task_update(dispatch_failed) failed: task_id=%s error=%s", result.task_id, emit_err)

                up_task = asyncio.create_task(_run_user_plugin_dispatch())
                Modules.task_async_handles[result.task_id] = up_task
                Modules._background_tasks.add(up_task)
                def _cleanup_up_task(_t, _tid=result.task_id):
                    Modules._background_tasks.discard(_t)
                    Modules.task_async_handles.pop(_tid, None)
                up_task.add_done_callback(_cleanup_up_task)
            else:
                logger.warning("[UserPlugin] ⚠️ Task requires UserPlugin but it's disabled")
        elif result.execution_method == 'openclaw':
            if Modules.agent_flags.get("openclaw_enabled", False) and Modules.openclaw:
                nk_start = _now_iso()
                instruction = ""
                if isinstance(result.tool_args, dict):
                    instruction = str(result.tool_args.get("instruction") or "")
                task_params = {"description": result.task_description or _default_openclaw_task_description()}
                nk_sender_id = Modules.openclaw.default_sender_id
                nk_session_id = Modules.openclaw.get_or_create_persistent_session_id(
                    role_name=lanlan_name,
                    sender_id=nk_sender_id,
                )
                Modules.task_registry[result.task_id] = {
                    "id": result.task_id,
                    "type": "openclaw",
                    "status": "running",
                    "start_time": nk_start,
                    "params": task_params,
                    "lanlan_name": lanlan_name,
                    "sender_id": nk_sender_id,
                    "session_id": nk_session_id,
                    "result": None,
                    "error": None,
                }
                try:
                    await _emit_main_event(
                        "task_update",
                        lanlan_name,
                        task={
                            "id": result.task_id,
                            "status": "running",
                            "type": "openclaw",
                            "start_time": nk_start,
                            "params": task_params,
                        },
                    )
                except Exception as emit_err:
                    logger.debug("[OpenClaw] emit task_update(running) failed: task_id=%s error=%s", result.task_id, emit_err)
                try:
                    await _emit_main_event(
                        "proactive_message",
                        lanlan_name,
                        text="我试试",
                        detail="我试试",
                        direct_reply=True,
                        timestamp=_now_iso(),
                    )
                except Exception as emit_err:
                    logger.debug("[OpenClaw] emit proactive_message(ack) failed: task_id=%s error=%s", result.task_id, emit_err)
                async def _run_openclaw_dispatch():
                    try:
                        nk_result = await Modules.openclaw.run_instruction(
                            instruction,
                            sender_id=nk_sender_id,
                            session_id=nk_session_id,
                            conversation_id=conversation_id,
                            role_name=lanlan_name,
                        )
                        success = bool(nk_result.get("success"))
                        reply = str(nk_result.get("reply") or "")
                        _reg = Modules.task_registry.get(result.task_id)
                        if _reg:
                            _reg["status"] = "completed" if success else "failed"
                            _reg["end_time"] = _now_iso()
                            _reg["result"] = nk_result
                            if not success:
                                _reg["error"] = str(nk_result.get("error") or "")[:500]
                        if success:
                            await _emit_task_result(
                                lanlan_name,
                                channel="openclaw",
                                task_id=str(result.task_id or ""),
                                success=True,
                                summary=reply[:500] if reply else _rp_phrase('openclaw_done', _rp_lang(None)),
                                detail=reply,
                                direct_reply=False,
                            )
                        else:
                            await _emit_task_result(
                                lanlan_name,
                                channel="openclaw",
                                task_id=str(result.task_id or ""),
                                success=False,
                                summary=_rp_phrase('openclaw_failed', _rp_lang(None)),
                                error_message=str(nk_result.get("error") or "")[:500],
                            )
                        await _emit_main_event(
                            "task_update",
                            lanlan_name,
                            task={
                                "id": result.task_id,
                                "status": "completed" if success else "failed",
                                "type": "openclaw",
                                "start_time": nk_start,
                                "end_time": _now_iso(),
                                "params": task_params,
                                "error": str(nk_result.get("error") or "")[:500] if not success else None,
                            },
                        )
                    except asyncio.CancelledError as e:
                        cancel_msg = str(e)[:500] if str(e) else "cancelled"
                        _reg = Modules.task_registry.get(result.task_id)
                        if _reg:
                            _reg["status"] = "cancelled"
                            _reg["error"] = cancel_msg
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="openclaw",
                                task_id=str(result.task_id or ""),
                                success=False,
                                summary=_rp_phrase('openclaw_cancelled', _rp_lang(None)),
                                error_message=cancel_msg,
                            )
                        except Exception:
                            pass
                        try:
                            await _emit_main_event(
                                "task_update",
                                lanlan_name,
                                task={
                                    "id": result.task_id,
                                    "status": "cancelled",
                                    "type": "openclaw",
                                    "start_time": nk_start,
                                    "end_time": _now_iso(),
                                    "params": task_params,
                                    "error": cancel_msg,
                                },
                            )
                        except Exception:
                            pass
                        raise
                    except Exception as e:
                        logger.exception("[OpenClaw] dispatch failed: %s", e)
                        _reg = Modules.task_registry.get(result.task_id)
                        if _reg:
                            _reg["status"] = "failed"
                            _reg["error"] = str(e)[:500]
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="openclaw",
                                task_id=str(result.task_id or ""),
                                success=False,
                                summary=_rp_phrase('openclaw_dispatch_failed', _rp_lang(None)),
                                error_message=str(e)[:500],
                            )
                        except Exception:
                            pass
                        try:
                            await _emit_main_event(
                                "task_update",
                                lanlan_name,
                                task={
                                    "id": result.task_id,
                                    "status": "failed",
                                    "type": "openclaw",
                                    "start_time": nk_start,
                                    "end_time": _now_iso(),
                                    "params": task_params,
                                    "error": str(e)[:500],
                                },
                            )
                        except Exception:
                            pass

                nk_task = asyncio.create_task(_run_openclaw_dispatch())
                Modules.task_async_handles[result.task_id] = nk_task
                Modules._background_tasks.add(nk_task)

                def _cleanup_nk_task(_t, _tid=result.task_id):
                    Modules._background_tasks.discard(_t)
                    Modules.task_async_handles.pop(_tid, None)

                nk_task.add_done_callback(_cleanup_nk_task)
            else:
                logger.warning("[OpenClaw] ⚠️ Task requires OpenClaw but it's disabled")
        elif result.execution_method == 'browser_use':
            if Modules.agent_flags.get("browser_use_enabled", False) and Modules.browser_use:
                sm = get_session_manager()
                bu_session = sm.get_or_create(None, "browser_use")
                bu_session.add_task(result.task_description)

                bu_task_id = str(uuid.uuid4())
                bu_start = _now_iso()
                bu_info = {
                    "id": bu_task_id,
                    "type": "browser_use",
                    "status": "running",
                    "start_time": bu_start,
                    "params": {"instruction": result.task_description},
                    "lanlan_name": lanlan_name,
                    "session_id": bu_session.session_id,
                    "result": None,
                    "error": None,
                }
                Modules.task_registry[bu_task_id] = bu_info
                Modules.active_browser_use_task_id = bu_task_id
                try:
                    await _emit_main_event(
                        "task_update", lanlan_name,
                        task={"id": bu_task_id, "status": "running", "type": "browser_use",
                              "start_time": bu_start, "params": {"instruction": result.task_description},
                              "session_id": bu_session.session_id},
                    )
                except Exception as e:
                    logger.debug("[BrowserUse] emit task_update(running) failed: task_id=%s error=%s", bu_task_id, e)
                async def _run_browser_use_dispatch():
                    try:
                        bres = await Modules.browser_use.run_instruction(
                            result.task_description,
                            session_id=bu_session.session_id,
                        )
                        success = bres.get("success", False) if isinstance(bres, dict) else False
                        _bu_ok, bu_parsed = parse_browser_use_result(bres)
                        _lang = _rp_lang(None)
                        _done = _rp_phrase('cu_status_done', _lang) if success else _rp_phrase('cu_status_ended', _lang)
                        if bu_parsed:
                            summary = _rp_phrase('cu_task_done', _lang, desc=result.task_description, status=_done, detail=bu_parsed)
                        else:
                            summary = _rp_phrase('cu_task_desc_only', _lang, desc=result.task_description, status=_done)
                        bu_session.complete_task(bu_parsed or summary, success)
                        bu_info["status"] = "completed" if success else "failed"
                        bu_info["end_time"] = _now_iso()
                        bu_info["result"] = bres
                        if not success:
                            bu_info["error"] = (bu_parsed or "")[:500]
                        await _emit_task_result(
                            lanlan_name,
                            channel="browser_use",
                            task_id=bu_task_id,
                            success=success,
                            summary=summary,
                            detail=bu_parsed if success else "",
                            error_message=bu_parsed if not success else "",
                        )
                        try:
                            await _emit_main_event(
                                "task_update", lanlan_name,
                                task={"id": bu_task_id, "status": bu_info["status"],
                                      "type": "browser_use", "start_time": bu_start, "end_time": _now_iso(),
                                      "error": (bu_parsed[:500] if bu_parsed else "") if not success else None,
                                      "session_id": bu_session.session_id},
                            )
                        except Exception as emit_err:
                            logger.debug("[BrowserUse] emit task_update(terminal) failed: task_id=%s error=%s", bu_task_id, emit_err)
                    except asyncio.CancelledError as e:
                        cancel_msg = str(e)[:500] if str(e) else "cancelled"
                        bu_info["status"] = "cancelled"
                        bu_info["error"] = cancel_msg
                        bu_session.complete_task(cancel_msg, success=False)
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="browser_use",
                                task_id=bu_task_id,
                                success=False,
                                summary=f'你的任务"{result.task_description}"已取消',
                                error_message=cancel_msg,
                            )
                        except Exception as emit_err:
                            logger.debug("[BrowserUse] emit task_result(cancelled) failed: task_id=%s error=%s", bu_task_id, emit_err)
                        try:
                            await _emit_main_event(
                                "task_update", lanlan_name,
                                task={"id": bu_task_id, "status": "cancelled", "type": "browser_use",
                                      "start_time": bu_start, "end_time": _now_iso(),
                                      "error": cancel_msg, "session_id": bu_session.session_id},
                            )
                        except Exception as emit_err:
                            logger.debug("[BrowserUse] emit task_update(cancelled) failed: task_id=%s error=%s", bu_task_id, emit_err)
                        raise
                    except Exception as e:
                        logger.warning(f"[BrowserUse] Failed: {e}")
                        bu_info["status"] = "failed"
                        bu_info["end_time"] = _now_iso()
                        bu_info["error"] = str(e)[:500]
                        bu_session.complete_task(str(e), success=False)
                        try:
                            await _emit_task_result(
                                lanlan_name,
                                channel="browser_use",
                                task_id=bu_task_id,
                                success=False,
                                summary=f'你的任务"{result.task_description}"执行异常',
                                error_message=str(e),
                            )
                        except Exception as emit_err:
                            logger.debug("[BrowserUse] emit task_result(failed) failed: task_id=%s error=%s", bu_task_id, emit_err)
                        try:
                            await _emit_main_event(
                                "task_update", lanlan_name,
                                task={"id": bu_task_id, "status": "failed", "type": "browser_use",
                                      "start_time": bu_start, "end_time": _now_iso(),
                                      "error": str(e)[:500],
                                      "session_id": bu_session.session_id},
                            )
                        except Exception as emit_err:
                            logger.debug("[BrowserUse] emit task_update(failed) failed: task_id=%s error=%s", bu_task_id, emit_err)
                    finally:
                        Modules.active_browser_use_task_id = None

                bu_task = asyncio.create_task(_run_browser_use_dispatch())
                Modules.task_async_handles[bu_task_id] = bu_task
                Modules._background_tasks.add(bu_task)
                def _cleanup_bu_task(_t, _tid=bu_task_id):
                    Modules._background_tasks.discard(_t)
                    Modules.task_async_handles.pop(_tid, None)
                bu_task.add_done_callback(_cleanup_bu_task)
            else:
                logger.warning("[BrowserUse] Task requires BrowserUse but it is disabled")

        elif result.execution_method == 'openfang':
            if Modules.agent_flags.get("openfang_enabled", False) and Modules.openfang:
                dup, matched = await _is_duplicate_task(result.task_description, lanlan_name)
                if not dup:
                    sm = get_session_manager()
                    of_session = sm.get_or_create(None, "openfang")
                    of_session.add_task(result.task_description)

                    of_task_id = str(uuid.uuid4())
                    of_start = _now_iso()
                    of_info = {
                        "id": of_task_id,
                        "type": "openfang",
                        "status": "running",
                        "start_time": of_start,
                        "params": {"instruction": result.task_description},
                        "lanlan_name": lanlan_name,
                        "session_id": of_session.session_id,
                        "result": None,
                        "error": None,
                    }
                    Modules.task_registry[of_task_id] = of_info

                    try:
                        await _emit_main_event(
                            "task_update", lanlan_name,
                            task={"id": of_task_id, "status": "running", "type": "openfang",
                                  "start_time": of_start,
                                  "params": {"instruction": result.task_description},
                                  "session_id": of_session.session_id},
                        )
                    except Exception as e:
                        logger.debug("[OpenFang] emit task_update(running) failed: task_id=%s error=%s", of_task_id, e)

                    async def _run_openfang_dispatch():
                        try:
                            of_res = await Modules.openfang.run_instruction(
                                result.task_description,
                                session_id=of_session.session_id,
                                local_task_id=of_task_id,
                            )
                            logger.info("[OpenFang] Task completed: success=%s, agent=%s, result_len=%d, steps=%s, artifacts_count=%d",
                                        of_res.get("success"), of_res.get("agent_name"),
                                        len(str(of_res.get("result", ""))),
                                        of_res.get("steps"),
                                        len(of_res.get("artifacts") or []))
                            logger.debug("[OpenFang] ====== RAW RESULT (debug) ======")
                            logger.debug("[OpenFang] keys=%s", list(of_res.keys()))
                            logger.debug("[OpenFang] result (first 500): %s", str(of_res.get("result", ""))[:500])
                            logger.debug("[OpenFang] error: %s", of_res.get("error"))
                            logger.debug("[OpenFang] artifacts=%s", of_res.get("artifacts"))
                            logger.debug("[OpenFang] ==============================")
                            success = of_res.get("success", False)
                            of_result_text = of_res.get("result", "") or ""
                            of_error_text = of_res.get("error", "") or ""
                            _lang = _rp_lang(None)
                            _done = _rp_phrase('cu_status_done', _lang) if success else _rp_phrase('cu_status_ended', _lang)
                            summary = _rp_phrase('cu_task_done', _lang, desc=result.task_description, status=_done, detail=of_result_text[:300]) if of_result_text else \
                                      _rp_phrase('cu_task_desc_only', _lang, desc=result.task_description, status=_done)
                            of_session.complete_task(of_result_text or summary, success)
                            of_info["status"] = "completed" if success else "failed"
                            of_info["end_time"] = _now_iso()
                            of_info["result"] = of_res
                            if not success:
                                of_info["error"] = (of_error_text or of_result_text)[:500]
                            await _emit_task_result(
                                lanlan_name,
                                channel="openfang",
                                task_id=of_task_id,
                                success=success,
                                summary=summary,
                                detail=of_result_text if success else "",
                                error_message=of_error_text if not success else "",
                            )
                            try:
                                await _emit_main_event(
                                    "task_update", lanlan_name,
                                    task={"id": of_task_id, "status": of_info["status"],
                                          "type": "openfang", "start_time": of_start, "end_time": _now_iso(),
                                          "error": of_info.get("error"),
                                          "session_id": of_session.session_id},
                                )
                            except Exception as emit_err:
                                logger.debug("[OpenFang] emit task_update(terminal) failed: task_id=%s error=%s", of_task_id, emit_err)
                        except asyncio.CancelledError as e:
                            cancel_msg = str(e)[:500] if str(e) else "cancelled"
                            # Best-effort remote cancel
                            try:
                                if Modules.openfang:
                                    await Modules.openfang.cancel_running(of_task_id)
                                    Modules.openfang.unregister_local_task(of_task_id)
                            except Exception as cancel_err:
                                logger.debug("[OpenFang] remote cancel failed for %s: %s", of_task_id, cancel_err)
                            of_info["status"] = "cancelled"
                            of_info["error"] = cancel_msg
                            of_session.complete_task(cancel_msg, success=False)
                            try:
                                await _emit_task_result(
                                    lanlan_name, channel="openfang", task_id=of_task_id,
                                    success=False,
                                    summary=f'虚拟机任务 "{result.task_description}" 已取消',
                                    error_message=cancel_msg,
                                )
                            except Exception:
                                logger.debug("[OpenFang] emit_task_result(cancelled) failed: task_id=%s", of_task_id, exc_info=True)
                            try:
                                await _emit_main_event(
                                    "task_update", lanlan_name,
                                    task={"id": of_task_id, "status": "cancelled", "type": "openfang",
                                          "start_time": of_start, "end_time": _now_iso(),
                                          "error": cancel_msg, "session_id": of_session.session_id},
                                )
                            except Exception:
                                logger.debug("[OpenFang] emit task_update(cancelled) failed: task_id=%s", of_task_id, exc_info=True)
                            raise
                        except Exception as e:
                            logger.warning(f"[OpenFang] Task failed: {e}")
                            of_info["status"] = "failed"
                            of_info["end_time"] = _now_iso()
                            of_info["error"] = str(e)[:500]
                            of_session.complete_task(str(e), success=False)
                            try:
                                await _emit_task_result(
                                    lanlan_name, channel="openfang", task_id=of_task_id,
                                    success=False,
                                    summary=f'虚拟机任务 "{result.task_description}" 执行异常',
                                    error_message=str(e),
                                )
                            except Exception:
                                logger.debug("[OpenFang] emit_task_result(failed) failed: task_id=%s", of_task_id, exc_info=True)
                            try:
                                await _emit_main_event(
                                    "task_update", lanlan_name,
                                    task={"id": of_task_id, "status": "failed", "type": "openfang",
                                          "start_time": of_start, "end_time": _now_iso(),
                                          "error": str(e)[:500],
                                          "session_id": of_session.session_id},
                                )
                            except Exception:
                                logger.debug("[OpenFang] emit task_update(failed) failed: task_id=%s", of_task_id, exc_info=True)

                    of_task = asyncio.create_task(_run_openfang_dispatch())
                    Modules.task_async_handles[of_task_id] = of_task
                    Modules._background_tasks.add(of_task)
                    def _cleanup_of_task(_t, _tid=of_task_id):
                        Modules._background_tasks.discard(_t)
                        Modules.task_async_handles.pop(_tid, None)
                    of_task.add_done_callback(_cleanup_of_task)
                else:
                    logger.info(f"[OpenFang] Duplicate task detected, matched with {matched}")
            else:
                logger.warning("[OpenFang] ⚠️ Task requires OpenFang but it is disabled or unavailable")

        else:
            logger.info(f"[TaskExecutor] No suitable execution method: {result.reason}")
    
    except Exception as e:
        logger.error(f"[TaskExecutor] Background task error: {e}", exc_info=True)
        try:
            await _emit_main_event(
                "agent_notification", lanlan_name,
                text=f"💥 Agent后台任务异常: {type(e).__name__}: {e}",
                source="brain",
                status="error",
                error_message=str(e)[:500],
            )
        except Exception:
            logger.debug("[TaskExecutor] emit notification failed", exc_info=True)

@app.on_event("startup")
async def startup():
    # Install token tracking hooks for this process
    try:
        from utils.token_tracker import TokenTracker, install_hooks
        install_hooks()
        TokenTracker.get_instance().start_periodic_save()
        TokenTracker.get_instance().record_app_start()
    except Exception as e:
        logger.warning(f"[Agent] Token tracker init failed: {e}")

    os.environ["NEKO_PLUGIN_HOSTED_BY_AGENT"] = "true"
    Modules.computer_use = ComputerUseAdapter()
    Modules.openclaw = OpenClawAdapter()
    Modules.task_executor = DirectTaskExecutor(
        computer_use=Modules.computer_use,
        browser_use=None,
        openclaw=Modules.openclaw,
    )
    Modules.deduper = TaskDeduper()
    Modules.throttled_logger = ThrottledLogger(logger, interval=30.0)
    _rewire_computer_use_dependents()

    async def _init_browser_use_background():
        try:
            bu = await asyncio.to_thread(BrowserUseAdapter)
            Modules.browser_use = bu
            Modules.task_executor.browser_use = bu
            logger.info("[Agent] BrowserUseAdapter ready (background init)")
            try:
                await _fire_agent_llm_connectivity_check()
            except Exception:
                logger.debug("[Agent] Post-browser-init capability refresh failed", exc_info=True)
        except Exception as exc:
            logger.error("[Agent] BrowserUseAdapter background init failed: %s", exc)

    _bu_task = asyncio.create_task(_init_browser_use_background())
    Modules._persistent_tasks.add(_bu_task)
    _bu_task.add_done_callback(Modules._persistent_tasks.discard)
    try:
        await _start_embedded_user_plugin_server()
    except Exception as e:
        logger.warning(f"[Agent] Failed to start embedded user plugin server: {e}")
    # ── OpenFang 后台初始化 (仅通信层，进程由 Electron 管理) ──
    async def _init_openfang_background():
        """等待 OpenFang daemon 连通 + 同步配置 + 注册执行 Agent。"""
        try:
            adapter = OpenFangAdapter(base_url=OPENFANG_BASE_URL)
            Modules.openfang = adapter
            Modules.task_executor.openfang = adapter

            # 等待 OpenFang 就绪 (由 Electron 并行启动，通常 <1s)
            # check_connectivity 是同步 httpx 调用，用 to_thread 避免阻塞 event loop
            for _attempt in range(30):
                ok = await asyncio.to_thread(adapter.check_connectivity)
                if ok:
                    break
                await asyncio.sleep(1)

            if not adapter.init_ok:
                logger.warning("[OpenFang] not reachable after 30s")
                _set_capability("openfang", False, "OPENFANG_DAEMON_UNREACHABLE")
                return

            # 同步 API Key + 写 config.toml（允许失败 — 用户可能尚未配置 Key）
            try:
                await adapter.sync_config()
            except Exception as e:
                logger.warning("[OpenFang] sync_config failed (non-fatal): %s", e)

            # 等待 OpenFang 检测并 reload config.toml
            # OpenFang 用文件监听检测 config 变化，但 reload 可能有延迟
            try:
                import os as _os
                _home = _os.environ.get("HOME") or _os.environ.get("USERPROFILE") or ""
                _cfg = _os.path.join(_home, ".openfang", "config.toml")
                if _os.path.exists(_cfg):
                    _os.utime(_cfg, None)  # touch to trigger fswatch
            except Exception:
                logger.debug("[OpenFang] failed to touch config file for fswatch", exc_info=True)
            await asyncio.sleep(5)

            # 拉取可用工具列表
            try:
                await adapter.fetch_tools_list()
            except Exception as e:
                logger.warning("[OpenFang] fetch_tools_list failed (non-fatal): %s", e)

            # 注册无人格执行 Agent（允许失败 — 连通即可用）
            # manifest 中直接带 api_key + provider=openai，不依赖环境变量
            try:
                print("[OpenFang DEBUG] Calling push_agent_manifest...")
                agent_id = await adapter.push_agent_manifest()
                print(f"[OpenFang DEBUG] push_agent_manifest returned: {agent_id}")
                print(f"[OpenFang DEBUG] adapter._executor_agent_id = {adapter._executor_agent_id}")
            except Exception as e:
                import traceback
                logger.warning("[OpenFang] push_agent_manifest failed (non-fatal): %s", e)
                print(f"[OpenFang DEBUG] push_agent_manifest EXCEPTION: {e}")
                print(f"[OpenFang DEBUG] push_agent_manifest traceback:\n{traceback.format_exc()}")
                agent_id = None

            # 只要 daemon 连通就标记 ready，不强制要求 agent 注册成功
            _set_capability("openfang", True, "")
            logger.info("[OpenFang] Ready (init_ok=%s, agent=%s, tools=%d)",
                        adapter.init_ok, agent_id, adapter._cached_tools_count or 0)
        except Exception as exc:
            logger.error("[OpenFang] background init failed: %s", exc)
            _set_capability("openfang", False, str(exc))

    _of_task = asyncio.create_task(_init_openfang_background())
    Modules._persistent_tasks.add(_of_task)
    _of_task.add_done_callback(Modules._persistent_tasks.discard)

    # Both CUA and BrowserUse share the agent LLM — default to "not connected"
    # and probe in background.  The single check updates both capability caches.
    _set_capability("computer_use", False, "connectivity check pending")
    _set_capability("browser_use", False, "connectivity check pending")
    # Plugin capability = ready (embedded HTTP server is always up), but lifecycle
    # is NOT started here — it syncs with user_plugin_enabled (default OFF).
    # The lifecycle starts on-demand when the user toggles the plugin flag ON.
    _set_capability("user_plugin", True, "")
    # OpenFang capability 由 _init_openfang_background() 管理，不在此处覆盖
    _llm_probe_task = asyncio.create_task(_fire_agent_llm_connectivity_check())
    Modules._persistent_tasks.add(_llm_probe_task)
    _llm_probe_task.add_done_callback(Modules._persistent_tasks.discard)
    
    try:
        async def _http_plugin_provider(force_refresh: bool = False):
            url = f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins"
            if force_refresh:
                url += "?refresh=true"
            try:
                async with httpx.AsyncClient(timeout=1.0, proxy=None, trust_env=False) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        try:
                            data = r.json()
                        except Exception as parse_err:
                            logger.debug(f"[Agent] plugin_list_provider parse error: {parse_err}")
                            data = {}
                        return data.get("plugins", []) or []
            except Exception as e:
                logger.debug(f"[Agent] plugin_list_provider http fetch failed: {e}")
            return []

        # inject http-based provider so DirectTaskExecutor can pick up user_plugin_server plugins
        try:
            Modules.task_executor.set_plugin_list_provider(_http_plugin_provider)
            logger.debug("[Agent] Registered http plugin_list_provider for task_executor")
        except Exception as e:
            logger.warning(f"[Agent] Failed to inject plugin_list_provider into task_executor: {e}")
    except Exception as e:
        logger.warning(f"[Agent] Failed to set http plugin_list_provider: {e}")

    # Start computer-use scheduler
    sch_task = asyncio.create_task(_computer_use_scheduler_loop())
    Modules._persistent_tasks.add(sch_task)
    sch_task.add_done_callback(Modules._persistent_tasks.discard)
    # Start ZeroMQ bridge for main_server events
    try:
        Modules.agent_bridge = AgentServerEventBridge(on_session_event=_on_session_event)
        await Modules.agent_bridge.start()
    except Exception as e:
        logger.warning(f"[Agent] Event bridge startup failed: {e}")
    # Push initial server status so frontend can render Agent popup without waiting.
    _bump_state_revision()


@app.on_event("shutdown")
async def shutdown():
    """Gracefully stop running tasks and release async resources."""
    logger.info("[Agent] Shutdown initiated — stopping running tasks")

    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().save()
    except Exception:
        pass

    if Modules.computer_use:
        Modules.computer_use.cancel_running()
    if Modules.browser_use:
        try:
            Modules.browser_use.cancel_running()
        except Exception:
            pass

    for t in list(Modules._persistent_tasks):
        if not t.done():
            t.cancel()
    if Modules.active_computer_use_async_task and not Modules.active_computer_use_async_task.done():
        Modules.active_computer_use_async_task.cancel()

    try:
        await _ensure_plugin_lifecycle_stopped()
    except Exception as e:
        logger.warning(f"[Agent] Plugin lifecycle cleanup error: {e}")

    try:
        await _stop_embedded_user_plugin_server()
    except Exception as e:
        logger.warning(f"[Agent] Embedded user plugin server cleanup error: {e}")

    logger.info("[Agent] 正在清理 AsyncClient 资源...")

    async def _close_router(name: str, module, attr: str):
        if module and hasattr(module, attr):
            try:
                router = getattr(module, attr)
                await asyncio.wait_for(router.aclose(), timeout=3.0)
                logger.debug(f"[Agent] ✅ {name}.{attr} 已清理")
            except asyncio.TimeoutError:
                logger.warning(f"[Agent] ⚠️ {name}.{attr} 清理超时，强制跳过")
            except asyncio.CancelledError:
                logger.debug(f"[Agent] {name}.{attr} 清理时被取消（正常关闭）")
            except RuntimeError as e:
                logger.debug(f"[Agent] {name}.{attr} 清理时遇到 RuntimeError（可能是正常关闭）: {e}")
            except Exception as e:
                logger.warning(f"[Agent] ⚠️ 清理 {name}.{attr} 时出现意外错误: {e}")

    try:
        _shutdown_coros = []
        for _name, _attr_name in [("DirectTaskExecutor", "task_executor")]:
            _mod = getattr(Modules, _attr_name, None)
            if _mod is not None:
                _shutdown_coros.append(_close_router(_name, _mod, "router"))
        if _shutdown_coros:
            await asyncio.wait_for(
                asyncio.gather(*_shutdown_coros, return_exceptions=True),
                timeout=5.0,
            )
    except asyncio.TimeoutError:
        logger.warning("[Agent] ⚠️ 整体清理过程超时，强制完成关闭")

    bridge = Modules.agent_bridge
    if bridge is not None:
        try:
            bridge._stop.set()
            try:
                import zmq as _zmq

                _LINGER = _zmq.LINGER
            except Exception:
                _LINGER = 17
            for sock_name in ("sub", "analyze_pull", "push"):
                sock = getattr(bridge, sock_name, None)
                if sock is not None:
                    try:
                        sock.setsockopt(_LINGER, 0)
                        sock.close()
                    except Exception as e:
                        logger.debug("[Agent] ZMQ socket %s close error: %s", sock_name, e)
            if bridge.ctx is not None:
                try:
                    bridge.ctx.term()
                except Exception as e:
                    logger.debug("[Agent] ZMQ context term error: %s", e)
            bridge.ready = False
            Modules.agent_bridge = None
            logger.debug("[Agent] ✅ ZMQ event bridge cleaned up")
        except Exception as e:
            logger.warning("[Agent] ⚠️ ZMQ event bridge cleanup error: %s", e)

    all_tasks = list(Modules._persistent_tasks) + list(Modules._background_tasks)
    tasks_to_await = [t for t in all_tasks if not t.done()]
    for t in tasks_to_await:
        t.cancel()
    if tasks_to_await:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_await, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[Agent] ⚠️ 部分后台任务取消超时")
    Modules._persistent_tasks.clear()
    Modules._background_tasks.clear()

    cu = Modules.computer_use
    if cu is not None and hasattr(cu, "wait_for_completion"):
        loop = asyncio.get_running_loop()
        finished = await loop.run_in_executor(None, cu.wait_for_completion, 8.0)
        if not finished:
            logger.warning("[Agent] CUA thread did not stop within 8s at shutdown")

    logger.info("[Agent] ✅ AsyncClient 资源清理完成")
    logger.info("[Agent] Shutdown cleanup complete")
    await _emit_agent_status_update()


@app.get("/health")
async def health():
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response(
        "agent",
        instance_id=INSTANCE_ID,
        extra={"agent_flags": Modules.agent_flags},
    )


# 插件直接触发路由（放在顶层，确保不在其它函数体内）
@app.post("/plugin/execute")
async def plugin_execute_direct(payload: Dict[str, Any]):
    """
    新增接口：直接触发 plugin_entry。
    请求 body 可包含:
      - plugin_id: str (必需)
      - entry_id: str (可选)
      - args: dict (可选)
      - lanlan_name: str (可选，用于日志/通知)
    该接口将调用 Modules.task_executor.execute_user_plugin_direct 来执行插件触发。
    """
    if not Modules.task_executor:
        raise HTTPException(503, "Task executor not ready")
    # 当后端显式关闭用户插件功能时，直接拒绝调用，避免绕过前端开关
    if not Modules.agent_flags.get("user_plugin_enabled", False):
        raise HTTPException(403, "User plugin is disabled")
    plugin_id = (payload or {}).get("plugin_id")
    entry_id = (payload or {}).get("entry_id")
    raw_args = (payload or {}).get("args", {}) or {}
    if not isinstance(raw_args, dict):
        raise HTTPException(400, "args must be a JSON object")
    args = raw_args
    lanlan_name = (payload or {}).get("lanlan_name")
    conversation_id = (payload or {}).get("conversation_id")
    if not plugin_id or not isinstance(plugin_id, str):
        raise HTTPException(400, "plugin_id required")

    # Dedup is not applied for direct plugin calls; client should dedupe if needed
    task_id = str(uuid.uuid4())
    # Log request
    logger.info(f"[Plugin] Direct execute request: plugin_id={plugin_id}, entry_id={entry_id}, lanlan={lanlan_name}")

    # 获取插件友好名称（用于 HUD 显示）
    plugin_name = _get_plugin_friendly_name(plugin_id)
    task_params = {"plugin_id": plugin_id, "entry_id": entry_id, "args": args}
    if plugin_name:
        task_params["plugin_name"] = plugin_name

    # Ensure task registry entry for tracking
    info = {
        "id": task_id,
        "type": "plugin_direct",
        "status": "running",
        "start_time": _now_iso(),
        "params": task_params,
        "lanlan_name": lanlan_name,
        "result": None,
        "error": None,
    }
    Modules.task_registry[task_id] = info

    # Execute via task_executor.execute_user_plugin_direct in background
    async def _run_plugin():
        try:
            await _emit_main_event(
                "task_update", lanlan_name,
                task={
                    "id": task_id,
                    "status": "running",
                    "type": "plugin_direct",
                    "start_time": info["start_time"],
                    "params": task_params,
                },
            )
        except Exception as emit_err:
            logger.debug("[Plugin] emit task_update(running) failed: task_id=%s error=%s", task_id, emit_err)

        async def _on_plugin_progress(
            *, progress=None, stage=None, message=None, step=None, step_total=None,
        ):
            task_payload: Dict[str, Any] = {
                "id": task_id,
                "status": "running",
                "type": "plugin_direct",
                "start_time": info["start_time"],
                "params": task_params,
            }
            if progress is not None:
                task_payload["progress"] = progress
            if stage is not None:
                task_payload["stage"] = stage
            if message is not None:
                task_payload["message"] = message
            if step is not None:
                task_payload["step"] = step
            if step_total is not None:
                task_payload["step_total"] = step_total
            await _emit_main_event("task_update", lanlan_name, task=task_payload)

        try:
            res = await Modules.task_executor.execute_user_plugin_direct(
                task_id=task_id,
                plugin_id=plugin_id,
                plugin_args=args,
                entry_id=entry_id,
                lanlan_name=lanlan_name,
                conversation_id=conversation_id,
                on_progress=_on_plugin_progress,
            )
            info["result"] = res.result
            info["status"] = "completed" if res.success else "failed"
            info["end_time"] = _now_iso()
            try:
                run_data = res.result.get("run_data") if isinstance(res.result, dict) else None
                run_error = res.result.get("run_error") if isinstance(res.result, dict) else None
                _llm_fields = _lookup_llm_result_fields(plugin_id, entry_id)
                _plugin_msg = str(res.result.get("message") or "") if isinstance(res.result, dict) else ""
                _error_to_pass = (run_error or res.error) if not res.success else None
                detail = parse_plugin_result(
                    run_data,
                    llm_result_fields=_llm_fields,
                    plugin_message=_plugin_msg,
                    error=_error_to_pass,
                )
                _suppress_reply = _is_reply_suppressed(res.result if isinstance(res.result, dict) else None)
                if not _suppress_reply:
                    if not res.success:
                        info["error"] = (detail or str(res.error or ""))[:500]
                    _lang = _rp_lang(None)
                    display_id = _get_plugin_display_id(plugin_id)
                    if res.success:
                        summary = _rp_phrase('plugin_done_with', _lang, id=display_id, detail=detail) if detail else _rp_phrase('plugin_done', _lang, id=display_id)
                    else:
                        summary = _rp_phrase('plugin_failed_with', _lang, id=display_id, detail=detail) if detail else _rp_phrase('plugin_failed', _lang, id=display_id)
                    await _emit_task_result(
                        lanlan_name,
                        channel="user_plugin",
                        task_id=task_id,
                        success=res.success,
                        summary=summary[:500],
                        detail=detail if res.success else "",
                        error_message=(detail or str(res.error or ""))[:500] if not res.success else "",
                        direct_reply=False,
                    )
                elif not res.success:
                    info["error"] = (detail or str(res.error or ""))[:500]
            except Exception as emit_err:
                logger.debug("[Plugin] emit task_result failed: task_id=%s plugin_id=%s error=%s", task_id, plugin_id, emit_err)
        except asyncio.CancelledError:
            info["status"] = "cancelled"
            if not info.get("error"):
                info["error"] = "Cancelled by shutdown"
            try:
                await _emit_task_result(
                    lanlan_name,
                    channel="user_plugin",
                    task_id=task_id,
                    success=False,
                    summary=_rp_phrase('plugin_cancelled_id', _rp_lang(None), id=plugin_id),
                    error_message="cancelled",
                )
            except Exception as emit_err:
                logger.debug("[Plugin] emit task_result(cancelled) failed: task_id=%s plugin_id=%s error=%s", task_id, plugin_id, emit_err)
            raise
        except Exception as e:
            info["status"] = "failed"
            info["end_time"] = _now_iso()
            info["error"] = str(e)[:500]
            logger.error(f"[Plugin] Direct execute failed: {e}", exc_info=True)
            try:
                await _emit_task_result(
                    lanlan_name,
                    channel="user_plugin",
                    task_id=task_id,
                    success=False,
                    summary=_rp_phrase('plugin_exception', _rp_lang(None), id=plugin_id, err=str(e)[:200]),
                    error_message=str(e)[:500],
                )
            except Exception as emit_err:
                logger.debug("[Plugin] emit task_result(exception) failed: task_id=%s plugin_id=%s error=%s", task_id, plugin_id, emit_err)
        finally:
            try:
                await _emit_main_event(
                    "task_update", lanlan_name,
                    task={
                        "id": task_id,
                        "status": info.get("status"),
                        "type": "plugin_direct",
                        "start_time": info.get("start_time"),
                        "end_time": _now_iso(),
                        "params": info.get("params", {}),
                        "error": info.get("error"),
                    },
                )
            except Exception as emit_err:
                logger.debug("[Plugin] emit task_update(terminal) failed: task_id=%s error=%s", task_id, emit_err)

    plugin_task = asyncio.create_task(_run_plugin())
    Modules.task_async_handles[task_id] = plugin_task
    Modules._background_tasks.add(plugin_task)
    def _cleanup_plugin_task(_t, _tid=task_id):
        Modules._background_tasks.discard(_t)
        Modules.task_async_handles.pop(_tid, None)
    plugin_task.add_done_callback(_cleanup_plugin_task)
    return {"success": True, "task_id": task_id, "status": info["status"], "start_time": info["start_time"]}



@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    info = Modules.task_registry.get(task_id)
    if info:
        out = {k: v for k, v in info.items() if k != "_proc"}
        return out
    raise HTTPException(404, "task not found")


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a specific running task."""
    info = Modules.task_registry.get(task_id)
    if not info:
        raise HTTPException(404, "task not found")
    if info.get("status") not in ("queued", "running"):
        return {"success": False, "error": "task is not active"}

    task_type = info.get("type")
    bg = Modules.task_async_handles.get(task_id)
    if bg and not bg.done():
        bg.cancel()
    if task_type == "computer_use":
        if Modules.computer_use:
            Modules.computer_use.cancel_running()
        if Modules.active_computer_use_task_id == task_id and Modules.active_computer_use_async_task:
            Modules.active_computer_use_async_task.cancel()
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
    elif task_type == "browser_use":
        if Modules.browser_use:
            Modules.browser_use.cancel_running()
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
    elif task_type == "openfang":
        if Modules.openfang:
            try:
                await Modules.openfang.cancel_running(task_id)
            except Exception as e:
                logger.warning("[OpenFang] cancel_running failed for %s: %s", task_id, e)
            Modules.openfang.unregister_local_task(task_id)
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
    elif task_type == "openclaw":
        if Modules.openclaw:
            try:
                stop_result = await Modules.openclaw.stop_running(
                    sender_id=info.get("sender_id"),
                    session_id=info.get("session_id"),
                    conversation_id=info.get("session_id"),
                    role_name=info.get("lanlan_name"),
                    task_id=task_id,
                )
                if not stop_result.get("success"):
                    logger.warning(
                        "[OpenClaw] stop_running failed for %s: %s",
                        task_id,
                        stop_result.get("error"),
                    )
            except Exception as e:
                logger.warning("[OpenClaw] stop_running failed for %s: %s", task_id, e)
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"
    else:
        info["status"] = "cancelled"
        info["error"] = "Cancelled by user"

    lanlan_name = info.get("lanlan_name")
    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={"id": task_id, "status": "cancelled", "type": task_type,
                  "end_time": _now_iso(), "params": info.get("params", {}),
                  "error": "Cancelled by user"},
        )
    except Exception:
        pass
    logger.info("[Agent] Task %s (%s) cancelled by user", task_id, task_type)
    return {"success": True, "task_id": task_id, "status": "cancelled"}


@app.post("/api/agent/tasks/{task_id}/complete")
async def complete_deferred_task(task_id: str):
    """供插件 daemon 回调：将 deferred 任务标记为已完成并通知前端 HUD。"""
    info = Modules.task_registry.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="Task not found")
    if info.get("status") != "running":
        # 已经是 terminal 状态，幂等返回
        return {"ok": True, "skipped": True, "status": info.get("status")}

    # 验证这是一个 deferred 任务（只有 user_plugin 且有 deferred_timeout 的任务才能通过此端点完成）
    if info.get("type") != "user_plugin":
        raise HTTPException(status_code=403, detail="Only user_plugin tasks can be completed via this endpoint")
    if not info.get("deferred_timeout"):
        raise HTTPException(status_code=400, detail="Not a deferred task - use normal completion flow")

    info["status"] = "completed"
    info["end_time"] = _now_iso()
    lanlan_name = info.get("lanlan_name")

    try:
        await _emit_main_event(
            "task_update", lanlan_name,
            task={
                "id": task_id,
                "status": "completed",
                "type": info.get("type"),
                "start_time": info.get("start_time"),
                "end_time": info["end_time"],
                "params": info.get("params", {}),
            },
        )
    except Exception as e:
        logger.warning("[Deferred] emit task_update(complete) failed: task_id=%s error=%s", task_id, e)

    logger.info("[Deferred] Task %s marked completed via callback", task_id)
    return {"ok": True}


# ── OpenFang LLM Proxy ──────────────────────────────────────
# OpenFang 的 Rust LLM driver 严格要求 OpenAI 格式的 completion_tokens 等字段。
# lanlan.app 的 API 可能不返回这些字段，导致 OpenFang parse error。
# 此代理拦截 LLM 请求，转发到真实 API，并在响应中补全缺失字段。

from fastapi import Request
from starlette.responses import StreamingResponse as StarletteStreamingResponse

@app.api_route("/openfang-llm-proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openfang_llm_proxy(request: Request, path: str):
    """
    透明代理：OpenFang → 此端点 → lanlan.app（或用户配置的 agent API）。
    在响应中补全 OpenAI 兼容性字段 (completion_tokens, prompt_tokens 等)。
    """
    # 获取真实 API 地址
    cm = get_config_manager()
    agent_cfg = cm.get_model_api_config('agent')
    real_base_url = (agent_cfg.get("base_url") or "").strip().rstrip("/")
    real_api_key = (agent_cfg.get("api_key") or "").strip()

    if not real_base_url:
        return JSONResponse({"error": "Agent API base_url not configured"}, status_code=502)

    # 智能拼接 URL：避免 /v1/v1 双重路径
    # OpenFang 调用：proxy_base/v1/chat/completions → path="v1/chat/completions"
    # 如果 real_base_url 已含 /v1，则去掉 path 中的 /v1 前缀
    if real_base_url.rstrip("/").endswith("/v1") and path.startswith("v1/"):
        path = path[3:]  # 去掉 "v1/"
    target_url = f"{real_base_url}/{path}"
    # 保留原始请求的 query string
    qs = request.url.query
    if qs:
        target_url = f"{target_url}?{qs}"

    print(f"[LLM Proxy] path={path}, real_base_url={real_base_url}, target_url={target_url}")

    # 读取请求体
    body = await request.body()

    # 构建转发请求头（保留 Content-Type，替换 Authorization）
    forward_headers = {}
    ct = request.headers.get("content-type")
    if ct:
        forward_headers["Content-Type"] = ct
    if real_api_key:
        forward_headers["Authorization"] = f"Bearer {real_api_key}"

    # 检查是否请求流式
    is_stream = False
    if body:
        try:
            req_json = json.loads(body)
            is_stream = req_json.get("stream", False)
        except Exception:
            logger.debug("[LLM Proxy] failed to parse request body for stream detection", exc_info=True)

    try:
        if is_stream:
            # 流式：手动管理 client 生命周期（generator 延迟消费，不能用 async with）
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
            try:
                upstream_resp = await client.send(
                    client.build_request(request.method, target_url, content=body, headers=forward_headers),
                    stream=True,
                )
            except Exception:
                await client.aclose()
                raise
            upstream_status = upstream_resp.status_code

            async def _stream_with_patch():
                try:
                    async for line in upstream_resp.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                chunk = json.loads(line[6:])
                                _patch_openai_response(chunk)
                                yield f"data: {json.dumps(chunk)}\n\n"
                                continue
                            except Exception:
                                logger.debug("[LLM Proxy] failed to parse streaming chunk", exc_info=True)
                        yield line + "\n"
                finally:
                    await upstream_resp.aclose()
                    await client.aclose()

            return StarletteStreamingResponse(
                _stream_with_patch(),
                status_code=upstream_status,
                media_type="text/event-stream",
            )
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                # 非流式：一次性读取并 patch
                resp = await client.request(
                    request.method, target_url,
                    content=body, headers=forward_headers,
                )
                logger.info("[LLM Proxy] upstream response: status=%s, len=%d", resp.status_code, len(resp.content))
                logger.debug("[LLM Proxy] upstream body (first 500): %s", resp.text[:500])
                # 尝试 JSON patch
                try:
                    data = resp.json()
                    _patch_openai_response(data)
                    return JSONResponse(data, status_code=resp.status_code)
                except Exception:
                    # 非 JSON 响应原样返回 (使用 raw Response 避免二次编码)
                    from starlette.responses import Response as RawResponse
                    return RawResponse(
                        content=resp.content,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/octet-stream"),
                    )
    except httpx.TimeoutException:
        return JSONResponse({"error": "Upstream API timeout"}, status_code=504)
    except Exception as e:
        logger.warning("[LLM Proxy] upstream error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=502)


def _patch_openai_response(data: dict) -> None:
    """
    全面修补 OpenAI 兼容响应，解决 OpenFang 严格解析的兼容性问题：
    1. 补全 usage 字段 (completion_tokens 等)
    2. 修复 malformed_function_call → 标准 tool_calls 格式
    3. 确保 message.content 不为 None
    """
    if not isinstance(data, dict):
        return

    _patch_usage(data)
    _patch_malformed_tool_calls(data)


def _patch_usage(data: dict) -> None:
    """补全缺失的 usage 字段。"""
    if not isinstance(data, dict):
        return

    usage = data.get("usage")
    if usage is None:
        data["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return

    if not isinstance(usage, dict):
        return

    if "prompt_tokens" not in usage:
        usage["prompt_tokens"] = 0
    if "completion_tokens" not in usage:
        usage["completion_tokens"] = 0
    if "total_tokens" not in usage:
        usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if usage.get(k) is None:
            usage[k] = 0


def _patch_malformed_tool_calls(data: dict) -> None:
    """
    修复 Gemini/OpenRouter 返回的 malformed_function_call 响应。

    问题：某些模型通过 OpenRouter 时不支持标准 OpenAI function calling，
    输出 `call:tool_name{json_args}` 格式放在 refusal 字段中。
    OpenFang 期望标准的 tool_calls 格式。

    修复：解析 refusal 中的工具调用，转换为标准 tool_calls 数组。
    """
    choices = data.get("choices")
    if not isinstance(choices, list):
        return

    for choice in choices:
        if not isinstance(choice, dict):
            continue

        finish_reason = choice.get("finish_reason", "")
        msg = choice.get("message", {})
        if not isinstance(msg, dict):
            continue

        refusal = msg.get("refusal", "")

        # 检测 malformed function call
        # 某些模型 (Gemini via OpenRouter) 不支持 OpenAI-style function calling round-trip:
        # 即使我们把 malformed call 转成标准 tool_calls，下一轮提交 tool result 时
        # 模型会报 thought_signature 错误。
        # 正确做法：不转 tool_calls，而是提取工具调用意图转为文本内容，
        # 让 OpenFang 用文本模式回复（不走 tool use 循环）。
        if finish_reason == "malformed_function_call" and refusal:
            # 解析 call:tool_name{args} 提取意图，作为文本指令
            intent_text = _extract_tool_intent_as_text(refusal)
            msg["content"] = intent_text
            msg.pop("refusal", None)
            msg.pop("tool_calls", None)  # 确保没有 tool_calls
            choice["finish_reason"] = "stop"
            print("[LLM Proxy] Converted malformed_function_call to text intent")

        # 确保 message.content 为非 null 字符串（有些 API 返回 null 或缺失该字段）
        if "content" not in msg or msg["content"] is None:
            msg["content"] = ""


def _extract_tool_intent_as_text(refusal_text: str) -> str:
    """
    从 malformed function call 中提取工具调用意图，转换为自然语言文本。

    例如:
    输入: "Malformed function call: call:web_search{queries:["中国到日本 机票价格"]}"
    输出: "I'll search for: 中国到日本 机票价格, China to Japan flight prices..."

    这样 OpenFang 可以把这段文字作为 agent 的回复，而不是尝试执行一个不兼容的 tool call。
    """
    import re as _re

    cleaned = refusal_text.replace("Malformed function call: ", "").strip()

    # 提取 call:name{args} 中的 args 部分
    pattern = r'call:(\w+)\s*(\{.*\})'
    match = _re.search(pattern, cleaned, _re.DOTALL)

    if not match:
        return f"I attempted to perform an action but encountered a compatibility issue. Let me provide what I know instead.\n\nContext: {cleaned[:300]}"

    tool_name = match.group(1)
    args_raw = match.group(2)

    # 尝试提取可读的参数内容
    # 常见格式: {queries:["q1","q2",...]} 或 {query:"..."}
    readable_args = []
    # 提取引号中的字符串
    strings = _re.findall(r'"([^"]*)"', args_raw)
    if strings:
        readable_args = strings[:5]  # 最多取5个

    tool_descriptions = {
        "web_search": "search the web for",
        "web_fetch": "fetch the web page",
        "file_read": "read the file",
        "file_write": "write to a file",
        "shell_exec": "run a command",
        "browser_navigate": "navigate to",
    }
    action = tool_descriptions.get(tool_name, f"use {tool_name} for")

    if readable_args:
        args_text = ", ".join(readable_args)
        return (
            f"I wanted to {action}: {args_text}\n\n"
            f"However, due to a model compatibility issue with tool calling, "
            f"I cannot execute this tool directly. "
            f"Based on my knowledge, let me provide what information I can about this topic."
        )
    else:
        return (
            f"I attempted to {action}, but encountered a compatibility issue.\n\n"
            f"Let me provide what information I can based on my existing knowledge."
        )


# ── OpenFang endpoints ──────────────────────────────────────

@app.get("/openfang/availability")
async def openfang_availability():
    """检查 OpenFang 可用性。"""
    if not Modules.openfang:
        return {"enabled": False, "ready": False, "reason": "adapter 未加载"}
    return Modules.openfang.is_available()


@app.get("/openclaw/availability")
async def openclaw_availability():
    if not Modules.openclaw:
        return {"enabled": False, "ready": False, "reasons": ["adapter 未加载"]}
    status = await asyncio.to_thread(Modules.openclaw.is_available)
    ready = bool(status.get("ready")) if isinstance(status, dict) else False
    reasons = status.get("reasons", []) if isinstance(status, dict) else []
    _set_capability("openclaw", ready, reasons[0] if reasons else "")
    if not ready and Modules.agent_flags.get("openclaw_enabled"):
        Modules.agent_flags["openclaw_enabled"] = False
        Modules.notification = json.dumps({
            "code": "AGENT_OPENCLAW_CAPABILITY_LOST",
            "details": {"reason_code": reasons[0] if reasons else "unknown"},
        })
    return status


@app.post("/openfang/run")
async def openfang_run(payload: Dict[str, Any]):
    """直接通过 OpenFang 执行任务 (绕过路由决策)。"""
    instruction = payload.get("instruction")
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)
    if not Modules.openfang or not Modules.openfang.init_ok:
        return JSONResponse({"error": "VM agent not available"}, status_code=503)

    task_id = f"of_{uuid.uuid4().hex[:12]}"

    _lanlan = payload.get("lanlan_name")

    async def _run():
        try:
            Modules.task_registry[task_id] = {
                "id": task_id, "type": "openfang", "status": "running",
                "params": {"instruction": instruction},
                "lanlan_name": _lanlan,
                "session_id": payload.get("conversation_id"),
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
            # Emit initial running event with full task object
            try:
                await _emit_main_event(
                    "task_update", _lanlan,
                    task_id=task_id, channel="openfang",
                    task=Modules.task_registry[task_id],
                )
            except Exception:
                logger.debug("[OpenFang] initial task_update emit failed", exc_info=True)

            def _on_progress(info):
                try:
                    reg = Modules.task_registry.get(task_id, {})
                    reg["status"] = info.get("status", reg.get("status", "running"))
                    reg["elapsed"] = info.get("elapsed", 0)
                    asyncio.create_task(_emit_main_event(
                        "task_update", _lanlan,
                        task_id=task_id, channel="openfang",
                        task=reg,
                    ))
                except Exception as e:
                    logger.debug("[OpenFang] _on_progress emit failed: %s", e)

            result = await Modules.openfang.run_instruction(
                instruction=instruction,
                session_id=payload.get("conversation_id"),
                on_progress=_on_progress,
                local_task_id=task_id,
            )
            final_status = "completed" if result.get("success") else "failed"
            reg = Modules.task_registry[task_id]
            reg["status"] = final_status
            reg["result"] = result
            reg["end_time"] = datetime.now(timezone.utc).isoformat()
            _r = result if isinstance(result, dict) else {}
            _success = _r.get("success", False)
            _result_text = _r.get("result", "") or ""
            _error_text = _r.get("error", "") or ""
            if not _success:
                reg["error"] = _error_text

            await _emit_task_result(
                _lanlan,
                channel="openfang",
                task_id=task_id,
                success=_success,
                summary=_result_text[:500],
                detail=_result_text,
                error_message=_error_text,
            )
            # Terminal task_update so HUD transitions out of running
            try:
                await _emit_main_event(
                    "task_update", _lanlan,
                    task_id=task_id, channel="openfang",
                    task=reg,
                )
            except Exception:
                logger.debug("[OpenFang] terminal task_update emit failed", exc_info=True)
        except Exception as e:
            logger.error("[OpenFang] Task %s failed: %s", task_id, e)
            reg = Modules.task_registry[task_id]
            reg["status"] = "failed"
            reg["error"] = str(e)
            reg["end_time"] = datetime.now(timezone.utc).isoformat()
            try:
                await _emit_task_result(
                    _lanlan,
                    channel="openfang",
                    task_id=task_id,
                    success=False,
                    summary="",
                    error_message=str(e),
                )
            except Exception:
                logger.debug("[OpenFang] terminal task_result emit failed", exc_info=True)
            try:
                await _emit_main_event(
                    "task_update", _lanlan,
                    task_id=task_id, channel="openfang",
                    task=reg,
                )
            except Exception:
                logger.debug("[OpenFang] terminal task_update emit failed", exc_info=True)

    bg = asyncio.create_task(_run())
    Modules.task_async_handles[task_id] = bg
    Modules._background_tasks.add(bg)
    def _cleanup_of_bg(_t, _tid=task_id):
        Modules._background_tasks.discard(_t)
        Modules.task_async_handles.pop(_tid, None)
    bg.add_done_callback(_cleanup_of_bg)

    return {"success": True, "task_id": task_id, "status": "running"}


@app.post("/openfang/sync_config")
async def openfang_sync_config():
    """手动触发 API Key 配置同步到 OpenFang。"""
    if not Modules.openfang:
        return {"success": False, "error": "adapter 未加载"}
    ok = await Modules.openfang.sync_config()
    return {"success": ok}


@app.get("/capabilities")
async def capabilities():
    return {"success": True, "capabilities": {}}


@app.get("/agent/flags")
async def get_agent_flags():
    """获取当前 agent flags 状态（供前端同步）"""
    note = Modules.notification
    # Read-once notification
    if Modules.notification:
        Modules.notification = None
        
    return {
        "success": True, 
        "agent_flags": Modules.agent_flags,
        "analyzer_enabled": Modules.analyzer_enabled,
        "agent_api_gate": _check_agent_api_gate(),
        "revision": Modules.state_revision,
        "notification": note
    }


@app.get("/agent/state")
async def get_agent_state():
    if not Modules.task_executor:
        raise HTTPException(503, "Task executor not ready")
    snapshot = _collect_agent_status_snapshot()
    return {"success": True, "snapshot": snapshot}


@app.post("/agent/flags")
async def set_agent_flags(payload: Dict[str, Any]):
    lanlan_name = (payload or {}).get("lanlan_name")
    cf = (payload or {}).get("computer_use_enabled")
    bf = (payload or {}).get("browser_use_enabled")
    uf = (payload or {}).get("user_plugin_enabled")
    nf = (payload or {}).get("openclaw_enabled")
    # Agent API gate: if any agent sub-feature is being enabled, gate must pass.
    gate = _check_agent_api_gate()
    changed = False
    old_flags = dict(Modules.agent_flags)
    old_analyzer_enabled = bool(Modules.analyzer_enabled)
    of = (payload or {}).get("openfang_enabled")
    if gate.get("ready") is not True and any(x is True for x in (cf, bf, uf, nf, of)):
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.agent_flags["browser_use_enabled"] = False
        Modules.agent_flags["user_plugin_enabled"] = False
        Modules.agent_flags["openclaw_enabled"] = False
        Modules.agent_flags["openfang_enabled"] = False
        first_reason = (gate.get('reasons') or ['AGENT_ENDPOINT_NOT_CONFIGURED'])[0]
        _set_capability("computer_use", False, first_reason)
        _set_capability("browser_use", False, first_reason)
        _set_capability("user_plugin", False, first_reason)
        _set_capability("openclaw", False, first_reason)
        _set_capability("openfang", False, first_reason)
        await _ensure_plugin_lifecycle_stopped()
        Modules.notification = None
        if Modules.agent_flags != old_flags:
            _bump_state_revision()
            await _emit_agent_status_update(lanlan_name=lanlan_name)
        return {"success": True, "agent_flags": Modules.agent_flags}

    prev_up = Modules.agent_flags.get("user_plugin_enabled", False)
    prev_nk = Modules.agent_flags.get("openclaw_enabled", False)

    # 1. Handle Computer Use Flag with Capability Check
    if isinstance(cf, bool):
        if cf: # Attempting to enable
            if not Modules.computer_use:
                _try_refresh_computer_use_adapter(force=True)
            if not Modules.computer_use:
                Modules.agent_flags["computer_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_CU_MODULE_NOT_LOADED"})
                logger.warning("[Agent] Cannot enable Computer Use: Module not loaded")
            elif not getattr(Modules.computer_use, "init_ok", False):
                Modules.agent_flags["computer_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_CU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                try:
                    avail = Modules.computer_use.is_available()
                    reasons = avail.get('reasons', []) if isinstance(avail, dict) else []
                    _set_capability("computer_use", bool(avail.get("ready")) if isinstance(avail, dict) else False, reasons[0] if reasons else "")
                    if avail.get("ready"):
                        Modules.agent_flags["computer_use_enabled"] = True
                    else:
                        Modules.agent_flags["computer_use_enabled"] = False
                        reason = avail.get('reasons', [])[0] if avail.get('reasons') else 'unknown'
                        Modules.notification = json.dumps({"code": "AGENT_CU_UNAVAILABLE", "details": {"reason_code": reason}})
                        logger.warning(f"[Agent] Cannot enable Computer Use: {avail.get('reasons')}")
                except Exception as e:
                    Modules.agent_flags["computer_use_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_CU_ENABLE_FAILED", "details": {"error": str(e)}})
                    logger.error(f"[Agent] Cannot enable Computer Use: Check failed {e}")
        else: # Disabling
            Modules.agent_flags["computer_use_enabled"] = False

    # 2.5. Handle Browser Use Flag with Capability Check
    if isinstance(bf, bool):
        if bf:
            bu = getattr(Modules, "browser_use", None)
            if not bu:
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_BU_MODULE_NOT_LOADED"})
            elif not getattr(bu, "_ready_import", False):
                Modules.agent_flags["browser_use_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_BU_NOT_INSTALLED", "details": {"error": str(bu.last_error)}})
            elif not getattr(Modules.computer_use, "init_ok", False):
                Modules.agent_flags["browser_use_enabled"] = True
                Modules.notification = json.dumps({"code": "AGENT_BU_ENABLED_CHECKING"})
                asyncio.ensure_future(_fire_agent_llm_connectivity_check())
            else:
                Modules.agent_flags["browser_use_enabled"] = True
                _set_capability("browser_use", True, "")
        else:
            Modules.agent_flags["browser_use_enabled"] = False
            
    if isinstance(uf, bool):
        if uf:  # Attempting to enable UserPlugin — non-blocking (like CUA)
            Modules.agent_flags["user_plugin_enabled"] = True
            Modules.notification = json.dumps({"code": "AGENT_UP_ENABLED_CHECKING"})

            async def _bg_plugin_enable():
                _ln = lanlan_name
                try:
                    started = await _ensure_plugin_lifecycle_started()
                    if not started:
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                        logger.warning("[Agent] Cannot enable UserPlugin: lifecycle startup failed")
                        _bump_state_revision()
                        await _emit_agent_status_update(lanlan_name=_ln)
                        return

                    plugins = []
                    for _attempt in range(8):
                        await asyncio.sleep(0.5)
                        try:
                            async with httpx.AsyncClient(timeout=1.0, proxy=None, trust_env=False) as client:
                                r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
                                if r.status_code == 200:
                                    data = r.json()
                                    plugins = data.get("plugins", []) if isinstance(data, dict) else []
                                    if plugins:
                                        break
                        except Exception:
                            pass

                    if not plugins:
                        Modules.agent_flags["user_plugin_enabled"] = False
                        Modules.notification = json.dumps({"code": "AGENT_NO_PLUGINS_FOUND"})
                        logger.warning("[Agent] Cannot enable UserPlugin: no plugins found after lifecycle start")
                        await _ensure_plugin_lifecycle_stopped()
                    else:
                        _set_capability("user_plugin", True, "")
                        logger.info("[Agent] UserPlugin lifecycle ready (%d plugins)", len(plugins))
                except Exception as exc:
                    Modules.agent_flags["user_plugin_enabled"] = False
                    Modules.notification = json.dumps({"code": "AGENT_PLUGIN_SERVER_ERROR"})
                    logger.error("[Agent] Background plugin enable failed: %s", exc)
                finally:
                    _bump_state_revision()
                    await _emit_agent_status_update(lanlan_name=_ln)

            _bg = asyncio.create_task(_bg_plugin_enable())
            Modules._persistent_tasks.add(_bg)
            _bg.add_done_callback(Modules._persistent_tasks.discard)
        else:  # Disabling UserPlugin — non-blocking
            Modules.agent_flags["user_plugin_enabled"] = False
            _set_capability("user_plugin", True, "")

            async def _bg_plugin_disable():
                try:
                    await _ensure_plugin_lifecycle_stopped()
                except Exception as exc:
                    logger.warning("[Agent] Background plugin disable error: %s", exc)

            _bg = asyncio.create_task(_bg_plugin_disable())
            Modules._persistent_tasks.add(_bg)
            _bg.add_done_callback(Modules._persistent_tasks.discard)

    if isinstance(nf, bool):
        if nf:
            adapter = Modules.openclaw
            if not adapter:
                Modules.agent_flags["openclaw_enabled"] = False
                Modules.notification = json.dumps({"code": "AGENT_OPENCLAW_MODULE_NOT_LOADED"})
            else:
                status = await asyncio.to_thread(adapter.is_available)
                ready = bool(status.get("ready")) if isinstance(status, dict) else False
                reasons = status.get("reasons", []) if isinstance(status, dict) else []
                _set_capability("openclaw", ready, reasons[0] if reasons else "")
                if ready:
                    Modules.agent_flags["openclaw_enabled"] = True
                else:
                    Modules.agent_flags["openclaw_enabled"] = False
                    Modules.notification = json.dumps({
                        "code": "AGENT_OPENCLAW_UNAVAILABLE",
                        "details": {"reason_code": reasons[0] if reasons else "unknown"},
                    })
        else:
            Modules.agent_flags["openclaw_enabled"] = False

    try:
        new_up = Modules.agent_flags.get("user_plugin_enabled", False)
        if prev_up != new_up:
            logger.info("[Agent] user_plugin_enabled toggled %s via /agent/flags", "ON" if new_up else "OFF")
    except Exception:
        pass
    try:
        new_nk = Modules.agent_flags.get("openclaw_enabled", False)
        if prev_nk != new_nk:
            logger.info("[Agent] openclaw_enabled toggled %s via /agent/flags", "ON" if new_nk else "OFF")
    except Exception:
        pass

    # 4. Handle OpenFang Flag
    if isinstance(of, bool):
        if of:
            adapter = Modules.openfang
            if adapter and adapter.init_ok:
                Modules.agent_flags["openfang_enabled"] = True
                _set_capability("openfang", True, "")
            elif adapter:
                # init_ok 为 False，尝试重新连接
                ok = await asyncio.to_thread(adapter.check_connectivity)
                if ok:
                    _set_capability("openfang", True, "")
                    Modules.agent_flags["openfang_enabled"] = True
                    logger.info("[Agent] OpenFang re-connected on toggle")
                else:
                    Modules.agent_flags["openfang_enabled"] = False
                    _set_capability("openfang", False, "OPENFANG_DAEMON_UNREACHABLE")
                    logger.warning("[Agent] Cannot enable OpenFang: not connected (%s)", adapter.last_error)
            else:
                Modules.agent_flags["openfang_enabled"] = False
                logger.warning("[Agent] Cannot enable OpenFang: adapter not initialized")
        else:
            Modules.agent_flags["openfang_enabled"] = False
            # Cancel any in-flight openfang tasks
            if Modules.openfang:
                try:
                    await Modules.openfang.cancel_running(None)
                except Exception as e:
                    logger.warning("[Agent] OpenFang cancel on disable failed: %s", e)

    changed = Modules.agent_flags != old_flags or bool(Modules.analyzer_enabled) != old_analyzer_enabled
    if changed:
        _bump_state_revision()
    await _emit_agent_status_update(lanlan_name=lanlan_name)
    return {"success": True, "agent_flags": Modules.agent_flags}


@app.post("/agent/command")
async def agent_command(payload: Dict[str, Any]):
    t0 = time.perf_counter()
    request_id = (payload or {}).get("request_id") or str(uuid.uuid4())
    command = (payload or {}).get("command")
    lanlan_name = (payload or {}).get("lanlan_name")
    if command == "set_agent_enabled":
        enabled = bool((payload or {}).get("enabled"))
        if enabled:
            Modules.analyzer_enabled = True
            Modules.analyzer_profile = (payload or {}).get("profile", {}) or {}
        else:
            Modules.analyzer_enabled = False
            Modules.analyzer_profile = {}
            Modules.agent_flags["computer_use_enabled"] = False
            Modules.agent_flags["browser_use_enabled"] = False
            Modules.agent_flags["user_plugin_enabled"] = False
            Modules.agent_flags["openclaw_enabled"] = False
            Modules.agent_flags["openfang_enabled"] = False
            _set_capability("user_plugin", True, "")
            await admin_control({"action": "end_all"})
            await _ensure_plugin_lifecycle_stopped()
        _bump_state_revision()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {"success": True, "request_id": request_id, "timing": {"agent_total_ms": total_ms}}
    if command == "set_flag":
        key = (payload or {}).get("key")
        value = bool((payload or {}).get("value"))
        if key not in {"computer_use_enabled", "browser_use_enabled", "user_plugin_enabled", "openclaw_enabled", "openfang_enabled"}:
            raise HTTPException(400, "invalid flag key")
        t_set = time.perf_counter()
        await set_agent_flags({"lanlan_name": lanlan_name, key: value})
        set_ms = round((time.perf_counter() - t_set) * 1000, 2)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s key=%s set_flags_ms=%s total_ms=%s", request_id, command, key, set_ms, total_ms)
        return {"success": True, "request_id": request_id, "timing": {"set_flags_ms": set_ms, "agent_total_ms": total_ms}}
    if command == "refresh_state":
        snapshot = _collect_agent_status_snapshot()
        await _emit_agent_status_update(lanlan_name=lanlan_name)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.info("[AgentTiming] request_id=%s command=%s total_ms=%s", request_id, command, total_ms)
        return {"success": True, "request_id": request_id, "snapshot": snapshot, "timing": {"agent_total_ms": total_ms}}
    raise HTTPException(400, "unknown command")


@app.get("/computer_use/availability")
async def computer_use_availability():
    gate = _check_agent_api_gate()
    if gate.get("ready") is not True:
        return {"ready": False, "reasons": gate.get("reasons", ["Agent API 未配置"])}
    if not Modules.computer_use:
        _try_refresh_computer_use_adapter(force=True)
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    if not Modules.computer_use:
        if Modules.agent_flags.get("computer_use_enabled"):
            Modules.agent_flags["computer_use_enabled"] = False
            Modules.notification = json.dumps({"code": "AGENT_CU_AUTO_CLOSED"})
        raise HTTPException(503, "ComputerUse not ready")
    if not getattr(Modules.computer_use, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())

    status = Modules.computer_use.is_available()
    reasons = status.get("reasons", []) if isinstance(status, dict) else []
    _set_capability("computer_use", bool(status.get("ready")) if isinstance(status, dict) else False, reasons[0] if reasons else "")
    
    # Auto-update flag if capability lost
    if not status.get("ready") and Modules.agent_flags.get("computer_use_enabled"):
        logger.info("[Agent] Computer Use capability lost, disabling flag")
        Modules.agent_flags["computer_use_enabled"] = False
        Modules.notification = json.dumps({"code": "AGENT_CU_CAPABILITY_LOST", "details": {"reason_code": status.get('reasons', [])[0] if status.get('reasons') else 'unknown'}})
        
    return status


@app.post("/notify_config_changed")
async def notify_config_changed():
    """Called by the main server after API-key / model config is saved.
    Rebuilds the CUA adapter with fresh config and kicks off a non-blocking
    LLM connectivity check."""
    _try_refresh_computer_use_adapter(force=True)
    _rewire_computer_use_dependents()
    asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    return {"success": True, "message": "CUA adapter refreshed, connectivity check started"}


@app.get("/browser_use/availability")
async def browser_use_availability():
    gate = _check_agent_api_gate()
    if gate.get("ready") is not True:
        return {"ready": False, "reasons": gate.get("reasons", ["Agent API 未配置"])}
    bu = Modules.browser_use
    if not bu:
        raise HTTPException(503, "BrowserUse not ready")
    if not getattr(bu, "_ready_import", False):
        reason = f"browser-use not installed: {bu.last_error}"
        _set_capability("browser_use", False, reason)
        return {"enabled": True, "ready": False, "reasons": [reason], "provider": "browser-use"}
    # LLM connectivity — reuse the shared agent-LLM check
    cua = Modules.computer_use
    if cua and not getattr(cua, "init_ok", False):
        asyncio.ensure_future(_fire_agent_llm_connectivity_check())
    llm_ok = cua is not None and getattr(cua, "init_ok", False)
    reasons = []
    if not llm_ok:
        reasons.append(cua.last_error if cua and cua.last_error else "Agent LLM not connected")
    ready = llm_ok and getattr(bu, "_ready_import", False)
    _set_capability("browser_use", ready, reasons[0] if reasons else "")
    return {"enabled": True, "ready": ready, "reasons": reasons, "provider": "browser-use"}


@app.post("/computer_use/run")
async def computer_use_run(payload: Dict[str, Any]):
    if not Modules.computer_use:
        raise HTTPException(503, "ComputerUse not ready")
    instruction = (payload or {}).get("instruction", "").strip()
    screenshot_b64 = (payload or {}).get("screenshot_b64")
    if not instruction:
        raise HTTPException(400, "instruction required")
    import base64
    screenshot = base64.b64decode(screenshot_b64) if isinstance(screenshot_b64, str) else None
    # Preflight readiness check to avoid scheduling tasks that will fail immediately
    try:
        avail = Modules.computer_use.is_available()
        if not avail.get("ready"):
            return JSONResponse(content={"success": False, "error": "ComputerUse not ready", "reasons": avail.get("reasons", [])}, status_code=503)
    except Exception as e:
        return JSONResponse(content={"success": False, "error": f"availability check failed: {e}"}, status_code=503)
    lanlan_name = (payload or {}).get("lanlan_name")
    # Dedup check
    dup, matched = await _is_duplicate_task(instruction, lanlan_name)
    if dup:
        return JSONResponse(content={"success": False, "duplicate": True, "matched_id": matched}, status_code=409)
    info = _spawn_task("computer_use", {"instruction": instruction, "screenshot": screenshot})
    info["lanlan_name"] = lanlan_name
    return {"success": True, "task_id": info["id"], "status": info["status"], "start_time": info["start_time"]}


@app.post("/browser_use/run")
async def browser_use_run(payload: Dict[str, Any]):
    if not Modules.browser_use:
        raise HTTPException(503, "BrowserUse not ready")
    instruction = (payload or {}).get("instruction", "").strip()
    if not instruction:
        raise HTTPException(400, "instruction required")
    try:
        result = await Modules.browser_use.run_instruction(instruction)
        return {"success": bool(result.get("success", False)), "result": result}
    except Exception as e:
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@app.get("/mcp/availability")
async def mcp_availability():
    return {"ready": False, "capabilities_count": 0, "reasons": ["MCP 已移除"]}


@app.get("/tasks")
async def list_tasks():
    """快速返回当前所有任务状态，优化响应速度"""
    items = []
    
    try:
        for tid, info in Modules.task_registry.items():
            try:
                task_item = {
                    "id": info.get("id", tid),
                    "type": info.get("type"),
                    "status": info.get("status"),
                    "start_time": info.get("start_time"),
                    "params": info.get("params"),
                    "result": info.get("result"),
                    "error": info.get("error"),
                    "lanlan_name": info.get("lanlan_name"),
                    "source": "runtime"
                }
                items.append(task_item)
            except Exception:
                continue
        
        debug_info = {
            "task_registry_count": len(Modules.task_registry),
            "total_returned": len(items)
        }
        
        return {"tasks": items, "debug": debug_info}
    
    except Exception as e:
        return {
            "tasks": items,
            "debug": {
                "error": str(e),
                "partial_results": True,
                "total_returned": len(items)
            }
        }


@app.post("/admin/control")
async def admin_control(payload: Dict[str, Any]):
    action = (payload or {}).get("action")
    if action == "end_all":
        # Cancel any in-flight background analyzer tasks
        tasks_to_await = []
        for t in list(Modules._background_tasks):
            if not t.done():
                t.cancel()
                tasks_to_await.append(t)
        if tasks_to_await:
            results = await asyncio.gather(*tasks_to_await, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                    logger.warning(f"[Agent] Error awaiting cancelled background task: {res}")
        Modules._background_tasks.clear()

        # Signal computer-use adapter to cancel at next step boundary
        if Modules.computer_use:
            Modules.computer_use.cancel_running()

        # Cancel any in-flight asyncio tasks and clear registry
        if Modules.active_computer_use_async_task and not Modules.active_computer_use_async_task.done():
            Modules.active_computer_use_async_task.cancel()
            try:
                await Modules.active_computer_use_async_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[Agent] Error awaiting cancelled computer use task: {e}")

        # Wait for the underlying thread to actually finish before clearing state,
        # so no pyautogui calls are still in-flight when we allow new tasks.
        cu = Modules.computer_use
        if cu is not None and hasattr(cu, "wait_for_completion"):
            loop = asyncio.get_running_loop()
            finished = await loop.run_in_executor(None, cu.wait_for_completion, 10.0)
            if not finished:
                logger.warning("[Agent] CUA thread did not stop within 10s during end_all")

        Modules.task_registry.clear()
        Modules.last_user_turn_fingerprint.clear()
        # Clear scheduling state
        Modules.computer_use_running = False
        Modules.active_computer_use_task_id = None
        Modules.active_computer_use_async_task = None
        # Drain the asyncio scheduler queue
        try:
            if Modules.computer_use_queue is not None:
                while not Modules.computer_use_queue.empty():
                    await Modules.computer_use_queue.get()
        except Exception:
            pass
        # Signal browser-use adapter to cancel at next step boundary
        try:
            if Modules.browser_use:
                Modules.browser_use.cancel_running()
                Modules.browser_use._stop_overlay()
                Modules.browser_use._agents.clear()
                try:
                    if Modules.browser_use._browser_session is not None:
                        await Modules.browser_use._remove_overlay(Modules.browser_use._browser_session)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[Agent] Error cleaning browser-use agents during end_all: {e}")
        Modules.active_browser_use_task_id = None
        # Cancel any in-flight openfang tasks
        try:
            if Modules.openfang:
                await Modules.openfang.cancel_running(None)
        except Exception as e:
            logger.warning(f"[Agent] Error cancelling openfang tasks during end_all: {e}")
        # Reset computer-use step history so stale context is cleared
        try:
            if Modules.computer_use:
                Modules.computer_use.reset()
        except Exception:
            pass
        return {"success": True, "message": "all tasks terminated and cleared"}
    elif action == "enable_analyzer":
        Modules.analyzer_enabled = True
        Modules.analyzer_profile = (payload or {}).get("profile", {})
        return {"success": True, "analyzer_enabled": True, "profile": Modules.analyzer_profile}
    elif action == "disable_analyzer":
        Modules.analyzer_enabled = False
        Modules.analyzer_profile = {}
        # cascade end_all
        await admin_control({"action": "end_all"})
        return {"success": True, "analyzer_enabled": False}
    else:
        raise HTTPException(400, "unknown action")


if __name__ == "__main__":
    import uvicorn
    import logging  # 仍需要用于uvicorn的过滤器
    
    # 使用统一的速率限制日志过滤器
    from utils.logger_config import create_agent_server_filter
    
    # Add filter to uvicorn access logger (uvicorn仍使用标准logging)
    logging.getLogger("uvicorn.access").addFilter(create_agent_server_filter())
    
    _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=TOOL_SERVER_PORT,
        proxy_headers=_behind_proxy,
        forwarded_allow_ips="*" if _behind_proxy else None,
    )
