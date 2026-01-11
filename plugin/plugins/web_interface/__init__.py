"""
Web Interface Plugin

一个提供 FastAPI Web 界面的插件，在插件激活后可以通过网页访问并查看消息。
"""
import asyncio
import html
import socket
import threading
import time
from typing import Any, Optional
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, lifecycle, plugin_entry
from plugin.sdk import ok, fail, ErrorCode
import os


@neko_plugin
class WebInterfacePlugin(NekoPluginBase):
    """Web 界面插件"""
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        # 启用文件日志
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        
        # FastAPI 应用
        self.app: Optional[FastAPI] = None
        self.server_thread: Optional[threading.Thread] = None
        self.server_config: Optional[uvicorn.Config] = None
        self.server: Optional[uvicorn.Server] = None
        
        # 服务器配置
        self.host = "127.0.0.1"
        self.port = int(os.getenv("NEKO_WEB_INTERFACE_PORT", "8888"))
        
        # 消息存储（使用锁保护并发访问）
        self.messages = []
        self.max_messages = 100
        self._messages_lock = threading.Lock()
        
        self.logger.info("WebInterfacePlugin initialized")
    
    @lifecycle(
        id="startup",
        name="Plugin Startup",
        description="插件启动时初始化并启动 Web 服务器"
    )
    def startup(self, **_):
        """启动时初始化 Web 服务器"""
        self.logger.info("Starting Web Interface Plugin...")
        
        try:
            # 创建 FastAPI 应用
            self.app = FastAPI(
                title="N.E.K.O Web Interface Plugin",
                description="插件 Web 界面",
                version="1.0.0"
            )
            
            # 添加欢迎消息
            self._add_message("系统", "Web 界面插件已启动", priority=7)
            
            # 注册路由
            self._setup_routes()

            def _find_available_port(start_port: int, max_tries: int = 50) -> int:
                for p in range(start_port, start_port + max_tries):
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        s.bind((self.host, p))
                    except OSError:
                        continue
                    else:
                        return p
                    finally:
                        try:
                            s.close()
                        except OSError:
                            pass
                raise RuntimeError(
                    f"无法在 {start_port} 到 {start_port + max_tries - 1} 范围内找到可用端口"
                )

            selected_port = _find_available_port(self.port)
            if selected_port != self.port:
                self.logger.warning(
                    "Web interface port {} is unavailable, switched to {}",
                    self.port,
                    selected_port,
                )
                self.port = selected_port
            
            # 在后台线程中启动服务器
            self.server_config = uvicorn.Config(
                app=self.app,
                host=self.host,
                port=self.port,
                log_level="info",
                log_config=None,  # 使用宿主进程配置的 loguru 拦截器
                access_log=True
            )
            self.server = uvicorn.Server(self.server_config)
            
            self.server_thread = threading.Thread(
                target=self._run_server,
                daemon=True,
                name="WebInterfaceServer"
            )
            self.server_thread.start()
            
            # 等待服务器启动
            time.sleep(0.5)
            
            # 上报状态
            self.report_status({
                "status": "running",
                "host": self.host,
                "port": self.port,
                "url": f"http://{self.host}:{self.port}"
            })
            
            self.ctx.push_message(
                source="web_interface",
                message_type="url",
                description="Web 界面已启动",
                priority=8,
                content=f"http://{self.host}:{self.port}",
                metadata={
                    "host": self.host,
                    "port": self.port,
                    "status": "started"
                },
                fast_mode=False,
            )
            
            self.logger.info(f"Web server started at http://{self.host}:{self.port}")
            
            return ok(
                data={
                    "status": "ready",
                    "host": self.host,
                    "port": self.port,
                    "url": f"http://{self.host}:{self.port}",
                }
            )
            
        except Exception as e:
            self.logger.exception("Failed to start web server")
            self.report_status({
                "status": "error",
                "error": str(e)
            })
            return fail(ErrorCode.INTERNAL, "Failed to start web server", details={"error": str(e)})
    
    def _run_server(self):
        """在后台线程中运行服务器"""
        try:
            if self.server:
                self.server.run()
        except Exception:
            self.logger.exception("Web server error")
    
    def _setup_routes(self):
        """设置路由"""
        if not self.app:
            return
        
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            """主页面"""
            return self._get_html_page()
        
        @self.app.get("/api/messages")
        async def get_messages():
            """获取消息列表 API"""
            # 返回消息列表的浅拷贝，避免并发修改问题
            with self._messages_lock:
                messages_copy = self.messages.copy()
            return {
                "messages": messages_copy,
                "count": len(messages_copy)
            }
        
        @self.app.post("/api/messages")
        async def add_message(message: dict):
            """添加消息 API"""
            source = message.get("source", "unknown")
            content = message.get("content", "")
            # 验证并转换 priority 为整数，失败则使用默认值
            try:
                priority = int(message.get("priority", 5))
                # 限制优先级范围在 0-10
                priority = max(0, min(10, priority))
            except (ValueError, TypeError):
                priority = 5
            self._add_message(source, content, priority)
            with self._messages_lock:
                count = len(self.messages)
            return {"success": True, "count": count}
        
        @self.app.get("/api/status")
        async def get_status():
            """获取状态 API"""
            with self._messages_lock:
                msg_count = len(self.messages)
            return {
                "status": "running",
                "host": self.host,
                "port": self.port,
                "message_count": msg_count,
                "uptime": "active"
            }
        
        @self.app.get("/api/timers")
        async def get_timers():
            """获取定时器列表 API（如果 timer_service 可用）"""
            try:
                # 使用 Queue 机制调用 timer_service 插件（异步包装同步调用）
                result = await asyncio.to_thread(
                    self.plugins.call_entry,
                    "timer_service:list_timers",
                    {},
                    timeout=5.0,
                )
                if result.get("success"):
                    return result
                else:
                    return {
                        "success": False,
                        "error": result.get("error", "未知错误"),
                        "timers": []
                    }
            except Exception as e:
                self.logger.exception("获取定时器列表失败")
                return {
                    "success": False,
                    "error": str(e),
                    "timers": []
                }
    
    def _add_message(self, source: str, content: str, priority: int = 5):
        """添加消息到列表"""
        message = {
            "source": source,
            "content": content,
            "priority": priority,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        with self._messages_lock:
            self.messages.append(message)
            # 限制消息数量
            if len(self.messages) > self.max_messages:
                self.messages = self.messages[-self.max_messages:]
        
        self.logger.debug(f"Added message: {source} - {content}")
    
    def _get_html_page(self) -> str:
        """生成 HTML 页面"""
        messages_html = ""
        # 在锁内获取消息快照，避免并发修改问题
        with self._messages_lock:
            recent_messages = self.messages[-20:]
            message_count = len(self.messages)
        for msg in reversed(recent_messages):  # 显示最近20条
            # 确保 priority 是整数类型
            priority = int(msg.get("priority", 5)) if isinstance(msg.get("priority"), (int, str)) else 5
            priority_class = "priority-high" if priority >= 7 else "priority-normal"
            timestamp = msg.get("timestamp", "")[:19].replace("T", " ")
            # 转义所有用户输入内容以防止 XSS 攻击
            source_escaped = html.escape(str(msg.get('source', 'unknown')))
            content_escaped = html.escape(str(msg.get('content', '')))
            timestamp_escaped = html.escape(timestamp)
            messages_html += f"""
            <div class="message {priority_class}">
                <div class="message-header">
                    <span class="source">{source_escaped}</span>
                    <span class="timestamp">{timestamp_escaped}</span>
                    <span class="priority">优先级: {priority}</span>
                </div>
                <div class="message-content">{content_escaped}</div>
            </div>
            """
        
        page_html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>N.E.K.O Web Interface Plugin</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
        }}
        
        .header p {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        
        .status-bar {{
            background: #f8f9fa;
            padding: 15px 30px;
            border-bottom: 1px solid #e9ecef;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .status-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .status-indicator {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #28a745;
            animation: pulse 2s infinite;
        }}
        
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        
        .content {{
            padding: 30px;
        }}
        
        .messages-container {{
            max-height: 600px;
            overflow-y: auto;
            margin-top: 20px;
        }}
        
        .message {{
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 6px;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        
        .message:hover {{
            transform: translateX(5px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        }}
        
        .message.priority-high {{
            border-left-color: #dc3545;
            background: #fff5f5;
        }}
        
        .message-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            font-size: 0.9em;
            color: #6c757d;
        }}
        
        .source {{
            font-weight: bold;
            color: #667eea;
        }}
        
        .message.priority-high .source {{
            color: #dc3545;
        }}
        
        .timestamp {{
            color: #adb5bd;
        }}
        
        .priority {{
            background: #e9ecef;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.85em;
        }}
        
        .message-content {{
            color: #212529;
            font-size: 1.05em;
            line-height: 1.6;
        }}
        
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #6c757d;
        }}
        
        .empty-state svg {{
            width: 64px;
            height: 64px;
            margin-bottom: 20px;
            opacity: 0.5;
        }}
        
        .refresh-btn {{
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1em;
            margin-top: 20px;
            transition: background 0.3s;
        }}
        
        .refresh-btn:hover {{
            background: #5568d3;
        }}
        
        .auto-refresh {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 20px;
        }}
        
        .auto-refresh input[type="checkbox"] {{
            width: 18px;
            height: 18px;
            cursor: pointer;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 N.E.K.O Web Interface</h1>
            <p>插件消息监控界面</p>
        </div>
        
        <div class="status-bar">
            <div class="status-item">
                <span class="status-indicator"></span>
                <span>状态: 运行中</span>
            </div>
            <div class="status-item">
                <span>消息总数: {message_count}</span>
            </div>
            <div class="status-item">
                <span>更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>
            </div>
        </div>
        
        <div class="content">
            <h2>消息列表</h2>
            
            <div class="messages-container" id="messagesContainer">
                {messages_html if messages_html else '<div class="empty-state"><p>暂无消息</p></div>'}
            </div>
            
            <div class="auto-refresh">
                <input type="checkbox" id="autoRefresh" checked>
                <label for="autoRefresh">自动刷新 (每 3 秒)</label>
            </div>
            
            <button class="refresh-btn" onclick="refreshMessages()">手动刷新</button>
        </div>
    </div>
    
    <script>
        let autoRefreshInterval = null;
        
        // HTML 转义函数，防止 XSS 攻击
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        function refreshMessages() {{
            fetch('/api/messages')
                .then(response => response.json())
                .then(data => {{
                    const container = document.getElementById('messagesContainer');
                    if (data.messages.length === 0) {{
                        container.innerHTML = '<div class="empty-state"><p>暂无消息</p></div>';
                        return;
                    }}
                    
                    // 清空容器
                    container.innerHTML = '';
                    const recentMessages = data.messages.slice(-20).reverse();
                    recentMessages.forEach(msg => {{
                        // 确保 priority 是数字类型
                        const priority = parseInt(msg.priority) || 5;
                        const priorityClass = priority >= 7 ? 'priority-high' : 'priority-normal';
                        const timestamp = msg.timestamp ? msg.timestamp.substring(0, 19).replace('T', ' ') : '';
                        
                        // 使用 createElement 和 textContent 来安全地创建 DOM 元素
                        // textContent 会自动转义，不需要手动转义 HTML
                        const sourceText = String(msg.source || 'unknown');
                        const contentText = String(msg.content || '');
                        const timestampText = timestamp;
                        
                        const messageDiv = document.createElement('div');
                        messageDiv.className = `message ${{priorityClass}}`;
                        
                        const headerDiv = document.createElement('div');
                        headerDiv.className = 'message-header';
                        
                        const sourceSpan = document.createElement('span');
                        sourceSpan.className = 'source';
                        sourceSpan.textContent = sourceText;
                        
                        const timestampSpan = document.createElement('span');
                        timestampSpan.className = 'timestamp';
                        timestampSpan.textContent = timestampText;
                        
                        const prioritySpan = document.createElement('span');
                        prioritySpan.className = 'priority';
                        prioritySpan.textContent = `优先级: ${{priority}}`;
                        
                        headerDiv.appendChild(sourceSpan);
                        headerDiv.appendChild(timestampSpan);
                        headerDiv.appendChild(prioritySpan);
                        
                        const contentDiv = document.createElement('div');
                        contentDiv.className = 'message-content';
                        contentDiv.textContent = contentText;
                        
                        messageDiv.appendChild(headerDiv);
                        messageDiv.appendChild(contentDiv);
                        
                        container.appendChild(messageDiv);
                    }});
                }})
                .catch(error => {{
                    console.error('刷新消息失败:', error);
                }});
        }}
        
        document.getElementById('autoRefresh').addEventListener('change', function(e) {{
            if (e.target.checked) {{
                autoRefreshInterval = setInterval(refreshMessages, 3000);
            }} else {{
                if (autoRefreshInterval) {{
                    clearInterval(autoRefreshInterval);
                    autoRefreshInterval = null;
                }}
            }}
        }});
        
        // 初始加载和启动自动刷新
        refreshMessages();
        if (document.getElementById('autoRefresh').checked) {{
            autoRefreshInterval = setInterval(refreshMessages, 3000);
        }}
    </script>
</body>
</html>
        """
        return page_html
    
    @lifecycle(
        id="shutdown",
        name="Plugin Shutdown",
        description="插件关闭时停止 Web 服务器"
    )
    def shutdown(self, **_):
        """关闭时停止 Web 服务器"""
        self.logger.info("Shutting down Web Interface Plugin...")
        
        try:
            # 停止服务器
            if self.server:
                # 设置退出标志（uvicorn 0.38.0+ 中 shutdown() 是异步方法，不能在同步线程中调用）
                self.server.should_exit = True
            
            # 等待服务器关闭
            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=3.0)
                if self.server_thread.is_alive():
                    self.logger.warning("Server thread did not stop within timeout")
            
            # 添加关闭消息
            self._add_message("系统", "Web 界面插件已关闭", priority=5)
            
            # 上报状态
            self.report_status({"status": "stopped"})
            
            self.logger.info("Web Interface Plugin shut down successfully")
            
            return ok(data={"status": "stopped"})
            
        except Exception as e:
            self.logger.exception("Error during shutdown")
            return fail(ErrorCode.INTERNAL, "Error during shutdown", details={"error": str(e)})
    
    @plugin_entry(
        id="add_message",
        name="Add Message",
        description="添加一条消息到 Web 界面显示。插件启动后会自动运行 Web 服务器，访问 http://127.0.0.1:8888 查看消息。",
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "消息来源标识",
                    "default": "external"
                },
                "content": {
                    "type": "string",
                    "description": "要显示的消息内容",
                    "default": ""
                },
                "message": {
                    "type": "string",
                    "description": "要显示的消息内容（content 的别名，用于兼容性）",
                    "default": ""
                },
                "priority": {
                    "type": "integer",
                    "description": "消息优先级，0-10，数字越大优先级越高",
                    "minimum": 0,
                    "maximum": 10,
                    "default": 5
                }
            },
            "required": []
        }
    )
    def add_message(self, content: str = "", source: str = "external", priority: int = 5, **kwargs):
        """添加消息"""
        # 关键日志：记录方法调用
        self.logger.info(
            "[WebInterface] add_message called: source=%s, priority=%s, has_content=%s, kwargs_keys=%s",
            source,
            priority,
            bool(content),
            list(kwargs.keys()) if kwargs else [],
        )
        # 详细参数信息使用 DEBUG
        self.logger.debug(
            "[WebInterface] Parameters: content=%s, source=%s, priority=%s, kwargs=%s",
            content,
            source,
            priority,
            kwargs,
        )
        self.logger.debug(
            "[WebInterface] Parameter types: content_type={}, source_type={}",
            type(content).__name__,
            type(source).__name__,
        )
        
        # 支持 message 参数作为 content 的别名（用于兼容性）
        if not content and "message" in kwargs:
            content = kwargs.pop("message")
            self.logger.info(
                "[WebInterface] Found 'message' in kwargs, using as content (length={})",
                len(content) if content else 0,
            )
            self.logger.debug(
                "[WebInterface] Converted message to content: {}",
                content,
            )
        
        # 如果没有提供内容，使用默认消息
        if not content:
            content = f"消息来自 {source} (无内容)"
            self.logger.warning(
                "[WebInterface] Content was empty, using default message",
            )
        else:
            self.logger.debug(
                "[WebInterface] Using provided content: {}",
                content,
            )
        
        # 最终参数使用 DEBUG
        self.logger.debug(
            "[WebInterface] Final parameters: source={}, content={}, priority={}",
            source,
            content,
            priority,
        )
        
        self._add_message(source, content, priority)

        # 记录消息添加成功，但不记录完整内容（避免泄露敏感信息）
        self.logger.info(
            "[WebInterface] Added message via API: source={}, priority={}, content_length={}",
            source,
            priority,
            len(content) if content else 0,
        )
        self.logger.debug("[WebInterface] Added message content: {}", content)
        
        with self._messages_lock:
            msg_count = len(self.messages)
        
        return ok(
            data={
                "message_count": msg_count,
                "message": {
                    "source": source,
                    "content": content,
                    "priority": priority,
                },
            }
        )
    
    @plugin_entry(
        id="get_status",
        name="Get Status",
        description="获取 Web 界面插件的运行状态，包括服务器地址、消息数量等信息"
    )
    def get_status(self, **_):
        """获取状态"""
        with self._messages_lock:
            msg_count = len(self.messages)
        
        thread_alive = self.server_thread.is_alive() if self.server_thread else False
        
        return ok(
            data={
                "status": "running" if self.server and thread_alive else "stopped",
                "host": self.host,
                "port": self.port,
                "url": f"http://{self.host}:{self.port}",
                "message_count": msg_count,
                "thread_alive": thread_alive,
            }
        )
    
    @plugin_entry(
        id="check_plugins",
        name="检查插件状态",
        description="检查所有已加载的插件状态，包括 timer_service"
    )
    async def check_plugins(self, **_):
        """检查插件状态"""
        # 注意：获取插件列表需要通过 HTTP API（这是外部调用，不是插件间通信）
        # 因为插件列表是主进程管理的，不是插件功能
        try:
            import httpx
            user_plugin_server_port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "48916"))
            url = f"http://localhost:{user_plugin_server_port}/plugins"
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    plugins = data.get("plugins", [])
                    plugin_ids = [p.get("id") for p in plugins if isinstance(p, dict)]
                    timer_service_available = "timer_service" in plugin_ids
                    
                    result = {
                        "success": True,
                        "loaded_plugins": plugin_ids,
                        "timer_service_available": timer_service_available,
                        "total_plugins": len(plugin_ids)
                    }
                    
                    if timer_service_available:
                        result["message"] = "定时器服务插件已加载"
                    else:
                        result["message"] = f"定时器服务插件未加载。已加载的插件: {plugin_ids}"
                        result["suggestion"] = "请检查 timer_service 插件的配置和日志"
                    
                    self.logger.info(f"[WebInterface] 插件状态检查: {result}")
                    return ok(data=result)
                else:
                    return fail(
                        ErrorCode.NOT_READY,
                        f"Failed to fetch plugins list, HTTP {response.status_code}",
                        details={"loaded_plugins": []},
                        retriable=True,
                    )
        except Exception as e:
            self.logger.exception("[WebInterface] 检查插件状态失败")
            return fail(ErrorCode.INTERNAL, "Check plugins failed", details={"error": str(e)})
    
    @plugin_entry(
        id="test_timer",
        name="测试定时器",
        description="测试定时器服务，创建一个简单的定时器并在 Web 界面显示消息",
        input_schema={
            "type": "object",
            "properties": {
                "interval": {
                    "type": "number",
                    "description": "定时器间隔（秒）",
                    "default": 10,
                    "minimum": 1
                },
                "count": {
                    "type": "integer",
                    "description": "执行次数（0表示不限制）",
                    "default": 5,
                    "minimum": 0
                }
            }
        }
    )
    async def test_timer(self, interval: float = 10, count: int = 5, **_):
        """测试定时器服务"""
        self.logger.info(f"[WebInterface] 测试定时器: interval={interval}s, count={count}")
        try:
            timer_id = f"web_interface_test_{int(time.time())}"
            
            # 使用 Queue 机制调用 timer_service 插件（异步包装同步调用）
            result = await asyncio.to_thread(
                self.plugins.call_entry,
                "timer_service:start_timer",
                {
                    "timer_id": timer_id,
                    "interval": interval,
                    "immediate": True,
                    "callback_plugin_id": self.ctx.plugin_id,
                    "callback_entry_id": "on_timer_tick",
                    "callback_args": {
                        "timer_id": timer_id,
                        "max_count": count,
                        "current_count": 0,
                    },
                },
                timeout=5.0,
            )
            
            if result.get("success"):
                self._add_message(
                    "定时器测试",
                    f"定时器已启动: {timer_id}，间隔 {interval} 秒，最多执行 {count} 次",
                    priority=7
                )
                return ok(data={"timer_id": timer_id, "interval": interval, "result": result})
            else:
                error_msg = result.get("error", "未知错误")
                self.logger.warning(f"[WebInterface] 定时器启动失败: {error_msg}")
                return fail(ErrorCode.INTERNAL, error_msg, details={"result": result})
        except Exception as e:
            self.logger.exception("[WebInterface] 测试定时器失败")
            return fail(ErrorCode.INTERNAL, "Test timer failed", details={"error": str(e)})
    
    @plugin_entry(
        id="on_timer_tick",
        name="定时器触发回调",
        description="定时器触发时的回调入口点",
        input_schema={
            "type": "object",
            "properties": {
                "timer_id": {"type": "string"},
                "max_count": {"type": "integer"},
                "current_count": {"type": "integer"}
            }
        }
    )
    def on_timer_tick(self, timer_id: str | None = None, max_count: int = 0, current_count: int = 0, **_):
        """定时器触发回调"""
        # current_count 已经由 timer_service 更新为正确的值，不需要再加1
        self.logger.info(
            f"[WebInterface] 定时器触发: {timer_id}, 第 {current_count} 次 (最大: {max_count if max_count > 0 else '∞'})"
        )
        
        # 添加消息到 Web 界面
        self._add_message(
            "定时器",
            f"定时器 {timer_id} 触发 (第 {current_count}/{max_count if max_count > 0 else '∞'} 次)",
            priority=6
        )
        
        # 如果达到最大次数，记录日志
        # 注意：不要在回调中同步调用 stop_timer，这可能导致死锁
        # timer_service 会在达到最大次数时自动停止定时器（如果支持 max_count 参数）
        if max_count > 0 and current_count >= max_count:
            self.logger.info(f"[WebInterface] 定时器 {timer_id} 已达到最大次数 {max_count}")
            self._add_message(
                "定时器",
                f"定时器 {timer_id} 已达到最大次数 {max_count}，将自动停止",
                priority=6
            )
        
        return ok(data={"timer_id": timer_id, "count": current_count, "max_count": max_count})

