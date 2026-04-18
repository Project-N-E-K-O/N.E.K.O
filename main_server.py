# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows multiprocessing 支持：确保子进程不会重复执行模块级初始化
from multiprocessing import freeze_support
import multiprocessing
from utils.port_utils import set_port_probe_reuse
freeze_support()

# 设置 multiprocessing 启动方法（确保跨进程共享结构的一致性）
# 在 Linux/macOS 上使用 fork，在 Windows 上使用 spawn（默认）
if sys.platform != "win32":
    try:
        multiprocessing.set_start_method('fork', force=False)
    except RuntimeError:
        # 启动方法已经设置过，忽略
        pass

# 检查是否需要执行初始化（用于防止 Windows spawn 方式创建的子进程重复初始化）
# 方案：首次导入时设置环境变量标记，子进程会继承这个标记从而跳过初始化
_INIT_MARKER = '_NEKO_MAIN_SERVER_INITIALIZED'
_IS_MAIN_PROCESS = _INIT_MARKER not in os.environ

if _IS_MAIN_PROCESS:
    # 立即设置标记，这样任何从此进程 spawn 的子进程都会继承此标记
    os.environ[_INIT_MARKER] = '1'

# 获取应用程序根目录（与 config_manager 保持一致）
def _get_app_root():
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()

# 仅在 Windows 上调整 DLL 搜索路径
if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    os.add_dll_directory(_get_app_root())
    
import mimetypes # noqa
mimetypes.add_type("application/javascript", ".js")
import asyncio # noqa
import logging # noqa
import atexit # noqa
import httpx # noqa
from config import MAIN_SERVER_PORT, MONITOR_SERVER_PORT # noqa
from utils.config_manager import get_config_manager, get_reserved # noqa
# 将日志初始化提前，确保导入阶段异常也能落盘
from utils.logger_config import setup_logging # noqa: E402
from utils.ssl_env_diagnostics import probe_ssl_environment, write_ssl_diagnostic # noqa: E402

logger, log_config = setup_logging(service_name="Main", log_level=logging.INFO, silent=not _IS_MAIN_PROCESS)

if _IS_MAIN_PROCESS:
    _ssl_precheck = probe_ssl_environment()
    if not _ssl_precheck.get("ok", True):
        diag_dir = os.path.join(log_config.get_log_directory_path(), "diagnostics")
        diag_path = write_ssl_diagnostic(
            event="main_server_ssl_precheck_failed",
            output_dir=diag_dir,
            extra=_ssl_precheck,
        )
        logger.warning(
            "SSL environment precheck failed: %s%s",
            _ssl_precheck.get("error_message"),
            f" | diagnostic: {diag_path}" if diag_path else "",
        )

try:
    from fastapi import FastAPI # noqa
    from fastapi.staticfiles import StaticFiles # noqa
    from main_logic import core as core, cross_server as cross_server # noqa
    from main_logic.agent_event_bus import MainServerAgentBridge, notify_analyze_ack, set_main_bridge # noqa
    from fastapi.templating import Jinja2Templates # noqa
    from threading import Thread, Event as ThreadEvent # noqa
    from queue import Queue, Empty as QueueEmpty # noqa
    from dataclasses import dataclass # noqa
    from typing import Any, Optional # noqa
except Exception as e:
    logger.exception(f"[Main] Module import failed during startup: {e}")
    raise

# 导入创意工坊工具模块
from utils.workshop_utils import ( # noqa
    get_workshop_root,
    get_workshop_path
)
# 导入创意工坊路由中的函数
from main_routers.workshop_router import get_subscribed_workshop_items, sync_workshop_character_cards, warmup_ugc_cache # noqa

# 确定 templates 目录位置（使用 _get_app_root）
template_dir = _get_app_root()

templates = Jinja2Templates(directory=template_dir)

def initialize_steamworks():
    try:
        # 明确读取steam_appid.txt文件以获取应用ID
        app_id = None
        app_id_file = os.path.join(_get_app_root(), 'steam_appid.txt')
        if os.path.exists(app_id_file):
            with open(app_id_file, 'r') as f:
                app_id = f.read().strip()
            print(f"从steam_appid.txt读取到应用ID: {app_id}")
        
        # 创建并初始化Steamworks实例
        from steamworks import STEAMWORKS
        steamworks = STEAMWORKS()
        # 显示Steamworks初始化过程的详细日志
        print("正在初始化Steamworks...")
        steamworks.initialize()
        steamworks.UserStats.RequestCurrentStats()
        # 初始化后再次获取应用ID以确认
        actual_app_id = steamworks.app_id
        print(f"Steamworks初始化完成，实际使用的应用ID: {actual_app_id}")
        
        # 检查全局logger是否已初始化，如果已初始化则记录成功信息
        if 'logger' in globals():
            logger.info(f"Steamworks初始化成功，应用ID: {actual_app_id}")
            logger.info(f"Steam客户端运行状态: {steamworks.IsSteamRunning()}")
            logger.info(f"Steam覆盖层启用状态: {steamworks.IsOverlayEnabled()}")
        
        return steamworks
    except Exception as e:
        # 检查全局logger是否已初始化，如果已初始化则记录错误，否则使用print
        error_msg = f"初始化Steamworks失败: {e}"
        if 'logger' in globals():
            logger.error(error_msg)
        else:
            print(error_msg)
        return None

def get_default_steam_info():
    global steamworks
    # 检查steamworks是否初始化成功
    if steamworks is None:
        print("Steamworks not initialized. Skipping Steam functionality.")
        if 'logger' in globals():
            logger.info("Steamworks not initialized. Skipping Steam functionality.")
        return
    
    try:
        my_steam64 = steamworks.Users.GetSteamID()
        my_steam_level = steamworks.Users.GetPlayerSteamLevel()
        subscribed_apps = steamworks.Workshop.GetNumSubscribedItems()
        print(f'Subscribed apps: {subscribed_apps}')

        print(f'Logged on as {my_steam64}, level: {my_steam_level}')
        print('Is subscribed to current app?', steamworks.Apps.IsSubscribed())
    except Exception as e:
        print(f"Error accessing Steamworks API: {e}")
        if 'logger' in globals():
            logger.error(f"Error accessing Steamworks API: {e}")

# Steamworks 初始化将在 @app.on_event("startup") 中延迟执行
# 这样可以避免在模块导入时就执行 DLL 加载等操作
steamworks = None
_server_loop: asyncio.AbstractEventLoop | None = None

_config_manager = get_config_manager()

@dataclass
class RoleState:
    """单个 catgirl 的 per-k 运行态容器。

    把之前 6 张并列 module-global dict（sync_message_queue / sync_shutdown_event /
    session_id / sync_process / websocket_locks / session_manager）合并成一个
    record，由 role_state[k] 统一持有，避免半初始化状态 + 维护成本分散。
    见 issue #857 / PR #855 review。

    不变量：
    - sync_message_queue / sync_shutdown_event / websocket_lock 在
      _ensure_character_slots 一次性构造，之后**永不替换**。特别是
      websocket_lock —— 替换会让已经 ``async with`` 进来的协程阻塞在一把
      孤立的旧 Lock 上；如果任何逻辑需要整体重建 role_state[k]，必须
      把旧 lock 原样传过去。
    - session_id / sync_process / session_manager 初始为 None，分别由
      websocket_router / _init_character_resources 后续赋值。
    """
    sync_message_queue: Queue
    sync_shutdown_event: ThreadEvent
    websocket_lock: asyncio.Lock
    session_id: Optional[str] = None
    sync_process: Optional[Thread] = None
    # 用 Any 而非 core.LLMSessionManager：避免 dataclass 运行时求值 annotation
    # 时踩到 forward-ref / 循环引用边界
    session_manager: Optional[Any] = None


# 角色名 -> RoleState 的主存储；所有 per-k 同步资源都通过它访问
role_state: dict[str, RoleState] = {}


def cleanup():
    """通知所有同步线程停止"""
    logger.info("正在关闭同步线程...")
    for rs in role_state.values():
        try:
            rs.sync_shutdown_event.set()
        except Exception:
            pass

# 只在主进程中注册 cleanup 函数，防止子进程退出时执行清理
if _IS_MAIN_PROCESS:
    atexit.register(cleanup)
# 角色数据全局变量（会在重载时更新）
master_name = None
her_name = None
master_basic_config = None
lanlan_basic_config = None
name_mapping = None
lanlan_prompt = None
time_store = None
setting_store = None
recent_log = None
catgirl_names = []
agent_event_bridge: MainServerAgentBridge | None = None


def _is_websocket_connected(ws) -> bool:
    """Check if a WebSocket is in CONNECTED state."""
    if not ws:
        return False
    if not hasattr(ws, "client_state"):
        return False
    try:
        return ws.client_state == ws.client_state.CONNECTED
    except Exception:
        return False


def _iter_session_managers():
    """Yield (name, session_manager) for every role with a live session_manager.

    Replaces the old ``session_manager.items()`` pattern after the per-k dicts
    were consolidated into ``role_state``.
    """
    for name, rs in role_state.items():
        if rs.session_manager is not None:
            yield name, rs.session_manager


def _get_session_manager(name):
    """Return ``role_state[name].session_manager`` or None — dict.get() equivalent."""
    if not name:
        return None
    rs = role_state.get(name)
    return rs.session_manager if rs is not None else None


def _select_fallback_session_manager():
    """Return a single connected session manager as a safe fallback, if unambiguous."""
    connected = []
    for name, mgr in _iter_session_managers():
        ws = getattr(mgr, "websocket", None)
        if _is_websocket_connected(ws):
            connected.append((name, mgr))
    if len(connected) == 1:
        return connected[0]
    return None, None


async def _broadcast_to_all_connected(event_payload: dict) -> int:
    """Broadcast an event to all connected WebSocket sessions in parallel.
    每秒可能多次（agent status），串行 await 会让一个慢的 ws 拖累其它会话。"""
    # Take a snapshot to avoid RuntimeError from concurrent dict mutation
    targets = [
        (name, getattr(mgr, "websocket", None))
        for name, mgr in list(_iter_session_managers())
        if mgr
    ]
    targets = [(n, ws) for n, ws in targets if _is_websocket_connected(ws) and hasattr(ws, "send_json")]

    async def _send_one(name, ws):
        try:
            await ws.send_json(event_payload)
            return True
        except Exception as e:
            logger.debug("[EventBus] broadcast to %s failed: %s", name, e)
            return False

    results = await asyncio.gather(*(_send_one(n, ws) for n, ws in targets), return_exceptions=False)
    return sum(1 for r in results if r is True)


async def _handle_agent_event(event: dict):
    """通过 ZeroMQ 接收 agent_server 事件，并分发到 core/websocket。"""
    try:
        event_type = event.get("event_type")
        lanlan = event.get("lanlan_name")

        if event_type == "analyze_ack":
            logger.info(
                "[EventBus] analyze_ack received on main: event_id=%s lanlan=%s",
                event.get("event_id"),
                lanlan,
            )
            notify_analyze_ack(str(event.get("event_id") or ""))
            return

        # Agent status updates may be broadcast (lanlan_name omitted).
        if event_type == "agent_status_update":
            payload = {
                "type": "agent_status_update",
                "snapshot": event.get("snapshot", {}),
            }
            mgr_for_status = _get_session_manager(lanlan)
            if lanlan and mgr_for_status is not None:
                mgr = mgr_for_status
                ws = getattr(mgr, "websocket", None) if mgr else None
                if _is_websocket_connected(ws):
                    try:
                        await ws.send_json(payload)
                    except Exception as e:
                        logger.debug("[EventBus] agent_status_update send failed: %s", e)
            else:
                await _broadcast_to_all_connected(payload)
            return

        # Resolve target session manager; fallback to broadcast if lanlan is unknown
        mgr = _get_session_manager(lanlan)
        if not mgr and event_type == "task_update":
            # Broadcast task_update to all connected sessions when lanlan is unresolvable
            task_payload = {"type": "agent_task_update", "task": event.get("task", {})}
            delivered = await _broadcast_to_all_connected(task_payload)
            if delivered == 0:
                logger.warning("[EventBus] task_update broadcast: no connected WebSocket sessions")
            return

        # --- Music Global Broadcasts (Must come before early 'if not mgr' returns) ---
        elif event_type == "music_allowlist_add":
            # Music allowlist is a global UI state, broadcast to all active sessions
            targets = [mgr] if mgr else [m for _, m in _iter_session_managers()]
            payload = {
                "type": "music_allowlist_add",
                "domains": event.get("domains") or event.get("metadata", {}).get("domains", [])
            }

            async def _send_allowlist(target_mgr):
                if target_mgr and target_mgr.websocket and hasattr(target_mgr.websocket, "send_json"):
                    try:
                        await target_mgr.websocket.send_json(payload)
                    except Exception as e:
                        logger.debug("[EventBus] music_allowlist_add broadcast failed: %s", e)

            await asyncio.gather(*(_send_allowlist(t) for t in targets), return_exceptions=True)
            if targets:
                logger.info("[EventBus] music_allowlist_add broadcasted to %d sessions", len(targets))
            return

        elif event_type == "music_play_url":
            # Music playback is a global UI action, broadcast to all active sessions
            targets = [mgr] if mgr else [m for _, m in _iter_session_managers()]
            payload = {
                "type": "music_play_url",
                "url": event.get("url"),
                "name": event.get("name") or "Plugin Music",
                "artist": event.get("artist") or "External"
            }

            async def _send_play(target_mgr):
                if target_mgr and target_mgr.websocket and hasattr(target_mgr.websocket, "send_json"):
                    try:
                        await target_mgr.websocket.send_json(payload)
                    except Exception as e:
                        logger.debug("[EventBus] music_play_url broadcast failed: %s", e)

            await asyncio.gather(*(_send_play(t) for t in targets), return_exceptions=True)
            if targets:
                logger.info("[EventBus] music_play_url broadcasted to %d sessions", len(targets))
            return
        if not mgr and event_type in ("proactive_message", "task_result"):
            fallback_name, fallback_mgr = _select_fallback_session_manager()
            if fallback_mgr is not None:
                mgr = fallback_mgr
                logger.warning(
                    "[EventBus] %s rerouted: lanlan=%s missing, fallback_session=%s",
                    event_type,
                    lanlan,
                    fallback_name,
                )
            else:
                # No target session found — drop the event entirely.
                # Do NOT broadcast text to other sessions to prevent cross-session leaks.
                logger.info(
                    "[EventBus] %s dropped: no target session for lanlan=%s, active_sessions=%s",
                    event_type,
                    lanlan,
                    [name for name, _ in _iter_session_managers()],
                )
                return
        if not mgr:
            logger.info("[EventBus] %s dropped: no session_manager for lanlan=%s", event_type, lanlan)
            return
        if event_type in ("task_result", "proactive_message"):
            text = (event.get("text") or "").strip()
            if text:
                if event.get("direct_reply"):
                    detail_text = (event.get("detail") or text).strip()
                    delivered = False
                    if detail_text and hasattr(mgr, "send_lanlan_response"):
                        try:
                            delivered = bool(await mgr.send_lanlan_response(detail_text, True))
                        except Exception as e:
                            logger.warning("[EventBus] direct task_result reply failed: %s", e)
                    if delivered and hasattr(mgr, "handle_proactive_complete"):
                        try:
                            await mgr.handle_proactive_complete()
                        except Exception as e:
                            logger.warning("[EventBus] direct task_result turn_end failed: %s", e)
                    if delivered:
                        logger.info("[EventBus] direct task_result reply delivered: %.60s", detail_text[:60])
                        return

                # Build structured callback and enqueue for LLM injection
                cb_status = event.get("status") or ("completed" if event.get("success", True) else "failed")
                callback = {
                    "event": "agent_task_callback",
                    "task_id": event.get("task_id") or "",
                    "channel": event.get("channel") or "unknown",
                    "status": cb_status,
                    "success": bool(event.get("success", True)),
                    "summary": event.get("summary") or text,
                    "detail": event.get("detail") or text,
                    "error_message": event.get("error_message") or "",
                    "timestamp": event.get("timestamp") or "",
                }
                mgr.enqueue_agent_callback(callback)
                logger.info("[EventBus] %s enqueued callback, scheduling trigger_agent_callbacks", event_type)

                # Create task with exception logging
                async def _run_trigger_with_logging():
                    try:
                        await mgr.trigger_agent_callbacks()
                    except Exception as e:
                        logger.error("[EventBus] trigger_agent_callbacks task failed: %s", e)
                mgr._pending_agent_callback_task = asyncio.create_task(_run_trigger_with_logging())
                ws = getattr(mgr, "websocket", None)
                if _is_websocket_connected(ws):
                    try:
                        notif = {
                            "type": "agent_notification",
                            "text": text,
                            "source": "brain",
                            "status": cb_status,
                        }
                        err_msg = event.get("error_message") or ""
                        if err_msg:
                            notif["error_message"] = err_msg[:500]
                        await ws.send_json(notif)
                        logger.info("[EventBus] agent_notification sent to frontend: %.60s", text[:60])
                    except Exception as e:
                        logger.warning("[EventBus] agent_notification WS send failed: %s", e)
                else:
                    logger.warning("[EventBus] agent_notification: WebSocket not connected for lanlan=%s", lanlan)
        elif event_type == "agent_notification":
            ws = getattr(mgr, "websocket", None)
            if _is_websocket_connected(ws):
                try:
                    notif = {
                        "type": "agent_notification",
                        "text": event.get("text", ""),
                        "source": event.get("source", "brain"),
                        "status": event.get("status", "error"),
                    }
                    err_msg = event.get("error_message") or ""
                    if err_msg:
                        notif["error_message"] = err_msg[:500]
                    await ws.send_json(notif)
                except Exception as e:
                    logger.debug("[EventBus] agent_notification send failed: %s", e)
            else:
                logger.debug("[EventBus] agent_notification: WebSocket not connected for lanlan=%s", lanlan)
        elif event_type == "task_update":
            task_payload = {"type": "agent_task_update", "task": event.get("task", {})}
            ws = getattr(mgr, "websocket", None)
            if _is_websocket_connected(ws):
                try:
                    await ws.send_json(task_payload)
                except Exception as e:
                    logger.warning("[EventBus] task_update send failed for lanlan=%s: %s", lanlan, e)
            else:
                logger.warning("[EventBus] task_update dropped: WebSocket not connected for lanlan=%s", lanlan)
    except Exception as e:
        logger.debug(f"handle_agent_event error: {e}")

async def _refresh_character_globals():
    """刷新角色相关 module globals（从 config 重新拉一次 aget_character_data）。

    所有 fast-path 入口都必须先走这一步，确保 set_current_catgirl / update_catgirl
    等操作后，后续读 her_name / lanlan_prompt / lanlan_basic_config 的代码看到最新值。
    """
    global master_name, her_name, master_basic_config, lanlan_basic_config
    global name_mapping, lanlan_prompt, time_store, setting_store, recent_log
    global catgirl_names
    master_name, her_name, master_basic_config, lanlan_basic_config, name_mapping, lanlan_prompt, time_store, setting_store, recent_log = await _config_manager.aget_character_data()
    catgirl_names = list(lanlan_prompt.keys())


def _ensure_character_slots(k: str) -> bool:
    """为单个 catgirl 预备 per-k 同步资源槽位。返回是否为新建角色（决定后续要不要强制启动线程）。

    纯内存的原子操作：要么 role_state[k] 已经存在（什么都不做），要么一次性
    把 queue / shutdown_event / websocket_lock 三件全部填好。避免旧代码里
    6 张 dict 用两种不同 sentinel（sync_message_queue vs websocket_locks）
    各自判断 "角色是否已有槽位" 造成的半初始化风险。
    """
    if k not in role_state:
        role_state[k] = RoleState(
            sync_message_queue=Queue(),
            sync_shutdown_event=ThreadEvent(),
            websocket_lock=asyncio.Lock(),
        )
        logger.info(f"为角色 {k} 初始化新资源")
        return True
    return False


async def _init_character_resources(k: str, is_new_character: bool):
    """为单个 catgirl 完成 session_manager 更新 + 同步连接器线程检查/重启。

    依赖 module globals: master_name, lanlan_prompt, lanlan_basic_config（调用方负责先刷新）。
    写入 per-k 槽位: role_state[k].session_manager / sync_process —— 不同 k 之间
    不共享状态，可安全并行。
    """
    rs = role_state[k]  # 调用方必须先 _ensure_character_slots，保证这里可直接索引
    # 更新或创建session manager（使用最新的prompt）
    # 使用锁保护websocket的preserve/restore操作，防止与cleanup()竞争
    async with rs.websocket_lock:
        # 如果已存在且已有websocket连接，保留websocket引用
        old_websocket = None
        if rs.session_manager is not None and rs.session_manager.websocket:
            old_websocket = rs.session_manager.websocket
            logger.info(f"保留 {k} 的现有WebSocket连接")

        # 注意：不在这里清理旧session，因为：
        # 1. 切换当前角色音色时，已在API层面关闭了session
        # 2. 切换其他角色音色时，已跳过重新加载
        # 3. 其他场景不应该影响正在使用的session
        # 如果旧session_manager有活跃session，保留它，只更新配置相关的字段

        # 先检查会话状态（在锁内检查避免竞态条件）
        has_active_session = rs.session_manager is not None and rs.session_manager.is_active

        if has_active_session:
            # 有活跃session，不重新创建session_manager，只更新配置
            # 这是为了防止重新创建session_manager时破坏正在运行的session
            try:
                old_mgr = rs.session_manager
                # 更新prompt
                old_mgr.lanlan_prompt = lanlan_prompt[k].replace('{LANLAN_NAME}', k).replace('{MASTER_NAME}', master_name)
                # 直接读 module global lanlan_basic_config，避免重复 load + deepcopy
                old_mgr.voice_id = get_reserved(
                    lanlan_basic_config[k],
                    'voice_id',
                    default='',
                    legacy_keys=('voice_id',),
                )
                logger.info(f"{k} 有活跃session，只更新配置，不重新创建session_manager")
            except Exception as e:
                logger.error(f"更新 {k} 的活跃session配置失败: {e}", exc_info=True)
                # 配置更新失败，但为了不影响正在运行的session，继续使用旧配置
                # 如果确实需要更新配置，可以考虑在下次session重启时再应用
        else:
            # 没有活跃session，可以安全地重新创建session_manager
            new_mgr = core.LLMSessionManager(
                rs.sync_message_queue,
                k,
                lanlan_prompt[k].replace('{LANLAN_NAME}', k).replace('{MASTER_NAME}', master_name)
            )

            # 将websocket锁存储到session manager中，供cleanup()使用
            new_mgr.websocket_lock = rs.websocket_lock

            # 恢复websocket引用（如果存在）
            if old_websocket:
                new_mgr.websocket = old_websocket
                logger.info(f"已恢复 {k} 的WebSocket连接")

            rs.session_manager = new_mgr

    # 检查并启动同步连接器线程
    # 如果是新角色，或者线程不存在/已停止，需要启动线程
    need_start_thread = False
    if is_new_character:
        need_start_thread = True
    elif rs.sync_process is None:
        need_start_thread = True
    elif hasattr(rs.sync_process, 'is_alive') and not await asyncio.to_thread(rs.sync_process.is_alive):
        need_start_thread = True
        try:
            await asyncio.to_thread(rs.sync_process.join, timeout=0.1)
        except Exception:
            # 注意不要写成 bare except：to_thread 是 cancellation point，
            # 如果 catch 了 BaseException 会吞掉 asyncio.CancelledError
            pass

    if need_start_thread:
        try:
            _char_name = k
            def _make_status_cb(char_name):
                def _cb(msg):
                    mgr = _get_session_manager(char_name)
                    if not mgr:
                        return
                    loop = _server_loop
                    if loop is None or loop.is_closed():
                        return
                    ws = mgr.websocket
                    if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                        import json as _json
                        data = _json.dumps({"type": "status", "message": msg})
                        asyncio.run_coroutine_threadsafe(ws.send_text(data), loop)
                return _cb
            _status_cb = _make_status_cb(_char_name)

            new_thread = Thread(
                target=cross_server.sync_connector_process,
                args=(rs.sync_message_queue, rs.sync_shutdown_event, k, f"ws://127.0.0.1:{MONITOR_SERVER_PORT}", {'bullet': False, 'monitor': True}, _status_cb),
                daemon=True,
                name=f"SyncConnector-{k}"
            )
            rs.sync_process = new_thread
            new_thread.start()
            logger.info(f"✅ 已为角色 {k} 启动同步连接器线程 ({new_thread.name})")
            await asyncio.sleep(0.1)  # 线程启动更快，减少等待时间
            # 与上面 is_alive 检查保持一致，走 to_thread 避免任何潜在阻塞
            if not await asyncio.to_thread(new_thread.is_alive):
                logger.error(f"❌ 同步连接器线程 {k} ({new_thread.name}) 启动后立即退出！")
            else:
                logger.info(f"✅ 同步连接器线程 {k} ({new_thread.name}) 正在运行")
        except Exception as e:
            logger.error(f"❌ 启动角色 {k} 的同步连接器线程失败: {e}", exc_info=True)


async def _stop_character_thread(k: str):
    """停止单个 catgirl 的同步连接器线程（最多 3s join）。dict 清理留给调用方顺序做。"""
    rs = role_state.get(k)
    if rs is None or rs.sync_process is None:
        return
    try:
        logger.info(f"正在停止角色 {k} 的同步连接器线程...")
        rs.sync_shutdown_event.set()
        await asyncio.to_thread(rs.sync_process.join, timeout=3)  # 等待线程正常结束
        if await asyncio.to_thread(rs.sync_process.is_alive):
            logger.warning(f"⚠️ 同步连接器线程 {k} 未能在超时内停止，将作为daemon线程自动清理")
        else:
            logger.info(f"✅ 已停止角色 {k} 的同步连接器线程")
    except Exception as e:
        logger.warning(f"停止角色 {k} 的同步连接器线程时出错: {e}")


def _cleanup_character_dicts(k: str):
    """同步清理单个 catgirl 的 per-k 槽位。调用前确保对应线程已停或超时。"""
    rs = role_state.get(k)
    if rs is None:
        return
    # 清理队列（queue.Queue 没有 close/join_thread 方法）
    try:
        while not rs.sync_message_queue.empty():
            rs.sync_message_queue.get_nowait()
    except QueueEmpty:
        # while empty + get_nowait 本身是 racy idiom：另一线程可能先 drain 掉，
        # 导致 get_nowait 抛 Empty。这里 role_state[k] 即将被 del 掉，忽略无害。
        pass
    # 一次 del 原子清掉所有 6 个字段 —— 替代旧代码里 6 张 dict 分别 del 的对称清理
    del role_state[k]


async def initialize_character_data():
    """全量刷新：加载 config + 对所有 catgirl 做 per-k init + 清理已删除的。

    冷路径（启动 / 主人名编辑 / 大规模批量导入）。per-catgirl 编辑请走
    init_one_catgirl / remove_one_catgirl / switch_current_catgirl_fast 这些 fast path。
    """
    logger.info("正在加载角色配置...")

    # 清理无效的voice_id引用；如果发现旧版 CosyVoice 音色，推入通知缓冲池等前端连接后弹出
    # cleanup_invalid_voice_ids 内部涉及同步 IO（load/save characters），offload 以免阻塞事件循环
    _cleaned, _legacy_names = await asyncio.to_thread(_config_manager.cleanup_invalid_voice_ids)
    if _legacy_names:
        core.enqueue_voice_migration_notice(_legacy_names)

    # 加载最新的角色数据（offload，避免同步 IO + deepcopy 阻塞事件循环）
    await _refresh_character_globals()

    # 为所有 catgirl 预备 per-k 同步资源槽位
    is_new_map: dict[str, bool] = {k: _ensure_character_slots(k) for k in catgirl_names}

    # 每个角色的初始化相互独立（只读共享 prompt / master_name，写各自的 session_manager[k] 等 per-key 槽位）。
    # 用 gather 并行，消除 O(N) × (thread roundtrip + 0.1s sleep) 的串行墙钟。
    # return_exceptions=True：某个角色初始化失败不应导致其它角色被取消。
    _init_results = await asyncio.gather(
        *[_init_character_resources(k, is_new_map[k]) for k in catgirl_names],
        return_exceptions=True,
    )
    for k, res in zip(catgirl_names, _init_results):
        if isinstance(res, BaseException):
            logger.error(f"❌ 初始化角色 {k} 失败: {res}", exc_info=res)

    # 清理已删除角色的资源
    removed_names = [k for k in role_state.keys() if k not in catgirl_names]

    # N 个 join(timeout=3) 串行最坏要 3N 秒；并行化后墙钟 ≈ 3 秒。
    if removed_names:
        await asyncio.gather(
            *[_stop_character_thread(k) for k in removed_names],
            return_exceptions=True,
        )

    # 线程都已停/超时，再在事件循环里顺序清理 dict —— 这些操作都是纯内存，不需要并行。
    for k in removed_names:
        logger.info(f"清理已删除角色 {k} 的资源")
        _cleanup_character_dicts(k)

    logger.info(f"角色配置加载完成，当前角色: {catgirl_names}，主人: {master_name}")


# ─────────────────────────────────────────────────────────────
# Fast-path helpers — 只处理受影响的单个 catgirl，避免全量遍历
# ─────────────────────────────────────────────────────────────

async def switch_current_catgirl_fast():
    """当前猫娘切换（`当前猫娘` 字段变更）专用 fast path。

    关键前提：切换只影响 `her_name` 这一个 global，per-k 的 prompt / voice_id / thread
    状态完全不变。所以这里**只刷 globals**，不做任何 per-k 工作。

    墙钟：一次 aget_character_data（~数 ms）即全部。
    """
    await _refresh_character_globals()
    logger.info(f"[fast-switch] 已刷新 globals，当前猫娘: {her_name}")


async def init_one_catgirl(name: str, *, is_new: bool = False):
    """新增 / 编辑单个 catgirl 的 fast path。

    - is_new=True：新增，强制启动同步连接器线程
    - is_new=False：编辑（prompt / voice_id 等）—— 只刷新 session_manager 的 prompt/voice_id，
                    不会重启线程
    """
    await _refresh_character_globals()
    if name not in lanlan_prompt:
        logger.warning(f"[init-one] '{name}' 不在 config 中，跳过（可能是并发删除）")
        return
    slot_new = _ensure_character_slots(name)
    await _init_character_resources(name, is_new_character=is_new or slot_new)


async def remove_one_catgirl(name: str):
    """删除单个 catgirl 的 fast path：停该角色的线程 + 清 dict + 刷 globals。"""
    await _stop_character_thread(name)
    _cleanup_character_dicts(name)
    # config 文件已由调用方写入，这里刷新 globals 让 catgirl_names 等反映删除
    await _refresh_character_globals()
    logger.info(f"[fast-remove] 已移除角色 {name}")

# 初始化角色数据（使用asyncio.run在模块级别执行async函数）
# 只在主进程中执行，防止 Windows 上子进程重复导入时再次启动子进程
if _IS_MAIN_PROCESS:
    import asyncio as _init_asyncio
    try:
        _init_asyncio.get_event_loop()
    except RuntimeError:
        _init_asyncio.set_event_loop(_init_asyncio.new_event_loop())
    _init_asyncio.get_event_loop().run_until_complete(initialize_character_data())
lock = asyncio.Lock()

# --- FastAPI App Setup ---
app = FastAPI()



class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript'
        return response

# 确定 static 目录位置（使用 _get_app_root）
static_dir = os.path.join(_get_app_root(), 'static')

app.mount("/static", CustomStaticFiles(directory=static_dir), name="static")

# 挂载用户文档下的live2d目录（只在主进程中执行，子进程不提供HTTP服务）
if _IS_MAIN_PROCESS:
    _config_manager.ensure_live2d_directory()
    _config_manager.ensure_vrm_directory()
    _config_manager.ensure_mmd_directory()
    _config_manager.ensure_chara_directory()

    # CFA (反勒索防护) 感知挂载：
    # 优先从原始 Documents 目录（可读）提供模型文件，
    # 可写回退目录（AppData）作为辅助挂载供新导入的模型使用
    _readable_live2d = _config_manager.readable_live2d_dir
    _serve_live2d_path = str(_readable_live2d) if _readable_live2d else str(_config_manager.live2d_dir)

    if os.path.exists(_serve_live2d_path):
        app.mount("/user_live2d", CustomStaticFiles(directory=_serve_live2d_path), name="user_live2d")
        logger.info(f"已挂载用户Live2D目录: {_serve_live2d_path}")

    # CFA 场景：可写回退目录额外挂载，供新导入的模型使用
    if _readable_live2d and str(_config_manager.live2d_dir) != _serve_live2d_path:
        _writable_live2d_path = str(_config_manager.live2d_dir)
        if os.path.exists(_writable_live2d_path):
            app.mount("/user_live2d_local", CustomStaticFiles(directory=_writable_live2d_path), name="user_live2d_local")
            logger.info(f"已挂载本地Live2D目录(CFA回退): {_writable_live2d_path}")

    # 挂载VRM动画目录（static/vrm/animation） 必须第一个挂载
    vrm_animation_path = str(_config_manager.vrm_animation_dir)
    if os.path.exists(vrm_animation_path):
        app.mount("/user_vrm/animation", CustomStaticFiles(directory=vrm_animation_path), name="user_vrm_animation")
        logger.info(f"已挂载VRM动画目录: {vrm_animation_path}")

    # 挂载VRM模型目录（用户文档目录）
    user_vrm_path = str(_config_manager.vrm_dir)
    if os.path.exists(user_vrm_path):
        app.mount("/user_vrm", CustomStaticFiles(directory=user_vrm_path), name="user_vrm")
        logger.info(f"已挂载VRM目录: {user_vrm_path}")
    
    # 挂载项目目录下的static/vrm（作为备用，如果文件在项目目录中）
    project_vrm_path = os.path.join(static_dir, 'vrm')
    if os.path.exists(project_vrm_path) and os.path.isdir(project_vrm_path):
        logger.info(f"项目VRM目录存在: {project_vrm_path} (可通过 /static/vrm/ 访问)")
    
    # 挂载MMD动画目录（必须在MMD模型目录之前挂载）
    mmd_animation_path = str(_config_manager.mmd_animation_dir)
    if os.path.exists(mmd_animation_path):
        app.mount("/user_mmd/animation", CustomStaticFiles(directory=mmd_animation_path), name="user_mmd_animation")
        logger.info(f"已挂载MMD动画目录: {mmd_animation_path}")

    # 挂载MMD模型目录（用户文档目录）
    user_mmd_path = str(_config_manager.mmd_dir)
    if os.path.exists(user_mmd_path):
        app.mount("/user_mmd", CustomStaticFiles(directory=user_mmd_path), name="user_mmd")
        logger.info(f"已挂载MMD目录: {user_mmd_path}")
    
    # 挂载项目目录下的static/mmd（作为备用）
    project_mmd_path = os.path.join(static_dir, 'mmd')
    if os.path.exists(project_mmd_path) and os.path.isdir(project_mmd_path):
        logger.info(f"项目MMD目录存在: {project_mmd_path} (可通过 /static/mmd/ 访问)")

    # 挂载用户mod路径
    user_mod_path = _config_manager.get_workshop_path()
    if os.path.exists(user_mod_path) and os.path.isdir(user_mod_path):
        app.mount("/user_mods", CustomStaticFiles(directory=user_mod_path), name="user_mods")
        logger.info(f"已挂载用户mod路径: {user_mod_path}")

# --- 初始化共享状态并挂载路由 ---
# 从 main_routers 包导入并挂载路由
from main_routers import ( # noqa
    config_router,
    characters_router,
    live2d_router,
    vrm_router,
    mmd_router,
    jukebox_router,
    workshop_router,
    memory_router,
    pages_router,
    websocket_router,
    agent_router,
    system_router,
)
from main_routers import music_router # noqa
from main_routers.cookies_login_router import router as cookies_login_router # noqa
from main_routers.shared_state import init_shared_state # noqa

# 初始化共享状态，供各路由访问
# 注意：steamworks 会在 startup 事件中初始化后更新
if _IS_MAIN_PROCESS:
    init_shared_state(
        role_state=role_state,
        steamworks=None,  # 延迟初始化，会在 startup 事件中设置
        templates=templates,
        config_manager=_config_manager,
        logger=logger,
        initialize_character_data=initialize_character_data,
        switch_current_catgirl_fast=switch_current_catgirl_fast,
        init_one_catgirl=init_one_catgirl,
        remove_one_catgirl=remove_one_catgirl,
    )


# ── 健康检查 / 指纹端点 ──────────────────────────────────────────
@app.get("/health")
async def health():
    """返回带 N.E.K.O 签名的健康响应，供 launcher/前端识别，
    以区分当前服务与随机占用该端口的其他进程。"""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response("main", instance_id=INSTANCE_ID)


@app.post('/api/beacon/shutdown')
async def beacon_shutdown():
    """Beacon 接口：用于优雅关闭服务器"""
    try:
        # 从 app.state 获取配置
        current_config = get_start_config()
        # 仅当服务由 --open-browser 模式启动时才响应 beacon
        if current_config['browser_mode_enabled']:
            logger.info("收到beacon信号，准备关闭服务器...")
            # 调度服务器关闭任务
            asyncio.create_task(shutdown_server_async())
            return {"success": True, "message": "服务器关闭信号已接收"}
    except Exception as e:
        logger.error(f"Beacon处理错误: {e}")
        return {"success": False, "error": str(e)}

# 挂载全部路由
app.include_router(config_router)
app.include_router(characters_router)
app.include_router(live2d_router)
app.include_router(vrm_router)
app.include_router(mmd_router)
app.include_router(jukebox_router)
app.include_router(workshop_router)
app.include_router(memory_router)
# 注意：pages_router 含 /{lanlan_name} 兜底路由，应最后挂载
app.include_router(websocket_router)
app.include_router(agent_router)
app.include_router(system_router)
app.include_router(music_router.router)
app.include_router(cookies_login_router) # Cookies登录相关路由，放在最后以避免与其他API路由冲突
app.include_router(pages_router)  # 兜底路由需最后挂载

# 后台预加载任务
_preload_task: asyncio.Task = None


async def _background_preload():
    """后台预加载音频处理模块
    
    注意：不需要 Event 同步机制，因为 Python 的 import lock 会自动等待首次导入完成。
    如果用户在预加载完成前点击语音，再次 import 会自动阻塞等待。
    """
    try:
        logger.info("🔄 后台预加载音频处理模块...")
        # 在线程池中执行同步导入（避免阻塞事件循环）
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, _sync_preload_modules)
    except Exception as e:
        logger.warning(f"⚠️ 音频处理模块预加载失败（不影响使用）: {e}")


def _sync_preload_modules():
    """同步预加载延迟导入的模块（在线程池中执行）
    
    注意：以下模块已通过导入链在启动时加载，无需预加载：
    - numpy, soxr: 通过 core.py / audio_processor.py
    - websockets: 通过 omni_realtime_client.py
    - langchain_openai/langchain_core: 通过 omni_offline_client.py
    - httpx: 通过 core.py
    - aiohttp: 通过 tts_client.py
    
    真正需要预加载的延迟导入模块：
    - pyrnnoise.rnnoise: audio_processor.py 中通过 _get_rnnoise() 延迟加载
    - dashscope: tts_client.py 中仅在 cosyvoice_vc_tts_worker 函数内部导入
    - googletrans/translatepy: language_utils.py 中延迟导入的翻译库
    - translation_service: language_utils.py 中的翻译服务（TranslationService）
    """
    import time
    start = time.time()
    
    # 1. 翻译服务相关模块（避免首轮对话延迟）
    try:
        # 预加载翻译库（googletrans, translatepy 等）
        from utils import language_utils
        # 触发翻译库的导入（如果可用）
        _ = language_utils.GOOGLETRANS_AVAILABLE
        _ = language_utils.TRANSLATEPY_AVAILABLE
        logger.debug("✅ 翻译库预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译库预加载失败（不影响使用）: {e}")
    
    # 2. 翻译服务实例（需要 config_manager）
    try:
        # 提前初始化翻译服务（如果在初始化过程中需要翻译数据）
        from utils.language_utils import get_translation_service
        from utils.config_manager import get_config_manager
        # 此处仅调用以触发单例初始化，后续使用时通过 get_translation_service 获取即可
        config_manager = get_config_manager()
        # 预初始化翻译服务实例（触发 LLM 客户端创建等）
        _ = get_translation_service(config_manager)
        logger.debug("✅ 翻译服务预加载完成")
    except Exception as e:
        logger.debug(f"⚠️ 翻译服务预加载失败（不影响使用）: {e}")
    
    # 3. pyrnnoise (音频降噪 - 延迟加载，可能较慢)
    try:
        from utils.audio_processor import _get_rnnoise, _LiteDenoiser
        rnnoise_mod = _get_rnnoise()
        if rnnoise_mod:
            _warmup = _LiteDenoiser(rnnoise_mod)
            del _warmup
            logger.debug("  ✓ pyrnnoise loaded and warmed up")
        else:
            logger.debug("  ✗ pyrnnoise not available")
    except Exception as e:
        logger.debug(f"  ✗ pyrnnoise: {e}")
    
    # 4. dashscope (阿里云 CosyVoice TTS SDK - 仅在使用自定义音色时需要)
    try:
        import dashscope  # noqa: F401
        logger.debug("  ✓ dashscope loaded")
    except Exception as e:
        logger.debug(f"  ✗ dashscope: {e}")
    
    # 5. AudioProcessor 预热（numpy buffer + soxr resampler 初始化）
    try:
        from utils.audio_processor import AudioProcessor
        import numpy as np
        # 创建临时实例预热 numpy/soxr
        _warmup_processor = AudioProcessor(
            input_sample_rate=48000,
            output_sample_rate=16000,
            noise_reduce_enabled=False  # 不需要 RNNoise，前面已预热
        )
        # 模拟处理一小块音频，预热 numpy 和 soxr 的 JIT
        _dummy_audio = np.zeros(480, dtype=np.int16).tobytes()
        _ = _warmup_processor.process_chunk(_dummy_audio)
        del _warmup_processor, _dummy_audio
        logger.debug("  ✓ AudioProcessor warmed up")
    except Exception as e:
        logger.debug(f"  ✗ AudioProcessor warmup: {e}")
    
    # 6. httpx SSL 上下文预热（首次创建 AsyncClient 会初始化 SSL）
    try:
        import httpx
        import asyncio
        
        async def _warmup_httpx():
            async with httpx.AsyncClient(timeout=1.0, proxy=None, trust_env=False) as client:
                # 发送一个简单请求预热 SSL 上下文
                try:
                    await client.get("http://127.0.0.1:1", timeout=0.01)
                except:  # noqa: E722
                    pass  # 预期会失败，只是为了初始化 SSL
        
        # 在当前线程的事件循环中运行（如果没有则创建临时循环）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已有运行中的循环，使用线程池
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_warmup_httpx())).result(timeout=2.0)
            else:
                loop.run_until_complete(_warmup_httpx())
        except RuntimeError:
            asyncio.run(_warmup_httpx())
        logger.debug("  ✓ httpx SSL context warmed up")
    except Exception as e:
        logger.debug(f"  ✗ httpx warmup: {e}")
    
    elapsed = time.time() - start
    logger.info(f"📦 模块预加载完成，耗时 {elapsed:.2f}s")


# Startup 事件：延迟初始化 Steamworks 和全局语言
@app.on_event("startup")
async def on_startup():
    """服务器启动时执行的初始化操作"""
    if _IS_MAIN_PROCESS:
        global steamworks, _preload_task, agent_event_bridge, _server_loop
        _server_loop = asyncio.get_running_loop()
        # asyncio 的慢回调告警只在 loop debug 模式下输出。默认关闭，
        # 需要排查事件循环停顿时设 NEKO_DEBUG_ASYNC=1 启用（会略微增加每 callback 开销）。
        if os.environ.get("NEKO_DEBUG_ASYNC") == "1":
            _server_loop.set_debug(True)
            _server_loop.slow_callback_duration = 0.05
            logger.info("[asyncio] debug mode enabled (slow_callback_duration=0.05s)")

        # 事件循环心跳：每 200ms 打一个点。当 /new_dialog 或其他 async 操作
        # 莫名其妙变慢时，看心跳是否缺席 —— 缺席 = 事件循环被阻塞，这段时间
        # 内所有 async 都没机会跑。心跳日志仅在开启 NEKO_DEBUG_HEARTBEAT=1
        # 时输出到 stdout，避免日志刷屏。
        if os.environ.get("NEKO_DEBUG_HEARTBEAT") == "1":
            async def _event_loop_heartbeat():
                import time as _t
                _last = _t.perf_counter()
                while True:
                    await asyncio.sleep(0.2)
                    _now = _t.perf_counter()
                    _gap_ms = int((_now - _last) * 1000)
                    # 只打异常的心跳（间隔 > 300ms），正常跳过
                    if _gap_ms > 300:
                        print(f"[{_t.strftime('%H:%M:%S')}] [heartbeat] stall {_gap_ms}ms (expected ~200ms)", flush=True)
                    _last = _now
            asyncio.create_task(_event_loop_heartbeat())
            logger.info("[asyncio] heartbeat enabled (stalls > 300ms will be logged)")

        # 预热重量级模块导入，避免 TTS worker 线程第一次启动时在
        # 工作线程里触发同步 import 阻塞事件循环（实测 dashscope
        # 首次 import 会吃 GIL 3-7 秒，导致此期间 /new_dialog 响应
        # 解析无法及时完成，表现为 "memory 服务响应超时"）。
        # 放 to_thread 里跑，不阻塞 startup 本身。
        async def _prewarm_heavy_imports():
            import importlib
            for mod in ("dashscope", "dashscope.audio.tts_v2"):
                try:
                    await asyncio.to_thread(importlib.import_module, mod)
                    logger.debug(f"[prewarm] imported {mod}")
                except Exception as e:
                    logger.debug(f"[prewarm] import {mod} failed (ignored): {e}")
        asyncio.create_task(_prewarm_heavy_imports())

        logger.info("正在初始化 Steamworks...")
        steamworks = initialize_steamworks()
        
        # 更新 shared_state 中的 steamworks 引用
        from main_routers.shared_state import set_steamworks
        set_steamworks(steamworks)
        
        # 尝试获取 Steam 信息
        get_default_steam_info()
        
        # 在后台异步预加载音频模块（不阻塞服务器启动）
        # 注意：不需要等待机制，Python import lock 会自动处理并发
        _preload_task = asyncio.create_task(_background_preload())
        # 启动 agent_server <-> main_server 的 ZeroMQ 事件桥接
        try:
            agent_event_bridge = MainServerAgentBridge(on_agent_event=_handle_agent_event)
            await agent_event_bridge.start()
            set_main_bridge(agent_event_bridge)
        except Exception as e:
            logger.warning(f"Agent event bridge startup failed: {e}")
        await _init_and_mount_workshop()
        
        # 后台预热 UGC 缓存 + 同步角色卡（分别独立任务，互不阻塞）
        if steamworks:
            import main_routers.workshop_router as _wr
            
            async def _warmup_only():
                """仅预热 UGC 缓存"""
                try:
                    await warmup_ugc_cache()
                except Exception as e:
                    logger.warning(f"UGC 缓存预热失败: {e}")
            
            async def _sync_characters_only():
                """等待预热完成后同步角色卡"""
                # 先等预热完成，角色卡同步依赖订阅物品列表
                if _wr._ugc_warmup_task is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(_wr._ugc_warmup_task), timeout=20)
                    except asyncio.TimeoutError:
                        logger.warning("等待 UGC 预热任务超时（20s），继续角色卡同步")
                    except Exception as e:
                        logger.debug(f"等待 UGC 预热任务时异常（不影响角色卡同步）: {e}")
                try:
                    sync_result = await sync_workshop_character_cards()
                    if sync_result["added"] > 0:
                        logger.info(f"✅ 创意工坊角色卡同步完成：新增 {sync_result['added']} 个，跳过 {sync_result['skipped']} 个")
                    else:
                        logger.info("创意工坊角色卡同步完成：无新增角色卡")
                except Exception as e:
                    logger.warning(f"创意工坊角色卡同步失败（不影响启动）: {e}")
            
            # _ugc_warmup_task 仅引用预热任务，等待它不会被角色卡同步阻塞
            _wr._ugc_warmup_task = asyncio.create_task(_warmup_only())
            _wr._ugc_sync_task = asyncio.create_task(_sync_characters_only())
        
        # 初始化全局 LLM Token 用量追踪器
        try:
            from utils.token_tracker import TokenTracker, install_hooks
            install_hooks()
            TokenTracker.get_instance().start_periodic_save()
            TokenTracker.get_instance().record_app_start()
            logger.info("Token usage tracker initialized")
        except Exception as e:
            logger.warning(f"Token tracker initialization failed (non-critical): {e}")

        logger.info("Startup 初始化完成，后台正在预加载音频模块...")

        # 初始化全局语言变量（优先级：Steam设置 > 系统设置）
        try:
            from utils.language_utils import initialize_global_language
            global_lang = initialize_global_language()
            logger.info(f"全局语言初始化完成: {global_lang}")
        except Exception as e:
            logger.warning(f"全局语言初始化失败: {e}，将使用默认值")


@app.on_event("shutdown")
async def on_shutdown():
    """服务器关闭时清理资源"""
    if _IS_MAIN_PROCESS:
        logger.info("正在清理资源...")
        
        # 等待预加载任务完成（如果还在运行）
        global _preload_task, agent_event_bridge
        if _preload_task:
            try:
                await asyncio.wait_for(_preload_task, timeout=1.0)
            except asyncio.TimeoutError:
                _preload_task.cancel()
                try:
                    await _preload_task
                except asyncio.CancelledError:
                    logger.debug("预加载任务清理时超时并已取消（正常关闭流程）")
            except asyncio.CancelledError:
                logger.debug("预加载任务清理时已取消（正常关闭流程）")
            except Exception as e:
                logger.debug(f"预加载任务清理时出错（正常关闭流程）: {e}", exc_info=True)
        
        # Clean up agent_event_bridge (ZMQ context/sockets/recv thread)
        if agent_event_bridge is not None:
            try:
                await agent_event_bridge.stop()
            except Exception as e:
                logger.debug(f"Agent event bridge cleanup failed: {e}", exc_info=True)
        
        # 释放 soxr ResampleStream（nanobind C 扩展），避免解释器退出时泄漏警告
        try:
            for _, mgr in _iter_session_managers():
                if hasattr(mgr, 'audio_resampler'):
                    mgr.audio_resampler = None
        except Exception as e:
            logger.debug(f"soxr resampler cleanup failed: {e}")

        # 关闭翻译服务
        try:
            from utils import language_utils
            close_fn = getattr(language_utils, "aclose_translation_service", None)
            if callable(close_fn):
                await close_fn()
            else:
                logger.debug("Translation service cleanup skipped: function not implemented")
        except Exception as e:
            logger.debug(f"Translation service cleanup failed: {e}")

        # 保存 Token 用量数据
        try:
            from utils.token_tracker import TokenTracker
            TokenTracker.get_instance().save()
        except Exception as e:
            logger.debug(f"Token usage save on shutdown failed: {e}")

        # 关闭音乐爬虫连接池
        try:
            from utils.music_crawlers import close_all_crawlers
            # 【核心修改】增加 1 秒超时兜底。如果 1 秒内关不完，直接抛弃，保障服务器顺利退出
            await asyncio.wait_for(close_all_crawlers(), timeout=1.0)

        except asyncio.TimeoutError:
            # 单独捕获超时异常，记录警告但放行
            logger.warning("音乐爬虫连接池清理超时，已强制跳过以保证服务正常退出。")
        except Exception as e:
            logger.debug(f"音乐爬虫清理失败: {e}", exc_info=True)

        # 关闭 memory_server 共享 httpx 连接池
        try:
            from utils.memory_client import aclose_memory_client
            await asyncio.wait_for(aclose_memory_client(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("memory_client 清理超时，已强制跳过。")
        except Exception as e:
            logger.debug(f"memory_client 清理失败: {e}", exc_info=True)

# 使用 FastAPI 的 app.state 来管理启动配置
def get_start_config():
    """从 app.state 获取启动配置"""
    if hasattr(app.state, 'start_config'):
        return app.state.start_config
    return {
        "browser_mode_enabled": False,
        "browser_page": "chara_manager",
        'server': None
    }

def set_start_config(config):
    """设置启动配置到 app.state"""
    app.state.start_config = config


async def _init_and_mount_workshop():
    """
    初始化并挂载创意工坊目录
    
    设计原则：
    - main 层只负责调用，不维护状态
    - 路径由 utils 层计算并持久化到 config 层
    - 其他代码需要路径时调用 get_workshop_path() 获取
    """
    try:
        # 1. 获取订阅的创意工坊物品列表
        workshop_items_result = await get_subscribed_workshop_items()
        
        # 2. 提取物品列表传给 utils 层
        subscribed_items = []
        if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
            subscribed_items = workshop_items_result.get('items', [])
        
        # 3. 调用 utils 层函数获取/计算路径（路径会被持久化到 config）
        workshop_path = get_workshop_root(subscribed_items)
        
        # 4. 挂载静态文件目录
        if workshop_path and os.path.exists(workshop_path) and os.path.isdir(workshop_path):
            try:
                app.mount("/workshop", StaticFiles(directory=workshop_path), name="workshop")
                logger.info(f"✅ 成功挂载创意工坊目录: {workshop_path}")
            except Exception as e:
                logger.error(f"挂载创意工坊目录失败: {e}")
        else:
            logger.warning(f"创意工坊目录不存在或不是有效的目录: {workshop_path}，跳过挂载")
    except Exception as e:
        logger.error(f"初始化创意工坊目录时出错: {e}")
        # 降级：确保至少有一个默认路径可用
        workshop_path = get_workshop_path()
        logger.info(f"使用配置中的默认路径: {workshop_path}")
        if workshop_path and os.path.exists(workshop_path) and os.path.isdir(workshop_path):
            try:
                app.mount("/workshop", StaticFiles(directory=workshop_path), name="workshop")
                logger.info(f"✅ 降级模式下成功挂载创意工坊目录: {workshop_path}")
            except Exception as mount_err:
                logger.error(f"降级模式挂载创意工坊目录仍然失败: {mount_err}")


async def shutdown_server_async():
    """异步关闭服务器"""
    try:
        # 短暂延时，确保 beacon 响应有机会先发送
        await asyncio.sleep(0.5)
        logger.info("正在关闭服务器...")

        # 取消后台创意工坊任务，避免残留协程
        try:
            import main_routers.workshop_router as _wr
            _SHUTDOWN_TASK_TIMEOUT = 5  # 等待后台任务结束的超时秒数
            for task_attr in ('_ugc_warmup_task', '_ugc_sync_task'):
                task = getattr(_wr, task_attr, None)
                if task and not task.done():
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=_SHUTDOWN_TASK_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.warning(f"后台任务 {task_attr} 在 {_SHUTDOWN_TASK_TIMEOUT}s 内未结束，跳过等待")
                    except asyncio.CancelledError:
                        logger.debug(f"后台任务 {task_attr} 已取消")
                    except Exception as e:
                        logger.debug(f"后台任务 {task_attr} 取消时异常: {e}")
        except Exception as e:
            logger.debug(f"取消创意工坊后台任务时出错: {e}")
        
        # 向memory_server发送关闭信号
        try:
            from config import MEMORY_SERVER_PORT
            shutdown_url = f"http://127.0.0.1:{MEMORY_SERVER_PORT}/shutdown"
            async with httpx.AsyncClient(timeout=1, proxy=None, trust_env=False) as client:
                response = await client.post(shutdown_url)
                if response.status_code == 200:
                    logger.info("已向memory_server发送关闭信号")
                else:
                    logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
        except Exception as e:
            logger.warning(f"向memory_server发送关闭信号时出错: {e}")
        
        # 通知服务器退出
        current_config = get_start_config()
        if current_config['server'] is not None:
            current_config['server'].should_exit = True
    except Exception as e:
        logger.error(f"关闭服务器时出错: {e}")


# Steam 创意工坊管理相关API路由
# 确保这个路由被正确注册
if _IS_MAIN_PROCESS:
    logger.info('注册Steam创意工坊扫描API路由')


def _format_size(size_bytes):
    """
    将字节大小格式化为人类可读的格式
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"



# 辅助函数
def get_folder_size(folder_path):
    """获取文件夹大小（字节）"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(filepath)
            except (OSError, FileNotFoundError):
                continue
    return total_size

def find_preview_image_in_folder(folder_path):
    """在文件夹中查找预览图片，只查找指定的8个图片名称"""
    # 按优先级顺序查找指定的图片文件列表
    preview_image_names = ['preview.jpg', 'preview.png', 'thumbnail.jpg', 'thumbnail.png', 
                         'icon.jpg', 'icon.png', 'header.jpg', 'header.png']
    
    for image_name in preview_image_names:
        image_path = os.path.join(folder_path, image_name)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            return image_path
    
    # 如果找不到指定的图片名称，返回None
    return None


def _get_port_owners(port: int) -> list[int]:
    """查询监听指定端口的进程 PID 列表（尽力而为）。"""
    pids: set[int] = set()
    try:
        import subprocess
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            needle = f":{port}"
            for raw in result.stdout.splitlines():
                line = raw.strip()
                if "LISTENING" not in line or needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                pid_str = parts[-1]
                if pid_str.isdigit():
                    pids.add(int(pid_str))
        else:
            result = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            for line in result.stdout.splitlines():
                s = line.strip()
                if s.isdigit():
                    pids.add(int(s))
    except Exception:
        pass
    return sorted(pids)


def _is_port_available(port: int) -> bool:
    """检查 127.0.0.1:port 是否可绑定。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        set_port_probe_reuse(sock)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()

# --- Run the Server ---
if __name__ == "__main__":
    import uvicorn
    import argparse
    import signal
    import threading
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-browser",   action="store_true",
                        help="启动后是否打开浏览器并监控它")
    parser.add_argument("--page",           type=str, default="",
                        choices=["index", "chara_manager", "api_key", ""],
                        help="要打开的页面路由（不含域名和端口）")
    args = parser.parse_args()

    logger.info("--- Starting FastAPI Server ---")
    # 使用 os.path.abspath 输出更清晰的完整路径
    logger.info(f"Serving static files from: {os.path.abspath('static')}")
    logger.info(f"Serving index.html from: {os.path.abspath('templates/index.html')}")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT} (or your network IP:{MAIN_SERVER_PORT})")
    logger.info("-----------------------------")

    # ── 前端构建产物检测 ──────────────────────────────────────
    _frontend_missing = []
    if not os.path.isfile("frontend/plugin-manager/dist/index.html"):
        _frontend_missing.append("plugin-manager  (frontend/plugin-manager/dist/index.html)")
    if not os.path.isfile("static/react/neko-chat/neko-chat-window.iife.js"):
        _frontend_missing.append("react-neko-chat  (static/react/neko-chat/neko-chat-window.iife.js)")
    if _frontend_missing:
        _bar = "!" * 60
        _msg = (
            f"\n{_bar}\n{_bar}\n"
            f"!!  WARNING: 前端资源未构建，以下模块缺失:\n"
        )
        for _m in _frontend_missing:
            _msg += f"!!    - {_m}\n"
        _msg += (
            f"!!\n"
            f"!!  请先运行构建脚本:\n"
            f"!!    Windows:  .\\build_frontend.bat\n"
            f"!!    Linux:    ./build_frontend.sh\n"
            f"!!\n"
            f"!!  否则部分页面将无法正常显示！\n"
            f"{_bar}\n{_bar}\n"
        )
        print(_msg, flush=True)
        logger.warning("前端资源未构建，部分页面将无法正常显示！请运行 build_frontend.sh / build_frontend.bat")

    # 使用统一的速率限制日志过滤器
    from utils.logger_config import create_main_server_filter, create_httpx_filter
    
    # 为 uvicorn access 日志添加过滤器
    logging.getLogger("uvicorn.access").addFilter(create_main_server_filter())
    
    # 为 httpx 日志添加可用性检查过滤器
    logging.getLogger("httpx").addFilter(create_httpx_filter())

    # 启动前预检端口，避免 uvicorn 启动后立刻退出且日志不明显
    if not _is_port_available(MAIN_SERVER_PORT):
        owner_pids = _get_port_owners(MAIN_SERVER_PORT)
        owner_hint = f"，占用PID: {owner_pids}" if owner_pids else ""
        logger.error(f"启动失败：端口 {MAIN_SERVER_PORT} 已被占用{owner_hint}")
        raise SystemExit(1)

    # 1) 配置 UVicorn
    _behind_proxy = os.environ.get("NEKO_BEHIND_PROXY", "").strip().lower() in ("1", "true", "yes")
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=MAIN_SERVER_PORT,
        log_level="info",
        loop="asyncio",
        reload=False,
        proxy_headers=_behind_proxy,
        forwarded_allow_ips="*" if _behind_proxy else None,
        # WebSocket keep-alive: send server-initiated pings every 20s, close if no pong within 60s
        ws_ping_interval=20.0,
        ws_ping_timeout=60.0,
    )
    server = uvicorn.Server(config)
    
    # Set browser mode flag if --open-browser is used
    if args.open_browser:
        # 使用 FastAPI 的 app.state 来管理配置
        start_config = {
            "browser_mode_enabled": True,
            "browser_page": args.page if args.page!='index' else '',
            'server': server
        }
        set_start_config(start_config)
    else:
        # 设置默认配置
        start_config = {
            "browser_mode_enabled": False,
            "browser_page": "",
            'server': server
        }
        set_start_config(start_config)

    print(f"启动配置: {get_start_config()}")

    # 2) 信号处理：Ctrl+C 时快速关闭
    #    uvicorn 的 install_signal_handlers() 会用 signal.signal(sig, self.handle_exit)
    #    覆盖我们直接注册的信号处理器。所以这里 monkey-patch server.handle_exit，
    #    这样无论 uvicorn 何时安装信号处理器，最终调用的都是我们的逻辑。
    _shutdown_state = {"signal_count": 0}
    _original_handle_exit = server.handle_exit

    def _custom_handle_exit(sig, frame):
        _shutdown_state["signal_count"] += 1
        if _shutdown_state["signal_count"] > 1:
            logger.warning("收到第二次关闭信号, 立即强制退出.")
            cleanup()
            os._exit(130)
        logger.info("正在关闭服务器...")
        cleanup()
        _original_handle_exit(sig, frame)

    server.handle_exit = _custom_handle_exit

    # 4) 启动服务器（阻塞，直到 server.should_exit=True）
    logger.info("--- Starting FastAPI Server ---")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT}/{args.page}")
    
    try:
        server.run()
    except KeyboardInterrupt:
        # Ctrl+C 正常关闭，不显示 traceback
        logger.info("收到关闭信号（Ctrl+C），正在关闭服务器...")
    except (asyncio.CancelledError, SystemExit):
        # 正常的关闭信号
        pass
    except Exception as e:
        # 真正的错误，显示完整 traceback
        logger.error(f"服务器运行时发生错误: {e}", exc_info=True)
        raise
    finally:
        logger.info("服务器已关闭")
