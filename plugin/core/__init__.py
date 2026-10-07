"""
Plugin Core 模块

提供核心运行时状态、上下文、进程管理和插件注册。
合并了原 runtime/ 模块的功能。

包级名字按需导入（PEP 562）：插件子进程经 ``plugin.core.host`` 进来时会先执行本
文件，在这里全量导入 registry / communication 等会让每个插件进程多付导入时间和内存。
"""

from __future__ import annotations

import importlib
from typing import Any

# ``state`` 与子模块 plugin.core.state 同名，必须 eager 导入：否则一旦有人
# ``import plugin.core.state``，导入系统会把包属性 ``state`` 绑定成子模块，
# ``from plugin.core import state`` 就拿不到 GlobalState 实例了。
from plugin.core.state import GlobalState, state

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "PluginContext": ("plugin.core.context", "PluginContext"),
    # 状态管理器
    "status_manager": ("plugin.core.status", "status_manager"),
    "PluginStatusManager": ("plugin.core.status", "PluginStatusManager"),
    # 注册表
    "load_plugins_from_toml": ("plugin.core.registry", "load_plugins_from_toml"),
    "get_plugins": ("plugin.core.registry", "get_plugins"),
    "register_plugin": ("plugin.core.registry", "register_plugin"),
    "scan_static_metadata": ("plugin.core.registry", "scan_static_metadata"),
    # 进程管理
    "PluginHost": ("plugin.core.host", "PluginHost"),
    "PluginProcessHost": ("plugin.core.host", "PluginProcessHost"),
    "PluginCommunicationResourceManager": ("plugin.core.communication", "PluginCommunicationResourceManager"),
}

__all__ = [
    # 状态管理
    "state",
    "GlobalState",
    *_LAZY_EXPORTS,
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
