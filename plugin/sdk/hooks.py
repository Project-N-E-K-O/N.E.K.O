"""
Hook 类型定义模块

提供 Hook 系统的类型定义，避免循环导入。
"""
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, Any, List

# Hook 装饰器的属性名
HOOK_META_ATTR = "_neko_hook_meta"

# Hook 时机类型
HookTiming = Literal["before", "after", "around", "replace"]


@dataclass
class HookMeta:
    """Hook 元数据
    
    Attributes:
        target: Hook 目标
            - 插件内: "entry_id" 或 "*" (所有 entry)
            - 跨插件: "plugin_id.entry_id"
        timing: 执行时机
            - "before": 在目标 entry 执行前
            - "after": 在目标 entry 执行后
            - "around": 包裹目标 entry（可以控制是否执行）
            - "replace": 替换目标 entry
        priority: 优先级（越大越先执行）
        condition: 条件函数（返回 True 才执行 Hook）
    """
    target: str
    timing: HookTiming = "before"
    priority: int = 0
    condition: Optional[str] = None  # 条件表达式或函数名
    
    @property
    def is_cross_plugin(self) -> bool:
        """是否是跨插件 Hook"""
        return "." in self.target and self.target != "*"
    
    @property
    def target_plugin(self) -> Optional[str]:
        """获取目标插件 ID（跨插件时）"""
        if self.is_cross_plugin:
            return self.target.split(".", 1)[0]
        return None
    
    @property
    def target_entry(self) -> str:
        """获取目标 entry ID"""
        if self.is_cross_plugin:
            return self.target.split(".", 1)[1]
        return self.target


@dataclass
class HookHandler:
    """Hook 处理器
    
    Attributes:
        meta: Hook 元数据
        handler: Hook 处理函数
        router_name: 所属 Router 名称（用于日志）
    """
    meta: HookMeta
    handler: Callable[..., Any]
    router_name: str = ""
