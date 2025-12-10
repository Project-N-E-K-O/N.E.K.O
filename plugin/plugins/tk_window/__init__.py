import threading
import tkinter as tk
from typing import Any
from plugin.sdk.decorators import neko_plugin, plugin_entry, on_event
from plugin.sdk.base import NekoPluginBase

"""
Plugin for test use.
Deprecated,considered to be discarded in the future.
May contains unpredictable bugs and bug fix is not provided.
"""
@neko_plugin
class TkWindowPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._lock = threading.Lock()
        self._started: bool = False
        self._thread: threading.Thread | None = None
        self._root: tk.Tk | None = None
        self._should_close: bool = False
        self.ctx = ctx
        
    def _run_tk(self, title: str, message: str):
        root = tk.Tk()
        self._root = root
        self._should_close = False
        
        root.title(title)
        label = tk.Label(root, text=message, padx=20, pady=20)
        label.pack()
        btn = tk.Button(root, text="Close", command=root.destroy)
        btn.pack()
        
        # 在 Tk 线程内部轮询关闭标志
        def poll_close_flag():
            if self._should_close:
                root.destroy()
            else:
                root.after(100, poll_close_flag)  # 100ms 后再检查一次
        root.after(100,poll_close_flag)
        root.mainloop()
        
        with self._lock:
            
            self._started = False
            self._root = None
            self._should_close = False

    # 1) 一个 plugin_entry:对外可调用,“打开窗口”
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
        with self._lock:
            if self._started:
                return {"started": False, "reason": "window already running"}
            self._started = True
        t = threading.Thread(
            target=self._run_tk,
            args=(title or "N.E.K.O Tk Plugin", message or "Hello from Tk plugin!"),
            daemon=True,
        )
        t.start()
        self._thread = t
        
        self.report_status({"started": True})
        return {"started": True, "info": "Tk window thread started"}

    # 2) 另一个 plugin_entry:关闭窗口
    @plugin_entry(
        id="close",
        name="Close Tk Window",
        description="Close Tk window if opened",
    )
    def close_window(self, **_):
        with self._lock:
            if self._root is not None:
                self._should_close = True
                return {"closed": True}
            else:
                return {"closed": False, "reason": "no window"}

    # 3) 一个 lifecycle 事件:插件加载后自动调用
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
        self.ctx.logger.info("[tkWindow] plugin started")
        self.report_status({"status": "initialized"})
        return {"status": "initialized"}
    # 4) 一个 lifecycle 事件:插件停止时自动调用
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
        self.ctx.logger.info("[tkWindow] plugin shutting down")
        self.report_status({"status": "shutdown"})
        with self._lock:
            if self._root is not None:
                self._should_close = True  # 通知 Tk 线程自己关闭
        return {"status": "shutdown"}