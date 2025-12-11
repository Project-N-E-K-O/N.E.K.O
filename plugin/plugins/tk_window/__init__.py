"""
Plugin for test use.
Deprecated, considered to be discarded in the future.
May contain unpredictable bugs and bug fix is not provided.
"""

import threading
import tkinter as tk
import logging
from datetime import datetime, timezone
from typing import Any
from plugin.sdk.decorators import neko_plugin, plugin_entry, on_event
from plugin.sdk.base import NekoPluginBase
@neko_plugin
class TkWindowPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        # 启用文件日志(同时输出到文件和控制台)
        self.file_logger = self.enable_file_logging(log_level=logging.INFO)
        self._lock = threading.Lock()
        self._started: bool = False
        self._thread: threading.Thread | None = None
        self._root: tk.Tk | None = None
        self._should_close: bool = False
        self.file_logger.info("TkWindowPlugin initialized with file logging enabled")
        
    def _run_tk(self, title: str, message: str):
        root = tk.Tk()
        with self._lock:
            self._root = root
            self._should_close = False
        
        root.title(title)
        label = tk.Label(root, text=message, padx=20, pady=20)
        label.pack()
        btn = tk.Button(root, text="Close", command=root.destroy)
        btn.pack()
        
        # 在 Tk 线程内部轮询关闭标志
        def poll_close_flag():
            with self._lock:
                should_close = self._should_close
            if should_close:
                root.destroy()
            else:
                root.after(100, poll_close_flag)  # 100ms 后再检查一次
        root.after(100, poll_close_flag)
        root.mainloop()
        
        with self._lock:
            
            self._started = False
            self._root = None
            self._should_close = False

        # 1) 一个 plugin_entry: 对外可调用,"打开窗口"
    @plugin_entry(
        id="open",
        name="Open a Tk window",
        description="Open a Tkinter window showing a custom title and message on the local desktop.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title text"},
                "message": {"type": "string", "description": "Message to display in the window"},
            },
        },
    )
    def open_window(self, title: str | None = None, message: str | None = None, **_):
        window_title = title or "N.E.K.O Tk Plugin"
        window_message = message or "Hello from Tk plugin!"
        with self._lock:
            if self._started:
                # 推送消息: 窗口已打开
                self.ctx.push_message(
                    source="open_window",
                    message_type="text",
                    description="Window already running",
                    priority=1,
                    content="Tk window is already open, cannot open another one.",
                    metadata={"action": "open", "status": "already_running"}
                )
                return {"started": False, "reason": "window already running"}
            self._started = True
            t = threading.Thread(
                target=self._run_tk,
                args=(window_title, window_message),
                daemon=True,
            )
            self._thread = t
        
        # 推送消息: 窗口正在打开
        self.ctx.push_message(
            source="open_window",
            message_type="text",
            description="Opening Tk window",
            priority=5,
            content=f"Opening window with title: {window_title}, message: {window_message}",
            metadata={"action": "open", "title": window_title, "message": window_message}
        )
        
        try:
            t.start()
        except Exception as e:
            # 线程启动失败，回滚状态并通知
            with self._lock:
                self._started = False
                self._thread = None
            self.ctx.push_message(
                source="open_window",
                message_type="text",
                description="Failed to start Tk window",
                priority=8,
                content=f"Failed to start window thread: {e}",
                metadata={"action": "open", "status": "error", "error": str(e)}
            )
            return {"started": False, "reason": f"thread start failed: {e}"}
        
        self.report_status({"started": True})
        
        # 推送消息: 窗口已成功打开
        self.ctx.push_message(
            source="open_window",
            message_type="text",
            description="Tk window opened successfully",
            priority=7,
            content=f"Tk window '{window_title}' has been opened successfully.",
            metadata={"action": "open", "status": "success", "title": window_title}
        )
        
        return {"started": True, "info": "Tk window thread started"}

    # 2) 另一个 plugin_entry: 关闭窗口
    @plugin_entry(
        id="close",
        name="Close Tk Window",
        description="Close Tk window if opened",
    )
    def close_window(self, **_):
        with self._lock:
            if self._root is not None:
                self._should_close = True
                # 推送消息: 窗口正在关闭
                self.ctx.push_message(
                    source="close_window",
                    message_type="text",
                    description="Closing Tk window",
                    priority=5,
                    content="Tk window is being closed.",
                    metadata={"action": "close", "status": "closing"}
                )
                return {"closed": True}
            else:
                # 推送消息: 没有窗口可关闭
                self.ctx.push_message(
                    source="close_window",
                    message_type="text",
                    description="No window to close",
                    priority=1,
                    content="No Tk window is currently open.",
                    metadata={"action": "close", "status": "no_window"}
                )
                return {"closed": False, "reason": "no window"}
    
    # 3) 新建一个测试入口: 发送测试消息
    @plugin_entry(
        id="test_message",
        name="Send Test Message",
        description="Send a test message to the plugin server to verify message pushing functionality",
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Test message content"},
                "priority": {"type": "integer", "description": "Message priority (0-10)", "default": 5},
            },
        },
    )
    def test_message(self, message: str | None = None, priority: int = 5, **_):
        """发送测试消息到服务器"""
        test_msg = message or "This is a test message from tkWindow plugin!"
        
        # 推送测试消息
        self.ctx.push_message(
            source="test_message",
            message_type="text",
            description="Test message from tkWindow plugin",
            priority=priority,
            content=test_msg,
            metadata={
                "action": "test",
                "plugin": "tkWindow",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
        
        self.file_logger.info(f"[tkWindow] Sent test message: {test_msg}")
        
        return {
            "success": True,
            "message": test_msg,
            "priority": priority,
            "info": "Test message sent to server"
        }

    # 3) 一个 lifecycle 事件: 插件加载后自动调用
    @on_event(
        event_type="lifecycle",
        id="startup",
        name="On Plugin Start",
        description="Run when plugin is loaded",
        kind="hook",
        auto_start=True,
    )
    def startup(self, **_):
        # 这里可以放一些初始化逻辑,比如预加载配置等
        self.file_logger.info("[tkWindow] plugin started")
        self.report_status({"status": "initialized"})
        return {"status": "initialized"}
    # 4) 一个 lifecycle 事件: 插件停止时自动调用
    @on_event(
        event_type="lifecycle",
        id="on_shutdown",
        name="On Plugin Shutdown",
        description="Run when plugin is stopped",
        kind="hook",
        auto_start=False,  # 不自动启动，插件停止时手动触发
    )
    def on_shutdown(self, **_):
        # 在这里执行一些资源清理、状态保存等操作
        self.file_logger.info("[tkWindow] plugin shutting down")
        self.report_status({"status": "shutdown"})
        with self._lock:
            if self._root is not None:
                self._should_close = True  # 通知 Tk 线程自己关闭
                thread = self._thread
            else:
                thread = None
        
        # 等待 Tk 线程结束（最多等待 2 秒）
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
            if thread.is_alive():
                self.file_logger.warning("[tkWindow] Tk thread did not stop within timeout")
        
        return {"status": "shutdown"}