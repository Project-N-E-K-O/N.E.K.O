"""
Moltbot Bridge Plugin

N.E.K.O 插件,用于与 Moltbot Gateway 集成。
提供双向消息转发和协议适配功能。
插件内部维护独立的 FastAPI 服务器用于 WebSocket 通信。
"""
from typing import Any, Dict, Optional
import asyncio
import time
import threading
import json
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry, lifecycle
from plugin.sdk import ok


@neko_plugin
class MoltbotBridgePlugin(NekoPluginBase):
    """Moltbot 桥接插件
    
    功能:
    - 接收来自 Moltbot 的消息请求
    - 转发到 N.E.K.O Main Server
    - 接收 N.E.K.O 的响应
    - 推送回 Moltbot Gateway
    """
    
    def __init__(self, ctx):
        super().__init__(ctx)
        
        # 启用文件日志
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.plugin_id = ctx.plugin_id
        
        # FastAPI 服务器
        self._fastapi_app: Optional[FastAPI] = None
        self._fastapi_server: Optional[uvicorn.Server] = None
        self._fastapi_thread: Optional[threading.Thread] = None
        self._active_ws_connections: Dict[str, WebSocket] = {}
        self._ws_lock = threading.Lock()
        
        # 存储待处理的响应 (runId -> response data)
        self._pending_responses: Dict[str, Dict[str, Any]] = {}
        # 等待响应的事件 (runId -> threading.Event) - 使用线程安全的 Event
        self._response_events: Dict[str, threading.Event] = {}
        # 流式文本累积 (runId -> accumulated text)
        self._streaming_text: Dict[str, str] = {}
        # 响应相关共享状态锁
        self._response_lock = threading.Lock()
        # 响应最大保留时间（秒）
        self._response_max_age_seconds: float = 300.0
        # 上次清理时间
        self._last_cleanup_time: float = time.time()
        
        self.logger.info("MoltbotBridgePlugin initialized")
    
    def _cleanup_old_responses(self, max_age_seconds: Optional[float] = None) -> int:
        """清理超时的 pending responses，防止内存泄漏
        
        Args:
            max_age_seconds: 最大保留时间，默认使用 self._response_max_age_seconds
            
        Returns:
            清理的条目数量
        """
        if max_age_seconds is None:
            max_age_seconds = self._response_max_age_seconds
        
        current_time = time.time()
        with self._response_lock:
            expired_keys = [
                key for key, value in self._pending_responses.items()
                if current_time - value.get("timestamp", 0) > max_age_seconds
            ]
            for key in expired_keys:
                del self._pending_responses[key]
                self._response_events.pop(key, None)
                self._streaming_text.pop(key, None)
        
        if expired_keys:
            self.logger.debug(f"Cleaned up {len(expired_keys)} expired responses")
        
        return len(expired_keys)
    
    @lifecycle(id="startup")
    async def startup(self, **_):
        """插件启动时的初始化"""
        try:
            # 使用 SDK 的 config 读取配置
            gateway_url = await self.config.get("moltbot.gateway_url", default="http://localhost:18789")
            neko_main_url = await self.config.get("moltbot.neko_main_url", default="http://localhost:48911")
            ws_port = await self.config.get("moltbot.ws_port", default=49916)
            
            # 创建 FastAPI 应用
            self._create_fastapi_app()
            
            # 启动 FastAPI 服务器
            self._start_fastapi_server(port=ws_port)
            
            self.logger.info(
                "Moltbot Bridge started: gateway={} neko={} ws_port={}",
                gateway_url,
                neko_main_url,
                ws_port
            )
            
            return ok(data={
                "status": "started",
                "gateway_url": gateway_url,
                "neko_main_url": neko_main_url,
                "ws_port": ws_port,
                "ws_endpoint": f"ws://127.0.0.1:{ws_port}/ws"
            })
        except Exception as e:
            self.logger.exception("Failed to start Moltbot Bridge")
            return ok(data={"status": "error", "error": str(e)})
    
    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        """插件关闭时的清理"""
        self.logger.info("Moltbot Bridge shutting down")
        
        # 停止 FastAPI 服务器
        self._stop_fastapi_server()
        
        return ok(data={"status": "shutdown"})
    
    def _create_fastapi_app(self):
        """创建 FastAPI 应用"""
        app = FastAPI(title="Moltbot Bridge WebSocket Server")
        
        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket 端点供 Moltbot 连接"""
            connection_id = f"moltbot-{uuid.uuid4().hex}"
            
            try:
                await websocket.accept()
                
                # 保存连接
                with self._ws_lock:
                    self._active_ws_connections[connection_id] = websocket
                
                self.logger.info(f"Moltbot connected: {connection_id}")
                
                # 发送连接确认
                await websocket.send_json({
                    "type": "connected",
                    "connection_id": connection_id,
                    "timestamp": time.time()
                })
                
                # 消息处理循环
                while True:
                    data = await websocket.receive_text()
                    try:
                        message = json.loads(data)
                    except json.JSONDecodeError as e:
                        self.logger.warning(f"Invalid JSON from {connection_id}: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "error": "Invalid JSON format"
                        })
                        continue
                    
                    msg_type = message.get("type")
                    
                    if msg_type == "ping":
                        # 心跳响应
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": time.time()
                        })
                    
                    elif msg_type == "message":
                        # 处理消息
                        msg_data = message.get("data", {})
                        user_message = msg_data.get("message", "")
                        session_key = msg_data.get("session_key")
                        
                        self.logger.info(
                            f"Received message from {connection_id}: "
                            f"session_key={session_key} message='{user_message[:50]}...'"
                        )
                        
                        # TODO: 转发到 N.E.K.O Main Server
                        # 当前返回模拟响应
                        await websocket.send_json({
                            "type": "response",
                            "data": {
                                "type": "text",
                                "content": f"[Bridge] 收到: {user_message}",
                                "session_key": session_key
                            }
                        })
                    
                    elif msg_type == "agent_event":
                        # Moltbot Gateway 的 AI 响应事件
                        run_id = message.get("runId")
                        if not run_id:
                            self.logger.warning("Agent event missing runId, ignoring")
                            continue
                        session_key = message.get("sessionKey")
                        event_state = message.get("state")  # delta, final, error, aborted
                        agent_message = message.get("message")
                        text_content = message.get("text")  # 已提取的文本内容
                        error_message = message.get("errorMessage")
                        
                        self.logger.info(
                            f"Agent event: state={event_state}, runId={run_id}, sessionKey={session_key}"
                        )
                        
                        if event_state == "delta":
                            # 流式响应片段 - Gateway 返回的是累积式文本，直接替换
                            if text_content and run_id:
                                with self._response_lock:
                                    self._streaming_text[run_id] = text_content
                                self.logger.info(f"Delta text: {len(text_content)} chars")
                        
                        elif event_state == "final":
                            # 最终响应
                            self.logger.info(f"Final response received for runId={run_id}")
                            
                            # 定期清理过期响应，防止内存泄漏
                            self._cleanup_old_responses()
                            
                            # 优先使用累积的流式文本，如果没有则使用 final 的 text
                            with self._response_lock:
                                final_text = self._streaming_text.get(run_id, "") or text_content or ""
                            
                            if final_text:
                                self.logger.info(f"Final text ({len(final_text)} chars): {final_text[:200]}...")
                            
                            # 存储最终响应并触发事件
                            with self._response_lock:
                                self._pending_responses[run_id] = {
                                    "text": final_text,
                                    "message": agent_message,
                                    "session_key": session_key,
                                    "timestamp": time.time(),
                                    "success": True
                                }
                                self._streaming_text.pop(run_id, None)
                                event = self._response_events.get(run_id)
                            
                            # 触发等待事件 (threading.Event 是线程安全的)
                            if event:
                                self.logger.info(f"Triggering event for runId={run_id}")
                                event.set()
                        
                        elif event_state == "error":
                            self.logger.error(f"Agent error for runId={run_id}: {error_message}")
                            # 存储错误响应
                            if run_id:
                                with self._response_lock:
                                    self._pending_responses[run_id] = {
                                        "text": "",
                                        "error": error_message,
                                        "session_key": session_key,
                                        "timestamp": time.time(),
                                        "success": False
                                    }
                                    self._streaming_text.pop(run_id, None)
                                    event = self._response_events.get(run_id)
                                if event:
                                    event.set()
                        
                        elif event_state == "aborted":
                            self.logger.warning(f"Agent aborted for runId={run_id}")
                            if run_id:
                                with self._response_lock:
                                    self._pending_responses[run_id] = {
                                        "text": self._streaming_text.get(run_id, ""),
                                        "error": "aborted",
                                        "session_key": session_key,
                                        "timestamp": time.time(),
                                        "success": False
                                    }
                                    self._streaming_text.pop(run_id, None)
                                    event = self._response_events.get(run_id)
                                if event:
                                    event.set()
                    
                    elif msg_type == "agent_error":
                        # Moltbot Gateway 的错误事件
                        run_id = message.get("runId")
                        session_key = message.get("sessionKey")
                        error = message.get("error")
                        self.logger.error(f"Agent error: runId={run_id}, error={error}")
                    
                    else:
                        self.logger.warning(f"Unknown message type: {msg_type}")
            
            except WebSocketDisconnect:
                self.logger.info(f"Moltbot disconnected: {connection_id}")
            
            except Exception as e:
                self.logger.exception(f"WebSocket error for {connection_id}: {e}")
            
            finally:
                # 移除连接
                with self._ws_lock:
                    self._active_ws_connections.pop(connection_id, None)
        
        @app.get("/health")
        async def health_check():
            """健康检查"""
            return {
                "status": "ok",
                "active_connections": len(self._active_ws_connections)
            }
        
        self._fastapi_app = app
        self.logger.info("FastAPI app created")
    
    def _start_fastapi_server(self, port: int = 48916):
        """在后台线程中启动 FastAPI 服务器"""
        if self._fastapi_app is None:
            raise RuntimeError("FastAPI app not created")
        
        config = uvicorn.Config(
            self._fastapi_app,
            host="127.0.0.1",
            port=port,
            log_level="info"
        )
        self._fastapi_server = uvicorn.Server(config)
        
        def run_server():
            try:
                self.logger.info(f"Starting FastAPI server on port {port}...")
                asyncio.run(self._fastapi_server.serve())
            except Exception as e:
                self.logger.exception(f"FastAPI server error: {e}")
        
        self._fastapi_thread = threading.Thread(
            target=run_server,
            daemon=True,
            name="moltbot-bridge-fastapi"
        )
        self._fastapi_thread.start()
        
        # 等待服务器启动
        time.sleep(1.0)
        self.logger.info(f"FastAPI server started on ws://127.0.0.1:{port}/ws")
    
    def _stop_fastapi_server(self):
        """停止 FastAPI 服务器"""
        if self._fastapi_server:
            self.logger.info("Stopping FastAPI server...")
            self._fastapi_server.should_exit = True
            
            if self._fastapi_thread and self._fastapi_thread.is_alive():
                self._fastapi_thread.join(timeout=3.0)
            
            self.logger.info("FastAPI server stopped")
    
    @plugin_entry(
        id="send_to_moltbot",
        name="Send Message to Moltbot",
        description="向 Moltbot 发送消息",
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要发送的消息内容"
                },
                "session_key": {
                    "type": "string",
                    "description": "会话标识"
                },
                "message_type": {
                    "type": "string",
                    "description": "消息类型: chat(发起对话), notify(通知), response(响应)",
                    "enum": ["chat", "notify", "response"],
                    "default": "chat"
                },
                "connection_id": {
                    "type": ["string", "null"],
                    "description": "指定连接 ID,不指定则广播到所有连接"
                }
            },
            "required": ["message", "session_key"]
        }
    )
    async def send_to_moltbot(self, message: str, session_key: str, message_type: str = "chat", connection_id: Optional[str] = None, **_):
        """向 Moltbot 发送指令 (已弃用,建议使用 chat_with_moltbot 或 send_command)"""
        try:
            # 构造指令消息
            msg_data = {
                "type": "neko_command",
                "data": {
                    "command": message_type,  # chat, notify, status, etc.
                    "payload": {
                        "message": message,
                        "session_key": session_key,
                    },
                    "timestamp": time.time()
                }
            }
            
            # 获取目标连接
            with self._ws_lock:
                if connection_id:
                    if connection_id not in self._active_ws_connections:
                        return ok(data={
                            "success": False,
                            "error": f"Connection {connection_id} not found"
                        })
                    target_connections = {connection_id: self._active_ws_connections[connection_id]}
                else:
                    target_connections = dict(self._active_ws_connections)
            
            if not target_connections:
                return ok(data={
                    "success": False,
                    "error": "No active connections"
                })
            
            sent_count = 0
            errors = []
            
            for conn_id, websocket in target_connections.items():
                try:
                    await websocket.send_json(msg_data)
                    sent_count += 1
                    self.logger.info(f"Sent command '{message_type}' to {conn_id}")
                except Exception as e:
                    error_msg = f"{conn_id}: {str(e)}"
                    errors.append(error_msg)
                    self.logger.error(f"Failed to send to {conn_id}: {e}")
            
            return ok(data={
                "success": True,
                "sent_count": sent_count,
                "total_connections": len(target_connections),
                "errors": errors if errors else None
            })
            
        except Exception as e:
            self.logger.exception("Failed to send message to Moltbot")
            return ok(data={
                "success": False,
                "error": str(e)
            })
    
    @plugin_entry(
        id="chat_with_moltbot",
        name="Chat with Moltbot",
        description="与 Moltbot 进行持续对话(支持多轮对话历史)",
        timeout=120,  # 自定义超时 120 秒，覆盖默认的 10 秒
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要发送的消息内容"
                },
                "session_key": {
                    "type": "string",
                    "description": "会话标识,相同的 session_key 会保持对话历史"
                },
                "timeout": {
                    "type": "number",
                    "description": "等待响应的超时时间(秒)",
                    "default": 120
                }
            },
            "required": ["message", "session_key"]
        }
    )
    async def chat_with_moltbot(self, message: str, session_key: str, timeout: float = 120, **_):
        """与 Moltbot 进行持续对话,通过 WebSocket 等待流式响应"""
        import aiohttp
        
        try:
            gateway_url = await self.config.get("moltbot.gateway_url", default="http://localhost:18789")
            url = f"{gateway_url}/neko/chat"
            
            self.logger.info(f"Sending chat to Moltbot: {message[:50]}... (session={session_key})")
            
            # 发送请求获取 runId
            run_id = None
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(
                    url,
                    json={
                        "message": message,
                        "sessionKey": session_key
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    result = await response.json()
                    
                    # 202 Accepted 也是成功的响应
                    if response.status in (200, 202) and result.get("ok"):
                        run_id = result.get("runId")
                        self.logger.info(f"Chat request accepted, runId={run_id}")
                    else:
                        error_msg = result.get("error", f"HTTP {response.status}")
                        self.logger.error(f"Moltbot chat request failed: {error_msg}")
                        return ok(data={
                            "success": False,
                            "error": error_msg
                        })
            
            if not run_id:
                return ok(data={
                    "success": False,
                    "error": "No runId returned from Gateway"
                })
            
            # 创建等待事件 (threading.Event 用于跨线程通信)
            with self._response_lock:
                event = self._response_events.get(run_id)
                if event is None:
                    event = threading.Event()
                    self._response_events[run_id] = event
                # 若响应已提前到达，直接唤醒等待
                if run_id in self._pending_responses:
                    event.set()
            
            try:
                # 等待 WebSocket 收到响应 (使用线程安全的 wait)
                # 注意：已通过 @plugin_entry(timeout=120) 设置框架超时为 120 秒
                effective_timeout = timeout
                self.logger.info(f"Waiting for response (runId={run_id}, timeout={effective_timeout}s)...")
                
                # 使用 asyncio.to_thread 在线程池中等待，避免阻塞事件循环
                event_set = await asyncio.to_thread(event.wait, effective_timeout)
                
                if not event_set:
                    # 超时，返回已累积的部分响应
                    with self._response_lock:
                        partial_text = self._streaming_text.get(run_id, "")
                    if partial_text:
                        # 有部分响应，视为成功但标记为不完整
                        self.logger.info(f"Response incomplete after {effective_timeout}s, returning partial: {len(partial_text)} chars")
                        
                        # 即使是部分响应也通过 export 输出
                        try:
                            await self.ctx.export_push_text_async(
                                text=partial_text,
                                description=f"Moltbot partial response (timeout) for session: {session_key}",
                                metadata={
                                    "source": "moltbot_bridge",
                                    "session_key": session_key,
                                    "moltbot_run_id": run_id,
                                    "incomplete": True,
                                },
                            )
                        except Exception as export_err:
                            self.logger.warning(f"Failed to export partial result: {export_err}")
                        
                        return ok(data={
                            "success": True,
                            "response": partial_text,
                            "session_key": session_key,
                            "incomplete": True
                        })
                    else:
                        self.logger.warning(f"Response timeout after {effective_timeout}s, no content")
                        return ok(data={
                            "success": False,
                            "error": f"Response timeout after {effective_timeout}s"
                        })
                
                # 获取响应
                with self._response_lock:
                    response_data = dict(self._pending_responses.get(run_id, {}))
                
                if response_data.get("success"):
                    final_text = response_data.get("text", "")
                    self.logger.info(f"Got response ({len(final_text)} chars): {final_text[:100]}...")
                    
                    # 通过 export 输出最终结果
                    try:
                        export_result = await self.ctx.export_push_text_async(
                            text=final_text,
                            description=f"Moltbot response for session: {session_key}",
                            metadata={
                                "source": "moltbot_bridge",
                                "session_key": session_key,
                                "moltbot_run_id": run_id,
                            },
                        )
                        self.logger.info(f"Export pushed: {export_result}")
                    except Exception as export_err:
                        self.logger.warning(f"Failed to export result: {export_err}")
                    
                    return ok(data={
                        "success": True,
                        "response": final_text,
                        "session_key": response_data.get("session_key", session_key)
                    })
                else:
                    error_msg = response_data.get("error", "Unknown error")
                    self.logger.error(f"Response error: {error_msg}")
                    return ok(data={
                        "success": False,
                        "error": error_msg,
                        "partial_response": response_data.get("text", "")
                    })
                    
            except asyncio.TimeoutError:
                # 超时，返回已累积的部分响应
                with self._response_lock:
                    partial_text = self._streaming_text.get(run_id, "")
                self.logger.warning(f"Response timeout after {timeout}s, partial: {len(partial_text)} chars")
                return ok(data={
                    "success": False,
                    "error": f"Response timeout after {timeout}s",
                    "partial_response": partial_text
                })
            finally:
                # 清理
                with self._response_lock:
                    self._response_events.pop(run_id, None)
                    self._pending_responses.pop(run_id, None)
                    self._streaming_text.pop(run_id, None)
                        
        except Exception as e:
            self.logger.exception("Failed to chat with Moltbot")
            return ok(data={
                "success": False,
                "error": str(e)
            })

    @plugin_entry(
        id="get_status",
        name="Get Bridge Status",
        description="获取桥接插件的状态信息",
        input_schema={
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    async def get_status(self, **_):
        """获取插件状态"""
        try:
            # 使用 SDK config 读取配置
            gateway_url = await self.config.get("moltbot.gateway_url", default="http://localhost:18789")
            neko_main_url = await self.config.get("moltbot.neko_main_url", default="http://localhost:48911")
            ws_port = await self.config.get("moltbot.ws_port", default=49916)
            debug = await self.config.get("moltbot.debug", default=False)
            
            # 获取 WebSocket 连接数
            with self._ws_lock:
                active_connections = len(self._active_ws_connections)
                connection_ids = list(self._active_ws_connections.keys())
            
            return ok(data={
                "plugin_id": self.plugin_id,
                "status": "running",
                "config": {
                    "gateway_url": gateway_url,
                    "neko_main_url": neko_main_url,
                    "ws_port": ws_port,
                    "debug": debug
                },
                "websocket": {
                    "active_connections": active_connections,
                    "connection_ids": connection_ids,
                    "endpoint": f"ws://127.0.0.1:{ws_port}/ws"
                }
            })
        except Exception as e:
            self.logger.exception("Failed to get status")
            return ok(data={
                "status": "error",
                "error": str(e)
            })
