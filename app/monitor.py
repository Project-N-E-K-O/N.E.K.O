# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys, os
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Force project root to sys.path[0] — see agent_server.py top for rationale.
if sys.path[0:1] != [_repo_root]:
    sys.path.insert(0, _repo_root)

# Wire DI bindings explicitly — direct script invocation
# (``python app/monitor.py``) doesn't run app/__init__.py.
# Idempotent under launcher's ``from app import monitor`` path too.
from app.runtime_bindings import install_runtime_bindings as _install_runtime_bindings
_install_runtime_bindings()

import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import json
import os
import logging
from contextlib import asynccontextmanager
from config import MONITOR_SERVER_PORT, MONITOR_HOST, MONITOR_TOKEN, MONITOR_VIEWER_TOKEN, DEFAULT_LIVE2D_MODEL_NAME
from app.monitor_auth import (
    MonitorAuthMiddleware,
    install_monitor_log_redaction,
    monitor_auth_enabled,
)
from utils.config_manager import get_config_manager, get_reserved
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn
from fastapi.templating import Jinja2Templates
from utils.frontend_utils import find_models, find_model_config_file, find_model_directory
from utils.workshop_utils import get_default_workshop_folder
from utils.preferences import GLOBAL_CONVERSATION_KEY, aload_user_preferences

# Setup logger
from utils.logger_config import setup_logging
from utils.static_asset_aliases import LegacyStaticAssetAliasMixin
logger, log_config = setup_logging(service_name="Monitor", log_level=logging.INFO)

# Get the resource path (supports packaged environments)
def get_resource_path(relative_path):
    """Get the absolute path of a resource, supporting both dev and packaged environments.

    monitor.py now lives at ``<repo>/app/monitor.py``, so in the source / Nuitka
    cases the resource root needs ``dirname(dirname(__file__))`` to get back to
    the project root (where static/ templates/ etc. live). PyInstaller unpacks
    all resources to the top level of ``sys._MEIPASS``, so that path is unchanged.
    """
    if getattr(sys, 'frozen', False):
        # Packaged environment
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller
            base_path = sys._MEIPASS
        else:
            # Nuitka standalone: app/ is a subpackage, and resources are in the parent directory
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    else:
        # Development environment: the grandparent of app/monitor.py is the project root
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

templates = Jinja2Templates(directory=get_resource_path(""))

_STATIC_ASSET_VERSION_CACHE = (0.0, "0")

def _viewer_static_assets_ctx():
    """Build viewer cache version without importing the full main router graph."""
    global _STATIC_ASSET_VERSION_CACHE
    now = asyncio.get_running_loop().time()
    cached_at, cached_version = _STATIC_ASSET_VERSION_CACHE
    if now - cached_at < 30.0:
        return {"static_asset_version": cached_version}
    from config import APP_VERSION
    latest_mtime = 0
    for relative_path in ("static/css/index.css", "static/css/edge-peek.css"):
        try:
            latest_mtime = max(latest_mtime, int(os.path.getmtime(get_resource_path(relative_path))))
        except OSError:
            continue
    version = f"{APP_VERSION}-{latest_mtime or 0}"
    _STATIC_ASSET_VERSION_CACHE = (now, version)
    return {"static_asset_version": version}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch a background task that periodically cleans up
    # disconnected WebSocket clients. Replaces the deprecated
    # @app.on_event("startup") hook (FastAPI lifespan is the supported API).
    # Redaction lives here rather than under __main__ so `uvicorn app.monitor:app`
    # and embedded runs never write ?token= into the access log either.
    install_monitor_log_redaction()
    if MONITOR_VIEWER_TOKEN and not MONITOR_TOKEN:
        logger.warning("MONITOR_VIEWER_TOKEN is ignored because MONITOR_TOKEN is empty")
    _fire_task(cleanup_disconnected_clients())
    yield


app = FastAPI(lifespan=lifespan)
# Gates every route except the static asset mounts once MONITOR_TOKEN is set.
app.add_middleware(MonitorAuthMiddleware)

# Viewers only need model layout; keep other preference fields off the LAN.
_ALLOWED_VIEWER_PREFERENCE_KEYS = {
    "model_path", "position", "scale", "rotation", "display", "viewport",
    "camera_position", "parameters",
}


def _viewer_preferences_only(preferences):
    if not isinstance(preferences, list):
        return preferences
    return [
        {k: v for k, v in entry.items() if k in _ALLOWED_VIEWER_PREFERENCE_KEYS}
        for entry in preferences
        if isinstance(entry, dict) and entry.get("model_path") != GLOBAL_CONVERSATION_KEY
    ]


DEFAULT_LIVE2D_MODEL = DEFAULT_LIVE2D_MODEL_NAME
LEGACY_DEFAULT_LIVE2D_MODELS = {
    model_name for model_name in ("yui_default", "yui-default") if model_name != DEFAULT_LIVE2D_MODEL
}

class _StaticRootFiles(LegacyStaticAssetAliasMixin, StaticFiles):
    pass


# Mount static files (map old asset paths to migrated files; see utils/static_asset_aliases.py)
app.mount("/static", _StaticRootFiles(directory=get_resource_path("static")), name="static")
_config_manager = get_config_manager()

# Mount the user's Live2D directory (consistent with the main_server package and CFA-aware)
_readable_live2d = _config_manager.readable_live2d_dir
_serve_live2d_path = str(_readable_live2d) if _readable_live2d else str(_config_manager.live2d_dir)
if os.path.exists(_serve_live2d_path):
    app.mount("/user_live2d", StaticFiles(directory=_serve_live2d_path), name="user_live2d")
    logger.info(f"已挂载用户Live2D目录: {_serve_live2d_path}")
# CFA scenario: also mount the writable fallback directory
if _readable_live2d and str(_config_manager.live2d_dir) != _serve_live2d_path:
    _writable_live2d_path = str(_config_manager.live2d_dir)
    if os.path.exists(_writable_live2d_path):
        app.mount("/user_live2d_local", StaticFiles(directory=_writable_live2d_path), name="user_live2d_local")
        logger.info(f"已挂载本地Live2D目录(CFA回退): {_writable_live2d_path}")

# Mount the workshop directory (consistent with the main_server package)
workshop_path = get_default_workshop_folder()
if workshop_path and os.path.exists(workshop_path):
    app.mount("/workshop", StaticFiles(directory=workshop_path), name="workshop")
    logger.info(f"已挂载创意工坊目录: {workshop_path}")

@app.get("/subtitle")
async def get_subtitle():
    return FileResponse(get_resource_path('templates/subtitle.html'))

@app.get("/api/config/page_config")
async def get_page_config(lanlan_name: str = ""):
    """Get page config (lanlan_name and model_path)"""
    try:
        # Get character data
        _, her_name, _, lanlan_basic_config, _, _, _, _, _ = await _config_manager.aget_character_data()
        
        # If lanlan_name is provided, use it; otherwise use the current character
        target_name = lanlan_name if lanlan_name else her_name
        
        # Get the live2d field (compatible with the new _reserved structure)
        live2d_model_path = get_reserved(
            lanlan_basic_config.get(target_name, {}),
            'avatar',
            'live2d',
            'model_path',
            default=DEFAULT_LIVE2D_MODEL,
            legacy_keys=('live2d',),
        )
        if not isinstance(live2d_model_path, str):
            live2d_model_path = str(live2d_model_path) if live2d_model_path is not None else DEFAULT_LIVE2D_MODEL
        live2d_model_path = live2d_model_path.strip()
        if live2d_model_path.endswith('.model3.json'):
            parts = live2d_model_path.replace('\\', '/').split('/')
            live2d = parts[-2] if len(parts) >= 2 else parts[-1].removesuffix('.model3.json')
        else:
            live2d = live2d_model_path
        live2d = live2d.strip()
        if not live2d:
            live2d = DEFAULT_LIVE2D_MODEL
        if live2d in LEGACY_DEFAULT_LIVE2D_MODELS:
            live2d = DEFAULT_LIVE2D_MODEL
        
        # Find all models
        models = find_models()
        
        # Find the corresponding model path based on the live2d field
        model_path = next((m["path"] for m in models if m["name"] == live2d), find_model_config_file(live2d))
        if not model_path and live2d != DEFAULT_LIVE2D_MODEL:
            model_path = next(
                (m["path"] for m in models if m["name"] == DEFAULT_LIVE2D_MODEL),
                find_model_config_file(DEFAULT_LIVE2D_MODEL),
            )
        
        return {
            "success": True,
            "lanlan_name": target_name,
            "model_path": model_path
        }
    except Exception as e:
        logger.error(f"获取页面配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/config/preferences")
async def get_preferences():
    """Get user preferences consistent with the main server package."""
    preferences = await aload_user_preferences()
    return _viewer_preferences_only(preferences)

@app.get('/api/live2d/emotion_mapping/{model_name}')
def get_emotion_mapping(model_name: str):
    """Get the emotion mapping config"""
    try:
        # Use find_model_directory to search static, user documents, and workshop directories
        model_dir, _ = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # Find the .model3.json file
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # Prefer EmotionMapping; if absent, derive from FileReferences
        emotion_mapping = config_data.get('EmotionMapping')
        if not emotion_mapping:
            derived_mapping = {"motions": {}, "expressions": {}}
            file_refs = config_data.get('FileReferences', {}) or {}

            # Derive from the standard Motions structure
            motions = file_refs.get('Motions', {}) or {}
            for group_name, items in motions.items():
                files = []
                for item in items or []:
                    try:
                        file_path = item.get('File') if isinstance(item, dict) else None
                        if file_path:
                            files.append(file_path.replace('\\', '/'))
                    except Exception:
                        continue
                derived_mapping["motions"][group_name] = files

            # Derive from the standard Expressions structure (group by the Name prefix, such as happy_xxx)
            expressions = file_refs.get('Expressions', []) or []
            for item in expressions:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name') or ''
                file_path = item.get('File') or ''
                if not file_path:
                    continue
                file_path = file_path.replace('\\', '/')
                # Split the group at the first underscore
                if '_' in name:
                    group = name.split('_', 1)[0]
                else:
                    # Put entries without a prefix in the neutral group to avoid losing them
                    group = 'neutral'
                derived_mapping["expressions"].setdefault(group, []).append(file_path)

            emotion_mapping = derived_mapping
        
        return {"success": True, "config": emotion_mapping}
    except Exception as e:
        print(f"获取情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # lanlan_name is extracted from the URL; the frontend gets the configuration through the API
    return templates.TemplateResponse(request, "templates/viewer.html", {
        "request": request,
        **_viewer_static_assets_ctx(),
    })


# Store all connected clients
connected_clients = set()
subtitle_clients = set()
current_subtitle = ""
should_clear_next = False

async def _receive_ws_frame(websocket: WebSocket) -> dict:
    """Receive one raw ws message; convert disconnect frames to WebSocketDisconnect.

    starlette 0.46's receive_text()/receive_bytes() raise KeyError on a frame of
    the mismatched type (and the frame is already consumed), so a single
    unexpected binary/text frame would kill the whole connection. Using raw
    receive() with explicit key lookups lets each caller tolerate any frame type.
    """
    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code") or 1000)
    return message


@app.websocket("/subtitle_ws")
async def subtitle_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print(f"字幕客户端已连接: {websocket.client}")

    # Add to the subtitle client set
    subtitle_clients.add(websocket)

    try:
        # Send the current subtitle, if any
        if current_subtitle:
            await websocket.send_json({
                "type": "subtitle",
                "text": current_subtitle
            })

        # Keep the connection alive (keep-alive: ignore frame contents and tolerate any frame type —
        # receive_text() raises KeyError on a binary frame, which would kill the subtitle connection)
        while True:
            await _receive_ws_frame(websocket)
    except WebSocketDisconnect:
        print(f"字幕客户端已断开: {websocket.client}")
    finally:
        subtitle_clients.discard(websocket)


# Broadcast the subtitle to all subtitle clients
async def broadcast_subtitle():
    global current_subtitle, should_clear_next
    if should_clear_next:
        await clear_subtitle()
        should_clear_next = False
        # Briefly delay to let the clearing animation finish
        await asyncio.sleep(0.3)

    await broadcast_subtitle_text(current_subtitle)


# Clear the subtitle
async def clear_subtitle():
    global current_subtitle
    current_subtitle = ""
    await _broadcast(subtitle_clients, lambda client: client.send_json({"type": "clear"}), "SUBTITLE")

# Main server connection endpoint
@app.websocket("/sync/{lanlan_name}")
async def sync_endpoint(websocket: WebSocket, lanlan_name:str):
    await websocket.accept()
    print(f"✅ [SYNC] 主服务器已连接: {websocket.client}")

    try:
        while True:
            try:
                global current_subtitle
                message = await asyncio.wait_for(_receive_ws_frame(websocket), timeout=25)
                data = message.get("text")
                if data is None:
                    # Ignore non-text frames such as binary data; do not let KeyError kill the sync connection
                    continue

                # Broadcast to all connected clients
                data = json.loads(data)
                msg_type = data.get("type", "unknown")


                if msg_type == "gemini_response":
                    # Send to the subtitle display
                    subtitle_text = data.get("text", "")
                    current_subtitle += subtitle_text
                    if subtitle_text:
                        await broadcast_subtitle()

                elif msg_type == "turn end":
                    # Handle end of turn: subtitles have already been broadcast incrementally with each gemini_response,
                    # so only mark them for clearing here.
                    global should_clear_next
                    should_clear_next = True

                if msg_type != "heartbeat":
                    await broadcast_message(data)
            except asyncio.exceptions.TimeoutError:
                pass
    except WebSocketDisconnect:
        print(f"❌ [SYNC] 主服务器已断开: {websocket.client}")
    except Exception as e:
        logger.error(f"❌ [SYNC] 同步端点错误: {e}")


# Binary data sync endpoint
@app.websocket("/sync_binary/{lanlan_name}")
async def sync_binary_endpoint(websocket: WebSocket, lanlan_name:str):
    await websocket.accept()
    print(f"✅ [BINARY] 主服务器二进制连接已建立: {websocket.client}")

    try:
        while True:
            try:
                message = await asyncio.wait_for(_receive_ws_frame(websocket), timeout=25)
                data = message.get("bytes")
                if data is None:
                    # Ignore non-binary frames such as text; do not let KeyError kill the sync connection
                    continue
                if len(data)>4:
                    await broadcast_binary(data)
            except asyncio.exceptions.TimeoutError:
                pass
    except WebSocketDisconnect:
        print(f"❌ [BINARY] 主服务器二进制连接已断开: {websocket.client}")
    except Exception as e:
        logger.error(f"❌ [BINARY] 二进制同步端点错误: {e}")


# Client connection endpoint
@app.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name:str):
    await websocket.accept()
    print(f"✅ [CLIENT] 查看客户端已连接: {websocket.client}, 当前总数: {len(connected_clients) + 1}")

    # Add to the connection set
    connected_clients.add(websocket)

    try:
        # Keep the connection alive until the client disconnects (keep-alive: ignore frame contents and tolerate any frame type;
        # disconnects are converted by _receive_ws_frame into WebSocketDisconnect and raised to outer cleanup)
        while True:
            await _receive_ws_frame(websocket)
    except WebSocketDisconnect:
        print(f"❌ [CLIENT] 查看客户端已断开: {websocket.client}")
    except Exception as e:
        print(f"❌ [CLIENT] 客户端连接异常: {e}")
    finally:
        # Safely remove a client (does not raise if it has already been removed)
        connected_clients.discard(websocket)
        print(f"🗑️ [CLIENT] 已移除客户端，当前剩余: {len(connected_clients)}")


# Send to one client (with timeout), for reuse by concurrent broadcasts
# Serial await lets a slow client stall the whole pipeline (head-of-line blocking); concurrent fan-out plus a timeout prevents slow clients from holding up fast ones
async def _send_to_client(client, sender, label):
    try:
        await asyncio.wait_for(sender(client), timeout=2.0)
        return client, True
    except Exception as e:
        print(f"❌ [{label}] 广播错误到 {client.client}: {e}")
        return client, False


async def _broadcast(client_set, sender, label, success_msg=None):
    clients = client_set.copy()
    if not clients:
        return

    results = await asyncio.gather(*(
        _send_to_client(client, sender, label) for client in clients
    ))

    success_count = sum(1 for _, ok in results if ok)
    disconnected_clients = [client for client, ok in results if not ok]

    # Remove all disconnected or timed-out clients
    for client in disconnected_clients:
        client_set.discard(client)
        print(f"🗑️ [{label}] 移除断开的客户端: {client.client}")

    fail_count = len(disconnected_clients)
    if success_msg and success_count > 0:
        print(success_msg.format(success_count) + (f", 失败并移除 {fail_count} 个" if fail_count > 0 else ""))


# Broadcast subtitles to subtitle clients (concurrently, sharing the main broadcast's timeout fan-out to prevent one stuck subtitle client from blocking sync_endpoint)
async def broadcast_subtitle_text(text):
    await _broadcast(subtitle_clients, lambda client: client.send_json({"type": "subtitle", "text": text}), "SUBTITLE")


# Broadcast a message to all clients
async def broadcast_message(message):
    await _broadcast(connected_clients, lambda client: client.send_json(message), "BROADCAST", "✅ [BROADCAST] 成功广播到 {} 个客户端")


# Broadcast binary data to all clients
async def broadcast_binary(data):
    await _broadcast(connected_clients, lambda client: client.send_bytes(data), "BINARY BROADCAST", "✅ [BINARY BROADCAST] 成功广播音频到 {} 个客户端")


# Keep fire-and-forget tasks from being garbage-collected by Python 3.11+
_bg_tasks: set = set()


def _fire_task(coro):
    """Create a background task with GC protection."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


# Periodically clean up disconnected WebSocket clients. Launched from the
# FastAPI lifespan handler above (replaces the deprecated on_event("startup")).
async def cleanup_disconnected_clients():
    while True:
        try:
            # Check for and remove disconnected clients
            for client in list(connected_clients):
                try:
                    await client.send_json({"type": "heartbeat"})
                except Exception as e:
                    print("广播错误:", e)
                    # discard() avoids KeyError if the client was already
                    # removed concurrently; connected_clients is a set.
                    connected_clients.discard(client)
            await asyncio.sleep(60)  # Check once per minute
        except Exception as e:
            print(f"清理客户端错误: {e}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    # In a packaged environment, pass the app object directly instead of a string
    # The monitor server is a read-only status receiver designed to be
    # reachable by external clients, so MONITOR_HOST defaults to 0.0.0.0.
    # Set it to 127.0.0.1 for local-only use, and MONITOR_TOKEN to require
    # authentication (see app/monitor_auth.py).
    logger.info(
        "Monitor listening on %s:%s; authentication %s",
        MONITOR_HOST,
        MONITOR_SERVER_PORT,
        "enabled" if monitor_auth_enabled() else "disabled",
    )
    uvicorn.run(app, host=MONITOR_HOST, port=MONITOR_SERVER_PORT, reload=False)
