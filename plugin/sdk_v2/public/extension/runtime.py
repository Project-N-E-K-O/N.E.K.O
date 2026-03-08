"""Extension flavor runtime exports.

Keep this narrower than plugin/runtime to enforce capability boundaries.
"""

from plugin.sdk_v2.shared.core import config as _config
from plugin.sdk_v2.shared.core import router as _router
from plugin.sdk_v2.shared.runtime import call_chain as _call_chain
from plugin.sdk_v2.shared.transport import message_plane as _message_plane

_MODULES = (_config, _router, _call_chain, _message_plane)

_exports: list[str] = []
for _module in _MODULES:
    for _export_name in _module.__all__:
        globals()[_export_name] = getattr(_module, _export_name)
        if _export_name not in _exports:
            _exports.append(_export_name)

__all__ = _exports
