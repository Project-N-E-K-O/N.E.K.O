"""Plugin flavor runtime exports."""

from plugin.sdk_v2.shared.core import config as _config
from plugin.sdk_v2.shared.core import plugins as _plugins
from plugin.sdk_v2.shared.core import router as _router
from plugin.sdk_v2.shared.runtime import call_chain as _call_chain
from plugin.sdk_v2.shared.runtime import memory as _memory
from plugin.sdk_v2.shared.runtime import system_info as _system_info
from plugin.sdk_v2.shared.storage import database as _database
from plugin.sdk_v2.shared.storage import state as _state
from plugin.sdk_v2.shared.storage import store as _store

_MODULES = (
    _config,
    _plugins,
    _router,
    _call_chain,
    _memory,
    _system_info,
    _database,
    _state,
    _store,
)

_exports: list[str] = []
for _module in _MODULES:
    for _export_name in _module.__all__:
        globals()[_export_name] = getattr(_module, _export_name)
        if _export_name not in _exports:
            _exports.append(_export_name)

__all__ = _exports
