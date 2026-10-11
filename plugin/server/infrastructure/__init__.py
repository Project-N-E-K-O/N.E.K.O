"""
基础设施模块

提供共享的基础设施组件:异常处理、认证等。
"""
from importlib import import_module

_EXPORTS = {
    "require_admin": (".auth", "require_admin"),
    "get_admin_code": (".auth", "get_admin_code"),
    "register_exception_handlers": (".exceptions", "register_exception_handlers"),
    "handle_plugin_error": (".error_handler", "handle_plugin_error"),
    "safe_execute": (".error_handler", "safe_execute"),
    "now_iso": ("plugin.utils.time_utils", "now_iso"),
}
_SUBMODULES = frozenset({"auth", "error_handler", "exceptions"})


def __getattr__(name: str):
    if name in _SUBMODULES:
        value = import_module(f".{name}", __name__)
    else:
        export = _EXPORTS.get(name)
        if export is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        module, attr = export
        value = getattr(import_module(module, __name__), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | _SUBMODULES)


__all__ = [
    'require_admin',
    'get_admin_code',
    'register_exception_handlers',
    'handle_plugin_error',
    'safe_execute',
    'now_iso',
]
