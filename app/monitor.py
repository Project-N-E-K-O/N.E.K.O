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
from config import MONITOR_SERVER_PORT, DEFAULT_LIVE2D_MODEL_NAME
from utils.config_manager import get_config_manager, get_reserved
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn
from fastapi.templating import Jinja2Templates
from utils.frontend_utils import find_models, find_model_config_file, find_model_directory
from utils.workshop_utils import get_default_workshop_folder
from utils.preferences import aload_user_preferences

# Setup logger
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Monitor", log_level=logging.INFO)

# 获取资源路径（支持打包后的环境）
def get_resource_path(relative_path):
    """Get the absolute path of a resource, supporting both dev and packaged environments.

    monitor.py now lives at ``<repo>/app/monitor.py``, so in the source / Nuitka
    cases the resource root needs ``dirname(dirname(__file__))`` to get back to
    the project root (where static/ templates/ etc. live). PyInstaller unpacks
    all resources to the top level of ``sys._MEIPASS``, so that path is unchanged.
    """
    if getattr(sys, 'frozen', False):
        # 打包后的环境
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller
            base_path = sys._MEIPASS
        else:
            # Nuitka standalone：app/ 是子包，资源在父目录
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    else:
        # 开发环境：app/monitor.py 的祖父目录就是项目根
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

templates = Jinja2Templates(directory=get_resource_path(""))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: launch a background task that periodically cleans up
    # disconnected WebSocket clients. Replaces the deprecated
    # @app.on_event("startup") hook (FastAPI lifespan is the supported API).
    _fire_task(cleanup_disconnected_clients())
    yield


app = FastAPI(lifespan=lifespan)

DEFAULT_LIVE2D_MODEL = DEFAULT_LIVE2D_MODEL_NAME
LEGACY_DEFAULT_LIVE2D_MODELS = {
    model_name for model_name in ("yui_default", "yui-default") if model_name != DEFAULT_LIVE2D_MODEL
}

# 挂载静态文件
app.mount("/static", StaticFiles(directory=get_resource_path("static")), name="static")
_config_manager = get_config_manager()

# 挂载用户Live2D目录（与main_server.py保持一致，CFA感知）
_readable_live2d = _config_manager.readable_live2d_dir
_serve_live2d_path = str(_readable_live2d) if _readable_live2d else str(_config_manager.live2d_dir)
if os.path.exists(_serve_live2d_path):
    app.mount("/user_live2d", StaticFiles(directory=_serve_live2d_path), name="user_live2d")
    logger.info(f"已挂载用户Live2D目录: {_serve_live2d_path}")
# CFA 场景：可写回退目录额外挂载
if _readable_live2d and str(_config_manager.live2d_dir) != _serve_live2d_path:
    _writable_live2d_path = str(_config_manager.live2d_dir)
    if os.path.exists(_writable_live2d_path):
        app.mount("/user_live2d_local", StaticFiles(directory=_writable_live2d_path), name="user_live2d_local")
        logger.info(f"已挂载本地Live2D目录(CFA回退): {_writable_live2d_path}")

# 挂载创意工坊目录（与main_server.py保持一致）
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
        # 获取角色数据
        _, her_name, _, lanlan_basic_config, _, _, _, _, _ = await _config_manager.aget_character_data()
        
        # 如果提供了 lanlan_name 参数，使用它；否则使用当前角色
        target_name = lanlan_name if lanlan_name else her_name
        
        # 获取 live2d 字段（兼容 _reserved 新结构）
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
        
        # 查找所有模型
        models = find_models()
        
        # 根据 live2d 字段查找对应的 model path
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
    """Get user preferences (consistent with main_server.py)"""
    preferences = await aload_user_preferences()
    return preferences

@app.get('/api/live2d/emotion_mapping/{model_name}')
def get_emotion_mapping(model_name: str):
    """Get the emotion mapping config"""
    try:
        # 使用 find_model_directory 在 static、用户文档目录、创意工坊目录中查找模型
        model_dir, _ = find_model_directory(model_name)
        if not model_dir or not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 优先使用 EmotionMapping；若不存在则从 FileReferences 推导
        emotion_mapping = config_data.get('EmotionMapping')
        if not emotion_mapping:
            derived_mapping = {"motions": {}, "expressions": {}}
            file_refs = config_data.get('FileReferences', {}) or {}

            # 从标准 Motions 结构推导
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

            # 从标准 Expressions 结构推导（按 Name 的前缀进行分组，如 happy_xxx）
            expressions = file_refs.get('Expressions', []) or []
            for item in expressions:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name') or ''
                file_path = item.get('File') or ''
                if not file_path:
                    continue
                file_path = file_path.replace('\\', '/')
                # 根据第一个下划线拆分分组
                if '_' in name:
                    group = name.split('_', 1)[0]
                else:
                    # 无前缀的归入 neutral 组，避免丢失
                    group = 'neutral'
                derived_mapping["expressions"].setdefault(group, []).append(file_path)

            emotion_mapping = derived_mapping
        
        return {"success": True, "config": emotion_mapping}
    except Exception as e:
        print(f"获取情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # lanlan_name 将从 URL 中提取，前端会通过 API 获取配置
    return templates.TemplateResponse("templates/viewer.html", {
        "request": request
    })


# 存储所有连接的客户端
connected_clients = set()
subtitle_clients = set()
current_subtitle = ""
should_clear_next = False

def is_japanese(text):
    import re
    # 检测平假名、片假名、汉字
    japanese_pattern = re.compile(r'[\u3040-\u309F\u30A0-\u30FF]')
    return bool(japanese_pattern.search(text))

# 简单的日文到中文翻译（这里需要你集成实际的翻译API）
async def translate_japanese_to_chinese(text):
    # 为了演示，这里返回一个占位符
    # 你需要根据实际情况实现翻译功能
    pass

@app.websocket("/subtitle_ws")
async def subtitle_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print(f"字幕客户端已连接: {websocket.client}")

    # 添加到字幕客户端集合
    subtitle_clients.add(websocket)

    try:
        # 发送当前字幕（如果有）
        if current_subtitle:
            await websocket.send_json({
                "type": "subtitle",
                "text": current_subtitle
            })

        # 保持连接
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        print(f"字幕客户端已断开: {websocket.client}")
    finally:
        subtitle_clients.discard(websocket)


# 广播字幕到所有字幕客户端
async def broadcast_subtitle():
    global current_subtitle, should_clear_next
    if should_clear_next:
        await clear_subtitle()
        should_clear_next = False
        # 给一个短暂的延迟让清空动画完成
        await asyncio.sleep(0.3)

    await broadcast_subtitle_text(current_subtitle)


# 清空字幕
async def clear_subtitle():
    global current_subtitle
    current_subtitle = ""
    await _broadcast(subtitle_clients, lambda client: client.send_json({"type": "clear"}), "SUBTITLE")

# 主服务器连接端点
@app.websocket("/sync/{lanlan_name}")
async def sync_endpoint(websocket: WebSocket, lanlan_name:str):
    await websocket.accept()
    print(f"✅ [SYNC] 主服务器已连接: {websocket.client}")

    try:
        while True:
            try:
                global current_subtitle
                data = await asyncio.wait_for(websocket.receive_text(), timeout=25)

                # 广播到所有连接的客户端
                data = json.loads(data)
                msg_type = data.get("type", "unknown")


                if msg_type == "gemini_response":
                    # 发送到字幕显示
                    subtitle_text = data.get("text", "")
                    current_subtitle += subtitle_text
                    if subtitle_text:
                        await broadcast_subtitle()

                elif msg_type == "turn end":
                    # 处理回合结束
                    if current_subtitle:
                        # 检查是否为日文，如果是则翻译
                        if is_japanese(current_subtitle):
                            translated_text = await translate_japanese_to_chinese(current_subtitle)
                            # 翻译未实现/失败时返回 None，保留原文，避免 current_subtitle 被置空后下一轮 += 崩溃
                            if translated_text:
                                current_subtitle = translated_text
                                await broadcast_subtitle_text(translated_text)

                    # 清空字幕区域，准备下一条
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


# 二进制数据同步端点
@app.websocket("/sync_binary/{lanlan_name}")
async def sync_binary_endpoint(websocket: WebSocket, lanlan_name:str):
    await websocket.accept()
    print(f"✅ [BINARY] 主服务器二进制连接已建立: {websocket.client}")

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_bytes(), timeout=25)
                if len(data)>4:
                    await broadcast_binary(data)
            except asyncio.exceptions.TimeoutError:
                pass
    except WebSocketDisconnect:
        print(f"❌ [BINARY] 主服务器二进制连接已断开: {websocket.client}")
    except Exception as e:
        logger.error(f"❌ [BINARY] 二进制同步端点错误: {e}")


# 客户端连接端点
@app.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name:str):
    await websocket.accept()
    print(f"✅ [CLIENT] 查看客户端已连接: {websocket.client}, 当前总数: {len(connected_clients) + 1}")

    # 添加到连接集合
    connected_clients.add(websocket)

    try:
        # 保持连接直到客户端断开
        while True:
            # 接收任何类型的消息（文本或二进制），主要用于保持连接
            try:
                await websocket.receive_text()
            except:
                # 如果收到的是二进制数据，receive_text() 会失败，尝试 receive_bytes()
                try:
                    await websocket.receive_bytes()
                except:
                    # 如果两者都失败，等待一下再继续
                    await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        print(f"❌ [CLIENT] 查看客户端已断开: {websocket.client}")
    except Exception as e:
        print(f"❌ [CLIENT] 客户端连接异常: {e}")
    finally:
        # 安全地移除客户端（即使已经被移除也不会报错）
        connected_clients.discard(websocket)
        print(f"🗑️ [CLIENT] 已移除客户端，当前剩余: {len(connected_clients)}")


# 单个客户端发送（带超时），供并发广播复用
# 串行 await 会被慢客户端拖住整条管线（队头阻塞），并发 fan-out + 超时让慢的不再拖快的
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

    # 移除所有断开/超时的客户端
    for client in disconnected_clients:
        client_set.discard(client)
        print(f"🗑️ [{label}] 移除断开的客户端: {client.client}")

    fail_count = len(disconnected_clients)
    if success_msg and success_count > 0:
        print(success_msg.format(success_count) + (f", 失败并移除 {fail_count} 个" if fail_count > 0 else ""))


# 广播字幕到字幕客户端（并发，与主广播共用超时 fan-out，避免某个字幕客户端卡住拖住 sync_endpoint）
async def broadcast_subtitle_text(text):
    await _broadcast(subtitle_clients, lambda client: client.send_json({"type": "subtitle", "text": text}), "SUBTITLE")


# 广播消息到所有客户端
async def broadcast_message(message):
    await _broadcast(connected_clients, lambda client: client.send_json(message), "BROADCAST", "✅ [BROADCAST] 成功广播到 {} 个客户端")


# 广播二进制数据到所有客户端
async def broadcast_binary(data):
    await _broadcast(connected_clients, lambda client: client.send_bytes(data), "BINARY BROADCAST", "✅ [BINARY BROADCAST] 成功广播音频到 {} 个客户端")


# 防止 fire-and-forget 任务被 Python 3.11+ GC 回收
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
            # 检查并移除已断开的客户端
            for client in list(connected_clients):
                try:
                    await client.send_json({"type": "heartbeat"})
                except Exception as e:
                    print("广播错误:", e)
                    # discard() avoids KeyError if the client was already
                    # removed concurrently; connected_clients is a set.
                    connected_clients.discard(client)
            await asyncio.sleep(60)  # 每分钟检查一次
        except Exception as e:
            print(f"清理客户端错误: {e}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    # 在打包环境中，直接传递 app 对象而不是字符串
    # The monitor server is a read-only status receiver designed to be
    # reachable by external clients. Keep binding to 0.0.0.0 to preserve
    # its intended use; hardening (e.g. token auth) should be additive.
    uvicorn.run(app, host="0.0.0.0", port=MONITOR_SERVER_PORT, reload=False)
