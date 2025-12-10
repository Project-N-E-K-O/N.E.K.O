"""
插件基类模块

提供插件开发的基础类和接口。
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
from .events import EventHandler, EventMeta, EVENT_META_ATTR

NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"
NEKO_PLUGIN_TAG = "__neko_plugin__"


@dataclass
class PluginMeta:
    """插件元数据（SDK 内部使用）"""
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""


class NekoPluginBase:
    """插件都继承这个基类."""
    
    def __init__(self, ctx: Any):
        self.ctx = ctx
        self._plugin_id = getattr(ctx, "plugin_id", "unknown")

    def get_input_schema(self) -> Dict[str, Any]:
        """默认从类属性 input_schema 取."""
        schema = getattr(self, "input_schema", None)
        return schema or {}

    def collect_entries(self) -> Dict[str, EventHandler]:
        """
        默认实现：扫描自身方法，把带入口标记的都收集起来。
        （注意：这是插件内部调用的，不是服务器在外面乱扫全模块）
        """
        entries: Dict[str, EventHandler] = {}
        for attr_name in dir(self):
            value = getattr(self, attr_name)
            if not callable(value):
                continue
            meta: EventMeta | None = getattr(value, EVENT_META_ATTR, None)
            if meta:
                entries[meta.id] = EventHandler(meta=meta, handler=value)
        return entries
    
    def report_status(self, status: Dict[str, Any]) -> None:
        """
        插件内部调用此方法上报状态。
        通过 ctx.update_status 把状态发回主进程。
        """
        if hasattr(self.ctx, "update_status"):
            # ✅ 这里只传原始 status，由 Context 负责打包成队列消息
            self.ctx.update_status(status)
        else:
            logger = getattr(self.ctx, "logger", None)
            if logger:
                logger.warning(
                    f"Plugin {self._plugin_id} tried to report status but ctx.update_status is missing."
                )

