import threading
import tkinter as tk
from plugin.decorators import neko_plugin, plugin_entry, on_event
from plugin.plugin_base import NekoPluginBase
@neko_plugin
class TkWindowPlugin(NekoPluginBase):
    def __init__(self):
        self._started = False
        self._thread = None
        self._root = None

    def _run_tk(self, title: str, message: str):
        root = tk.Tk()
        self._root = root
        root.title(title)
        label = tk.Label(root, text=message, padx=20, pady=20)
        label.pack()
        btn = tk.Button(root, text="Close", command=root.destroy)
        btn.pack()
        root.mainloop()
        self._started = False
        self._root = None

    # 1) 一个 plugin_entry：对外可调用，“打开窗口”
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
        self.report_status({"started": True})
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
        return {"started": True, "info": "Tk window thread started"}

    # 2) 另一个 plugin_entry：关闭窗口
    @plugin_entry(
        id="close",
        name="Close Tk Window",
        description="Close Tk window if opened",
    )
    def close_window(self, **_):
        if self._root is not None:
            self._root.destroy()
            return {"closed": True}
        return {"closed": False, "reason": "no window"}

    # 3) 一个 lifecycle 事件：插件加载后自动调用
    @on_event(
        event_type="lifecycle",
        id="on_start",
        name="On Plugin Start",
        description="Run when plugin is loaded",
        kind="hook",
        auto_start=True,
    )
    def on_start(self, **_):
        # 这里可以放一些初始化逻辑，比如预加载配置等
        print("[tkWindow] plugin started")
        return {"status": "initialized"}
