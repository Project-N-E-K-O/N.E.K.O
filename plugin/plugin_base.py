# neko_plugin_core/plugin_base.py
from dataclasses import dataclass
from typing import Optional, Dict, Any

NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"
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

    def collect_entries(self) -> Dict[str, "Entry"]:
        """
        默认实现：扫描自身方法，把带入口标记的都收集起来。
        （注意：这是插件内部调用的，不是服务器在外面乱扫全模块）
        """
        from .entry_base import Entry, EntryMeta

        entries: Dict[str, Entry] = {}
        for attr_name in dir(self):
            value = getattr(self, attr_name)
            meta: EntryMeta | None = getattr(value, "__neko_entry_meta__", None)
            if meta:
                entries[meta.id] = Entry(meta=meta, handler=value)
        return entries
