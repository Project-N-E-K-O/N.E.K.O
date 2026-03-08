"""Plugin flavor decorators exports."""

from plugin.sdk_v2.shared.core import decorators as _shared_decorators

for _export_name in _shared_decorators.__all__:
    globals()[_export_name] = getattr(_shared_decorators, _export_name)

__all__ = list(_shared_decorators.__all__)
