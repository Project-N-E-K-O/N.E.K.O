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
        
        # 存储待处理的响应
        self._pending_responses: Dict[str, Dict[str, Any]] = {}
        
        self.logger.info("MoltbotBridgePlugin initialized")
    
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
            connection_id = f"moltbot-{int(time.time() * 1000)}"
            
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
                    message = json.loads(data)
                    
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
                        session_key = message.get("sessionKey")
                        state = message.get("state")  # delta, final, error, aborted
                        agent_message = message.get("message")
                        text_content = message.get("text")  # 已提取的文本内容
                        error_message = message.get("errorMessage")
                        
                        self.logger.info(
                            f"Agent event: state={state}, runId={run_id}, sessionKey={session_key}"
                        )
                        
                        if state == "delta":
                            # 流式响应片段
                            if text_content:
                                self.logger.info(f"Delta text: {text_content[:100]}...")
                        
                        elif state == "final":
                            # 最终响应
                            self.logger.info(f"Final response received for runId={run_id}")
                            if text_content:
                                self.logger.info(f"Final text: {text_content[:200]}...")
                                # 存储最终响应，供后续查询
                                self._pending_responses[run_id] = {
                                    "text": text_content,
                                    "message": agent_message,
                                    "session_key": session_key,
                                    "timestamp": time.time()
                                }
                        
                        elif state == "error":
                            self.logger.error(f"Agent error for runId={run_id}: {error_message}")
                        
                        elif state == "aborted":
                            self.logger.warning(f"Agent aborted for runId={run_id}")
                    
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
                }
            },
            "required": ["message", "session_key"]
        }
    )
    async def chat_with_moltbot(self, message: str, session_key: str, **_):
        """与 Moltbot 进行持续对话,直接调用 /neko/chat 端点"""
        import aiohttp
        
        try:
            gateway_url = await self.config.get("moltbot.gateway_url", default="http://localhost:18789")
            url = f"{gateway_url}/neko/chat"
            
            self.logger.info(f"Sending chat to Moltbot: {message[:50]}... (session={session_key})")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={
                        "message": message,
                        "sessionKey": session_key
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    result = await response.json()
                    
                    if response.status == 200 and result.get("ok"):
                        self.logger.info(f"Moltbot response: {result.get('response', '')[:100]}...")
                        return ok(data={
                            "success": True,
                            "response": result.get("response", ""),
                            "session_key": result.get("sessionKey", session_key)
                        })
                    else:
                        error_msg = result.get("error", f"HTTP {response.status}")
                        self.logger.error(f"Moltbot chat failed: {error_msg}")
                        return ok(data={
                            "success": False,
                            "error": error_msg
                        })
                        
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
