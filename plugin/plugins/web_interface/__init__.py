"""
Web Interface Plugin

一个提供 FastAPI Web 界面的插件，在插件激活后可以通过网页访问并查看消息。
"""
import logging
import threading
from typing import Any, Optional
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, lifecycle, plugin_entry


@neko_plugin
class WebInterfacePlugin(NekoPluginBase):
    """Web 界面插件"""
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        # 启用文件日志
        self.file_logger = self.enable_file_logging(log_level=logging.INFO)
        self.logger = self.file_logger
        
        # FastAPI 应用
        self.app: Optional[FastAPI] = None
        self.server_thread: Optional[threading.Thread] = None
        self.server_config: Optional[uvicorn.Config] = None
        self.server: Optional[uvicorn.Server] = None
        
        # 服务器配置
        self.host = "127.0.0.1"
        self.port = 8888
        
        # 消息存储
        self.messages = []
        self.max_messages = 100
        
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
            
            # 在后台线程中启动服务器
            self.server_config = uvicorn.Config(
                app=self.app,
                host=self.host,
                port=self.port,
                log_level="info",
                access_log=False  # 减少日志输出
            )
            self.server = uvicorn.Server(self.server_config)
            
            self.server_thread = threading.Thread(
                target=self._run_server,
                daemon=True,
                name="WebInterfaceServer"
            )
            self.server_thread.start()
            
            # 等待服务器启动
            import time
            time.sleep(0.5)
            
            # 上报状态
            self.report_status({
                "status": "running",
                "host": self.host,
                "port": self.port,
                "url": f"http://{self.host}:{self.port}"
            })
            
            # 推送消息
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
                }
            )
            
            self.logger.info(f"Web server started at http://{self.host}:{self.port}")
            
            return {
                "status": "ready",
                "host": self.host,
                "port": self.port,
                "url": f"http://{self.host}:{self.port}"
            }
            
        except Exception as e:
            self.logger.exception(f"Failed to start web server: {e}")
            self.report_status({
                "status": "error",
                "error": str(e)
            })
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _run_server(self):
        """在后台线程中运行服务器"""
        try:
            if self.server:
                self.server.run()
        except Exception as e:
            self.logger.exception(f"Web server error: {e}")
    
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
            return {
                "messages": self.messages,
                "count": len(self.messages)
            }
        
        @self.app.post("/api/messages")
        async def add_message(message: dict):
            """添加消息 API"""
            source = message.get("source", "unknown")
            content = message.get("content", "")
            priority = message.get("priority", 5)
            self._add_message(source, content, priority)
            return {"success": True, "count": len(self.messages)}
        
        @self.app.get("/api/status")
        async def get_status():
            """获取状态 API"""
            return {
                "status": "running",
                "host": self.host,
                "port": self.port,
                "message_count": len(self.messages),
                "uptime": "active"
            }
    
    def _add_message(self, source: str, content: str, priority: int = 5):
        """添加消息到列表"""
        message = {
            "source": source,
            "content": content,
            "priority": priority,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        self.messages.append(message)
        
        # 限制消息数量
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
        
        self.logger.debug(f"Added message: {source} - {content}")
    
    def _get_html_page(self) -> str:
        """生成 HTML 页面"""
        messages_html = ""
        for msg in reversed(self.messages[-20:]):  # 显示最近20条
            # 确保 priority 是整数类型
            priority = int(msg.get("priority", 5)) if isinstance(msg.get("priority"), (int, str)) else 5
            priority_class = "priority-high" if priority >= 7 else "priority-normal"
            timestamp = msg.get("timestamp", "")[:19].replace("T", " ")
            messages_html += f"""
            <div class="message {priority_class}">
                <div class="message-header">
                    <span class="source">{msg.get('source', 'unknown')}</span>
                    <span class="timestamp">{timestamp}</span>
                    <span class="priority">优先级: {priority}</span>
                </div>
                <div class="message-content">{msg.get('content', '')}</div>
            </div>
            """
        
        html = f"""
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
                <span>消息总数: {len(self.messages)}</span>
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
        
        function refreshMessages() {{
            fetch('/api/messages')
                .then(response => response.json())
                .then(data => {{
                    const container = document.getElementById('messagesContainer');
                    if (data.messages.length === 0) {{
                        container.innerHTML = '<div class="empty-state"><p>暂无消息</p></div>';
                        return;
                    }}
                    
                    let html = '';
                    const recentMessages = data.messages.slice(-20).reverse();
                    recentMessages.forEach(msg => {{
                        // 确保 priority 是数字类型
                        const priority = parseInt(msg.priority) || 5;
                        const priorityClass = priority >= 7 ? 'priority-high' : 'priority-normal';
                        const timestamp = msg.timestamp ? msg.timestamp.substring(0, 19).replace('T', ' ') : '';
                        html += `
                            <div class="message ${{priorityClass}}">
                                <div class="message-header">
                                    <span class="source">${{msg.source || 'unknown'}}</span>
                                    <span class="timestamp">${{timestamp}}</span>
                                    <span class="priority">优先级: ${{priority}}</span>
                                </div>
                                <div class="message-content">${{msg.content || ''}}</div>
                            </div>
                        `;
                    }});
                    container.innerHTML = html;
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
        return html
    
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
                # 设置退出标志
                self.server.should_exit = True
                # 触发关闭
                if hasattr(self.server, 'shutdown'):
                    self.server.shutdown()
            
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
            
            return {"status": "stopped"}
            
        except Exception as e:
            self.logger.exception(f"Error during shutdown: {e}")
            return {"status": "error", "error": str(e)}
    
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
            "[WebInterface] Parameter types: content_type=%s, source_type=%s",
            type(content).__name__,
            type(source).__name__,
        )
        
        # 支持 message 参数作为 content 的别名（用于兼容性）
        original_content = content
        if not content and "message" in kwargs:
            content = kwargs.pop("message")
            self.logger.info(
                "[WebInterface] Found 'message' in kwargs, using as content (length=%d)",
                len(content) if content else 0,
            )
            self.logger.debug(
                "[WebInterface] Converted message to content: %s",
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
                "[WebInterface] Using provided content: %s",
                content,
            )
        
        # 最终参数使用 DEBUG
        self.logger.debug(
            "[WebInterface] Final parameters: source=%s, content=%s, priority=%s",
            source,
            content,
            priority,
        )
        
        self._add_message(source, content, priority)
        
        self.logger.info(f"Added message via API: {source} - {content}")
        
        return {
            "success": True,
            "message_count": len(self.messages),
            "message": {
                "source": source,
                "content": content,
                "priority": priority
            }
        }
    
    @plugin_entry(
        id="get_status",
        name="Get Status",
        description="获取 Web 界面插件的运行状态，包括服务器地址、消息数量等信息"
    )
    def get_status(self, **_):
        """获取状态"""
        return {
            "status": "running" if self.server and self.server_thread and self.server_thread.is_alive() else "stopped",
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}",
            "message_count": len(self.messages),
            "thread_alive": self.server_thread.is_alive() if self.server_thread else False
        }

