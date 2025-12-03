# neko_plugin_core/plugin_base.pyfrom plugin_server import upda
from dataclasses import dataclass
from typing import Optional, Dict, Any
from .event_base import EventHandler, EventMeta
from user_plugin_server import update_plugin_status
NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"
EVENT_META_ATTR = "__neko_event_meta__"
NEKO_PLUGIN_TAG = "__neko_plugin__"


@dataclass
class PluginMeta:
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""

class NekoPluginBase:
    """插件都继承这个基类."""
    def __init__(self, ctx: Any):
        self.ctx = ctx

    def get_input_schema(self) -> Dict[str, Any]:
        """默认从类属性 input_schema 取."""
        schema = getattr(self, "input_schema", None)
        return schema or {}

    def collect_entries(self) -> Dict[str, "EventHandler"]:
        """
        默认实现：扫描自身方法，把带入口标记的都收集起来。
        （注意：这是插件内部调用的，不是服务器在外面乱扫全模块）
        """

        entries: Dict[str, EventHandler] = {}
        for attr_name in dir(self):
            value = getattr(self, attr_name)
            meta: EventMeta | None = getattr(value, EVENT_META_ATTR, None)
            if meta:
                entries[meta.id] = EventHandler(meta=meta, handler=value)
        return entries
    
    def report_status(self, status: Dict[str, Any]) -> None:
            """
            插件内部直接调用 self.report_status({...}),
            不用自己管 plugin_id。
            """
            pid = getattr(self, "_plugin_id", None)
            if not pid:
                # 保险一点，避免忘记注入
                raise RuntimeError("Plugin instance missing _plugin_id, cannot report status")
            update_plugin_status(pid, status)